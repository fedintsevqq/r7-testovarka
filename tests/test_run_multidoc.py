"""Тесты мультидокументного режима (этап 3, H4): открытие N файлов в одном
экземпляре Р7 и параллельное выполнение ops_per_doc через ThreadPoolExecutor.

subprocess.Popen, R7WebDriverConnector и time.sleep мокаются — тесты
проверяют логику оркестрации (что и в каком порядке запускается,
маршрутизация по filename_hint, обработка ошибок), не реальный Р7.
Механизм ("второй Popen открывает документ в уже запущенном процессе, а не
порождает новый") подтверждён живым прогоном отдельно — см. комментарий
перед run_multidoc в r7_Testovarka.py.
"""
import threading
import time as time_mod
from unittest.mock import Mock, patch

import pytest

import r7_Testovarka as r7mod


class _FakeConnector:
    """Дублёр R7WebDriverConnector: connect() успешен, если filename_hint не
    в списке failing_names; close() и filename_hint отслеживаются."""

    instances = []

    def __init__(self, port=None, filename_hint=None, log_cb=None,
                failing_names=()):
        self.port = port
        self.filename_hint = filename_hint
        self.log_cb = log_cb
        self._failing_names = failing_names
        self.closed = False
        _FakeConnector.instances.append(self)

    def connect(self, timeout=None):
        return self.filename_hint not in self._failing_names

    def close(self):
        self.closed = True


def _make_factory(failing_names=()):
    def factory(port=None, filename_hint=None, log_cb=None):
        return _FakeConnector(port=port, filename_hint=filename_hint,
                              log_cb=log_cb, failing_names=failing_names)
    return factory


@pytest.fixture(autouse=True)
def _reset_fake_connector_instances():
    _FakeConnector.instances = []
    yield
    _FakeConnector.instances = []


@pytest.fixture
def no_sleep(monkeypatch):
    """run_multidoc спит launch_wait_sec + N*additional_wait_sec секунд по
    умолчанию — тесты не должны реально ждать."""
    monkeypatch.setattr(r7mod.time, "sleep", Mock())


def test_run_multidoc_requires_at_least_one_file():
    with pytest.raises(ValueError):
        r7mod.run_multidoc("r7.exe", [], lambda c, p: None)


def test_run_multidoc_launches_first_file_with_debug_flag(no_sleep, monkeypatch, tmp_path):
    f1 = tmp_path / "a.xlsx"
    f1.write_text("x")
    popen = Mock(return_value=Mock())
    monkeypatch.setattr(r7mod.subprocess, "Popen", popen)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    r7mod.run_multidoc("r7.exe", [f1], lambda c, p: "ok")

    popen.assert_any_call(["r7.exe", str(f1), "--ascdesktop-support-debug-info"])


def test_run_multidoc_launches_each_additional_file_separately(no_sleep, monkeypatch, tmp_path):
    f1, f2, f3 = (tmp_path / n for n in ("a.xlsx", "b.xlsx", "c.xlsx"))
    for f in (f1, f2, f3):
        f.write_text("x")
    popen = Mock(return_value=Mock())
    monkeypatch.setattr(r7mod.subprocess, "Popen", popen)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    r7mod.run_multidoc("r7.exe", [f1, f2, f3], lambda c, p: "ok")

    assert popen.call_count == 3
    for f in (f1, f2, f3):
        popen.assert_any_call(["r7.exe", str(f), "--ascdesktop-support-debug-info"])


def test_run_multidoc_waits_longer_after_first_launch(no_sleep, monkeypatch, tmp_path):
    """Первый файл — холодный старт процесса (launch_wait_sec), остальные —
    открытие в уже запущенном Р7 (короче, additional_wait_sec)."""
    f1, f2 = (tmp_path / n for n in ("a.xlsx", "b.xlsx"))
    for f in (f1, f2):
        f.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())
    sleeps = []
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: sleeps.append(s))

    r7mod.run_multidoc("r7.exe", [f1, f2], lambda c, p: "ok",
                       launch_wait_sec=14.0, additional_wait_sec=6.0)

    assert sleeps == [14.0, 6.0]


def test_run_multidoc_routes_ops_per_doc_to_matching_file(no_sleep, monkeypatch, tmp_path):
    f1, f2 = (tmp_path / n for n in ("a.xlsx", "b.xlsx"))
    for f in (f1, f2):
        f.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    seen = {}

    def ops(conn, path):
        seen[conn.filename_hint] = path
        return "done"

    r7mod.run_multidoc("r7.exe", [f1, f2], ops)

    assert seen == {"a.xlsx": f1, "b.xlsx": f2}


def test_run_multidoc_reports_per_file_results(no_sleep, monkeypatch, tmp_path):
    f1 = tmp_path / "a.xlsx"
    f1.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    out = r7mod.run_multidoc("r7.exe", [f1], lambda c, p: {"time": 1.23})

    assert out["opened"] == ["a.xlsx"]
    assert out["failed_to_open"] == []
    assert out["per_file"]["a.xlsx"] == {"ok": True, "result": {"time": 1.23}, "error": None}


def test_run_multidoc_marks_failed_connect_without_calling_ops(no_sleep, monkeypatch, tmp_path):
    f1, f2 = (tmp_path / n for n in ("a.xlsx", "b.xlsx"))
    for f in (f1, f2):
        f.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory(failing_names=("b.xlsx",)))

    called_for = []
    out = r7mod.run_multidoc("r7.exe", [f1, f2],
                             lambda c, p: called_for.append(c.filename_hint))

    assert out["failed_to_open"] == ["b.xlsx"]
    assert called_for == ["a.xlsx"]
    assert out["per_file"]["b.xlsx"]["ok"] is False
    assert out["per_file"]["b.xlsx"]["result"] is None


def test_run_multidoc_catches_exception_in_ops_per_doc(no_sleep, monkeypatch, tmp_path):
    f1 = tmp_path / "a.xlsx"
    f1.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    def boom(conn, path):
        raise RuntimeError("документ упал")

    out = r7mod.run_multidoc("r7.exe", [f1], boom)

    assert out["per_file"]["a.xlsx"]["ok"] is False
    assert "документ упал" in out["per_file"]["a.xlsx"]["error"]


def test_run_multidoc_closes_all_connectors(no_sleep, monkeypatch, tmp_path):
    f1, f2 = (tmp_path / n for n in ("a.xlsx", "b.xlsx"))
    for f in (f1, f2):
        f.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    r7mod.run_multidoc("r7.exe", [f1, f2], lambda c, p: "ok")

    assert len(_FakeConnector.instances) == 2
    assert all(c.closed for c in _FakeConnector.instances)


def test_run_multidoc_returns_proc_handle(no_sleep, monkeypatch, tmp_path):
    f1 = tmp_path / "a.xlsx"
    f1.write_text("x")
    fake_proc = Mock()
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=fake_proc))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    out = r7mod.run_multidoc("r7.exe", [f1], lambda c, p: "ok")

    assert out["proc"] is fake_proc


def test_run_multidoc_uses_default_cdp_port_when_unset(no_sleep, monkeypatch, tmp_path):
    f1 = tmp_path / "a.xlsx"
    f1.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    r7mod.run_multidoc("r7.exe", [f1], lambda c, p: "ok")

    assert _FakeConnector.instances[0].port == r7mod.DEFAULT_CDP_PORT


def test_run_multidoc_runs_ops_per_doc_concurrently(no_sleep, monkeypatch, tmp_path):
    """Не строгий тест на тайминг, а детерминированный тест на параллелизм:
    оба ops_per_doc должны быть ВНУТРИ вызова одновременно (оба выставляют
    Event и ждут друг друга) — последовательное выполнение зависло бы и
    тест упал по таймауту join()."""
    f1, f2 = (tmp_path / n for n in ("a.xlsx", "b.xlsx"))
    for f in (f1, f2):
        f.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    entered = threading.Barrier(2, timeout=5)

    def ops(conn, path):
        entered.wait()  # взрывается по таймауту, если выполняется последовательно
        return "ok"

    out = r7mod.run_multidoc("r7.exe", [f1, f2], ops, max_workers=2)

    assert out["per_file"]["a.xlsx"]["ok"] is True
    assert out["per_file"]["b.xlsx"]["ok"] is True
