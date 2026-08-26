"""Тесты для _try_cdp_saveas — открытие диалога «Сохранить как» кликом по
DOM через CDP, в обход синтетической клавиатуры (Ctrl+Shift+S/Alt+F/
WM_COMMAND — все три ненадёжны в этой среде, см. CLAUDE.md, L2).

Коннектор мокается (Mock), реальный CDP/websocket не поднимается — как и в
tests/test_cdp_ops.py. Сам клик по ribbon-вкладке «Файл» и найденный живым
прогоном (27.08.2026, tests/manual_saveas_cdp_probe.py) факт, что она — не
пункт классического меню, проверяется отдельно в tests/test_cdp_ops.py для
click_ribbon_item/_click_by_text_js; здесь — только логика _try_cdp_saveas
поверх уже готового ответа коннектора.
"""
from unittest.mock import Mock


def _connected(**attrs):
    c = Mock()
    c.connected = True
    for k, v in attrs.items():
        setattr(c, k, v)
    return c


def test_cdp_saveas_returns_false_when_not_connected(bare_r7, log):
    bare_r7._webdriver_connector = None

    assert bare_r7._try_cdp_saveas(999, log_cb=log) is False
    assert any("соединение недоступно" in m for m in log.messages)


def test_cdp_saveas_returns_false_when_file_tab_not_found(bare_r7, log):
    connector = _connected()
    connector.dump_visible_ui.return_value = []
    connector.click_ribbon_item.return_value = {"clicked": False, "candidates": 0}
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0

    assert bare_r7._try_cdp_saveas(999, log_cb=log) is False
    assert any("вкладка «Файл»" in m for m in log.messages)
    connector.click_ribbon_item.assert_called_once_with(["файл", "file"], timeout=5)


def test_cdp_saveas_returns_false_when_saveas_item_not_found(bare_r7, log, monkeypatch):
    connector = _connected()
    baseline = [{"text": "Файл", "tag": "a", "x": 0, "y": 33}]
    connector.dump_visible_ui.return_value = baseline
    connector.click_ribbon_item.side_effect = [
        {"clicked": True, "tag": "a", "text": "Файл"},
        {"clicked": False, "candidates": 0},
    ]
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0
    monkeypatch.setattr(bare_r7, "_pace", Mock())

    assert bare_r7._try_cdp_saveas(999, log_cb=log) is False
    assert any("пункт «Сохранить как»" in m for m in log.messages)
    second_call = connector.click_ribbon_item.call_args_list[1]
    assert second_call.args[0] == ["сохранить как", "save as"]
    assert second_call.kwargs["baseline"] == baseline
    bare_r7._pace.assert_called_once_with(bare_r7.OP_CDP_PANEL_PACE_SEC)


def test_cdp_saveas_returns_false_when_dialog_never_appears(bare_r7, log, monkeypatch):
    connector = _connected()
    connector.dump_visible_ui.return_value = []
    connector.click_ribbon_item.side_effect = [
        {"clicked": True, "tag": "a", "text": "Файл"},
        {"clicked": True, "text": "Сохранить как"},
    ]
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0
    monkeypatch.setattr(bare_r7, "_pace", Mock())
    monkeypatch.setattr(bare_r7, "_wait_for_window_title", Mock(return_value=False))

    assert bare_r7._try_cdp_saveas(999, log_cb=log) is False
    assert any("диалог" in m and "не появился" in m for m in log.messages)


def test_cdp_saveas_full_success(bare_r7, log, monkeypatch):
    connector = _connected()
    connector.dump_visible_ui.return_value = []
    connector.click_ribbon_item.side_effect = [
        {"clicked": True, "tag": "a", "text": "Файл"},
        {"clicked": True, "text": "Сохранить как"},
    ]
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0
    monkeypatch.setattr(bare_r7, "_pace", Mock())
    monkeypatch.setattr(bare_r7, "_wait_for_window_title", Mock(return_value=True))

    assert bare_r7._try_cdp_saveas(999, log_cb=log) is True
    assert any("диалог" in m and "открыт" in m for m in log.messages)


def test_cdp_saveas_returns_false_and_does_not_raise_on_connector_exception(bare_r7, log):
    connector = _connected()
    connector.dump_visible_ui.side_effect = RuntimeError("websocket closed")
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0

    assert bare_r7._try_cdp_saveas(999, log_cb=log) is False
    assert any("ошибка при попытке" in m for m in log.messages)


def test_cdp_saveas_uses_default_log_cb(bare_r7, monkeypatch):
    """log_cb=None должен упасть на self.add_test_log, как у остальных
    _try_*_saveas методов (см. _try_wm_command_saveas)."""
    bare_r7._webdriver_connector = None
    seen = []
    bare_r7.add_test_log = seen.append

    assert bare_r7._try_cdp_saveas(999) is False
    assert any("Сохранить как" in m for m in seen)
