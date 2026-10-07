"""Маркер готовности «Жирный» (r7.bold_button) — прямые тесты на
поддельных win32gui, часах и CDP-коннекторе (plan-to-10, шаг 3)."""
import sys
import types
from types import SimpleNamespace

import pytest

import r7_Testovarka as r7mod
import r7.bold_button as bb

R = r7mod.R7Testovarka


class _Clock:
    def __init__(self):
        self.t = 100.0

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(bb, "time", SimpleNamespace(perf_counter=c.perf_counter, sleep=c.sleep))
    return c


@pytest.fixture
def app():
    a = R.__new__(R)
    a._webdriver_connector = None
    a._current_webdriver_port = None
    return a


@pytest.fixture
def gui(monkeypatch):
    """Дерево дочерних окон: hwnd → (класс, текст); enabled — множество hwnd."""
    state = SimpleNamespace(children={}, enabled=set(), enum_error=False)

    def enum(parent, cb, extra):
        if state.enum_error:
            raise OSError("окно закрылось")
        for h in state.children:
            cb(h, extra)

    def cls(h):
        if state.children[h] is None:
            raise OSError("окно исчезло")
        return state.children[h][0]
    fake = types.SimpleNamespace(
        EnumChildWindows=enum, GetClassName=cls,
        GetWindowText=lambda h: state.children[h][1],
        IsWindowEnabled=lambda h: h in state.enabled)
    monkeypatch.setitem(sys.modules, "win32gui", fake)
    monkeypatch.setattr(r7mod.env, "WIN32_OK", True)
    return state


# ── win32gui-путь ────────────────────────────────────────────────────────

def test_find_bold_button_by_class_and_mnemonic_label(app, gui):
    gui.children = {1: ("Static", "Ж"), 2: None, 3: ("ToolbarButton", "&Ж "), 4: ("Button", "b")}
    assert app._find_bold_button_hwnd(10) == 3            # первая подходящая, «&» снят


def test_find_bold_button_none_without_match_or_win32(app, gui, monkeypatch):
    gui.children = {1: ("Button", "Курсив")}
    assert app._find_bold_button_hwnd(10) is None
    gui.enum_error = True
    assert app._find_bold_button_hwnd(10) is None
    monkeypatch.setattr(r7mod.env, "WIN32_OK", False)
    assert app._find_bold_button_hwnd(10) is None
    assert app._is_bold_button_visible(10) is False


def test_bold_button_visible_only_when_enabled(app, gui):
    gui.children = {3: ("Button", "bold")}
    assert app._is_bold_button_visible(10) is False
    gui.enabled.add(3)
    assert app._is_bold_button_visible(10) is True


def test_wait_for_bold_button_returns_at_once_when_absent(app, gui, clock):
    assert app._wait_for_bold_button(10, timeout=5) is False
    assert clock.t == 100.0                               # таймаут не тратится


def test_wait_for_bold_button_until_enabled_or_timeout(app, gui, clock, monkeypatch):
    gui.children = {3: ("Button", "жирный")}
    assert app._wait_for_bold_button(10, timeout=0.5) is False
    assert clock.t >= 100.5
    orig_sleep = clock.sleep

    def enable_later(s):
        orig_sleep(s)
        if clock.t >= 101.2:
            gui.enabled.add(3)
    monkeypatch.setattr(bb.time, "sleep", enable_later)
    assert app._wait_for_bold_button(10) is True          # по умолчанию BOLD_BUTTON_TIMEOUT_SEC


# ── CDP ──────────────────────────────────────────────────────────────────

class _Conn:
    def __init__(self, connects=True, states=(), raises=None, connected=False):
        self.port, self.connected = 8080, connected
        self._connects, self._states, self._raises = connects, list(states), raises
        self.connect_calls = []

    def connect(self, timeout):
        self.connect_calls.append(timeout)
        if self._raises:
            raise self._raises
        return self._connects

    def bold_button_state(self):
        return self._states.pop(0) if self._states else {"found": True, "disabled": True}

    def bold_ready_probe(self, timeout):
        if self._raises:
            raise self._raises
        return {"ready": True, "timeout": timeout}


def test_cdp_wait_without_connector_logs_and_skips(app, clock):
    logs = []
    assert app._wait_for_bold_button_cdp(2.0, logs.append) is False
    assert "коннектор не создан" in logs[0]


def test_cdp_wait_port_closed(app, clock):
    app._webdriver_connector = conn = _Conn(connects=False)
    logs = []
    assert app._wait_for_bold_button_cdp(2.0, logs.append) is False
    assert conn.connect_calls == [R.BOLD_BUTTON_CDP_CONNECT_TIMEOUT_SEC]   # короткий таймаут
    assert any("fallback" in m for m in logs)


def test_cdp_wait_button_becomes_enabled(app, clock):
    app._webdriver_connector = _Conn(states=[None, {"found": False},
                                             {"found": True, "disabled": True},
                                             {"found": True, "disabled": False}])
    logs = []
    assert app._wait_for_bold_button_cdp(5.0, logs.append) is True
    assert any("доступна (CDP" in m for m in logs)


def test_cdp_wait_times_out_and_survives_errors(app, clock):
    app._webdriver_connector = _Conn()
    logs = []
    assert app._wait_for_bold_button_cdp(0.3, logs.append) is False
    assert "не стала доступна" in logs[-1]
    app._webdriver_connector = _Conn(raises=RuntimeError("ws оборвался"))
    assert app._wait_for_bold_button_cdp(1.0, logs.append) is False
    assert "RuntimeError" in logs[-1]


def test_early_connector_throttles_attempts(app, clock):
    assert app._early_connector() is None                 # коннектора нет
    app._webdriver_connector = conn = _Conn(connects=False)
    assert app._early_connector() is None
    assert app._early_connector() is None                 # раньше секунды — без попытки
    assert len(conn.connect_calls) == 1
    clock.t += 1.0
    conn._connects = True
    assert app._early_connector() is conn
    app._webdriver_connector = already = _Conn(connected=True)
    assert app._early_connector() is already and already.connect_calls == []
    app._webdriver_connector = broken = _Conn(raises=OSError("нет порта"))
    clock.t += 2.0
    assert app._early_connector() is None and broken.connect_calls


def test_bold_ready_probe(app, clock):
    assert app._bold_ready_probe() is None
    app._webdriver_connector = _Conn(connected=True)
    assert app._bold_ready_probe() == {"ready": True, "timeout": R.BOLD_PROBE_TIMEOUT_SEC}
    app._webdriver_connector = _Conn(connected=True, raises=RuntimeError("занят"))
    assert app._bold_ready_probe() is None
    app._webdriver_connector = SimpleNamespace(connected=True)   # старый коннектор без пробы
    assert app._bold_ready_probe() is None
