"""Batch целиком, без Р7 и без установки (правило 6 CLAUDE.md): цикл по
версиям и прогон одной версии с подменёнными границами."""
import json
import threading
import time
from pathlib import Path

import pytest

import r7_Testovarka as r7mod
import r7.runs as runs


def _bare():
    a = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    a._applied_r7_window_size = None
    a._cached_cpu_count = 4
    a._webdriver_connector = None
    return a


# ── цикл по версиям (_batch_worker) ──────────────────────────────────────

@pytest.fixture
def batch(monkeypatch):
    a = _bare()
    a.calls, a.logs, a.done = [], [], []
    a._capture_environment = lambda log_cb=None: {}
    a.uninstall_current_version = lambda: a.calls.append("uninstall") or True
    a.install_fail = set()

    def install(dist):
        a.calls.append(("install", dist.name))
        return dist.name not in a.install_fail
    a.install_version = install
    a.detect_current_version = lambda: None
    a.current_version_info = {"name": "Р7-Офис", "version": "2026.3.2.3229"}
    a._clear_r7_cache = lambda: 0

    def single(test_file, label, log_cb, stop_event, pause_event):
        a.calls.append(("run", label))
        return {"open_elapsed": 8.0, "vlookup_elapsed": 2.8, "results": []}
    a._batch_run_single_version = single
    # time — только для r7.runs: 2 с после удаления версии → 0, опрос паузы —
    # как есть (глобальная подмена time.sleep остановила бы и потоки теста).
    from types import SimpleNamespace
    monkeypatch.setattr(runs, "time", SimpleNamespace(
        sleep=lambda sec: time.sleep(min(sec, 0.01)), perf_counter=time.perf_counter,
        time=time.time))
    a.PAUSE_POLL_SEC = 0.01
    return a


def _run(a, versions, stop_on_error=False, stop=None, pause=None):
    stop = stop or threading.Event()
    pause = pause or threading.Event()
    a._batch_worker(versions, Path("f.xlsx"), stop_on_error, False,
                    a.logs.append, lambda t: None, lambda f, t: None, lambda n: None,
                    lambda res, err: a.done.append((res, err)), stop, pause)
    return a.done[-1]


VERSIONS = [Path("R7-2026.3.1.msi"), Path("R7-2026.3.2.msi")]


def test_each_version_installed_then_measured_in_order(batch):
    res, errors = _run(batch, VERSIONS)
    assert errors == 0 and [r["success"] for r in res] == [True, True]
    installs = [c[1] for c in batch.calls if c[0] == "install"]
    assert installs == ["R7-2026.3.1.msi", "R7-2026.3.2.msi"]
    assert [c[0] for c in batch.calls if isinstance(c, tuple)] == ["install", "run", "install", "run"]


def test_install_error_stops_when_asked(batch):
    batch.install_fail = {"R7-2026.3.1.msi"}
    res, errors = _run(batch, VERSIONS, stop_on_error=True)
    assert errors == 1 and len(res) == 1 and "Установка" in res[0]["error"]
    assert not any(c == ("install", "R7-2026.3.2.msi") for c in batch.calls)


def test_install_error_continues_otherwise(batch):
    batch.install_fail = {"R7-2026.3.1.msi"}
    res, errors = _run(batch, VERSIONS, stop_on_error=False)
    assert errors == 1 and [r["success"] for r in res] == [False, True]


def test_stop_before_start_runs_nothing(batch):
    stop = threading.Event()
    stop.set()
    res, errors = _run(batch, VERSIONS, stop=stop)
    assert res == [] and batch.calls == []


def test_pause_really_holds_the_run(batch):
    """Кнопка «Пауза» устанавливает событие — прогон ждёт, пока его не
    снимут (прежде wait() на установленном событии возвращался сразу)."""
    pause = threading.Event()
    pause.set()
    batch.PAUSE_POLL_SEC = 0.01
    released = []

    def release():
        time.sleep(0.15)
        released.append(len([c for c in batch.calls if isinstance(c, tuple) and c[0] == "run"]))
        pause.clear()
    threading.Thread(target=release).start()
    res, _ = _run(batch, VERSIONS[:1], pause=pause)
    assert released == [0]                      # пока стояла пауза, замер не начался
    assert res[0]["success"] and any("Пауза" in m for m in batch.logs)


def test_stop_during_pause_ends_wait(batch):
    pause, stop = threading.Event(), threading.Event()
    pause.set()
    batch.PAUSE_POLL_SEC = 0.01
    threading.Timer(0.05, stop.set).start()
    _run(batch, VERSIONS, pause=pause, stop=stop)
    assert any("Остановлено во время паузы" in m for m in batch.logs)
    assert not any(isinstance(c, tuple) and c[0] == "run" for c in batch.calls)


# ── одна версия (_batch_run_single_version) ──────────────────────────────

class _Sampler:
    def __init__(self, **k):
        pass

    start = stop = join = lambda self, *a, **k: None

    def snapshot(self):
        return []


@pytest.fixture
def single(tmp_path, monkeypatch):
    a = _bare()
    a.calls, a.logs = [], []
    a.reports_folder = tmp_path / "Reports"
    a.reports_folder.mkdir()
    a.current_version_info = {"name": "Р7-Офис", "version": "2026.3.2.3229"}
    a._run_environment = None
    a.ready = True
    a._find_r7_path = lambda: "editors.exe"
    for name in ("_wait_system_quiet", "_purge_os_file_cache", "_remove_stale_lock_files",
                 "_x2t", "_fix_r7_window_geometry", "_close_update_dialog_if_exists",
                 "_cdp_ensure_connected", "_capture_cdp_ui_baseline", "_cdp_log_api_info",
                 "_suspend_autosave", "_restore_autosave", "_cleanup_x2t_temp_pdfs",
                 "_close_webdriver_connector"):
        setattr(a, name, (lambda n: lambda *x, **k: a.calls.append(n))(name))
    a._prepare_webdriver_launch = lambda **k: []
    a._find_r7_window = lambda stem=None: 1
    a._focus_r7_window = lambda hwnd, log_cb=None: True
    a._monitor_update_dialog = lambda ev, log_cb=None: None

    def ready(find_hwnd, timeout=120, log_cb=None):
        a._ready_at, a._ready_marker = time.perf_counter() + 0.5, "bold"
        return a.ready
    a._wait_until_r7_ready = ready
    a._x2t_since = lambda mark: []
    a._get_r7_processes = lambda *x, **k: []
    a._sample_r7_resources = lambda procs, measure_cpu=True: None
    a._close_r7_gracefully = lambda hwnd, log_cb=None, timeout=10: a.calls.append("close") or True
    a._r7_gone = lambda: True
    a._emergency_close_r7 = lambda f, log_cb=None: a.calls.append("emergency") or True
    a.measured = []

    def measure(name, func, n, find_hwnd, log_cb, stop_event, **kw):
        a.measured.append((name, n))
        return {"name": name, "time": 1.0, "error": None, "runs": [1.0] * n,
                "run_statuses": ["ok"] * n, "n_runs": n, "ram": 800.0, "cpu": 40.0}
    a._measure_op_repeated = measure
    # subprocess — только для r7.runs: глобальная подмена Popen ломала
    # platform.platform() на Python 3.11 (он сам зовёт «ver» через subprocess).
    from types import SimpleNamespace
    monkeypatch.setattr(runs, "subprocess", SimpleNamespace(
        Popen=lambda *x, **k: a.calls.append("popen")))
    monkeypatch.setattr(runs, "_disk_snapshot", lambda: None)
    monkeypatch.setattr(runs, "_disk_delta", lambda *x, **k: None)
    fixture = tmp_path / "файл-50К.xlsx"
    fixture.write_bytes(b"x")
    a.fixture = fixture
    return a


def test_single_version_measures_all_tests_with_batch_repeats(single):
    out = single._batch_run_single_version(single.fixture, "2026.3.2", single.logs.append,
                                           threading.Event(), threading.Event())
    names = [n for n, _ in single.measured]
    edit = [n for n in r7mod.R7Testovarka.TEST_DEFINITIONS
            if n != r7mod.R7Testovarka.OPEN_TEST_NAME]
    assert names == edit
    runs_by = dict(single.measured)
    assert runs_by["Функция ВПР (50K строк)"] == r7mod.R7Testovarka.BATCH_TEST_RUNS
    assert runs_by["Сохранение в ODS (конвертация x2t)"] == r7mod.R7Testovarka.DEFAULT_FORMAT_TEST_RUNS
    data = json.loads(Path(out["json_path"]).read_text(encoding="utf-8"))
    assert data["version"] == "2026.3.2" and data["results"][0]["name"] == "Открытие файла"
    assert out["vlookup_elapsed"] == 1.0 and "close" in single.calls
    assert "emergency" not in single.calls


def test_single_version_not_loaded_skips_edit_tests(single):
    single.ready = False
    out = single._batch_run_single_version(single.fixture, "v", single.logs.append,
                                           threading.Event(), threading.Event())
    assert single.measured == [] and out["results"][0]["error"]


def test_single_version_exception_closes_r7(single):
    def boom(*a, **k):
        raise RuntimeError("Р7 упал")
    single._measure_op_repeated = boom
    with pytest.raises(RuntimeError):
        single._batch_run_single_version(single.fixture, "v", single.logs.append,
                                         threading.Event(), threading.Event())
    assert "emergency" in single.calls
