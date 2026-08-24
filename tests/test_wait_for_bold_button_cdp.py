"""Тесты для _wait_for_bold_button_cdp — CDP-часть триггера готовности,
которую _wait_until_r7_ready пробует первой (см. CLAUDE.md, «Доп. триггер:
кнопка «Жирный»»). Коннектор полностью мокается: тесты не открывают
реальный CDP-порт и не ждут реальный Р7.
"""
from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    import r7_Testovarka as r7mod
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)


def test_returns_false_when_no_connector_created(bare_r7, log):
    bare_r7._webdriver_connector = None
    bare_r7._current_webdriver_port = None

    assert bare_r7._wait_for_bold_button_cdp(timeout=1.0, log_cb=log) is False
    assert any("CDP-коннектор не создан" in m for m in log.messages)


def test_returns_false_when_connect_fails(bare_r7, log):
    connector = Mock()
    connector.port = 8080
    connector.connect.return_value = False
    bare_r7._webdriver_connector = connector

    assert bare_r7._wait_for_bold_button_cdp(timeout=1.0, log_cb=log) is False
    assert any("CDP недоступен" in m for m in log.messages)


def test_returns_true_when_button_found_and_enabled(bare_r7, log):
    connector = Mock()
    connector.port = 8080
    connector.connect.return_value = True
    connector.bold_button_state.return_value = {"found": True, "disabled": False}
    bare_r7._webdriver_connector = connector

    assert bare_r7._wait_for_bold_button_cdp(timeout=1.0, log_cb=log) is True
    assert any("доступна" in m for m in log.messages)


def test_returns_false_when_button_stays_disabled_until_timeout(bare_r7, log, monkeypatch):
    import r7_Testovarka as r7mod
    connector = Mock()
    connector.port = 8080
    connector.connect.return_value = True
    connector.bold_button_state.return_value = {"found": True, "disabled": True}
    bare_r7._webdriver_connector = connector

    clock = {"t": 0.0}
    monkeypatch.setattr(r7mod.time, "time", lambda: clock["t"])

    def fake_sleep(seconds):
        clock["t"] += 10  # проматываем время быстрее timeout

    monkeypatch.setattr(r7mod.time, "sleep", fake_sleep)

    assert bare_r7._wait_for_bold_button_cdp(timeout=1.0, log_cb=log) is False
    assert any("не стала доступна" in m for m in log.messages)


def test_returns_false_when_button_not_found(bare_r7, log, monkeypatch):
    import r7_Testovarka as r7mod
    connector = Mock()
    connector.port = 8080
    connector.connect.return_value = True
    connector.bold_button_state.return_value = None  # кнопка не найдена в DOM
    bare_r7._webdriver_connector = connector

    clock = {"t": 0.0}
    monkeypatch.setattr(r7mod.time, "time", lambda: clock["t"])
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + 10))

    assert bare_r7._wait_for_bold_button_cdp(timeout=1.0, log_cb=log) is False


def test_catches_exceptions_and_returns_false(bare_r7, log):
    connector = Mock()
    connector.port = 8080
    connector.connect.side_effect = RuntimeError("boom")
    bare_r7._webdriver_connector = connector

    assert bare_r7._wait_for_bold_button_cdp(timeout=1.0, log_cb=log) is False
    assert any("CDP недоступен, использую fallback" in m for m in log.messages)


def test_connect_timeout_capped_even_with_large_overall_timeout(bare_r7, log):
    """Регрессия: раньше подключение получало до 2 с даже когда порт
    заведомо закрыт (сборка Р7 без реального CDP) — это время инфлировало
    замер «Открытие файла», т.к. вызывается уже после совпадения остальных
    признаков готовности. Подключение должно быть ограничено
    BOLD_BUTTON_CDP_CONNECT_TIMEOUT_SEC независимо от общего timeout."""
    connector = Mock()
    connector.port = 8080
    connector.connect.return_value = True
    connector.bold_button_state.return_value = {"found": True, "disabled": False}
    bare_r7._webdriver_connector = connector

    bare_r7._wait_for_bold_button_cdp(timeout=3.0, log_cb=log)

    used_timeout = connector.connect.call_args.kwargs["timeout"]
    assert used_timeout <= bare_r7.BOLD_BUTTON_CDP_CONNECT_TIMEOUT_SEC


def test_connect_timeout_respects_smaller_overall_timeout(bare_r7, log):
    connector = Mock()
    connector.port = 8080
    connector.connect.return_value = False
    bare_r7._webdriver_connector = connector

    bare_r7._wait_for_bold_button_cdp(timeout=0.2, log_cb=log)

    used_timeout = connector.connect.call_args.kwargs["timeout"]
    assert used_timeout == pytest.approx(0.2)
