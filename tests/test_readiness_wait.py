"""Ожидание готовности по шагам (r7.readiness_wait) — на поддельных часах и
процессах: какие пути объявляют готовность и какой момент берут."""
from types import SimpleNamespace

import pytest

import r7_Testovarka as r7mod
import r7.readiness_wait as rw

R = r7mod.R7Testovarka


class _Clock:
    def __init__(self):
        self.t = 100.0

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Proc:
    """Процесс Р7: cpu — функция от времени (возвращает % ядра)."""

    def __init__(self, pid, name, cpu, clock, dies_at=None):
        self.pid, self._name, self._cpu, self.clock, self.dies_at = pid, name, cpu, clock, dies_at

    def name(self):
        return self._name

    def cpu_percent(self, interval=None):
        if self.dies_at is not None and self.clock.t >= self.dies_at:
            raise rw.psutil.NoSuchProcess(self.pid)
        return self._cpu(self.clock.t)

    def num_threads(self):
        return 10

    def parent(self):
        return None


@pytest.fixture
def env(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(rw, "time", SimpleNamespace(perf_counter=clock.perf_counter,
                                                    sleep=clock.sleep))
    monkeypatch.setattr(r7mod.env, "WIN32_OK", False)
    logs = []
    app = SimpleNamespace(
        READY_POLL_SEC=R.READY_POLL_SEC, READY_PROC_REFRESH_SEC=R.READY_PROC_REFRESH_SEC,
        READY_IDLE_CORE_PCT=R.READY_IDLE_CORE_PCT, READY_IDLE_SAMPLES=R.READY_IDLE_SAMPLES,
        READY_MIN_BUSY_SEC=R.READY_MIN_BUSY_SEC, HEAVY_CALC_CHECK_SEC=R.HEAVY_CALC_CHECK_SEC,
        BOLD_STABLE_SEC=R.BOLD_STABLE_SEC, BOLD_BUTTON_TIMEOUT_SEC=R.BOLD_BUTTON_TIMEOUT_SEC,
        READY_ESC_WITHOUT_CDP=True, _ready_at=None, _ready_marker=None, _r7_pids=None)
    app.procs = []
    app._get_r7_processes = lambda log_cb=None: list(app.procs)
    app._window_responsive = lambda h: True
    app._bold_ready_probe = lambda: None
    app._dismiss_heavy_calc_prompt = lambda log_cb: False
    app._wait_for_bold_button = lambda h, timeout: False
    app._find_bold_button_hwnd = lambda h: None
    app._early_connector = lambda: None
    app._press_esc_in_r7 = lambda h: False
    return SimpleNamespace(app=app, clock=clock, logs=logs)


def _run(e, timeout=30):
    return rw.ReadinessWait(e.app, None, timeout, e.logs.append).run()


def test_cpu_path_ready_at_start_of_idle(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 300.0 if t < 105 else 2.0, env.clock)]
    assert _run(env) is True
    assert env.app._ready_marker == "cpu"
    assert env.app._ready_at == pytest.approx(105.0, abs=R.READY_POLL_SEC + 1e-6)


def test_r7_crash_during_open(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 300.0, env.clock, dies_at=103.0)]
    assert _run(env) is False
    assert any("исчезли" in m for m in env.logs)


def test_timeout_mentions_disabled_bold_button(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 1.0, env.clock)]
    env.app._bold_ready_probe = lambda: {"found": True, "disabled": True}
    assert _run(env, timeout=5) is False
    assert env.app._ready_marker == "timeout"
    assert any("так и не стала доступной" in m for m in env.logs)


def test_win32_bold_button_declares_ready_at_idle_start(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 300.0 if t < 104 else 1.0, env.clock)]
    env.app._wait_for_bold_button = lambda h, timeout: True
    assert _run(env) is True and env.app._ready_marker == "win32_bold"


def test_win32_bold_found_but_disabled_logs_fallback(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 300.0 if t < 104 else 1.0, env.clock)]
    env.app._find_bold_button_hwnd = lambda h: 42
    assert _run(env) is True and env.app._ready_marker == "cpu"
    assert any("найдена, но не стала доступна" in m for m in env.logs)


def test_esc_without_cdp_prompt_was_there(env):
    """После Esc Р7 занялся работой — модалка была: ожидание ответа вычтено,
    готовность — конец пересчёта (маркер cpu_esc)."""
    state = {"busy_from": None}

    def cpu(t):
        if t < 104:
            return 300.0
        if state["busy_from"] is not None and t < state["busy_from"] + 2.0:
            return 300.0                      # пересчёт после ответа «Нет»
        return 1.0

    def press(h):
        state["busy_from"] = env.clock.t
        return True
    env.app.procs = [_Proc(1, "editors.exe", cpu, env.clock)]
    env.app._press_esc_in_r7 = press
    assert _run(env) is True and env.app._ready_marker == "cpu_esc"
    assert any("была модалка" in m for m in env.logs)


def test_esc_without_cdp_no_prompt_keeps_first_idle(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 300.0 if t < 104 else 1.0, env.clock)]
    env.app._press_esc_in_r7 = lambda h: True
    assert _run(env) is True and env.app._ready_marker == "cpu"
    assert env.app._ready_at == pytest.approx(104.0, abs=R.READY_POLL_SEC + 1e-6)


def test_converter_alive_blocks_ready(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 1.0, env.clock),
                     _Proc(2, "x2t.exe", lambda t: 0.0, env.clock, dies_at=110.0)]
    assert _run(env) is True
    assert env.app._ready_at >= 110.0 - R.READY_POLL_SEC     # пока жив x2t — не готов


def test_without_psutil_uses_window_only(env):
    env.app._window_responsive = lambda h: env.clock.t >= 102.0
    assert rw.wait_without_psutil(env.app, None, 110.0, env.logs.append) is True
    assert env.app._ready_marker == "responsive_only"
    env.app._window_responsive = lambda h: False
    assert rw.wait_without_psutil(env.app, None, env.clock.t + 1, env.logs.append) is False


def test_callable_hwnd_resolved_when_window_gone(env, monkeypatch):
    monkeypatch.setattr(r7mod.env, "WIN32_OK", True)
    monkeypatch.setattr(rw, "win32gui", SimpleNamespace(IsWindow=lambda h: False))
    calls = []
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 300.0 if t < 103 else 1.0, env.clock)]
    assert rw.ReadinessWait(env.app, lambda: calls.append(1) or 7, 30, env.logs.append).run()
    assert calls                                   # окно перерешалось
