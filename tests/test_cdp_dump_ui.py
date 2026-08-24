"""Тесты для _cdp_dump_ui / _capture_cdp_ui_baseline — вычитание базового
DOM-снимка из дампа контекстного меню (issue #9: разные вызовы возвращали
идентичный список из 12 пунктов на неизменных координатах — оказался
постоянно смонтированный элемент тулбара, а не реальный попап).
"""
from unittest.mock import Mock

import pytest


def _item(text, tag="li", id_="", cls="", x=1, y=2):
    return {"text": text, "tag": tag, "id": id_, "cls": cls, "x": x, "y": y, "depth": 1}


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


def test_capture_baseline_logs_warning_on_exception(bare_r7, log):
    """Регрессия для находки код-ревью PR #10: раньше исключение при снятии
    базового снимка глушилось молча, без единой строки в логе — при обрыве
    CDP разработчик не мог отличить «CDP не настроен» от «CDP отвалился
    прямо на этом шаге»."""
    connector = Mock()
    connector.connected = True
    connector.dump_visible_ui.side_effect = RuntimeError("ws closed")
    bare_r7._webdriver_connector = connector

    bare_r7._capture_cdp_ui_baseline(log_cb=log)

    assert any("Базовый DOM-снимок не снят" in m for m in log.messages)


def test_capture_baseline_resets_dedup_seen_set(bare_r7):
    """Регрессия для находки код-ревью PR #10: без сброса _cdp_dump_seen
    повторный запуск теста в той же сессии GUI платил бы CDP round-trip за
    базовый снимок, который _cdp_dump_ui заведомо не покажет (короткое
    замыкание на `if key in seen`) — пустая трата времени на каждый запуск
    после первого."""
    bare_r7._cdp_dump_seen = {"контекстное меню ячейки (копирование)"}
    connector = Mock()
    connector.connected = True
    connector.dump_visible_ui.return_value = []
    bare_r7._webdriver_connector = connector

    bare_r7._capture_cdp_ui_baseline()

    assert bare_r7._cdp_dump_seen == set()


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

def test_dump_with_empty_baseline_list_still_uses_diff_mode(bare_r7, log):
    """Регрессия для находки код-ревью PR #10: пустой, но УСПЕШНО снятый
    базовый снимок ([]) — это не то же самое, что «снимок не снимался»
    (None). `if baseline:` считал бы их одинаковыми (обе falsy) и молча
    откатывался на старый режим сплошного дампа, маскируя, что diff-режим
    реально включён и просто ничего не нашёл в базовом снимке."""
    connector = Mock()
    connector.connected = True
    connector.dump_visible_ui.return_value = [_item("Вставить")]
    bare_r7._webdriver_connector = connector
    bare_r7._cdp_ui_baseline = []

    bare_r7._cdp_dump_ui("метка", log_cb=log)

    assert any("новых элементов 1 из 1" in m for m in log.messages)
    assert not any("видимых элементов 1" in m for m in log.messages)


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


def test_dump_with_baseline_tolerates_small_position_jitter(bare_r7, log):
    """Тот же пункт, но popup чуть подрожал координатами (суб-пиксельный
    рендер того же элемента между снимками) — не должен считаться «новым»,
    пока сдвиг в пределах CDP_ITEM_POSITION_TOLERANCE_PX."""
    import r7_Testovarka as r7mod
    connector = Mock()
    connector.connected = True
    baseline_item = _item("Копировать", x=100, y=200)
    tol = r7mod.R7Testovarka.CDP_ITEM_POSITION_TOLERANCE_PX
    shifted_item = dict(baseline_item, x=100 + tol, y=200)
    connector.dump_visible_ui.return_value = [shifted_item]
    bare_r7._webdriver_connector = connector
    bare_r7._cdp_ui_baseline = [baseline_item]

    bare_r7._cdp_dump_ui("метка", log_cb=log)

    assert any("новых элементов нет" in m for m in log.messages)


def test_dump_with_baseline_treats_same_text_at_different_location_as_new(bare_r7, log):
    """Регрессия для находки код-ревью PR #10: одинаковый (tag, id, cls,
    text) — например, generic `<li id="" class="">` — но элемент реально
    сидит в другом месте экрана (overflow-меню тулбара vs настоящий пункт
    контекстного меню, ровно то, что запутало issue #9) должен считаться
    НОВЫМ, а не одним и тем же элементом. Строгое совпадение только по
    содержимому пряталось бы за такие коллизии."""
    connector = Mock()
    connector.connected = True
    baseline_item = _item("Условное форматирование", x=1676, y=534)  # тулбарная кнопка
    real_menu_item = _item("Условное форматирование", x=200, y=800)  # реальный пункт меню
    connector.dump_visible_ui.return_value = [real_menu_item]
    bare_r7._webdriver_connector = connector
    bare_r7._cdp_ui_baseline = [baseline_item]

    bare_r7._cdp_dump_ui("метка", log_cb=log)

    assert any("новых элементов 1" in m for m in log.messages)
    assert any("Условное форматирование" in m for m in log.messages)


def test_dump_respects_once_per_key_dedup_even_with_baseline(bare_r7, log):
    connector = Mock()
    connector.connected = True
    connector.dump_visible_ui.return_value = [_item("Вставить")]
    bare_r7._webdriver_connector = connector
    bare_r7._cdp_ui_baseline = []

    bare_r7._cdp_dump_ui("метка", log_cb=log)
    bare_r7._cdp_dump_ui("метка", log_cb=log)

    connector.dump_visible_ui.assert_called_once()
