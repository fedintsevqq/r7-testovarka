"""Мелкие находки аудита проглоченных ошибок (06.10.2026)."""
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod


# ── OpResourceWatch: без процессов — не «0 % CPU», а «нет данных» ───────

def test_resource_watch_without_processes_returns_none():
    watch = r7mod.OpResourceWatch(lambda: [], interval=0.01)
    watch.start()
    assert watch.stop() is None


def test_resource_watch_get_procs_error_returns_none():
    def boom():
        raise RuntimeError("psutil")

    watch = r7mod.OpResourceWatch(boom, interval=0.01)
    watch.start()
    assert watch.stop() is None


def test_aggregate_skips_empty_windows(bare_r7):
    agg = bare_r7._aggregate_op_resources([None, None], [0, 1])
    assert agg["cpu_sec"] is None and agg["cpu"] is None


# ── X2tTracker: код выхода не прочитался — процесс ещё не «завершён» ─────

class _Proc:
    def __init__(self, running):
        self.running = running

    def cpu_times(self):
        return Mock(user=1.0, system=0.5)

    def io_counters(self):
        return Mock(read_bytes=0, write_bytes=0)

    def is_running(self):
        return self.running


@pytest.fixture
def tracker(monkeypatch):
    monkeypatch.setattr(r7mod.env, "WIN32_OK", True)
    monkeypatch.setattr(r7mod.psutil, "pids", lambda: [777])
    monkeypatch.setattr("win32process.GetExitCodeProcess",
                        Mock(side_effect=OSError("access denied")))
    monkeypatch.setattr("win32api.CloseHandle", lambda h: None)
    log = []
    t = r7mod.X2tTracker(log_cb=log.append)
    t._known = {777}
    return t, log


def _add_active(t, running):
    run = {"pid": 777, "start": 0.0, "end": None, "exit_code": None, "cpu_sec": None,
           "io_read_mb": None, "io_write_mb": None}
    t.runs.append(run)
    t._active[777] = (run, 12345, _Proc(running))
    return run


def test_unreadable_exit_code_of_live_x2t_keeps_it_running(tracker):
    t, log = tracker
    run = _add_active(t, running=True)
    t._poll()
    assert run["end"] is None and 777 in t._active
    assert log == []


def test_finished_x2t_with_unknown_code_is_not_reported_as_success(tracker):
    t, log = tracker
    run = _add_active(t, running=False)
    t._poll()
    assert run["end"] is not None and run["exit_code"] is None
    assert any("код выхода неизвестен" in m for m in log)
    assert not any(m.lstrip().startswith("🔧 x2t завершён") for m in log)


# ── _wait_renderer_idle: мгновенный отказ пинга — с паузой ─────────────

class _FailingPing:
    """Пинг мгновенно отвечает «нет» n раз, соединение формально живо."""
    connected = True

    def __init__(self, fails, sleeps):
        self.fails = fails
        self.sleeps = sleeps
        self.sleeps_before_success = None

    def ping(self, timeout=None):
        if self.fails:
            self.fails -= 1
            return False
        if self.sleeps_before_success is None:
            self.sleeps_before_success = len(self.sleeps)
        return True


def test_instant_ping_failures_do_not_spin(bare_r7, monkeypatch):
    clock = {"t": 100.0}
    sleeps = []
    monkeypatch.setattr(r7mod.time, "perf_counter", lambda: clock["t"])

    def sleep(s):
        sleeps.append(s)
        clock["t"] += s

    monkeypatch.setattr(r7mod.time, "sleep", sleep)
    bare_r7._op_max_wait = None
    bare_r7._dismiss_heavy_calc_prompt = lambda log_cb=None: False
    ping = _FailingPing(fails=5, sleeps=sleeps)
    bare_r7._webdriver_connector = ping

    end, status = bare_r7._wait_renderer_idle(lambda m: None)

    assert status == "ok"
    assert ping.sleeps_before_success == 5          # пауза после каждого отказа
