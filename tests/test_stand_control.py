"""Управление стендом (plan-to-20, этап 3, п. 6): план питания на время
прогона и частота CPU с пометкой троттлинга. powercfg, psutil и PDH —
подмены; план машины тесты не трогают (conftest._no_real_powercfg)."""
import subprocess
from types import SimpleNamespace

import pytest

import r7_Testovarka as r7mod
from r7 import cpu_freq, env, resources, settings, stand

BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH = stand.HIGH_PERFORMANCE_GUID
# Настоящая run_powercfg: conftest подменяет имя модуля перед каждым тестом,
# а это присваивание выполняется при импорте, до подмены.
_ORIG_RUN = stand.run_powercfg

LIST_OUT = (
    "Существующие схемы управления питанием (* - активные)\n"
    "-----------------------------------\n"
    f"GUID схемы питания: {BALANCED}  (Сбалансированная) *\n"
    f"GUID схемы питания: {HIGH}  (Высокая производительность)\n"
    "GUID схемы питания: a1841308-3541-4fab-bc81-f71556f20b4a  (Экономия энергии)\n")


class FakePowercfg:
    """powercfg по сценарию: активная схема, список, исход /setactive."""

    def __init__(self, active=BALANCED, list_out=LIST_OUT, set_code=0, fail=None):
        self.active = active
        self.list_out = list_out
        self.set_code = set_code
        self.fail = fail            # исключение на любой вызов
        self.calls = []

    def __call__(self, args, timeout=None):
        self.calls.append(list(args))
        if self.fail is not None:
            raise self.fail
        if args[0] == "/getactivescheme":
            names = {BALANCED: "Сбалансированная", HIGH: "Высокая производительность"}
            return 0, f"GUID схемы питания: {self.active}  ({names.get(self.active, 'X')})\n"
        if args[0] == "/list":
            return 0, self.list_out
        if args[0] == "/setactive":
            if self.set_code == 0:
                self.active = args[1]
            return self.set_code, ""
        return 1, ""

    def setactive_calls(self):
        return [c[1] for c in self.calls if c[0] == "/setactive"]


@pytest.fixture
def pcfg(monkeypatch):
    fake = FakePowercfg()
    monkeypatch.setattr(stand, "run_powercfg", fake)
    monkeypatch.setattr(settings, "load_settings", lambda: dict(settings.DEFAULTS))
    return fake


class _Worker(stand.StandMixin):
    """Минимальный хозяин декоратора: воркер, который может упасть."""

    def __init__(self, log):
        self.add_test_log = log
        self.seen_state = None

    @stand.power_plan_during_run
    def run(self, fail=False, log_cb=None):
        self.seen_state = dict(self._power_plan_state or {})
        if fail:
            raise RuntimeError("прогон упал")
        return "ok"

    @stand.power_plan_during_run
    def outer(self):
        return self.run()


# ── разбор вывода powercfg ────────────────────────────────────────────────

def test_parse_scheme_line_keeps_nested_parentheses():
    line = "GUID схемы питания: 60261C2F-3bc2-44b8-94b8-f4a2200bcdd8  (GameTurbo (High Performance)) *"
    assert stand.parse_scheme_line(line) == ("60261c2f-3bc2-44b8-94b8-f4a2200bcdd8",
                                             "GameTurbo (High Performance)")
    assert stand.parse_scheme_line("мусор") is None


def test_list_schemes_reads_all_guids(pcfg):
    assert set(stand.list_schemes()) == {BALANCED, HIGH, "a1841308-3541-4fab-bc81-f71556f20b4a"}


# ── переключение и возврат ───────────────────────────────────────────────

def test_switches_to_high_performance_and_restores(pcfg, log):
    w = _Worker(log)
    assert w.run() == "ok"
    assert w.seen_state["before"] == "Сбалансированная"
    assert w.seen_state["during"] == "Высокая производительность"
    assert pcfg.setactive_calls() == [HIGH, BALANCED]
    assert pcfg.active == BALANCED
    assert w._power_plan_state is None                 # следующий прогон начнёт с чистого


def test_restores_in_finally_when_run_raises(pcfg, log):
    w = _Worker(log)
    with pytest.raises(RuntimeError):
        w.run(fail=True)
    assert pcfg.setactive_calls() == [HIGH, BALANCED]
    assert pcfg.active == BALANCED


def test_nested_workers_switch_once(pcfg, log):
    w = _Worker(log)
    w.outer()
    assert pcfg.setactive_calls() == [HIGH, BALANCED]
    assert w._power_plan_depth == 0


def test_absent_high_performance_scheme_is_logged_and_run_continues(pcfg, log):
    pcfg.list_out = f"GUID схемы питания: {BALANCED}  (Сбалансированная) *\n"
    w = _Worker(log)
    assert w.run() == "ok"
    assert pcfg.setactive_calls() == []
    assert w.seen_state["during"] == "Сбалансированная"
    assert any("Высокая производительность» в системе нет" in m for m in log.messages)


@pytest.mark.parametrize("exc", [OSError("нет powercfg"),
                                 subprocess.TimeoutExpired("powercfg", 5)])
def test_powercfg_failure_never_breaks_run(pcfg, log, exc):
    pcfg.fail = exc
    w = _Worker(log)
    assert w.run() == "ok"
    assert w.seen_state == {}
    assert any("План питания: не удалось переключить" in m for m in log.messages)


def test_setactive_failure_keeps_old_plan_and_skips_restore(pcfg, log):
    pcfg.set_code = 5
    w = _Worker(log)
    w.run()
    assert pcfg.setactive_calls() == [HIGH]            # возвращать нечего
    assert any("/setactive не прошёл" in m for m in log.messages)


def test_restore_failure_is_loud(pcfg, log):
    w = _Worker(log)
    w._engage_power_plan()
    pcfg.fail = OSError("пропал")
    w._restore_power_plan()
    assert any("не вернулся — включите его вручную" in m for m in log.messages)


def test_already_high_performance_is_not_switched(pcfg, log):
    pcfg.active = HIGH
    _Worker(log).run()
    assert pcfg.setactive_calls() == []


def test_setting_off_leaves_plan_alone(pcfg, log, monkeypatch):
    monkeypatch.setattr(settings, "load_settings",
                        lambda: {**settings.DEFAULTS, "manage_power_plan": False})
    w = _Worker(log)
    w.run()
    assert pcfg.setactive_calls() == []
    assert w.seen_state["before"] == w.seen_state["during"] == "Сбалансированная"


def test_setting_default_is_on():
    assert settings.DEFAULTS["manage_power_plan"] is True


def test_log_cb_argument_of_worker_gets_messages(pcfg, log):
    own = []
    w = _Worker(log)
    w.run(log_cb=own.append)
    assert any("План питания на время прогона" in m for m in own)
    assert not log.messages


def test_workers_are_wrapped():
    R = r7mod.R7Testovarka
    for name in ("_spreadsheet_worker", "_batch_worker", "_worker_run_test"):
        assert getattr(R, name).__wrapped__, name


def test_environment_records_plan_before_and_during(bare_r7, pcfg, log, monkeypatch):
    bare_r7.add_test_log = log
    bare_r7._engage_power_plan()
    assert bare_r7._power_plan_environment() == {
        "power_plan_before": "Сбалансированная",
        "power_plan_during": "Высокая производительность"}
    bare_r7._restore_power_plan()
    assert bare_r7._power_plan_environment() == {"power_plan_before": None,
                                                 "power_plan_during": None}


# ── запуск powercfg: без shell, с таймаутом и kill ───────────────────────

class _Proc:
    def __init__(self, hang=False):
        self.hang = hang
        self.killed = False
        self.returncode = 0

    def communicate(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("powercfg", timeout)
        return "GUID: x".encode("cp866"), b""

    def kill(self):
        self.killed = True


def test_run_powercfg_no_shell_and_kills_on_timeout(monkeypatch):
    seen = {}
    proc = _Proc(hang=True)

    def popen(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return proc
    monkeypatch.setattr(stand.subprocess, "Popen", popen)
    with pytest.raises(subprocess.TimeoutExpired):
        _ORIG_RUN(["/list"])
    assert proc.killed
    assert isinstance(seen["args"], list) and seen["args"][0].lower().endswith("powercfg.exe")
    assert "shell" not in seen["kw"]


def test_run_powercfg_decodes_console_output(monkeypatch):
    monkeypatch.setattr(stand.subprocess, "Popen", lambda args, **kw: _Proc())
    assert _ORIG_RUN(["/getactivescheme"]) == (0, "GUID: x")


# ── частота CPU ──────────────────────────────────────────────────────────

class FakePdh:
    PDH_FMT_DOUBLE = 0x200

    def __init__(self, values, fail_add=False):
        self.values = list(values)
        self.fail_add = fail_add
        self.collects = 0
        self.closed = False

    def OpenQuery(self):
        return "q"

    def AddEnglishCounter(self, q, path):
        if self.fail_add:
            raise RuntimeError("нет счётчика")
        assert path == cpu_freq.PDH_COUNTER
        return "c"

    def CollectQueryData(self, q):
        self.collects += 1

    def GetFormattedCounterValue(self, c, fmt):
        v = self.values.pop(0)
        if isinstance(v, Exception):
            raise v
        return (0, v)

    def CloseQuery(self, q):
        self.closed = True


def test_probe_reads_pdh_percent(monkeypatch):
    pdh = FakePdh([97.57, RuntimeError("PDH_INVALID_DATA"), 1e9])
    monkeypatch.setattr(env, "PDH_OK", True)
    monkeypatch.setattr(env, "win32pdh", pdh)
    p = cpu_freq.CpuFreqProbe()
    assert p.sample() == 97.6 and p.source == "pdh"
    assert pdh.collects == 2                       # база при открытии + точка
    assert p.sample() is None                      # сбой счётчика — точки нет
    assert p.sample() is None                      # абсурдное значение — тоже
    p.close()
    assert pdh.closed


def test_probe_falls_back_to_psutil_then_none(monkeypatch):
    monkeypatch.setattr(env, "PDH_OK", True)
    monkeypatch.setattr(env, "win32pdh", FakePdh([], fail_add=True))
    monkeypatch.setattr(env, "PSUTIL_OK", True)
    monkeypatch.setattr(env, "psutil", SimpleNamespace(
        cpu_freq=lambda: SimpleNamespace(current=2350.0, max=4700.0)))
    p = cpu_freq.CpuFreqProbe()
    assert p.sample() == 50.0 and p.source == "psutil"

    monkeypatch.setattr(env, "PDH_OK", False)
    monkeypatch.setattr(env, "PSUTIL_OK", False)
    q = cpu_freq.CpuFreqProbe()
    assert q.sample() is None and q.source is None


class _SeqProbe:
    def __init__(self, values):
        self.values = list(values)

    def sample(self):
        return self.values.pop(0) if self.values else None


class _Proc1:
    pid = 1

    def cpu_times(self):
        return SimpleNamespace(user=1.0, system=0.0)

    def cpu_percent(self, interval=None):
        return 10.0

    def memory_info(self):
        return SimpleNamespace(rss=2 ** 20)


def test_op_watch_tracks_min_frequency_without_pre_window_value():
    # 40 — значение ДО окна (start сдвигает начало интервала), его не берём.
    w = resources.OpResourceWatch(lambda: [_Proc1()], interval=10.0,
                                  freq_probe=_SeqProbe([40.0, 95.0, 72.0]))
    w.start()
    w._sample_freq()
    res = w.stop()
    assert res["cpu_freq_min_pct"] == 72.0


def test_op_watch_without_probe_reports_none():
    w = resources.OpResourceWatch(lambda: [_Proc1()], interval=10.0)
    w.start()
    assert w.stop()["cpu_freq_min_pct"] is None


def test_resource_sampler_row_has_frequency():
    s = resources.ResourceSampler(lambda: [], freq_probe=_SeqProbe([88.0]))
    s._sample_once()
    assert s.snapshot()[0]["cpu_freq_pct"] == 88.0
    s2 = resources.ResourceSampler(lambda: [])
    s2._sample_once()
    assert s2.snapshot()[0]["cpu_freq_pct"] is None


def test_throttled_repeat_is_flagged_not_excluded(bare_r7):
    from r7.measure import _RunAcc
    bare_r7._cached_cpu_count = 4
    bare_r7._get_r7_processes = lambda log_cb=None, fresh=False: []
    bare_r7._sample_r7_resources = lambda procs, measure_cpu=True: None
    acc = _RunAcc()
    acc.pass_times = [1.0, 1.0, 3.0]
    acc.run_statuses = ["ok"] * 3
    base = {"cpu_sec": 1.0, "cpu_peak_core_pct": 100.0, "cpu_avg_core_pct": 50.0,
            "ram_peak_mb": 100.0}
    acc.run_res = [{**base, "cpu_freq_min_pct": 99.0}, {**base, "cpu_freq_min_pct": None},
                   {**base, "cpu_freq_min_pct": 55.0}]
    logs = []
    rec = bare_r7._op_record("A", acc, logs.append)
    assert rec["run_notes"] == [[], [], ["throttle"]]
    assert rec["run_cpu_freq_pct"] == [99.0, None, 55.0]
    assert rec["n_throttled"] == 1
    assert rec["median"] == 1.0 and rec["n_runs"] == 3   # в медиане, как и был
    assert any("троттлинг" in m for m in logs)
    assert bare_r7.CPU_THROTTLE_PCT == 80.0


def test_custom_plan_of_the_stand_is_left_alone(pcfg, log):
    # Свой план (здесь — игровой GameTurbo): не подменяем, иначе отпечаток
    # машины сменится и прошлые отчёты станут «другим стендом».
    pcfg.active = "60261c2f-3bc2-44b8-94b8-f4a2200bcdd8"
    _Worker(log).run()
    assert pcfg.setactive_calls() == []
    assert any("свой план стенда" in m for m in log.messages)
