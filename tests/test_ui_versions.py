"""Вкладка «Версии» на настоящем (скрытом) Tk: дистрибутивы, установка и
удаление, проверка хэшей. Установщик и файловая система подменены в модуле
вкладки — тесты ничего не ставят и не удаляют."""
import subprocess
import threading
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import r7.ui.versions_tab as vt
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


class _Proc:
    def __init__(self, code=0, hang=False):
        self.returncode, self.hang, self.killed = code, hang, False

    def wait(self, timeout=None):
        if self.hang:
            raise subprocess.TimeoutExpired("msiexec", timeout)

    def kill(self):
        self.killed = True


@pytest.fixture
def app(monkeypatch, tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    monkeypatch.setattr(R, "_load_test_selection", lambda self: {})
    monkeypatch.setattr(R, "_save_test_selection", lambda self: None)
    monkeypatch.setattr(R, "_read_current_version_from_registry", lambda self: None)
    mb = Mock()
    patch_ui_name(monkeypatch, "messagebox", mb)
    patch_ui_name(monkeypatch, "threading", _SyncThread())
    procs, removed = [], []
    # Только в модуле вкладки: установщик, задержки и удаление каталогов.
    monkeypatch.setattr(vt, "subprocess", SimpleNamespace(
        Popen=lambda cmd, shell=False: procs.append(cmd) or app_state["proc"],
        TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(vt, "time", SimpleNamespace(sleep=lambda s: None))
    monkeypatch.setattr(vt, "os", SimpleNamespace(path=SimpleNamespace(exists=lambda p: True),
                                                  startfile=lambda p: None))
    monkeypatch.setattr(vt, "shutil", SimpleNamespace(rmtree=lambda p, ignore_errors=False:
                                                      removed.append(p),
                                                      copy2=lambda a, b: None))
    app_state = {"proc": _Proc(0)}
    inst = R(root)
    inst.distributives_folder = tmp_path / "Distributives"
    inst.distributives_folder.mkdir()
    inst.mb, inst.procs, inst.removed, inst.state_, inst.root_ = mb, procs, removed, app_state, root
    root.update()
    yield inst
    root.destroy()


def _dist(app, *names):
    for n in names:
        (app.distributives_folder / n).write_bytes(b"x" * 1000)
    app.refresh_distributives()


# ── список дистрибутивов ─────────────────────────────────────────────────

def test_refresh_lists_installers_with_versions(app):
    _dist(app, "R7-2026.3.2.msi", "R7-2026.3.1.exe", "readme.txt")
    rows = [app.tree.item(i, "values") for i in app.tree.get_children()]
    assert {r[0] for r in rows} == {"R7-2026.3.2.msi", "R7-2026.3.1.exe"}
    assert {r[1] for r in rows} == {"v2026.3.2", "v2026.3.1"}


def test_empty_folder_disables_install(app):
    app.refresh_distributives()
    assert str(app.btn_install["state"]) == "disabled"
    assert "не найдены" in app.status_var.get()


def test_selecting_row_enables_install(app):
    _dist(app, "R7-2026.3.2.msi")
    app.tree.selection_set(app.tree.get_children()[0])
    app.on_select_distributive(None)
    assert app.selected_distributive["name"] == "R7-2026.3.2.msi"
    assert str(app.btn_install["state"]) == "normal"


# ── установка ────────────────────────────────────────────────────────────

def _select(app, name="R7-2026.3.2.msi"):
    _dist(app, name)
    app.tree.selection_set(app.tree.get_children()[0])
    app.on_select_distributive(None)


def test_install_selected_runs_msiexec_and_reports_done(app):
    _select(app)
    app.install_selected()
    app.root_.update()
    assert app.procs[-1][:2] == ["msiexec", "/i"] and app.procs[-1][-1] == "/quiet"
    assert app.mb.showinfo.call_args.args == ("Готово", "Установка завершена")
    assert app.run_state.active is None
    # Список перечитан, выделения нет — «Установить» ждёт нового выбора.
    assert not app.tree.selection() and str(app.btn_install["state"]) == "disabled"


def test_install_error_code_reported_and_button_restored(app):
    _select(app)
    app.state_["proc"] = _Proc(1603)
    app.install_selected()
    app.root_.update()
    assert "не завершилась успешно" in app.mb.showerror.call_args.args[1]
    assert app.run_state.active is None


def test_install_refused_while_perf_run(app):
    _select(app)
    app.run_state.try_start("perf")
    app.install_selected()
    assert app.procs == [] and app.mb.showwarning.called


def test_install_timeout_kills_installer(app):
    proc = _Proc(hang=True)
    app.state_["proc"] = proc
    from pathlib import Path
    assert app.install_version(Path("x.msi")) is False
    assert proc.killed and "не завершилась" in app.status_var.get()


def test_uninstall_uses_registry_command_and_cleans_folders(app):
    app.current_version_info = {"name": "Р7", "version": "2026.3.2",
                                "uninstall_string": "MsiExec.exe /I{ABC}"}
    assert app.uninstall_current_version() is True
    assert "/X{ABC}" in app.procs[-1] and "/I{ABC}" not in app.procs[-1]
    assert app.removed                                   # каталоги программы — подменённый rmtree


def test_uninstall_failure_keeps_folders(app):
    app.current_version_info = {"name": "Р7", "version": "2026.3.2",
                                "uninstall_string": "MsiExec.exe /I{ABC}"}
    app.state_["proc"] = _Proc(1605)
    assert app.uninstall_current_version() is False
    assert app.removed == [] and "1605" in app.status_var.get()


def test_nothing_installed_uninstall_is_noop(app):
    app.current_version_info = None
    assert app.uninstall_current_version() is True and app.procs == []


# ── проверка хэшей ───────────────────────────────────────────────────────

def test_check_hashes_opens_results_window(app):
    _dist(app, "R7-2026.3.2.msi")
    app.check_hashes()
    app.root_.update()
    tops = [w for w in app.root_.winfo_children() if isinstance(w, tk.Toplevel)]
    titles = [t.title() for t in tops]
    assert any("Хеш" in t for t in titles)


def test_check_hashes_without_files_warns(app):
    app.check_hashes()
    assert app.mb.showwarning.call_args.args[0] == "Нет файлов"
