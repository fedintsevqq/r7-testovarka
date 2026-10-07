"""Прогоны без окна вкладки: одна версия в Batch-режиме и тест своего файла.

Операции и подготовки — r7_ops.SpreadsheetOps, цикл повторов —
_measure_op_repeated: цифры Batch сравнимы с вкладкой «Производительность»
по построению. Р7 закрывается при любом исходе (finally →
_emergency_close_r7). RunsMixin — методы, которые R7Testovarka получает
наследованием.
"""
import json
import subprocess
import time
from datetime import datetime

from r7 import env
from r7.config import _OPEN_NOT_READY
from r7.env import pyperclip, win32gui
from r7.resources import _disk_delta, _disk_snapshot, _format_disk
from r7.processes import X2tTracker
from r7.run_summary import report_summary, resource_summary
from r7.versions import version_label
from r7_ops import SpreadsheetOps


class RunsMixin:
    """Batch по одной версии и тест своего файла — часть R7Testovarka (через наследование)."""

    PAUSE_POLL_SEC = 0.2

    def _wait_while_paused(self, pause_event, stop_event, log_cb, where=""):
        """Держит Batch, пока нажата «Пауза»; «Стоп» прерывает ожидание.

        Прежде здесь стоял pause_event.wait(): кнопка «Пауза» УСТАНАВЛИВАЕТ
        событие, а wait() на установленном событии возвращается сразу — пауза
        писала «Пауза… Продолжение…» и прогон шёл дальше (07.10.2026).
        """
        if not pause_event.is_set():
            return
        log_cb(f"⏸ Пауза{(' ' + where) if where else ''}...")
        while pause_event.is_set() and not stop_event.is_set():
            time.sleep(self.PAUSE_POLL_SEC)
        log_cb("⏹ Остановлено во время паузы" if stop_event.is_set() else "▶ Продолжение...")

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

                self._wait_while_paused(pause_event, stop_event, log_cb)
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

            self._wait_while_paused(pause_event, stop_event, log_cb, "между версиями")

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

        launched = self._batch_launch(r7_path, test_file, log_cb, _find_hwnd, _focus)
        if launched is None:
            return None
        open_start, _window_appeared_ts, _setup_elapsed, _open_disk_before = launched

        _upd_stop = self._start_update_monitor(log_cb)

        _r7_closed = False   # зеркало _spreadsheet_worker (G-05)
        try:
            data_ready = self._wait_until_r7_ready(_find_hwnd, timeout=120, log_cb=log_cb)
            opened = self._batch_open_timing(open_start, _window_appeared_ts, _setup_elapsed,
                                             _open_disk_before, data_ready, log_cb)
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
            results = [self._batch_open_record(opened, data_ready, open_start, sample0)]

            def measure(name, func):
                """Замер операции Batch-режима — общий цикл повторов
                _measure_op_repeated (аудит 29.09.2026, пункт 13). Повторов
                BATCH_TEST_RUNS (экспорт в дополнительные форматы —
                DEFAULT_FORMAT_TEST_RUNS), чтобы для Batch работал вердикт
                сравнения версий. Документ не загрузился — тесты правки не
                идут (зеркало run_test_with_runs)."""
                if stop_event.is_set() or not data_ready:
                    return
                self._wait_while_paused(pause_event, stop_event, log_cb)
                if stop_event.is_set():
                    return
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
            res = resource_summary(results)

            # ── Закрытие Р7-Офис ──────────────────────────────────────────────────
            _upd_stop.set()
            log_cb("🔍 Мониторинг окна обновления остановлен")
            log_cb("🔚 Закрытие Р7-Офис...")
            self._restore_autosave(log_cb=log_cb)
            self._close_r7_gracefully(_find_hwnd(), log_cb=log_cb)
            _r7_closed = self._r7_gone()   # зеркало _spreadsheet_worker
            self._cleanup_x2t_temp_pdfs(log_cb=log_cb)   # см. _spreadsheet_worker

            json_path = self._batch_save_json(version_label, test_file, results, res, log_cb)
            return self._batch_summary(opened, results, res, json_path)
        finally:
            _upd_stop.set()
            self._restore_autosave(log_cb=log_cb)   # no-op после штатного закрытия
            if not _r7_closed:
                if not self._emergency_close_r7(_find_hwnd, log_cb=log_cb):
                    log_cb("❌ Р7-Офис не закрылся — закройте его вручную, иначе "
                           "следующая версия упрётся в занятый порт CDP")
            self._close_webdriver_connector()

    def _batch_launch(self, r7_path, test_file, log_cb, find_hwnd, focus):
        """Запуск Р7 с файлом в Batch: тишина системы, холодный кэш ОС, CDP,
        ожидание окна и его подготовка. Возвращает (старт, появление окна,
        подготовка окна, снимок диска до открытия) или None — окна нет."""
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
        open_disk_before = _disk_snapshot()
        open_start = time.perf_counter()
        # shell=False — см. _spreadsheet_worker.
        subprocess.Popen([r7_path, str(test_file), *debug_args])

        deadline = time.perf_counter() + 60
        while time.perf_counter() < deadline:
            if find_hwnd():
                break
            time.sleep(self.WINDOW_POLL_SEC)
        else:
            log_cb("❌ Окно Р7-Офис не появилось.")
            return None
        # L1: граница холодного/тёплого старта — см. комментарий в
        # _spreadsheet_worker у _window_appeared_ts.
        window_appeared_ts = time.perf_counter()

        # Подготовку окна засекаем только для лога: Р7 грузит документ
        # параллельно с ней, вычитать её нельзя (см. _spreadsheet_worker).
        setup_start = time.perf_counter()
        # L3 (этап 3): фиксированная геометрия вместо maximize — зеркало
        # maximize_window() из _spreadsheet_worker, см. _fix_r7_window_geometry.
        self._fix_r7_window_geometry(find_hwnd(), log_cb=log_cb)
        focus()
        self._close_update_dialog_if_exists(log_cb=log_cb, search_timeout=0)
        setup_elapsed = time.perf_counter() - setup_start
        return open_start, window_appeared_ts, setup_elapsed, open_disk_before

    def _batch_open_timing(self, open_start, window_appeared_ts, setup_elapsed,
                           open_disk_before, data_ready, log_cb):
        """Время открытия в Batch и строка журнала о нём. Возвращает словарь:
        open_elapsed, cold/warm/total_open_ms, disk."""
        open_disk = _disk_delta(open_disk_before, _disk_snapshot(),
                                self._matches_r7_process, self._x2t_since(open_start))
        if open_disk:
            log_cb(f"   💽 Открытие: {_format_disk(open_disk)}")
        ready_ts = self._ready_at   # начало простоя, см. _wait_until_r7_ready
        open_elapsed = ready_ts - open_start
        # L1: см. _split_open_timing. Окно точно появилось: иначе
        # _batch_launch вернул бы None и воркер — тоже.
        timing = self._split_open_timing(open_start, window_appeared_ts, ready_ts)
        cold_start_ms = timing["cold_start_ms"]
        warm_start_ms = timing["warm_start_ms"]
        log_cb(f"✅ Файл открыт за {open_elapsed:.2f} сек "
               f"(холодный {cold_start_ms / 1000:.2f} с, тёплый {warm_start_ms / 1000:.2f} с; "
               f"подготовка окна {setup_elapsed:.2f} с шла параллельно)"
               + ("" if data_ready else " (таймаут — возможна частичная загрузка)"))
        return {"open_elapsed": open_elapsed, "cold_start_ms": cold_start_ms,
                "warm_start_ms": warm_start_ms, "total_open_ms": timing["total_open_ms"],
                "disk": open_disk}

    def _batch_open_record(self, opened, data_ready, open_start, sample0):
        """Запись «Открытие файла» отчёта Batch (одно открытие на версию)."""
        return {
            "name": "Открытие файла", "time": opened["open_elapsed"],
            "error": None if data_ready else _OPEN_NOT_READY,
            "cold_start_ms":  opened["cold_start_ms"],
            "warm_start_ms":  opened["warm_start_ms"],
            "total_open_ms":  opened["total_open_ms"],
            "ready_markers":  [self._ready_marker],
            "x2t_at_open":    [X2tTracker.summarize(self._x2t_since(open_start))],
            "disk_at_open":   [opened["disk"]],
            "ram":            sample0["ram_mb"]       if sample0 else None,
            "cpu":            sample0["cpu_raw_pct"]   if sample0 else None,
            "cpu_normalized": sample0["cpu_norm_pct"]  if sample0 else None,
            "threads":        sample0["threads"]       if sample0 else None,
            "uptime_sec":     sample0["uptime_sec"]    if sample0 else None,
        }

    def _batch_save_json(self, version_label, test_file, results, res, log_cb):
        """Полный JSON версии (_build_full_report). Возвращает путь, даже если
        запись не удалась — ошибка уходит в журнал, как раньше."""
        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = self.reports_folder / f"performance_full_{ts_now}.json"
        try:
            with open(json_path, "w", encoding="utf-8") as jf:
                json.dump(self._build_full_report(
                    ts_now, version_label, test_file, results,
                    report_summary(res)), jf, indent=2, ensure_ascii=False)
            log_cb(f"📄 JSON сохранён: {json_path.name}")
        except Exception as e:
            log_cb(f"⚠️ Ошибка сохранения JSON: {e}")
        return json_path

    @staticmethod
    def _batch_summary(opened, results, res, json_path):
        """Итог версии для сводки Batch (_generate_batch_summary_html)."""
        vpr_r = next((r for r in results if r["name"] == "Функция ВПР (50K строк)"), None)
        return {
            "open_elapsed":     opened["open_elapsed"],
            "cold_start_ms":    opened["cold_start_ms"],
            "warm_start_ms":    opened["warm_start_ms"],
            "total_open_ms":    opened["total_open_ms"],
            "vlookup_elapsed":  vpr_r["time"] if vpr_r else None,
            "peak_ram":         res["peak_ram_mb"],
            "avg_ram":          res["avg_ram_mb"],
            "peak_cpu":         res["peak_cpu_pct"],
            "peak_cpu_normalized": res["peak_cpu_normalized_pct"],
            "results":          results,
            "json_path":        str(json_path),
        }

    def _worker_run_test(self, file_path, rows, cols, done_cb):
        """Worker: kills stale R7 instances, clears cache, opens file, runs VPR, shows report."""
        success = False
        try:
            cleared, real_rows = self._custom_prepare_stand(file_path, rows)

            # ----- 4. Поиск пути к Р7-Офис -----------------------------------------------
            r7_path = self._find_r7_path()
            if not r7_path:
                self.add_test_log("❌ Р7-Офис не найден.")
                return

            def _find_hwnd():
                # Только видимое окно процесса Р7. Прежний поиск по одному
                # заголовку (в том числе среди невидимых окон) нашёл вкладку
                # браузера «Техническая поддержка Р7-Офис» — и тест закрыл
                # браузер вместо Р7 (30.09.2026).
                return self._find_r7_window(file_path.stem[:12])

            open_start, hwnd, _window_appeared_ts, _window_found = self._custom_launch(
                r7_path, file_path, _find_hwnd)

            # ----- 7. Динамическое ожидание загрузки -------------------------------------
            data_ready = self._wait_until_r7_ready(_find_hwnd, timeout=120)
            opened = self._custom_open_timing(open_start, _window_appeared_ts,
                                              _window_found, data_ready)

            vlookup_elapsed, vlookup_error, vlookup_rows = self._custom_vlookup(
                file_path, _find_hwnd, real_rows)

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
                "open_elapsed":    round(opened["open_elapsed"], 3),
                "cold_start_ms":   opened["cold_start_ms"],
                "warm_start_ms":   opened["warm_start_ms"],
                "total_open_ms":   opened["total_open_ms"],
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

    def _custom_prepare_stand(self, file_path, rows):
        """Старые процессы Р7, кэш %TEMP%, окружение стенда и число строк
        файла. Возвращает (очищено объектов, реальное число строк)."""
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
        return cleared, real_rows

    def _custom_launch(self, r7_path, file_path, find_hwnd):
        """Запуск Р7 со своим файлом, ожидание окна и его подготовка.
        Возвращает (старт, hwnd или None, момент появления окна, нашлось ли)."""
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

        deadline = time.perf_counter() + 60
        hwnd = None
        while time.perf_counter() < deadline:
            hwnd = find_hwnd()
            if hwnd:
                break
            time.sleep(self.WINDOW_POLL_SEC)

        if not hwnd:
            self.add_test_log("⚠️ Окно Р7 не найдено, продолжаем без фокуса")
        # L1 (этап 3): в отличие от _spreadsheet_worker/_batch_run_single_version,
        # этот цикл ожидания НЕ прерывает функцию по таймауту — она продолжает
        # без фокуса. Если hwnd не нашёлся, window_appeared_ts — это момент
        # сдачи ожидания, а не появления окна: честной границы cold/warm нет
        # (см. window_found у _split_open_timing, code review).
        window_appeared_ts = time.perf_counter()

        # ----- 6. Фокус и разворот ---------------------------------------------------
        # Засекаем только для журнала: подготовка окна шла параллельно с
        # загрузкой и из открытия не вычитается.
        setup_start = time.perf_counter()
        if env.WIN32_OK and hwnd:
            try:
                # L3: фиксированная геометрия вместо maximize — см.
                # _fix_r7_window_geometry.
                self._fix_r7_window_geometry(hwnd, log_cb=self.add_test_log)
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.3)
            except Exception as e:
                self.add_test_log(f"   ⚠️ Окно Р7 не подготовлено (геометрия/фокус): "
                                  f"{type(e).__name__}: {e}")
        self.add_test_log(f"   🪟 Подготовка окна {time.perf_counter() - setup_start:.2f} сек "
                          f"(шла параллельно с загрузкой, из открытия не вычитается)")
        return open_start, hwnd, window_appeared_ts, hwnd is not None

    def _custom_open_timing(self, open_start, window_appeared_ts, window_found, data_ready):
        """Время открытия своего файла и строка журнала о нём."""
        ready_ts = self._ready_at   # начало простоя, см. _wait_until_r7_ready
        open_elapsed = ready_ts - open_start   # подготовка окна шла параллельно
        # L1: см. _split_open_timing и window_found в _custom_launch.
        timing = self._split_open_timing(open_start, window_appeared_ts, ready_ts,
                                         window_found=window_found)
        cold_start_ms = timing["cold_start_ms"]
        warm_start_ms = timing["warm_start_ms"]
        timing_txt = (
            f"холодный {cold_start_ms / 1000:.2f} с, тёплый {warm_start_ms / 1000:.2f} с"
            if cold_start_ms is not None else
            "холодный/тёплый старт не определён — окно Р7 не найдено за 60 с")
        self.add_test_log(
            f"✅ Файл открыт за {open_elapsed:.2f} сек "
            f"({timing_txt}; "
            f"{'данные загружены' if data_ready else 'таймаут — возможна частичная загрузка'})"
        )
        return {"open_elapsed": open_elapsed, "cold_start_ms": cold_start_ms,
                "warm_start_ms": warm_start_ms, "total_open_ms": timing["total_open_ms"]}

    def _custom_vlookup(self, file_path, find_hwnd, real_rows):
        """ВПР-бенчмарк на всех строках своего файла.
        Возвращает (время или None, ошибка или None, строк в замере)."""
        if not (env.PYAUTOGUI_OK and pyperclip):
            self.add_test_log("⚠️ pyautogui/pyperclip недоступны — ВПР пропущен")
            return None, None, 0
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
                vlookup = dict(SpreadsheetOps(
                    self, find_hwnd, self.add_test_log, file_path
                ).tests())["Функция ВПР (50K строк)"]
                vres = self._measure_op_repeated(
                    "Функция ВПР", vlookup, 1, find_hwnd, self.add_test_log, None)
            finally:
                self._restore_autosave()
            if vres.get("error"):
                self.add_test_log(f"⚠️ Ошибка ВПР: {vres['error']}")
                return None, vres["error"], 0
            elapsed = round(vres["time"], 3)
            self.add_test_log(
                f"✅ ВПР по {real_rows:,} строкам завершён за {elapsed:.2f} сек")
            return elapsed, None, real_rows
        except Exception as e:
            self.add_test_log(f"⚠️ Ошибка ВПР: {e}")
            return None, str(e), 0
