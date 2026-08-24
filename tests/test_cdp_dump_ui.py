"""Тесты для _cdp_dump_ui / _capture_cdp_ui_baseline — вычитание базового
DOM-снимка из дампа контекстного меню (issue #9: разные вызовы возвращали
идентичный список из 12 пунктов на неизменных координатах — оказался
постоянно смонтированный элемент тулбара, а не реальный попап).
"""
from unittest.mock import Mock

import pytest


def _item(text, tag="li", id_="", cls=""):
    return {"text": text, "tag": tag, "id": id_, "cls": cls, "x": 1, "y": 2, "depth": 1}


# ── _capture_cdp_ui_baseline ─────────────────────────────────────────────

def test_capture_baseline_stores_dump_when_connected(bare_r7):
    connector = Mock()
    connector.connected = True
    connector.dump_visible_ui.return_value = [_item("Вырезать")]
    bare_r7._webdriver_connector = connector

    bare_r7._capture_cdp_ui_baseline()

    assert bare_r7._cdp_ui_baseline == [_item("Вырезать")]


def test_capture_baseline_none_when_no_connector(bare_r7):
    bare_r7._webdriver_connector = None
    bare_r7._cdp_ui_baseline = [_item("stale")]  # должно быть перезаписано в None

    bare_r7._capture_cdp_ui_baseline()

    assert bare_r7._cdp_ui_baseline is None


def test_capture_baseline_none_when_not_connected(bare_r7):
    connector = Mock()
    connector.connected = False
    bare_r7._webdriver_connector = connector

    bare_r7._capture_cdp_ui_baseline()

    assert bare_r7._cdp_ui_baseline is None
    connector.dump_visible_ui.assert_not_called()


def test_capture_baseline_none_on_exception(bare_r7):
    connector = Mock()
    connector.connected = True
    connector.dump_visible_ui.side_effect = RuntimeError("ws closed")
    bare_r7._webdriver_connector = connector

    bare_r7._capture_cdp_ui_baseline()

    assert bare_r7._cdp_ui_baseline is None


# ── _cdp_item_key ─────────────────────────────────────────────────────────

def test_cdp_item_key_ignores_coordinates():
    """x/y не входят в ключ — popup может сдвинуться на пиксель между
    снимками без смены содержимого, а нас интересует именно контент."""
    import r7_Testovarka as r7mod
    a = {"tag": "li", "id": "", "cls": "", "text": "Копировать", "x": 10, "y": 20}
    b = {"tag": "li", "id": "", "cls": "", "text": "Копировать", "x": 999, "y": 999}
    assert r7mod.R7Testovarka._cdp_item_key(a) == r7mod.R7Testovarka._cdp_item_key(b)


# ── _cdp_dump_ui: без базового снимка (обратная совместимость) ──────────

def test_dump_without_baseline_prints_everything(bare_r7, log):
    connector = Mock()
    connector.connected = True
    connector.dump_visible_ui.return_value = [_item("Вырезать"), _item("Копировать")]
    bare_r7._webdriver_connector = connector
    bare_r7._cdp_ui_baseline = None

    bare_r7._cdp_dump_ui("метка", log_cb=log)

    assert any("видимых элементов 2" in m for m in log.messages)
    assert any("Вырезать" in m for m in log.messages)
    assert any("Копировать" in m for m in log.messages)


# ── _cdp_dump_ui: с базовым снимком — вычитание ──────────────────────────

def test_dump_with_baseline_shows_only_new_items(bare_r7, log):
    connector = Mock()
    connector.connected = True
    connector.dump_visible_ui.return_value = [
        _item("Вырезать"), _item("Копировать"), _item("Вставить", id_="paste-btn"),
    ]
    bare_r7._webdriver_connector = connector
    bare_r7._cdp_ui_baseline = [_item("Вырезать"), _item("Копировать")]

    bare_r7._cdp_dump_ui("вставка", log_cb=log)

    assert any("новых элементов 1" in m for m in log.messages)
    assert any("Вставить" in m for m in log.messages)
    assert not any("Вырезать" in m for m in log.messages)
    assert not any("'Копировать'" in m for m in log.messages)


def test_dump_with_baseline_and_no_new_items_logs_zero(bare_r7, log):
    """Регрессия для issue #9: если дамп после right-click полностью
    совпадает с базовым снимком (постоянно смонтированный элемент тулбара,
    а не реальный попап) — явно сказать «новых нет», а не молчать и не
    повторно печатать тот же статичный список."""
    connector = Mock()
    connector.connected = True
    same_items = [_item("Вырезать"), _item("Копировать")]
    connector.dump_visible_ui.return_value = same_items
    bare_r7._webdriver_connector = connector
    bare_r7._cdp_ui_baseline = same_items

    bare_r7._cdp_dump_ui("контекстное меню ячейки (вставка)", log_cb=log)

    assert any("новых элементов нет" in m for m in log.messages)
    assert not any("Вырезать" in m for m in log.messages)


def test_dump_with_baseline_ignores_coordinate_drift(bare_r7, log):
    """Тот же пункт, но popup чуть сдвинулся координатами — не должен
    считаться «новым» (ключ сравнения игнорирует x/y, см. _cdp_item_key)."""
    connector = Mock()
    connector.connected = True
    baseline_item = _item("Копировать")
    shifted_item = dict(baseline_item, x=500, y=600)
    connector.dump_visible_ui.return_value = [shifted_item]
    bare_r7._webdriver_connector = connector
    bare_r7._cdp_ui_baseline = [baseline_item]

    bare_r7._cdp_dump_ui("метка", log_cb=log)

    assert any("новых элементов нет" in m for m in log.messages)


def test_dump_respects_once_per_key_dedup_even_with_baseline(bare_r7, log):
    connector = Mock()
    connector.connected = True
    connector.dump_visible_ui.return_value = [_item("Вставить")]
    bare_r7._webdriver_connector = connector
    bare_r7._cdp_ui_baseline = []

    bare_r7._cdp_dump_ui("метка", log_cb=log)
    bare_r7._cdp_dump_ui("метка", log_cb=log)

    connector.dump_visible_ui.assert_called_once()
