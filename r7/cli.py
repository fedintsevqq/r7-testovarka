"""Командная строка: `python -m r7 run | suites | check` (docs/cli.md).

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

from r7 import config, firstrun, logfile
from r7.gate import gate_model, gate_page, junit_xml
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

def preconditions(app):
    """Что мешает прогону: Р7 запущен, Р7 не найден, нет фикстуры.
    Список строк «проблема. Что сделать»; пустой — можно запускать."""
    checks = [firstrun.check_r7_running(app), firstrun.check_r7_found(app),
              firstrun.check_fixture(firstrun.fixture_search_dirs(app.test_files_folder))]
    return [f"{c.name}: {c.detail}. {c.fix}".rstrip(". ") + "."
            for c in checks if c.status != firstrun.OK]


def run_suite(app, suite, stop_event=None):
    """Прогон набора воркером вкладки. Возвращает путь к новому
    performance_full_*.json или None (воркер упал или отчёт не записан)."""
    stop_event = stop_event or threading.Event()
    before = set(app.reports_folder.glob("performance_full_*.json"))
    failures = []

    def worker():
        try:
            app._spreadsheet_worker(set(suite.tests), dict(suite.tests), stop_event)
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
    return "\n".join(lines)


def cmd_run(args):
    app = make_headless_app(log, args.out)
    try:
        suite = load_suite(args.suite, app.TEST_DEFINITIONS)
        baseline = load_baseline(args.baseline) if args.baseline else None
    except SuiteError as e:
        log(f"❌ {e}")
        return EXIT_PRECONDITION
    problems = preconditions(app)
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
                       baseline_name=Path(args.baseline).name if args.baseline else None)
    print(format_summary(model), flush=True)
    if args.junit:
        junit_path = Path(args.junit)
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        junit_path.write_text(junit_xml(model, app.TEST_DEFINITIONS), encoding="utf-8")
        log(f"📄 JUnit: {junit_path}")
    if args.gate:
        gate_path = app.reports_folder / f"gate_{ts}.html"
        gate_path.write_text(gate_page(model), encoding="utf-8")
        log(f"📄 Страница готовности: {gate_path}")
    return EXIT_OK if model["ready"] else EXIT_GATE


# ── suites, check ────────────────────────────────────────────────────────

def cmd_suites(args):
    names = app_class().TEST_DEFINITIONS
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
        print(f"{s.name:10} {path.name:16} тестов {len(s.tests):2}, повторов "
              f"{s.total_runs:3}{budgets} — {s.description}")
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
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="прогон набора на установленном Р7 (Р7 должен быть закрыт)")
    run.add_argument("--suite", required=True, help="набор: suites/smoke.toml и т. п.")
    run.add_argument("--out", help="папка отчётов вместо Reports (или reports_folder из настроек)")
    run.add_argument("--baseline", help="эталонный performance_full_*.json для сравнения")
    run.add_argument("--junit", help="куда записать JUnit XML")
    run.add_argument("--gate", action="store_true",
                     help="записать страницу «Релиз готов / Не готов» (gate_<ts>.html)")
    run.set_defaults(func=cmd_run)

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
    return args.func(args)
