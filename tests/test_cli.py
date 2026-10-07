"""Командная строка (r7/cli.py, python -m r7): разбор аргументов, коды
выхода, JUnit и страница готовности — без Р7, воркер подменён."""
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock

import pytest

import r7_doc_ops
import r7_Testovarka as r7mod
from r7 import cli, config, firstrun, logfile
from r7.cli import EXIT_GATE, EXIT_OK, EXIT_PRECONDITION, EXIT_RUN

ROOT = Path(__file__).resolve().parent.parent
NAMES = r7mod.R7Testovarka.TEST_DEFINITIONS
OPEN, CTRL_A, CTRL_V = NAMES[0], NAMES[1], NAMES[3]
STEADY = [1.00, 1.01, 0.99, 1.02, 1.00, 0.98]
SLOW = [1.50, 1.52, 1.49, 1.51, 1.50, 1.48]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Журнал, suites/ и фикстура — во временной папке; поиск фикстуры —
    только в TestFiles приложения, а не в загрузках стенда."""
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(firstrun, "fixture_search_dirs", lambda folder: [Path(folder)])
    logfile.shutdown_logging()
    yield
    logfile.shutdown_logging()


def _op(name, runs, error=None):
    t = sorted(runs)[len(runs) // 2] if runs else 0.0
    return {"name": name, "time": 0.0 if error else t, "mad": 0.01, "n_runs": len(runs),
            "runs": runs, "run_statuses": ["ok"] * len(runs), "first_run_discarded": False,
            "error": error}


class FakeApp:
    """Голый R7Testovarka для CLI: условия прогона и воркер, который пишет
    performance_full_*.json как настоящий (_write_run_reports)."""
    TEST_DEFINITIONS = NAMES
    editor_test_names = r7mod.R7Testovarka.editor_test_names   # уже связан с классом

    def __init__(self, tmp_path, results=None, r7_running=False, r7_path="E:/R7/x.exe",
                 fixture=True, worker_error=None, write_report=True):
        self.reports_folder = tmp_path / "Reports"
        self.reports_folder.mkdir(exist_ok=True)
        self.test_files_folder = tmp_path / "TestFiles"
        self.test_files_folder.mkdir(exist_ok=True)
        if fixture:
            (self.test_files_folder / "r7-test-50k.xlsx").write_bytes(b"\0" * 2048)
        self.results = results or {}
        self.r7_running, self.r7_path = r7_running, r7_path
        self.worker_error, self.write_report = worker_error, write_report
        self.current_version_info = {"version": "2026.3.3"}
        self._r7_path_searched = ["реестр: записи нет"]
        self.calls = []

    plugin_names: list = []           # имена тестов плагинов (r7.plugins)

    def effective_test_definitions(self):
        return list(self.TEST_DEFINITIONS) + list(self.plugin_names)

    def _get_r7_processes(self, log_cb=None):
        return [Mock(pid=4242)] if self.r7_running else []

    def _find_r7_path(self):
        return self.r7_path

    def _document_worker(self, enabled, runs, stop_event):
        self.calls.append(("document", set(enabled), dict(runs)))
        self._write(enabled, editor="document")

    def _spreadsheet_worker(self, enabled, runs, stop_event):
        self.calls.append((set(enabled), dict(runs)))
        self._write(enabled)

    def _write(self, enabled, editor=None):
        if self.worker_error:
            raise self.worker_error
        if not self.write_report:
            return
        results = [self.results[n] for n in self.results if n in {
            "Открытие файла" if e == OPEN else e for e in enabled}]
        data = {"measure_schema": 9, "version": "2026.3.3", "results": results}
        if editor:
            data["editor"] = editor
        path = self.reports_folder / f"performance_full_{time.strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _suite_file(tmp_path, body=None):
    body = body if body is not None else (
        f'[suite]\nname = "smoke"\ndescription = "дым"\n[tests]\n"{OPEN}" = 3\n"{CTRL_A}" = 6\n')
    p = tmp_path / "smoke.toml"
    p.write_text(body, encoding="utf-8")
    return p


def _install(monkeypatch, app):
    monkeypatch.setattr(cli, "make_headless_app", lambda log_cb=None, reports_folder=None: app)


def _ok_results():
    return {"Открытие файла": _op("Открытие файла", [9.0, 9.2, 9.1]), CTRL_A: _op(CTRL_A, STEADY)}


# ── разбор аргументов ────────────────────────────────────────────────────

def test_parser_run_arguments():
    a = cli.build_parser().parse_args(["run", "--suite", "s.toml", "--out", "o", "--baseline", "b.json",
                                       "--junit", "j.xml", "--gate"])
    assert (a.command, a.suite, a.out, a.baseline, a.junit, a.gate) == \
        ("run", "s.toml", "o", "b.json", "j.xml", True)
    assert cli.build_parser().parse_args(["suites"]).dir is None
    assert cli.build_parser().parse_args(["check"]).func is cli.cmd_check


def test_run_requires_suite():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["run"])


# ── run: условия ─────────────────────────────────────────────────────────

def test_run_refuses_when_r7_running(monkeypatch, tmp_path, capsys):
    app = FakeApp(tmp_path, r7_running=True)
    _install(monkeypatch, app)
    assert cli.main(["run", "--suite", str(_suite_file(tmp_path))]) == EXIT_PRECONDITION
    assert "4242" in capsys.readouterr().out and not app.calls


def test_run_refuses_without_r7_or_fixture(monkeypatch, tmp_path, capsys):
    app = FakeApp(tmp_path, r7_path=None, fixture=False)
    _install(monkeypatch, app)
    assert cli.main(["run", "--suite", str(_suite_file(tmp_path))]) == EXIT_PRECONDITION
    out = capsys.readouterr().out
    assert "реестр: записи нет" in out and "r7-test-50k.xlsx" in out and not app.calls


def test_run_bad_suite_is_precondition(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, FakeApp(tmp_path))
    bad = _suite_file(tmp_path, '[suite]\nname="x"\n[tests]\n"Нет такого" = 3\n')
    assert cli.main(["run", "--suite", str(bad)]) == EXIT_PRECONDITION
    assert "Нет такого" in capsys.readouterr().out
    assert cli.main(["run", "--suite", str(tmp_path / "нет.toml")]) == EXIT_PRECONDITION


def test_run_bad_baseline_is_precondition(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, FakeApp(tmp_path, results=_ok_results()))
    (tmp_path / "b.json").write_text("[1, 2]", encoding="utf-8")
    assert cli.main(["run", "--suite", str(_suite_file(tmp_path)), "--baseline",
                     str(tmp_path / "b.json")]) == EXIT_PRECONDITION
    assert "нет списка results" in capsys.readouterr().out
    assert cli.main(["run", "--suite", str(_suite_file(tmp_path)), "--baseline",
                     str(tmp_path / "нет.json")]) == EXIT_PRECONDITION


# ── run: прогон и вердикт ────────────────────────────────────────────────

def test_run_ok_writes_junit_and_gate(monkeypatch, tmp_path, capsys):
    app = FakeApp(tmp_path, results=_ok_results())
    _install(monkeypatch, app)
    junit = tmp_path / "ci" / "junit.xml"
    code = cli.main(["run", "--suite", str(_suite_file(tmp_path)), "--junit", str(junit), "--gate"])
    assert code == EXIT_OK
    assert app.calls == [({OPEN, CTRL_A}, {OPEN: 3, CTRL_A: 6})]      # набор ушёл в воркер
    out = capsys.readouterr().out
    assert "Набор smoke: Релиз готов" in out and "в норме" in out and "<<" not in out

    root = ET.fromstring(junit.read_text(encoding="utf-8"))
    assert root.get("tests") == str(len(NAMES)) and root.get("failures") == "0"
    assert root.get("skipped") == str(len(NAMES) - 2)
    gate = list(app.reports_folder.glob("gate_*.html"))
    report = list(app.reports_folder.glob("performance_full_*.json"))
    assert len(gate) == 1 and gate[0].stem.removeprefix("gate_") == \
        report[0].stem.removeprefix("performance_full_")
    assert "Релиз готов" in gate[0].read_text(encoding="utf-8")


def test_run_budget_exceeded_exit_1(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, FakeApp(tmp_path, results=_ok_results()))
    suite = _suite_file(tmp_path, f'[suite]\nname="s"\n[tests]\n"{OPEN}" = 3\n'
                                  f'[budgets]\n"{OPEN}" = 5.0\n')
    assert cli.main(["run", "--suite", str(suite)]) == EXIT_GATE
    out = capsys.readouterr().out
    assert "Не готов" in out and "выше бюджета 5 с" in out and "<<" in out


def test_run_regression_against_baseline_exit_1(monkeypatch, tmp_path, capsys):
    app = FakeApp(tmp_path, results={CTRL_A: _op(CTRL_A, SLOW)})
    _install(monkeypatch, app)
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"measure_schema": 9, "version": "2026.3.2",
                                "results": [_op(CTRL_A, STEADY)]}), encoding="utf-8")
    suite = _suite_file(tmp_path, f'[suite]\nname="s"\n[tests]\n"{CTRL_A}" = 6\n')
    junit = tmp_path / "j.xml"
    assert cli.main(["run", "--suite", str(suite), "--baseline", str(base),
                     "--junit", str(junit)]) == EXIT_GATE
    assert "+50.0" in capsys.readouterr().out
    root = ET.fromstring(junit.read_text(encoding="utf-8"))
    assert root.get("failures") == "1" and root.find("testcase/failure").get("type") == "regression"


# ── run: набор документов (этап 5) ───────────────────────────────────────

DOC_NAMES = r7_doc_ops.DOCUMENT_TEST_DEFINITIONS
DOC_ADD = r7_doc_ops.ADD_PAGES_TEST


def _doc_suite(tmp_path):
    return _suite_file(tmp_path, f'[suite]\nname = "docs"\neditor = "document"\n'
                                 f'[tests]\n"{OPEN}" = 3\n"{DOC_ADD}" = 5\n')


def test_run_document_suite_uses_document_worker(monkeypatch, tmp_path, capsys):
    """Набор документа: воркер документа, фикстура xlsx не нужна, JUnit — по
    именам тестов документа."""
    app = FakeApp(tmp_path, fixture=False, results={
        "Открытие файла": _op("Открытие файла", [3.0, 3.1, 3.2]), DOC_ADD: _op(DOC_ADD, STEADY)})
    _install(monkeypatch, app)
    junit = tmp_path / "j.xml"
    assert cli.main(["run", "--suite", str(_doc_suite(tmp_path)), "--junit", str(junit)]) == EXIT_OK
    assert app.calls == [("document", {OPEN, DOC_ADD}, {OPEN: 3, DOC_ADD: 5})]
    root = ET.fromstring(junit.read_text(encoding="utf-8"))
    assert root.get("tests") == str(len(DOC_NAMES))
    assert "Набор docs: Релиз готов" in capsys.readouterr().out


def test_run_document_suite_refuses_spreadsheet_baseline(monkeypatch, tmp_path, capsys):
    app = FakeApp(tmp_path, results=_ok_results())
    _install(monkeypatch, app)
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"measure_schema": 9, "results": [_op(CTRL_A, STEADY)]}),
                    encoding="utf-8")
    assert cli.main(["run", "--suite", str(_doc_suite(tmp_path)), "--baseline",
                     str(base)]) == EXIT_PRECONDITION
    assert "spreadsheet" in capsys.readouterr().out and not app.calls


def test_suites_lists_document_suite(monkeypatch, tmp_path, capsys):
    folder = tmp_path / "suites"
    folder.mkdir()
    (folder / "docs.toml").write_text(_doc_suite(tmp_path).read_text(encoding="utf-8"),
                                      encoding="utf-8")
    assert cli.main(["suites", "--dir", str(folder)]) == EXIT_OK
    assert "[document]" in capsys.readouterr().out


def test_run_not_measured_when_worker_stops_early(monkeypatch, tmp_path):
    _install(monkeypatch, FakeApp(tmp_path, results={"Открытие файла": _op("Открытие файла", [9.0])}))
    junit = tmp_path / "j.xml"
    assert cli.main(["run", "--suite", str(_suite_file(tmp_path)), "--junit", str(junit)]) == EXIT_GATE
    root = ET.fromstring(junit.read_text(encoding="utf-8"))
    assert root.get("errors") == "1" and root.find("testcase/error").get("type") == "not_measured"


def test_run_worker_error_exit_2(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, FakeApp(tmp_path, worker_error=RuntimeError("окно не появилось")))
    assert cli.main(["run", "--suite", str(_suite_file(tmp_path))]) == EXIT_RUN
    out = capsys.readouterr().out
    assert "окно не появилось" in out and "отчёта нет" in out


def test_run_no_report_exit_2(monkeypatch, tmp_path):
    _install(monkeypatch, FakeApp(tmp_path, write_report=False))
    assert cli.main(["run", "--suite", str(_suite_file(tmp_path))]) == EXIT_RUN


def test_run_out_overrides_reports_folder(monkeypatch, tmp_path):
    seen = {}

    def fake_make(log_cb=None, reports_folder=None):
        seen["out"] = reports_folder
        return FakeApp(tmp_path, results=_ok_results())
    monkeypatch.setattr(cli, "make_headless_app", fake_make)
    assert cli.main(["run", "--suite", str(_suite_file(tmp_path)), "--out", "X"]) == EXIT_OK
    assert seen["out"] == "X"


# ── suites, check ────────────────────────────────────────────────────────

def test_suites_lists_and_flags_bad(tmp_path, capsys):
    _suite_file(tmp_path)
    (tmp_path / "bad.toml").write_text('[tests]\n"нет" = 1\n', encoding="utf-8")
    assert cli.main(["suites", "--dir", str(tmp_path)]) == EXIT_PRECONDITION
    out = capsys.readouterr().out
    assert "smoke" in out and "тестов  2" in out and "✗ bad.toml" in out
    (tmp_path / "bad.toml").unlink()
    assert cli.main(["suites", "--dir", str(tmp_path)]) == EXIT_OK


def test_suites_empty_dir(tmp_path, capsys):
    assert cli.main(["suites", "--dir", str(tmp_path / "нет")]) == EXIT_PRECONDITION
    assert "наборов нет" in capsys.readouterr().out


def test_suites_default_dir_is_shipped(capsys, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", ROOT)
    assert cli.main(["suites"]) == EXIT_OK
    out = capsys.readouterr().out
    assert all(n in out for n in ("smoke", "release", "export"))


def test_check_prints_checks_and_exit_codes(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, FakeApp(tmp_path))
    checks = [firstrun.Check("Сборка", firstrun.OK, "всё на месте"),
              firstrun.Check("Р7-Офис", firstrun.FAIL, "не найден", "укажите r7_path")]
    monkeypatch.setattr(cli.firstrun, "run_checks", lambda app: checks)
    assert cli.main(["check"]) == EXIT_GATE
    out = capsys.readouterr().out
    assert "✓ Сборка" in out and "✗ Р7-Офис: не найден" in out and "→ укажите r7_path" in out
    assert "не пойдёт" in out
    monkeypatch.setattr(cli.firstrun, "run_checks", lambda app: checks[:1])
    assert cli.main(["check"]) == EXIT_OK
    assert "готов к прогону" in capsys.readouterr().out


# ── голый экземпляр и вход ───────────────────────────────────────────────

def test_make_headless_app_builds_without_tk(monkeypatch, tmp_path):
    monkeypatch.setattr(r7mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(r7mod.R7Testovarka, "_read_current_version_from_registry",
                        lambda self: {"version": "2026.3.2"})
    lines = []
    app = cli.make_headless_app(lines.append, reports_folder=tmp_path / "out")
    assert isinstance(app, r7mod.R7Testovarka) and not hasattr(app, "root")
    assert app.reports_folder == tmp_path / "out" and app.reports_folder.is_dir()
    assert (tmp_path / "TestFiles").is_dir() and (tmp_path / "Distributives").is_dir()
    assert app.current_version_info == {"version": "2026.3.2"}
    assert app._paced_total == 0.0 and app._webdriver_connector is None
    assert app.perf_stop_event is not None and app._run_state is not None
    app.add_test_log("привет")
    assert lines == ["привет"]
    app._ui_call(lambda: 1 / 0)                      # заглушки не трогают виджеты
    app._set_perf_progress(1, 2)
    app._show_post_test_dialog("x", "y")


def test_init_state_matches_init_attributes():
    """__init__ зовёт _init_state: атрибуты прогона задаются в одном месте."""
    src = Path(r7mod.__file__).read_text(encoding="utf-8")
    init = src[src.index("def __init__(self, root)"):src.index("def _init_state(self)")]
    assert "self._init_state()" in init and "_paced_total" not in init


def test_format_summary_columns():
    from r7.gate import gate_model
    from r7.suites import Suite
    m = gate_model([_op("A", STEADY), _op("B", [], error="упал")],
                   Suite("s", "", {"A": 6, "B": 3}, {"A": 2.0}))
    text = cli.format_summary(m)
    assert text.splitlines()[0] == "Набор s: Не готов"
    assert "1.000" in text and "ошибка  <<" in text and "• B: упал" in text


def test_module_entry_help():
    proc = subprocess.run([sys.executable, "-m", "r7", "--help"], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    assert proc.returncode == 0 and "suites" in proc.stdout and "check" in proc.stdout


def test_log_goes_to_console_and_file(tmp_path, capsys):
    logfile.setup_logging(tmp_path)
    cli.log("❌ беда")
    assert "беда" in capsys.readouterr().out
    text = (tmp_path / "Reports" / "logs" / "r7-testovarka.log").read_text(encoding="utf-8")
    assert "ERROR" in text and "беда" in text


# ── трасса при регрессии (r7/trace.py) ───────────────────────────────────

class TracingApp(FakeApp):
    """FakeApp с дублёром второй сессии Р7: пишет файл трассы как настоящая."""

    def __init__(self, *a, trace_ok=True, **k):
        super().__init__(*a, **k)
        self.traced, self.trace_ok = [], trace_ok

    def trace_ops_session(self, op_names, report_ts, out_dir, stop_event=None):
        self.traced.append((list(op_names), report_ts, Path(out_dir)))
        if not self.trace_ok:
            return {op_names[0]: {"error": "CDP не отдал ни трассу, ни профиль"}}
        out = {}
        for name in op_names:
            fname = f"{report_ts}_op.trace.json"
            (Path(out_dir) / fname).write_text("{}", encoding="utf-8")
            out[name] = {"trace_file": fname, "profile_file": None,
                         "phases": {"scripting_ms": 812.0}, "top_functions": []}
        return out


def _regression_setup(tmp_path):
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"measure_schema": 9, "version": "2026.3.2",
                                "results": [_op(CTRL_A, STEADY)]}), encoding="utf-8")
    suite = _suite_file(tmp_path, f'[suite]\nname="s"\n[tests]\n"{CTRL_A}" = 6\n')
    return base, suite


def test_parser_trace_arguments():
    a = cli.build_parser().parse_args(["run", "--suite", "s.toml", "--trace-regressions"])
    assert a.trace_regressions is True
    assert cli.build_parser().parse_args(["run", "--suite", "s"]).trace_regressions is False
    t = cli.build_parser().parse_args(["trace", "--op", CTRL_A, "--report", "r.json"])
    assert (t.func, t.op, t.report, t.out) == (cli.cmd_trace, CTRL_A, "r.json", None)
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["trace"])


def test_run_trace_regressions_traces_regressed_ops(monkeypatch, tmp_path, capsys):
    app = TracingApp(tmp_path, results={CTRL_A: _op(CTRL_A, SLOW)})
    _install(monkeypatch, app)
    base, suite = _regression_setup(tmp_path)
    code = cli.main(["run", "--suite", str(suite), "--baseline", str(base), "--gate",
                     "--trace-regressions"])
    assert code == EXIT_GATE                          # трасса код выхода не меняет
    report = next(app.reports_folder.glob("performance_full_*.json"))
    ts = report.stem.removeprefix("performance_full_")
    assert app.traced == [([CTRL_A], ts, app.reports_folder)]
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["diagnostics"][CTRL_A]["trace_file"] == f"{ts}_op.trace.json"
    assert data["measure_schema"] == 9
    gate_html = next(app.reports_folder.glob("gate_*.html")).read_text(encoding="utf-8")
    assert f'href="{ts}_op.trace.json"' in gate_html and "скрипты 812 мс" in gate_html


def test_run_trace_skipped_without_regression(monkeypatch, tmp_path, capsys):
    app = TracingApp(tmp_path, results={CTRL_A: _op(CTRL_A, STEADY)})
    _install(monkeypatch, app)
    base, suite = _regression_setup(tmp_path)
    assert cli.main(["run", "--suite", str(suite), "--baseline", str(base),
                     "--trace-regressions"]) == EXIT_OK
    assert app.traced == []
    assert "Трасса не нужна" in capsys.readouterr().out


def test_run_without_flag_or_setting_does_not_trace(monkeypatch, tmp_path):
    app = TracingApp(tmp_path, results={CTRL_A: _op(CTRL_A, SLOW)})
    _install(monkeypatch, app)
    base, suite = _regression_setup(tmp_path)
    assert cli.main(["run", "--suite", str(suite), "--baseline", str(base)]) == EXIT_GATE
    assert app.traced == []


def test_run_setting_trace_on_regression_enables_trace(monkeypatch, tmp_path):
    (tmp_path / "r7_settings.json").write_text('{"trace_on_regression": true}', encoding="utf-8")
    app = TracingApp(tmp_path, results={CTRL_A: _op(CTRL_A, SLOW)})
    _install(monkeypatch, app)
    base, suite = _regression_setup(tmp_path)
    assert cli.main(["run", "--suite", str(suite), "--baseline", str(base)]) == EXIT_GATE
    assert [t[0] for t in app.traced] == [[CTRL_A]]


def test_run_trace_crash_keeps_exit_code(monkeypatch, tmp_path, capsys):
    app = TracingApp(tmp_path, results={CTRL_A: _op(CTRL_A, SLOW)})

    def boom(*a, **k):
        raise RuntimeError("Р7 не запустился")
    app.trace_ops_session = boom
    _install(monkeypatch, app)
    base, suite = _regression_setup(tmp_path)
    assert cli.main(["run", "--suite", str(suite), "--baseline", str(base),
                     "--trace-regressions"]) == EXIT_GATE
    assert "Трасса не снята" in capsys.readouterr().out


def test_trace_command_unknown_or_open_op_is_precondition(monkeypatch, tmp_path):
    app = TracingApp(tmp_path)
    _install(monkeypatch, app)
    assert cli.main(["trace", "--op", "Нет такой"]) == EXIT_PRECONDITION
    assert cli.main(["trace", "--op", OPEN]) == EXIT_PRECONDITION
    assert cli.main(["trace", "--op", CTRL_A, "--report", str(tmp_path / "нет.json")]) == \
        EXIT_PRECONDITION
    assert app.traced == []


def test_trace_command_refuses_when_r7_running(monkeypatch, tmp_path):
    app = TracingApp(tmp_path, r7_running=True)
    _install(monkeypatch, app)
    assert cli.main(["trace", "--op", CTRL_A]) == EXIT_PRECONDITION
    assert app.traced == []


def test_trace_command_writes_diagnostics_file(monkeypatch, tmp_path, capsys):
    app = TracingApp(tmp_path)
    _install(monkeypatch, app)
    assert cli.main(["trace", "--op", CTRL_A]) == EXIT_OK
    ((names, ts, out_dir),) = app.traced
    assert names == [CTRL_A] and out_dir == app.reports_folder
    saved = json.loads((app.reports_folder / f"diagnostics_{ts}.json").read_text(encoding="utf-8"))
    assert saved["diagnostics"][CTRL_A]["phases"]["scripting_ms"] == 812.0
    assert "скрипты 812 мс" in capsys.readouterr().out


def test_trace_command_attaches_to_report(monkeypatch, tmp_path):
    app = TracingApp(tmp_path)
    _install(monkeypatch, app)
    report = tmp_path / "runs" / "performance_full_20261007_120000.json"
    report.parent.mkdir()
    report.write_text(json.dumps({"measure_schema": 10, "results": []}), encoding="utf-8")
    assert cli.main(["trace", "--op", CTRL_A, "--report", str(report)]) == EXIT_OK
    assert app.traced == [([CTRL_A], "20261007_120000", report.parent)]
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["diagnostics"][CTRL_A]["trace_file"] == "20261007_120000_op.trace.json"
    assert (report.parent / "20261007_120000_op.trace.json").is_file()


def test_trace_command_failure_exit_2(monkeypatch, tmp_path, capsys):
    app = TracingApp(tmp_path, trace_ok=False)
    _install(monkeypatch, app)
    assert cli.main(["trace", "--op", CTRL_A]) == EXIT_RUN
    assert "CDP не отдал" in capsys.readouterr().out
