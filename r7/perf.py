"""Прогон вкладки «Производительность»: открытие файла, все выбранные
тесты по r7_ops.SpreadsheetOps, отчёты, закрытие Р7 при любом исходе.

Работает в фоновом потоке; виджеты трогает только через _ui_call (главный поток).
PerfRunMixin — методы, которые R7Testovarka получает наследованием.
"""
import statistics
import subprocess
import threading
import time
from pathlib import Path

from r7 import config, env
from r7.batch_config import find_test_file as _find_fixture
from r7.config import _OPEN_NOT_READY, DEFAULT_TEST_RUNS
from r7.env import psutil
from r7.processes import X2tTracker
from r7.resources import ResourceSampler, _disk_delta, _disk_snapshot, _format_disk
from r7.run_summary import resource_summary, run_leak_verdict
from r7_ops import SpreadsheetOps


class PerfRunMixin:
    """Прогон вкладки «Производительность» — часть R7Testovarka (через наследование)."""

    def _spreadsheet_worker(self, enabled_tests=None, test_runs=None, stop_event=None):
        """Runs selected spreadsheet performance tests sequentially and saves reports.

        Args:
            enabled_tests: Set of test-name strings to execute. None → all tests.
            test_runs: Dict of test-name → run count, snapshotted from
                self.test_runs on the main thread. None → empty dict.
            stop_event: threading.Event — установка прерывает прогон между
                операциями (и между повторами внутри одной операции). Р7-Офис
                при этом закрывается штатно, отчёт по уже выполненным
                операциям сохраняется. None → создаётся локально, никогда не
                устанавливается (для вызовов в обход UI).
        """
        if enabled_tests is None:
            enabled_tests = set(self.TEST_DEFINITIONS)
        if test_runs is None:
            test_runs = {}
        if stop_event is None:
            stop_event = threading.Event()
        self.add_test_log("\n🚀 ЗАПУСК СТРЕСС-ТЕСТА ТАБЛИЦ")
        # Диагностика раньше по вызовам: если пакеты requests/websocket-client
        # не видны интерпретатору, которым реально запущен инструмент (venv
        # vs системный python — см. CLAUDE.md, "смотреть на интерпретатор, а
        # не на код"), CDP-триггер отключается ещё до первой попытки
        # подключения, а без этой строки это неотличимо от "порт занят"/
        # "Р7 запущен без --ascdesktop-support-debug-info".
        self.add_test_log(f"🔌 WebDriver: WEBDRIVER_OK={env.WEBDRIVER_OK}")
        # Окружение — до запуска Р7, пока он не грузит систему (пункт 11 аудита).
        self._run_environment = self._capture_environment()

        # ----- 1. Поиск тестового файла -----
        test_file = self._locate_test_file()
        if not test_file:
            self.add_test_log("❌ Тестовый файл не найден.")
            return
        self.add_test_log(f"✅ Найден файл: {test_file}")

        # ----- 2. Вспомогательные функции для окон -----
        def find_r7_window():
            """Returns the hwnd of the visible R7-Office window, or None —
            только окно процесса Р7 (см. _find_r7_window)."""
            return self._find_r7_window(test_file.stem)

        def focus_window():
            return self._focus_r7_settled(test_file)

        def close_update_dialog(search_timeout=0):
            return self._close_update_dialog_if_exists(search_timeout=search_timeout)

        def post_action_delay(seconds=0.5):
            """Waits after an operation completes — called outside measure() timing window."""
            time.sleep(seconds)

        # ----- 3. Запуск Р7 и замер времени открытия -----
        r7_path = self._find_r7_path()
        if not r7_path:
            self.add_test_log("❌ Р7-Офис не найден.")
            return

        def launch_r7():
            return self._launch_r7(r7_path, test_file)

        # ----- 3.0 Повторное открытие файла (аудит 29.09.2026, пункт 4) -----
        # Одно открытие — одна точка, медиану и MAD из неё не посчитать, а
        # сравнение версий по «Открытию файла» было самым шумным. Лишние
        # циклы «запуск → готовность → закрытие» идут ДО основного запуска,
        # основной — последний повтор, Р7 после него остаётся для операций.
        open_runs_n = 1
        if self.OPEN_TEST_NAME in enabled_tests:
            open_runs_n = max(1, int(test_runs.get(self.OPEN_TEST_NAME, self.DEFAULT_OPEN_RUNS)))
        extra_opens = []   # [{"open_elapsed", "cold_start_ms", "warm_start_ms", "status"}]
        for _k in range(open_runs_n - 1):
            if stop_event.is_set():
                break
            self.add_test_log(f"⏳ Повторное открытие файла: {_k + 1}/{open_runs_n}")
            _l = launch_r7()
            if _l is None:
                self.add_test_log("❌ Окно Р7 не появилось — повторы открытия прерваны.")
                self._terminate_r7_processes(log_cb=self.add_test_log)
                break
            _os, _wts, _ = _l
            _ok = self._wait_until_r7_ready(find_r7_window, timeout=120)
            _disk = _disk_delta(self._open_disk_before, _disk_snapshot(),
                                self._matches_r7_process, self._x2t_since(_os))
            if _disk:
                self.add_test_log(f"   💽 Открытие: {_format_disk(_disk)}")
            _t = self._split_open_timing(_os, _wts, self._ready_at)
            extra_opens.append({"x2t": X2tTracker.summarize(self._x2t_since(_os)),
                                "disk": _disk,
                                "open_elapsed": self._ready_at - _os,
                                "cold_start_ms": _t["cold_start_ms"],
                                "warm_start_ms": _t["warm_start_ms"],
                                "status": "ok" if _ok else "timeout",
                                "ready_marker": self._ready_marker})
            self.add_test_log(f"   ✅ открытие {_k + 1}: {self._ready_at - _os:.3f} сек")
            self._close_r7_gracefully(find_r7_window(), log_cb=self.add_test_log, timeout=15)
            self._close_webdriver_connector()
            # Ждём, пока процессы Р7 уйдут: иначе следующий запуск отдаст
            # файл в живой экземпляр, и это будет уже не холодный старт.
            _gone_deadline = time.perf_counter() + 15
            while time.perf_counter() < _gone_deadline:
                self._r7_pids = None
                if not self._get_r7_processes(log_cb=lambda *_a: None):
                    break
                time.sleep(0.2)
            else:
                self._terminate_r7_processes(log_cb=self.add_test_log)

        _launched = launch_r7()
        if _launched is None:
            self.add_test_log("❌ Окно Р7 не появилось.")
            return
        open_start, _window_appeared_ts, _setup_elapsed = _launched

        # Фоновый мониторинг окна обновления на весь период теста
        _upd_stop = threading.Event()
        threading.Thread(
            target=self._monitor_update_dialog,
            args=(_upd_stop,),
            daemon=True,
        ).start()
        self.add_test_log("🔍 Запущен мониторинг окна обновления (проверка каждые 2 сек)")

        # Фоновый семплер ресурсов (этап 2, H3) — создаётся здесь (не внутри
        # try ниже), тем же паттерном, что и _upd_stop чуть выше: чтобы имя
        # было гарантированно определено к моменту finally, даже если try
        # упадёт на первой же строке. .start() — позже, у начала прогона
        # операций (см. там же), здесь ещё рано: RAM/CPU только формируются
        # открытием файла, замерять эту фазу как часть теста не нужно.
        _resource_sampler = ResourceSampler(
            get_procs=self._get_r7_processes,
            connector=self._webdriver_connector,
            interval=1.0,
            log_cb=self.add_test_log,
        )

        _r7_closed = False   # штатное закрытие прошло — finally не трогает Р7 (G-05)
        try:
            data_ready = self._wait_until_r7_ready(find_r7_window, timeout=120)
            _open_disk = _disk_delta(self._open_disk_before, _disk_snapshot(),
                                     self._matches_r7_process, self._x2t_since(open_start))
            if _open_disk:
                self.add_test_log(f"   💽 Открытие: {_format_disk(_open_disk)}")
            # Начало простоя, а не момент возврата — см. _wait_until_r7_ready.
            _ready_ts = self._ready_at
            # Подготовка окна больше НЕ вычитается (аудит 29.09.2026): Р7
            # грузит документ в своём процессе параллельно с ней, и вычитание
            # занижало открытие на всё время подготовки.
            open_elapsed = _ready_ts - open_start
            # L1: раздельные холодный/тёплый старт — см. _split_open_timing.
            # window_found=True: цикл ожидания окна выше уже вернул бы
            # False на всю функцию, если бы окно не появилось.
            _open_timing = self._split_open_timing(
                open_start, _window_appeared_ts, _ready_ts)
            cold_start_ms = _open_timing["cold_start_ms"]
            warm_start_ms = _open_timing["warm_start_ms"]
            self.add_test_log(
                f"✅ Файл открыт за {open_elapsed:.2f} сек "
                f"(холодный старт {cold_start_ms / 1000:.2f} с, тёплый {warm_start_ms / 1000:.2f} с; "
                f"{'данные загружены' if data_ready else 'таймаут — возможна частичная загрузка'};"
                f" подготовка окна {_setup_elapsed:.2f} сек шла параллельно с загрузкой)")

            if not focus_window():
                _upd_stop.set()
                self.add_test_log("❌ Окно Р7-Офис недоступно после открытия файла — тест прерван")
                return

            # Подключаемся к CDP до снятия базового снимка: без соединения
            # снимок был бы пустым, и вычитать из дампов меню стало бы нечего.
            self._cdp_ensure_connected()
            # Базовый DOM-снимок ДО первой операции — см. _cdp_dump_ui и
            # _capture_cdp_ui_baseline (issue #9).
            self._capture_cdp_ui_baseline()
            # Один раз за запуск: найден ли внутренний api редактора. От этого
            # зависит, пойдут тесты через CDP или клавишами.
            self._cdp_log_api_info()
            self._suspend_autosave()

            # ----- 3.5 Мониторинг ресурсов ------------------------------------------------
            self._r7_pids = None  # сбросить кэш перед новым поиском
            self._x2t_logged_pids = set()  # сбросить дедуп x2t перед новым тестом
            self._restore_unavailable_logged = False
            r7_procs = self._get_r7_processes()
            if env.PSUTIL_OK and r7_procs:
                try:
                    _init_ram = round(
                        sum(p.memory_info().rss for p in r7_procs) / (1024 * 1024), 1
                    )
                    pids_str = ", ".join(str(p.pid) for p in r7_procs)
                    self.add_test_log(
                        f"🔍 Поиск процесса Р7: найдено {len(r7_procs)} процессов "
                        f"(PID: {pids_str}), суммарная RAM = {_init_ram:.1f} МБ"
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    self.add_test_log(f"🔍 Найдено {len(r7_procs)} процессов Р7, RAM недоступна")
            else:
                self.add_test_log(
                    "⚠️ Процесс Р7 не найден — замеры RAM/CPU будут недоступны"
                    if env.PSUTIL_OK else
                    "⚠️ psutil не установлен — замеры RAM/CPU недоступны"
                )

            # ----- 4. Тесты ----------------------------------------------------------------
            sample0 = self._sample_r7_resources(r7_procs)
            # Все повторы открытия: дополнительные циклы + основной запуск.
            _opens = extra_opens + [{
                "open_elapsed": open_elapsed, "cold_start_ms": cold_start_ms,
                "warm_start_ms": warm_start_ms,
                "status": "ok" if data_ready else "timeout",
                "ready_marker": self._ready_marker,
                "x2t": X2tTracker.summarize(self._x2t_since(open_start)),
                "disk": _open_disk}]
            results = [self._open_result(_opens, data_ready, sample0)]

            def run_test_with_runs(name, func, runs):
                """Замер операции вкладки «Производительность» — общий цикл
                повторов _measure_op_repeated (тот же, что у Batch-режима).
                Если тест снят чекбоксом, ничего не делает. Документ не
                загрузился — тоже (зеркало measure в Batch)."""
                if name not in enabled_tests or not data_ready:
                    return
                results.append(self._measure_op_repeated(
                    name, func, runs, find_r7_window, self.add_test_log,
                    stop_event, focus_cb=focus_window, post_delay=post_action_delay))

            # Операции — один набор на оба воркера (r7_ops.SpreadsheetOps):
            # прежде они жили здесь и в Batch двумя копиями, которые
            # приходилось зеркалить вручную (docs/plan-to-8.md, этап 1).
            _ops = SpreadsheetOps(self, find_r7_window, self.add_test_log, test_file)
            _test_ops = _ops.tests()

            def _update_status(text):
                """Safely updates the status bar from this worker thread —
                marshals onto the main thread via root.after and swallows
                errors from a window closed mid-run."""
                try:
                    self._ui_call(lambda: self.status_var.set(text))
                except Exception:  # окно закрыто посреди прогона — статус показывать негде
                    pass

            # Прогресс и статус считаются только по включённым тестам — раньше
            # цикл шёл по всем 13 операциям и показывал «⚙ Название — N/13»
            # даже для снятых чекбоксом тестов, которые run_test_with_runs
            # молча пропускает.
            _active_ops = [op for op in _test_ops if op[0] in enabled_tests]
            # Семплер (запущен раньше, до try — см. комментарий там же)
            # начинает копить точки именно с этого момента: до сих пор RAM/CPU
            # ещё формировались самим открытием файла (переходный процесс), а
            # detect_leak() интересует дрейф ВО ВРЕМЯ теста, не старт.
            _resource_sampler.start()

            _run_start = time.time()
            self._set_perf_progress(0, len(_active_ops))
            for _i, (_name, _func) in enumerate(_active_ops, start=1):
                if stop_event.is_set():
                    self.add_test_log(
                        f"⏹ Остановлено пользователем ({_i - 1}/{len(_active_ops)} тестов выполнено)")
                    break
                _update_status(
                    f"⚙ {_name} — {_i}/{len(_active_ops)} "
                    f"(прошло {time.time() - _run_start:.0f} сек)")
                run_test_with_runs(_name, _func, test_runs.get(_name, DEFAULT_TEST_RUNS))
                self._set_perf_progress(_i, len(_active_ops))
            if not stop_event.is_set():
                _update_status(
                    f"✅ Готово: {len(_active_ops)}/{len(_active_ops)} "
                    f"(всего {time.time() - _run_start:.0f} сек)")
            self._cleanup_x2t_temp_pdfs()

            # ----- 5. Статистика ресурсов --------------------------------------------------
            res = resource_summary(results)
            peak_ram, avg_ram, min_ram = res["peak_ram_mb"], res["avg_ram_mb"], res["min_ram_mb"]
            peak_cpu, peak_cpu_norm = res["peak_cpu_pct"], res["peak_cpu_normalized_pct"]
            if peak_ram is not None:
                self.add_test_log(
                    f"📊 Пик RAM: {peak_ram:.1f} МБ  Средн: {avg_ram:.1f} МБ  Мин: {min_ram:.1f} МБ")
            if peak_cpu is not None:
                self.add_test_log(
                    f"📊 Пик CPU: {peak_cpu:.1f}% (сырое)  {peak_cpu_norm:.1f}% (норм., "
                    f"{psutil.cpu_count() if env.PSUTIL_OK else '?'} ядер)")

            # ── Детектор утечек (этап 2, H3) ────────────────────────────────────
            # Останавливаем сразу после операций теста, до сохранения отчётов и
            # закрытия Р7 — семплер должен покрывать сам прогон, не переходные
            # процессы вокруг него. finally ниже вызовет stop() повторно на
            # случай исключения выше (идемпотентно, безопасно).
            _resource_sampler.stop()
            _resource_sampler.join(timeout=5)
            leak_verdict = run_leak_verdict(_resource_sampler.snapshot())
            if leak_verdict.get("applicable") is False:
                self.add_test_log(f"ℹ️ Утечки памяти: {leak_verdict['verdict']}")

            # ----- 6. Сохранение отчётов ---------------------------------------------------
            ts, HTML_REPORT_PATH = self._write_run_reports(
                results, test_file, open_elapsed, res, leak_verdict, self.add_test_log)

            # ----- 7. Закрытие -------------------------------------------------------------
            _upd_stop.set()
            self.add_test_log("🔍 Мониторинг окна обновления остановлен")
            self.add_test_log("🔚 Закрытие Р7-Офис...")
            self._restore_autosave()
            # Флаг — «процессов Р7 не осталось»: False от _close_r7_gracefully
            # значит лишь «пришлось убить», а не «жив». Прежде флаг ставился
            # без проверки, и Р7 мог остаться (аудит 06.10.2026).
            self._close_r7_gracefully(find_r7_window())
            _r7_closed = self._r7_gone()
            # После «Сохранить как» в XLTX Р7 держит сохранённый файл открытым,
            # и очистка до закрытия его не удаляла (34 МБ в %TEMP% на прогон).
            self._cleanup_x2t_temp_pdfs()
            self.add_test_log("🏁 Тест завершён.")

            # ----- 8. Диалог после теста ---------------------------------------------------
            self._ui_call(lambda: self._show_post_test_dialog(HTML_REPORT_PATH, ts))
        finally:
            # Поток-монитор диалога обновления не должен пережить эту функцию —
            # раньше _upd_stop.set() стоял в линейном коде, и любое исключение
            # выше оставляло монитор сканировать все окна системы до закрытия
            # приложения. CDP/Selenium-соединение и семплер ресурсов — тот же
            # случай: должны остановиться независимо от того, как функция
            # завершилась. _resource_sampler.stop() безопасно вызывать даже
            # если .start() выше так и не случился (исключение до него) —
            # это просто Event.set(), не требует живого потока.
            _upd_stop.set()
            _resource_sampler.stop()
            # Исключение до штатного закрытия — Р7 ещё жив, вернуть настройку
            # пользователя можно. После штатного закрытия это no-op.
            self._restore_autosave()
            if not _r7_closed and not self._emergency_close_r7(find_r7_window):
                self.add_test_log("❌ Р7-Офис не закрылся — закройте его вручную, "
                                  "иначе следующий прогон упрётся в занятый порт CDP")
            self._close_webdriver_connector()

    def _locate_test_file(self):
        """Searches known directories for the 50K-row test spreadsheet.

        Ignores Office lock-файлы (`~$...`) — они появляются, пока файл
        открыт в другом приложении (или остаются после сбоя), и без
        фильтра glob() находил их вместо настоящего файла.

        Returns:
            Path: Path to the found file, or None.
        """
        real_file, lock_files = _find_fixture(
            [self.test_files_folder, config.BASE_DIR, Path.home() / "Downloads",
             Path.home() / "Загрузки", Path.cwd()])

        # Lock-файл рядом с настоящим файлом — не нужен, чистим его
        # заранее, чтобы он не мешал следующему запуску теста.
        for lock in lock_files:
            real_name = lock.name[2:]
            if lock.with_name(real_name).exists():
                self.add_test_log(f"⚠️ Рядом с рабочим файлом найден lock-файл ({lock.name}) — удаляю.")
                try:
                    lock.unlink()
                except OSError as e:
                    self.add_test_log(f"❌ Не удалось удалить lock-файл: {e}")

        if real_file is not None:
            return real_file

        if lock_files:
            lock = lock_files[0]
            self.add_test_log(
                f"⚠️ Настоящий тестовый файл не найден — есть только lock-файл "
                f"({lock.name}). Файл открыт в другом приложении либо остался "
                f"после сбоя. Удаляю lock-файл.")
            try:
                lock.unlink()
            except OSError as e:
                self.add_test_log(f"❌ Не удалось удалить lock-файл: {e}")
            new_path = self.test_files_folder / lock.name[2:]
            try:
                self._generate_fixture(new_path, rows=50_000, profile="flat")
                self.add_test_log(f"✅ Создан новый тестовый файл: {new_path}")
                return new_path
            except Exception as e:
                self.add_test_log(f"❌ Не удалось создать тестовый файл: {e}")

        return None

    def _wait_r7_window(self, title_part, timeout=60):
        """Polls for a visible window containing title_part, sets it foreground when found.

        Args:
            title_part: Substring to search for in window titles.
            timeout: Maximum seconds to wait.

        Returns:
            bool: True if window found, False on timeout.
        """
        import win32gui
        start = time.perf_counter()
        while time.perf_counter() - start < timeout:
            wins = []
            def enum_cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    # Чужое окно с тем же текстом в заголовке (вкладка
                    # браузера) давало «холодный старт 0.00 с».
                    if title_part.lower() in title.lower() and self._is_r7_window(hwnd):
                        wins.append(hwnd)
            win32gui.EnumWindows(enum_cb, wins)
            if wins:
                # Момент появления окна снимается ДО SetForegroundWindow —
                # это граница холодного старта (L1).
                self._window_seen_at = time.perf_counter()
                try:
                    win32gui.SetForegroundWindow(wins[0])
                except Exception:  # Windows отказала в фокусе — окно найдено, фокус ставят позже
                    pass
                return True
            # Шаг опроса = разрешение cold_start_ms. Прежние 0.5 с
            # квантовали холодный старт на полсекунды (аудит 29.09.2026);
            # EnumWindows стоит ~1 мс, 30 мс его не нагружают.
            time.sleep(self.WINDOW_POLL_SEC)
        return False

    def _launch_r7(self, r7_path, test_file):
        """Холодный запуск Р7 с тестовым файлом и подготовка окна.

        Общий код основного запуска и дополнительных циклов «Повторного
        открытия файла» — чтобы они не разъехались (аудит 29.09.2026).

        Returns:
            tuple | None: (open_start, window_appeared_ts, setup_elapsed)
            в perf_counter, либо None, если окно не появилось.
        """
        # L1 (этап 3): без очистки кеша «открытие файла» мерило бы не
        # холодный старт, а тёплый — R7-Офис переиспользует temp-объекты
        # прошлого запуска.
        _cleared = self._clear_r7_cache()
        if _cleared:
            self.add_test_log(f"🧹 Очищено {_cleared} временных объектов Р7 из %TEMP% (холодный старт)")
        # Плюс файловый кэш ОС: иначе DLL Р7 и тестовый файл читаются из
        # памяти, и «холодный» старт на деле тёплый (пункт 11 аудита).
        # Сначала — спокойная система (хвост закрытия прошлого экземпляра).
        self._wait_system_quiet()
        self._purge_os_file_cache()

        self.add_test_log(f"🔄 Запуск Р7-Офис с файлом: {test_file.name}")
        # Порт проверяется ДО старта секундомера — иначе TCP-connect_ex
        # внутри _prepare_webdriver_launch попадает в open_elapsed.
        self._remove_stale_lock_files(test_file)
        debug_args = self._prepare_webdriver_launch(filename_hint=test_file.name)
        self._x2t()                       # отслеживатель x2t — до запуска Р7
        self._open_disk_before = _disk_snapshot()
        open_start = time.perf_counter()
        # shell=False: с shell=True в холодный старт попадал запуск cmd.exe,
        # а proc.kill() убил бы cmd.exe, а не Р7 (см. правила в CLAUDE.md).
        subprocess.Popen([r7_path, str(test_file), *debug_args])

        if not self._wait_r7_window(test_file.stem, timeout=60) and not self._wait_r7_window("Р7-Офис", timeout=10):
            return None
        # L1: граница холодного/тёплого старта — окно уже нарисовано ОС,
        # но документ Р7 ещё не распарсил. Момент снимается ДО подготовки
        # окна, иначе она сдвинула бы границу cold/warm на своё время.
        window_ts = self._window_seen_at

        # Подготовка окна (геометрия, фокус, снятие диалога обновления).
        # Р7 грузит документ параллельно с ней, поэтому из открытия она
        # не вычитается — засекается только для лога.
        _setup_start = time.perf_counter()
        self._fix_r7_window_geometry(self._find_r7_window(test_file.stem), log_cb=self.add_test_log)
        self._focus_r7_settled(test_file)
        # Один проход без опроса: дальше диалог обновления ловит фоновый монитор.
        self._close_update_dialog_if_exists(search_timeout=0)
        return open_start, window_ts, time.perf_counter() - _setup_start

    def _focus_r7_settled(self, test_file):
        """Окно Р7 с тестовым файлом — на передний план, затем 0.3 с, чтобы
        фокус устоялся. False — окна нет или фокус не подтвердился."""
        hwnd = self._find_r7_window(test_file.stem)
        if hwnd:
            ok = self._focus_r7_window(hwnd)
            time.sleep(0.3)
            return ok
        return False

    def _open_result(self, opens, data_ready, sample0):
        """Запись «Открытия файла» по всем повторам открытия (медиана, MAD,
        холодный/тёплый старт, диск, x2t) — без Р7, по уже снятым данным.

        Args:
            opens: повторы открытия: open_elapsed, cold_start_ms,
                warm_start_ms, status, ready_marker, x2t, disk.
            data_ready: документ основного запуска загрузился.
            sample0: замер ресурсов после открытия (_sample_r7_resources) или None.
        """
        _opens = opens
        _open_times = [o["open_elapsed"] for o in _opens]
        _open_statuses = [o["status"] for o in _opens]
        # Открытия — независимые холодные старты (кэш сбрасывается перед
        # каждым), систематического «прогрева» у первого нет (6 открытий
        # подряд: 9.14 / 9.00 / 9.04 / 8.98 / 9.64 / 11.70 с). Поэтому
        # первый повтор не отбрасывается — в медиану идут все.
        _open_stats, _open_first_discarded, _open_timeouts = self._select_stats_runs(
            _open_times, _open_statuses, discard_warmup=False)
        _open_disk_note, _open_x2t_waits = self._open_disk_wait_note(_opens)
        if _open_disk_note:
            self.add_test_log(f"   ⚠️ {_open_disk_note}")
        _open_median = statistics.median(_open_stats)
        _open_mad = self._mad(_open_stats)
        # Холодный/тёплый старт — медианы по тем же повторам, что вошли
        # в статистику времени.
        _all_timeout = _open_timeouts == len(_opens)
        _stat_idx = [i for i, st in enumerate(_open_statuses)
                     if _all_timeout or st != "timeout"]
        if _open_first_discarded:
            _stat_idx = _stat_idx[1:]

        def _med(key):
            vals = [_opens[i][key] for i in _stat_idx if _opens[i][key] is not None]
            return round(statistics.median(vals), 1) if vals else None

        if len(_opens) > 1:
            self.add_test_log(
                f"   📊 Открытие файла: медиана {_open_median:.3f} сек (MAD {_open_mad:.3f}), "
                f"{len(_open_stats)}/{len(_opens)} повторов"
                + (" (1-й отброшен: холодный файловый кэш ОС)" if _open_first_discarded else ""))
        # Все открытия — таймаут: «время открытия» — предохранитель, а не
        # длительность (аудит 06.10.2026: прежде error оставался None).
        _open_error = ("все открытия упёрлись в таймаут — время открытия "
                       "недостоверно" if _all_timeout else None)
        if not data_ready:
            # Основной запуск — тот, на котором дальше идут тесты правки.
            self.add_test_log(f"❌ {_OPEN_NOT_READY}")
            _open_error = _open_error or _OPEN_NOT_READY
        return {
            "name": "Открытие файла", "time": _open_median, "error": _open_error,
            # L1 (этап 3): раздельные холодный/тёплый старт — см.
            # _split_open_timing. С аудита 29.09.2026 — медианы по повторам.
            "cold_start_ms":  _med("cold_start_ms"),
            "warm_start_ms":  _med("warm_start_ms"),
            "total_open_ms":  round(_open_median * 1000, 1),
            # Чем определена готовность на каждом повторе: "bold" — кнопка
            # «Жирный» (основной маркер), "cpu" — запасной путь и т.д.
            "ready_markers":  [o.get("ready_marker") for o in _opens],
            # Конвертация .xlsx при открытии (x2t) — по каждому повтору.
            "x2t_at_open":    [o.get("x2t") for o in _opens],
            # Диск за время открытия — по каждому повтору (_disk_delta).
            "disk_at_open":   [o.get("disk") for o in _opens],
            # Сколько x2t ждал (не работал) на каждом открытии и пометка,
            # если это ожидание «гуляет» — см. _open_disk_wait_note.
            "x2t_wait_sec":   _open_x2t_waits,
            "disk_note":      _open_disk_note,
            "runs": _open_times, "run_statuses": _open_statuses,
            "avg": sum(_open_times) / len(_open_times),
            "min": min(_open_times), "max": max(_open_times),
            "median": _open_median, "mad": _open_mad, "n_runs": len(_open_stats),
            "first_run_discarded": _open_first_discarded,
            "n_timeouts": _open_timeouts, "runs_independent": True,
            "ram":            sample0["ram_mb"]       if sample0 else None,
            "cpu":            sample0["cpu_raw_pct"]   if sample0 else None,
            "cpu_normalized": sample0["cpu_norm_pct"]  if sample0 else None,
            "threads":        sample0["threads"]       if sample0 else None,
            "uptime_sec":     sample0["uptime_sec"]    if sample0 else None,
        }
