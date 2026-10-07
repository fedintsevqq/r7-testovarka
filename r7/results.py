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
import r7_doc_ops
import r7_pptx_ops
from r7 import build_meta, config, env, fingerprint, noise, settings, team_folder, test_selection
from r7.batch_config import FIXTURE_COLS, FIXTURE_NAME, FIXTURE_ROWS
from r7.config import DEFAULT_TEST_RUNS, MEASURE_SCHEMA_VERSION, RUNS_MAX, RUNS_MIN, SERIES_OTHER_COLOR
from r7.editors import EDITOR_DOCUMENT, EDITOR_PRESENTATION, EDITOR_SPREADSHEET
from r7.env import psutil
from r7.run_summary import report_summary
from r7.version import __version__
from r7.stats import MIN_RUNS_FOR_COMPARISON, compare_runs
from r7.versions import version_label

# Списки тестов документа и презентации для вкладки «Производительность».
_EDITOR_DEFAULT_RUNS = {EDITOR_DOCUMENT: r7_doc_ops.DEFAULT_DOC_RUNS,
                        EDITOR_PRESENTATION: r7_pptx_ops.DEFAULT_PPTX_RUNS}
_EDITOR_EXPORTS = {
    EDITOR_DOCUMENT: frozenset({r7_doc_ops.EXPORT_PDF_TEST, r7_doc_ops.EXPORT_DOCX_TEST}),
    EDITOR_PRESENTATION: frozenset({r7_pptx_ops.EXPORT_PDF_TEST, r7_pptx_ops.EXPORT_PPTX_TEST}),
}
_OPS_GROUP_TITLES = {EDITOR_SPREADSHEET: "ОПЕРАЦИИ В ТАБЛИЦЕ",
                     EDITOR_DOCUMENT: "ОПЕРАЦИИ В ДОКУМЕНТЕ",
                     EDITOR_PRESENTATION: "ОПЕРАЦИИ В ПРЕЗЕНТАЦИИ"}


class ResultsMixin:
    """Отчёты, тренды и сохранённые настройки — часть R7Testovarka (через наследование)."""

    # Редактор, выбранный на вкладке «Производительность» (переключатель над
    # списком тестов). Не путать с _run_editor — режимом идущего прогона.
    _perf_editor = EDITOR_SPREADSHEET

    def _tab_test_names(self, editor=None):
        """Тесты редактора в порядке прогона: у таблиц — встроенные и тесты
        плагинов (effective_test_definitions), у документа и презентации —
        их списки (плагины только для таблиц)."""
        editor = editor or self._perf_editor
        if editor == EDITOR_SPREADSHEET:
            return list(self.effective_test_definitions())
        return list(self.editor_test_names().get(editor, []))

    def _tab_is_export(self, name, editor=None):
        """Тест экспорта в списке редактора: у таблиц — _is_export_test
        (и плагины kind="export"), у документа и презентации — «Сохранение в …»."""
        editor = editor or self._perf_editor
        if editor == EDITOR_SPREADSHEET:
            return self._is_export_test(name)
        return name in _EDITOR_EXPORTS.get(editor, ())

    def _default_test_entry(self, name):
        """Настройки теста, которого ещё нет в selected_tests.json."""
        editor_runs = _EDITOR_DEFAULT_RUNS.get(self._perf_editor)
        if editor_runs is not None:
            return {"enabled": True, "runs": editor_runs.get(name, DEFAULT_TEST_RUNS)}
        if name in self.EXTRA_FORMAT_TESTS:
            return {"enabled": False, "runs": self.DEFAULT_FORMAT_TEST_RUNS}
        if self._is_export_test(name):      # и экспорт из плагина (kind="export")
            return {"enabled": True, "runs": self.DEFAULT_FORMAT_TEST_RUNS}
        if name == self.OPEN_TEST_NAME:
            return {"enabled": True, "runs": self.DEFAULT_OPEN_RUNS}
        return {"enabled": True, "runs": self._default_edit_runs()}

    @staticmethod
    def _default_edit_runs():
        """Повторы по умолчанию для тестов правки: default_runs из
        r7_settings.json в пределах RUNS_MIN..RUNS_MAX, иначе DEFAULT_TEST_RUNS."""
        custom = settings.get("default_runs")
        try:
            runs = int(custom)
        except (TypeError, ValueError):  # ключа нет или в нём не число — умолчание
            return DEFAULT_TEST_RUNS
        return max(RUNS_MIN, min(RUNS_MAX, runs))

    def _test_groups(self):
        """Тесты по группам в порядке TEST_DEFINITIONS: [(заголовок, [имена])].

        Экспорт выделен отдельно: один его повтор на большом файле идёт до
        полутора минут, и это стоит видеть до запуска. Тесты плагинов — в
        конце своей группы (правка или экспорт по их .kind).
        """
        names = self._tab_test_names()
        opening = [n for n in names if n == self.OPEN_TEST_NAME]
        exports = [n for n in names if self._tab_is_export(n)]
        ops = [n for n in names if n not in opening and n not in exports]
        return [(title, names) for title, names in (
            ("ОТКРЫТИЕ ФАЙЛА", opening),
            (_OPS_GROUP_TITLES[self._perf_editor], ops),
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
    def _load_selection_store(self):
        """selected_tests.json целиком: {"editor", "sections"} (r7/test_selection.py).

        Читаются все три формата: {тест: bool}, плоский {тест: {enabled,
        runs}} (это выбор таблиц) и по редакторам. Файл читается при запуске
        программы: битый файл или запись ("runs": "abc", список вместо
        словаря) раньше роняли интерфейс (QA-аудит 29.09.2026, G-14) —
        теперь это значения по умолчанию.
        """
        path = config.BASE_DIR / test_selection.SELECTION_FILE
        raw = None
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception:  # битый JSON, нет прав — как будто файла нет
                raw = None
        return test_selection.parse_selection(raw)

    def _load_test_selection(self):
        """Сохранённый выбор тестов редактора, выбранного на вкладке.

        Returns:
            dict: тест → {"enabled": bool, "runs": int}.
        """
        store = self._load_selection_store()
        return store["sections"].get(self._perf_editor, {})

    def _saved_perf_editor(self):
        """Редактор, выбранный на вкладке в прошлый раз (по умолчанию — таблица)."""
        return self._load_selection_store()["editor"]

    def _save_test_selection(self):
        """Пишет выбор тестов текущего редактора в selected_tests.json;
        выбор других редакторов остаётся как был."""
        path = config.BASE_DIR / test_selection.SELECTION_FILE
        try:
            editor = self._perf_editor
            current = {
                name: {"enabled": var.get(), "runs": self.test_runs[name].get()}
                for name, var in self.test_vars.items()
            }
            sections = {**self._load_selection_store()["sections"], editor: current}
            data = test_selection.build_selection(sections, editor)
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
        json_path = self.reports_folder / f"performance_full_{ts}.json"
        version = version_label(self.current_version_info)
        full_data = {}
        json_written = False
        try:
            self.reports_folder.mkdir(parents=True, exist_ok=True)
            full_data = self._build_full_report(ts, version, test_file, results,
                                                report_summary(res, leak_verdict))
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(full_data, f, indent=2, ensure_ascii=False)
            json_written = True
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
                build=full_data.get("build"),
            )
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            log_cb(f"📄 HTML-отчёт сохранён: {html_path}")
        except Exception as e:
            log_cb(f"⚠️ HTML-отчёт не сохранён: {type(e).__name__}: {e}")
        if json_written:
            self._copy_report_to_team(json_path, html_path, log_cb)
        return ts, html_path

    def _copy_report_to_team(self, json_path, html_path, log_cb):
        """Копия отчёта в общую папку команды (team_reports_folder), подпапка
        «<hostname>-<отпечаток>». Папка не задана — тихо ничего; недоступна
        или не записалась — строка в журнал, прогон не страдает.

        Returns:
            Path | None: папка назначения, если копия удалась.
        """
        folder = team_folder.configured_folder()
        if folder is None:
            return None
        env_info = getattr(self, "_run_environment", None) or {}
        fp_hash = env_info.get("fingerprint_hash") if isinstance(env_info, dict) else None
        machine_dir = fingerprint.machine_dir_name(fp_hash)
        return team_folder.copy_reports(folder, machine_dir, (json_path, html_path), log_cb)

    def _build_metadata(self):
        """Объект `build` отчёта (r7.build_meta): версия и сборка из реестра,
        exe — тот, что нашёл _find_r7_path перед запуском (кэш пути; реестр
        заново не читается, пока Р7 может быть ещё открыт). Дистрибутив
        известен, только если версию ставил Batch в этой сессии и она всё ещё
        установлена (_session_installer)."""
        info = getattr(self, "current_version_info", None)
        installer = None
        rec = getattr(self, "_session_installer", None)
        if rec and (not rec[1] or rec[1] == (info or {}).get("version")):
            installer = rec[0]
        return build_meta.build_metadata(
            info, getattr(self, "_cached_r7_path", None), installer,
            settings.get("changelog_url_template"))

    def _build_full_report(self, ts, version, test_file, results, summary):
        """Содержимое performance_full_*.json — общий писатель для вкладки
        «Производительность» и Batch-режима.

        Раньше словарь собирался двумя копиями в линейном коде воркеров, и
        писатель не проверялся ни одним тестом: страница трендов и сравнение
        версий тестировались на рукописных JSON, так что их расхождение с
        реальным выходом осталось бы незамеченным (QA-аудит 29.09.2026, G-12).

        Returns:
            dict: timestamp, measure_schema, tool_version, version, build,
            test_file, system, summary, results.
        """
        return {
            "timestamp": ts,
            "measure_schema": MEASURE_SCHEMA_VERSION,
            # Версия инструмента — метаданные, не схема замера: цифры от неё не
            # зависят, а читатели (тренды, сравнение) неизвестные ключи
            # пропускают, поэтому MEASURE_SCHEMA_VERSION не поднимается.
            "tool_version": __version__,
            "version": version,
            # Сборка Р7 (номер, exe, sha256, дистрибутив) — тоже метаданные:
            # схема не поднимается, читатели терпят отчёты без ключа `build`
            # (r7.build_meta.build_summary).
            "build": self._build_metadata(),
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
                              summary=None, system=None, build=None):
        """HTML-отчёт прогона (templates/reports/run.html, см. r7_reports).

        ram_vals/cpu_vals/avg_ram/min_ram оставлены в сигнатуре ради
        вызывающего кода; страница берёт пики из summary и из записей
        операций. summary/system/build — те же, что ушли в JSON; без них
        собираются на месте (build — None: блок «Стенд» без сборки).
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
            summary=summary, cpu_count=cpu_count, schema=MEASURE_SCHEMA_VERSION,
            tool_version=__version__, build=build,
            editor=getattr(self, "_run_editor", None))
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
        переименовании файлов). Если задана общая папка команды
        (team_reports_folder), добавляются и отчёты из её подпапок с меткой
        machine = имя подпапки; файл с тем же именем, что локальный, второй
        раз не читается (своя же копия).

        Файлы, которые не удалось разобрать (битый JSON, обрезанный прогон),
        пропускаются молча — один повреждённый файл не должен ронять всю
        страницу трендов, накопленную за недели прогонов.

        Returns:
            list[dict]: [{"path", "ts_raw", "ts_disp", "version", "schema",
            "machine" (None — локальный отчёт), "fingerprint" (хэш или None),
            "fingerprint_fields" (словарь отпечатка или None),
            "results": {имя_операции: dict-результат}}, ...], отсортировано
            по времени.
        """
        files = [(None, fp) for fp in self.reports_folder.glob("performance_full_*.json")]
        seen = {fp.name for _m, fp in files}
        for machine, fp in team_folder.team_report_files(team_folder.configured_folder()):
            if fp.name not in seen:
                seen.add(fp.name)
                files.append((machine, fp))

        def _mtime(item):
            try:
                return item[1].stat().st_mtime
            except OSError:
                return 0.0
        runs = []
        for machine, fp in sorted(files, key=_mtime):
            run = self._trend_run_from_file(fp, machine)
            if run is not None:
                runs.append(run)
        return runs

    @staticmethod
    def _trend_run_from_file(fp, machine=None):
        """Одна запись трендов из файла отчёта; None — файл не разобрать."""
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        ts_raw = data.get("timestamp", "")
        ts_disp = (f"{ts_raw[6:8]}.{ts_raw[4:6]}.{ts_raw[:4]} "
                   f"{ts_raw[9:11]}:{ts_raw[11:13]}"
                   if len(ts_raw) >= 13 else (ts_raw or fp.stem))
        fp_hash, fp_fields = fingerprint.report_fingerprint(data)
        # Отчёты документов (этап 5) — свои линии трендов: «Открытие файла»
        # .docx и .xlsx на одном графике смешали бы разные величины.
        editor = data.get("editor") or "spreadsheet"
        suffix = ("" if editor == "spreadsheet"
                  else f" · {r7_reports.EDITOR_TITLES.get(editor, editor)}")
        return {
            "path": fp,
            "ts_raw": ts_raw,
            "ts_disp": ts_disp,
            "version": data.get("version") or fp.stem,
            "schema": data.get("measure_schema", 1),
            "machine": machine,
            "fingerprint": fp_hash,
            "fingerprint_fields": fp_fields,
            "editor": editor,
            "results": {r["name"] + suffix: r for r in data.get("results", [])
                        if isinstance(r, dict) and "name" in r},
        }

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
        # Пороги — из профиля шума машины базового прогона (r7/noise.py);
        # профиля нет — 10 % для всех операций.
        base = next((ds for ds in datasets if ds["path"] == base_path_str), None)
        profile = noise.noise_for_report(getattr(self, "reports_folder", None),
                                         base["data"] if base else None)
        model = r7_reports.comparison_model(datasets, base_path_str, compare_runs,
                                            MIN_RUNS_FOR_COMPARISON, noise_profile=profile)
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
        return {"rows": FIXTURE_ROWS, "cols": FIXTURE_COLS, "filename": FIXTURE_NAME}

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
