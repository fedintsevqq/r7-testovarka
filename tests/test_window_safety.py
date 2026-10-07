"""Действия только над окнами Р7 (правило 9 CLAUDE.md, аудит 06.10.2026).

- Клавиатурный путь слал хоткеи в любое активное окно.
- Если Р7 не запущен, поиск «Сохранить как» сверял только заголовок.
- Без psutil/pywin32 любое окно считалось окном Р7.
"""
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod


@pytest.fixture
def app():
    inst = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    inst._r7_pids = None
    inst._pace = lambda sec: None      # ожидание без окна — без реального сна
    return inst


@pytest.fixture
def keys(monkeypatch):
    m = {"hotkey": Mock(), "press": Mock()}
    monkeypatch.setattr(r7mod.env.pyautogui, "hotkey", m["hotkey"])
    monkeypatch.setattr(r7mod.env.pyautogui, "press", m["press"])
    monkeypatch.setattr("win32gui.GetForegroundWindow", lambda: 777)
    monkeypatch.setattr("win32gui.GetWindowText", lambda h: "Вход — Google Chrome")
    return m


# ── Клавиши ───────────────────────────────────────────────────────────────

def test_keys_not_sent_to_foreign_foreground_window(app, keys):
    app._is_r7_window = lambda hwnd: False
    with pytest.raises(RuntimeError, match="не Р7.*Google Chrome"):
        app._hotkey("ctrl", "-")
    with pytest.raises(RuntimeError):
        app._press("enter")
    keys["hotkey"].assert_not_called()
    keys["press"].assert_not_called()


def test_keys_sent_to_r7_window(app, keys):
    seen = []
    app._is_r7_window = lambda hwnd: seen.append(hwnd) or True
    app._hotkey("ctrl", "a")
    app._press("right", presses=3)
    keys["hotkey"].assert_called_once_with("ctrl", "a")
    keys["press"].assert_called_once_with("right", presses=3)
    assert seen == [777, 777]                 # проверка перед каждым вызовом


def test_no_foreground_window_is_not_r7(app, keys, monkeypatch):
    monkeypatch.setattr("win32gui.GetForegroundWindow", lambda: 0)
    app._is_r7_window = lambda hwnd: True
    with pytest.raises(RuntimeError):
        app._press("enter")
    keys["press"].assert_not_called()


def test_keys_refused_without_pywin32(app, keys, monkeypatch):
    monkeypatch.setattr(r7mod.env, "WIN32_OK", False)
    with pytest.raises(RuntimeError, match="pywin32"):
        app._hotkey("ctrl", "v")
    keys["hotkey"].assert_not_called()


# ── Владелец окна ─────────────────────────────────────────────────────────

def test_is_r7_window_false_without_psutil(app, monkeypatch):
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    assert app._is_r7_window(123) is False


@pytest.mark.parametrize("setup", ["no_r7", "no_psutil", "error"])
def test_owner_pids_never_none(app, monkeypatch, setup):
    """None означал бы «владельца не проверять» — ровно та дыра."""
    if setup == "no_psutil":
        monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    elif setup == "no_r7":
        monkeypatch.setattr(r7mod.env, "PSUTIL_OK", True)
        app._get_r7_processes = lambda log_cb=None: []
    else:
        monkeypatch.setattr(r7mod.env, "PSUTIL_OK", True)

        def boom(log_cb=None):
            raise RuntimeError("psutil")

        app._get_r7_processes = boom
    assert app._r7_window_owner_pids() == set()


def test_save_as_of_other_program_not_found_when_r7_closed(app, monkeypatch):
    """Р7 не запущен, на экране «Сохранить как» Chrome — не находить его."""
    monkeypatch.setattr("win32gui.EnumWindows", lambda cb, extra: cb(555, extra))
    monkeypatch.setattr("win32gui.IsWindowVisible", lambda h: True)
    monkeypatch.setattr("win32gui.GetWindowText", lambda h: "Сохранить как")
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (1, 9999))
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", True)
    app._get_r7_processes = lambda log_cb=None: []

    owners = app._r7_window_owner_pids()
    assert app._find_window_hwnd("сохранить как", owner_pids=owners) is None


def test_save_as_of_r7_is_found(app, monkeypatch):
    monkeypatch.setattr("win32gui.EnumWindows", lambda cb, extra: cb(555, extra))
    monkeypatch.setattr("win32gui.IsWindowVisible", lambda h: True)
    monkeypatch.setattr("win32gui.GetWindowText", lambda h: "Сохранить как")
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (1, 4242))
    assert app._find_window_hwnd("сохранить как", owner_pids={4242}) == 555


# ── Ревью #39: мгновение без окна и проверка фокуса ──────────────────────

def test_waits_out_short_gap_without_foreground_window(app, keys, monkeypatch):
    """Сразу после закрытия диалога окна на переднем плане нет — подождать,
    а не валить тест."""
    seq = iter([0, 0, 777])
    monkeypatch.setattr("win32gui.GetForegroundWindow", lambda: next(seq))
    app._is_r7_window = lambda hwnd: hwnd == 777
    paced = []
    app._pace = paced.append
    app._press("enter")
    keys["press"].assert_called_once()
    assert len(paced) == 2                      # пауза через _pace — вычитается


def test_gap_too_long_still_refused(app, keys, monkeypatch):
    monkeypatch.setattr("win32gui.GetForegroundWindow", lambda: 0)
    app._is_r7_window = lambda hwnd: True
    app._pace = lambda s: None
    with pytest.raises(RuntimeError):
        app._press("enter")
    keys["press"].assert_not_called()


def test_foreign_window_refused_without_waiting(app, keys):
    app._is_r7_window = lambda hwnd: False
    paced = []
    app._pace = paced.append
    with pytest.raises(RuntimeError):
        app._press("enter")
    assert paced == []


@pytest.mark.parametrize("fg_is_r7, click_ok, expected, clicked", [
    (True, False, True, False),     # SetForegroundWindow сработал — клик не нужен
    (False, True, True, True),      # не сработал — клик по заголовку помог
    (False, False, False, True),    # и клик не помог — честное False
])
def test_focus_r7_window_verifies_foreground(app, monkeypatch, fg_is_r7, click_ok,
                                             expected, clicked):
    monkeypatch.setattr("win32gui.SetForegroundWindow", lambda h: None)
    monkeypatch.setattr("win32gui.GetForegroundWindow", lambda: 555)
    app._is_r7_window = lambda hwnd: fg_is_r7
    click = Mock(return_value=click_ok)
    app._ensure_foreground_click = click
    assert app._focus_r7_window(123) is expected
    assert click.called is clicked


def test_no_raw_pyautogui_key_calls_outside_wrappers():
    """Защита от регресса: прямой pyautogui.hotkey/press в обход _hotkey/_press
    снова слал бы клавиши в любое окно. Вложенные функции воркеров юнит-тесты
    не видят (правило 6), поэтому проверка по тексту модуля."""
    import re
    from pathlib import Path
    root = Path(r7mod.__file__).parent
    found = {}
    # tests/live — тоже: живой набор должен слать клавиши тем же путём, что прогон.
    for f in [root / "r7_Testovarka.py", root / "r7_ops.py", *(root / "r7").glob("*.py"),
              *(root / "tests" / "live").glob("*.py")]:
        code_lines = [ln for ln in f.read_text(encoding="utf-8").splitlines()
                      if not ln.lstrip().startswith("#") and "`pyautogui" not in ln]
        n = len(re.findall(r"pyautogui\.(?:hotkey|press)\(", "\n".join(code_lines)))
        if n:
            found[f.name] = n
    assert found == {"windows.py": 2}       # только внутри _hotkey и _press
