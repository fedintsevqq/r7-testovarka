"""Сбои, которые раньше проходили молча (аудит 06.10.2026)."""
from unittest.mock import Mock

import r7_Testovarka as r7mod


def _app(connector, log):
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app._early_connector = lambda: connector
    app._cdp_ops_connector = lambda: connector
    app.add_test_log = log.append
    return app


def test_heavy_calc_check_exception_logged_once_per_connector():
    log = []
    c = Mock()
    c.dismiss_heavy_calc_prompt.side_effect = RuntimeError("ws died")
    app = _app(c, log)
    assert app._dismiss_heavy_calc_prompt() is False
    assert app._dismiss_heavy_calc_prompt() is False
    assert sum("проверить не удалось" in m for m in log) == 1


def test_heavy_calc_timeout_is_not_logged():
    """None — таймаут занятого рендерера (норма при открытии), не сбой."""
    log = []
    c = Mock()
    c.dismiss_heavy_calc_prompt.return_value = None
    assert _app(c, log)._dismiss_heavy_calc_prompt() is False
    assert log == []


def test_heavy_calc_no_modal_is_quiet():
    log = []
    c = Mock()
    c.dismiss_heavy_calc_prompt.return_value = {"clicked": False}
    assert _app(c, log)._dismiss_heavy_calc_prompt() is False
    assert log == []


def test_autosave_not_suspended_is_logged():
    for outcome in (None, {}, RuntimeError("ws")):
        log = []
        c = Mock()
        if isinstance(outcome, Exception):
            c.suspend_autosave.side_effect = outcome
        else:
            c.suspend_autosave.return_value = outcome
        app = _app(c, log)
        app._suspend_autosave()
        assert app._autosave_state is None
        assert any("не отключено" in m for m in log), outcome


def test_autosave_suspended_is_not_a_warning():
    log = []
    c = Mock()
    c.suspend_autosave.return_value = {"gap_ms": 1000, "periodic": True}
    app = _app(c, log)
    app._suspend_autosave()
    assert app._autosave_state == {"gap_ms": 1000, "periodic": True}
    assert not any("не отключено" in m for m in log)


def test_heavy_calc_failure_logged_again_for_new_connector():
    """Раз на соединение: новый прогон (новый коннектор) предупреждает снова."""
    log = []
    first, second = Mock(), Mock()
    for c in (first, second):
        c.dismiss_heavy_calc_prompt.side_effect = RuntimeError("ws died")
    app = _app(first, log)
    app._dismiss_heavy_calc_prompt()
    app._early_connector = lambda: second
    app._dismiss_heavy_calc_prompt()
    assert sum("проверить не удалось" in m for m in log) == 2


def test_heavy_calc_clicked_still_works():
    log = []
    c = Mock()
    c.dismiss_heavy_calc_prompt.return_value = {"clicked": True, "waited_ms": 1500}
    app = _app(c, log)
    assert app._dismiss_heavy_calc_prompt() is True
    assert app._last_prompt_wait_sec == 1.5
