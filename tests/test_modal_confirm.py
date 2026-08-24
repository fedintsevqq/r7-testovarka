"""Тесты для _pace / _confirm_modal_enter / _flush_pending_modal_confirm —
учёт времени модалки «Вставить ячейки» в замере (см. CLAUDE.md,
раздел «Модалка «Вставить ячейки»»).
"""
from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """_pace()/flush используют time.sleep — не ждать реальные OP_DIALOG_PACE
    (0.6с) в каждом тесте. _paced_total по-прежнему считается по time.time(),
    поэтому подменяем и его на детерминированные тики."""
    import r7_Testovarka as r7mod

    clock = {"t": 1000.0}

    def fake_sleep(seconds):
        clock["t"] += seconds

    def fake_time():
        return clock["t"]

    monkeypatch.setattr(r7mod.time, "sleep", fake_sleep)
    monkeypatch.setattr(r7mod.time, "time", fake_time)


@pytest.fixture(autouse=True)
def _mock_pyautogui(monkeypatch):
    import r7_Testovarka as r7mod
    monkeypatch.setattr(r7mod.pyautogui, "press", Mock())
    return r7mod.pyautogui.press


def test_pace_accumulates_paced_total(bare_r7):
    bare_r7._pace(0.6)
    assert bare_r7._paced_total == pytest.approx(0.6)


def test_pace_noop_for_nonpositive_seconds(bare_r7):
    bare_r7._pace(0)
    bare_r7._pace(-1)
    assert bare_r7._paced_total == 0.0


def test_confirm_modal_enter_paces_and_sets_pending_flag(bare_r7):
    bare_r7._confirm_modal_enter()

    assert bare_r7._paced_total == pytest.approx(bare_r7.OP_DIALOG_PACE)
    assert bare_r7._pending_modal_confirm is True


def test_confirm_modal_enter_presses_enter_once(bare_r7, _mock_pyautogui):
    bare_r7._confirm_modal_enter()
    _mock_pyautogui.assert_called_once_with("enter")


def test_confirm_modal_enter_accepts_custom_pace(bare_r7):
    bare_r7._confirm_modal_enter(pace=0.1)
    assert bare_r7._paced_total == pytest.approx(0.1)


def test_flush_pending_modal_confirm_noop_when_nothing_pending(bare_r7, log, _mock_pyautogui):
    bare_r7._flush_pending_modal_confirm(log_cb=log)
    _mock_pyautogui.assert_not_called()
    assert bare_r7._paced_total == 0.0


def test_flush_sends_remaining_attempts_outside_paced_total(bare_r7, log, _mock_pyautogui):
    """Регрессия: страховочные Enter'ы после первого — ВНЕ замера. Если бы они
    шли через _pace(), у коротких операций (< OP_DIALOG_ATTEMPTS * OP_DIALOG_PACE)
    результат уезжал бы в 0/below_floor (см. CLAUDE.md)."""
    bare_r7._confirm_modal_enter()          # 1-й Enter, ВНУТРИ замера
    paced_after_confirm = bare_r7._paced_total

    bare_r7._flush_pending_modal_confirm(log_cb=log)

    assert bare_r7._paced_total == paced_after_confirm   # flush не трогает _paced_total
    assert _mock_pyautogui.call_count == 1 + (bare_r7.OP_DIALOG_ATTEMPTS - 1)
    assert bare_r7._pending_modal_confirm is False


def test_flush_dumps_dom_diagnostics_once(bare_r7, log):
    bare_r7._pending_modal_confirm = True
    bare_r7._cdp_dump_ui = Mock()

    bare_r7._flush_pending_modal_confirm(log_cb=log)

    bare_r7._cdp_dump_ui.assert_called_once()


def test_flush_logs_warning_and_stops_on_pyautogui_failure(bare_r7, log, monkeypatch):
    import r7_Testovarka as r7mod
    bare_r7._pending_modal_confirm = True
    bare_r7._cdp_dump_ui = Mock()
    monkeypatch.setattr(r7mod.pyautogui, "press",
                         Mock(side_effect=RuntimeError("no display")))

    bare_r7._flush_pending_modal_confirm(log_cb=log)

    assert any("Не удалось дослать Enter" in m for m in log.messages)
