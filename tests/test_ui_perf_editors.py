"""Переключатель редактора на вкладке «Производительность»: таблица,
документ, презентация.

Формат selected_tests.json проверяется без Tk (r7/test_selection.py и
методы ResultsMixin на «голом» экземпляре). Пересборка списка, сохранение
выбора по редакторам, запуск нужного воркера и раскладка при минимальном
окне — на настоящем скрытом окне; воркеры и поток — заглушки, Р7 не
запускается.
"""
import json
import threading
import tkinter as tk
from tkinter import ttk
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import r7.config as r7config
import r7.privileges as privileges
import r7.readiness as rready
import r7.ui.perf_tab as up
from conftest import SAVED_PERF_EDITOR, patch_ui_name  # noqa: E402
from r7 import test_selection
from r7.doc_fixtures import DOC_FIXTURE_NAME
from r7.editors import EDITOR_LABELS, EDITOR_WORKERS
from r7.run_state import PERF
from r7_doc_ops import DEFAULT_DOC_RUNS, DOCUMENT_TEST_DEFINITIONS
from r7_pptx_ops import DEFAULT_PPTX_RUNS, PRESENTATION_TEST_DEFINITIONS

R = r7mod.R7Testovarka
DEFAULT_RUNS = r7config.DEFAULT_TEST_RUNS


# ── Формат selected_tests.json без Tk ──────────────────────────────────────

@pytest.mark.parametrize("raw, editor, sections", [
    ({"Ctrl+A": True}, "spreadsheet",                                   # {тест: bool}
     {"spreadsheet": {"Ctrl+A": {"enabled": True, "runs": DEFAULT_RUNS}}}),
    ({"Ctrl+A": {"enabled": False, "runs": 3}}, "spreadsheet",          # плоский
     {"spreadsheet": {"Ctrl+A": {"enabled": False, "runs": 3}}}),
    ({"_editor": "document", "document": {"Поиск и замена": {"enabled": False, "runs": 2}},
      "spreadsheet": {"Ctrl+A": True}}, "document",                     # по редакторам
     {"document": {"Поиск и замена": {"enabled": False, "runs": 2}},
      "spreadsheet": {"Ctrl+A": {"enabled": True, "runs": DEFAULT_RUNS}}}),
    ({"presentation": {"x": {"runs": "abc"}}}, "spreadsheet",           # без _editor, мусор
     {"presentation": {"x": {"enabled": True, "runs": DEFAULT_RUNS}}}),
    ({"_editor": "visio", "document": ["не", "словарь"]}, "spreadsheet", {}),
    (["не", "словарь"], "spreadsheet", {}),
    (None, "spreadsheet", {}),
])
def test_parse_selection_formats(raw, editor, sections):
    parsed = test_selection.parse_selection(raw)
    assert parsed["editor"] == editor
    expected = {"spreadsheet": {}, "document": {}, "presentation": {}, **sections}
    assert parsed["sections"] == expected


def test_build_selection_does_not_share_entries():
    entry = {"enabled": True, "runs": 4}
    data = test_selection.build_selection({"document": {"t": entry}}, "document")
    assert data == {"_editor": "document", "spreadsheet": {},
                    "document": {"t": {"enabled": True, "runs": 4}}, "presentation": {}}
    data["document"]["t"]["runs"] = 9
    assert entry["runs"] == 4
    assert test_selection.build_selection({}, "visio")["_editor"] == "spreadsheet"


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def test_save_keeps_other_editors_and_old_flat_file(bare_r7, tmp_path, monkeypatch):
    """Старый плоский файл — выбор таблиц; сохранение выбора документа его
    не теряет, а переводит в формат по редакторам."""
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(R, "_saved_perf_editor", SAVED_PERF_EDITOR)
    path = tmp_path / "selected_tests.json"
    path.write_text(json.dumps({"Ctrl+A": {"enabled": False, "runs": 3}}), encoding="utf-8")
    assert bare_r7._load_test_selection() == {"Ctrl+A": {"enabled": False, "runs": 3}}

    bare_r7._perf_editor = "document"
    assert bare_r7._load_test_selection() == {}
    bare_r7.test_vars = {"Поиск и замена": _Var(False)}
    bare_r7.test_runs = {"Поиск и замена": _Var(2)}
    bare_r7.add_test_log = Mock()
    bare_r7._save_test_selection()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["_editor"] == "document"
    assert data["spreadsheet"] == {"Ctrl+A": {"enabled": False, "runs": 3}}
    assert data["document"] == {"Поиск и замена": {"enabled": False, "runs": 2}}
    assert bare_r7._load_test_selection() == {"Поиск и замена": {"enabled": False, "runs": 2}}
    assert bare_r7._saved_perf_editor() == "document"
    bare_r7._perf_editor = "spreadsheet"
    assert bare_r7._load_test_selection() == {"Ctrl+A": {"enabled": False, "runs": 3}}
    bare_r7.add_test_log.assert_not_called()


def test_saved_editor_from_broken_file_is_spreadsheet(bare_r7, tmp_path, monkeypatch):
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(R, "_saved_perf_editor", SAVED_PERF_EDITOR)
    (tmp_path / "selected_tests.json").write_text("{битый json", encoding="utf-8")
    assert bare_r7._saved_perf_editor() == "spreadsheet"
    assert bare_r7._load_test_selection() == {}


@pytest.mark.parametrize("editor, names, runs", [
    ("document", DOCUMENT_TEST_DEFINITIONS, DEFAULT_DOC_RUNS),
    ("presentation", PRESENTATION_TEST_DEFINITIONS, DEFAULT_PPTX_RUNS),
])
def test_groups_and_defaults_per_editor(bare_r7, editor, names, runs):
    bare_r7._perf_editor = editor
    groups = bare_r7._test_groups()
    assert [n for _t, g in groups for n in g] == list(names)
    titles = [t for t, _g in groups]
    assert titles[0] == "ОТКРЫТИЕ ФАЙЛА" and "ЭКСПОРТ" in titles[-1]
    exports = dict(groups)[titles[-1]]
    assert exports and all(n.startswith("Сохранение в") for n in exports)
    for n in names:
        assert bare_r7._default_test_entry(n) == {"enabled": True, "runs": runs[n]}


# ── Настоящее окно ──────────────────────────────────────────────────────────

class _ManualThread:
    """threading для _start_run: поток не стартует сам, тест зовёт run()."""
    created: list = []

    class Thread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            _ManualThread.created.append(self)

        def start(self):
            pass

        def run(self):
            self.target()

    def __getattr__(self, name):
        return getattr(threading, name)


@pytest.fixture
def app(monkeypatch, tmp_path):
    """Окно с настоящим чтением и записью selected_tests.json — в tmp_path."""
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    mb = Mock()
    mb.askyesno.return_value = True
    patch_ui_name(monkeypatch, "messagebox", mb)
    _ManualThread.created = []
    patch_ui_name(monkeypatch, "threading", _ManualThread())
    monkeypatch.setattr(privileges, "is_admin", lambda: True)
    monkeypatch.setattr(rready, "_missing_cdp_warning", lambda: None)
    monkeypatch.setattr(up, "pyperclip", object())
    for flag in ("PYAUTOGUI_OK", "EXCEL_OK", "WIN32_OK"):
        monkeypatch.setattr(r7mod.env, flag, True)
    calls = []
    for editor, method in EDITOR_WORKERS.items():
        monkeypatch.setattr(R, method, lambda self, enabled, runs, stop, _e=editor:
                            calls.append((_e, set(enabled), dict(runs), stop)))
    inst = R(root)
    inst.test_files_folder = tmp_path / "TestFiles"
    inst.current_version_info = {"name": "Р7-Офис", "version": "2026.3.2"}
    inst.calls, inst.mb = calls, mb
    root.update()
    yield inst
    root.destroy()


def _select(app, editor):
    rb = app._editor_radios[list(EDITOR_LABELS).index(editor)]
    rb.invoke()
    app.root.update()


def _labels(w):
    for c in w.winfo_children():
        if isinstance(c, ttk.Label):
            yield str(c.cget("text"))
        yield from _labels(c)


def test_selector_has_three_editors_and_starts_on_spreadsheet(app):
    assert [str(rb.cget("text")) for rb in app._editor_radios] == list(EDITOR_LABELS.values())
    assert app.perf_editor_var.get() == "spreadsheet"
    assert list(app.test_vars) == list(R.TEST_DEFINITIONS)


def test_switching_editor_rebuilds_list_with_its_defaults(app):
    _select(app, "document")
    assert app._perf_editor == "document"
    assert list(app.test_vars) == list(DOCUMENT_TEST_DEFINITIONS)
    assert {n: v.get() for n, v in app.test_runs.items()} == DEFAULT_DOC_RUNS
    assert len(app._runs_controls) == len(DOCUMENT_TEST_DEFINITIONS)
    texts = set(_labels(app.tab_perf))
    assert "ОПЕРАЦИИ В ДОКУМЕНТЕ" in texts and "ОПЕРАЦИИ В ТАБЛИЦЕ" not in texts
    assert f"Отмечено {len(DOCUMENT_TEST_DEFINITIONS)} из {len(DOCUMENT_TEST_DEFINITIONS)}" \
        in app.lbl_tests_summary.cget("text")

    _select(app, "presentation")
    assert list(app.test_vars) == list(PRESENTATION_TEST_DEFINITIONS)
    assert {n: v.get() for n, v in app.test_runs.items()} == DEFAULT_PPTX_RUNS

    _select(app, "spreadsheet")
    assert list(app.test_vars) == list(R.TEST_DEFINITIONS)


def test_selection_persists_per_editor(app, tmp_path, monkeypatch):
    """Выбор каждого редактора свой: смена редактора и перезапуск окна его
    не теряют; файл — в формате по редакторам, последний редактор запомнен."""
    app.test_vars["Функция ВПР (50K строк)"].set(False)
    _select(app, "document")
    app.test_vars[DOCUMENT_TEST_DEFINITIONS[1]].set(False)
    app.test_runs[DOCUMENT_TEST_DEFINITIONS[1]].set(9)
    _select(app, "spreadsheet")
    assert app.test_vars["Функция ВПР (50K строк)"].get() is False
    _select(app, "document")
    assert app.test_vars[DOCUMENT_TEST_DEFINITIONS[1]].get() is False
    assert app.test_runs[DOCUMENT_TEST_DEFINITIONS[1]].get() == 9

    data = json.loads((tmp_path / "selected_tests.json").read_text(encoding="utf-8"))
    assert data["_editor"] == "document"
    assert data["spreadsheet"]["Функция ВПР (50K строк)"]["enabled"] is False
    assert data["document"][DOCUMENT_TEST_DEFINITIONS[1]] == {"enabled": False, "runs": 9}

    # Новое окно открывается на документе с тем же выбором.
    monkeypatch.setattr(R, "_saved_perf_editor", SAVED_PERF_EDITOR)
    root2 = tk.Tk()
    root2.withdraw()
    try:
        app2 = R(root2)
        root2.update()
        assert app2.perf_editor_var.get() == "document"
        assert list(app2.test_vars) == list(DOCUMENT_TEST_DEFINITIONS)
        assert app2.test_runs[DOCUMENT_TEST_DEFINITIONS[1]].get() == 9
    finally:
        root2.destroy()


def test_old_flat_file_opens_as_spreadsheet(monkeypatch, tmp_path):
    (tmp_path / "selected_tests.json").write_text(json.dumps(
        {"Функция ВПР (50K строк)": False}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(R, "_saved_perf_editor", SAVED_PERF_EDITOR)
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    try:
        inst = R(root)
        root.update()
        assert inst.perf_editor_var.get() == "spreadsheet"
        assert inst.test_vars["Функция ВПР (50K строк)"].get() is False
    finally:
        root.destroy()


@pytest.mark.parametrize("editor, names", [
    ("spreadsheet", R.TEST_DEFINITIONS),
    ("document", DOCUMENT_TEST_DEFINITIONS),
    ("presentation", PRESENTATION_TEST_DEFINITIONS),
])
def test_run_dispatches_to_editor_worker(app, editor, names):
    _select(app, editor)
    app.test_vars[names[1]].set(False)
    app.test_runs[names[2]].set(4)
    expected = {n for n, v in app.test_vars.items() if v.get()}
    assert names[1] not in expected and expected <= set(names)
    threads_before = len(_ManualThread.created)
    app.run_spreadsheet_test()

    # Прогон идёт: «Запустить» и переключатель недоступны.
    assert app._perf_running
    assert str(app.btn_run_perf.cget("state")) == tk.DISABLED
    assert all(str(rb.cget("state")) == tk.DISABLED for rb in app._editor_radios)
    assert len(_ManualThread.created) == threads_before + 1
    _ManualThread.created[-1].run()
    app.root.update()

    assert len(app.calls) == 1
    called, enabled, runs, stop = app.calls[0]
    assert called == editor
    assert enabled == expected
    assert runs[names[2]] == 4
    assert stop is app.perf_stop_event
    assert not app._perf_running
    assert all(str(rb.cget("state")) == tk.NORMAL for rb in app._editor_radios)
    assert str(app.btn_run_perf.cget("state")) == tk.NORMAL


def test_editor_switch_ignored_while_running(app):
    app.run_state.try_start(PERF)
    try:
        app.perf_editor_var.set("document")
        app._on_perf_editor_selected()
        assert app._perf_editor == "spreadsheet"
        assert app.perf_editor_var.get() == "spreadsheet"
        assert list(app.test_vars) == list(R.TEST_DEFINITIONS)
    finally:
        app.run_state.finish(PERF)


def test_fixture_hint_per_editor(app, tmp_path):
    _select(app, "document")
    hint = app.lbl_fixture_hint.cget("text")
    assert DOC_FIXTURE_NAME in hint and "создастся сам" in hint
    folder = tmp_path / "TestFiles"
    folder.mkdir()
    (folder / DOC_FIXTURE_NAME).write_bytes(b"PK")
    _select(app, "presentation")
    _select(app, "document")
    hint = app.lbl_fixture_hint.cget("text")
    assert DOC_FIXTURE_NAME in hint and "создастся" not in hint
    _select(app, "presentation")
    assert "r7-test-slides-" in app.lbl_fixture_hint.cget("text")


def test_selector_and_run_button_visible_at_minimum_size(app):
    root = app.root
    root.deiconify()
    root.state("normal")
    root.geometry(f"{R.MIN_WIN_W}x{R.MIN_WIN_H}")
    app.notebook.select(app.tab_perf)
    for editor in EDITOR_LABELS:
        _select(app, editor)
        for _ in range(3):
            root.update()
        for w in (*app._editor_radios, app.btn_run_perf, app.btn_stop_perf):
            assert w.winfo_viewable()
            assert w.winfo_rooty() + w.winfo_height() - root.winfo_rooty() <= root.winfo_height()
            assert w.winfo_rootx() + w.winfo_width() - root.winfo_rootx() <= root.winfo_width()


def test_plugin_tests_only_in_spreadsheet_mode(tmp_path, monkeypatch):
    from r7 import plugins
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / "p.py").write_text(
        "def register(ops):\n"
        "    return [('Плагин: правка', ops.make_test(lambda: None, lambda: None))]\n",
        encoding="utf-8")
    monkeypatch.setattr(plugins, "plugins_dir", lambda: folder)
    plugins.reset_cache()
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    try:
        inst = R(root)
        root.update()
        assert "Плагин: правка" in inst.test_vars
        for editor in ("document", "presentation"):
            _select(inst, editor)
            assert "Плагин: правка" not in inst.test_vars
            assert not any("плагин" in t for t in _labels(inst.tab_perf))
        _select(inst, "spreadsheet")
        assert "Плагин: правка" in inst.test_vars
    finally:
        root.destroy()
