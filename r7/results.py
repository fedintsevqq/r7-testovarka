"""Результаты прогона: полный JSON-отчёт, сводки для HTML-отчётов, тренды,
сохранённые настройки (выбор тестов, параметры сравнения и Batch).

Полный JSON пишет только _build_full_report (MEASURE_SCHEMA_VERSION); вид
HTML-страниц — r7_reports.py и шаблоны templates/html/. ResultsMixin —
методы, которые R7Testovarka получает наследованием.
"""
import json
from datetime import datetime
import platform
import re
from pathlib import Path

import r7_reports
from r7 import config, env
from r7.config import DEFAULT_TEST_RUNS, MEASURE_SCHEMA_VERSION, RUNS_MAX, RUNS_MIN, SERIES_OTHER_COLOR
from r7.env import psutil
from r7.run_summary import report_summary
from r7.stats import MIN_RUNS_FOR_COMPARISON, compare_runs
from r7.versions import version_label


class ResultsMixin:
    """Отчёты, тренды и сохранённые настройки — часть R7Testovarka (через наследование)."""

    def _default_test_entry(self, name):
        """Настройки теста, которого ещё нет в selected_tests.json."""
        if name in self.EXTRA_FORMAT_TESTS:
            return {"enabled": False, "runs": self.DEFAULT_FORMAT_TEST_RUNS}
        if name in self.EXPORT_TESTS:
            return {"enabled": True, "runs": self.DEFAULT_FORMAT_TEST_RUNS}
        if name == self.OPEN_TEST_NAME:
            return {"enabled": True, "runs": self.DEFAULT_OPEN_RUNS}
        return {"enabled": True, "runs": DEFAULT_TEST_RUNS}

    def _test_groups(self):
        """Тесты по группам в порядке TEST_DEFINITIONS: [(заголовок, [имена])].

        Экспорт выделен отдельно: один его повтор на большом файле идёт до
        полутора минут, и это стоит видеть до запуска.
        """
        opening = [n for n in self.TEST_DEFINITIONS if n == self.OPEN_TEST_NAME]
        exports = [n for n in self.TEST_DEFINITIONS if n in self.EXPORT_TESTS]
        ops = [n for n in self.TEST_DEFINITIONS if n not in opening and n not in exports]
        return [(title, names) for title, names in (
            ("ОТКРЫТИЕ ФАЙЛА", opening),
            ("ОПЕРАЦИИ В ТАБЛИЦЕ", ops),
            ("ЭКСПОРТ ЧЕРЕЗ X2T · до 1.5 мин на повтор", exports),
        ) if names]

    @staticmethod
    def _clamp_runs(value, fallback):
        """Число повторов из поля ввода: целое в RUNS_MIN..RUNS_MAX.

        Пустое поле и мусор дают fallback. Раньше Spinbox отдавал текст как
        есть, и пустое поле роняло IntVar.get() при нажатии «Запустить».
        """
        try:
            v = int(str(value).strip())
        except (TypeError, ValueError):
            return fallback
        return max(RUNS_MIN, min(RUNS_MAX, v))

    @staticmethod
    def _short_version_text(info):
        """Строка версии для шапки: «Р7-Офис. Профессиональный · 2026.3.2.3229».

        Полное имя из реестра с «(десктопная версия)» не помещалось в шапку
        узкого окна, и обрезался именно номер сборки — самое важное.
        """
        name = re.sub(r"\s*\(десктопная версия\)", "", info.get("name") or "").strip()
        return f"{name} · {info.get('version', '')}" if name else str(info.get("version", ""))

    def _extract_version(self, filename):
        """Extracts a version string like v2026.1.3 from a filename.

        Args:
            filename: The installer filename stem (without extension).

        Returns:
            str: Version string like 'v2026.1.3', or None if not found.
        """
        match = re.search(r'(\d+\.\d+(?:\.\d+)*)', filename)
        return f"v{match.group(1)}" if match else None

    # ---------------------- Настройки тестов ----------------------
    def _load_test_selection(self):
        """Loads saved test-selection state from selected_tests.json.

        Accepts both the old shape ({name: bool}) and the current one
        ({name: {"enabled": bool, "runs": int}}), upgrading the old one
        in memory so files saved by earlier versions of the app keep working.

        Returns:
            dict: Mapping test_name → {"enabled": bool, "runs": int}.
        """
        path = config.BASE_DIR / "selected_tests.json"
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return {}
        # Файл читается при запуске программы: битая запись ("runs": "abc",
        # список вместо словаря) раньше роняла весь интерфейс исключением из
        # int()/.items() (QA-аудит 29.09.2026, G-14). Теперь плохая запись
        # заменяется значениями по умолчанию, а не валит запуск.
        if not isinstance(raw, dict):
            return {}
        upgraded = {}
        for name, value in raw.items():
            if isinstance(value, dict):
                try:
                    runs = int(value.get("runs", DEFAULT_TEST_RUNS))
                except (TypeError, ValueError):
                    runs = DEFAULT_TEST_RUNS
                upgraded[name] = {
                    "enabled": bool(value.get("enabled", True)),
                    "runs": max(1, runs),
                }
            else:
                # Старый формат: значение — просто bool.
                upgraded[name] = {"enabled": bool(value), "runs": DEFAULT_TEST_RUNS}
        return upgraded

    def _save_test_selection(self):
        """Persists the current checkbox + run-count state to selected_tests.json."""
        path = config.BASE_DIR / "selected_tests.json"
        try:
            data = {
                name: {"enabled": var.get(), "runs": self.test_runs[name].get()}
                for name, var in self.test_vars.items()
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.add_test_log(f"⚠️ Не удалось сохранить настройки тестов: {e}")

    @staticmethod
    def _split_open_timing(open_start, window_appeared_ts, ready_ts, setup_elapsed=0.0,
                            window_found=True):
        """Раздельный холодный/тёплый старт (L1, этап 3) — общая арифметика
        для трёх мест открытия файла (_spreadsheet_worker,
        _batch_run_single_version, _worker_run_test), вынесенная сюда
        вместо тройной копии одних и тех же трёх строк (code review, этап 3).

        cold_start — от запуска процесса до появления окна ОС (загрузка
        самого Р7); warm_start — от появления окна до готовности документа
        (парсинг файла), за вычетом времени подготовки окна (maximize/focus).
        cold_start_ms + warm_start_ms == open_elapsed*1000 — это точное
        алгебраическое тождество при любых входных метках, а не только при
        валидных: сумма телескопируется до (ready_ts - open_start -
        setup_elapsed), поэтому округление round(cold,1)+round(warm,1)
        может разойтись с round(total,1) не больше чем на 0.1 мс.

        Args:
            open_start: time.perf_counter() сразу после subprocess.Popen.
            window_appeared_ts: time.perf_counter() в момент, когда окно ОС нашлось.
            ready_ts: time.perf_counter() сразу после _wait_until_r7_ready.
            setup_elapsed: вычитаемое из тёплого старта время. С аудита
                29.09.2026 все места вызова передают 0 (по умолчанию):
                подготовка окна идёт параллельно с загрузкой документа в
                процессе Р7, и её вычитание занижало открытие. Параметр
                оставлен для совместимости.
            window_found: False, если window_appeared_ts на самом деле —
                момент СДАЧИ ожидания (таймаут), а не появления окна.
                _worker_run_test, в отличие от двух других мест, не
                прерывается по таймауту ожидания окна, а продолжает работу
                без фокуса — честной границы cold/warm тогда нет, и
                возвращать правдоподобно выглядящее, но бессмысленное
                число (~время таймаута) неверно: это было бы тихо неверным
                результатом, а не отказом от измерения.

        Returns:
            dict: {"cold_start_ms", "warm_start_ms", "total_open_ms"}.
            Все три — None при window_found=False.
        """
        if not window_found:
            return {"cold_start_ms": None, "warm_start_ms": None, "total_open_ms": None}
        cold_ms = (window_appeared_ts - open_start) * 1000
        warm_ms = (ready_ts - window_appeared_ts - setup_elapsed) * 1000
        return {
            "cold_start_ms": round(cold_ms, 1),
            "warm_start_ms": round(warm_ms, 1),
            "total_open_ms": round(cold_ms + warm_ms, 1),
        }

    def _write_run_reports(self, results, test_file, open_elapsed, res, leak_verdict, log_cb):
        """JSON, Excel и HTML прогона вкладки в reports_folder.

        Три отчёта пишутся независимо: прежде один try на все три, и открытый
        в Excel .xlsx (PermissionError) лишал прогон JSON, на котором держатся
        сравнение версий и тренды (аудит 06.10.2026). JSON — первым. Время в
        имени файлов — иначе следующий прогон затирал Excel и HTML предыдущего.

        Args:
            res: r7.run_summary.resource_summary(results).
            leak_verdict: r7.run_summary.run_leak_verdict(...) или None.

        Returns:
            tuple[str, Path]: метка времени отчётов и путь к HTML.
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        xlsx_path = self.reports_folder / f"Performance_Report_{ts}.xlsx"
        html_path = xlsx_path.with_suffix(".html")
        version = version_label(self.current_version_info)
        full_data = {}
        try:
            self.reports_folder.mkdir(parents=True, exist_ok=True)
            json_path = self.reports_folder / f"performance_full_{ts}.json"
            full_data = self._build_full_report(ts, version, test_file, results,
                                                report_summary(res, leak_verdict))
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(full_data, f, indent=2, ensure_ascii=False)
            log_cb(f"📄 JSON-данные сохранены: {json_path.name}")
        except Exception as e:
            log_cb(f"❌ JSON-отчёт не сохранён — прогон не попадёт в "
                   f"сравнение и тренды: {type(e).__name__}: {e}")

        try:
            from openpyxl import Workbook as WB
            wb = WB()
            ws = wb.active
            ws.title = "Результаты"
            ws.append(["Операция", "Время (сек)", "RAM (МБ)", "CPU (%)", "Ошибка"])
            for r in results:
                ws.append([r["name"], round(r["time"], 2),
                           r.get("ram") or "", r.get("cpu") or "", r.get("error") or ""])
            wb.save(str(xlsx_path))
            log_cb(f"📊 Excel-отчёт сохранён: {xlsx_path}")
        except Exception as e:
            log_cb(f"⚠️ Excel-отчёт не сохранён: {type(e).__name__}: {e}")

        try:
            html_content = self._generate_html_report(
                results, test_file, open_elapsed, version,
                res["ram_vals"], res["cpu_vals"], res["peak_ram_mb"], res["avg_ram_mb"],
                res["min_ram_mb"], res["peak_cpu_pct"],
                summary=full_data.get("summary"), system=full_data.get("system"),
            )
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            log_cb(f"📄 HTML-отчёт сохранён: {html_path}")
        except Exception as e:
            log_cb(f"⚠️ HTML-отчёт не сохранён: {type(e).__name__}: {e}")
        return ts, html_path

    def _build_full_report(self, ts, version, test_file, results, summary):
        """Содержимое performance_full_*.json — общий писатель для вкладки
        «Производительность» и Batch-режима.

        Раньше словарь собирался двумя копиями в линейном коде воркеров, и
        писатель не проверялся ни одним тестом: страница трендов и сравнение
        версий тестировались на рукописных JSON, так что их расхождение с
        реальным выходом осталось бы незамеченным (QA-аудит 29.09.2026, G-12).

        Returns:
            dict: timestamp, measure_schema, version, test_file, system,
            summary, results.
        """
        return {
            "timestamp": ts,
            "measure_schema": MEASURE_SCHEMA_VERSION,
            "version": version,
            "test_file": str(test_file),
            "system": self._build_system_info(),
            "summary": summary,
            "results": results,
        }

    def _build_system_info(self):
        """Окружение прогона для JSON-результатов — общий код для обоих
        воркеров (одиночный тест и Batch), раньше продублированный дословно
        в двух местах.

        Returns:
            dict: {"os", "ram_total_gb", "cpu_model", "cpu_cores_logical",
            "dpi_scale_pct", "window_size"}.
        """
        sys_mem_gb = (round(psutil.virtual_memory().total / (1024 ** 3), 1)
                     if env.PSUTIL_OK else None)
        return {
            "os": platform.platform(),
            "ram_total_gb": sys_mem_gb,
            "cpu_model": platform.processor() or None,
            # Нужно, чтобы сравнивать нормированный CPU (measure_schema 2,
            # см. OP_BUSY_CORE_PCT) между стендами осмысленно — без числа
            # ядер нормированный процент сам по себе не восстановить обратно
            # в сырую загрузку.
            "cpu_cores_logical": self._cpu_count(),
            # L3 (этап 3): масштаб снимается заново при каждом сохранении
            # отчёта (а не один раз при старте программы) — на случай, если
            # пользователь сменил масштаб между прогонами в одной сессии.
            "dpi_scale_pct": self._get_dpi_scale_pct(),
            # Реально применённый _fix_r7_window_geometry размер окна Р7 за
            # этот прогон; None — геометрию не фиксировали вовсе (WIN32_OK
            # выключен, окно не нашлось).
            "window_size": self._applied_r7_window_size,
            # Аудит 29.09.2026, пункт 11: окружение, снятое до запуска Р7
            # (_capture_environment); None — прогон старой версии.
            "environment": self._environment_with_interference(),
            "interference": dict(getattr(self, "_interference", None) or {}),
        }

    INTERFERENCE_TEXT = {
        "focus_lost": "окно Р7 теряло фокус {n} раз — на стенде работала другая программа",
        "clipboard_foreign": "буфер обмена перезаписан посторонней программой {n} раз — "
                             "копию листа пришлось делать заново",
    }

    def _environment_with_interference(self):
        """Окружение, снятое до прогона, плюс вмешательства стенда во время
        него — тем же списком предупреждений «Условия прогона» в HTML."""
        env_info = getattr(self, "_run_environment", None)
        counts = getattr(self, "_interference", None) or {}
        extra = [self.INTERFERENCE_TEXT[k].format(n=n) for k, n in counts.items()
                 if n and k in self.INTERFERENCE_TEXT]
        if not extra:
            return env_info
        out = dict(env_info or {})
        out["warnings"] = list(out.get("warnings") or []) + extra
        return out

    def _generate_html_report(self, results, test_file, open_elapsed,
                              version_str, ram_vals, cpu_vals,
                              peak_ram, avg_ram, min_ram, peak_cpu,
                              summary=None, system=None):
        """HTML-отчёт прогона (templates/reports/run.html, см. r7_reports).

        ram_vals/cpu_vals/avg_ram/min_ram оставлены в сигнатуре ради
        вызывающего кода; страница берёт пики из summary и из записей
        операций. summary/system — те же, что ушли в JSON; без них
        собираются на месте.
        """
        if summary is None:
            summary = {"peak_ram_mb": peak_ram, "avg_ram_mb": avg_ram,
                       "min_ram_mb": min_ram, "peak_cpu_pct": peak_cpu}
        if system is None:
            try:
                system = self._build_system_info()
            except Exception:
                system = {}
        try:
            cpu_count = self._cpu_count()
        except Exception:
            cpu_count = None
        model = r7_reports.run_report_model(
            results, Path(test_file), open_elapsed, version_str, system=system,
            summary=summary, cpu_count=cpu_count, schema=MEASURE_SCHEMA_VERSION)
        return r7_reports.render("run.html", **model)

    def _load_comparison_settings(self):
        path = config.BASE_DIR / "last_comparison_settings.json"
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"custom_names": {}, "last_selected_files": [], "last_base_version": ""}

    def _save_comparison_settings(self, settings):
        path = config.BASE_DIR / "last_comparison_settings.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.add_test_log(f"   ⚠️ Настройки сравнения не сохранены "
                              f"({type(e).__name__}: {e})")

    def _load_trends_runs(self):
        """Читает все performance_full_*.json из reports_folder в
        хронологическом порядке (по времени модификации файла — timestamp
        внутри JSON тот же по построению, mtime надёжнее при ручном
        переименовании файлов).

        Файлы, которые не удалось разобрать (битый JSON, обрезанный прогон),
        пропускаются молча — один повреждённый файл не должен ронять всю
        страницу трендов, накопленную за недели прогонов.

        Returns:
            list[dict]: [{"path", "ts_raw", "ts_disp", "version", "schema",
            "results": {имя_операции: dict-результат}}, ...], отсортировано
            по времени.
        """
        files = sorted(self.reports_folder.glob("performance_full_*.json"),
                       key=lambda p: p.stat().st_mtime)
        runs = []
        for fp in files:
            try:
                with open(fp, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            ts_raw = data.get("timestamp", "")
            ts_disp = (f"{ts_raw[6:8]}.{ts_raw[4:6]}.{ts_raw[:4]} "
                      f"{ts_raw[9:11]}:{ts_raw[11:13]}"
                      if len(ts_raw) >= 13 else (ts_raw or fp.stem))
            runs.append({
                "path": fp,
                "ts_raw": ts_raw,
                "ts_disp": ts_disp,
                "version": data.get("version") or fp.stem,
                "schema": data.get("measure_schema", 1),
                "results": {r["name"]: r for r in data.get("results", [])
                           if isinstance(r, dict) and "name" in r},
            })
        return runs

    def _generate_trends_html(self, runs):
        """Страница трендов из загруженных прогонов (см. _load_trends_runs):
        график на операцию, точки по версиям, полоса MAD, границы смены
        версии. Разделено с загрузкой с диска ради тестируемости.

        Args:
            runs: список прогонов в формате _load_trends_runs.
        """
        model = r7_reports.trends_model(runs, palette=self.TRENDS_CHART_COLORS,
                                        other=SERIES_OTHER_COLOR)
        return r7_reports.render("trends.html", **model)

    def _comparable_time(result):
        """См. r7_reports.comparable_time — одна реализация на отчёты."""
        return r7_reports.comparable_time(result)

    @staticmethod
    def _valid_runs(result):
        """См. r7_reports.valid_runs — те же повторы, что вошли в медиану."""
        return r7_reports.valid_runs(result)

    def _generate_comparison_html(self, datasets, base_path_str):
        """Страница сравнения 2–8 прогонов (templates/reports/comparison.html).

        Args:
            datasets: list of dicts {path: str, version: str, data: dict}
            base_path_str: path string of the dataset used as baseline
        """
        model = r7_reports.comparison_model(datasets, base_path_str, compare_runs,
                                            MIN_RUNS_FOR_COMPARISON)
        return r7_reports.render("comparison.html", **model)

    def _generate_batch_summary_html(self, batch_results):
        """Сводка Batch по версиям (templates/reports/batch.html)."""
        return r7_reports.render("batch.html", **r7_reports.batch_model(batch_results))

    def _load_last_params(self):
        """Returns dict with last used rows/cols/filename, or defaults."""
        path = config.BASE_DIR / self._LAST_PARAMS_FILE
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:  # файла нет или он битый — берём параметры по умолчанию
            pass
        return {"rows": 50000, "cols": 50, "filename": "test_data_50000x50.xlsx"}

    def _save_last_params(self, rows, cols, filename):
        """Persists rows/cols/filename to last_test_params.json."""
        path = config.BASE_DIR / self._LAST_PARAMS_FILE
        try:
            path.write_text(
                json.dumps({"rows": rows, "cols": cols, "filename": filename},
                           indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            self.add_test_log(f"   ⚠️ Параметры теста не сохранены ({type(e).__name__}: {e})")
