"""Прогоны без окна вкладки: одна версия в Batch-режиме и тест своего файла.

Операции и подготовки — r7_ops.SpreadsheetOps, цикл повторов —
_measure_op_repeated: цифры Batch сравнимы с вкладкой «Производительность»
по построению. Р7 закрывается при любом исходе (finally →
_emergency_close_r7). RunsMixin — методы, которые R7Testovarka получает
наследованием.
"""
import json
import subprocess
import threading
import time
from datetime import datetime

from r7 import env
from r7.config import _OPEN_NOT_READY
from r7.env import pyperclip, win32gui
from r7.resources import _disk_delta, _disk_snapshot, _format_disk
from r7.processes import X2tTracker
from r7.versions import version_label
from r7_ops import SpreadsheetOps


class RunsMixin:
    """Batch по одной версии и тест своего файла — часть R7Testovarka (через наследование)."""

    def _batch_worker(self, versions, test_file, stop_on_error, cleanup,
                      log_cb, current_cb, ver_status_cb, progress_cb,
                      done_cb, stop_event, pause_event):
        """Batch worker thread: install each version, run tests, collect results."""
        batch_results = []
        errors = 0
        log_cb(f"🚀 Запуск Batch-режима: найдено {len(versions)} версий")
        self._run_environment = self._capture_environment(log_cb=log_cb)

        for idx, dist_file in enumerate(versions):
            if stop_event.is_set():
                log_cb("⏹ Остановлено пользователем.")
                break

            ver_name = self._extract_version(dist_file.stem) or dist_file.stem
            current_cb(f"Текущая версия: {ver_name} ({idx + 1} из {len(versions)})")
            ver_status_cb(dist_file, f"🔄 {dist_file.name}: выполняется...")
            log_cb(f"--- Версия {idx + 1}/{len(versions)}: {dist_file.name} ---")

            result = {
                "file":            dist_file.name,
                "version":         ver_name,
                "success":         False,
                "error":           None,
                "open_elapsed":    None,
                "cold_start_ms":   None,
                "warm_start_ms":   None,
                "total_open_ms":   None,
                "vlookup_elapsed": None,
                "peak_ram":        None,
                "avg_ram":         None,
                "peak_cpu":        None,
            }

            try:
                log_cb("🗑️ Удаление текущей версии...")
                if not self.uninstall_current_version():
                    raise RuntimeError("Удаление текущей версии не завершилось успешно")
                time.sleep(2)

                log_cb(f"📥 Установка {dist_file.name}...")
                if not self.install_version(dist_file):
                    raise RuntimeError("Установка не завершилась успешно (таймаут или код ошибки)")
                self.detect_current_version()
                ver_display = version_label(self.current_version_info) or ver_name
                log_cb(f"✅ Установлена: {ver_display}")

                if pause_event.is_set():
                    log_cb("⏸ Пауза...")
                    pause_event.wait()
                    log_cb("▶ Продолжение...")
                if stop_event.is_set():
                    break

                if cleanup:
                    cleared = self._clear_r7_cache()
                    if cleared:
                        log_cb(f"🧹 Очищено {cleared} объектов кеша")

                test_result = self._batch_run_single_version(
                    test_file, ver_display, log_cb, stop_event, pause_event)

                if test_result:
                    result.update(test_result)
                    result["success"] = True
                    ot  = result.get("open_elapsed") or 0
                    vt  = result.get("vlookup_elapsed")
                    vts = f", ВПР {vt:.2f} сек" if vt else ""
                    log_cb(f"✅ {ver_name}: открытие {ot:.2f} сек{vts}")
                    ver_status_cb(dist_file,
                                  f"✅ {dist_file.name}: {ot:.1f} сек"
                                  + (f" / ВПР {vt:.1f} сек" if vt else ""))
                else:
                    raise RuntimeError("Тест не вернул результатов")

            except Exception as e:
                errors += 1
                result["error"] = str(e)
                log_cb(f"❌ {ver_name}: ошибка — {e}")
                ver_status_cb(dist_file, f"❌ {dist_file.name}: ошибка")
                if stop_on_error:
                    log_cb("🛑 Остановка (включена опция 'стоп при ошибке')")
                    batch_results.append(result)
                    break

            batch_results.append(result)
            progress_cb(idx + 1)

            if pause_event.is_set():
                log_cb("⏸ Пауза между версиями...")
                pause_event.wait()
                log_cb("▶ Продолжение...")

        done_cb(batch_results, errors)

    def _batch_run_single_version(self, test_file, version_label, log_cb,
                                  stop_event, pause_event):
        """Runs the full 12-operation stress test for the currently installed version.

        Returns a result dict with timing/resource data, or None on critical failure.
        """
        r7_path = self._find_r7_path()
        if not r7_path:
            log_cb("❌ Р7-Офис не найден.")
            return None

        # ── Оконные вспомогательные функции ──────────────────────────────────
        def _find_hwnd():
            # Только окно процесса Р7 — см. _find_r7_window.
            return self._find_r7_window(test_file.stem[:12])

        def _focus():
            # Зеркало focus_window: фокус проверяется (_focus_r7_window).
            hwnd = _find_hwnd()
            if hwnd:
                ok = self._focus_r7_window(hwnd, log_cb=log_cb)
                time.sleep(0.2)
                return ok
            return not env.WIN32_OK

        def _maximize():
            # L3 (этап 3): фиксированная геометрия вместо maximize — зеркало
            # maximize_window() из _spreadsheet_worker, см. _fix_r7_window_geometry.
            hwnd = _find_hwnd()
            self._fix_r7_window_geometry(hwnd, log_cb=log_cb)

        def _close_update_dlg(search_timeout=0):
            self._close_update_dialog_if_exists(log_cb=log_cb,
                                                search_timeout=search_timeout)

        # ── Открытие Р7-Офис ──────────────────────────────────────────────────
        # L1 (этап 3): холодный старт здесь обеспечивает опциональная очистка
        # кеша в _batch_worker (флаг «cleanup», перед вызовом этой функции) —
        # в отличие от _spreadsheet_worker, у Batch уже был такой переключатель.
        log_cb(f"▶ Запуск Р7-Офис: {test_file.name}")
        # Порт проверяется ДО старта секундомера — см. комментарий в
        # _spreadsheet_worker (зеркалим сюда, как требует правило репозитория
        # про синхронность мест паузы между Batch и вкладкой «Производительность»).
        # Зеркало _spreadsheet_worker: спокойная система и холодный кэш ОС.
        self._wait_system_quiet(log_cb=log_cb)
        self._purge_os_file_cache(log_cb=log_cb)
        self._remove_stale_lock_files(test_file, log_cb=log_cb)
        debug_args = self._prepare_webdriver_launch(log_cb=log_cb, filename_hint=test_file.name)
        self._x2t(log_cb)                     # зеркало _spreadsheet_worker
        _open_disk_before = _disk_snapshot()
        open_start = time.perf_counter()
        # shell=False — см. _spreadsheet_worker.
        subprocess.Popen([r7_path, str(test_file), *debug_args])

        deadline = time.perf_counter() + 60
        while time.perf_counter() < deadline:
            if _find_hwnd():
                break
            time.sleep(self.WINDOW_POLL_SEC)
        else:
            log_cb("❌ Окно Р7-Офис не появилось.")
            return None
        # L1: граница холодного/тёплого старта — см. комментарий в
        # _spreadsheet_worker у _window_appeared_ts.
        _window_appeared_ts = time.perf_counter()

        # Подготовку окна засекаем только для лога: Р7 грузит документ
        # параллельно с ней, вычитать её нельзя (см. _spreadsheet_worker).
        _setup_start = time.perf_counter()
        _maximize()
        _focus()
        _close_update_dlg(search_timeout=0)
        _setup_elapsed = time.perf_counter() - _setup_start

        # Фоновый мониторинг окна обновления на весь период теста
        _upd_stop = threading.Event()
        threading.Thread(
            target=self._monitor_update_dialog,
            args=(_upd_stop,),
            kwargs={"log_cb": log_cb},
            daemon=True,
        ).start()
        log_cb("🔍 Запущен мониторинг окна обновления (проверка каждые 2 сек)")

        _r7_closed = False   # зеркало _spreadsheet_worker (G-05)
        try:
            data_ready   = self._wait_until_r7_ready(_find_hwnd, timeout=120, log_cb=log_cb)
            _open_disk   = _disk_delta(_open_disk_before, _disk_snapshot(),
                                       self._matches_r7_process, self._x2t_since(open_start))
            if _open_disk:
                log_cb(f"   💽 Открытие: {_format_disk(_open_disk)}")
            _ready_ts    = self._ready_at   # начало простоя, см. _wait_until_r7_ready
            open_elapsed = _ready_ts - open_start
            # L1: см. _split_open_timing. window_found=True: цикл ожидания
            # окна выше уже вернул бы None на всю функцию, если бы окно
            # не появилось (см. комментарий у "return None" перед try).
            _open_timing = self._split_open_timing(
                open_start, _window_appeared_ts, _ready_ts)
            cold_start_ms = _open_timing["cold_start_ms"]
            warm_start_ms = _open_timing["warm_start_ms"]
            total_open_ms = _open_timing["total_open_ms"]
            log_cb(f"✅ Файл открыт за {open_elapsed:.2f} сек "
                   f"(холодный {cold_start_ms / 1000:.2f} с, тёплый {warm_start_ms / 1000:.2f} с; "
                   f"подготовка окна {_setup_elapsed:.2f} с шла параллельно)"
                   + ("" if data_ready else " (таймаут — возможна частичная загрузка)"))
            _focus()

            # Зеркало _spreadsheet_worker — подключение к CDP, базовый
            # DOM-снимок ДО первой операции (issue #9, см.
            # _capture_cdp_ui_baseline) и разовая диагностика api редактора.
            self._cdp_ensure_connected(log_cb=log_cb)
            self._capture_cdp_ui_baseline(log_cb=log_cb)
            self._cdp_log_api_info(log_cb=log_cb)
            self._suspend_autosave(log_cb=log_cb)

            # ── Мониторинг ресурсов ───────────────────────────────────────────────
            self._r7_pids = None
            self._x2t_logged_pids = set()  # сбросить дедуп x2t перед новым тестом
            r7_procs = self._get_r7_processes(log_cb=log_cb)

            sample0 = self._sample_r7_resources(r7_procs)
            if not data_ready:
                log_cb(f"❌ {_OPEN_NOT_READY}")
            results = [{
                "name": "Открытие файла", "time": open_elapsed,
                "error": None if data_ready else _OPEN_NOT_READY,
                "cold_start_ms":  cold_start_ms,
                "warm_start_ms":  warm_start_ms,
                "total_open_ms":  total_open_ms,
                "ready_markers":  [self._ready_marker],
                "x2t_at_open":    [X2tTracker.summarize(self._x2t_since(open_start))],
                "disk_at_open":   [_open_disk],
                "ram":            sample0["ram_mb"]       if sample0 else None,
                "cpu":            sample0["cpu_raw_pct"]   if sample0 else None,
                "cpu_normalized": sample0["cpu_norm_pct"]  if sample0 else None,
                "threads":        sample0["threads"]       if sample0 else None,
                "uptime_sec":     sample0["uptime_sec"]    if sample0 else None,
            }]

            def measure(name, func):
                """Замер операции Batch-режима — общий цикл повторов
                _measure_op_repeated (аудит 29.09.2026, пункт 13). Повторов
                BATCH_TEST_RUNS (экспорт в дополнительные форматы —
                DEFAULT_FORMAT_TEST_RUNS), чтобы для Batch работал вердикт
                сравнения версий. Документ не загрузился — тесты правки не
                идут (зеркало run_test_with_runs)."""
                if stop_event.is_set() or not data_ready:
                    return
                if pause_event.is_set():
                    log_cb("⏸ Пауза...")
                    pause_event.wait()
                    log_cb("▶ Продолжение...")
                runs = (self.DEFAULT_FORMAT_TEST_RUNS if name in self.EXTRA_FORMAT_TESTS
                        else self.BATCH_TEST_RUNS)
                results.append(self._measure_op_repeated(
                    name, func, runs, _find_hwnd, log_cb, stop_event, focus_cb=_focus))

            # ── Выполнение тестов ─────────────────────────────────────────────────
            # Те же операции и подготовки, что во вкладке «Производительность»
            # (r7_ops.SpreadsheetOps) — Batch прогоняет все, по порядку.
            for _name, _func in SpreadsheetOps(self, _find_hwnd, log_cb, test_file).tests():
                measure(_name, _func)
            self._cleanup_x2t_temp_pdfs(log_cb=log_cb)

            # ── Статистика ────────────────────────────────────────────────────────
            ram_vals      = [r["ram"] for r in results if r.get("ram") is not None]
            cpu_vals      = [r["cpu"] for r in results if r.get("cpu") is not None]
            cpu_norm_vals = [r["cpu_normalized"] for r in results if r.get("cpu_normalized") is not None]
            peak_ram = max(ram_vals) if ram_vals else None
            avg_ram  = round(sum(ram_vals) / len(ram_vals), 1) if ram_vals else None
            peak_cpu = max(cpu_vals) if cpu_vals else None
            peak_cpu_norm = max(cpu_norm_vals) if cpu_norm_vals else None
            avg_cpu_norm  = round(sum(cpu_norm_vals) / len(cpu_norm_vals), 1) if cpu_norm_vals else None

            # ── Закрытие Р7-Офис ──────────────────────────────────────────────────
            _upd_stop.set()
            log_cb("🔍 Мониторинг окна обновления остановлен")
            log_cb("🔚 Закрытие Р7-Офис...")
            self._restore_autosave(log_cb=log_cb)
            self._close_r7_gracefully(_find_hwnd(), log_cb=log_cb)
            _r7_closed = self._r7_gone()   # зеркало _spreadsheet_worker
            self._cleanup_x2t_temp_pdfs(log_cb=log_cb)   # см. _spreadsheet_worker

            # ── Сохранение JSON ───────────────────────────────────────────────────
            ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_path = self.reports_folder / f"performance_full_{ts_now}.json"
            try:
                with open(json_path, "w", encoding="utf-8") as jf:
                    json.dump(self._build_full_report(
                        ts_now, version_label, test_file, results, {
                            "peak_ram_mb": peak_ram, "avg_ram_mb": avg_ram,
                            "min_ram_mb":  min(ram_vals) if ram_vals else None,
                            "peak_cpu_pct": peak_cpu,
                            "peak_cpu_normalized_pct": peak_cpu_norm,
                            "avg_cpu_normalized_pct": avg_cpu_norm,
                        }), jf, indent=2, ensure_ascii=False)
                log_cb(f"📄 JSON сохранён: {json_path.name}")
            except Exception as e:
                log_cb(f"⚠️ Ошибка сохранения JSON: {e}")

            vpr_r = next((r for r in results if r["name"] == "Функция ВПР (50K строк)"), None)
            return {
                "open_elapsed":     open_elapsed,
                "cold_start_ms":    cold_start_ms,
                "warm_start_ms":    warm_start_ms,
                "total_open_ms":    total_open_ms,
                "vlookup_elapsed":  vpr_r["time"] if vpr_r else None,
                "peak_ram":         peak_ram,
                "avg_ram":          avg_ram,
                "peak_cpu":         peak_cpu,
                "peak_cpu_normalized": peak_cpu_norm,
                "results":          results,
                "json_path":        str(json_path),
            }
        finally:
            _upd_stop.set()
            self._restore_autosave(log_cb=log_cb)   # no-op после штатного закрытия
            if not _r7_closed:
                if not self._emergency_close_r7(_find_hwnd, log_cb=log_cb):
                    log_cb("❌ Р7-Офис не закрылся — закройте его вручную, иначе "
                           "следующая версия упрётся в занятый порт CDP")
            self._close_webdriver_connector()

    def _worker_run_test(self, file_path, rows, cols, done_cb):
        """Worker: kills stale R7 instances, clears cache, opens file, runs VPR, shows report."""
        success = False
        try:
            # ----- 1. Завершаем старые процессы Р7 ---------------------------------------
            killed = self._kill_r7_processes_for_test()
            if killed:
                self.add_test_log(f"🔄 Завершено {killed} процессов Р7-Офис")
                time.sleep(2)
            else:
                self.add_test_log("ℹ️ Активных процессов Р7-Офис не найдено")

            # ----- 2. Очистка кеша -------------------------------------------------------
            cleared = self._clear_r7_cache()
            if cleared:
                self.add_test_log(f"🧹 Очищено {cleared} временных объектов Р7 из %TEMP%")
            self._run_environment = self._capture_environment()

            # ----- 3. Реальное количество строк ------------------------------------------
            real_rows = self._get_xlsx_row_count(file_path)
            if real_rows is not None:
                self.add_test_log(f"📊 Реальное количество строк в файле: {real_rows:,}")
            else:
                real_rows = rows

            # ----- 4. Поиск пути к Р7-Офис -----------------------------------------------
            r7_path = self._find_r7_path()
            if not r7_path:
                self.add_test_log("❌ Р7-Офис не найден.")
                return

            # ----- 5. Запуск и ожидание окна --------------------------------------------
            self.add_test_log(f"⏳ Запуск теста на файле {file_path.name}")
            # Холодный старт и по кэшу ОС — зеркало _spreadsheet_worker.
            self._wait_system_quiet()
            self._purge_os_file_cache()
            self._remove_stale_lock_files(file_path)
            debug_args = self._prepare_webdriver_launch(filename_hint=file_path.name)
            self._x2t()
            open_start = time.perf_counter()
            # shell=False — см. _spreadsheet_worker.
            subprocess.Popen([r7_path, str(file_path), *debug_args])

            def _find_hwnd():
                # Только видимое окно процесса Р7. Прежний поиск по одному
                # заголовку (в том числе среди невидимых окон) нашёл вкладку
                # браузера «Техническая поддержка Р7-Офис» — и тест закрыл
                # браузер вместо Р7 (30.09.2026).
                return self._find_r7_window(file_path.stem[:12])

            deadline = time.perf_counter() + 60
            hwnd = None
            while time.perf_counter() < deadline:
                hwnd = _find_hwnd()
                if hwnd:
                    break
                time.sleep(self.WINDOW_POLL_SEC)

            if not hwnd:
                self.add_test_log("⚠️ Окно Р7 не найдено, продолжаем без фокуса")
            # L1 (этап 3): в отличие от _spreadsheet_worker/_batch_run_single_version,
            # этот цикл ожидания НЕ прерывает функцию по таймауту — она продолжает
            # без фокуса. Если hwnd не нашёлся, _window_appeared_ts — это момент
            # сдачи ожидания, а не появления окна: честной границы cold/warm нет
            # (см. window_found у _split_open_timing, code review).
            _window_appeared_ts = time.perf_counter()
            _window_found = hwnd is not None

            # ----- 6. Фокус и разворот ---------------------------------------------------
            # Засекаем отдельно и вычитаем: подготовка окна не относится к
            # скорости открытия файла.
            _setup_start = time.perf_counter()
            if env.WIN32_OK and hwnd:
                try:
                    # L3: фиксированная геометрия вместо maximize — см.
                    # _fix_r7_window_geometry.
                    self._fix_r7_window_geometry(hwnd, log_cb=self.add_test_log)
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.3)
                except Exception:
                    pass
            self.add_test_log(f"   🪟 Подготовка окна {time.perf_counter() - _setup_start:.2f} сек "
                              f"(шла параллельно с загрузкой, из открытия не вычитается)")

            # ----- 7. Динамическое ожидание загрузки -------------------------------------
            data_ready   = self._wait_until_r7_ready(_find_hwnd, timeout=120)
            _ready_ts    = self._ready_at   # начало простоя, см. _wait_until_r7_ready
            open_elapsed = _ready_ts - open_start   # подготовка окна шла параллельно
            # L1: см. _split_open_timing и window_found выше.
            _open_timing = self._split_open_timing(
                open_start, _window_appeared_ts, _ready_ts,
                window_found=_window_found)
            cold_start_ms = _open_timing["cold_start_ms"]
            warm_start_ms = _open_timing["warm_start_ms"]
            total_open_ms = _open_timing["total_open_ms"]
            _timing_txt = (
                f"холодный {cold_start_ms / 1000:.2f} с, тёплый {warm_start_ms / 1000:.2f} с"
                if cold_start_ms is not None else
                "холодный/тёплый старт не определён — окно Р7 не найдено за 60 с")
            self.add_test_log(
                f"✅ Файл открыт за {open_elapsed:.2f} сек "
                f"({_timing_txt}; "
                f"{'данные загружены' if data_ready else 'таймаут — возможна частичная загрузка'})"
            )

            # ----- 8. ВПР-бенчмарк на всех строках --------------------------------------
            vlookup_elapsed = None
            vlookup_error   = None
            vlookup_rows    = 0

            if env.PYAUTOGUI_OK and pyperclip:
                # Тот же замер, что у теста «Функция ВПР» вкладки
                # «Производительность» (_vlookup_prepare/_vlookup_op через
                # _measure_op_repeated): формулы на каждую строку вставляются
                # одной операцией, конец — по ответу редактора, а операция,
                # не изменившая документ, считается ошибкой. Прежний код
                # вводил формулу клавишами и тянул её Ctrl+D — Р7 2026.3.2
                # этого не принимает, и секундомер мерил нажатия в пустоту.
                try:
                    self._cdp_ensure_connected(log_cb=self.add_test_log)
                    self._suspend_autosave()
                    try:
                        # Операция и подготовка — из общего набора r7_ops.
                        _vlookup = dict(SpreadsheetOps(
                            self, _find_hwnd, self.add_test_log, file_path
                        ).tests())["Функция ВПР (50K строк)"]
                        _vres = self._measure_op_repeated(
                            "Функция ВПР", _vlookup, 1, _find_hwnd, self.add_test_log, None)
                    finally:
                        self._restore_autosave()
                    if _vres.get("error"):
                        vlookup_error = _vres["error"]
                        self.add_test_log(f"⚠️ Ошибка ВПР: {vlookup_error}")
                    else:
                        vlookup_elapsed = round(_vres["time"], 3)
                        vlookup_rows = real_rows
                        self.add_test_log(
                            f"✅ ВПР по {real_rows:,} строкам завершён за {vlookup_elapsed:.2f} сек")
                except Exception as e:
                    vlookup_error = str(e)
                    self.add_test_log(f"⚠️ Ошибка ВПР: {e}")
            else:
                self.add_test_log("⚠️ pyautogui/pyperclip недоступны — ВПР пропущен")

            # ----- 9. Закрытие Р7 --------------------------------------------------------
            self._close_r7_gracefully(hwnd)

            # ----- 10. Отчёт -------------------------------------------------------------
            file_size_mb = (round(file_path.stat().st_size / (1024 ** 2), 2)
                            if file_path.exists() else None)
            self._show_custom_test_report({
                "filename":        file_path.name,
                "rows":            rows,
                "cols":            cols,
                "real_rows":       real_rows,
                "vlookup_rows":    vlookup_rows,
                "file_size_mb":    file_size_mb,
                "open_elapsed":    round(open_elapsed, 3),
                "cold_start_ms":   cold_start_ms,
                "warm_start_ms":   warm_start_ms,
                "total_open_ms":   total_open_ms,
                "vlookup_elapsed": vlookup_elapsed,
                "vlookup_error":   vlookup_error,
                "cache_cleared":   cleared > 0,
                "data_ready":      data_ready,
                "timestamp":       datetime.now().strftime("%d.%m.%Y %H:%M"),
            })
            success = True
        except Exception as e:
            self.add_test_log(f"❌ Ошибка тестирования: {e}")
        finally:
            self._close_webdriver_connector()
            done_cb(success)
