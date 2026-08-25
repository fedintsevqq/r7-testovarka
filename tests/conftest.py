"""Общие фикстуры для тестов R7-Testovarka.

Тесты не требуют живого Р7-Офис и не должны запускать реальные win32-вызовы
или сетевые запросы — все внешние зависимости (win32gui/win32con/win32process,
psutil, requests, websocket, pyautogui) мокаются через monkeypatch.

r7_Testovarka.py — Tkinter-приложение, но методы, покрытые здесь, не трогают
Tk-виджеты напрямую (используют переданный log_cb вместо self.add_test_log).
Поэтому вместо полноценного экземпляра (который создал бы окно) используется
"голый" объект через R7Testovarka.__new__ — он даёт доступ к методам и
классовым константам (OP_*, READY_* и т.д.), не вызывая __init__/Tk.
"""
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import r7_Testovarka as r7mod  # noqa: E402
import r7_webdriver_connector as wdmod  # noqa: E402


@pytest.fixture
def log():
    """Простой log_cb, копящий сообщения в список — вместо self.add_test_log."""
    messages = []

    def _log(msg):
        messages.append(msg)

    _log.messages = messages
    return _log


@pytest.fixture
def bare_r7():
    """"Голый" экземпляр R7Testovarka без Tk: только методы и константы класса.

    Атрибуты, которые обычно выставляет __init__, задаются вручную — ровно
    те, что нужны методам под тестом (_paced_total, _pending_modal_confirm,
    _webdriver_connector и т.п.). Метод не создаёт никакого окна.
    """
    inst = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    inst._paced_total = 0.0
    inst._pending_modal_confirm = False
    inst._webdriver_connector = None
    inst._current_webdriver_port = None
    inst._cdp_dump_seen = set()
    inst._cdp_ui_baseline = None
    inst._pending_cdp_verify = None
    inst._op_via_cdp = False
    inst._r7_pids = None
    return inst


@pytest.fixture
def connector():
    """Неподключённый R7WebDriverConnector с замоканным log_cb."""
    return wdmod.R7WebDriverConnector(port=8080, log_cb=Mock())


@pytest.fixture
def fake_process():
    """Фабрика фейковых psutil.Process — только методы, нужные _terminate_r7_processes."""
    def _make(pid=1234, name="editors_helper.exe", alive_after_terminate=False):
        p = Mock()
        p.pid = pid
        p.name.return_value = name
        p.terminate = Mock()
        p.kill = Mock()
        p._alive_after_terminate = alive_after_terminate
        return p
    return _make
