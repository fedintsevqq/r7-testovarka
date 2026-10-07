"""Ресурсы Р7 (r7.resources) — прямые тесты на поддельном psutil
(plan-to-10, шаг 3): снимок и разница диска, сводка RAM/CPU по процессам,
строки журнала, процессорное время Р7."""
from types import SimpleNamespace

import psutil
import pytest

import r7_Testovarka as r7mod
import r7.resources as res

R = r7mod.R7Testovarka
MB = 2 ** 20


class _P:
    """Процесс: поля — значения или исключения (процесс умер)."""

    def __init__(self, pid=1, name="editors.exe", rss=100 * MB, cpu=50.0, threads=10,
                 created=1000.0, io=(0, 0), times=(1.0, 0.5), dead=()):
        self.pid, self.info = pid, {"name": name}
        self._rss, self._cpu, self._threads, self._created = rss, cpu, threads, created
        self._io, self._times, self._dead = io, times, set(dead)

    def _check(self, what):
        if what in self._dead:
            raise psutil.NoSuchProcess(self.pid)

    def memory_info(self):
        self._check("memory")
        return SimpleNamespace(rss=self._rss)

    def cpu_percent(self, interval=None):
        self._check("cpu")
        return self._cpu

    def num_threads(self):
        self._check("threads")
        return self._threads

    def create_time(self):
        self._check("create")
        return self._created

    def io_counters(self):
        self._check("io")
        return SimpleNamespace(read_bytes=self._io[0], write_bytes=self._io[1])

    def cpu_times(self):
        self._check("times")
        return SimpleNamespace(user=self._times[0], system=self._times[1])


@pytest.fixture
def app(monkeypatch, log):
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", True)
    a = R.__new__(R)
    a.add_test_log = log
    a._cached_cpu_count = 4
    return a


# ── снимок и разница диска ───────────────────────────────────────────────

def test_disk_snapshot_collects_system_and_process_io(monkeypatch):
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", True)
    procs = [_P(1, "editors.exe", io=(5 * MB, MB)), _P(2, "закрылся", dead={"io"})]
    monkeypatch.setattr(res, "psutil", SimpleNamespace(
        disk_io_counters=lambda: SimpleNamespace(read_bytes=10, write_bytes=20),
        process_iter=lambda attrs: procs))
    snap = res._disk_snapshot()
    assert snap["sys"] == (10, 20) and snap["procs"] == {1: ("editors.exe", 5 * MB, MB)}


def test_disk_snapshot_without_disk_counters_or_psutil(monkeypatch):
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", True)

    def no_counters():
        raise RuntimeError("нет счётчиков")
    monkeypatch.setattr(res, "psutil", SimpleNamespace(disk_io_counters=no_counters,
                                                       process_iter=lambda attrs: []))
    assert res._disk_snapshot()["sys"] is None
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    assert res._disk_snapshot() is None


def test_disk_delta_splits_r7_dead_x2t_and_background():
    a = {"t": 0.0, "sys": (0, 0), "procs": {1: ("editors.exe", 0, 0), 2: ("chrome.exe", 0, 0),
                                            3: ("chrome.exe", 0, 0)}}
    b = {"t": 2.0, "sys": (40 * MB, 20 * MB),
         "procs": {1: ("editors.exe", 10 * MB, 2 * MB), 2: ("chrome.exe", 3 * MB, 0),
                   3: ("chrome.exe", MB, 0), 4: ("новый.exe", 0, 512 * 1024)}}
    x2t = [{"pid": 99, "io_read_mb": 5.0, "io_write_mb": 300.0},   # умер внутри окна
           {"pid": 1, "io_read_mb": 1000.0}]                       # жив — уже в снимке
    d = res._disk_delta(a, b, lambda n: n == "editors.exe", x2t)
    assert d["sys_mb_per_sec"] == 30.0 and d["sys_read_mb"] == 40.0
    assert (d["r7_read_mb"], d["r7_write_mb"]) == (15.0, 302.0)
    assert d["top_other"] == [{"name": "chrome.exe", "read_mb": 4.0, "write_mb": 0.0}]
    text = res._format_disk(d)
    assert "диск: чтение 40 МБ" in text and "фон: chrome.exe 4 МБ" in text
    assert res._disk_delta(None, b, str) is None and res._format_disk(None) == ""


def test_disk_delta_without_system_counters():
    a = {"t": 0.0, "sys": None, "procs": {}}
    b = {"t": 1.0, "sys": None, "procs": {1: ("editors.exe", MB, 0)}}
    d = res._disk_delta(a, b, lambda n: True)
    assert d["sys_read_mb"] is None and d["r7_read_mb"] == 1.0
    assert res._format_disk(d).startswith("Р7: чтение 1 МБ")


# ── RAM/CPU по процессам Р7 ─────────────────────────────────────────────

def test_sample_resources_sums_live_processes(app, monkeypatch):
    monkeypatch.setattr(res, "time", SimpleNamespace(time=lambda: 1100.0,
                                                     perf_counter=lambda: 0.0))
    procs = [_P(1, rss=100 * MB, cpu=120.0, threads=10, created=1000.0),
             _P(2, rss=50 * MB, cpu=40.0, threads=5, created=1050.0),
             _P(3, dead={"memory", "cpu", "threads", "create"})]
    s = app._sample_r7_resources(procs)
    assert s == {"ram_mb": 150.0, "cpu_raw_pct": 160.0, "cpu_norm_pct": 40.0,
                 "threads": 15, "uptime_sec": 100.0}
    assert app._sample_r7_resources(procs, measure_cpu=False)["cpu_raw_pct"] == 0.0


def test_sample_resources_none_when_nothing_alive(app, monkeypatch):
    assert app._sample_r7_resources([]) is None
    assert app._sample_r7_resources([_P(dead={"memory"})]) is None
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    assert app._sample_r7_resources([_P()]) is None


@pytest.mark.parametrize("norm, icon", [(None, "⚪"), (10.0, "🟢"), (60.0, "🟡"), (95.0, "🔴")])
def test_log_resources_icon_by_normalized_cpu(app, log, norm, icon):
    app._log_resources({"ram_mb": 1.0, "cpu_raw_pct": 2.0, "cpu_norm_pct": norm,
                        "threads": 3, "uptime_sec": None})
    assert icon in log.messages[-1] and "Аптайм: —" in log.messages[-1]


def test_log_resources_and_op_resources_skip_empty(app, log):
    app._log_resources(None)
    app._log_op_resources(None)
    app._log_op_resources({"cpu_sec": None})
    assert log.messages == []
    app._log_op_resources({"cpu_sec": 1.5, "cpu": 75.0, "cpu_peak_core_pct": 130.0, "ram": None})
    assert "1.500 с" in log.messages[-1] and "пик RAM —" in log.messages[-1]


# ── процессорное время Р7 ────────────────────────────────────────────────

def test_r7_cpu_seconds(app, monkeypatch):
    app._get_r7_processes = lambda log_cb=None, fresh=False: [
        _P(times=(1.0, 0.5)), _P(times=(2.0, 0.0)), _P(dead={"times"})]
    assert app._r7_cpu_seconds() == pytest.approx(3.5)
    app._get_r7_processes = lambda log_cb=None, fresh=False: []
    assert app._r7_cpu_seconds() is None

    def boom(log_cb=None, fresh=False):
        raise RuntimeError("psutil сломался")
    app._get_r7_processes = boom
    assert app._r7_cpu_seconds() is None
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    assert app._r7_cpu_seconds() is None
