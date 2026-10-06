"""Тесты сценария восстановления после сбоя (этап 3, M4).

subprocess.Popen и R7WebDriverConnector мокаются — тесты проверяют
оркестрацию (порядок вызовов, обработку ошибок, подсчёт метрик), а НЕ
реальное поведение Р7 при сбое: сам факт и механизм автовосстановления
сознательно не верифицирован живым прогоном (см. комментарий над
run_crash_recovery_scenario в r7_Testovarka.py) — это то, что явно
оставлено на пользователя.
"""
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod

# Настоящая функция — автофикстура fake_kill_since подменяет её в модуле.
_real_kill_since = r7mod._kill_r7_processes_since
_real_running = r7mod._running_r7_pids


class _FakeConnector:
    instances = []

    def __init__(self, port=None, filename_hint=None, log_cb=None, connect_ok=True,
                close_raises=False):
        self.port = port
        self.filename_hint = filename_hint
        self.log_cb = log_cb
        self._connect_ok = connect_ok
        self._close_raises = close_raises
        self.closed = False
        _FakeConnector.instances.append(self)

    def connect(self, timeout=None):
        return self._connect_ok

    def close(self):
        self.closed = True
        if self._close_raises:
            raise RuntimeError("close() упал")


@pytest.fixture(autouse=True)
def _reset_instances():
    _FakeConnector.instances = []
    yield
    _FakeConnector.instances = []


def _make_factory(before_ok=True, after_ok=True):
    calls = {"n": 0}

    def factory(port=None, filename_hint=None, log_cb=None):
        calls["n"] += 1
        ok = before_ok if calls["n"] == 1 else after_ok
        return _FakeConnector(port=port, filename_hint=filename_hint, log_cb=log_cb,
                              connect_ok=ok)
    return factory


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(r7mod.time, "sleep", Mock())


@pytest.fixture(autouse=True)
def _no_real_port_check(monkeypatch):
    """См. тот же фикстур в test_run_multidoc.py — port=None по умолчанию
    вызывает _pick_cdp_port, которая реально стучится в сокет."""
    monkeypatch.setattr(r7mod, "_pick_cdp_port",
                        lambda log_cb=None: (r7mod.env.DEFAULT_CDP_PORT,
                                             ["--ascdesktop-support-debug-info"]))


@pytest.fixture(autouse=True)
def fake_kill_since(monkeypatch):
    """Настоящий _kill_r7_processes_since убил бы живой Р7 на машине —
    в тестах оркестрации он заглушка: «убит 1 процесс, никто не выжил»."""
    m = Mock(return_value=(1, []))
    monkeypatch.setattr(r7mod, "_kill_r7_processes_since", m)
    return m


@pytest.fixture(autouse=True)
def fake_running_r7(monkeypatch):
    """Список живых процессов Р7: до запуска и после сбоя — пусто. Иначе
    тесты зависели бы от того, открыт ли Р7 на машине."""
    m = Mock(return_value=set())
    monkeypatch.setattr(r7mod, "_running_r7_pids", m)
    return m


def _patch_popen(monkeypatch, procs=None):
    """procs: список объектов, возвращаемых последовательными Popen()-ами.
    По умолчанию — два независимых Mock (до и после "сбоя")."""
    procs = procs or [Mock(), Mock()]
    it = iter(procs)
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(side_effect=lambda *a, **k: next(it)))
    return procs


def test_applies_all_edits_and_reports_count(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")
    seen = []
    edits = [lambda c, i=i: seen.append(i) for i in range(3)]

    out = r7mod.run_crash_recovery_scenario(
        "r7.exe", f, edits, verify_recovered=lambda c: 3)

    assert seen == [0, 1, 2]
    assert out["edits_applied"] == 3
    assert out["edits_failed"] == 0


def test_edit_exception_counted_as_failed_others_still_run(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")
    ran = []

    def boom(c):
        raise RuntimeError("правка упала")

    edits = [lambda c: ran.append("a"), boom, lambda c: ran.append("c")]

    out = r7mod.run_crash_recovery_scenario(
        "r7.exe", f, edits, verify_recovered=lambda c: 0)

    assert ran == ["a", "c"]
    assert out["edits_applied"] == 2
    assert out["edits_failed"] == 1


def test_skips_edits_when_initial_connect_fails(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory(before_ok=False))

    f = tmp_path / "a.docx"
    f.write_text("x")
    ran = []
    edits = [lambda c: ran.append(1)]

    out = r7mod.run_crash_recovery_scenario(
        "r7.exe", f, edits, verify_recovered=lambda c: 0)

    assert ran == []
    assert out["connected_before_crash"] is False
    assert out["edits_applied"] == 0


def test_kills_process_and_relaunches_with_same_path(no_sleep, monkeypatch, tmp_path):
    procs = _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "doc.pptx"
    f.write_text("x")

    r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    first_proc, second_proc = procs
    first_proc.kill.assert_called_once()
    first_proc.wait.assert_called_once()
    second_proc.kill.assert_not_called()
    call_args = r7mod.subprocess.Popen.call_args_list
    assert call_args[0][0][0] == ["r7.exe", str(f), "--ascdesktop-support-debug-info"]
    assert call_args[1][0][0] == ["r7.exe", str(f), "--ascdesktop-support-debug-info"]


def test_process_death_timeout_is_recorded_not_fatal(no_sleep, monkeypatch, tmp_path):
    first = Mock()
    first.wait.side_effect = r7mod.subprocess.TimeoutExpired(cmd="r7.exe", timeout=10)
    second = Mock()
    _patch_popen(monkeypatch, procs=[first, second])
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert out["process_died_cleanly"] is False
    # Перезапуск всё равно происходит — таймаут не должен обрывать сценарий.
    assert out["proc"] is second


def test_computes_recovered_fraction(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")
    edits = [lambda c: None for _ in range(4)]

    out = r7mod.run_crash_recovery_scenario(
        "r7.exe", f, edits, verify_recovered=lambda c: 3)

    assert out["recovered_count"] == 3
    assert out["recovered_fraction"] == pytest.approx(0.75)


def test_recovered_fraction_none_without_edits(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert out["recovered_fraction"] is None


def test_verify_recovered_not_called_when_reconnect_fails(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory(after_ok=False))

    f = tmp_path / "a.docx"
    f.write_text("x")
    verify = Mock(return_value=1)

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=verify)

    verify.assert_not_called()
    assert out["connected_after_crash"] is False
    assert out["recovered_count"] is None
    assert out["time_to_reconnect_sec"] is None


def test_verify_recovered_exception_does_not_crash_scenario(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    def verify(c):
        raise RuntimeError("не удалось прочитать документ")

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=verify)

    assert out["recovered_count"] is None
    assert out["connected_after_crash"] is True


def test_time_to_reconnect_is_nonnegative(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert out["time_to_reconnect_sec"] >= 0.0


def test_uses_default_cdp_port_when_unset(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert _FakeConnector.instances[0].port == r7mod.env.DEFAULT_CDP_PORT
    assert _FakeConnector.instances[1].port == r7mod.env.DEFAULT_CDP_PORT


def test_both_connectors_use_same_filename_hint(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "report.xlsx"
    f.write_text("x")

    r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert _FakeConnector.instances[0].filename_hint == "report.xlsx"
    assert _FakeConnector.instances[1].filename_hint == "report.xlsx"


# ── регрессии, найденные code-review ────────────────────────────────────

def test_raises_when_webdriver_not_ok(no_sleep, monkeypatch, tmp_path):
    monkeypatch.setattr(r7mod.env, "WEBDRIVER_OK", False)
    f = tmp_path / "a.docx"
    f.write_text("x")

    with pytest.raises(RuntimeError):
        r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)


def test_closes_both_connectors_on_happy_path(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert len(_FakeConnector.instances) == 2
    assert all(c.closed for c in _FakeConnector.instances)


def test_closes_pre_crash_connector_even_if_no_edits_ran(no_sleep, monkeypatch, tmp_path):
    """conn (до сбоя) закрывается сразу после kill(), не дожидаясь конца
    сценария — если бы close() был только в самом конце, соединение к уже
    убитому процессу висело бы открытым всё время перезапуска."""
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory(before_ok=False))

    f = tmp_path / "a.docx"
    f.write_text("x")

    r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert _FakeConnector.instances[0].closed is True


def test_new_connector_closed_even_when_verify_recovered_raises(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    def boom(c):
        raise RuntimeError("verify упал")

    r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=boom)

    assert _FakeConnector.instances[1].closed is True


def test_close_exception_on_one_connector_does_not_block_the_other(no_sleep, monkeypatch, tmp_path):
    calls = {"n": 0}

    def factory(port=None, filename_hint=None, log_cb=None):
        calls["n"] += 1
        # Первый (pre-crash) коннектор ломается на close() — второй должен
        # всё равно закрыться штатно.
        return _FakeConnector(port=port, filename_hint=filename_hint, log_cb=log_cb,
                              close_raises=(calls["n"] == 1))

    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", factory)

    f = tmp_path / "a.docx"
    f.write_text("x")

    # Не должно поднять исключение наружу — close() обёрнут в try/except.
    r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert all(c.closed for c in _FakeConnector.instances)


# ── process_died_cleanly гейтит verify_recovered ────────────────────────

def test_verify_recovered_skipped_when_process_did_not_die_cleanly(no_sleep, monkeypatch, tmp_path):
    """Регрессия (найдена code-review): без подтверждённой смерти старого
    процесса второй Popen с тем же путём мог просто переоткрыть файл в ещё
    живом старом процессе (та же механика, что у run_multidoc/H4) — и
    verify_recovered увидела бы исходный документ без единого сбоя, дав
    ложный "100% восстановлено" вердикт. Правильное поведение: не звать
    verify_recovered вовсе, оставить recovered_count/fraction None."""
    first = Mock()
    first.wait.side_effect = r7mod.subprocess.TimeoutExpired(cmd="r7.exe", timeout=10)
    second = Mock()
    _patch_popen(monkeypatch, procs=[first, second])
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")
    verify = Mock(return_value=99)

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [lambda c: None],
                                            verify_recovered=verify)

    assert out["process_died_cleanly"] is False
    assert out["connected_after_crash"] is True  # переподключение само по себе прошло
    verify.assert_not_called()
    assert out["recovered_count"] is None
    assert out["recovered_fraction"] is None


def test_verify_recovered_called_when_process_died_cleanly(no_sleep, monkeypatch, tmp_path):
    """Контроль к предыдущему тесту: когда смерть процесса ПОДТВЕРЖДЕНА,
    verify_recovered вызывается как обычно — гейт не перекрывает штатный
    путь."""
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")
    verify = Mock(return_value=1)

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [lambda c: None],
                                            verify_recovered=verify)

    assert out["process_died_cleanly"] is True
    verify.assert_called_once()
    assert out["recovered_count"] == 1


# ── after_relaunch (M4, run_crash_recovery.py) ───────────────────────────

def test_after_relaunch_called_with_new_proc(no_sleep, monkeypatch, tmp_path):
    procs = _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")
    seen = {}

    def after_relaunch(proc):
        seen["proc"] = proc
        return "ok"

    out = r7mod.run_crash_recovery_scenario(
        "r7.exe", f, [], verify_recovered=lambda c: 0, after_relaunch=after_relaunch)

    assert seen["proc"] is procs[1]
    assert out["after_relaunch"] == "ok"


def test_after_relaunch_called_before_sleep_and_reconnect(no_sleep, monkeypatch, tmp_path):
    """after_relaunch должен успеть отработать ДО time.sleep(relaunch_wait_sec)
    и до переподключения — иначе диалог восстановления мог бы всплыть уже
    после того, как сценарий начал опрашивать CDP."""
    _patch_popen(monkeypatch)
    call_order = []

    def factory(port=None, filename_hint=None, log_cb=None):
        call_order.append("connector_created")
        return _FakeConnector(port=port, filename_hint=filename_hint, log_cb=log_cb)

    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", factory)
    monkeypatch.setattr(r7mod.time, "sleep",
                        lambda s: call_order.append(f"sleep({s})"))

    f = tmp_path / "a.docx"
    f.write_text("x")

    def after_relaunch(proc):
        call_order.append("after_relaunch")

    r7mod.run_crash_recovery_scenario(
        "r7.exe", f, [], verify_recovered=lambda c: 0, after_relaunch=after_relaunch)

    # connector_created(до сбоя) -> sleep(launch) -> sleep(kill_delay) ->
    # after_relaunch -> sleep(relaunch) -> connector_created(после сбоя)
    idx_after_relaunch = call_order.index("after_relaunch")
    idx_relaunch_sleep = call_order.index("sleep(14.0)", idx_after_relaunch)
    idx_second_connector = len(call_order) - 1 - call_order[::-1].index("connector_created")
    assert idx_after_relaunch < idx_relaunch_sleep < idx_second_connector


def test_after_relaunch_exception_does_not_crash_scenario(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    def boom(proc):
        raise RuntimeError("поиск диалога упал")

    out = r7mod.run_crash_recovery_scenario(
        "r7.exe", f, [], verify_recovered=lambda c: 0, after_relaunch=boom)

    assert "after_relaunch" not in out
    assert "поиск диалога упал" in out["after_relaunch_error"]
    # Сценарий должен продолжиться штатно несмотря на сбой колбэка.
    assert out["connected_after_crash"] is True


def test_no_after_relaunch_keys_when_hook_not_given(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert "after_relaunch" not in out
    assert "after_relaunch_error" not in out


# ── «Сбой» убивает сам Р7, а не лаунчер (живой прогон 06.10.2026) ───────

def test_crash_kills_r7_processes_launched_by_scenario(no_sleep, monkeypatch, tmp_path,
                                                       fake_kill_since):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())
    fake_kill_since.return_value = (6, [])
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    before = r7mod.time.time()

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    since_ts = fake_kill_since.call_args.args[0]
    assert before <= since_ts <= r7mod.time.time()
    assert out["r7_processes_killed"] == 6
    assert out["process_died_cleanly"] is True


def test_surviving_r7_process_means_no_clean_death(no_sleep, monkeypatch, tmp_path,
                                                   fake_kill_since):
    """Лаунчер умер, а editors.exe пережил kill() — второй запуск откроет
    файл в нём же, проверка восстановления дала бы ложный успех."""
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())
    fake_kill_since.return_value = (6, [Mock(pid=14868)])
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    verify = Mock(return_value=3)

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=verify)

    assert out["process_died_cleanly"] is False
    verify.assert_not_called()
    assert out["recovered_count"] is None


class _P:
    def __init__(self, pid, name, create_time):
        self.pid = pid
        self.info = {"name": name, "create_time": create_time}
        self.killed = False

    def kill(self):
        self.killed = True


def test_kill_r7_processes_since_picks_only_fresh_r7(monkeypatch):
    procs = [
        _P(1, "editors.exe", 1000.0),
        _P(2, "editors_helper.exe", 1001.0),
        _P(3, "DesktopEditors.exe", 999.5),     # в пределах секунды допуска
        _P(4, "editors.exe", 900.0),            # Р7, открытый до сценария
        _P(5, "chrome.exe", 1002.0),
        _P(6, "R7Manager.exe", 1002.0),
    ]
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", True)
    monkeypatch.setattr(r7mod.psutil, "process_iter", lambda attrs=None: list(procs))
    monkeypatch.setattr(r7mod.psutil, "wait_procs",
                        lambda ps, timeout=None: ([], [p for p in ps if p.pid == 2]))

    killed, alive = _real_kill_since(1000.0, timeout=1)

    assert sorted(p.pid for p in procs if p.killed) == [1, 2, 3]
    assert killed == 3
    assert [p.pid for p in alive] == [2]


def test_kill_r7_processes_since_without_psutil(monkeypatch):
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    killed, alive = _real_kill_since(0.0)
    assert killed == 0 and alive == [None]      # смерть не подтверждена


class _Denied(_P):
    def kill(self):
        raise r7mod.psutil.AccessDenied(self.pid)


class _Gone(_P):
    def kill(self):
        raise r7mod.psutil.NoSuchProcess(self.pid)


def _fake_psutil(monkeypatch, procs, alive_pids=()):
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", True)
    monkeypatch.setattr(r7mod.psutil, "process_iter", lambda attrs=None: list(procs))
    monkeypatch.setattr(r7mod.psutil, "wait_procs",
                        lambda ps, timeout=None: ([], [p for p in ps if p.pid in alive_pids]))


def test_kill_continues_after_access_denied_and_vanished(monkeypatch):
    log = []
    procs = [_Denied(1, "editors.exe", 1000.0), _Gone(2, "editors_helper.exe", 1000.0),
             _P(3, "editors_helper.exe", 1000.0)]
    _fake_psutil(monkeypatch, procs, alive_pids={1})

    killed, alive = _real_kill_since(1000.0, log_cb=log.append)

    assert procs[2].killed                       # отказ на первом не остановил остальных
    assert killed == 3 and [p.pid for p in alive] == [1]
    assert any("Не удалось убить 1" in m for m in log)
    assert not any("Не удалось убить 2" in m for m in log)   # уже исчез — не ошибка


def test_kill_treats_unreadable_create_time_as_victim(monkeypatch):
    """AccessDenied на create_time даёт None. Пропустить такой процесс —
    снова ложный «сбой»: Р7 жив, а сценарий считает его убитым."""
    log = []
    procs = [_P(1, "editors.exe", None)]
    _fake_psutil(monkeypatch, procs)
    assert _real_kill_since(1000.0, log_cb=log.append)[0] == 1
    assert procs[0].killed
    assert any("не читается" in m for m in log)


def test_kill_keeps_listed_pids(monkeypatch):
    procs = [_P(1, "editors.exe", 1000.0), _P(2, "editors_helper.exe", 1000.0)]
    _fake_psutil(monkeypatch, procs)
    assert _real_kill_since(1000.0, keep_pids={1})[0] == 1
    assert not procs[0].killed and procs[1].killed


def test_running_r7_pids(monkeypatch):
    procs = [_P(1, "editors.exe", 0), _P(2, "chrome.exe", 0), _P(3, "x2t.exe", 0)]
    _fake_psutil(monkeypatch, procs)
    assert _real_running() == {1, 3}
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    assert _real_running() is None


# ── Предусловия, исход «сбоя» и Р7 при исключении ───────────────────────

def test_refuses_when_r7_already_running(no_sleep, monkeypatch, tmp_path, fake_running_r7,
                                        fake_kill_since):
    """Р7 пользователя открыт — второй запуск отдал бы файл ему, «сбоя» бы
    не было. Сценарий отказывается и ничего не запускает и не убивает."""
    popen = Mock()
    monkeypatch.setattr(r7mod.subprocess, "Popen", popen)
    fake_running_r7.return_value = {4242}
    f = tmp_path / "a.xlsx"
    f.write_text("x")

    with pytest.raises(RuntimeError, match="уже запущен"):
        r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    popen.assert_not_called()
    fake_kill_since.assert_not_called()


@pytest.mark.parametrize("killed, alive, leftover", [
    (0, [], set()),          # ничего не убито — Р7 и не был нашим
    (6, [], {14868}),        # убили, но editors.exe жив (например, не попал в выборку)
    (6, [], None),           # проверить нельзя (нет psutil)
])
def test_no_clean_death_without_real_kill(no_sleep, monkeypatch, tmp_path, fake_kill_since,
                                          fake_running_r7, killed, alive, leftover):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())
    fake_kill_since.return_value = (killed, alive)
    fake_running_r7.side_effect = [set(), leftover]
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    verify = Mock(return_value=3)

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=verify)

    assert out["process_died_cleanly"] is False
    verify.assert_not_called()


def test_exception_mid_scenario_still_kills_r7(no_sleep, monkeypatch, tmp_path, fake_kill_since):
    _patch_popen(monkeypatch)

    def broken_connector(**kw):
        raise RuntimeError("CDP упал")

    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", broken_connector)
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    before = r7mod.time.time()

    with pytest.raises(RuntimeError, match="CDP упал"):
        r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    fake_kill_since.assert_called_once()
    assert fake_kill_since.call_args.args[0] >= before


def test_before_edits_runs_first_and_fraction_counts_applied(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod.env, "R7WebDriverConnector", _make_factory())
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    order = []

    def boom(c):
        raise RuntimeError("правка упала")

    edits = [lambda c: order.append("edit"), boom]
    out = r7mod.run_crash_recovery_scenario(
        "r7.exe", f, edits, verify_recovered=lambda c: 1,
        before_edits=lambda c: order.append("snapshot"))

    assert order == ["snapshot", "edit"]
    assert out["edits_applied"] == 1
    assert out["recovered_fraction"] == 1.0     # 1 из 1 применённой, а не 1 из 2
