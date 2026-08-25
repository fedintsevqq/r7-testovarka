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

    def __init__(self, port=None, filename_hint=None, log_cb=None, connect_ok=True):
        self.port = port
        self.filename_hint = filename_hint
        self.log_cb = log_cb
        self._connect_ok = connect_ok
        self.edits_seen = []
        _FakeConnector.instances.append(self)

    def connect(self, timeout=None):
        return self._connect_ok


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
