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
                        lambda log_cb=None: (r7mod.DEFAULT_CDP_PORT,
                                             ["--ascdesktop-support-debug-info"]))


def _patch_popen(monkeypatch, procs=None):
    """procs: список объектов, возвращаемых последовательными Popen()-ами.
    По умолчанию — два независимых Mock (до и после "сбоя")."""
    procs = procs or [Mock(), Mock()]
    it = iter(procs)
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(side_effect=lambda *a, **k: next(it)))
    return procs


def test_applies_all_edits_and_reports_count(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

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
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

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
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory(before_ok=False))

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
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

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
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert out["process_died_cleanly"] is False
    # Перезапуск всё равно происходит — таймаут не должен обрывать сценарий.
    assert out["proc"] is second


def test_computes_recovered_fraction(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")
    edits = [lambda c: None for _ in range(4)]

    out = r7mod.run_crash_recovery_scenario(
        "r7.exe", f, edits, verify_recovered=lambda c: 3)

    assert out["recovered_count"] == 3
    assert out["recovered_fraction"] == pytest.approx(0.75)


def test_recovered_fraction_none_without_edits(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert out["recovered_fraction"] is None


def test_verify_recovered_not_called_when_reconnect_fails(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory(after_ok=False))

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
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    def verify(c):
        raise RuntimeError("не удалось прочитать документ")

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=verify)

    assert out["recovered_count"] is None
    assert out["connected_after_crash"] is True


def test_time_to_reconnect_is_nonnegative(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert out["time_to_reconnect_sec"] >= 0.0


def test_uses_default_cdp_port_when_unset(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")

    r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert _FakeConnector.instances[0].port == r7mod.DEFAULT_CDP_PORT
    assert _FakeConnector.instances[1].port == r7mod.DEFAULT_CDP_PORT


def test_both_connectors_use_same_filename_hint(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "report.xlsx"
    f.write_text("x")

    r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert _FakeConnector.instances[0].filename_hint == "report.xlsx"
    assert _FakeConnector.instances[1].filename_hint == "report.xlsx"


# ── регрессии, найденные code-review ────────────────────────────────────

def test_raises_when_webdriver_not_ok(no_sleep, monkeypatch, tmp_path):
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", False)
    f = tmp_path / "a.docx"
    f.write_text("x")

    with pytest.raises(RuntimeError):
        r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)


def test_closes_both_connectors_on_happy_path(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

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
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory(before_ok=False))

    f = tmp_path / "a.docx"
    f.write_text("x")

    r7mod.run_crash_recovery_scenario("r7.exe", f, [], verify_recovered=lambda c: 0)

    assert _FakeConnector.instances[0].closed is True


def test_new_connector_closed_even_when_verify_recovered_raises(no_sleep, monkeypatch, tmp_path):
    _patch_popen(monkeypatch)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

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
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", factory)

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
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

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
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    f = tmp_path / "a.docx"
    f.write_text("x")
    verify = Mock(return_value=1)

    out = r7mod.run_crash_recovery_scenario("r7.exe", f, [lambda c: None],
                                            verify_recovered=verify)

    assert out["process_died_cleanly"] is True
    verify.assert_called_once()
    assert out["recovered_count"] == 1
