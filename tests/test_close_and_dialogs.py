"""Тесты закрытия Р7-Офис: _close_r7_gracefully, _cancel_blocking_dialogs,
_click_priority_button, _terminate_r7_processes.

win32gui/win32con/win32process — реальный установленный pywin32, но все его
функции подменяются через monkeypatch: тесты не открывают и не ищут
настоящие окна ОС, только проверяют логику R7Testovarka поверх фейковых
ответов Win32 API.
"""
from unittest.mock import Mock

import pytest


# ── _close_r7_gracefully ─────────────────────────────────────────────────

def test_close_returns_true_when_window_already_gone(bare_r7, log, monkeypatch):
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 111))
    monkeypatch.setattr("win32gui.PostMessage", Mock())
    monkeypatch.setattr("win32gui.IsWindow", Mock(return_value=False))
    bare_r7._cancel_blocking_dialogs = Mock(return_value=0)

    result = bare_r7._close_r7_gracefully(hwnd=12345, log_cb=log, timeout=5)

    assert result is True
    assert any("закрыт штатно" in m for m in log.messages)


def test_close_cancels_blocking_dialogs_before_wm_close(bare_r7, log, monkeypatch):
    """«Сохранить как» блокирует WM_CLOSE наглухо — снимать его нужно ДО
    отправки WM_CLOSE, иначе весь timeout уходит впустую (см. CLAUDE.md)."""
    order = []
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 111))
    monkeypatch.setattr("win32gui.PostMessage",
                         Mock(side_effect=lambda *a, **kw: order.append("wm_close")))
    monkeypatch.setattr("win32gui.IsWindow", Mock(return_value=False))
    bare_r7._cancel_blocking_dialogs = Mock(
        side_effect=lambda *a, **kw: order.append("cancel_blocking") or 0)

    bare_r7._close_r7_gracefully(hwnd=1, log_cb=log, timeout=5)

    assert order == ["cancel_blocking", "wm_close"]


def test_close_clicks_win32_save_dialog_button(bare_r7, log, monkeypatch):
    """Путь 1: диалог «Сохранить изменения?» как отдельное Win32-окно того
    же процесса — находится через EnumWindows/GetWindowThreadProcessId и
    закрывается кликом по «Не сохранять»."""
    fake_dialog_hwnd = 777
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 555))
    monkeypatch.setattr("win32gui.PostMessage", Mock())
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: cb(fake_dialog_hwnd, extra)))
    monkeypatch.setattr("win32gui.IsWindow", Mock(side_effect=[True, False]))
    monkeypatch.setattr("win32gui.GetClassName", Mock(return_value="#32770"))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="Сохранить изменения?"))

    bare_r7._click_priority_button = Mock(return_value=(True, "Не сохранять"))
    bare_r7._cancel_blocking_dialogs = Mock(return_value=0)

    result = bare_r7._close_r7_gracefully(hwnd=1, log_cb=log, timeout=5)

    assert result is True
    bare_r7._click_priority_button.assert_called_once()
    called_hwnd, called_keywords = bare_r7._click_priority_button.call_args[0][:2]
    assert called_hwnd == fake_dialog_hwnd
    assert "не сохранять" in called_keywords
    assert any("закрыт кнопкой" in m for m in log.messages)


def test_close_falls_back_to_cdp_when_no_win32_dialog(bare_r7, log, monkeypatch):
    """Путь 2: модалка — HTML внутри CEF, у неё нет своего HWND (siblings
    пусты) — единственный путь к ней лежит через CDP."""
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 555))
    monkeypatch.setattr("win32gui.PostMessage", Mock())
    monkeypatch.setattr("win32gui.EnumWindows", Mock(side_effect=lambda cb, extra: None))
    monkeypatch.setattr("win32gui.IsWindow", Mock(side_effect=[True, False]))

    bare_r7._cancel_blocking_dialogs = Mock(return_value=0)
    bare_r7._webdriver_connector = Mock()
    bare_r7._webdriver_connector.connect = Mock(return_value=True)
    bare_r7._cdp_dismiss_save_dialog = Mock(return_value="Не сохранять")

    result = bare_r7._close_r7_gracefully(hwnd=1, log_cb=log, timeout=5)

    assert result is True
    bare_r7._cdp_dismiss_save_dialog.assert_called()
    assert any("через CDP" in m for m in log.messages)


def test_close_cdp_click_does_not_short_circuit_win32_path(bare_r7, log, monkeypatch):
    """Клик через CDP не означает «модалка закрылась» (JS видит только сам
    клик) — латч dismissed не должен взводиться от одного CDP-клика, иначе
    ложное срабатывание навсегда отключило бы и Win32-путь, и повторы."""
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 555))
    monkeypatch.setattr("win32gui.PostMessage", Mock())
    monkeypatch.setattr("win32gui.EnumWindows", Mock(side_effect=lambda cb, extra: None))
    # Окно не исчезает - таймаут короткий, чтобы тест был быстрым.
    monkeypatch.setattr("win32gui.IsWindow", Mock(return_value=True))

    bare_r7._cancel_blocking_dialogs = Mock(return_value=0)
    bare_r7._webdriver_connector = Mock()
    bare_r7._webdriver_connector.connect = Mock(return_value=True)
    bare_r7._cdp_dismiss_save_dialog = Mock(return_value="Не сохранять")
    bare_r7._terminate_r7_processes = Mock(return_value=True)

    result = bare_r7._close_r7_gracefully(hwnd=1, log_cb=log, timeout=0.3)

    assert result is False
    bare_r7._terminate_r7_processes.assert_called_once()


def test_close_force_kills_after_timeout_when_nothing_dismisses(bare_r7, log, monkeypatch):
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 111))
    monkeypatch.setattr("win32gui.PostMessage", Mock())
    monkeypatch.setattr("win32gui.EnumWindows", Mock(side_effect=lambda cb, extra: None))
    monkeypatch.setattr("win32gui.IsWindow", Mock(return_value=True))

    bare_r7._cancel_blocking_dialogs = Mock(return_value=0)
    bare_r7._webdriver_connector = None
    bare_r7._terminate_r7_processes = Mock(return_value=True)

    result = bare_r7._close_r7_gracefully(hwnd=999, log_cb=log, timeout=0.3)

    assert result is False
    bare_r7._terminate_r7_processes.assert_called_once()
    assert any("не закрылся за" in m for m in log.messages)


def test_close_terminates_directly_when_hwnd_is_none(bare_r7, log):
    bare_r7._terminate_r7_processes = Mock(return_value=True)

    result = bare_r7._close_r7_gracefully(hwnd=None, log_cb=log, timeout=5)

    assert result is False
    bare_r7._terminate_r7_processes.assert_called_once()


# ── _cancel_blocking_dialogs ─────────────────────────────────────────────

def test_cancel_blocking_dialogs_matches_save_as_but_not_save_changes(bare_r7, log, monkeypatch):
    """Регрессия: маска составная («сохранить как»), а не голое «сохранить» —
    иначе под неё попал бы и диалог «Сохранить изменения?», где «Отмена»
    означает «не закрывать Р7» (это должно остаться нетронутым)."""
    windows = {
        1: "Сохранить как",
        2: "Сохранить изменения?",
    }
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(side_effect=lambda h: windows[h]))
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 555))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: [cb(h, extra) for h in windows]))

    bare_r7._click_priority_button = Mock(return_value=(True, "Отмена"))

    closed = bare_r7._cancel_blocking_dialogs(owner_pid=555, log_cb=log, max_rounds=1)

    assert closed == 1
    clicked_hwnd = bare_r7._click_priority_button.call_args[0][0]
    assert clicked_hwnd == 1


def test_cancel_blocking_dialogs_skips_foreign_pid(bare_r7, log, monkeypatch):
    """Чужой «Сохранить как» (другой процесс Р7 или сторонний) — не наш, не трогаем."""
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="Сохранить как"))
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 999))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: cb(42, extra)))

    bare_r7._click_priority_button = Mock(return_value=(True, "Отмена"))

    closed = bare_r7._cancel_blocking_dialogs(owner_pid=555, log_cb=log, max_rounds=1)

    assert closed == 0
    bare_r7._click_priority_button.assert_not_called()


def test_cancel_blocking_dialogs_falls_back_to_wm_close_without_button(bare_r7, log, monkeypatch):
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="Подтвердите перезапись"))
    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 555))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: cb(9, extra)))
    post = Mock()
    monkeypatch.setattr("win32gui.PostMessage", post)

    bare_r7._click_priority_button = Mock(return_value=(False, None))

    closed = bare_r7._cancel_blocking_dialogs(owner_pid=555, log_cb=log, max_rounds=1)

    assert closed == 1
    post.assert_called_once()


# ── _click_priority_button ───────────────────────────────────────────────

def test_click_priority_button_prefers_higher_priority_keyword(bare_r7, monkeypatch):
    children = [
        (1, "Нет", "Button"),
        (2, "Не сохранять", "Button"),
    ]
    monkeypatch.setattr(
        "win32gui.EnumChildWindows",
        Mock(side_effect=lambda h, cb, extra: [cb(c[0], extra) for c in children]))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(
        side_effect=lambda h: next(c[1] for c in children if c[0] == h)))
    monkeypatch.setattr("win32gui.GetClassName", Mock(return_value="Button"))
    send = Mock()
    monkeypatch.setattr("win32gui.SendMessage", send)

    clicked, text = bare_r7._click_priority_button(
        hwnd=100, keyword_priority=("не сохранять", "нет"), log_cb=lambda m: None)

    assert clicked is True
    assert text == "Не сохранять"
    send.assert_called_once()
    assert send.call_args[0][0] == 2


def test_click_priority_button_returns_false_and_dumps_when_no_match(bare_r7, monkeypatch):
    children = [(1, "Отмена", "Button")]
    monkeypatch.setattr(
        "win32gui.EnumChildWindows",
        Mock(side_effect=lambda h, cb, extra: [cb(c[0], extra) for c in children]))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="Отмена"))
    monkeypatch.setattr("win32gui.GetClassName", Mock(return_value="Button"))

    logged = []
    clicked, text = bare_r7._click_priority_button(
        hwnd=100, keyword_priority=("не сохранять",), log_cb=logged.append)

    assert clicked is False
    assert text is None
    assert any("Кнопки для закрытия не найдены" in m for m in logged)


def test_click_priority_button_falls_back_to_post_message_on_send_failure(bare_r7, monkeypatch):
    monkeypatch.setattr(
        "win32gui.EnumChildWindows",
        Mock(side_effect=lambda h, cb, extra: cb(5, extra)))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="Не сохранять"))
    monkeypatch.setattr("win32gui.GetClassName", Mock(return_value="Button"))
    monkeypatch.setattr("win32gui.SendMessage", Mock(side_effect=RuntimeError("no BM_CLICK")))
    post = Mock()
    monkeypatch.setattr("win32gui.PostMessage", post)

    clicked, text = bare_r7._click_priority_button(
        hwnd=100, keyword_priority=("не сохранять",), log_cb=lambda m: None)

    assert clicked is True
    assert post.call_count == 2  # WM_LBUTTONDOWN + WM_LBUTTONUP


# ── _terminate_r7_processes ──────────────────────────────────────────────

def test_terminate_returns_true_when_no_processes(bare_r7, log):
    bare_r7._get_r7_processes = Mock(return_value=[])
    assert bare_r7._terminate_r7_processes(log_cb=log) is True


def test_terminate_kills_processes_unresponsive_to_terminate(bare_r7, log, monkeypatch, fake_process):
    proc = fake_process()
    bare_r7._get_r7_processes = Mock(return_value=[proc])
    monkeypatch.setattr("psutil.wait_procs", Mock(return_value=([], [proc])))

    result = bare_r7._terminate_r7_processes(log_cb=log)

    assert result is True
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()
    assert any("Принудительно завершено" in m for m in log.messages)


def test_terminate_does_not_kill_processes_that_exit_gracefully(bare_r7, log, monkeypatch, fake_process):
    proc = fake_process()
    bare_r7._get_r7_processes = Mock(return_value=[proc])
    monkeypatch.setattr("psutil.wait_procs", Mock(return_value=([proc], [])))

    bare_r7._terminate_r7_processes(log_cb=log)

    proc.terminate.assert_called_once()
    proc.kill.assert_not_called()
