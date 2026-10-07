"""Конец операции: когда Р7 закончил работу, начатую замеряемым действием.

На CDP-пути — пинг редактора (_wait_renderer_idle, точность ~1 мс), на
клавиатурном — простой CPU процессов Р7 (_wait_operation_done), у экспорта
— запись файла (_wait_for_export_file). Правило 2 CLAUDE.md. OpEndMixin —
методы, которые R7Testovarka получает наследованием.
"""
import time
from pathlib import Path
from r7.op_wait import OpWait


class OpEndMixin:
    """Детекторы конца операции — часть R7Testovarka."""

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
        return OpWait(self, hwnd, log_cb, start_grace, max_wait).run()

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
