"""Конец операции: когда Р7 закончил работу, начатую замеряемым действием.

На CDP-пути — пинг редактора (_wait_renderer_idle, точность ~1 мс), на
клавиатурном — простой CPU процессов Р7 (_wait_operation_done), у экспорта
— запись файла (_wait_for_export_file). Правило 2 CLAUDE.md. OpEndMixin —
методы, которые R7Testovarka получает наследованием.
"""
import time
from pathlib import Path
from r7 import env
from r7.env import psutil, win32gui
from r7.processes import _is_crash_snapshot


class OpEndMixin:
    """Детекторы конца операции — часть R7Testovarka."""

    # ── Конец операции: пороги детектора ──────────────────────────────────
    # Операция считается завершённой, когда Р7 перестал быть занятым. Занятость
    # определяется по двум признакам сразу: окно не прокачивает очередь
    # сообщений ИЛИ процессы Р7 грузят CPU (плюс отдельно — жив ли конвертер x2t).
    OP_POLL_SEC         = 0.05   # шаг опроса состояния Р7
    OP_RESPONSIVE_MS    = 40     # окно не ответило за это — считаем занятым
    # Шкала — % одного ядра, см. комментарий у READY_IDLE_CORE_PCT. Порог выше,
    # чем у READY_IDLE_CORE_PCT: окно усреднения здесь короче (OP_CPU_WINDOW_SEC
    # против READY_POLL_SEC·READY_IDLE_SAMPLES), и короткое окно дрожит сильнее.
    OP_BUSY_CORE_PCT    = 25.0   # % одного ядра: сумма по процессам Р7 не ниже — занято
                                 # (если держится два окна подряд, см. ниже)
    OP_BUSY_STRONG_CORE_PCT = 60.0  # одно окно выше — занято сразу. Фон GPU/рендерера
                                    # на окне 0.2 с — до ~25% (живой замер 29.09.2026)
    OP_CPU_WINDOW_SEC   = 0.20   # окно усреднения CPU: квант GetProcessTimes ≈15.6 мс,
                                 # на окне 50 мс это давало бы шум в десятки процентов
    OP_IDLE_SAMPLES     = 6      # подряд «не занято» → операция завершена (0.3 с)
    OP_START_GRACE_SEC  = 1.00   # ждём начала работы столько, прежде чем признать
                                 # операцию слишком быстрой для измерения.
                                 # В замер это ожидание НЕ попадает — стоит только
                                 # времени прогона, поэтому взято с запасом
    OP_CDP_TAIL_GRACE_SEC = 0.45 # после вызова api: хвост операции начинается сразу —
                                 # хватает на две 0.2-секундные выборки CPU подряд
                                 # (запасной путь, если пинг редактора недоступен)
    # Конец операции на CDP-пути — по пингу редактора (_wait_renderer_idle).
    OP_PING_FAST_SEC  = 0.010    # ответ быстрее — поток редактора свободен (обычно 0–4 мс)
    OP_PING_QUIET_SEC = 0.30     # столько подряд свободен → операция завершена
    OP_PING_GAP_SEC   = 0.05     # пауза между пингами в окне тишины
    OP_PROC_REFRESH_SEC = 0.50   # пересбор списка процессов (ловим x2t)
    OP_MAX_WAIT_SEC     = 180    # предохранитель на одну операцию
    OP_SELECT_ALL_MAX_SEC = 20   # отдельный, куда более короткий предохранитель
                                 # для Ctrl+A: выделив 25 млн ячеек, Р7 считает
                                 # по ним агрегаты в статусной строке и держит
                                 # CPU занятым десятками секунд. Общие 180 с
                                 # выглядели как зависание приложения; честнее
                                 # отметить операцию как timeout и идти дальше
    # save_as_format(): прямое ожидание появления/дозаписи файла экспорта —
    # независимая от CPU/PID-эвристик подстраховка. Живой прогон на 50K
    # показал разброс 0.009 → 48 сек на одной и той же операции: x2t иногда
    # укладывается в окно между двумя опросами CPU (OP_CPU_WINDOW_SEC) и
    # busy-детектор его просто не ловит.
    OP_EXPORT_FILE_POLL_SEC      = 0.05   # шаг опроса
    OP_EXPORT_FILE_STABLE_CHECKS = 8      # опросов подряд с неизменным размером (0.4 с) = файл
                                          # дописан. Длительность окна в замер не идёт: конец
                                          # экспорта берётся по mtime (см. _wait_for_export_file)
    OP_EXPORT_FILE_TIMEOUT_SEC   = 120.0  # первая калибровка (живой прогон видел ~48 сек)
    OP_DIALOG_PACE      = 0.60   # отрисовка МОДАЛЬНОГО диалога («Вставить ячейки»).
                                 # OP_MENU_PACE=0.12 для него мало: модалка Р7 —
                                 # HTML внутри CEF, и на нагруженном документе она
                                 # не успевает появиться за 120 мс. Enter уходил в
                                 # сетку, а диалог оставался висеть (см. PR #4:
                                 # второй Enter добавили, но гонку не убрали)
    OP_DIALOG_ATTEMPTS  = 3      # столько раз подтверждаем модалку (см. _confirm_modal_enter)


    def _resolve_op_end(self, done_ts, status):
        """Уточняет конец операции, если тест-функция сама дождалась результата.

        save_as_format ждёт файл экспорта внутри себя (_wait_for_export_file)
        и кладёт время последней записи файла в self._op_completed_at. К
        моменту _wait_operation_done x2t уже мёртв, Р7 простаивает, и
        детектор честно отвечает below_floor — а HTML помечал 100-секундный
        экспорт в PDF как «<порога» (аудит 29.09.2026). Если детектор видел
        работу Р7 и после файла (status ok), берётся его момент — он позже.

        Returns:
            tuple[float | None, str]: (момент конца, статус).
        """
        completed = getattr(self, "_op_completed_at", None)
        if completed is not None and status != "timeout":
            # Файл записан — экспорт закончен. Активность Р7 после этого
            # (сборка мусора, перерисовка) — не экспорт: с окном старта 6 с
            # детектор ловил её и сдвигал конец на секунды.
            return completed, "ok"
        return done_ts, status

    def _idle_share_of_pause(self, dur, c0, c1):
        """Сколько из паузы длиной dur Р7 простаивал — см. _pace.

        Args:
            dur: Длительность паузы, сек.
            c0, c1: Процессорное время Р7 в начале и в конце (None — неизвестно).

        Returns:
            float: Секунды, которые можно вычесть из замера.
        """
        if c0 is None or c1 is None or dur <= 0:
            return dur
        busy = max(0.0, c1 - c0)
        if busy / dur * 100.0 < self.OP_BUSY_CORE_PCT:
            return dur
        return dur * max(0.0, 1.0 - busy / dur)

    def _flush_pending_modal_confirm(self, log_cb=None):
        """Досылает страховочные Enter'ы по модалке — уже ВНЕ окна замера.

        Нужно на случай, когда модалка не успела появиться за OP_DIALOG_PACE:
        тогда подтверждающий Enter из _confirm_modal_enter ушёл в сетку, модалка
        всплыла позже и висит. Здесь она добивается, не искажая цифру: замер к
        этому моменту уже закрыт (_wait_operation_done отработал), поэтому пауза
        обычная time.sleep, а не _pace.

        Лишний Enter безвреден: если модалки нет, он лишь сдвигает активную
        ячейку на строку вниз, а следующий тест всё равно начинается с Ctrl+Home.
        Последовательность самоисправляющаяся — какой бы из Enter'ов ни совпал с
        появлением модалки, она закроется.

        Вызывать в обоих воркерах сразу после _wait_operation_done (см. правило
        зеркалирования в CLAUDE.md).

        Args:
            log_cb: Функция логирования; по умолчанию self.add_test_log.
        """
        if not self._pending_modal_confirm:
            return
        self._pending_modal_confirm = False
        # Диагностика (один раз за прогон): что на самом деле висит на экране
        # после подтверждающего Enter. Именно эти подписи нужны, чтобы чинить
        # слепую навигацию стрелками по контекстному меню — сейчас счётчик
        # `down` подобран вслепую и уезжает, стоит меню обзавестись пунктом.
        # Стоит вне замера, поэтому на цифры не влияет.
        self._cdp_dump_ui("после подтверждения модалки «Вставить ячейки»", log_cb=log_cb)
        for _ in range(max(0, self.OP_DIALOG_ATTEMPTS - 1)):
            time.sleep(self.OP_DIALOG_PACE)
            try:
                self._press('enter')
            except Exception as e:
                (log_cb or self.add_test_log)(
                    f"   ⚠️ Не удалось дослать Enter по модалке: {e}")
                return

    def _cdp_settle(self, connector=None, max_wait=5.0):
        """Ждёт, пока редактор доделает предыдущий шаг: пинг до быстрого ответа.

        Замена слепой паузе между шагами CDP-цепочки (копирование → вставка):
        пауза вычиталась из замера по процессорному времени Р7, а оно
        квантуется тиком 15.6 мс — шум ±16 мс на операции в 230 мс. Пинг
        возвращается ровно тогда, когда редактор свободен (после копирования —
        через 15–120 мс), и это время — работа Р7, оно остаётся в замере.

        Returns:
            bool: True — редактор ответил быстро (свободен).
        """
        if connector is None:
            connector = self._cdp_ops_connector()
        if connector is None:
            return False
        deadline = time.perf_counter() + max_wait
        while time.perf_counter() < deadline:
            t0 = time.perf_counter()
            if not connector.ping(timeout=max_wait):
                return False
            if time.perf_counter() - t0 <= self.OP_PING_FAST_SEC:
                return True
        return False

    def _wait_renderer_idle(self, log_cb=None):
        """Конец операции на CDP-пути: момент, когда редактор снова свободен.

        Трассировка на живом Р7 (30.09.2026, автосохранение отключено): после
        каждого вызова api редактор свободен через 0–4 мс, после копирования —
        через 15–120 мс (дописывается буфер обмена). Позже идут только
        редкие многопоточные всплески CPU — фоновая сборка мусора, интерфейс
        она не блокирует. Опрос CPU время от времени принимал такой всплеск
        за продолжение операции и прибавлял 0.2–1 с (Ctrl+A: 0.84 / 1.88 /
        0.84 с при вызове 0.84 с).

        Пинг (R7WebDriverConnector.ping) отвечает, когда главный поток
        редактора обработал пустую задачу. Медленный ответ = редактор был
        занят до момента ответа; конец операции — момент последнего
        медленного ответа, либо возврат вызова api, если медленных не было.
        Окно OP_PING_QUIET_SEC ловит отложенную работу, которая стартует
        сразу после вызова (так ведёт себя автосохранение: первый пинг 3 мс,
        второй — 2.7 с).

        Модалка тяжёлого пересчёта после операции: редактор простаивает,
        дожидаясь ответа, — закрываем «Нет», ожидание ответа относим к
        собственным паузам, дальше ждём конец пересчёта.

        Returns:
            tuple[float | None, str] | None: (момент, "ok"/"timeout");
            None — пинг недоступен (нет CDP, обрыв), вызывающий код
            переходит на опрос CPU.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        connector = self._cdp_ops_connector()
        if connector is None:
            return None
        start = time.perf_counter()
        max_wait = getattr(self, "_op_max_wait", None) or self.OP_MAX_WAIT_SEC
        deadline = start + max_wait
        end = start                  # возврат вызова api
        quiet_since = None
        prompt_checked = False
        while True:
            t0 = time.perf_counter()
            if t0 >= deadline:
                log_cb(f"   ⚠️ Р7-Офис не освободился за {max_wait:.0f} сек")
                return None, "timeout"
            answered = connector.ping(timeout=max(1.0, deadline - t0))
            t1 = time.perf_counter()
            if not answered:
                if not getattr(connector, "connected", False):
                    return None          # обрыв — дальше по CPU
                # Таймаут пинга: редактор всё ещё занят. Пауза — на случай
                # мгновенного отказа: без неё цикл крутился до дедлайна и
                # отнимал у Р7 ядро (аудит 06.10.2026). Конец операции берётся
                # из отметок времени, пауза в цифру не попадает.
                time.sleep(self.OP_PING_GAP_SEC)
                continue
            if t1 - t0 > self.OP_PING_FAST_SEC:
                end = t1                 # был занят до этого момента
                quiet_since = None
            else:
                if quiet_since is None:
                    quiet_since = t0
                if t1 - quiet_since >= self.OP_PING_QUIET_SEC:
                    if not prompt_checked:
                        prompt_checked = True
                        if self._dismiss_heavy_calc_prompt(log_cb):
                            clicked_at = time.perf_counter()
                            exact = getattr(self, "_last_prompt_wait_sec", None)
                            self._paced_total += (exact if exact is not None
                                                  else max(0.0, clicked_at - end))
                            end = clicked_at
                            quiet_since = None
                            prompt_checked = False
                            continue
                    return end, "ok"
            time.sleep(self.OP_PING_GAP_SEC)

    def _wait_operation_done(self, hwnd, log_cb=None, start_grace=None):
        """Ждёт, пока Р7-Офис закончит обрабатывать только что отправленную операцию.

        Прежде замер операции заканчивался на последнем нажатии клавиши:
        pyautogui только кладёт события во входную очередь и возвращается сразу,
        поэтому «время операции» равнялось сумме собственных задержек скрипта.
        Здесь секундомер останавливается по реальному признаку — Р7 перестал
        быть занятым.

        Занятость определяется по трём признакам (любой означает «занят»):
          1. Окно не прокачивает очередь сообщений за OP_RESPONSIVE_MS.
          2. Процессы Р7 грузят CPU не ниже OP_BUSY_CORE_PCT (% одного ядра).
          3. Жив конвертер x2t — им идёт экспорт в PDF.

        Возвращается момент НАЧАЛА простоя, а не конец окна подтверждения,
        поэтому OP_IDLE_SAMPLES не добавляется к результату замера. Все
        моменты — time.perf_counter(): вызывающий код обязан засекать старт
        тем же счётчиком.

        Начало простоя — не момент опроса, на котором детектор его заметил,
        а начало CPU-окна, в котором загрузка упала ниже порога (но не
        раньше последнего опроса с другим признаком занятости). Раньше
        бралось время опроса, и результат опаздывал на 0.2–0.4 с: для
        операций короче секунды это 30–50% ошибки (аудит 29.09.2026).
        Остаточная погрешность — в пределах одного OP_CPU_WINDOW_SEC, в
        сторону завышения.

        Args:
            hwnd: Дескриптор окна Р7 либо функция его поиска.
            log_cb: Функция логирования; по умолчанию self.add_test_log.
            start_grace: Сколько ждать начала работы Р7. По умолчанию
                OP_START_GRACE_SEC; экспорт в PDF просит больше через
                self._op_start_grace, потому что x2t стартует с задержкой.

        Returns:
            tuple[float | None, str]: (момент завершения, статус).
              "ok"          — работа началась и закончилась;
              "below_floor" — Р7 не стал занятым за OP_START_GRACE_SEC, то есть
                              операция быстрее порога измерения;
              "timeout"     — не дождались за OP_MAX_WAIT_SEC (момент — None).
        """
        if log_cb is None:
            log_cb = self.add_test_log

        # Операция ушла через api — конец определяет сам редактор (пинг),
        # а не опрос CPU. Явное окно старта (экспорт) оставляет прежний путь.
        if (start_grace is None and getattr(self, "_op_via_cdp", False)
                and getattr(self, "_op_start_grace", None) is None):
            res = self._wait_renderer_idle(log_cb)
            if res is not None:
                return res

        if start_grace is None:
            start_grace = getattr(self, "_op_start_grace", None)
        if start_grace is None and getattr(self, "_op_via_cdp", False):
            # Операция ушла через api: к возврату вызова она уже отработала, и
            # настоящий асинхронный хвост (вставка — ещё 2–3 с при 100%+ ядра)
            # начинается сразу. Занятость, начавшаяся позже, — фон Р7, а не
            # операция: с общим окном в 1 с она время от времени попадала в
            # замер, и Ctrl+A в полном прогоне дал 0.84 / 1.88 / 0.84 с при
            # вызове api 0.84 с каждый раз (30.09.2026).
            start_grace = self.OP_CDP_TAIL_GRACE_SEC
        if start_grace is None:
            start_grace = self.OP_START_GRACE_SEC

        # Предохранитель на операцию — как и start_grace, его может укоротить
        # сама тест-функция через self._op_max_wait (см. select_all).
        max_wait = getattr(self, "_op_max_wait", None) or self.OP_MAX_WAIT_SEC
        start    = time.perf_counter()
        deadline = start + max_wait

        cur_hwnd     = None if callable(hwnd) else hwnd
        tracked      = {}     # pid -> (psutil.Process с «прогретым» CPU, имя)
        last_refresh = 0.0
        last_cpu_at  = 0.0
        last_cpu     = 0.0
        prev_cpu     = 0.0      # CPU предыдущего окна — для подтверждения занятости
        cpu_win_start = start   # начало окна, к которому относится last_cpu
        last_signal_busy_at = None   # последний опрос с «не отвечает» / живым x2t
        seen_busy    = False
        idle_streak  = 0
        idle_since   = None
        last_prompt_check = 0.0

        while time.perf_counter() < deadline:
            now = time.perf_counter()

            if callable(hwnd):
                if not (cur_hwnd and env.WIN32_OK and win32gui.IsWindow(cur_hwnd)):
                    cur_hwnd = hwnd()

            if env.PSUTIL_OK and now - last_refresh >= self.OP_PROC_REFRESH_SEC:
                last_refresh = now
                self._r7_pids = None
                for p in self._get_r7_processes(log_cb=log_cb):
                    if p.pid in tracked:
                        continue
                    try:
                        name = (p.name() or "").lower()
                        p.cpu_percent(None)
                        tracked[p.pid] = (p, name)
                    # процесс завершился до первого замера CPU — считать нечего
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

            # x2t проверяем на каждом опросе — он короткоживущий, и лишние
            # 0.2 сек ожидания его смерти уехали бы прямо в замер PDF-экспорта.
            converter_alive = False
            for pid, (p, name) in list(tracked.items()):
                if "x2t" not in name:
                    continue
                try:
                    if p.is_running() and not _is_crash_snapshot(p):
                        converter_alive = True
                    else:
                        tracked.pop(pid, None)
                except Exception:
                    tracked.pop(pid, None)

            if env.PSUTIL_OK and now - last_cpu_at >= self.OP_CPU_WINDOW_SEC:
                cpu_win_start = last_cpu_at or start
                last_cpu_at = now
                prev_cpu = last_cpu
                total = 0.0
                dead  = []
                for pid, (p, _name) in tracked.items():
                    try:
                        total += p.cpu_percent(None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        dead.append(pid)
                for pid in dead:
                    tracked.pop(pid, None)
                # Сырая сумма в % одного ядра — см. READY_IDLE_CORE_PCT
                # (measure_schema 3): нормировка на число ядер прятала
                # однопоточную работу Р7 на многоядерных стендах.
                last_cpu = total

            responsive = self._window_responsive(cur_hwnd, self.OP_RESPONSIVE_MS)
            signal_busy = (not responsive) or converter_alive
            if signal_busy:
                # После проверки: неотзывчивое окно держит её до OP_RESPONSIVE_MS.
                last_signal_busy_at = time.perf_counter()
            # CPU — признак занятости, только если подтверждён: два окна
            # подряд выше OP_BUSY_CORE_PCT или одно выше OP_BUSY_STRONG_CORE_PCT.
            # Одиночный всплеск — фон GPU-процесса и рендерера Р7 (1–2 тика
            # таймера, 31–62% ядра на окне 50 мс), он есть и без всякой
            # операции. Раньше такой всплеск время от времени «ловился», и
            # детектор ждал, пока он утихнет: вставка 1–5 ячеек давала то
            # 0.2 с, то 0.6–1.2 с (живой прогон 29.09.2026). Настоящая работа
            # Р7 идёт с загрузкой 100% ядра и выше — её правило не теряет.
            cpu_busy = env.PSUTIL_OK and (
                last_cpu >= self.OP_BUSY_STRONG_CORE_PCT
                or (last_cpu >= self.OP_BUSY_CORE_PCT and prev_cpu >= self.OP_BUSY_CORE_PCT))
            busy = signal_busy or cpu_busy

            # Модалка тяжёлого пересчёта может всплыть и после операции
            # (большая вставка). Пока она ждёт ответа, Р7 простаивает, и
            # детектор закрыл бы замер ДО пересчёта. Закрываем «Нет», время
            # ожидания ответа относим к собственным паузам (_paced_total).
            if (not busy and now - last_prompt_check >= self.HEAVY_CALC_CHECK_SEC):
                last_prompt_check = now
                if self._dismiss_heavy_calc_prompt(log_cb):
                    clicked_at = time.perf_counter()
                    shown_since = idle_since if idle_since is not None else now
                    exact = getattr(self, "_last_prompt_wait_sec", None)
                    self._paced_total += (exact if exact is not None
                                          else max(0.0, clicked_at - shown_since))
                    last_signal_busy_at = clicked_at
                    idle_streak = 0
                    idle_since = None
                    continue

            if busy:
                seen_busy   = True
                idle_streak = 0
                idle_since  = None
            else:
                if idle_streak == 0:
                    # Простой начался не раньше начала простойного CPU-окна
                    # и не раньше последнего опроса с другим признаком
                    # занятости.
                    idle_since = max(cpu_win_start, last_signal_busy_at or start)
                idle_streak += 1
                if seen_busy and idle_streak >= self.OP_IDLE_SAMPLES:
                    return idle_since, "ok"
                if not seen_busy and now - start >= start_grace:
                    # Р7 вообще не стал занятым — операция быстрее, чем мы умеем мерить.
                    return start, "below_floor"

            time.sleep(self.OP_POLL_SEC)

        log_cb(f"   ⚠️ Р7-Офис не освободился за {max_wait:.0f} сек")
        return None, "timeout"

    def _wait_for_export_file(self, path_str, timeout=None, log_cb=None):
        """Ждёт, пока x2t допишет файл экспорта (save_as_format).

        _wait_operation_done меряет занятость Р7 по CPU и живому процессу
        x2t — но x2t короткоживущий, и на живом прогоне (50K строк) иногда
        укладывался в промежуток между двумя опросами CPU
        (OP_CPU_WINDOW_SEC=0.2 с) целиком: детектор ни разу не видел «занято»,
        и операция уходила в below_floor с результатом порядка нескольких
        миллисекунд — на той же самой операции, где другой прогон честно
        показывал ~48 сек. Прямая проверка файла на диске не зависит от того,
        успел ли опрос CPU поймать x2t: либо файл есть и дописан, либо нет.

        «Дописан» — размер не меняется OP_EXPORT_FILE_STABLE_CHECKS опросов
        подряд, а не просто наличие файла: x2t создаёт файл и заполняет его
        постепенно, голый exists() поймал бы файл нулевого/частичного размера
        и вернулся бы раньше, чем экспорт реально закончился.

        Вызывается СИНХРОННО внутри тест-функции, ДО _wait_operation_done —
        это не замена детектору, а подстраховка: время ожидания остаётся
        частью замера (не через self._pace()), потому что x2t в это время
        действительно работает.

        Args:
            path_str: Путь к ожидаемому файлу экспорта.
            timeout: Секунд ожидания. По умолчанию OP_EXPORT_FILE_TIMEOUT_SEC.
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            bool: True — файл появился и стабилизировался; False — таймаут.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if timeout is None:
            timeout = self.OP_EXPORT_FILE_TIMEOUT_SEC

        path = Path(path_str)
        deadline = time.perf_counter() + timeout
        last_size = None
        stable = 0
        op_start = getattr(self, "_op_started_at", None)
        while time.perf_counter() < deadline:
            # Ранний выход: x2t этой операции упал, а файла нет — ждать
            # остаток таймаута бессмысленно (раньше — молча 120 с).
            if op_start is not None and not path.exists():
                op_runs = self._x2t_since(op_start)
                failed = [r for r in op_runs if r.get("exit_code") not in (0, None)]
                # Р7 может перезапустить x2t (живой прогон ODS: второй x2t
                # стартовал за секунду до падения первого) — проваленным
                # экспорт считаем, только когда живых x2t этой операции нет.
                still_running = [r for r in op_runs if r.get("end") is None]
                if failed and not still_running:
                    code = failed[-1]["exit_code"] & 0xFFFFFFFF
                    self._export_fail_reason = (
                        f"конвертер x2t упал с кодом {code:#010x} — файл экспорта "
                        f"не записан (ошибка конвертера, а не инструмента)")
                    if code == 0xC0000409:
                        self._export_fail_reason += (
                            "; вероятная причина — лимит памяти x2t "
                            "(X2T_MEMORY_LIMIT, по умолчанию 4 ГБ)")
                    # Р7 показывает окно «Нельзя сохранить…» через 1–3 с после
                    # падения — ждём его, закрываем и кладём текст в ошибку.
                    alert_deadline = time.perf_counter() + self.ALERT_AFTER_X2T_CRASH_SEC
                    while time.perf_counter() < alert_deadline:
                        alerts = self._dismiss_info_alerts(log_cb)
                        if alerts:
                            self._export_fail_reason += "; Р7: " + " / ".join(
                                f"«{t}»" for t in alerts)
                            break
                        time.sleep(0.3)
                    log_cb(f"   ❌ {self._export_fail_reason}")
                    return False
            try:
                st = path.stat()
                size = st.st_size
            except OSError:
                st, size = None, None
            if size is not None and size > 0 and size == last_size:
                stable += 1
                if stable >= self.OP_EXPORT_FILE_STABLE_CHECKS:
                    # Конец экспорта — время ПОСЛЕДНЕЙ ЗАПИСИ файла (mtime),
                    # переведённое в шкалу perf_counter, а не момент, когда мы
                    # заметили стабильный размер: иначе в замер попадали окно
                    # стабильности и всё, что шло между Enter и этим вызовом
                    # (например, до 3 с _dismiss_saveas_format_warning на PDF,
                    # где предупреждения нет). Аудит 29.09.2026. Вызывающий
                    # воркер берёт self._op_completed_at вместо статуса
                    # below_floor детектора.
                    lag = max(0.0, time.time() - st.st_mtime)
                    self._op_completed_at = time.perf_counter() - lag
                    return True
            else:
                stable = 0
            last_size = size
            time.sleep(self.OP_EXPORT_FILE_POLL_SEC)

        log_cb(f"   ⚠️ Файл экспорта не появился/не стабилизировался за "
               f"{timeout:.0f} сек: {path.name}")
        return False
