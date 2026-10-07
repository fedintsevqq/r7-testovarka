"""Плагины тестов (r7/plugins.py, docs/plugins.md): поиск и проверка файлов
plugins/*.py, действующий список тестов (вкладка, наборы, CLI), путь
плагина через SpreadsheetOps.tests() в воркер, поле plugin в отчёте и
пример docs/plugins/example_bold_column.py на подменённом коннекторе.
"""
import json
import textwrap
import threading
from pathlib import Path

import pytest

import r7_ops
import r7_Testovarka as r7mod
from r7 import cli, config, plugins, settings
from r7.suites import SuiteError, load_suite, parse_suite

R = r7mod.R7Testovarka
EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "docs" / "plugins"
GOOD = '''
def register(ops):
    def op():
        ops.log("op")
    return [("Плагин: операция", ops.make_test(op, lambda: None))]
'''


class FakeApp:
    """Минимум для SpreadsheetOps: шаг клавиш и имена встроенных тестов."""
    OP_KEY_PACE = R.OP_KEY_PACE
    TEST_DEFINITIONS = R.TEST_DEFINITIONS


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    """Своя папка plugins/ вместо пустой из conftest."""
    folder = tmp_path / "plugins"
    folder.mkdir()
    monkeypatch.setattr(plugins, "plugins_dir", lambda: folder)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)       # r7_settings.json — тоже здесь
    plugins.reset_cache()

    return _Folder(folder)


class _Folder:
    """Папка плагинов теста: write(имя, исходник), / как у Path."""
    def __init__(self, path):
        self.path = path

    def write(self, name, src):
        (self.path / name).write_text(textwrap.dedent(src), encoding="utf-8")

    def __truediv__(self, other):
        return self.path / other


def _ops(logs=None):
    return r7_ops.SpreadsheetOps(FakeApp(), find_hwnd=lambda: 1,
                                 log_cb=(logs.append if logs is not None else None),
                                 test_file=None)


def _names(ops):
    return [n for n, _ in ops.tests()]


# ── поиск и проверка ───────────────────────────────────────────────────

def test_good_plugin_goes_after_builtins_with_plugin_mark(pdir):
    pdir.write("good.py", GOOD)
    ops = _ops()
    tests = ops.tests()
    builtin = [n for n, _ in ops._builtin_tests()]
    assert [n for n, _ in tests] == builtin + ["Плагин: операция"]
    fn = tests[-1][1]
    assert fn.plugin == "good.py" and fn.kind == "edit" and fn.mutates is True
    assert callable(fn.prepare)


def test_broken_plugins_are_skipped_with_warning(pdir):
    pdir.write("a_syntax.py", "def register(ops:\n")
    pdir.write("b_raises.py", "raise RuntimeError('нет зависимости')\n")
    pdir.write("c_exit.py", "import sys\nsys.exit(3)\n")
    pdir.write("d_no_register.py", "X = 1\n")
    pdir.write("e_register_raises.py", "def register(ops):\n    raise ValueError('сломан')\n")
    pdir.write("f_not_list.py", "def register(ops):\n    return None\n")
    pdir.write("z_good.py", GOOD)
    logs = []
    assert _names(_ops(logs))[-1] == "Плагин: операция"
    text = "\n".join(logs)
    for f in ("a_syntax.py", "b_raises.py", "c_exit.py", "d_no_register.py",
              "e_register_raises.py", "f_not_list.py"):
        assert f in text
    assert "нет функции register" in text and "нет зависимости" in text


@pytest.mark.parametrize("entry, reason", [
    ('("Выделение всех ячеек (Ctrl+A)", ops.make_test(lambda: None, lambda: None))', "занято"),
    ('("Повторное открытие файла", ops.make_test(lambda: None, lambda: None))', "занято"),
    ('("Без подготовки", lambda: None)', ".prepare"),
    ('("x" * 81, ops.make_test(lambda: None, lambda: None))', "длиннее 80"),
    ('("", ops.make_test(lambda: None, lambda: None))', "непустая"),
    ('("Не функция", 42)', "функция"),
    ('("Вид", ops.make_test(lambda: None, lambda: None, kind="import"))', ".kind"),
    ('("Уборка", ops.make_test(lambda: None, lambda: None, cleanup=5))', ".cleanup"),
    ('("одно имя",)', "не пара"),
])
def test_invalid_entries_are_skipped(pdir, entry, reason):
    pdir.write("p.py", f"def register(ops):\n    return [{entry}]\n")
    logs = []
    ops = _ops(logs)
    assert _names(ops) == [n for n, _ in ops._builtin_tests()]
    assert any(reason in m for m in logs), logs


def test_duplicate_name_across_plugins_keeps_first(pdir):
    pdir.write("a.py", GOOD)
    pdir.write("b.py", GOOD)
    logs = []
    tests = _ops(logs).tests()
    assert [fn.plugin for n, fn in tests if n == "Плагин: операция"] == ["a.py"]
    assert any("b.py" in m and "занято" in m for m in logs)


def test_only_py_files_are_loaded(pdir):
    pdir.write("example.py.txt", GOOD)
    pdir.write("_helper.py", GOOD)
    (pdir / "sub").mkdir()
    (pdir / "sub" / "x.py").write_text(GOOD, encoding="utf-8")
    assert plugins.load_plugins() == []


def test_modules_are_imported_once_per_process(pdir):
    pdir.write("counter.py", "import builtins\n"
                             "builtins.R7_PLUGIN_IMPORTS = getattr(builtins, 'R7_PLUGIN_IMPORTS', 0) + 1\n"
                             + GOOD)
    import builtins
    builtins.R7_PLUGIN_IMPORTS = 0
    try:
        _ops().tests()
        _ops().tests()
        assert builtins.R7_PLUGIN_IMPORTS == 1
    finally:
        del builtins.R7_PLUGIN_IMPORTS


def test_register_gets_this_ops_instance(pdir):
    pdir.write("p.py", '''
        def register(ops):
            return [("Плагин: свой ops", ops.make_test(lambda: ops, lambda: None))]
    ''')
    ops = _ops()
    assert dict(ops.tests())["Плагин: свой ops"]() is ops


def test_disabled_by_setting(pdir):
    pdir.write("good.py", GOOD)
    settings.save_settings({"plugins_enabled": False})
    assert "Плагин: операция" not in _names(_ops())


def test_disabled_by_cli_flag(pdir, monkeypatch):
    pdir.write("good.py", GOOD)
    seen = {}
    monkeypatch.setattr(cli, "cmd_check", lambda args: seen.setdefault("on",
                                                                    plugins.plugins_enabled()) and 0)
    monkeypatch.setattr(cli.logfile, "setup_logging", lambda base: None)
    cli.main(["--no-plugins", "check"])
    assert seen["on"] is False
    assert "Плагин: операция" not in _names(_ops())


def test_export_kind_does_not_expect_document_change(pdir):
    pdir.write("p.py", '''
        def register(ops):
            return [("Плагин: экспорт", ops.make_test(lambda: None, lambda: None, kind="export"))]
    ''')
    fn = dict(_ops().tests())["Плагин: экспорт"]
    assert fn.kind == "export" and fn.mutates is False


# ── действующий список: вкладка, наборы, CLI ───────────────────────────

def _app():
    a = R.__new__(R)
    a.add_test_log = lambda m: None
    return a


def test_effective_list_and_groups(pdir):
    pdir.write("p.py", '''
        def register(ops):
            t = ops.make_test
            return [("Плагин: правка", t(lambda: None, lambda: None)),
                    ("Плагин: экспорт", t(lambda: None, lambda: None, kind="export"))]
    ''')
    a = _app()
    assert a.effective_test_definitions() == R.TEST_DEFINITIONS + ["Плагин: правка",
                                                                    "Плагин: экспорт"]
    groups = dict(a._test_groups())
    assert groups["ОПЕРАЦИИ В ТАБЛИЦЕ"][-1] == "Плагин: правка"
    exports = next(v for k, v in groups.items() if k.startswith("ЭКСПОРТ"))
    assert exports[-1] == "Плагин: экспорт"
    assert a._default_test_entry("Плагин: экспорт")["runs"] == R.DEFAULT_FORMAT_TEST_RUNS
    assert a._default_test_entry("Плагин: правка")["enabled"] is True
    assert a._plugin_test("Плагин: правка").file == "p.py"
    assert a._plugin_test(R.TEST_DEFINITIONS[1]) is None
    assert "Плагин: правка" in a._scenario_edit_tests()
    assert "Плагин: экспорт" not in a._scenario_edit_tests()


def test_without_plugins_effective_list_is_builtin():
    assert _app().effective_test_definitions() == R.TEST_DEFINITIONS


def test_selection_file_keeps_plugin_entry(pdir):
    a = _app()
    (config.BASE_DIR / "selected_tests.json").write_text(json.dumps(
        {"Плагин: правка": {"enabled": False, "runs": 4}}, ensure_ascii=False), encoding="utf-8")
    assert a._load_test_selection()["Плагин: правка"] == {"enabled": False, "runs": 4}


def test_suite_accepts_plugin_name_only_when_loaded(pdir):
    data = {"suite": {"name": "p"}, "tests": {"Плагин: операция": 3}}
    with pytest.raises(SuiteError):
        parse_suite(data, _app().effective_test_definitions())
    pdir.write("good.py", GOOD)
    plugins.reset_cache()                 # новый файл виден после перезапуска программы
    s = parse_suite(data, _app().effective_test_definitions())
    assert s.tests == {"Плагин: операция": 3}


def test_cli_suites_validates_against_plugins(pdir, tmp_path, monkeypatch, capsys):
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "p.toml").write_text('[suite]\nname = "p"\n[tests]\n"Плагин: операция" = 3\n',
                                   encoding="utf-8")
    monkeypatch.setattr(cli, "app_class", lambda: R)
    args = cli.build_parser().parse_args(["suites", "--dir", str(suites)])
    assert cli.cmd_suites(args) == cli.EXIT_PRECONDITION          # плагина нет
    pdir.write("good.py", GOOD)
    plugins.reset_cache()
    assert cli.cmd_suites(args) == cli.EXIT_OK
    assert load_suite(suites / "p.toml", _app().effective_test_definitions()).name == "p"


def test_cli_trace_accepts_plugin_op(pdir, monkeypatch):
    pdir.write("good.py", GOOD)
    app = _app()
    app.reports_folder = config.BASE_DIR / "Reports"
    monkeypatch.setattr(cli, "make_headless_app", lambda *a, **k: app)
    monkeypatch.setattr(cli, "preconditions",
                        lambda a, editor="spreadsheet": ["стоп после проверки имени"])
    args = cli.build_parser().parse_args(["trace", "--op", "Плагин: операция"])
    logs = []
    monkeypatch.setattr(cli, "log", logs.append)
    assert cli.cmd_trace(args) == cli.EXIT_PRECONDITION
    assert not any("Нет такой операции" in m for m in logs)


# ── воркер и отчёт ─────────────────────────────────────────────────────

def test_perf_worker_measures_plugin_op(pdir):
    pdir.write("good.py", GOOD)
    a = _app()
    a.add_test_log = lambda m: None
    a._find_r7_window = lambda stem=None: 1
    a._focus_r7_settled = lambda f: True
    a._set_perf_progress = lambda *x: None
    a._ui_call = lambda fn: None
    measured = []

    def measure(name, func, runs, *args, **kw):
        measured.append((name, getattr(func, "plugin", None), runs))
        return {"name": name}
    a._measure_op_repeated = measure

    class _S:
        def start(self):
            pass
    results = []
    a._run_tab_tests(results, Path("f.xlsx"), {"Плагин: операция"}, {"Плагин: операция": 4},
                     True, threading.Event(), _S())
    assert measured == [("Плагин: операция", "good.py", 4)]


# ── пример docs/plugins/example_bold_column.py ─────────────────────────

class FakeConnector:
    connected = True

    def __init__(self, op_result):
        self.op_result = op_result
        self.js = []

    def sheets_info(self, timeout=None):
        return [{"index": 0, "name": "1", "autofilter": False, "rows": 50001, "cols": 50}]

    def show_sheet(self, target, relative=False, timeout=None):
        return {"ok": True}

    def select_range(self, ref, timeout=None):
        self.selected = ref
        return {"ok": True}

    def evaluate(self, js, timeout=None):
        self.js.append(js)
        return self.op_result


@pytest.fixture
def example(bare_r7, monkeypatch):
    plugins.reset_cache()
    keys = []
    bare_r7._hotkey = lambda *k: keys.append(k)
    bare_r7._press = lambda *k, **kw: keys.append(k)
    bare_r7._op_unverified = None
    logs = []
    ops = r7_ops.SpreadsheetOps(bare_r7, lambda: 1, logs.append, None)
    tests = plugins.collect_tests(ops, R.TEST_DEFINITIONS, folder=EXAMPLE_DIR)
    assert [t.name for t in tests] == ["Жирный шрифт столбца A"]
    return bare_r7, tests[0].fn, keys


def test_example_is_not_loaded_from_plugins_folder():
    assert EXAMPLE_DIR.name == "plugins" and EXAMPLE_DIR.parent.name == "docs"
    assert not (EXAMPLE_DIR.parent.parent / "plugins" / "example_bold_column.py").exists()


def test_example_cdp_ok_sends_no_keys(example):
    app, fn, keys = example
    conn = app._webdriver_connector = FakeConnector(
        {"ok": True, "mutated": True, "api_ms": 3.5, "method": "asc_setCellBold",
         "before": {"historyIndex": 1}, "after": {"historyIndex": 2}})
    fn.prepare()
    assert conn.selected == "A1:A50001"
    fn()
    assert keys == []
    assert "api.asc_setCellBold(true)" in conn.js[-1]
    assert "st.mutated = true" in conn.js[-1]
    assert app._op_via_cdp is True and app._cdp_api_ms == pytest.approx(3.5)
    assert fn.plugin == "example_bold_column.py" and fn.mutates is True


def test_example_cdp_failed_raises_without_keys(example):
    app, fn, keys = example
    app._webdriver_connector = FakeConnector(
        {"ok": False, "mutated": False, "reason": "no-method:asc_setCellBold"})
    with pytest.raises(RuntimeError, match="запасного"):
        fn()
    assert keys == []


def test_example_unknown_result_is_unverified_not_retried(example):
    app, fn, keys = example
    app._webdriver_connector = FakeConnector(None)       # ответа нет, соединение живо
    fn()
    assert keys == [] and app._op_unverified


def test_example_without_cdp_prepare_fails_and_no_keys(example):
    app, fn, keys = example
    app._webdriver_connector = None
    with pytest.raises(RuntimeError, match="CDP"):
        fn.prepare()
    with pytest.raises(RuntimeError):
        fn()
    assert keys == []


# ── публичный API ops ──────────────────────────────────────────────────

def test_api_call_js_rejects_non_identifier():
    from r7_webdriver_connector import api_call_js, op_script_js
    with pytest.raises(ValueError):
        api_call_js("asc_x(); evil")
    with pytest.raises(ValueError):
        op_script_js("st.ok = true; return st;")
    js = api_call_js("asc_setCellBold", (True,), mutates=False)
    assert "st.mutated = true" not in js and "api.asc_setCellBold(true)" in js


def test_cdp_script_uses_same_sequence(bare_r7):
    from r7_webdriver_connector import AFTER_SNAPSHOT_LINE
    bare_r7._op_unverified = None
    conn = bare_r7._webdriver_connector = FakeConnector(
        {"ok": True, "mutated": True, "before": {}, "after": {}})
    ops = r7_ops.SpreadsheetOps(bare_r7, lambda: 1, lambda m: None, None)
    body = "    st.mutated = true;\n    api.asc_x();\n    st.ok = true;\n" + AFTER_SNAPSHOT_LINE \
        + "    return st;\n"
    assert ops.cdp_script("своё", body, check=None) is True
    assert "api.asc_x()" in conn.js[-1]
    with pytest.raises(ValueError):
        ops.cdp_call("x", "asc_x", check="что-то")


def test_pace_goes_through_app(bare_r7):
    calls = []
    bare_r7._pace = calls.append
    r7_ops.SpreadsheetOps(bare_r7, lambda: 1, None, None).pace(0.2)
    assert calls == [0.2]
