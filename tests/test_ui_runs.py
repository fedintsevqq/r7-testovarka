"""Запуск прогонов из интерфейса (plan-to-10, шаг 3): проверки перед
вкладкой «Производительность» и Batch, окно прогресса Batch (пауза,
остановка, журнал, итог), диалог после теста — на настоящем (скрытом) Tk.
Поток и воркеры — заглушки, Р7 не запускается."""
import threading
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import r7.privileges as privileges
import r7.readiness as rready
import r7.ui.batch as ub
import r7.ui.perf_tab as up
from conftest import patch_ui_name  # noqa: E402

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
    mb = Mock()
    mb.askyesno.return_value = True
    patch_ui_name(monkeypatch, "messagebox", mb)
    patch_ui_name(monkeypatch, "threading", _SyncThread())
    # Права — через r7.privileges.is_admin (одно место для подмены); тесты
    # переключают их через app.admin.IsUserAnAdmin.
    admin = SimpleNamespace(IsUserAnAdmin=lambda: True)
    monkeypatch.setattr(privileges, "is_admin", lambda: bool(admin.IsUserAnAdmin()))
    monkeypatch.setattr(rready, "_missing_cdp_warning", lambda: None)
    for mod in (ub, up):
        monkeypatch.setattr(mod, "pyperclip", object())
    monkeypatch.setattr(r7mod.env, "PYAUTOGUI_OK", True)
    monkeypatch.setattr(r7mod.env, "EXCEL_OK", True)
    monkeypatch.setattr(r7mod.env, "WIN32_OK", True)
    opened = []
    monkeypatch.setattr(ub.webbrowser, "open", opened.append)
    inst = R(root)
    inst.reports_folder = tmp_path / "Reports"
    inst.reports_folder.mkdir()
    inst.distributives_folder = tmp_path / "Distributives"
    inst.distributives_folder.mkdir()
    inst.current_version_info = {"name": "Р7-Офис", "version": "2026.3.2"}
    inst.mb, inst.opened, inst.root_, inst.admin = mb, opened, root, admin
    root.update()
    yield inst
    root.destroy()


def _walk(w):
    for c in w.winfo_children():
        yield c
        yield from _walk(c)


def _button(win, text):
    return next(b for b in _walk(win) if isinstance(b, ttk.Button) and text in str(b.cget("text")))


def _toplevel(app, title):
    return [w for w in app.root_.winfo_children()
            if isinstance(w, tk.Toplevel) and w.title() == title][-1]


# ── Проверки перед прогоном вкладки ──────────────────────────────────────

def test_perf_run_starts_worker_with_selection_snapshot(app):
    got = {}
    app._spreadsheet_worker = lambda enabled, runs, stop: got.update(enabled=enabled, runs=runs)
    app.run_spreadsheet_test()
    app.root_.update()
    assert got["enabled"] and set(got["runs"]) == set(app.test_runs)
    assert str(app.btn_run_perf.cget("state")) == "normal"      # кнопки вернулись после прогона


def test_perf_run_refusals(app, monkeypatch):
    app._spreadsheet_worker = Mock()
    # Без прав администратора прогон вкладки идёт (права нужны только
    # установке версий и сбросу кэша ОС) — отказа «Ошибка прав» больше нет.
    app.admin.IsUserAnAdmin = lambda: False
    app.run_spreadsheet_test()
    app.root_.update()
    assert app._spreadsheet_worker.call_count == 1
    assert not app.mb.showerror.called
    app._spreadsheet_worker.reset_mock()
    app.admin.IsUserAnAdmin = lambda: True
    app.current_version_info = None
    app.run_spreadsheet_test()
    assert app.mb.showwarning.call_args.args[0] == "Нет версии"
    app.current_version_info = {"name": "Р7", "version": "1"}
    monkeypatch.setattr(r7mod.env, "EXCEL_OK", False)
    app.run_spreadsheet_test()
    assert "openpyxl" in app.mb.showerror.call_args.args[1]
    monkeypatch.setattr(r7mod.env, "EXCEL_OK", True)
    for v in app.test_vars.values():
        v.set(False)
    app.run_spreadsheet_test()
    assert app.mb.showwarning.call_args.args[0] == "Нет тестов"
    app._spreadsheet_worker.assert_not_called()


def test_perf_run_cancelled_on_missing_cdp_warning(app, monkeypatch):
    monkeypatch.setattr(rready, "_missing_cdp_warning", lambda: "нет CDP")
    app.mb.askyesno.return_value = False
    app._spreadsheet_worker = Mock()
    app.run_spreadsheet_test()
    app._spreadsheet_worker.assert_not_called()


# ── Диалог после теста ───────────────────────────────────────────────────

def test_post_test_dialog_report_missing_and_new_test(app, tmp_path):
    app._show_post_test_dialog(tmp_path / "нет.html", "1")
    dlg = _toplevel(app, "Тест завершён")
    _button(dlg, "Показать отчёт").invoke()
    assert app.mb.showerror.call_args.args[0] == "Отчёт не сохранён"
    _button(dlg, "Новый тест").invoke()
    assert app.status_var.get() == "Готов"


def test_post_test_dialog_opens_report_and_saves_copy(app, tmp_path, monkeypatch):
    html = tmp_path / "r.html"
    html.write_text("<html></html>", encoding="utf-8")
    opened, copy_to = [], tmp_path / "copy.html"
    monkeypatch.setattr(up.webbrowser, "open", opened.append)
    monkeypatch.setattr(up.filedialog, "asksaveasfilename", lambda **k: str(copy_to))
    app._show_post_test_dialog(html, "1")
    _button(_toplevel(app, "Тест завершён"), "Показать отчёт").invoke()
    assert opened == [str(html)] and copy_to.read_text(encoding="utf-8") == "<html></html>"


# ── Batch: проверки перед диалогом ───────────────────────────────────────

def test_batch_mode_refusals_and_dialog(app, monkeypatch):
    app._show_batch_config_dialog = Mock()
    app.admin.IsUserAnAdmin = lambda: False
    app.run_batch_mode()
    assert app.mb.showerror.call_args.args[0] == "Ошибка прав"
    app.admin.IsUserAnAdmin = lambda: True
    app.run_batch_mode()
    assert app.mb.showwarning.call_args.args[0] == "Нет дистрибутивов"
    monkeypatch.setattr(r7mod.env, "WIN32_OK", False)
    app.run_batch_mode()
    assert "pywin32" in app.mb.showerror.call_args.args[1]
    monkeypatch.setattr(r7mod.env, "WIN32_OK", True)
    (app.distributives_folder / "R7-2026.3.2.msi").write_bytes(b"")
    app.run_batch_mode()
    assert app._show_batch_config_dialog.call_count == 1


# ── Batch: окно прогресса ────────────────────────────────────────────────

@pytest.fixture
def progress(app, tmp_path):
    versions = [tmp_path / "R7-2026.3.1.msi", tmp_path / "R7-2026.3.2.msi"]
    cb = {}

    def worker(vers, test_file, stop_on_error, cleanup, log, set_current, set_ver,
               set_progress, on_done, stop_event, pause_event, aba=False,
               editor="spreadsheet"):
        cb.update(log=log, set_current=set_current, set_ver=set_ver,
                  set_progress=set_progress, on_done=on_done, stop=stop_event,
                  pause=pause_event)
    app._batch_worker = worker
    app._generate_batch_summary_html = lambda results: "<html>итог</html>"
    work = app._open_batch_progress(versions, tmp_path / "f.xlsx", True, False)
    work()
    win = _toplevel(app, "Batch-режим: выполнение")
    return SimpleNamespace(app=app, win=win, cb=cb, versions=versions)


def test_batch_progress_pause_and_stop(progress):
    cb, win = progress.cb, progress.win
    _button(win, "Пауза").invoke()
    assert cb["pause"].is_set() and _button(win, "Продолжить")
    _button(win, "Продолжить").invoke()
    assert not cb["pause"].is_set()
    _button(win, "Пауза").invoke()
    _button(win, "Остановить").invoke()
    progress.app.root_.update()
    assert cb["stop"].is_set() and not cb["pause"].is_set()   # стоп снимает паузу
    assert str(_button(win, "Остановить").cget("state")) == "disabled"


def test_batch_progress_callbacks_update_window(progress):
    cb, app = progress.cb, progress.app
    cb["log"]("установка версии")
    cb["set_current"]("Версия 1 из 2")
    cb["set_ver"](progress.versions[0], "✅ R7-2026.3.1.msi")
    cb["set_progress"](1)
    app.root_.update()
    text = next(w for w in _walk(progress.win) if isinstance(w, tk.Text)).get("1.0", tk.END)
    labels = [w.cget("text") for w in _walk(progress.win) if isinstance(w, ttk.Label)]
    assert "установка версии" in text and "Версия 1 из 2" in labels
    assert any("установка версии" in m for m in app.test_log.get("1.0", tk.END).splitlines())


def test_batch_progress_done_writes_summary(progress):
    cb, app = progress.cb, progress.app
    cb["on_done"]([{"success": True}, {"success": False}], 1)
    app.root_.update()
    pages = list(app.reports_folder.glob("batch_summary_*.html"))
    assert len(pages) == 1 and app.opened == [str(pages[0])]
    assert app.status_var.get() == "Batch завершён: 1/2 успешно"
    assert str(_button(progress.win, "Пауза").cget("state")) == "disabled"


def test_batch_progress_callbacks_after_window_closed(progress):
    progress.win.destroy()
    cb = progress.cb
    cb["log"]("поздно")
    cb["set_current"]("поздно")
    cb["set_ver"](progress.versions[0], "поздно")
    cb["set_progress"](2)
    cb["on_done"]([], 0)                                   # фоновый поток не падает
