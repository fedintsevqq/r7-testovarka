"""Вкладка «Сценарии» (r7/ui/scenarios_tab.py) на настоящем (скрытом) Tk:
карточки собираются, запуск идёт через RunState и зовёт функции
r7.scenarios / r7.crash_recovery с нужными аргументами, итог пишется в
Reports/scenario_<вид>_<время>.json. Сами функции сценариев и запуск Р7
подменены — Р7 не запускается."""
import json
import threading
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import r7.processes as rproc
import r7.ui.scenarios_tab as st
from conftest import patch_ui_name  # noqa: E402
from r7 import crash_recovery, scenarios
from r7.run_state import PERF, SCENARIO

R = r7mod.R7Testovarka


class _SyncThread:
    class Thread:
        def __init__(self, target=None, daemon=None, args=(), kwargs=None):
            self._t, self._a, self._k = target, args, kwargs or {}

        def start(self):
            self._t(*self._a, **self._k)

    def __getattr__(self, name):
        return getattr(threading, name)


@pytest.fixture
def app(monkeypatch, tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    monkeypatch.setattr(R, "_load_test_selection", lambda self: {})
    monkeypatch.setattr(R, "_save_test_selection", lambda self: None)
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    monkeypatch.setattr(R, "refresh_distributives", lambda self: None)
    mb = Mock()
    patch_ui_name(monkeypatch, "messagebox", mb)
    patch_ui_name(monkeypatch, "threading", _SyncThread())
    monkeypatch.setattr(r7mod.env, "WEBDRIVER_OK", True)
    monkeypatch.setattr(rproc, "_running_r7_pids", lambda: set())
    inst = R(root)
    inst.reports_folder = tmp_path / "Reports"
    inst.reports_folder.mkdir()
    inst.test_files_folder = tmp_path / "TestFiles"
    inst.test_files_folder.mkdir()
    inst.current_version_info = {"name": "Р7-Офис", "version": "2026.3.2"}
    inst._find_r7_path = lambda: "r7.exe"
    fixture = tmp_path / "r7-test-50k.xlsx"
    fixture.write_bytes(b"xlsx")
    inst._locate_test_file = lambda: fixture
    inst.mb, inst.root_, inst.fixture = mb, root, fixture
    root.update()
    yield inst
    root.destroy()


def _walk(w):
    for c in w.winfo_children():
        yield c
        yield from _walk(c)


def _run_button(app, kind):
    return app.scenario_run_buttons[kind]


def _set(app, key, value):
    app.scenario_vars[key].set(str(value))


def _json(app, kind):
    files = list(app.reports_folder.glob(f"scenario_{kind}_*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


# ── Вкладка собирается ───────────────────────────────────────────────────

def test_scenarios_tab_is_third_with_three_cards(app):
    tabs = app.notebook.tabs()
    assert len(tabs) == 3 and app.notebook.tab(tabs[2], "text") == " Сценарии "
    assert set(app.scenario_run_buttons) == {st.SOAK, st.MULTIDOC, st.CRASH}
    frames = [w.cget("text") for w in _walk(app.tab_scenarios) if isinstance(w, ttk.LabelFrame)]
    assert frames == ["Долгий прогон (soak)", "Много документов (multidoc)",
                      "Восстановление после падения"]
    assert str(app.btn_stop_scenario.cget("state")) == "disabled"
    ops = app._scenario_edit_tests()
    assert app.OPEN_TEST_NAME not in ops and not (set(ops) & app.EXPORT_TESTS) and len(ops) == 12
    assert app.scenario_vars["soak_op"].get() == "Добавление нового листа"


@pytest.mark.parametrize("raw, expected", [("7", 7), ("", 30), ("abc", 30), ("0", 1), ("99999", 1440)])
def test_scenario_int_clamps(raw, expected):
    var = SimpleNamespace(get=lambda: raw)
    assert R._scenario_int(var, st.SOAK_MINUTES) == expected


# ── Soak ─────────────────────────────────────────────────────────────────

@pytest.fixture
def soak_env(app, monkeypatch):
    """Запуск Р7 и замер подменены: run_soak получает op, который зовёт
    _measure_op_repeated с именем операции."""
    session = SimpleNamespace(find_hwnd=lambda: 42, upd_stop=threading.Event(), data_ready=True)
    opened, closed, measured = [], [], []
    app._scenario_open_r7 = lambda f: opened.append(f) or session
    app._scenario_close_r7 = lambda s: closed.append(s)
    app._measure_op_repeated = lambda name, fn, runs, find, log, stop: (
        measured.append((name, runs, find(), stop)) or {"time": 0.25, "error": None})
    app._new_resource_sampler = lambda: "sampler"
    run_soak = Mock(return_value={"iterations_completed": 3, "elapsed_sec": 90.0,
                                  "stopped_early": False, "control_measurements": [{"value": 1.0}],
                                  "leak": {"verdict": "утечки не обнаружено"}})
    monkeypatch.setattr(scenarios, "run_soak", run_soak)
    return SimpleNamespace(session=session, opened=opened, closed=closed, measured=measured,
                           run_soak=run_soak)


def test_soak_runs_with_arguments_and_writes_json(app, soak_env):
    _set(app, "soak_iterations", 3)
    _set(app, "soak_control", 1)
    app.scenario_vars["soak_op"].set("Выделение всех ячеек (Ctrl+A)")
    _run_button(app, st.SOAK).invoke()
    app.root_.update()

    assert soak_env.opened == [app.fixture] and soak_env.closed == [soak_env.session]
    kw = soak_env.run_soak.call_args.kwargs
    assert kw["iterations"] == 3 and kw["duration_sec"] is None and kw["control_every"] == 1
    assert kw["sampler"] == "sampler" and kw["stop_event"] is app.scenario_stop_event
    assert kw["log_cb"] == app.add_test_log
    op = soak_env.run_soak.call_args.args[0]
    op()
    assert kw["control_op"]() == 0.25
    assert soak_env.measured[0] == ("Выделение всех ячеек (Ctrl+A)", 1, 42, app.scenario_stop_event)
    saved = _json(app, st.SOAK)
    assert saved["kind"] == "soak" and saved["version"] == "2026.3.2"
    assert saved["params"]["op"] == "Выделение всех ячеек (Ctrl+A)"
    assert saved["result"]["iterations_completed"] == 3
    text = app.scenario_result_labels[st.SOAK].cget("text")
    assert text.startswith("Итераций: 3 за 1.5 мин") and "утечки не обнаружено" in text
    assert "мало контрольных замеров" in text
    assert app.run_state.active is None
    assert str(_run_button(app, st.SOAK).cget("state")) == "normal"


def test_soak_by_time_when_iterations_zero(app, soak_env):
    _set(app, "soak_minutes", 2)
    _set(app, "soak_iterations", 0)
    _set(app, "soak_control", 0)
    _run_button(app, st.SOAK).invoke()
    kw = soak_env.run_soak.call_args.kwargs
    assert kw["iterations"] is None and kw["duration_sec"] == 120
    assert kw["control_every"] == 0 and kw["control_op"] is None


def test_soak_stop_button_sets_event_and_crash_has_no_stop(app, soak_env, monkeypatch):
    seen = {}

    def fake_soak(op, **kw):
        seen["stop_state"] = str(app.btn_stop_scenario.cget("state"))
        seen["run_state"] = str(_run_button(app, st.MULTIDOC).cget("state"))
        seen["active"] = app.run_state.active
        app.btn_stop_scenario.invoke()
        seen["event"] = kw["stop_event"].is_set()
        return {"iterations_completed": 1, "elapsed_sec": 1.0, "stopped_early": True,
                "control_measurements": []}
    monkeypatch.setattr(scenarios, "run_soak", fake_soak)
    _run_button(app, st.SOAK).invoke()
    app.root_.update()
    assert seen == {"stop_state": "normal", "run_state": "disabled", "active": SCENARIO,
                    "event": True}
    assert "(остановлено)" in app.scenario_result_labels[st.SOAK].cget("text")
    assert str(app.btn_stop_scenario.cget("state")) == "disabled"

    monkeypatch.setattr(crash_recovery, "run_recovery_check",
                        lambda *a, **k: seen.update(crash_stop=str(app.btn_stop_scenario.cget("state")))
                        or {"report": None, "result": None, "error": "x", "dialog": {},
                            "leftover_pids": [], "elapsed_sec": 1.0})
    _run_button(app, st.CRASH).invoke()
    assert seen["crash_stop"] == "disabled"


def test_soak_failure_is_shown_and_state_released(app, soak_env, monkeypatch):
    monkeypatch.setattr(scenarios, "run_soak", Mock(side_effect=RuntimeError("CDP отвалился")))
    _run_button(app, st.SOAK).invoke()
    app.root_.update()
    assert soak_env.closed == [soak_env.session]                 # Р7 закрыт при любом исходе
    assert "Ошибка: RuntimeError: CDP отвалился" in app.scenario_result_labels[st.SOAK].cget("text")
    assert app.run_state.active is None
    assert "упал" in app.test_log.get("1.0", tk.END)


def test_soak_without_fixture_or_window(app, soak_env):
    app._locate_test_file = lambda: None
    _run_button(app, st.SOAK).invoke()
    app.root_.update()
    assert "тестовый файл не найден" in app.scenario_result_labels[st.SOAK].cget("text")
    soak_env.run_soak.assert_not_called()
    app._locate_test_file = lambda: app.fixture
    app._scenario_open_r7 = lambda f: None
    _run_button(app, st.SOAK).invoke()
    app.root_.update()
    assert "Р7 не открыл файл" in app.scenario_result_labels[st.SOAK].cget("text")
    soak_env.run_soak.assert_not_called()


# ── Multidoc ─────────────────────────────────────────────────────────────

def test_multidoc_generates_fixtures_runs_and_writes_json(app, monkeypatch):
    generated = []
    app._generate_fixture = lambda path, **kw: generated.append((path, kw)) or path.write_bytes(b"x")
    closed = []
    app._scenario_close_multidoc = lambda *a: closed.append(a)
    run_multidoc = Mock(return_value={
        "proc": object(), "opened": ["scenario_doc_1.xlsx", "scenario_doc_2.xlsx"],
        "failed_to_open": [],
        "per_file": {"scenario_doc_1.xlsx": {"ok": True, "result": {"ops_done": 2}, "error": None},
                     "scenario_doc_2.xlsx": {"ok": False, "result": None, "error": "connect() не удался"}}})
    monkeypatch.setattr(scenarios, "run_multidoc", run_multidoc)
    _set(app, "multidoc_files", 2)
    _set(app, "multidoc_ops", 2)
    _run_button(app, st.MULTIDOC).invoke()
    app.root_.update()

    folder = app.test_files_folder / "scenarios"
    assert [p for p, _ in generated] == [folder / "scenario_doc_1.xlsx", folder / "scenario_doc_2.xlsx"]
    assert generated[0][1] == {"rows": 10_000, "profile": "flat", "seed": 101, "cols": 6}
    args, kw = run_multidoc.call_args
    assert args[0] == "r7.exe" and args[1] == [folder / "scenario_doc_1.xlsx", folder / "scenario_doc_2.xlsx"]
    assert kw["log_cb"] == app.add_test_log
    # Операции над документом: новый лист ×N, остановка — между операциями.
    conn = Mock()
    conn.document_state.return_value = {"sheets": 3}
    out = args[2](conn, folder / "scenario_doc_1.xlsx")
    assert conn.add_sheet.call_count == 2 and out["ops_done"] == 2 and out["sheets"] == 3
    app.scenario_stop_event.set()
    conn.reset_mock()
    assert args[2](conn, folder / "scenario_doc_1.xlsx")["ops_done"] == 0
    conn.add_sheet.assert_not_called()
    assert len(closed) == 1
    saved = _json(app, st.MULTIDOC)
    assert "proc" not in saved["result"] and saved["params"]["ops_per_doc"] == 2
    text = app.scenario_result_labels[st.MULTIDOC].cget("text")
    assert text.startswith("Открыто 2 из 2 файлов, операций выполнено 2, ошибок 1")
    assert "connect() не удался" in text
    # Повторный запуск фикстуры не пересоздаёт.
    generated.clear()
    _run_button(app, st.MULTIDOC).invoke()
    assert generated == []


def test_multidoc_closes_r7_even_when_scenario_raises(app, monkeypatch):
    app._generate_fixture = lambda path, **kw: path.write_bytes(b"x")
    closed = []
    app._scenario_close_multidoc = lambda *a: closed.append(a)
    monkeypatch.setattr(scenarios, "run_multidoc", Mock(side_effect=RuntimeError("порт занят")))
    _run_button(app, st.MULTIDOC).invoke()
    app.root_.update()
    assert len(closed) == 1 and app.run_state.active is None
    assert "порт занят" in app.scenario_result_labels[st.MULTIDOC].cget("text")



def test_multidoc_removes_stale_locks_before_launch(app, monkeypatch):
    # Lock-файл от прошлого аварийного закрытия ставил документ на диалог
    # блокировки, и коннектор цеплялся к чужому документу.
    app._generate_fixture = lambda path, **kw: path.write_bytes(b"x")
    app._scenario_close_multidoc = lambda *a: None
    order = []
    app._remove_stale_lock_files = lambda f: order.append(("lock", f.name))
    monkeypatch.setattr(scenarios, "run_multidoc", Mock(
        side_effect=lambda *a, **k: order.append(("run",)) or
        {"proc": None, "opened": [], "failed_to_open": [], "per_file": {}}))
    _set(app, "multidoc_files", 2)
    _run_button(app, st.MULTIDOC).invoke()
    app.root_.update()
    assert order == [("lock", "scenario_doc_1.xlsx"), ("lock", "scenario_doc_2.xlsx"), ("run",)]


def test_multidoc_close_closes_every_window_then_cleans_locks(app, monkeypatch, tmp_path):
    windows = [11, 22]
    closed, locks = [], []
    app._find_r7_window = lambda *a: windows[0] if windows else None
    app._close_r7_gracefully = lambda hwnd, timeout=10: closed.append(hwnd) or windows.remove(hwnd)
    app._r7_gone = lambda *a, **k: True
    app._remove_stale_lock_files = lambda f: locks.append(f.name)
    monkeypatch.setattr(st.processes, "_running_r7_pids", lambda: [])
    files = [tmp_path / "a.xlsx", tmp_path / "b.xlsx"]
    app._scenario_close_multidoc(0.0, files)
    assert closed == [11, 22] and locks == ["a.xlsx", "b.xlsx"]


# ── Crash recovery ───────────────────────────────────────────────────────

def test_crash_runs_recovery_check_and_writes_json(app, monkeypatch):
    report = {"verdict": "Успешно", "scenario": {"recovered_count": 4}, "log": []}
    check = Mock(return_value={"report": report,
                               "result": {"recovered_count": 4, "time_to_reconnect_sec": 0.37},
                               "error": None, "dialog": {"dialog_seen": True, "clicked": True},
                               "leftover_pids": [], "elapsed_sec": 40.0})
    monkeypatch.setattr(crash_recovery, "run_recovery_check", check)
    _set(app, "crash_edits", 4)
    _set(app, "crash_timeout", 20)
    _run_button(app, st.CRASH).invoke()
    app.root_.update()
    args = check.call_args.args
    assert args[0] is app and args[1] == "r7.exe" and args[2] == app.fixture
    assert args[3] == 4 and args[4] == 20
    args[5]("строка сценария")                                   # log_cb пишет в журнал и в log_lines
    assert args[6] == ["строка сценария"]
    assert "строка сценария" in app.test_log.get("1.0", tk.END)
    saved = _json(app, st.CRASH)
    assert saved["result"]["verdict"] == "Успешно" and saved["params"] == {
        "edits": 4, "timeout_sec": 20, "test_file": str(app.fixture)}
    text = app.scenario_result_labels[st.CRASH].cget("text")
    assert text == ("Вердикт: Успешно; восстановлено правок 4/4; диалог восстановления найден "
                    "и нажат; переподключение за 0.4 с")


def test_crash_failure_summary(app, monkeypatch):
    monkeypatch.setattr(crash_recovery, "run_recovery_check", Mock(return_value={
        "report": None, "result": None, "error": "RuntimeError: Р7 уже запущен",
        "dialog": {}, "leftover_pids": [123], "elapsed_sec": 1.0}))
    _run_button(app, st.CRASH).invoke()
    app.root_.update()
    saved = _json(app, st.CRASH)
    assert saved["result"]["verdict"] == "Ошибка" and saved["result"]["leftover_r7_pids"] == [123]
    assert app.scenario_result_labels[st.CRASH].cget("text") == "Ошибка: RuntimeError: Р7 уже запущен"


# ── Проверки перед запуском и взаимное исключение с прогонами ────────────

def test_preflight_refusals(app, monkeypatch):
    run_soak = Mock()
    monkeypatch.setattr(scenarios, "run_soak", run_soak)
    monkeypatch.setattr(rproc, "_running_r7_pids", lambda: {4242})
    _run_button(app, st.SOAK).invoke()
    assert app.mb.showwarning.call_args.args[0] == "Закройте Р7-Офис"
    monkeypatch.setattr(rproc, "_running_r7_pids", lambda: set())
    monkeypatch.setattr(r7mod.env, "WEBDRIVER_OK", False)
    _run_button(app, st.MULTIDOC).invoke()
    assert app.mb.showerror.call_args.args[0] == "Нет доступа к интерфейсу Р7"
    monkeypatch.setattr(r7mod.env, "WEBDRIVER_OK", True)
    app._find_r7_path = lambda: None
    _run_button(app, st.CRASH).invoke()
    assert app.mb.showerror.call_args.args[0] == "Р7-Офис не найден"
    app._find_r7_path = lambda: "r7.exe"
    app.run_state.try_start(PERF)
    _run_button(app, st.SOAK).invoke()
    assert app.mb.showwarning.call_args.args[0] == "Выполняется тест производительности"
    run_soak.assert_not_called()
    assert not list(app.reports_folder.glob("scenario_*.json"))


def test_perf_and_batch_refused_while_scenario_runs(app):
    app._spreadsheet_worker = Mock()
    app._show_batch_config_dialog = Mock()
    app.run_state.try_start(SCENARIO)
    app.run_spreadsheet_test()
    assert app.mb.showwarning.call_args.args[0] == "Выполняется сценарий"
    app.run_batch_mode()
    assert app.mb.showwarning.call_args.args[0] == "Выполняется сценарий"
    app._spreadsheet_worker.assert_not_called()
    app._show_batch_config_dialog.assert_not_called()
