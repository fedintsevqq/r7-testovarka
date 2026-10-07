"""Режим без прав администратора (r7/privileges.py): прогон вкладки идёт,
Batch отказывает с объяснением, сброс кэша ОС пропускается с меткой в
«Условиях прогона» отчёта, кнопка «Установить» выключена с подсказкой."""
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import r7_reports
import r7.privileges as privileges
import r7.windows as r7windows
import r7.readiness as rready
from conftest import patch_ui_name  # noqa: E402

R = r7mod.R7Testovarka


# ── is_admin ─────────────────────────────────────────────────────────────

def test_is_admin_false_on_any_error(monkeypatch):
    def _boom():
        raise OSError("нет shell32")
    monkeypatch.setattr(r7windows, "ctypes",
                        SimpleNamespace(windll=SimpleNamespace(shell32=SimpleNamespace(
                            IsUserAnAdmin=_boom))))
    privileges.is_admin.cache_clear()
    try:
        assert privileges.is_admin() is False
    finally:
        privileges.is_admin.cache_clear()


def test_is_admin_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(r7windows, "ctypes",
                        SimpleNamespace(windll=SimpleNamespace(shell32=SimpleNamespace(
                            IsUserAnAdmin=lambda: calls.append(1) or 1))))
    privileges.is_admin.cache_clear()
    try:
        assert privileges.is_admin() is True and privileges.is_admin() is True
        assert len(calls) == 1
    finally:
        privileges.is_admin.cache_clear()


def test_no_direct_admin_calls_outside_privileges():
    """IsUserAnAdmin зовёт только r7/privileges.py (через обёртку границы
    r7.windows.is_user_an_admin) — остальным нужна подмена в одном месте."""
    from pathlib import Path
    root = Path(r7mod.__file__).resolve().parent
    files = [root / "r7_Testovarka.py", *(root / "r7").rglob("*.py")]
    boundary = root / "r7" / "windows.py"
    offenders = [str(f) for f in files
                 if f.name != "privileges.py" and f != boundary
                 and "IsUserAnAdmin" in f.read_text(encoding="utf-8")]
    assert offenders == []
    callers = [str(f) for f in files
               if f.name != "privileges.py" and f != boundary
               and "is_user_an_admin" in f.read_text(encoding="utf-8")]
    assert callers == []


# ── Сброс кэша ОС без прав: пропуск с меткой в окружении ─────────────────

def test_purge_skipped_without_admin_and_warning_lands_in_report(bare_r7, monkeypatch, log):
    monkeypatch.setattr(privileges, "is_admin", lambda: False)
    bare_r7._run_environment = {"warnings": ["ноутбук работает от батареи"]}
    bare_r7._interference = {}

    assert bare_r7._purge_os_file_cache(log_cb=log) is False
    assert bare_r7._purge_os_file_cache(log_cb=log) is False      # повтор не дублирует метку

    assert any("кэш ОС не сброшен: нет прав администратора" in m for m in log.messages)
    assert bare_r7._os_cache_not_purged is True
    env_warnings = bare_r7._environment_with_interference()["warnings"]
    assert env_warnings.count(R.NO_ADMIN_CACHE_WARNING) == 1
    assert "открытие файла могло быть тёплым" in R.NO_ADMIN_CACHE_WARNING
    # …и доходит до блока «Условия прогона» HTML-отчёта.
    from pathlib import Path
    model = r7_reports.run_report_model([], Path("f.xlsx"), 1.0, "2026.3.2",
                                        system={"environment": bare_r7._environment_with_interference()})
    assert R.NO_ADMIN_CACHE_WARNING in model["warnings"]


def test_purge_with_admin_does_not_mark(bare_r7, monkeypatch, log):
    monkeypatch.setattr(privileges, "is_admin", lambda: True)
    monkeypatch.setattr(R, "PURGE_OS_FILE_CACHE", False)          # сам вызов ОС не делаем
    bare_r7._run_environment = {"warnings": []}
    assert bare_r7._purge_os_file_cache(log_cb=log) is False
    assert bare_r7._run_environment["warnings"] == []
    assert not getattr(bare_r7, "_os_cache_not_purged", False)


# ── Интерфейс на настоящем (скрытом) Tk ──────────────────────────────────

class _SyncThread:
    class Thread:
        def __init__(self, target=None, daemon=None, args=(), kwargs=None):
            self._t, self._a, self._k = target, args, kwargs or {}

        def start(self):
            self._t(*self._a, **self._k)

    def __getattr__(self, name):
        import threading
        return getattr(threading, name)


@pytest.fixture
def app_no_admin(monkeypatch, tmp_path):
    monkeypatch.setattr(privileges, "is_admin", lambda: False)
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
    monkeypatch.setattr(rready, "_missing_cdp_warning", lambda: None)
    patch_ui_name(monkeypatch, "pyperclip", object())
    for flag in ("PYAUTOGUI_OK", "EXCEL_OK", "WIN32_OK"):
        monkeypatch.setattr(r7mod.env, flag, True)
    inst = R(root)
    inst.reports_folder = tmp_path / "Reports"
    inst.reports_folder.mkdir()
    inst.distributives_folder = tmp_path / "Distributives"
    inst.distributives_folder.mkdir()
    inst.current_version_info = {"name": "Р7-Офис", "version": "2026.3.2"}
    inst.mb, inst.root_ = mb, root
    root.update()
    yield inst
    root.destroy()


def test_perf_run_allowed_without_admin(app_no_admin):
    app = app_no_admin
    got = {}
    app._spreadsheet_worker = lambda enabled, runs, stop: got.update(enabled=enabled)
    app.run_spreadsheet_test()
    app.root_.update()
    assert got["enabled"]
    assert not app.mb.showerror.called


def test_batch_refused_without_admin_with_explanation(app_no_admin):
    app = app_no_admin
    app._show_batch_config_dialog = Mock()
    (app.distributives_folder / "R7-2026.3.2.msi").write_bytes(b"")
    app.run_batch_mode()
    title, text = app.mb.showerror.call_args.args[:2]
    assert title == "Ошибка прав"
    assert "msiexec" in text and "Производительность" in text    # почему и что доступно
    assert not app._show_batch_config_dialog.called


def test_install_button_disabled_without_admin(app_no_admin):
    app = app_no_admin
    assert app.btn_install._r7_tooltip.text == R.NO_ADMIN_INSTALL_HINT
    assert "права администратора" in app.lbl_file_info.cget("text")
    (app.distributives_folder / "R7-2026.3.2.msi").write_bytes(b"\0" * 1024)
    app.refresh_distributives()
    app.tree.selection_set("0")
    app.on_select_distributive(None)
    app.root_.update()
    assert str(app.btn_install.cget("state")) == "disabled"
    assert "права администратора" in app.lbl_file_info.cget("text")
    # Enter по строке и прямой вызов — тоже отказ, установка не стартует.
    app.install_version = Mock()
    app.install_selected()
    assert app.mb.showerror.call_args.args[0] == "Ошибка прав"
    assert not app.install_version.called


def test_install_button_enabled_with_admin(app_no_admin, monkeypatch):
    app = app_no_admin
    monkeypatch.setattr(privileges, "is_admin", lambda: True)
    (app.distributives_folder / "R7-2026.3.2.msi").write_bytes(b"\0" * 1024)
    app.refresh_distributives()
    app.tree.selection_set("0")
    app.on_select_distributive(None)
    assert str(app.btn_install.cget("state")) == "normal"
    assert "МБ" in app.lbl_file_info.cget("text")


def test_main_entry_uses_privileges_module():
    """__main__ спрашивает права через privileges.is_admin, не через ctypes."""
    from pathlib import Path
    src = Path(r7mod.__file__).read_text(encoding="utf-8")
    main = src[src.index('if __name__ == "__main__":'):]
    assert "privileges.is_admin()" in main and "shell32" not in main
