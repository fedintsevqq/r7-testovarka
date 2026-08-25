"""Тесты для _try_wm_command_saveas — запасной путь открытия диалога
«Сохранить как» через нативное меню окна (WM_COMMAND), добавленный после
живого прогона 27.08.2026: Ctrl+Shift+S и Alt+F синхронно не открывали
диалог даже после перезагрузки машины (см. CLAUDE.md, L2, «продолжение
№3» и docstring save_as_format).

win32gui/win32con — реальный установленный pywin32, но функции подменяются
через monkeypatch: тесты не открывают и не ищут настоящие окна ОС, только
проверяют логику разбора меню поверх фейковых ответов Win32 API.
`_menu_item_info` мокается напрямую (не через win32gui_struct/ctypes-буфер)
— она сама протестирована косвенно тем, что весь стек уже гоняется на
живом Р7 в tests/manual_saveas_uia_save.py для смежной UIA-логики; здесь
важна только логика поиска пункта меню поверх её результата.

Сам save_as_format() — вложенная функция внутри _spreadsheet_worker и
недоступна юнит-тестам напрямую (см. CLAUDE.md про run_test_with_runs) —
поэтому здесь проверяется только вынесенный в метод класса запасной путь,
не вся цепочка хоткей → меню → WM_COMMAND целиком.
"""
from unittest.mock import Mock

import r7_Testovarka as r7mod


def test_wm_command_saveas_returns_false_when_win32_unavailable(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "WIN32_OK", False)

    result = bare_r7._try_wm_command_saveas(999, log_cb=log)

    assert result is False
    assert any("WIN32_OK=False" in m for m in log.messages)


def test_wm_command_saveas_returns_false_when_no_hwnd(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "WIN32_OK", True)

    result = bare_r7._try_wm_command_saveas(None, log_cb=log)

    assert result is False


def test_wm_command_saveas_returns_false_when_no_native_menu(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    monkeypatch.setattr("win32gui.GetMenu", lambda h: 0)

    result = bare_r7._try_wm_command_saveas(999, log_cb=log)

    assert result is False
    assert any("нет классического HMENU" in m for m in log.messages)


def test_wm_command_saveas_returns_false_when_file_menu_missing(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    monkeypatch.setattr("win32gui.GetMenu", lambda h: 111)
    monkeypatch.setattr("win32gui.GetMenuItemCount", lambda h: 2)
    items = {(111, 0): ("&Правка", 0, 0), (111, 1): ("&Вид", 0, 0)}
    bare_r7._menu_item_info = lambda h, i: items[(h, i)]

    result = bare_r7._try_wm_command_saveas(999, log_cb=log)

    assert result is False
    assert any("«Файл» не найден" in m for m in log.messages)


def test_wm_command_saveas_returns_false_when_save_as_item_missing(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    monkeypatch.setattr("win32gui.GetMenu", lambda h: 111)

    def fake_count(h):
        return {111: 2, 222: 2}[h]

    items = {
        (111, 0): ("&Файл", 0, 222),
        (111, 1): ("&Правка", 0, 0),
        (222, 0): ("&Открыть", 40001, 0),
        (222, 1): ("&Печать", 40002, 0),
    }
    monkeypatch.setattr("win32gui.GetMenuItemCount", fake_count)
    bare_r7._menu_item_info = lambda h, i: items[(h, i)]

    result = bare_r7._try_wm_command_saveas(999, log_cb=log)

    assert result is False
    assert any("«Сохранить как» не найден" in m for m in log.messages)


def test_wm_command_saveas_posts_command_and_returns_true(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    monkeypatch.setattr("win32gui.GetMenu", lambda h: 111)

    def fake_count(h):
        return {111: 3, 222: 5}[h]

    items = {
        (111, 0): ("&Правка", 0, 0),
        (111, 1): ("&Файл", 0, 222),
        (111, 2): ("&Вид", 0, 0),
        (222, 0): ("&Открыть", 40001, 0),
        (222, 1): ("&Сохранить", 40020, 0),
        (222, 2): ("Сохранить &как...\tCtrl+Shift+S", 40021, 0),
        (222, 3): ("&Печать", 40002, 0),
        (222, 4): ("В&ыход", 40099, 0),
    }
    monkeypatch.setattr("win32gui.GetMenuItemCount", fake_count)
    bare_r7._menu_item_info = lambda h, i: items[(h, i)]
    post_mock = Mock()
    monkeypatch.setattr("win32gui.PostMessage", post_mock)

    result = bare_r7._try_wm_command_saveas(999, log_cb=log)

    assert result is True
    post_mock.assert_called_once_with(999, r7mod.win32con.WM_COMMAND, 40021, 0)


def test_wm_command_saveas_returns_false_when_post_message_raises(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    monkeypatch.setattr("win32gui.GetMenu", lambda h: 111)

    def fake_count(h):
        return {111: 1, 222: 1}[h]

    items = {
        (111, 0): ("&Файл", 0, 222),
        (222, 0): ("Сохранить &как...", 40021, 0),
    }
    monkeypatch.setattr("win32gui.GetMenuItemCount", fake_count)
    bare_r7._menu_item_info = lambda h, i: items[(h, i)]
    monkeypatch.setattr("win32gui.PostMessage", Mock(side_effect=OSError("boom")))

    result = bare_r7._try_wm_command_saveas(999, log_cb=log)

    assert result is False


def test_wm_command_saveas_returns_false_when_menu_parsing_raises(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    monkeypatch.setattr("win32gui.GetMenu", lambda h: 111)
    monkeypatch.setattr("win32gui.GetMenuItemCount", Mock(side_effect=OSError("boom")))

    result = bare_r7._try_wm_command_saveas(999, log_cb=log)

    assert result is False
    assert any("не удалось разобрать меню" in m for m in log.messages)
