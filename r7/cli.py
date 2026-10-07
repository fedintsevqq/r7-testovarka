"""Командная строка: `python -m r7 run | trace | bisect | corpus | corpus-compare |
suites | check` (docs/cli.md, корпус — docs/corpus.md).

Прогон без окна идёт тем же воркером, что и вкладка «Производительность»
(_spreadsheet_worker), на «голом» R7Testovarka: класс собирается через
__new__ и _init_state, а виджеты и диалоги подменяются заглушками — как в
tests/nightly_local.py, только без Tk. r7_Testovarka импортируется лениво
и только здесь: при `python -m r7` он не __main__, второй копии класса не
возникает; модуль tkinter при этом загружается, но окно не создаётся.

Коды выхода (EXIT_*): 0 — всё в норме; 1 — регрессия к эталону или медиана
выше бюджета; 2 — прогон не удался (отчёта нет, прерван Ctrl+C);
3 — не выполнены условия (Р7 запущен или не найден, нет фикстуры, набор
или эталон не читаются).
"""
import argparse
import contextlib
import io
import json
import sys
import threading
import time
from pathlib import Path

from r7 import (batch_config, bisect, bisect_runner, config, corpus, firstrun, logfile, noise,
                plugins, privileges, settings, trace)
from r7.gate import OPEN_TEST_NAME, attach_diagnostics, gate_model, gate_page, junit_xml
from r7.stats import MIN_RUNS_FOR_COMPARISON
from r7.suites import SuiteError, list_suites, load_suite

EXIT_OK, EXIT_GATE, EXIT_RUN, EXIT_PRECONDITION = 0, 1, 2, 3
JOIN_POLL_SEC = 0.5          # главный поток ждёт воркер короткими join, чтобы ловить Ctrl+C
STATUS_MARK = {firstrun.OK: "✓", firstrun.WARN: "!", firstrun.FAIL: "✗"}


def utf8_console():
    """Консоль Windows по умолчанию cp1251/cp866: без этого print с эмодзи
    и кириллицей из журнала прогона падает. Нет консоли — оставляем как есть."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # поток None (pythonw) или не TextIOWrapper — кодировка по умолчанию
            pass


def log(msg):
    """Строка журнала прогона: в консоль с меткой времени и в файловый
    журнал (уровень — по значку, как у add_test_log в окне)."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    logfile.get_logger().log(logfile.level_for_message(msg), "%s", msg)


def app_class():
    """Класс R7Testovarka — лениво. Импорт печатает диагностику
    WEBDRIVER_OK; в консоли CLI она лишняя, поэтому уходит в файловый журнал."""
    base = str(config.BASE_DIR)
    if base not in sys.path:
        sys.path.insert(0, base)                 # r7_Testovarka.py лежит рядом с пакетом
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        import r7_Testovarka as r7mod
    for line in buf.getvalue().splitlines():
        logfile.get_logger().info("%s", line)
    return r7mod.R7Testovarka


def make_headless_app(log_cb=log, reports_folder=None):
    """R7Testovarka без окна: состояние как у __init__ (_init_state), журнал —
    в log_cb, виджеты и диалог после прогона — заглушки."""
    cls = app_class()
    app = cls.__new__(cls)
    app._init_state()
    if reports_folder:
        app.reports_folder = Path(reports_folder)
        app.reports_folder.mkdir(parents=True, exist_ok=True)
    app.add_test_log = log_cb
    # Статус, шкала прогресса и диалог «Тест завершён» показываются в окне,
    # которого здесь нет; воркер зовёт их через эти три имени.
    app._ui_call = lambda fn: None
    app._set_perf_progress = lambda done, total: None
    app._show_post_test_dialog = lambda *a, **k: None
    # Версия для шапки отчёта — чистое чтение реестра (detect_current_version
    # пишет в виджеты).
    app.current_version_info = app._read_current_version_from_registry()
    return app


# ── run ──────────────────────────────────────────────────────────────────

# Воркер прогона по редактору набора; не указан — табличный.
WORKERS = {"spreadsheet": "_spreadsheet_worker", "document": "_document_worker",
           "presentation": "_presentation_worker"}


def suite_names(app):
    """Допустимые имена тестов по редактору для наборов: у таблиц — встроенные
    и тесты плагинов (effective_test_definitions), у документов и презентаций —
    DocumentOps и PresentationOps."""
    names = dict(app.editor_test_names())
    names["spreadsheet"] = list(app.effective_test_definitions())
    return names


def preconditions(app, editor="spreadsheet"):
    """Что мешает прогону: Р7 запущен, Р7 не найден, нет фикстуры.
    Список строк «проблема. Что сделать»; пустой — можно запускать.
    Фикстуры документа и презентации воркер создаёт сам (r7/doc_fixtures.py,
    r7/pptx_fixtures.py) — их не ищем."""
    checks = [firstrun.check_r7_running(app), firstrun.check_r7_found(app)]
    if editor == "spreadsheet":
        checks.append(firstrun.check_fixture(
            firstrun.fixture_search_dirs(app.test_files_folder)))
    return [f"{c.name}: {c.detail}. {c.fix}".rstrip(". ") + "."
            for c in checks if c.status != firstrun.OK]


def run_suite(app, suite, stop_event=None):
    """Прогон набора воркером вкладки. Возвращает путь к новому
    performance_full_*.json или None (воркер упал или отчёт не записан)."""
    stop_event = stop_event or threading.Event()
    before = set(app.reports_folder.glob("performance_full_*.json"))
    failures = []

    run = getattr(app, WORKERS.get(suite.editor, "_spreadsheet_worker"))

    def worker():
        try:
            run(set(suite.tests), dict(suite.tests), stop_event)
        except Exception as e:
            logfile.get_logger().exception("прогон набора %s упал", suite.name)
            log(f"❌ Прогон упал: {type(e).__name__}: {e}")
            failures.append(e)

    thread = threading.Thread(target=worker, name="r7-cli-run", daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(JOIN_POLL_SEC)
    except KeyboardInterrupt:
        log("⏹ Ctrl+C — останавливаю после текущей операции, Р7 закроется штатно")
        stop_event.set()
        thread.join()
        failures.append(KeyboardInterrupt())
    new = sorted(set(app.reports_folder.glob("performance_full_*.json")) - before,
                 key=lambda p: p.stat().st_mtime)
    if failures or not new:
        return None
    return new[-1]


def load_baseline(path):
    """Эталонный performance_full_*.json; SuiteError с причиной, если не читается."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SuiteError(f"эталон не найден: {path}") from None
    except (OSError, ValueError) as e:
        raise SuiteError(f"эталон {path.name} не прочитан: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise SuiteError(f"эталон {path.name}: это не performance_full_*.json "
                         f"(нет списка results)")
    return data


def check_baseline_editor(baseline, suite, name):
    """Эталон того же редактора, что и набор: «Открытие файла» .docx против
    .xlsx — разные величины. Отчёт без "editor" — табличный (до этапа 5)."""
    editor = (baseline or {}).get("editor") or "spreadsheet"
    if baseline is not None and editor != suite.editor:
        raise SuiteError(f"эталон {name} снят на редакторе «{editor}», а набор "
                         f"{suite.name} — «{suite.editor}»: сравнивать нечего")


def format_summary(model):
    """Таблица итогов для консоли: операция, медиана, MAD, бюджет, эталон, Δ, вердикт."""
    lines = [f"Набор {model['suite_name']}: {model['verdict']}"]
    lines.append(f"{'операция':44} {'медиана':>8} {'MAD':>6} {'бюджет':>7} {'эталон':>8} "
                 f"{'Δ%':>7}  вердикт")
    for r in model["rows"]:
        med = "—" if r["median"] is None else f"{r['median']:.3f}"
        mad = "—" if r["mad"] is None or r["median"] is None else f"{r['mad']:.3f}"
        budget = "—" if r["budget"] is None else f"{r['budget']:g}"
        base = "—" if r["baseline_median"] is None else f"{r['baseline_median']:.3f}"
        delta = "" if r["delta_pct"] is None else f"{r['delta_pct']:+.1f}"
        mark = "" if r["verdict"] == "ok" else "  <<"
        lines.append(f"{r['name'][:44]:44} {med:>8} {mad:>6} {budget:>7} {base:>8} "
                     f"{delta:>7}  {r['verdict_text']}{mark}")
    for reason in model["problems"]:
        lines.append(f"  • {reason}")
    for w in model["warnings"]:
        lines.append(f"  ⚠️ {w}")
    if model.get("noise_note"):
        lines.append(f"  ℹ️ {model['noise_note']}")
    return "\n".join(lines)


def cmd_run(args):
    app = make_headless_app(log, args.out)
    try:
        suite = load_suite(args.suite, suite_names(app))
        baseline = load_baseline(args.baseline) if args.baseline else None
        check_baseline_editor(baseline, suite, Path(args.baseline).name if args.baseline else "")
    except SuiteError as e:
        log(f"❌ {e}")
        return EXIT_PRECONDITION
    problems = preconditions(app, suite.editor)
    if problems:
        for p in problems:
            log(f"❌ {p}")
        return EXIT_PRECONDITION

    log(f"▶ Набор {suite.name}: {len(suite.tests)} тестов, {suite.total_runs} повторов, "
        f"отчёты в {app.reports_folder}")
    started = time.time()
    report_path = run_suite(app, suite)
    if report_path is None:
        log("❌ Прогон не удался — отчёта нет")
        return EXIT_RUN
    log(f"📄 Отчёт: {report_path.name} ({(time.time() - started) / 60:.1f} мин)")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    ts = report_path.stem.removeprefix("performance_full_")

    model = gate_model(report.get("results", []), suite, baseline=baseline,
                       schema=report.get("measure_schema"), version=report.get("version"),
                       report_name=report_path.name,
                       baseline_name=Path(args.baseline).name if args.baseline else None,
                       noise_profile=noise.noise_for_report(app.reports_folder, report))
    if args.trace_regressions or settings.get("trace_on_regression"):
        if suite.editor == "spreadsheet":
            model = attach_diagnostics(model, trace_regressions(app, model, report_path, ts))
        else:
            # r7/trace.py повторяет операции SpreadsheetOps — других редакторов не знает.
            log(f"ℹ️ Трасса регрессий пока только для таблиц — для редактора "
                f"«{suite.editor}» пропущена")
    print(format_summary(model), flush=True)
    if args.junit:
        junit_path = Path(args.junit)
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        junit_path.write_text(junit_xml(model, suite_names(app)[suite.editor]),
                              encoding="utf-8")
        log(f"📄 JUnit: {junit_path}")
    if args.gate:
        gate_path = app.reports_folder / f"gate_{ts}.html"
        gate_path.write_text(gate_page(model), encoding="utf-8")
        log(f"📄 Страница готовности: {gate_path}")
    return EXIT_OK if model["ready"] else EXIT_GATE


def trace_regressions(app, model, report_path, ts):
    """Трасса операций с регрессией или «вероятной регрессией» к эталону:
    вторая короткая сессия Р7 после прогона (воркер свой Р7 уже закрыл),
    по одному диагностическому повтору вне замера (r7/trace.py). Записи
    diagnostics добавляются в полный JSON прогона. Код выхода не меняет:
    трасса — диагностика, вердикт уже вынесен. Returns: {операция: запись}."""
    names = trace.regressed_ops(model)
    if not names:
        log("ℹ️ Трасса не нужна: регрессий к эталону нет")
        return {}
    log(f"🔬 Трасса для {len(names)} операций с регрессией: {', '.join(names)}")
    try:
        diags = app.trace_ops_session(names, ts, report_path.parent)
        if diags:
            trace.attach_to_report(report_path, diags)
            log(f"📄 Трассы: diagnostics в {report_path.name}")
        return diags
    except Exception as e:  # трасса не должна ронять прогон, у которого уже есть отчёт
        logfile.get_logger().exception("трасса после прогона упала")
        log(f"⚠️ Трасса не снята: {type(e).__name__}: {e}")
        return {}


def cmd_trace(args):
    """Диагностический повтор одной операции с трассой на установленном Р7."""
    app = make_headless_app(log, args.out)
    names = app.effective_test_definitions()
    if args.op not in names or args.op == OPEN_TEST_NAME:
        allowed = [n for n in names if n != OPEN_TEST_NAME]
        log(f"❌ Нет такой операции: «{args.op}». Можно: " + "; ".join(allowed))
        return EXIT_PRECONDITION
    report = Path(args.report) if args.report else None
    if report is not None and not report.is_file():
        log(f"❌ Отчёт не найден: {report}")
        return EXIT_PRECONDITION
    problems = preconditions(app)
    if problems:
        for p in problems:
            log(f"❌ {p}")
        return EXIT_PRECONDITION
    if report is not None:
        ts, out_dir = report.stem.removeprefix("performance_full_"), report.parent
    else:
        ts, out_dir = time.strftime("%Y%m%d_%H%M%S"), app.reports_folder
    diags = app.trace_ops_session([args.op], ts, out_dir)
    rec = diags.get(args.op)
    if not rec or rec.get("error"):
        log(f"❌ Трасса не снята: {(rec or {}).get('error') or 'Р7 не открыл файл или нет CDP'}")
        return EXIT_RUN
    if report is not None:
        trace.attach_to_report(report, diags)
        log(f"📄 diagnostics добавлены в {report.name}")
    else:
        path = out_dir / f"diagnostics_{ts}.json"
        path.write_text(json.dumps({trace.DIAGNOSTICS_KEY: diags}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        log(f"📄 Сводка: {path}")
    print(f"{args.op}: {trace.diagnostics_summary(rec)}", flush=True)
    return EXIT_OK


# ── bisect ───────────────────────────────────────────────────────────────

# Итог бисекта → код выхода. Найдена первая плохая — 0; ответа нет (крайние
# не различаются, ускорение вместо регрессии, отрезок из-за пропусков) — 1;
# бисект не дошёл до конца (крайняя не измерена, Ctrl+C) — 2.
BISECT_EXIT = {bisect.STATUS_FOUND: EXIT_OK, bisect.STATUS_RANGE: EXIT_GATE,
               bisect.STATUS_NO_CHANGE: EXIT_GATE, bisect.STATUS_SPEEDUP: EXIT_GATE,
               bisect.STATUS_ERROR: EXIT_RUN, bisect.STATUS_STOPPED: EXIT_RUN}


def headless_installer(app):
    """Установка без окна: строка статуса установщика — в журнал, версия —
    из реестра (detect_current_version окна пишет в метку вкладки «Версии»)."""
    app._set_status = lambda text: log(f"   {text}")

    def detect_current_version():
        app.current_version_info = app._read_current_version_from_registry()
    app.detect_current_version = detect_current_version
    return app


BISECT_STOP_NOTE = "⏹ Ctrl+C — останавливаю после текущего замера; исходная версия вернётся"


def run_worker(target, stop_event, name, stop_note=BISECT_STOP_NOTE):
    """target() в фоновом потоке; Ctrl+C ставит stop_event и ждёт, пока
    поток доделает своё (у бисекта — вернёт исходную версию).
    Returns: (значение, исключение | None, прерван ли Ctrl+C)."""
    box = {"value": None, "error": None}

    def worker():
        try:
            box["value"] = target()
        except Exception as e:
            box["error"] = e

    thread = threading.Thread(target=worker, name=name, daemon=True)
    thread.start()
    interrupted = False
    try:
        while thread.is_alive():
            thread.join(JOIN_POLL_SEC)
    except KeyboardInterrupt:
        log(stop_note)
        stop_event.set()
        interrupted = True
        thread.join()
    return box["value"], box["error"], interrupted


def bisect_preflight(app, args):
    """Сборки, крайние, операция, фикстура и права. Returns: (план, None) или
    (None, список проблем). План — dict: builds, good, bad, test_file."""
    problems = []
    names = app.effective_test_definitions()
    if args.op not in names or args.op == OPEN_TEST_NAME:
        allowed = [n for n in names if n != OPEN_TEST_NAME]
        problems.append(f"Нет такой операции: «{args.op}» (открытие файла бисект не меряет). "
                        f"Можно: " + "; ".join(allowed))
    if args.runs < MIN_RUNS_FOR_COMPARISON or args.max_runs < args.runs:
        problems.append(f"--runs не меньше {MIN_RUNS_FOR_COMPARISON}, --max-runs не меньше --runs")
    dist = Path(args.dist) if args.dist else Path(app.distributives_folder)
    builds, unversioned = bisect.builds_from_files(
        batch_config.list_distributives(dist, app._extract_version))
    for name in unversioned:
        log(f"⚠️ {name}: в имени нет номера версии — в бисект не входит")
    plan = {"builds": builds}
    try:
        plan["good"] = bisect.resolve_build(builds, args.good)
        plan["bad"] = bisect.resolve_build(builds, args.bad)
        plan["segment"] = bisect.segment(builds, plan["good"], plan["bad"])
    except bisect.BisectError as e:
        problems.append(f"{e} (папка {dist}, сборок с номером: {len(builds)})")
    if not privileges.is_admin():
        problems.append("Нужны права администратора: бисект ставит и удаляет версии Р7-Офис. "
                        "Запустите консоль от имени администратора.")
    running = firstrun.check_r7_running(app)
    if running.status != firstrun.OK:
        problems.append(f"{running.detail}. {running.fix}".rstrip(". ") + ".")
    plan["test_file"] = app._locate_test_file()
    if plan["test_file"] is None:
        problems.append("Рабочая фикстура не найдена. Создайте её: «Тестовые файлы» в окне.")
    if not args.no_restore:
        orig = (app._read_current_version_from_registry() or {}).get("version")
        if orig and bisect.find_build_for_installed(builds, orig) is None:
            problems.append(f"Дистрибутива исходной версии {orig} нет в {dist} — вернуть её "
                            f"после бисекта не из чего. Положите его туда или добавьте "
                            f"--no-restore.")
    return (None, problems) if problems else (plan, None)


def save_bisect_report(folder, result, ts):
    """bisect_<ts>.json и bisect_<ts>.html. Returns: (путь JSON, путь HTML)."""
    import r7_reports
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    data = result.to_dict()
    json_path = folder / f"bisect_{ts}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = folder / f"bisect_{ts}.html"
    html_path.write_text(r7_reports.bisect_page(r7_reports.bisect_model(data)), encoding="utf-8")
    return json_path, html_path


def cmd_bisect(args):
    """Бисект по сборкам: первая сборка, на которой операция медленнее базы."""
    app = headless_installer(make_headless_app(log, args.out))
    plan, problems = bisect_preflight(app, args)
    if problems:
        for p in problems:
            log(f"❌ {p}")
        return EXIT_PRECONDITION
    seg = plan["segment"]
    log(f"▶ Бисект «{args.op}»: {len(seg)} сборок от {plan['good'].label} до "
        f"{plan['bad'].label}, {args.runs} повторов на заход (до {args.max_runs}); "
        f"установок до {bisect._max_probes(len(seg)) + 3}")
    stop_event = threading.Event()
    result, error, interrupted = run_worker(
        lambda: app.bisect_builds(plan["builds"], plan["good"], plan["bad"], args.op,
                                  plan["test_file"], runs=args.runs, max_runs=args.max_runs,
                                  restore=not args.no_restore, log_cb=log,
                                  stop_event=stop_event),
        stop_event, "r7-cli-bisect")
    if isinstance(error, bisect.BisectError):
        log(f"❌ {error}")
        return EXIT_PRECONDITION
    if error is not None or result is None:
        logfile.get_logger().error("бисект упал", exc_info=error)
        log(f"❌ Бисект упал: {type(error).__name__}: {error}")
        return EXIT_RUN
    ts = time.strftime("%Y%m%d_%H%M%S")
    json_path, html_path = save_bisect_report(app.reports_folder, result, ts)
    print(bisect.format_result(result), flush=True)
    log(f"📄 Отчёт: {html_path} (данные — {json_path.name})")
    if result.extra.get("restore") == bisect_runner.RESTORE_FAILED:
        log(f"❌ {result.extra.get('restore_text')}")
        return EXIT_RUN
    if interrupted:
        return EXIT_RUN
    return BISECT_EXIT.get(result.status, EXIT_RUN)


# ── corpus ───────────────────────────────────────────────────────────────

def corpus_plan(args):
    """План корпуса из аргументов. CorpusError — неверные шаги, форматы, повторы."""
    return corpus.Plan(steps=corpus.parse_steps(args.steps),
                       formats=corpus.parse_formats(args.formats),
                       open_runs=corpus.parse_runs(args.open_runs, "--open-runs"),
                       recalc_runs=corpus.parse_runs(args.recalc_runs, "--recalc-runs"),
                       export_runs=corpus.parse_runs(args.export_runs, "--export-runs"))


def r7_preconditions(app):
    """Р7 закрыт и найден (рабочая фикстура корпусу не нужна)."""
    checks = [firstrun.check_r7_running(app), firstrun.check_r7_found(app)]
    return [f"{c.name}: {c.detail}. {c.fix}".rstrip(". ") + "."
            for c in checks if c.status != firstrun.OK]


def save_corpus_report(folder, report, hide):
    """corpus_<ts>.json и .html. С hide — только обезличенная копия: имён
    файлов клиентов нет ни в одном из двух. Returns: (JSON, HTML, данные)."""
    from r7 import corpus_report
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    data = corpus.hide_names(report) if hide else report
    ts = data.get("timestamp") or time.strftime("%Y%m%d_%H%M%S")
    json_path = folder / f"corpus_{ts}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = folder / f"corpus_{ts}.html"
    html_path.write_text(corpus_report.matrix_page(corpus_report.matrix_model(data)),
                         encoding="utf-8")
    return json_path, html_path, data


def corpus_exit_code(report, interrupted):
    """0 — все файлы и шаги дали цифры; 1 — у части файлов ошибка;
    2 — прогон прерван (Ctrl+C)."""
    from r7 import corpus_report
    if interrupted or report.get("stopped"):
        return EXIT_RUN
    rows = corpus_report.matrix_model(report)["rows"]
    return EXIT_GATE if any(r["bad"] for r in rows) else EXIT_OK


def corpus_items(args):
    """(папка, план, файлы, предупреждения) или (None, …) с выводом причины."""
    folder = Path(args.dir) if args.dir else corpus.corpus_dir()
    if not folder.is_dir():
        log(f"❌ Папки корпуса нет: {folder}. Положите туда обезличенные файлы "
            f"(docs/corpus.md) или укажите --dir")
        return None, None, [], []
    try:
        plan = corpus_plan(args)
    except corpus.CorpusError as e:
        log(f"❌ {e}")
        return None, None, [], []
    items, warnings = corpus.build_items(folder, plan, corpus.load_manifest(folder))
    for w in warnings:
        log(f"⚠️ {w}")
    if not items:
        log(f"❌ В {folder} нет файлов {', '.join(corpus.SUPPORTED_EXTS)} для прогона")
        return None, None, [], warnings
    return folder, plan, items, warnings


def cmd_corpus(args):
    """Прогон корпуса: открытие, пересчёт, экспорт по каждому файлу."""
    from r7 import corpus_report
    from r7.corpus_runner import CorpusRunError
    folder, plan, items, warnings = corpus_items(args)
    if folder is None:
        return EXIT_PRECONDITION
    app = make_headless_app(log, args.out)
    problems = r7_preconditions(app)
    if problems:
        for p in problems:
            log(f"❌ {p}")
        return EXIT_PRECONDITION
    log(f"▶ Корпус {folder}: {len(items)} файлов, шаги {', '.join(plan.steps)}, "
        f"экспорт {', '.join(plan.formats)}; отчёты в {app.reports_folder}")
    stop_event = threading.Event()
    report, error, interrupted = run_worker(
        lambda: app.run_corpus(items, plan, corpus_dir=folder, warnings=warnings, log_cb=log,
                               stop_event=stop_event),
        stop_event, "r7-cli-corpus",
        stop_note="⏹ Ctrl+C — останавливаю после текущего замера, Р7 закроется штатно")
    if isinstance(error, CorpusRunError):
        log(f"❌ {error}")
        return EXIT_PRECONDITION
    if error is not None or report is None:
        logfile.get_logger().error("корпус упал", exc_info=error)
        log(f"❌ Корпус упал: {type(error).__name__}: {error}")
        return EXIT_RUN
    json_path, html_path, data = save_corpus_report(app.reports_folder, report, args.hide_names)
    print(corpus_report.matrix_text(corpus_report.matrix_model(data)), flush=True)
    log(f"📄 Отчёт: {html_path} (данные — {json_path.name})")
    return corpus_exit_code(data, interrupted)


def cmd_corpus_compare(args):
    """Сравнение прогонов корпуса: матрица «файл × версия» с вердиктами.
    0 — регрессий нет, 1 — есть РЕГРЕССИЯ, 3 — отчёты не читаются."""
    from r7 import corpus_report
    if len(args.reports) < 2:
        log("❌ Нужно не меньше двух отчётов corpus_*.json: база и версия")
        return EXIT_PRECONDITION
    try:
        reports = [corpus.load_report(p) for p in args.reports]
    except corpus.CorpusError as e:
        log(f"❌ {e}")
        return EXIT_PRECONDITION
    if args.hide_names:
        reports = [corpus.hide_names(r) for r in reports]
    base_dir = Path(args.reports[0]).parent
    out = Path(args.out) if args.out else base_dir
    profile_dir = Path(args.profile_dir) if args.profile_dir else base_dir
    model = corpus_report.compare_model(reports, noise.noise_for_report(profile_dir, reports[0]))
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / f"corpus_compare_{time.strftime('%Y%m%d_%H%M%S')}.html"
    html_path.write_text(corpus_report.compare_page(model), encoding="utf-8")
    print(corpus_report.compare_text(model), flush=True)
    log(f"📄 Сравнение: {html_path}")
    return EXIT_GATE if corpus_report.has_regression(model) else EXIT_OK


# ── suites, check ────────────────────────────────────────────────────────

def cmd_suites(args):
    cls = app_class()
    app = cls.__new__(cls)          # без окна и без _init_state: нужны только имена тестов
    app.add_test_log = log
    names = suite_names(app)
    files = list_suites(args.dir)
    if not files:
        print(f"наборов нет: в {args.dir or config.BASE_DIR / 'suites'} нет *.toml")
        return EXIT_PRECONDITION
    bad = 0
    for path in files:
        try:
            s = load_suite(path, names)
        except SuiteError as e:
            bad += 1
            print(f"✗ {path.name}: {e}")
            continue
        budgets = f", бюджетов {len(s.budgets)}" if s.budgets else ""
        editor = "" if s.editor == "spreadsheet" else f" [{s.editor}]"
        print(f"{s.name:10} {path.name:16} тестов {len(s.tests):2}, повторов "
              f"{s.total_runs:3}{budgets}{editor} — {s.description}")
    return EXIT_PRECONDITION if bad else EXIT_OK


def cmd_check(args):
    app = make_headless_app(log)
    checks = firstrun.run_checks(app)
    for c in checks:
        print(f"{STATUS_MARK[c.status]} {c.name}: {c.detail}")
        if c.fix and c.status != firstrun.OK:
            print(f"    → {c.fix}")
    failed = firstrun.has_failures(checks)
    print("прогон не пойдёт — исправьте ✗" if failed else "стенд готов к прогону")
    return EXIT_GATE if failed else EXIT_OK


# ── разбор аргументов ────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(prog="python -m r7",
                                description="R7-Testovarka без окна: прогон набора тестов, "
                                            "список наборов, проверка стенда (docs/cli.md)")
    p.add_argument("--no-plugins", action="store_true",
                   help="не загружать тесты из plugins/*.py (docs/plugins.md)")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="прогон набора на установленном Р7 (Р7 должен быть закрыт)")
    run.add_argument("--suite", required=True, help="набор: suites/smoke.toml и т. п.")
    run.add_argument("--out", help="папка отчётов вместо Reports (или reports_folder из настроек)")
    run.add_argument("--baseline", help="эталонный performance_full_*.json для сравнения")
    run.add_argument("--junit", help="куда записать JUnit XML")
    run.add_argument("--gate", action="store_true",
                     help="записать страницу «Релиз готов / Не готов» (gate_<ts>.html)")
    run.add_argument("--trace-regressions", action="store_true",
                     help="после прогона снять трассу и профиль с операций с регрессией "
                          "к эталону (отдельный повтор вне замера, docs/cli.md)")
    run.set_defaults(func=cmd_run)

    tr = sub.add_parser("trace", help="трасса и профиль одной операции (повтор вне замера)")
    tr.add_argument("--op", required=True, help="имя теста, точно как в списке тестов (встроенные и плагины)")
    tr.add_argument("--report", help="performance_full_*.json, в который дописать diagnostics; "
                                     "файлы лягут рядом с ним")
    tr.add_argument("--out", help="папка отчётов вместо Reports (без --report)")
    tr.set_defaults(func=cmd_trace)

    bi = sub.add_parser("bisect", help="первая сборка с регрессией операции: двоичный поиск "
                                       "между двумя дистрибутивами (ставит сборки, нужны "
                                       "права администратора)")
    bi.add_argument("--good", required=True,
                    help="база: имя или путь дистрибутива или номер версии (2026.3.2)")
    bi.add_argument("--bad", required=True, help="сборка, на которой операция медленнее")
    bi.add_argument("--op", required=True, help="имя теста, точно как в списке тестов (встроенные и плагины)")
    bi.add_argument("--runs", type=int, default=bisect.DEFAULT_RUNS,
                    help=f"повторов на заход (по умолчанию {bisect.DEFAULT_RUNS})")
    bi.add_argument("--max-runs", type=int, default=bisect.DEFAULT_MAX_RUNS,
                    help="потолок повторов сборки, пока она «не определено» "
                         f"(по умолчанию {bisect.DEFAULT_MAX_RUNS})")
    bi.add_argument("--dist", help="папка дистрибутивов вместо Distributives")
    bi.add_argument("--no-restore", action="store_true",
                    help="не возвращать исходную версию в конце")
    bi.add_argument("--out", help="папка отчётов вместо Reports")
    bi.set_defaults(func=cmd_bisect)

    co = sub.add_parser("corpus", help="корпус реальных файлов: открытие, пересчёт и экспорт "
                                       "каждого файла из Corpus/ (docs/corpus.md)")
    co.add_argument("--dir", help="папка корпуса вместо Corpus/ рядом с программой")
    co.add_argument("--steps", default=",".join(corpus.DEFAULT_STEPS),
                    help="шаги через запятую: open, recalc, export (по умолчанию все)")
    co.add_argument("--formats", default=",".join(corpus.DEFAULT_FORMATS),
                    help="форматы экспорта через запятую: " + ", ".join(corpus.EXPORT_FORMATS)
                         + f" (по умолчанию {','.join(corpus.DEFAULT_FORMATS)})")
    co.add_argument("--open-runs", type=int, default=corpus.DEFAULT_OPEN_RUNS,
                    help=f"повторов открытия (по умолчанию {corpus.DEFAULT_OPEN_RUNS})")
    co.add_argument("--recalc-runs", type=int, default=corpus.DEFAULT_RECALC_RUNS,
                    help=f"повторов пересчёта (по умолчанию {corpus.DEFAULT_RECALC_RUNS})")
    co.add_argument("--export-runs", type=int, default=corpus.DEFAULT_EXPORT_RUNS,
                    help=f"повторов экспорта (по умолчанию {corpus.DEFAULT_EXPORT_RUNS})")
    co.add_argument("--hide-names", action="store_true",
                    help="в JSON и HTML вместо имён файлов — их id (для передачи наружу)")
    co.add_argument("--out", help="папка отчётов вместо Reports")
    co.set_defaults(func=cmd_corpus)

    cc = sub.add_parser("corpus-compare", help="сравнение прогонов корпуса: матрица "
                                               "«файл × версия», первый отчёт — база")
    cc.add_argument("reports", nargs="+", help="corpus_*.json: база, затем версии")
    cc.add_argument("--hide-names", action="store_true",
                    help="на странице вместо имён файлов — их id")
    cc.add_argument("--out", help="куда записать страницу (по умолчанию — рядом с базой)")
    cc.add_argument("--profile-dir", help="папка с noise_profile.json (по умолчанию — "
                                          "рядом с базой)")
    cc.set_defaults(func=cmd_corpus_compare)

    suites = sub.add_parser("suites", help="список наборов с проверкой")
    suites.add_argument("--dir", help="папка наборов вместо suites/")
    suites.set_defaults(func=cmd_suites)

    check = sub.add_parser("check", help="проверки стенда, как в мастере первого запуска")
    check.set_defaults(func=cmd_check)
    return p


def main(argv=None):
    utf8_console()
    args = build_parser().parse_args(argv)
    logfile.setup_logging(config.BASE_DIR)
    if args.no_plugins:
        plugins.disable_for_process()
    return args.func(args)
