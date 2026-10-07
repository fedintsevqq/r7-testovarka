"""Прогон вкладки «Производительность» целиком, без Р7 (правило 6 CLAUDE.md:
вложенный код воркеров юнит-тесты не видели — теперь видят).

Подменены только границы с внешним миром: запуск и окно Р7, готовность
документа, CDP, ресурсы, сами замеры. Проверяется оркестровка: какие
тесты меряются, что пишется в отчёт, что Р7 закрывается при любом исходе.
"""
import json
import threading
from pathlib import Path

import pytest

import r7_Testovarka as r7mod
import r7.perf as perf

ALL_EDIT = [n for n in r7mod.R7Testovarka.TEST_DEFINITIONS
            if n != r7mod.R7Testovarka.OPEN_TEST_NAME]


class _Sampler:
    def __init__(self, **k):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def join(self, timeout=None):
        pass

    def snapshot(self):
        return []


@pytest.fixture
def worker(tmp_path, monkeypatch):
    a = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    calls, logs = [], []
    a.add_test_log = logs.append
    a.reports_folder = tmp_path / "Reports"
    a.current_version_info = {"name": "Р7-Офис", "version": "2026.3.2.3229"}
    a._applied_r7_window_size = None
    a._cached_cpu_count = 4
    a._webdriver_connector = None
    fixture = tmp_path / "файл-50К.xlsx"
    fixture.write_bytes(b"x")
    a.ready = True                                  # загрузился ли документ

    def rec(name, ret=None):
        def f(*args, **kw):
            calls.append(name)
            return ret
        return f

    a._capture_environment = rec("env", {})
    a._locate_test_file = rec("locate", fixture)
    a._find_r7_path = rec("r7_path", "editors.exe")
    a._launch_r7 = rec("launch", (100.0, 101.0, 0.2))

    def ready(find_hwnd, timeout=120):
        calls.append("ready")
        a._ready_at, a._ready_marker = 108.0, "bold"
        return a.ready
    a._wait_until_r7_ready = ready
    a._x2t_since = lambda mark: []
    a._open_disk_before = None
    a._monitor_update_dialog = lambda ev: None
    a._focus_r7_settled = rec("focus", True)
    a._find_r7_window = lambda stem=None: 1
    for name in ("_cdp_ensure_connected", "_capture_cdp_ui_baseline", "_cdp_log_api_info",
                 "_suspend_autosave", "_restore_autosave", "_cleanup_x2t_temp_pdfs",
                 "_close_webdriver_connector", "_set_perf_progress"):
        setattr(a, name, rec(name))
    a._get_r7_processes = lambda *x, **k: []
    a._sample_r7_resources = lambda procs, measure_cpu=True: None
    a._ui_call = lambda fn: calls.append("ui")
    a._close_r7_gracefully = rec("close", True)
    a._r7_gone = rec("gone", True)
    a._emergency_close_r7 = rec("emergency", True)
    measured = []

    def measure(name, func, runs, find_hwnd, log_cb, stop_event, **kw):
        measured.append((name, runs, callable(getattr(func, "prepare", None))))
        if getattr(a, "measure_raises", None) == name:
            raise RuntimeError("Р7 упал посреди операции")
        return {"name": name, "time": 1.0, "error": None, "runs": [1.0] * runs,
                "run_statuses": ["ok"] * runs, "n_runs": runs, "ram": 900.0, "cpu": 50.0,
                "cpu_normalized": 3.0}
    a._measure_op_repeated = measure
    monkeypatch.setattr(perf, "ResourceSampler", _Sampler)
    monkeypatch.setattr(perf, "_disk_snapshot", lambda: None)
    monkeypatch.setattr(perf, "_disk_delta", lambda *x, **k: None)
    a.calls, a.logs, a.measured = calls, logs, measured
    return a


def _report(a):
    files = list(a.reports_folder.glob("performance_full_*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_full_run_measures_enabled_tests_and_writes_reports(worker):
    enabled = {"Выделение всех ячеек (Ctrl+A)", "Функция ВПР (50K строк)"}
    worker._spreadsheet_worker(enabled, {"Функция ВПР (50K строк)": 3}, threading.Event())
    assert [m[0] for m in worker.measured] == [n for n in ALL_EDIT if n in enabled]
    assert dict((m[0], m[1]) for m in worker.measured)["Функция ВПР (50K строк)"] == 3
    assert all(m[2] for m in worker.measured)              # у тестов правки есть подготовка
    data = _report(worker)
    assert [r["name"] for r in data["results"]] == ["Открытие файла", *sorted(enabled, key=ALL_EDIT.index)]
    assert data["results"][0]["time"] == pytest.approx(8.0)  # готовность − старт
    assert data["version"] == "Р7-Офис 2026.3.2.3229" and data["summary"]["peak_ram_mb"] == 900.0
    assert (worker.calls.index("launch") < worker.calls.index("ready")
            < worker.calls.index("_cdp_ensure_connected") < worker.calls.index("close"))
    assert "emergency" not in worker.calls                 # штатное закрытие прошло
    assert list(worker.reports_folder.glob("Performance_Report_*.html"))


def test_document_not_loaded_skips_edit_tests_but_reports_open(worker):
    worker.ready = False
    worker._spreadsheet_worker(set(ALL_EDIT), {}, threading.Event())
    assert worker.measured == []
    res = _report(worker)["results"]
    assert len(res) == 1 and res[0]["error"]           # открытие помечено ошибкой
    assert any(m.startswith("❌") and "не загрузился" in m for m in worker.logs)


def test_stop_event_ends_run_between_operations(worker):
    stop = threading.Event()
    stop.set()
    worker._spreadsheet_worker(set(ALL_EDIT), {}, stop)
    assert worker.measured == []
    assert any("Остановлено пользователем" in m for m in worker.logs)
    assert "close" in worker.calls


def test_exception_mid_run_still_closes_r7(worker):
    worker.measure_raises = "Добавление нового листа"
    with pytest.raises(RuntimeError):
        worker._spreadsheet_worker(set(ALL_EDIT), {}, threading.Event())
    assert "emergency" in worker.calls and "_close_webdriver_connector" in worker.calls


def test_no_fixture_or_no_r7_stops_before_launch(worker):
    worker._locate_test_file = lambda: None
    worker._spreadsheet_worker(set(ALL_EDIT), {}, threading.Event())
    assert "launch" not in worker.calls and any("не найден" in m for m in worker.logs)


def test_window_never_appeared(worker):
    worker._launch_r7 = lambda r7_path, test_file: None
    worker._spreadsheet_worker(set(ALL_EDIT), {}, threading.Event())
    assert worker.measured == [] and any("Окно Р7 не появилось" in m for m in worker.logs)
    assert not Path(worker.reports_folder).exists() or not list(worker.reports_folder.glob("*.json"))


def test_open_test_repeats_launch_and_close(worker):
    """«Повторное открытие файла» ×3: два дополнительных цикла запуск →
    готовность → закрытие, основной запуск — третий."""
    launches = []
    worker._launch_r7 = lambda r7_path, test_file: launches.append(1) or (100.0, 101.0, 0.2)
    worker._get_r7_processes = lambda *x, **k: []
    open_name = r7mod.R7Testovarka.OPEN_TEST_NAME
    worker._spreadsheet_worker({open_name}, {open_name: 3}, threading.Event())
    assert len(launches) == 3
    assert worker.calls.count("close") == 3              # 2 повтора + штатное закрытие
    res = _report(worker)["results"][0]
    assert res["name"] == "Открытие файла" and len(res["runs"]) == 3
