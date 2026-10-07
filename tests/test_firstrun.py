"""Мастер первого запуска: проверки (r7/firstrun.py) на подменённых входах
и окно (r7/ui/firstrun_dialog.py) на настоящем (скрытом) Tk."""
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
from conftest import patch_ui_name  # noqa: E402
from r7 import config as r7config
from r7 import firstrun, privileges, selfcheck, settings
from r7.ui import firstrun_dialog

R = r7mod.R7Testovarka


# ── Проверки ──────────────────────────────────────────────────────────────

def test_check_admin_is_warning_not_failure():
    assert firstrun.check_admin(is_admin=True).status == firstrun.OK
    c = firstrun.check_admin(is_admin=False)
    assert c.status == firstrun.WARN and "кэш" in c.detail and c.fix


def test_check_r7_found_reports_where_it_looked():
    app = SimpleNamespace(_find_r7_path=lambda: r"E:\R7\DesktopEditors.exe",
                          current_version_info={"version": "2026.3.2"})
    c = firstrun.check_r7_found(app)
    assert c.status == firstrun.OK and "2026.3.2" in c.detail

    app = SimpleNamespace(_find_r7_path=lambda: None,
                          _r7_path_searched=["реестр: записи нет", r"C:\Program Files\R7-Office"])
    c = firstrun.check_r7_found(app)
    assert c.status == firstrun.FAIL
    assert "реестр: записи нет" in c.detail and r"C:\Program Files\R7-Office" in c.detail
    assert "r7_path" in c.fix


def test_check_r7_running():
    app = SimpleNamespace(_get_r7_processes=lambda log_cb=None: [])
    assert firstrun.check_r7_running(app).status == firstrun.OK
    app = SimpleNamespace(_get_r7_processes=lambda log_cb=None: [Mock(pid=4242)])
    c = firstrun.check_r7_running(app)
    assert c.status == firstrun.WARN and "4242" in c.detail


def test_check_cdp_port():
    assert firstrun.check_cdp_port(8080, port_free=lambda p: True).status == firstrun.OK
    c = firstrun.check_cdp_port(8080, port_free=lambda p: False)
    assert c.status == firstrun.WARN and "8080" in c.detail and "8081" in c.fix


@pytest.mark.parametrize("name", ["файл-для-теста-Р7-офис-50К.xlsx", "r7-test-50k.xlsx",
                                  "r7-test-50k-v2.xlsx"])
def test_check_fixture_finds_cyrillic_and_latin_names(tmp_path, name):
    (tmp_path / name).write_bytes(b"\0" * 2048)
    c = firstrun.check_fixture([tmp_path])
    assert c.status == firstrun.OK and name in c.detail


def test_check_fixture_missing_hints_generator(tmp_path):
    (tmp_path / "~$r7-test-50k.xlsx").write_bytes(b"")       # lock-файл — не фикстура
    c = firstrun.check_fixture([tmp_path, tmp_path / "нет"])
    assert c.status == firstrun.FAIL and "Тестовые файлы" in c.fix


def test_check_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(firstrun.shutil, "disk_usage",
                        lambda p: SimpleNamespace(free=12 * 1024 ** 3))
    assert firstrun.check_disk(tmp_path).status == firstrun.OK
    monkeypatch.setattr(firstrun.shutil, "disk_usage",
                        lambda p: SimpleNamespace(free=int(1.5 * 1024 ** 3)))
    c = firstrun.check_disk(tmp_path / "ещё" / "нет")           # папки может не быть
    assert c.status == firstrun.WARN and "1.5" in c.detail


def test_check_dpi():
    assert firstrun.check_dpi(100).status == firstrun.OK
    c = firstrun.check_dpi(150)
    assert c.status == firstrun.WARN and "150" in c.detail and "100" in c.fix
    assert firstrun.check_dpi(None).status == firstrun.WARN


def test_check_packages(monkeypatch):
    for flag in ("PYAUTOGUI_OK", "EXCEL_OK", "WIN32_OK", "WEBDRIVER_OK", "PYWINAUTO_OK",
                 "PSUTIL_OK"):
        monkeypatch.setattr(firstrun.env, flag, True)
    monkeypatch.setattr(firstrun.env, "pyperclip", object())
    assert firstrun.check_packages().status == firstrun.OK
    monkeypatch.setattr(firstrun.env, "WEBDRIVER_OK", False)
    monkeypatch.setattr(firstrun.env, "EXCEL_OK", False)
    c = firstrun.check_packages()
    assert c.status == firstrun.WARN and "CDP" in c.detail and "openpyxl" in c.detail


def test_check_build_uses_selfcheck(monkeypatch):
    monkeypatch.setattr(selfcheck, "run", lambda out: (out("R7-Testovarka self-check"),
                                                       out("OK"), 0)[-1])
    assert firstrun.check_build().status == firstrun.OK
    monkeypatch.setattr(selfcheck, "run", lambda out: (out("  ✗ шаблон отчёта не найден: x"),
                                                       out("ПРОБЛЕМ: 1"), 1)[-1])
    c = firstrun.check_build()
    assert c.status == firstrun.FAIL and "шаблон отчёта не найден" in c.detail


def test_run_checks_covers_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(selfcheck, "run", lambda out: 0)
    monkeypatch.setattr(privileges, "is_admin", lambda: True)
    app = SimpleNamespace(_find_r7_path=lambda: r"E:\R7\DesktopEditors.exe",
                          current_version_info=None,
                          _get_r7_processes=lambda log_cb=None: [],
                          test_files_folder=tmp_path, reports_folder=tmp_path,
                          _get_dpi_scale_pct=lambda: 100)
    monkeypatch.setattr(firstrun.ReadinessMixin, "_cdp_port_free", staticmethod(lambda p, timeout=0.2: True))
    checks = firstrun.run_checks(app)
    names = [c.name for c in checks]
    assert names == ["Сборка программы", "Права администратора", "Р7-Офис", "Р7-Офис закрыт",
                     "Порт CDP", "Тестовый файл", "Место на диске", "Масштаб экрана",
                     "Пакеты Python"]
    assert firstrun.has_failures(checks)                       # нет фикстуры в tmp_path
    assert all(c.fix for c in checks if c.status != firstrun.OK)


# ── Окно ──────────────────────────────────────────────────────────────────

@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    monkeypatch.setattr(R, "_load_test_selection", lambda self: {})
    monkeypatch.setattr(R, "_save_test_selection", lambda self: None)
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    patch_ui_name(monkeypatch, "messagebox", Mock())
    inst = R(root)
    root.update()
    yield inst
    root.destroy()


def _labels(widget):
    out = []
    for c in widget.winfo_children():
        if c.winfo_class() == "TLabel":
            out.append(str(c.cget("text")))
        out.extend(_labels(c))
    return out


FAIL_SET = [firstrun.Check("Сборка программы", firstrun.OK, "всё на месте"),
            firstrun.Check("Р7-Офис", firstrun.FAIL, "не найден. Искали: реестр",
                           "укажите r7_path"),
            firstrun.Check("Масштаб экрана", firstrun.WARN, "150 %", "поставьте 100 %")]
WARN_SET = [firstrun.Check("Сборка программы", firstrun.OK, "всё на месте"),
            firstrun.Check("Права администратора", firstrun.WARN, "нет", "перезапустите")]


def test_dialog_lists_checks_and_disables_continue_on_fail(app):
    d = firstrun_dialog.show_first_run_dialog(app, checks=FAIL_SET, wait=False)
    app.root.update()
    assert d.dlg.winfo_exists() and d.dlg.title() == firstrun_dialog.TITLE
    texts = _labels(d.dlg)
    assert "✓" in texts and "!" in texts and "✗" in texts
    assert "Р7-Офис" in texts and "не найден. Искали: реестр" in texts
    assert any("укажите r7_path" in t for t in texts)
    assert str(d.btn_continue.cget("state")) == "disabled"
    assert any("не пойдёт" in t for t in texts)
    d.close()
    app.root.update()
    assert not d.dlg.winfo_exists()


def test_retry_reruns_checks_and_enables_continue(app):
    sets = iter([FAIL_SET, WARN_SET])
    d = firstrun_dialog.show_first_run_dialog(app, run_checks=lambda: next(sets), wait=False)
    assert str(d.btn_continue.cget("state")) == "disabled"
    d.btn_retry.invoke()
    app.root.update()
    assert str(d.btn_continue.cget("state")) == "normal"
    assert "Права администратора" in _labels(d.dlg)
    assert "Р7-Офис" not in _labels(d.dlg)
    d.close()


def test_checkbox_persists_first_run_done(app, tmp_path):
    d = firstrun_dialog.show_first_run_dialog(app, checks=WARN_SET, wait=False)
    d.btn_continue.invoke()
    app.root.update()
    assert settings.get("first_run_done") is None            # без флажка не запоминается
    assert not d.dlg.winfo_exists()

    d = firstrun_dialog.show_first_run_dialog(app, checks=WARN_SET, wait=False)
    d.dont_show.set(True)
    d.btn_continue.invoke()
    app.root.update()
    assert settings.get("first_run_done") is True
    assert (tmp_path / settings.SETTINGS_FILE).exists()


def test_main_entry_shows_wizard_until_first_run_done():
    """__main__ показывает мастер до deiconify и только без first_run_done."""
    from pathlib import Path
    src = Path(r7mod.__file__).read_text(encoding="utf-8")
    main = src[src.index('if __name__ == "__main__":'):]
    hook = main.index("show_first_run_dialog(app)")
    assert main.index('settings.get("first_run_done")') < hook < main.index("root.deiconify()")


def test_dialog_is_visible_while_root_hidden(app):
    """Корень ещё скрыт (withdraw) — диалог не должен прятаться вместе с ним."""
    assert app.root.state() == "withdrawn"
    d = firstrun_dialog.show_first_run_dialog(app, checks=WARN_SET, wait=False)
    app.root.update()
    assert d.dlg.state() == "normal"
    d.close()
