"""Ожидание конца операции по шагам (r7.op_wait) — на поддельных часах и
процессах: когда операция считается законченной и какой момент берётся."""
from types import SimpleNamespace

import pytest

import r7_Testovarka as r7mod
import r7.op_wait as ow

R = r7mod.R7Testovarka


class _Clock:
    def __init__(self):
        self.t = 100.0

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Proc:
    """Процесс Р7: cpu — функция от времени (% одного ядра)."""

    def __init__(self, pid, name, cpu, clock, exits_at=None):
        self.pid, self._name, self._cpu, self.clock, self.exits_at = pid, name, cpu, clock, exits_at

    def name(self):
        return self._name

    def cpu_percent(self, interval=None):
        return self._cpu(self.clock.t)

    def is_running(self):
        return self.exits_at is None or self.clock.t < self.exits_at


@pytest.fixture
def env(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(ow, "time", SimpleNamespace(perf_counter=clock.perf_counter,
                                                    sleep=clock.sleep))
    monkeypatch.setattr(ow, "_is_crash_snapshot", lambda p: False)
    monkeypatch.setattr(r7mod.env, "WIN32_OK", False)
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", True)
    app = SimpleNamespace(**{n: getattr(R, n) for n in dir(R) if n.startswith("OP_")},
                          HEAVY_CALC_CHECK_SEC=R.HEAVY_CALC_CHECK_SEC,
                          _paced_total=0.0, _r7_pids=None)
    app.procs = []
    app.responsive = lambda t: True
    app._get_r7_processes = lambda log_cb=None: list(app.procs)
    app._window_responsive = lambda h, ms: app.responsive(clock.t)
    app._dismiss_heavy_calc_prompt = lambda log_cb: False
    logs = []
    return SimpleNamespace(app=app, clock=clock, logs=logs)


def _run(e, grace=1.0, max_wait=30.0, hwnd=1):
    return ow.OpWait(e.app, hwnd, e.logs.append, grace, max_wait).run()


def test_cpu_burst_then_idle_ends_at_start_of_idle_window(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 150.0 if t < 102 else 1.0, env.clock)]
    end, status = _run(env)
    assert status == "ok"
    # конец — начало CPU-окна, где загрузка упала; погрешность — одно окно
    assert 102.0 - R.OP_CPU_WINDOW_SEC <= end <= 102.0 + R.OP_CPU_WINDOW_SEC


def test_never_busy_is_below_floor_at_start(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 0.0, env.clock)]
    end, status = _run(env, grace=0.5)
    assert (end, status) == (100.0, "below_floor")
    assert env.clock.t >= 100.5


def test_single_cpu_spike_is_background_not_operation(env):
    """Одно окно выше OP_BUSY_CORE_PCT, но ниже сильного порога — фон Р7."""
    spike = lambda t: 40.0 if 100.15 <= t < 100.35 else 0.0
    env.app.procs = [_Proc(1, "editors.exe", spike, env.clock)]
    assert _run(env, grace=1.0)[1] == "below_floor"


def test_strong_cpu_window_counts_as_busy_at_once(env):
    strong = lambda t: R.OP_BUSY_STRONG_CORE_PCT + 1 if 100.15 <= t < 100.35 else 0.0
    env.app.procs = [_Proc(1, "editors.exe", strong, env.clock)]
    assert _run(env)[1] == "ok"


def test_unresponsive_window_is_busy_and_bounds_idle_start(env):
    env.app.responsive = lambda t: t >= 103.0
    end, status = _run(env)
    assert status == "ok"
    assert end == pytest.approx(103.0, abs=R.OP_POLL_SEC + 1e-6)


def test_live_x2t_keeps_operation_open(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 0.0, env.clock),
                     _Proc(2, "x2t.exe", lambda t: 0.0, env.clock, exits_at=104.0)]
    end, status = _run(env, grace=6.0)
    assert status == "ok" and end == pytest.approx(104.0, abs=R.OP_POLL_SEC + 1e-6)


def test_timeout_logs_and_returns_none(env):
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 200.0, env.clock)]
    assert _run(env, max_wait=2.0) == (None, "timeout")
    assert any("не освободился за 2 сек" in m for m in env.logs)


def test_heavy_calc_prompt_wait_goes_to_paced_total(env):
    """Модалка пересчёта: время до ответа — собственная пауза, замер не закрыт."""
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 150.0 if t < 101 or 101.05 <= t < 103 else 0.0,
                           env.clock)]
    answered = []

    def dismiss(log_cb):
        if not answered and env.clock.t >= 101.0:
            answered.append(env.clock.t)
            env.app._last_prompt_wait_sec = 0.4
            return True
        return False
    env.app._dismiss_heavy_calc_prompt = dismiss
    end, status = _run(env)
    assert answered and env.app._paced_total == pytest.approx(0.4)
    assert status == "ok" and end > 102.5        # закрыт уже после пересчёта


def test_callable_hwnd_resolved_once_when_win32_missing(env):
    calls = []
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 0.0, env.clock)]
    _run(env, grace=0.3, hwnd=lambda: calls.append(1) or 7)
    assert calls                                # окно найдено функцией поиска


def test_callable_hwnd_kept_while_window_alive_and_reresolved_when_gone(env, monkeypatch):
    """С pywin32 окно перерешается, только когда прежнее перестало быть окном
    (IsWindow через r7.windows)."""
    import r7.windows as r7windows
    alive = {7}
    monkeypatch.setattr(r7mod.env, "WIN32_OK", True)
    monkeypatch.setattr(r7windows, "win32gui", SimpleNamespace(IsWindow=lambda h: h in alive))
    calls = []
    env.app.procs = [_Proc(1, "editors.exe", lambda t: 0.0, env.clock)]
    _run(env, grace=0.3, hwnd=lambda: calls.append(1) or 7)
    assert len(calls) == 1                      # окно живо — повторно не ищется
    alive.clear()
    calls.clear()
    _run(env, grace=0.3, hwnd=lambda: calls.append(1) or 7)
    assert len(calls) > 1                       # окна нет — ищется на каждом шаге


def test_wait_operation_done_uses_cdp_tail_grace(monkeypatch):
    """На CDP-пути без пинга окно старта — OP_CDP_TAIL_GRACE_SEC."""
    app = R.__new__(R)
    app._op_via_cdp, app._op_start_grace, app._op_max_wait = True, None, None
    app._wait_renderer_idle = lambda log_cb: None
    seen = {}

    class _W:
        def __init__(self, a, hwnd, log_cb, start_grace, max_wait):
            seen.update(grace=start_grace, max_wait=max_wait)

        def run(self):
            return 1.0, "ok"
    monkeypatch.setattr("r7.op_end.OpWait", _W)
    assert app._wait_operation_done(1, log_cb=lambda m: None) == (1.0, "ok")
    assert seen == {"grace": R.OP_CDP_TAIL_GRACE_SEC, "max_wait": R.OP_MAX_WAIT_SEC}
    app._op_via_cdp, app._op_max_wait = False, 20
    app._wait_operation_done(1, log_cb=lambda m: None)
    assert seen == {"grace": R.OP_START_GRACE_SEC, "max_wait": 20}
