"""Ожидание конца операции по опросу CPU и окна — по шагам (plan-to-10, шаг 2).

Прежде это был один цикл _wait_operation_done на 190 строк с дюжиной
переменных состояния. Здесь тот же цикл разложен на шаги класса OpWait:
состояние — атрибуты, каждая проверка — метод. Порядок проверок, пороги и
тексты журнала не менялись; что значит «занят» и почему так — в докстринге
OpEndMixin._wait_operation_done и docs/measurement.md.

Шаг опроса (OP_POLL_SEC):
  1. окно Р7 (перерешить hwnd, если прежнее исчезло);
  2. новые процессы Р7 — раз в OP_PROC_REFRESH_SEC (ловим x2t);
  3. жив ли x2t — на каждом опросе;
  4. CPU — раз в OP_CPU_WINDOW_SEC, сырая сумма в % одного ядра;
  5. занятость: окно не отвечает, жив x2t или CPU подтверждён;
  6. модалка тяжёлого пересчёта (ответ «Нет», ожидание — в _paced_total);
  7. серия простоя → "ok", ни разу не занят за start_grace → "below_floor".
"""
import time

from r7 import env, windows
from r7.env import psutil
from r7.processes import _is_crash_snapshot


class OpWait:
    """Одно ожидание конца операции: run() → (момент, статус), как у
    _wait_operation_done. Все моменты — time.perf_counter()."""

    CONTINUE = object()     # шаг просит начать следующий опрос без паузы

    def __init__(self, app, hwnd, log_cb, start_grace, max_wait):
        self.app, self.hwnd, self.log_cb = app, hwnd, log_cb
        self.start_grace, self.max_wait = start_grace, max_wait
        self.start = time.perf_counter()
        self.deadline = self.start + max_wait
        self.cur_hwnd = None if callable(hwnd) else hwnd
        self.tracked = {}          # pid -> (psutil.Process с «прогретым» CPU, имя)
        self.last_refresh = 0.0
        self.last_cpu_at = 0.0
        self.last_cpu = 0.0
        self.prev_cpu = 0.0        # CPU предыдущего окна — для подтверждения занятости
        self.cpu_win_start = self.start   # начало окна, к которому относится last_cpu
        self.last_signal_busy_at = None   # последний опрос с «не отвечает» / живым x2t
        self.seen_busy = False
        self.idle_streak = 0
        self.idle_since = None
        self.last_prompt_check = 0.0

    # ── окно и процессы ───────────────────────────────────────────────────
    def _refresh_hwnd(self):
        if callable(self.hwnd):
            if not (self.cur_hwnd and env.WIN32_OK and windows.is_window(self.cur_hwnd)):
                self.cur_hwnd = self.hwnd()

    def _adopt(self, now):
        """Новые процессы Р7 — в tracked. Первый cpu_percent(None) задаёт базу
        отсчёта (всегда 0.0), поэтому он здесь, а не в замере."""
        if not (env.PSUTIL_OK and now - self.last_refresh >= self.app.OP_PROC_REFRESH_SEC):
            return
        self.last_refresh = now
        self.app._r7_pids = None
        for p in self.app._get_r7_processes(log_cb=self.log_cb):
            if p.pid in self.tracked:
                continue
            try:
                name = (p.name() or "").lower()
                p.cpu_percent(None)
                self.tracked[p.pid] = (p, name)
            # процесс завершился до первого замера CPU — считать нечего
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def _converter_alive(self):
        """x2t проверяем на каждом опросе — он короткоживущий, и лишние
        0.2 сек ожидания его смерти уехали бы прямо в замер PDF-экспорта."""
        alive = False
        for pid, (p, name) in list(self.tracked.items()):
            if "x2t" not in name:
                continue
            try:
                if p.is_running() and not _is_crash_snapshot(p):
                    alive = True
                else:
                    self.tracked.pop(pid, None)
            except Exception:
                self.tracked.pop(pid, None)
        return alive

    def _poll_cpu(self, now):
        if not (env.PSUTIL_OK and now - self.last_cpu_at >= self.app.OP_CPU_WINDOW_SEC):
            return
        self.cpu_win_start = self.last_cpu_at or self.start
        self.last_cpu_at = now
        self.prev_cpu = self.last_cpu
        total = 0.0
        dead = []
        for pid, (p, _name) in self.tracked.items():
            try:
                total += p.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                dead.append(pid)
        for pid in dead:
            self.tracked.pop(pid, None)
        # Сырая сумма в % одного ядра — см. READY_IDLE_CORE_PCT
        # (measure_schema 3): нормировка на число ядер прятала
        # однопоточную работу Р7 на многоядерных стендах.
        self.last_cpu = total

    # ── занятость ─────────────────────────────────────────────────────────
    def _busy(self, converter_alive):
        app = self.app
        responsive = app._window_responsive(self.cur_hwnd, app.OP_RESPONSIVE_MS)
        signal_busy = (not responsive) or converter_alive
        if signal_busy:
            # После проверки: неотзывчивое окно держит её до OP_RESPONSIVE_MS.
            self.last_signal_busy_at = time.perf_counter()
        # CPU — признак занятости, только если подтверждён: два окна
        # подряд выше OP_BUSY_CORE_PCT или одно выше OP_BUSY_STRONG_CORE_PCT.
        # Одиночный всплеск — фон GPU-процесса и рендерера Р7 (1–2 тика
        # таймера, 31–62% ядра на окне 50 мс), он есть и без всякой
        # операции. Раньше такой всплеск время от времени «ловился», и
        # детектор ждал, пока он утихнет: вставка 1–5 ячеек давала то
        # 0.2 с, то 0.6–1.2 с (живой прогон 29.09.2026). Настоящая работа
        # Р7 идёт с загрузкой 100% ядра и выше — её правило не теряет.
        cpu_busy = env.PSUTIL_OK and (
            self.last_cpu >= app.OP_BUSY_STRONG_CORE_PCT
            or (self.last_cpu >= app.OP_BUSY_CORE_PCT and self.prev_cpu >= app.OP_BUSY_CORE_PCT))
        return signal_busy or cpu_busy

    def _heavy_calc_prompt(self, busy, now):
        """Модалка тяжёлого пересчёта может всплыть и после операции
        (большая вставка). Пока она ждёт ответа, Р7 простаивает, и
        детектор закрыл бы замер ДО пересчёта. Закрываем «Нет», время
        ожидания ответа относим к собственным паузам (_paced_total)."""
        app = self.app
        if busy or now - self.last_prompt_check < app.HEAVY_CALC_CHECK_SEC:
            return None
        self.last_prompt_check = now
        if not app._dismiss_heavy_calc_prompt(self.log_cb):
            return None
        clicked_at = time.perf_counter()
        shown_since = self.idle_since if self.idle_since is not None else now
        exact = getattr(app, "_last_prompt_wait_sec", None)
        app._paced_total += (exact if exact is not None
                             else max(0.0, clicked_at - shown_since))
        self.last_signal_busy_at = clicked_at
        self.idle_streak = 0
        self.idle_since = None
        return self.CONTINUE

    def _verdict(self, busy, now):
        """Итог опроса: (момент, статус) или None — ждать дальше."""
        if busy:
            self.seen_busy = True
            self.idle_streak = 0
            self.idle_since = None
            return None
        if self.idle_streak == 0:
            # Простой начался не раньше начала простойного CPU-окна
            # и не раньше последнего опроса с другим признаком
            # занятости.
            self.idle_since = max(self.cpu_win_start, self.last_signal_busy_at or self.start)
        self.idle_streak += 1
        if self.seen_busy and self.idle_streak >= self.app.OP_IDLE_SAMPLES:
            return self.idle_since, "ok"
        if not self.seen_busy and now - self.start >= self.start_grace:
            # Р7 вообще не стал занятым — операция быстрее, чем мы умеем мерить.
            return self.start, "below_floor"
        return None

    # ── цикл ──────────────────────────────────────────────────────────────
    def run(self):
        while time.perf_counter() < self.deadline:
            now = time.perf_counter()
            self._refresh_hwnd()
            self._adopt(now)
            converter_alive = self._converter_alive()
            self._poll_cpu(now)
            busy = self._busy(converter_alive)
            if self._heavy_calc_prompt(busy, now) is self.CONTINUE:
                continue
            res = self._verdict(busy, now)
            if res is not None:
                return res
            time.sleep(self.app.OP_POLL_SEC)
        self.log_cb(f"   ⚠️ Р7-Офис не освободился за {self.max_wait:.0f} сек")
        return None, "timeout"
