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
from unittest.mock import Mock

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


@pytest.fixture(autouse=True)
def _no_real_port_check(monkeypatch):
    """port=None (по умолчанию во всех тестах) заставляет run_multidoc
    вызвать _pick_cdp_port, а она реально стучится в 127.0.0.1:8080/8081/8082
    сокетом (_cdp_port_free) — без этой заглушки тесты делают настоящий
    сетевой I/O (замечено: ~5с на файл вместо миллисекунд), что и медленно,
    и противоречит конвенции тестов этого репозитория (см. conftest.py)."""
    monkeypatch.setattr(r7mod, "_pick_cdp_port",
                        lambda log_cb=None: (r7mod.DEFAULT_CDP_PORT,
                                             ["--ascdesktop-support-debug-info"]))


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


def test_run_multidoc_closes_connectors_even_when_ops_per_doc_raises(no_sleep, monkeypatch, tmp_path):
    """close() стоит в отдельном цикле ПОСЛЕ ThreadPoolExecutor, а
    _run_one сам ловит исключения из ops_per_doc и не даёт им выйти наружу
    — но это стоит проверить явно, а не полагаться на то, что оба факта
    останутся верны при будущих правках."""
    f1, f2 = (tmp_path / n for n in ("a.xlsx", "b.xlsx"))
    for f in (f1, f2):
        f.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    def failing_ops(conn, path):
        raise RuntimeError(f"упал на {conn.filename_hint}")

    out = r7mod.run_multidoc("r7.exe", [f1, f2], failing_ops)

    assert out["per_file"]["a.xlsx"]["ok"] is False
    assert out["per_file"]["b.xlsx"]["ok"] is False
    assert len(_FakeConnector.instances) == 2
    assert all(c.closed for c in _FakeConnector.instances)


def test_run_multidoc_close_exception_on_one_connector_does_not_block_others(no_sleep, monkeypatch, tmp_path):
    calls = {"n": 0}

    class _BreakableConnector(_FakeConnector):
        def close(self):
            calls["n"] += 1
            super().close()
            if calls["n"] == 1:
                raise RuntimeError("close() упал")

    def factory(port=None, filename_hint=None, log_cb=None):
        return _BreakableConnector(port=port, filename_hint=filename_hint, log_cb=log_cb)

    f1, f2 = (tmp_path / n for n in ("a.xlsx", "b.xlsx"))
    for f in (f1, f2):
        f.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", factory)

    # Не должно поднять исключение наружу.
    r7mod.run_multidoc("r7.exe", [f1, f2], lambda c, p: "ok")

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


# ── регрессии, найденные code-review ────────────────────────────────────

def test_run_multidoc_rejects_duplicate_basenames(no_sleep, monkeypatch, tmp_path):
    """Два файла с одинаковым именем из разных папок сломали бы
    маршрутизацию по filename_hint (H5 различает документы только по
    базовому имени) — должно быть явной ошибкой, а не молчаливой путаницей."""
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    f1, f2 = d1 / "report.xlsx", d2 / "report.xlsx"
    f1.write_text("x")
    f2.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    with pytest.raises(ValueError, match="report.xlsx"):
        r7mod.run_multidoc("r7.exe", [f1, f2], lambda c, p: None)


def test_run_multidoc_raises_when_webdriver_not_ok(no_sleep, monkeypatch, tmp_path):
    f1 = tmp_path / "a.xlsx"
    f1.write_text("x")
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", False)

    with pytest.raises(RuntimeError):
        r7mod.run_multidoc("r7.exe", [f1], lambda c, p: None)


def test_run_multidoc_connects_files_in_parallel_not_sequentially(no_sleep, monkeypatch, tmp_path):
    """Регрессия: connect() раньше вызывался в обычном for-цикле — N файлов
    с медленным connect() стоили бы N*connect_timeout вместо ~connect_timeout.
    Детерминированный тест на параллелизм (barrier), не на тайминг."""
    f1, f2 = (tmp_path / n for n in ("a.xlsx", "b.xlsx"))
    for f in (f1, f2):
        f.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))

    entered = threading.Barrier(2, timeout=5)

    class _BarrierConnector(_FakeConnector):
        def connect(self, timeout=None):
            entered.wait()  # взрывается по таймауту при последовательном connect()
            return super().connect(timeout=timeout)

    def factory(port=None, filename_hint=None, log_cb=None):
        return _BarrierConnector(port=port, filename_hint=filename_hint, log_cb=log_cb)

    monkeypatch.setattr(r7mod, "R7WebDriverConnector", factory)

    out = r7mod.run_multidoc("r7.exe", [f1, f2], lambda c, p: "ok")

    assert set(out["opened"]) == {"a.xlsx", "b.xlsx"}


def test_run_multidoc_log_cb_is_thread_safe(no_sleep, monkeypatch, tmp_path):
    """Регрессия: log_cb вызывается конкурентно из ThreadPoolExecutor — без
    внутреннего замка это было бы первым местом в кодовой базе, где log_cb
    зовётся не из одного потока (риск для Tk-колбэков вроде add_test_log).

    conn.log_cb — это ИМЕННО обёрнутый (locked) log_cb, который run_multidoc
    передал в R7WebDriverConnector(...); вызываем его из ops_per_doc, чтобы
    проверить реальную обёртку, а не отдельную функцию. Без log_lock
    неатомарная пара append/append дала бы порядок [1, 1, 2, 2] вместо
    гарантированного [1, 2, 1, 2] — два потока входят в барьер одновременно
    и гонятся за доступом к общему списку сразу после него."""
    f1, f2 = (tmp_path / n for n in ("a.xlsx", "b.xlsx"))
    for f in (f1, f2):
        f.write_text("x")
    monkeypatch.setattr(r7mod.subprocess, "Popen", Mock(return_value=Mock()))

    entered = threading.Barrier(2, timeout=5)
    monkeypatch.setattr(r7mod, "R7WebDriverConnector", _make_factory())

    def ops(conn, path):
        entered.wait()
        conn.log_cb("x")  # conn.log_cb — обёртка run_multidoc с log_lock
        return "ok"

    # Патчим саму базовую функцию, которую оборачивает log_lock, через
    # прямую подмену поведения calls.append внутри неё нельзя — вместо
    # этого проверяем эффект обёртки другим способом: конкурентный доступ
    # к calls внутри самого log_cb, переданного в run_multidoc.
    hits = []

    def racy_base_log_cb(msg):
        hits.append(1)
        hits.append(2)

    r7mod.run_multidoc("r7.exe", [f1, f2], ops, log_cb=racy_base_log_cb)

    assert hits == [1, 2, 1, 2]
