"""Бисект на стенде (r7/bisect_runner.py), команда `python -m r7 bisect`
(r7/cli.py) и страница бисекта (r7_reports.bisect_model, bisect.html).

Установка, удаление, Р7 и замер подменены: ничего не ставится и не
запускается. Фальшивый стенд помнит, какая версия «установлена», и отдаёт
повторы операции по ней: до сборки STEP — 1 с, начиная с неё — 1,25 с."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import r7_ops
import r7_reports
from r7 import bisect as bs
from r7 import bisect_runner as br
from r7 import cli, config, firstrun, logfile, noise, privileges
from r7.bisect import BisectError, Build
from r7.cli import EXIT_GATE, EXIT_OK, EXIT_PRECONDITION, EXIT_RUN
from r7.run_state import BISECT, PERF, RunStateMixin

OP = "Выделение всего (Ctrl+A)"
STEP = 5


def _builds(n=9):
    return [Build(f"R7-Office-2026.3.2.{100 + i}.exe", (2026, 3, 2, 100 + i),
                  Path(f"D/R7-Office-2026.3.2.{100 + i}.exe")) for i in range(n)]


class FakeOps:
    """r7_ops.SpreadsheetOps без Р7: операции — заглушки."""

    def __init__(self, app, find_hwnd, log_cb, test_file):
        pass

    def tests(self):
        return [(OP, lambda: None), ("Копирование (Ctrl+C)", lambda: None)]


class Stand(br.BisectMixin, RunStateMixin):
    """Стенд без Р7: реестр, установщик, открытие и замер — подмены."""
    BISECT_UNINSTALL_SETTLE_SEC = 0

    def __init__(self, tmp_path, installed="2026.3.2.100", step=STEP, fail_install=(),
                 fail_uninstall=False, no_runs=(), interrupt_on=None):
        self.reports_folder = tmp_path / "Reports"
        self.reports_folder.mkdir(exist_ok=True)
        self.installed = installed
        self.step, self.fail_install, self.fail_uninstall = step, set(fail_install), fail_uninstall
        self.no_runs, self.interrupt_on = set(no_runs), interrupt_on
        self.events, self.log, self.active_seen = [], [], []
        self.current_version_info = None

    def add_test_log(self, msg):
        self.log.append(msg)

    def _engage_power_plan(self, log_cb):
        self.events.append(("power", "on"))

    def _restore_power_plan(self, log_cb):
        self.events.append(("power", "off"))

    def _read_current_version_from_registry(self):
        return {"version": self.installed, "name": "Р7-Офис"} if self.installed else None

    def detect_current_version(self):
        self.current_version_info = self._read_current_version_from_registry()

    def uninstall_current_version(self):
        self.events.append(("uninstall", self.installed))
        if self.fail_uninstall:
            return False
        self.installed = None
        return True

    def install_version(self, path):
        name = Path(path).name
        self.events.append(("install", name))
        if name in self.fail_install:
            return False
        self.installed = ".".join(map(str, bs.parse_version(Path(path).stem)))
        return True

    def _capture_environment(self, log_cb=None):
        return {"fingerprint_hash": "fp1"}

    def _scenario_open_r7(self, test_file):
        self.events.append(("open", self.installed))
        return SimpleNamespace(find_hwnd=lambda: 1)

    def _scenario_close_r7(self, session):
        self.events.append(("close", self.installed))

    def _measure_op_repeated(self, name, fn, runs, find_hwnd, log_cb, stop_event):
        self.active_seen.append(self.run_state.active)
        self.events.append(("measure", self.installed, runs))
        n_measures = sum(1 for e in self.events if e[0] == "measure")
        if self.interrupt_on == n_measures:
            raise KeyboardInterrupt
        build_no = bs.parse_version(self.installed)[-1] - 100
        if build_no in self.no_runs:
            return {"name": name, "time": 0.0, "runs": [], "error": "операция не прошла"}
        level = 1.25 if build_no >= self.step else 1.0
        vals = [level * (1 + 0.001 * ((i * 7) % 5 - 2)) for i in range(runs)]
        return {"name": name, "time": level, "runs": vals, "run_statuses": ["ok"] * runs,
                "first_run_discarded": False, "error": None}

    def installs(self):
        return [e[1] for e in self.events if e[0] == "install"]


@pytest.fixture(autouse=True)
def _no_stand(monkeypatch):
    monkeypatch.setattr(r7_ops, "SpreadsheetOps", FakeOps)
    monkeypatch.setattr(privileges, "is_admin", lambda: True)


def _bisect(stand, builds=None, good=0, bad=-1, **kw):
    builds = builds or _builds()
    return stand.bisect_builds(builds, builds[good], builds[bad], OP, Path("f.xlsx"), **kw)


# ── поток бисекта ────────────────────────────────────────────────────────

def test_finds_step_and_restores_original(tmp_path):
    stand = Stand(tmp_path)
    builds = _builds()
    res = _bisect(stand, builds)
    assert res.status == bs.STATUS_FOUND and res.first_bad == builds[STEP]
    # база уже стояла — первая установка сразу плохая; в конце — исходная
    assert stand.installs()[0] == builds[-1].name
    assert stand.installs()[-1] == builds[0].name and stand.installed == "2026.3.2.100"
    assert res.extra["restore"] == br.RESTORE_OK and res.extra["original_version"] == "2026.3.2.100"
    assert res.extra["installs"] == len(stand.installs())
    assert res.threshold_pct == 10.0 and res.threshold_source == noise.SOURCE_DEFAULT


def test_every_measurement_is_install_open_measure_close(tmp_path):
    stand = Stand(tmp_path)
    _bisect(stand)
    kinds = [e[0] for e in stand.events if e[0] != "power"]
    # каждый замер — между open и close, а установка — до open, не внутри сессии
    for i, k in enumerate(kinds):
        if k == "measure":
            assert kinds[i - 1] == "open" and kinds[i + 1] == "close"
        if k == "install":
            assert kinds[i - 1] == "uninstall"
    assert stand.events[0] == ("power", "on") and stand.events[-1] == ("power", "off")
    # замер меряет ту сборку, которую поставили
    for i, e in enumerate(stand.events):
        if e[0] == "open":
            assert stand.events[i + 1][1] == e[1]


def test_run_state_is_bisect_during_run_and_released_after(tmp_path):
    stand = Stand(tmp_path)
    _bisect(stand)
    assert set(stand.active_seen) == {BISECT}
    assert stand.run_state.active is None


def test_refused_while_other_run_is_active(tmp_path):
    stand = Stand(tmp_path)
    stand.run_state.try_start(PERF)
    with pytest.raises(BisectError, match="Выполняется тест производительности"):
        _bisect(stand)
    assert stand.installs() == [] and stand.run_state.active == PERF


def test_other_runs_refused_while_bisect_runs():
    from r7.run_state import BATCH, CUSTOM, INSTALL, REFUSALS, SCENARIO, RunState
    st = RunState()
    st.try_start(BISECT)
    for kind in (PERF, BATCH, CUSTOM, SCENARIO, INSTALL, BISECT):
        assert st.try_start(kind) == REFUSALS[(kind, BISECT)]
    st.finish(BISECT)
    for active in (PERF, BATCH, CUSTOM, SCENARIO, INSTALL):
        st.try_start(active)
        assert st.try_start(BISECT) == REFUSALS[(BISECT, active)]
        st.finish(active)


def test_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(privileges, "is_admin", lambda: False)
    stand = Stand(tmp_path)
    with pytest.raises(BisectError, match="администратора"):
        _bisect(stand)
    assert stand.events == [("power", "on"), ("power", "off")]
    assert stand.run_state.active is None


def test_failed_install_skips_probe_and_bisect_goes_on(tmp_path):
    builds = _builds()
    stand = Stand(tmp_path, fail_install={builds[6].name})
    res = _bisect(stand, builds)
    p6 = next(p for p in res.probes if p.build == builds[6])
    assert p6.verdict == bs.SKIPPED and "не удался" in p6.note
    assert res.status == bs.STATUS_FOUND and res.first_bad == builds[STEP]
    assert stand.installed == "2026.3.2.100"            # исходная возвращена


def test_uninstall_failure_is_not_followed_by_install(tmp_path):
    stand = Stand(tmp_path, fail_uninstall=True)
    res = _bisect(stand)
    assert res.status == bs.STATUS_ERROR                # плохую не поставить
    assert stand.installs() == []
    assert res.extra["restore"] == br.RESTORE_FAILED


def test_no_valid_runs_means_skip(tmp_path):
    builds = _builds()
    stand = Stand(tmp_path, no_runs={4})
    res = _bisect(stand, builds)
    assert next(p for p in res.probes if p.build == builds[4]).verdict == bs.SKIPPED
    assert any("годных повторов нет" in m for m in stand.log)


def test_restores_original_after_interrupt(tmp_path):
    """Ctrl+C посреди замера (KeyboardInterrupt) — исходная версия всё равно
    возвращается, RunState освобождается."""
    stand = Stand(tmp_path, interrupt_on=3)
    with pytest.raises(KeyboardInterrupt):
        _bisect(stand)
    assert stand.installs()[-1] == _builds()[0].name and stand.installed == "2026.3.2.100"
    assert stand.run_state.active is None


def test_stop_event_stops_and_restores(tmp_path):
    stand = Stand(tmp_path)
    stop = threading.Event()
    orig = stand._measure_op_repeated

    def measure_then_stop(*a, **k):
        out = orig(*a, **k)
        if sum(1 for e in stand.events if e[0] == "measure") == 3:
            stop.set()
        return out
    stand._measure_op_repeated = measure_then_stop
    res = _bisect(stand, stop_event=stop)
    assert res.status == bs.STATUS_STOPPED
    assert stand.installed == "2026.3.2.100" and res.extra["restore"] == br.RESTORE_OK


def test_no_restore_leaves_last_build(tmp_path):
    builds = _builds()
    stand = Stand(tmp_path)
    res = _bisect(stand, builds, restore=False)
    assert res.extra["restore"] == br.RESTORE_OFF
    # последняя проба — 105 (после 108, 104, 106): она и осталась
    assert stand.installs() == [builds[i].name for i in (8, 4, 6, 5)]
    assert stand.installed == "2026.3.2.105"


def test_original_without_installer_is_refused_before_installing(tmp_path):
    stand = Stand(tmp_path, installed="2025.1.1.1")
    with pytest.raises(BisectError, match="--no-restore"):
        _bisect(stand)
    assert stand.installs() == [] and stand.run_state.active is None
    res = _bisect(Stand(tmp_path, installed="2025.1.1.1"), restore=False)
    assert res.status == bs.STATUS_FOUND


def test_nothing_installed_originally(tmp_path):
    stand = Stand(tmp_path, installed=None)
    res = _bisect(stand)
    assert res.extra["restore"] == br.RESTORE_NO_ORIGINAL
    assert stand.installs()[0] == _builds()[0].name     # базу пришлось поставить


def test_restore_not_needed_when_original_is_last(tmp_path):
    """Исходная — плохая (её и меряли последней из крайних), шаг на ней же."""
    builds = _builds(2)
    stand = Stand(tmp_path, installed="2026.3.2.101", step=1)
    res = _bisect(stand, builds)
    assert res.status == bs.STATUS_FOUND and res.first_bad == builds[1]
    assert stand.installed == "2026.3.2.101" and stand.installs() == [builds[0].name, builds[1].name]
    assert res.extra["restore"] == br.RESTORE_NOT_NEEDED


def test_threshold_from_noise_profile(tmp_path):
    stand = Stand(tmp_path)
    noise.save_profile_doc(stand.reports_folder, {"format": 1, "machines": {"fp1": {
        "fingerprint_hash": "fp1", "tests": {OP: {"cv_pct": 1.5}}}}})
    res = _bisect(stand)
    assert res.threshold_pct == 4.5 and res.threshold_source == noise.SOURCE_NOISE


# ── команда python -m r7 bisect ──────────────────────────────────────────

@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    logfile.shutdown_logging()
    dist = tmp_path / "Distributives"
    dist.mkdir()
    for b in _builds():
        (dist / b.name).write_bytes(b"x")
    (dist / "setup.exe").write_bytes(b"x")
    yield tmp_path
    logfile.shutdown_logging()


class CliApp:
    """Голый R7Testovarka глазами cmd_bisect."""
    TEST_DEFINITIONS = ["Повторное открытие файла", OP, "Копирование (Ctrl+C)"]

    def effective_test_definitions(self):
        return list(self.TEST_DEFINITIONS)

    def __init__(self, tmp_path, result=None, error=None, installed="2026.3.2.100",
                 r7_running=False, fixture=True):
        self.reports_folder = tmp_path / "Reports"
        self.distributives_folder = tmp_path / "Distributives"
        self.result, self.error, self.installed = result, error, installed
        self.r7_running, self.fixture = r7_running, fixture
        self.calls = []

    def _extract_version(self, stem):
        v = bs.parse_version(stem)
        return "v" + ".".join(map(str, v)) if v else None

    def _get_r7_processes(self, log_cb=None):
        return [Mock(pid=77)] if self.r7_running else []

    def _locate_test_file(self):
        return Path("TestFiles/r7-test-50k.xlsx") if self.fixture else None

    def _read_current_version_from_registry(self):
        return {"version": self.installed} if self.installed else None

    def bisect_builds(self, builds, good, bad, op, test_file, **kw):
        self.calls.append((good.name, bad.name, op, kw))
        if self.error:
            raise self.error
        return self.result(builds, good, bad) if callable(self.result) else self.result


def _found(builds, good, bad):
    return bs.BisectResult(bs.STATUS_FOUND, "регрессия появилась в сборке X", tuple(builds),
                           first_bad=builds[5], last_good=builds[4], threshold_pct=10.0, op=OP,
                           extra={"restore": br.RESTORE_OK, "restore_text": "возвращена",
                                  "installs": 4})


def _status(status, restore=br.RESTORE_OK):
    def make(builds, good, bad):
        return bs.BisectResult(status, "итог", tuple(builds), threshold_pct=10.0, op=OP,
                               extra={"restore": restore, "restore_text": "текст"})
    return make


def _cli(monkeypatch, app, *extra):
    monkeypatch.setattr(cli, "make_headless_app", lambda log_cb=None, reports_folder=None: app)
    return cli.main(["bisect", "--good", "2026.3.2.100", "--bad", "2026.3.2.108", "--op", OP,
                     *extra])


def test_parser_bisect_defaults():
    a = cli.build_parser().parse_args(["bisect", "--good", "a", "--bad", "b", "--op", OP])
    assert (a.runs, a.max_runs, a.no_restore, a.dist, a.func) == (7, 21, False, None, cli.cmd_bisect)
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["bisect", "--good", "a", "--op", OP])


def test_cli_found_writes_report_and_exits_ok(monkeypatch, cli_env, capsys):
    app = CliApp(cli_env, result=_found)
    assert _cli(monkeypatch, app) == EXIT_OK
    good, bad, op, kw = app.calls[0]
    assert (good, bad, op) == (_builds()[0].name, _builds()[-1].name, OP)
    assert kw["runs"] == 7 and kw["max_runs"] == 21 and kw["restore"] is True
    out = capsys.readouterr().out
    assert "setup.exe" in out and "нет номера версии" in out
    js = list(app.reports_folder.glob("bisect_*.json"))
    html = list(app.reports_folder.glob("bisect_*.html"))
    assert len(js) == 1 and len(html) == 1
    assert json.loads(js[0].read_text(encoding="utf-8"))["first_bad"]["label"] == "2026.3.2.105"
    assert "Регрессия появилась в сборке 2026.3.2.105" in html[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("status,code", [(bs.STATUS_RANGE, EXIT_GATE), (bs.STATUS_NO_CHANGE, EXIT_GATE),
                                         (bs.STATUS_SPEEDUP, EXIT_GATE), (bs.STATUS_ERROR, EXIT_RUN),
                                         (bs.STATUS_STOPPED, EXIT_RUN)])
def test_cli_exit_codes_by_status(monkeypatch, cli_env, status, code):
    assert _cli(monkeypatch, CliApp(cli_env, result=_status(status))) == code


def test_cli_restore_failure_is_run_error(monkeypatch, cli_env, capsys):
    app = CliApp(cli_env, result=_status(bs.STATUS_FOUND, restore=br.RESTORE_FAILED))
    assert _cli(monkeypatch, app) == EXIT_RUN


def test_cli_bisect_error_from_runner_is_precondition(monkeypatch, cli_env):
    app = CliApp(cli_env, error=BisectError("Выполняется Batch-режим"))
    assert _cli(monkeypatch, app) == EXIT_PRECONDITION


def test_cli_crash_is_run_error(monkeypatch, cli_env):
    assert _cli(monkeypatch, CliApp(cli_env, error=RuntimeError("бах"))) == EXIT_RUN


@pytest.mark.parametrize("kwargs,args,needle", [
    ({}, ["--op", "Повторное открытие файла"], "Нет такой операции"),
    ({}, ["--op", "нет такой"], "Нет такой операции"),
    ({}, ["--runs", "3"], "--runs"),
    ({}, ["--good", "2025.1"], "нет среди"),
    ({}, ["--bad", "2026.3.2.100"], "одна и та же"),
    ({"r7_running": True}, [], "77"),
    ({"fixture": False}, [], "фикстура"),
    ({"installed": "2025.9.9.9"}, [], "--no-restore"),
])
def test_cli_preconditions(monkeypatch, cli_env, capsys, kwargs, args, needle):
    app = CliApp(cli_env, result=_found, **kwargs)
    monkeypatch.setattr(cli, "make_headless_app", lambda log_cb=None, reports_folder=None: app)
    base = {"--good": "2026.3.2.100", "--bad": "2026.3.2.108", "--op": OP}
    rest = []
    for i in range(0, len(args), 2):
        if args[i] in base:
            base[args[i]] = args[i + 1]
        else:
            rest += args[i:i + 2]
    argv = ["bisect"] + [x for kv in base.items() for x in kv] + rest
    assert cli.main(argv) == EXIT_PRECONDITION
    assert needle in capsys.readouterr().out and not app.calls


def test_cli_requires_admin(monkeypatch, cli_env, capsys):
    monkeypatch.setattr(privileges, "is_admin", lambda: False)
    app = CliApp(cli_env, result=_found)
    assert _cli(monkeypatch, app) == EXIT_PRECONDITION
    assert "администратора" in capsys.readouterr().out and not app.calls


def test_cli_no_restore_skips_original_check(monkeypatch, cli_env):
    app = CliApp(cli_env, result=_found, installed="2025.9.9.9")
    assert _cli(monkeypatch, app, "--no-restore") == EXIT_OK
    assert app.calls[0][3]["restore"] is False


def test_cli_headless_installer_status_goes_to_log(monkeypatch, cli_env, capsys):
    app = cli.headless_installer(CliApp(cli_env))
    app._set_status("Удаление...")
    app.detect_current_version()
    assert app.current_version_info == {"version": "2026.3.2.100"}
    assert "Удаление..." in capsys.readouterr().out


def test_firstrun_check_used_for_running_r7():
    app = SimpleNamespace(_get_r7_processes=lambda log_cb=None: [])
    assert firstrun.check_r7_running(app).status == firstrun.OK


# ── страница ─────────────────────────────────────────────────────────────

def _real_result(tmp_path, **stand_kw):
    builds = _builds()
    stand = Stand(tmp_path, **stand_kw)
    return _bisect(stand, builds), builds


def test_page_model_rows_and_verdict(tmp_path):
    res, builds = _real_result(tmp_path)
    model = r7_reports.bisect_model(res.to_dict())
    assert model["tone"] == "critical" and "2026.3.2.105" in model["headline"]
    assert [r["label"] for r in model["rows"]] == [b.label for b in builds]
    first = next(r for r in model["rows"] if r["first_bad"])
    assert first["label"] == "2026.3.2.105" and first["verdict"] == bs.LIKE_BAD
    assert model["rows"][0]["role"] == bs.ROLE_GOOD and model["rows"][-1]["role"] == bs.ROLE_BAD
    unprobed = [r for r in model["rows"] if r["verdict"] == "не проверялась"]
    assert unprobed and all(r["n"] == 0 and r["median"] == "—" for r in unprobed)
    assert model["restore"]["tone"] == "good"
    assert model["endpoints"]["text"].startswith("+25 %")


def test_page_renders_escaped_and_complete(tmp_path):
    res, _ = _real_result(tmp_path)
    data = res.to_dict()
    data["op"] = "<script>alert(1)</script>"
    html = r7_reports.bisect_page(r7_reports.bisect_model(data))
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "Бисект по сборкам" in html and "первая плохая" in html
    assert "Исходная версия" in html


@pytest.mark.parametrize("status,tone", [("range", "warning"), ("no_change", "good"),
                                         ("speedup", "neutral"), ("error", "warning"),
                                         ("stopped", "warning")])
def test_page_headline_per_status(status, tone):
    data = {"status": status, "message": "м", "builds": [], "probes": [],
            "suspects": [{"name": "a.exe", "label": "1.2"}], "threshold_pct": None}
    model = r7_reports.bisect_model(data)
    assert model["tone"] == tone
    html = r7_reports.bisect_page(model)
    assert model["headline"] in html


def test_page_range_marks_suspects(tmp_path):
    builds = _builds(6)
    stand = Stand(tmp_path, step=4, fail_install={builds[3].name})
    res = _bisect(stand, builds)
    assert res.status == bs.STATUS_RANGE
    model = r7_reports.bisect_model(res.to_dict())
    assert [r["label"] for r in model["rows"] if r["suspect"]] == [builds[3].label, builds[4].label]
    assert "одной из 2" in model["headline"]
