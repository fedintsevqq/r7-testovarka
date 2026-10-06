"""Предусловия живого прогона в CI (tests/ci_preflight.py)."""
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import ci_preflight  # tests/ в sys.path (pytest, режим prepend)


@pytest.fixture
def stand(monkeypatch):
    env = {"session": 1, "procs": [], "path": r"E:\R7\DesktopEditors.exe"}
    monkeypatch.setattr(ci_preflight, "_session_id", lambda: env["session"])
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", True)
    monkeypatch.setattr(r7mod.R7Testovarka, "_get_r7_processes",
                        lambda self, log_cb=None, fresh=False: env["procs"])
    monkeypatch.setattr(r7mod.R7Testovarka, "_find_r7_path", lambda self: env["path"])
    return env


def test_ready_stand(stand):
    assert ci_preflight.check(log=lambda m: None) == []


def test_service_session_refused(stand):
    stand["session"] = 0
    assert any("сессии 0" in p for p in ci_preflight.check(log=lambda m: None))


def test_running_r7_refused(stand):
    stand["procs"] = [Mock(pid=14868)]
    assert any("14868" in p for p in ci_preflight.check(log=lambda m: None))


def test_missing_cdp_refused(stand, monkeypatch):
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", False)
    assert any("CDP" in p for p in ci_preflight.check(log=lambda m: None))


def test_missing_r7_refused(stand):
    stand["path"] = None
    assert any("не найден" in p for p in ci_preflight.check(log=lambda m: None))
