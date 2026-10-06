"""Старт Batch-режима: диалог настройки и поток (QA-аудит, G-11 / G-14).

Прежде были покрыты только отказы (Batch во время прогона и наоборот), а
сам старт — нет: он идёт через Tk-диалог настройки. Здесь окно настоящее
(скрытое), поток и воркер — заглушки.
"""
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
from conftest import patch_ui_name  # noqa: E402

R = r7mod.R7Testovarka


class _FakeThread:
    created = []

    def __init__(self, target=None, daemon=None):
        self.target = target
        self.started = False
        _FakeThread.created.append(self)

    def start(self):
        self.started = True


class _ThreadingView:
    """threading глазами модуля: Thread — фейк, остальное настоящее."""
    Thread = _FakeThread

    def __getattr__(self, name):
        return getattr(threading, name)


@pytest.fixture
def app(monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    monkeypatch.setattr(R, "_load_test_selection", lambda self: {})
    monkeypatch.setattr(R, "_save_test_selection", lambda self: None)
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    _FakeThread.created = []
    patch_ui_name(monkeypatch, "threading", _ThreadingView())
    mb = Mock()
    patch_ui_name(monkeypatch, "messagebox", mb)
    inst = R(root)
    inst.mb = mb
    inst.busy = []
    inst._set_busy_indicator = lambda busy, text=None: inst.busy.append(busy)
    root.update()
    yield inst
    root.destroy()


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _dialog(app):
    return [w for w in app.root.winfo_children()
            if isinstance(w, tk.Toplevel) and w.title() == "Batch-режим"][-1]


def _button(dlg, text):
    return next(w for w in _walk(dlg) if isinstance(w, ttk.Button) and w.cget("text") == text)


# ── _start_batch_run: флаг, поток, возврат в покой ───────────────────────

def test_batch_start_runs_worker_and_returns_to_idle(app, tmp_path):
    seen = {}

    def worker(versions, test_file, stop_on_error, cleanup, *callbacks):
        seen.update(versions=versions, test_file=test_file,
                    stop_on_error=stop_on_error, cleanup=cleanup)

    app._batch_worker = worker
    versions = [tmp_path / "R7-2026.3.2.msi"]
    app._start_batch_run(versions, tmp_path / "f.xlsx", True, False)

    assert app._batch_running is True
    assert app.busy == [True]
    assert len(_FakeThread.created) == 1 and _FakeThread.created[0].started

    _FakeThread.created[0].target()
    app.root.update()                         # root.after(0, …) — снять индикатор
    assert seen == {"versions": versions, "test_file": tmp_path / "f.xlsx",
                    "stop_on_error": True, "cleanup": False}
    assert app._batch_running is False
    assert app.busy == [True, False]


def test_batch_worker_exception_still_returns_to_idle(app, tmp_path):
    def worker(*a):
        raise RuntimeError("воркер упал")

    app._batch_worker = worker
    app._start_batch_run([tmp_path / "a.msi"], tmp_path / "f.xlsx", True, False)
    with pytest.raises(RuntimeError):
        _FakeThread.created[0].target()
    app.root.update()
    assert app._batch_running is False and app.busy[-1] is False


def test_perf_refused_after_batch_started(app, tmp_path):
    app._batch_worker = lambda *a: None
    app._start_batch_run([tmp_path / "a.msi"], tmp_path / "f.xlsx", True, False)
    app.run_spreadsheet_test()
    assert app.mb.showwarning.call_args.args[0] == "Выполняется Batch-режим"
    assert len(_FakeThread.created) == 1


# ── Диалог настройки: кнопка «▶ Запустить» ──────────────────────────────

@pytest.fixture
def dialog(app, tmp_path):
    files = [tmp_path / "R7-2026.3.1.msi", tmp_path / "R7-2026.3.2.msi"]
    for f in files:
        f.write_bytes(b"")
    app._start_batch_run = Mock()
    app._show_batch_config_dialog(files)
    app.root.update()
    dlg = _dialog(app)
    entry = next(w for w in _walk(dlg) if isinstance(w, ttk.Entry))
    return {"app": app, "dlg": dlg, "entry": entry, "files": files}


def test_dialog_start_passes_selection_and_file(dialog, tmp_path):
    xlsx = tmp_path / "test.xlsx"
    xlsx.write_bytes(b"x")
    dialog["entry"].delete(0, tk.END)
    dialog["entry"].insert(0, str(xlsx))

    _button(dialog["dlg"], "▶ Запустить").invoke()

    start = dialog["app"]._start_batch_run
    start.assert_called_once()
    versions, test_file, stop_on_error, cleanup = start.call_args.args
    assert versions == dialog["files"] and test_file == Path(str(xlsx))
    assert (stop_on_error, cleanup) == (True, False)


def test_dialog_refuses_missing_test_file(dialog, tmp_path):
    dialog["entry"].delete(0, tk.END)
    dialog["entry"].insert(0, str(tmp_path / "нет.xlsx"))
    _button(dialog["dlg"], "▶ Запустить").invoke()
    dialog["app"]._start_batch_run.assert_not_called()
    assert dialog["app"].mb.showwarning.call_args.args[0] == "Файл не найден"


def test_dialog_refuses_empty_version_selection(dialog, tmp_path):
    xlsx = tmp_path / "test.xlsx"
    xlsx.write_bytes(b"x")
    dialog["entry"].delete(0, tk.END)
    dialog["entry"].insert(0, str(xlsx))
    for cb in (w for w in _walk(dialog["dlg"]) if isinstance(w, ttk.Checkbutton)):
        if cb.cget("text").endswith(".msi") and cb.instate(["selected"]):
            cb.invoke()                     # снять галочку версии
    _button(dialog["dlg"], "▶ Запустить").invoke()
    dialog["app"]._start_batch_run.assert_not_called()
    assert dialog["app"].mb.showwarning.call_args.args[0] == "Нет выбора"
