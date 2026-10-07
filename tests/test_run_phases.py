"""Фазы прогона без Р7: сводка ресурсов, вердикт утечки, запись отчётов."""
import json

import pytest

import r7_Testovarka as r7mod
from r7.run_summary import report_summary, resource_summary, run_leak_verdict


def _op(name, t=1.0, ram=None, cpu=None, cpu_n=None, error=None):
    return {"name": name, "time": t, "ram": ram, "cpu": cpu, "cpu_normalized": cpu_n,
            "error": error, "runs": [t], "run_statuses": ["ok"], "n_runs": 1}


def test_resource_summary_and_report_section():
    res = resource_summary([_op("A", ram=900.0, cpu=80.0, cpu_n=5.0),
                            _op("B", ram=1100.0, cpu=120.0, cpu_n=7.5), _op("C")])
    assert res["peak_ram_mb"] == 1100.0 and res["min_ram_mb"] == 900.0
    assert res["avg_ram_mb"] == 1000.0 and res["peak_cpu_pct"] == 120.0
    assert res["avg_cpu_normalized_pct"] == 6.2 and res["ram_vals"] == [900.0, 1100.0]
    section = report_summary(res, {"leak": None})
    assert "ram_vals" not in section and section["leak_detection"] == {"leak": None}
    assert list(section)[:6] == ["peak_ram_mb", "avg_ram_mb", "min_ram_mb", "peak_cpu_pct",
                                 "peak_cpu_normalized_pct", "avg_cpu_normalized_pct"]


def test_resource_summary_without_data():
    res = resource_summary([_op("A"), "мусор"])
    assert all(res[k] is None for k in ("peak_ram_mb", "avg_ram_mb", "peak_cpu_pct"))
    assert "leak_detection" not in report_summary(res)


def test_run_leak_verdict_never_claims_leak_in_operations_run():
    samples = [{"t": i * 60.0, "heap_mb": 100.0 + i * 50, "doc_count": 5} for i in range(40)]
    v = run_leak_verdict(samples)
    assert v["leak"] is None and v["applicable"] is False
    assert "soak" in v["verdict"] and v["slope_mb_per_hour"] > 0


def test_run_leak_verdict_short_run_passes_through():
    v = run_leak_verdict([{"t": 0.0, "heap_mb": 100.0}])
    assert v["leak"] is None and "applicable" not in v


@pytest.fixture
def app(tmp_path):
    a = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    a.reports_folder = tmp_path
    a.current_version_info = {"name": "Р7-Офис", "version": "2026.3.2.3229"}
    a._applied_r7_window_size = None
    a._run_environment = None
    a._cached_cpu_count = 4
    return a


def test_write_run_reports_writes_three_files(app, tmp_path, log):
    results = [_op("Выделение всех ячеек (Ctrl+A)", 0.22, ram=900.0, cpu=80.0, cpu_n=5.0)]
    res = resource_summary(results)
    ts, html = app._write_run_reports(results, tmp_path / "f.xlsx", 7.9, res, None, log)
    data = json.loads((tmp_path / f"performance_full_{ts}.json").read_text(encoding="utf-8"))
    assert data["version"] == "Р7-Офис 2026.3.2.3229"
    assert data["summary"]["peak_ram_mb"] == 900.0
    assert (tmp_path / f"Performance_Report_{ts}.xlsx").exists()
    assert html.exists() and "Ctrl+A" in html.read_text(encoding="utf-8")


def test_excel_failure_keeps_json_and_html(app, tmp_path, log, monkeypatch):
    """Открытый в Excel .xlsx (PermissionError) не лишает прогон JSON и HTML."""
    import openpyxl

    class _Boom:
        def __init__(self, *a, **k):
            raise PermissionError("файл открыт в Excel")
    monkeypatch.setattr(openpyxl, "Workbook", _Boom)
    results = [_op("A", 1.0)]
    ts, html = app._write_run_reports(results, tmp_path / "f.xlsx", None,
                                      resource_summary(results), None, log)
    assert (tmp_path / f"performance_full_{ts}.json").exists() and html.exists()
    assert any("Excel-отчёт не сохранён" in m for m in log.messages)


# ── «Открытие файла» из повторов (_open_result) ──────────────────────────

def _open(t, status="ok", cold=1000.0, warm=None):
    return {"open_elapsed": t, "cold_start_ms": cold,
            "warm_start_ms": warm if warm is not None else t * 1000 - cold,
            "status": status, "ready_marker": "bold", "x2t": None, "disk": None}


@pytest.fixture
def bare(log):
    a = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    a.add_test_log = log
    return a


def test_open_result_median_of_all_opens(bare):
    r = bare._open_result([_open(9.1), _open(9.0), _open(12.5), _open(9.2), _open(9.05)],
                          True, None)
    assert r["name"] == "Открытие файла" and r["error"] is None
    assert r["time"] == r["median"] == pytest.approx(9.1)
    assert r["first_run_discarded"] is False and r["n_runs"] == 5   # холодные старты независимы
    assert r["cold_start_ms"] == 1000.0 and r["ready_markers"] == ["bold"] * 5


def test_open_result_excludes_timeouts_and_flags_all_timeout(bare):
    r = bare._open_result([_open(9.0), _open(120.0, "timeout"), _open(9.4)], True, None)
    assert r["n_timeouts"] == 1 and r["median"] == pytest.approx(9.2)
    allt = bare._open_result([_open(120.0, "timeout")] * 2, True, None)
    assert "таймаут" in allt["error"]


def test_open_result_not_ready_document_is_error(bare, log):
    r = bare._open_result([_open(9.0)], False, None)
    from r7.config import _OPEN_NOT_READY
    assert r["error"] == _OPEN_NOT_READY
    assert any(m.startswith("❌") for m in log.messages)


def test_open_result_carries_resource_sample(bare):
    sample = {"ram_mb": 900.0, "cpu_raw_pct": 80.0, "cpu_norm_pct": 5.0,
              "threads": 40, "uptime_sec": 10.0}
    r = bare._open_result([_open(9.0)], True, sample)
    assert (r["ram"], r["cpu"], r["threads"]) == (900.0, 80.0, 40)
