"""Ожидание готовности документа после открытия — по шагам (до 10, шаг 3).

Прежде это был один цикл _wait_until_r7_ready на 385 строк с пятнадцатью
переменными состояния. Здесь то же самое разложено на шаги класса
ReadinessWait: состояние — атрибуты, каждая проверка — метод. Порядок
проверок, пороги и тексты журнала не менялись; подробное описание признаков
готовности — в докстринге R7Testovarka._wait_until_r7_ready и
docs/readiness.md.

Шаг опроса (READY_POLL_SEC):
  1. окно Р7 (перерешить hwnd, если прежнее исчезло);
  2. процессы Р7 и их CPU (новые — раз в READY_PROC_REFRESH_SEC; все исчезли
     — Р7 упал);
  3. модалка тяжёлого пересчёта (ответ «Нет», ожидание вычитается);
  4. основной маркер — кнопка «Жирный» через CDP;
  5. разовая проба кнопки через win32gui в начале простоя;
  6. запасной путь — серия простоя CPU (без CDP — один Esc на модалку).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from r7 import env, windows
from r7.env import psutil
from r7.processes import _is_crash_snapshot

LogCb = Callable[[str], object]
HwndArg = int | Callable[[], int | None] | None


class ReadinessWaitHost(Protocol):
    """Что ReadinessWait берёт у приложения (R7Testovarka: ReadinessMixin,
    BoldButtonMixin, WindowsMixin, ProcessesMixin) — пороги, итог готовности и
    методы проб. Нужен только mypy: так проверяется сам ReadinessWait, не весь
    класс приложения. _last_prompt_wait_sec читается через getattr — его тут нет."""

    READY_POLL_SEC: float
    READY_PROC_REFRESH_SEC: float
    READY_IDLE_CORE_PCT: float
    READY_IDLE_SAMPLES: int
    READY_MIN_BUSY_SEC: float
    READY_ESC_WITHOUT_CDP: bool
    HEAVY_CALC_CHECK_SEC: float
    BOLD_STABLE_SEC: float
    BOLD_BUTTON_TIMEOUT_SEC: float
    _r7_pids: Any
    _ready_at: float | None
    _ready_marker: str | None

    def _get_r7_processes(self, log_cb: LogCb | None = None,
                          fresh: bool = False) -> list[Any]: ...

    def _window_responsive(self, hwnd: int | None, timeout_ms: int | None = None) -> bool: ...

    def _dismiss_heavy_calc_prompt(self, log_cb: LogCb | None = None) -> bool: ...

    def _bold_ready_probe(self) -> dict[str, Any] | None: ...

    def _ready_marker_label(self) -> str: ...

    def _wait_for_bold_button(self, hwnd: int | None, timeout: float | None = None) -> bool: ...

    def _find_bold_button_hwnd(self, hwnd: int | None) -> int | None: ...

    def _early_connector(self) -> Any: ...

    def _press_esc_in_r7(self, hwnd: int | None) -> bool: ...


def wait_without_psutil(app: ReadinessWaitHost, hwnd: HwndArg, deadline: float,
                        log_cb: LogCb) -> bool:
    """Без psutil остаётся только отзывчивость окна. Этого мало, чтобы
    поймать фоновую загрузку, поэтому — короткая фиксированная выдержка, и
    в журнал честно пишется, как определена готовность."""
    log_cb("⚠️ psutil недоступен — готовность определяется только по отзывчивости окна")
    while time.perf_counter() < deadline:
        h = hwnd() if callable(hwnd) else hwnd
        if app._window_responsive(h):
            app._ready_at = time.perf_counter()
            app._ready_marker = "responsive_only"
            time.sleep(1.0)
            return True
        time.sleep(app.READY_POLL_SEC)
    app._ready_at = time.perf_counter()
    return False


class ReadinessWait:
    """Одно ожидание готовности: run() → True (готов) / False (таймаут, падение Р7).
    Результат — app._ready_at и app._ready_marker, как у _wait_until_r7_ready."""

    CONTINUE = object()     # шаг просит начать следующий опрос

    def __init__(self, app: ReadinessWaitHost, hwnd: HwndArg, timeout: float,
                 log_cb: LogCb) -> None:
        self.app, self.hwnd, self.timeout, self.log_cb = app, hwnd, timeout, log_cb
        self.start = time.perf_counter()
        self.deadline = self.start + timeout
        self.tracked: dict[int, tuple[Any, str]] = {}  # pid -> (psutil.Process с «прогретым» CPU, имя процесса)
        self.had_procs = False
        self.last_refresh = self.prev_poll = 0.0
        self.idle_streak = 0
        self.idle_since: float | None = None  # начало текущей серии простоя
        self.last_busy_signal: float | None = None  # последний момент «окно не отвечает» / жив x2t
        self.prompt_wait = 0.0         # сколько Р7 ждал ответа на модалку пересчёта
        self.last_prompt_check = 0.0
        self.bold_found = False        # кнопка «Жирный» есть в DOM (CDP)
        self.bold_disabled_seen = False  # на последней пробе кнопка была недоступна
        self.bold_candidate: tuple[float, Any, str] | None = None  # (момент включения, отметка страницы, маркер)
        self.peak_cpu = 0.0
        self.cur_hwnd: int | None = None if callable(hwnd) else hwnd
        self.bold_button_tried = False  # проба win32-кнопки — не чаще раза за вызов
        self.esc_probe: dict[str, Any] | None = None  # Esc без CDP (модалка пересчёта)

    # ── итог ──────────────────────────────────────────────────────────────
    def _ready(self, at: float, marker: str) -> bool:
        self.app._ready_at = at
        self.app._ready_marker = marker
        return True

    # ── процессы ──────────────────────────────────────────────────────────
    def _adopt(self) -> int:
        """Добавляет в tracked новые процессы Р7. Возвращает их число.

        Первый cpu_percent(None) у процесса задаёт базу отсчёта и всегда
        возвращает 0.0, поэтому он делается здесь, а не в замере.
        Имя запоминается сразу, чтобы не дёргать name() на каждом опросе.
        """
        added = 0
        self.app._r7_pids = None   # форсируем полное сканирование, чтобы поймать x2t
        for p in self.app._get_r7_processes(log_cb=self.log_cb):
            if p.pid in self.tracked:
                continue
            try:
                name = (p.name() or "").lower()
                p.cpu_percent(None)
                self.tracked[p.pid] = (p, name)
                added += 1
            # процесс завершился до первого замера CPU — считать нечего
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return added

    def _poll_cpu(self, now: float) -> tuple[float, bool]:
        """Пересобирает список процессов (раз в READY_PROC_REFRESH_SEC: x2t
        стартует уже после появления окна редактора) и суммирует CPU.
        Returns: (total_cpu в % одного ядра, жив ли конвертер x2t)."""
        if now - self.last_refresh >= self.app.READY_PROC_REFRESH_SEC:
            self.last_refresh = now
            if self._adopt():
                # Появился новый процесс — начинаем подтверждение заново.
                self.idle_streak = 0
                self.had_procs = True
        total_cpu, converter_alive = 0.0, False
        dead: list[int] = []
        for pid, (p, name) in self.tracked.items():
            try:
                total_cpu += p.cpu_percent(None)
                if "x2t" in name and not _is_crash_snapshot(p):
                    converter_alive = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                dead.append(pid)
        for pid in dead:
            self.tracked.pop(pid, None)
        return total_cpu, converter_alive

    # ── модалка тяжёлого пересчёта ────────────────────────────────────────
    def _heavy_calc_prompt(self, now: float, window_start: float, total_cpu: float,
                          converter_alive: bool) -> object | None:
        """Модалка тяжёлого пересчёта (2026.3+): пока она висит, CPU простаивает,
        и без этой проверки готовность объявлялась ДО пересчёта. Ищем её на
        простое — и тогда, когда кнопка «Жирный» найдена, но недоступна: это и
        есть признак модалки, а CPU при ней может держаться выше порога
        (живой прогон 29.09.2026: открытие 13.8 с вместо 7.9). Ожидание ответа
        — ожидание пользователя, а не работа Р7: вычитается (prompt_wait)."""
        app = self.app
        if (converter_alive
                or not (total_cpu < app.READY_IDLE_CORE_PCT or self.bold_disabled_seen)
                or now - self.last_prompt_check < app.HEAVY_CALC_CHECK_SEC):
            return None
        self.last_prompt_check = now
        if not app._dismiss_heavy_calc_prompt(self.log_cb):
            return None
        clicked_at = time.perf_counter()
        shown_since = self.idle_since if self.idle_since is not None else window_start
        exact = getattr(app, "_last_prompt_wait_sec", None)
        self.prompt_wait += exact if exact is not None else max(0.0, clicked_at - shown_since)
        self.last_busy_signal = clicked_at
        self.idle_streak = 0
        self.idle_since = None
        self.bold_button_tried = False   # после пересчёта проба заново
        self.bold_candidate = None
        return self.CONTINUE

    # ── основной маркер: кнопка «Жирный» через CDP ───────────────────────
    def _bold_via_cdp(self, converter_alive: bool) -> bool:
        """Кнопка «Жирный» доступна BOLD_STABLE_SEC подряд — документ готов;
        момент — когда она включилась (по часам страницы)."""
        probe = self.app._bold_ready_probe()
        self.bold_disabled_seen = bool(probe and probe.get("found") and probe.get("disabled"))
        if not (probe and probe.get("found")):
            return False
        self.bold_found = True
        if probe.get("disabled") or converter_alive:
            self.bold_candidate = None
            return False
        polled_at = time.perf_counter()
        page_mark = probe.get("enabledAt")
        if page_mark is not None and probe.get("now") is not None:
            # Разность по часам страницы — без сопоставления часов Python и рендерера.
            at = polled_at - max(0.0, (probe["now"] - page_mark) / 1000.0)
            marker = "bold"
        else:
            at, marker = polled_at, "bold_late"
        if self.bold_candidate is None or self.bold_candidate[1] != page_mark:
            self.bold_candidate = (at, page_mark, marker)
            return False
        if polled_at - self.bold_candidate[0] < self.app.BOLD_STABLE_SEC:
            return False
        ready_at = self.bold_candidate[0] - self.prompt_wait
        self.log_cb(f"   📊 Документ открыт за {ready_at + self.prompt_wait - self.start:.2f} сек "
                    f"ожидания: {self.app._ready_marker_label()} доступна"
                    + (" (момент — верхняя оценка: наблюдатель поставлен "
                       "поздно)" if self.bold_candidate[2] == "bold_late" else ""))
        return self._ready(ready_at, self.bold_candidate[2])

    # ── разовая проба кнопки через win32gui ───────────────────────────────
    def _bold_via_win32(self, idle_since: float) -> bool:
        """CDP-кнопка проверяется на каждом опросе. Здесь — только win32gui, на
        случай сборки с классическими Win32-виджетами на панели. Проба
        ограничена оставшимся бюджетом deadline. idle_since — начало серии
        простоя, которое run() только что записал в self.idle_since."""
        app = self.app
        self.bold_button_tried = True
        self.log_cb("⏳ Ожидание кнопки 'Жирный'...")
        btn_timeout = max(0.0, min(app.BOLD_BUTTON_TIMEOUT_SEC,
                                   self.deadline - time.perf_counter()))
        if app._wait_for_bold_button(self.cur_hwnd, timeout=btn_timeout):
            self.log_cb("✅ Кнопка 'Жирный' доступна")
            self.log_cb(f"   📊 Документ открыт за {idle_since - self.start:.2f} сек "
                        f"ожидания: кнопка «Жирный» на панели инструментов доступна")
            return self._ready(idle_since - self.prompt_wait, "win32_bold")
        # «Не найдена» и «найдена, но не включилась» — разные диагнозы:
        # сообщение не должно вводить в заблуждение при разборе журнала.
        if app._find_bold_button_hwnd(self.cur_hwnd) is None:
            self.log_cb("⚠️ Кнопка 'Жирный' не найдена, использую fallback")
        else:
            self.log_cb(f"⚠️ Кнопка 'Жирный' найдена, но не стала доступна за "
                        f"{btn_timeout:.1f} сек, использую fallback")
        return False

    # ── запасной путь: серия простоя CPU ──────────────────────────────────
    def _cpu_idle_series(self, total_cpu: float) -> object | None:
        """Серия простоя набрана, а кнопки в DOM нет — готовность по CPU.

        Без CDP модалку пересчёта не увидеть: она HTML, окна ОС у неё нет, а
        CPU при ней простаивает. Один раз шлём Esc (у этой модалки он равен
        «Нет», на сетке без модалки ничего не делает) и ждём ещё одну серию.
        Р7 занялся работой — модалка была: её ожидание вычитается. Не занялся
        — готовность с первого простоя."""
        app = self.app
        # idle_since не None, пока idle_streak > 0: run() ставит его на первом
        # простое серии и сбрасывает вместе с серией. Проверка — для mypy.
        if (self.idle_streak < app.READY_IDLE_SAMPLES or self.bold_found
                or self.idle_since is None):
            return None
        idle_since: float = self.idle_since
        if (self.esc_probe is None and app.READY_ESC_WITHOUT_CDP
                and app._early_connector() is None
                and app._press_esc_in_r7(self.cur_hwnd)):
            self.esc_probe = {"first_idle": self.idle_since,
                              "at": time.perf_counter(), "busy": False}
            self.idle_streak = 0
            self.idle_since = None
            return self.CONTINUE
        if self.esc_probe is not None and not self.esc_probe["busy"]:
            idle_since = self.idle_since = self.esc_probe["first_idle"]
        elif self.esc_probe is not None:
            waited = self.esc_probe["at"] - self.esc_probe["first_idle"]
            self.prompt_wait += max(0.0, waited)
            self.log_cb("   🧮 После Esc Р7 занялся работой — была модалка "
                        "«Автоматический пересчёт может занять время» (ответ «Нет»); "
                        f"ожидание ответа {waited:.2f} с из открытия вычтено")
        ready_at = idle_since - self.prompt_wait
        marker = "cpu_esc" if self.esc_probe is not None and self.esc_probe["busy"] else "cpu"
        self.log_cb(f"   📊 Документ открыт за {ready_at - self.start:.2f} сек ожидания: "
                    f"CPU процессов Р7 упал до {total_cpu:.1f}% ядра "
                    f"(пик {self.peak_cpu:.1f}%), окно отзывчиво")
        return self._ready(ready_at, marker)

    # ── цикл ──────────────────────────────────────────────────────────────
    def run(self) -> bool:
        app, log_cb = self.app, self.log_cb
        log_cb("⏳ Ожидание готовности документа (отзывчивость окна + простой CPU)...")
        self._adopt()
        self.had_procs = bool(self.tracked)
        self.last_refresh = self.prev_poll = time.perf_counter()
        while time.perf_counter() < self.deadline:
            time.sleep(app.READY_POLL_SEC)
            now = time.perf_counter()
            # cpu_percent(None) отдаёт загрузку за окно (prev_poll, now]: если оно
            # простойное, простой начался не позже prev_poll.
            window_start, self.prev_poll = self.prev_poll, now
            # Окно перерешается, только когда прежнее перестало быть окном
            # (Р7 может заменить top-level окно после сплэша).
            if callable(self.hwnd):
                if not (self.cur_hwnd and env.WIN32_OK and windows.is_window(self.cur_hwnd)):
                    self.cur_hwnd = self.hwnd()

            total_cpu, converter_alive = self._poll_cpu(now)
            # Процессы Р7 были и исчезли — приложение упало; ждать до конца
            # таймаута бессмысленно.
            if self.had_procs and not self.tracked:
                log_cb("❌ Все процессы Р7-Офис исчезли — приложение завершилось "
                       "или упало во время открытия файла")
                app._ready_at = time.perf_counter()
                return False

            self.peak_cpu = max(self.peak_cpu, total_cpu)
            responsive = app._window_responsive(self.cur_hwnd)
            if not responsive or converter_alive:
                # Момент ПОСЛЕ проверки: неотзывчивое окно держит её до
                # READY_RESPONSIVE_MS, и всё это время Р7 заведомо занят.
                self.last_busy_signal = time.perf_counter()
            # Пока жив x2t — документ ещё конвертируется, каким бы низким ни был CPU.
            base_idle = (responsive and self.tracked and not converter_alive
                         and total_cpu < app.READY_IDLE_CORE_PCT
                         and now - self.start >= app.READY_MIN_BUSY_SEC)

            if self._heavy_calc_prompt(now, window_start, total_cpu,
                                       converter_alive) is self.CONTINUE:
                continue
            if self._bold_via_cdp(converter_alive):
                return True

            if base_idle and self.idle_streak == 0:
                # Простой — с начала простойного CPU-окна, но не раньше
                # последнего признака занятости (цикл с неотзывчивым окном длится
                # до 0.45 с — поймано живым прогоном).
                idle_since = max(window_start, self.last_busy_signal or self.start)
                self.idle_since = idle_since
                if not self.bold_button_tried and self._bold_via_win32(idle_since):
                    return True

            if base_idle:
                self.idle_streak += 1
            else:
                self.idle_streak = 0
                self.idle_since = None
            if self.esc_probe is not None and not base_idle:
                self.esc_probe["busy"] = True

            step = self._cpu_idle_series(total_cpu)
            if step is self.CONTINUE:
                continue
            if step:
                return True

        log_cb(f"⚠️ Таймаут {self.timeout} сек: готовность не подтверждена "
               f"(пик CPU за ожидание {self.peak_cpu:.1f}% ядра"
               + ("; кнопка «Жирный» так и не стала доступной" if self.bold_found else "")
               + "), продолжаем тест")
        app._ready_at = time.perf_counter() - self.prompt_wait
        app._ready_marker = "timeout"
        return False
