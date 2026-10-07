"""Окна сравнения версий, трендов и генератора файла — на настоящем
(скрытом) Tk: открыть, нажать кнопку, проверить результат на диске."""
import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import r7.ui.compare as cmp_ui
from conftest import patch_ui_name  # noqa: E402
from r7 import config as r7config
from r7.batch_config import auto_fixture_name, fixture_file_name, validate_fixture_dims

R = r7mod.R7Testovarka


def _report(folder, name, version, time_a, mtime):
    import os
    runs = [time_a * (1 + d) for d in (0.0, 0.01, -0.01, 0.02, 0.0, -0.02)]
    data = {"timestamp": "20261007_0" + name[-6:-5] + "0000", "version": version,
            "measure_schema": 9,
            "results": [{"name": "Выделение всех ячеек (Ctrl+A)", "time": time_a, "error": None,
                         "runs": runs, "run_statuses": ["ok"] * 6, "n_runs": 6,
                         "first_run_discarded": False, "mad": 0.01}]}
    p = folder / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


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
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)       # настройки сравнения и генератора
    mb = Mock()
    patch_ui_name(monkeypatch, "messagebox", mb)
    patch_ui_name(monkeypatch, "threading", _SyncThread())
    opened = []
    monkeypatch.setattr(cmp_ui.webbrowser, "open", lambda p: opened.append(p))
    inst = R(root)
    inst.reports_folder = tmp_path / "Reports"
    inst.reports_folder.mkdir()
    inst.test_files_folder = tmp_path / "TestFiles"
    inst.test_files_folder.mkdir()
    inst.mb, inst.opened, inst.root_ = mb, opened, root
    root.update()
    yield inst
    root.destroy()


def _walk(w):
    for c in w.winfo_children():
        yield c
        yield from _walk(c)


def _toplevel(app):
    tops = [w for w in app.root_.winfo_children() if isinstance(w, tk.Toplevel)]
    assert tops, "окно не открылось"
    return tops[-1]


def _button(win, text):
    return next(b for b in _walk(win) if isinstance(b, ttk.Button) and text in str(b.cget("text")))


# ── Сравнить версии ──────────────────────────────────────────────────────

def test_compare_dialog_builds_page_with_saved_base(app):
    a = _report(app.reports_folder, "performance_full_1.json", "v1", 1.0, 1000)
    b = _report(app.reports_folder, "performance_full_2.json", "v2", 2.0, 2000)
    app._save_comparison_settings({"custom_names": {}, "last_selected_files": [str(a), str(b)],
                                   "last_base_version": str(a)})
    app.compare_versions()
    win = _toplevel(app)
    combo = next(w for w in _walk(win) if isinstance(w, ttk.Combobox))
    app.root_.update()
    assert combo.current() >= 0 and "v1" in combo.get()        # база подставлена, поле не пустое
    _button(win, "Сравнить").invoke()
    pages = list(app.reports_folder.glob("comparison_*.html"))
    assert len(pages) == 1 and app.opened == [str(pages[0])]
    html = pages[0].read_text(encoding="utf-8")
    assert "Регрессии: 1" in html
    assert app._load_comparison_settings()["last_base_version"] == str(a)


def test_compare_dialog_refuses_single_selection(app):
    a = _report(app.reports_folder, "performance_full_1.json", "v1", 1.0, 1000)
    _report(app.reports_folder, "performance_full_2.json", "v2", 2.0, 2000)
    app._save_comparison_settings({"custom_names": {}, "last_selected_files": [str(a)],
                                   "last_base_version": str(a)})
    app.compare_versions()
    _button(_toplevel(app), "Сравнить").invoke()
    assert app.mb.showwarning.call_args.args[0] == "Мало файлов"
    assert not list(app.reports_folder.glob("comparison_*.html"))


def test_compare_needs_two_reports(app):
    _report(app.reports_folder, "performance_full_1.json", "v1", 1.0, 1000)
    app.compare_versions()
    assert app.mb.showwarning.called
    assert not [w for w in app.root_.winfo_children() if isinstance(w, tk.Toplevel)]


# ── Пакет улик ───────────────────────────────────────────────────────────

def _open_compare(app, selected):
    app._save_comparison_settings({"custom_names": {}, "last_selected_files": selected,
                                   "last_base_version": selected[0] if selected else ""})
    app.compare_versions()
    win = _toplevel(app)
    app.root_.update()
    return win


def test_evidence_button_enabled_only_with_two_selected(app):
    import r7.ui.compare_dialog as cd
    a = _report(app.reports_folder, "performance_full_1.json", "v1", 1.0, 1000)
    _report(app.reports_folder, "performance_full_2.json", "v2", 2.0, 2000)
    _report(app.reports_folder, "performance_full_3.json", "v3", 2.0, 3000)
    win = _open_compare(app, [str(a)])
    btn = _button(win, "Пакет улик")
    assert str(btn.cget("state")) == "disabled"            # один отмечен
    checks = [w for w in _walk(win) if isinstance(w, ttk.Checkbutton)]
    checks[1].invoke()
    assert str(btn.cget("state")) == "normal"              # два
    checks[2].invoke()
    assert str(btn.cget("state")) == "disabled"            # три
    checks[2].invoke()
    assert str(btn.cget("state")) == "normal"
    # Прямой вызов при неверном выборе — предупреждение, без потока.
    checks[1].invoke()
    cd.CompareDialog.build_evidence(win_dialog(app, win))
    assert app.mb.showwarning.call_args.args[0] == "Пакет улик"
    assert not (app.reports_folder / "evidence").exists()


def win_dialog(app, win):
    """Экземпляр CompareDialog по его окну: compare_versions ссылку не хранит."""
    import gc
    import r7.ui.compare_dialog as cd
    return next(o for o in gc.get_objects() if isinstance(o, cd.CompareDialog) and o.dlg is win)


def test_evidence_button_builds_zip_and_opens_folder(app, monkeypatch):
    import r7.ui.compare_dialog as cd
    a = _report(app.reports_folder, "performance_full_1.json", "v1", 1.0, 1000)
    b = _report(app.reports_folder, "performance_full_2.json", "v2", 2.0, 2000)
    started = []
    monkeypatch.setattr(cd.os, "startfile", started.append)
    win = _open_compare(app, [str(a), str(b)])
    _button(win, "Пакет улик").invoke()
    app.root_.update()                                      # _ui_call → _done в главном потоке
    packs = list((app.reports_folder / "evidence").glob("evidence_*.zip"))
    assert len(packs) == 1
    assert started == [str(packs[0].parent)]
    assert app.mb.showinfo.call_args.args[0] == "Пакет улик"
    assert str(packs[0]) in app.mb.showinfo.call_args.args[1]
    import zipfile
    ticket = zipfile.ZipFile(packs[0]).read("ticket.md").decode("utf-8")
    assert ticket.startswith("# Регрессия Выделение всех ячеек (Ctrl+A): v1 → v2 (+100 %)")
    assert "Пакет улик собран" in app.test_log.get("1.0", tk.END)
    assert str(_button(win, "Пакет улик").cget("state")) == "normal"   # кнопка вернулась


def test_evidence_button_reports_failure(app, monkeypatch):
    import r7.ui.compare_dialog as cd
    a = _report(app.reports_folder, "performance_full_1.json", "v1", 1.0, 1000)
    b = _report(app.reports_folder, "performance_full_2.json", "v2", 2.0, 2000)
    monkeypatch.setattr(cd.evidence, "build_evidence_pack",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("диск полон")))
    win = _open_compare(app, [str(a), str(b)])
    _button(win, "Пакет улик").invoke()
    app.root_.update()
    assert app.mb.showerror.call_args.args[0] == "Пакет улик"
    assert "диск полон" in app.mb.showerror.call_args.args[1]
    assert not (app.reports_folder / "evidence").exists()


# ── Тренды ───────────────────────────────────────────────────────────────

def test_trends_page_written_and_opened(app):
    _report(app.reports_folder, "performance_full_1.json", "v1", 1.0, 1000)
    _report(app.reports_folder, "performance_full_2.json", "v2", 1.1, 2000)
    app.show_trends()
    pages = list(app.reports_folder.glob("trends_*.html"))
    assert len(pages) == 1 and app.opened == [str(pages[0])]


def test_trends_need_two_runs(app):
    app.show_trends()
    assert app.mb.showinfo.call_args.args[0] == "Недостаточно данных"


# ── Генератор тестового файла ────────────────────────────────────────────

def _drive_generator(app, actions):
    """compare_file_sizes ждёт закрытия окна (wait_window) — действия
    выполняются из цикла событий, затем окно закрывается."""
    def go():
        win = _toplevel(app)
        actions(win)
        win.destroy()
    app.root_.after(200, go)
    app.compare_file_sizes()


def _set_entries(win, rows, cols, name):
    entries = [w for w in _walk(win) if isinstance(w, ttk.Entry)]
    for e, val in zip(entries, (rows, cols, name)):
        e.delete(0, tk.END)
        e.insert(0, val)


def test_generator_creates_file(app):
    def act(win):
        _set_entries(win, "1000", "5", "small_fixture")
        _button(win, "Создать файл").invoke()
    _drive_generator(app, act)
    f = app.test_files_folder / "small_fixture.xlsx"
    assert f.exists() and f.stat().st_size > 1000
    assert app._load_last_params()["rows"] == 1000


def test_generator_rejects_bad_input(app):
    def act(win):
        _set_entries(win, "10", "5", "x")
        _button(win, "Создать файл").invoke()
    _drive_generator(app, act)
    assert app.mb.showwarning.call_args.args[1].startswith("Строки")
    assert not list(app.test_files_folder.glob("*.xlsx"))


def test_generator_test_button_refused_while_run_active(app):
    (app.test_files_folder / "f.xlsx").write_bytes(b"x")
    app.run_state.try_start("perf")
    started = []
    app._worker_run_test = lambda *a: started.append(a)

    def act(win):
        _set_entries(win, "1000", "5", "f.xlsx")
        _button(win, "Протестировать").invoke()
    _drive_generator(app, act)
    assert started == [] and app.mb.showwarning.called


# ── Проверки ввода генератора без окна ───────────────────────────────────

@pytest.mark.parametrize("rows, cols, err_field", [
    ("1000", "1", None), ("1000000", "100", None), (" 50000 ", "50", None),
    ("999", "5", "rows"), ("abc", "5", "rows"), ("1000", "0", "cols"), ("1000", "101", "cols"),
])
def test_validate_fixture_dims(rows, cols, err_field):
    r, c, err = validate_fixture_dims(rows, cols)
    assert (err[0] if err else None) == err_field
    if not err:
        assert (r, c) == (int(rows), int(cols))


@pytest.mark.parametrize("name, out, ok", [
    ("data_1", "data_1.xlsx", True), ("a.xlsx", "a.xlsx", True),
    ("файл", None, False), ("a b", None, False), ("", None, False),
])
def test_fixture_file_name(name, out, ok):
    got, err = fixture_file_name(name)
    assert got == out and (err is None) == ok


def test_auto_fixture_name():
    assert auto_fixture_name("50000", 50) == "r7-test-50k.xlsx"      # рабочая фикстура
    assert auto_fixture_name("50000", 49) == "test_data_50000x49.xlsx"
    assert Path(auto_fixture_name(1000, 5)).suffix == ".xlsx"
