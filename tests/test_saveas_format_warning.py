"""Тесты для _find_window_hwnd (множественный exclude) и
_dismiss_saveas_format_warning — регрессия, найденная живым прогоном
27.08.2026 (пользователь наблюдал экран: диалог-предупреждение «весь
функционал будет потерян» реально висел с кнопками OK/Отмена, а лог писал
«кнопка OK — нет»).

Корень: поиск второго диалога шёл по подстроке заголовка «р7-офис»/
«r7-office», исключая только уже закрытый диалог «Сохранить как». Заголовок
ГЛАВНОГО окна редактора («...— Р7-Офис. Профессиональный (десктопная
версия)») тоже содержит эту подстроку и уже виден на экране в момент
самого первого вызова — поиск защёлкивался на нём раньше, чем настоящее
предупреждение вообще успевало появиться, и EnumChildWindows дальше искал
кнопку OK у Qt+CEF главного окна, где её нет в принципе.

win32gui — реальный установленный pywin32, функции подменяются через
monkeypatch (тот же приём, что и в tests/test_close_and_dialogs.py).
"""
from unittest.mock import Mock

import r7_Testovarka as r7mod


# ── _find_window_hwnd: exclude как один hwnd или множество ───────────────

def test_find_window_hwnd_still_accepts_single_int_exclude(bare_r7, monkeypatch):
    """Обратная совместимость: старые вызовы передают один hwnd, не set."""
    windows = [(100, "окно А (р7-офис)"), (200, "окно Б (р7-офис)")]
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(side_effect=lambda h: dict(windows)[h]))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: [cb(h, extra) for h, _ in windows]))

    result = bare_r7._find_window_hwnd("р7-офис", exclude=100)

    assert result == 200


def test_find_window_hwnd_excludes_iterable_of_hwnds(bare_r7, monkeypatch):
    """Главное окно и уже закрытый диалог «Сохранить как» исключаются
    одновременно — находится только реально новое окно."""
    windows = [
        (100, "test.xlsx - Р7-Офис. Профессиональный (десктопная версия)"),
        (50, "Сохранить как"),
        (200, "  Р7-Офис"),
    ]
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(side_effect=lambda h: dict(windows)[h]))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: [cb(h, extra) for h, _ in windows]))

    result = bare_r7._find_window_hwnd("р7-офис", "r7-office", exclude={100, 50})

    assert result == 200


def test_find_window_hwnd_returns_none_when_only_excluded_match(bare_r7, monkeypatch):
    windows = [(100, "test.xlsx - Р7-Офис"), (50, "Сохранить как")]
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(side_effect=lambda h: dict(windows)[h]))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: [cb(h, extra) for h, _ in windows]))

    result = bare_r7._find_window_hwnd("р7-офис", exclude={100, 50})

    assert result is None


# ── _dismiss_saveas_format_warning ────────────────────────────────────────

def test_dismiss_format_warning_excludes_main_window_too(bare_r7, log, monkeypatch):
    find_mock = Mock(return_value=None)
    monkeypatch.setattr(bare_r7, "_find_window_hwnd", find_mock)

    bare_r7._dismiss_saveas_format_warning(50, main_hwnd=100, timeout=0, log_cb=log)

    assert find_mock.call_args.kwargs["exclude"] == {50, 100}


def test_dismiss_format_warning_main_hwnd_optional(bare_r7, log, monkeypatch):
    """main_hwnd=None (по умолчанию) — старое поведение, только exclude_hwnd."""
    find_mock = Mock(return_value=None)
    monkeypatch.setattr(bare_r7, "_find_window_hwnd", find_mock)

    bare_r7._dismiss_saveas_format_warning(50, timeout=0, log_cb=log)

    assert find_mock.call_args.kwargs["exclude"] == {50}


def test_dismiss_format_warning_clicks_ok_when_found(bare_r7, log, monkeypatch):
    monkeypatch.setattr(bare_r7, "_find_window_hwnd", Mock(return_value=200))
    monkeypatch.setattr(
        "win32gui.EnumChildWindows",
        Mock(side_effect=lambda h, cb, extra: cb(5, extra)))
    monkeypatch.setattr("win32gui.GetClassName", Mock(return_value="Button"))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="OK"))
    send = Mock()
    monkeypatch.setattr("win32gui.SendMessage", send)

    result = bare_r7._dismiss_saveas_format_warning(50, main_hwnd=100, timeout=0, log_cb=log)

    assert result is True
    send.assert_called_once()


def test_dismiss_format_warning_returns_false_when_dialog_never_appears(bare_r7, log, monkeypatch):
    monkeypatch.setattr(bare_r7, "_find_window_hwnd", Mock(return_value=None))

    result = bare_r7._dismiss_saveas_format_warning(50, main_hwnd=100, timeout=0, log_cb=log)

    assert result is False


def test_dismiss_format_warning_returns_false_when_no_ok_button(bare_r7, log, monkeypatch):
    """Регрессия 27.08.2026: раньше это ветвление срабатывало для
    ГЛАВНОГО окна (у него нет классической кнопки OK) даже когда настоящий
    диалог с OK физически был на экране — теперь main_hwnd исключён из
    поиска, так что confirm_hwnd сюда попадает, только если это
    действительно окно БЕЗ найденной кнопки (проверка неизменного
    поведения самого разбора EnumChildWindows).

    time.sleep мокается: сам метод ретраит EnumChildWindows до 1 сек
    (см. docstring — гонка отрисовки дочерних контролов), не ждать эту
    секунду по-настоящему в юнит-тесте."""
    monkeypatch.setattr(bare_r7, "_find_window_hwnd", Mock(return_value=200))
    monkeypatch.setattr(
        "win32gui.EnumChildWindows",
        Mock(side_effect=lambda h, cb, extra: None))
    monkeypatch.setattr(r7mod.time, "sleep", Mock())
    # Вызовы time.time() по порядку: (1) deadline внешнего цикла — не влияет,
    # confirm_hwnd уже не None; (2) btn_deadline = time.time() + 1.0;
    # (3) проверка после первой же попытки EnumChildWindows — уже за
    # дедлайном, цикл выходит, не дожидаясь реальной секунды.
    monkeypatch.setattr(r7mod.time, "time", Mock(side_effect=[0.0, 100.0, 200.0]))

    result = bare_r7._dismiss_saveas_format_warning(50, main_hwnd=100, timeout=0, log_cb=log)

    assert result is False
