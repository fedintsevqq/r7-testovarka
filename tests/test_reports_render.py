"""HTML-отчёты на шаблонах (r7_reports.py, templates/html/)."""
import json
import re
from pathlib import Path

import pytest

import r7_reports
import r7_Testovarka as r7mod

EVIL = "<script>alert(1)</script>"


@pytest.fixture
def app():
    inst = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    inst._applied_r7_window_size = None
    inst._run_environment = None
    return inst


def _op(name, t, runs=None, **extra):
    r = {"name": name, "time": t, "error": None, "runs": runs or [t], "n_runs": len(runs or [t]),
         "run_statuses": ["ok"] * len(runs or [t]), "mad": 0.01, "min": t, "max": t,
         "ram": 900.0, "cpu": 80.0, "cpu_sec": 1.0}
    r.update(extra)
    return r


def _no_raw(html_text):
    assert EVIL not in html_text
    assert "&lt;script&gt;" in html_text


def test_run_report_escapes_and_summarizes(app, tmp_path):
    f = tmp_path / "файл.xlsx"
    f.write_bytes(b"x")
    results = [_op("Открытие файла", 9.9, cold_start_ms=2690, warm_start_ms=7220),
               _op("Вставка большого массива (Ctrl+V)", 21.99),
               _op(EVIL, 0.0, error=EVIL)]
    out = app._generate_html_report(results, f, 9.9, EVIL, [], [], 2893.0, 1000.0, 800.0, 546.0,
                                    summary={"peak_ram_mb": 2893.0},
                                    system={"os": EVIL, "environment": {"warnings": [EVIL]}})
    _no_raw(out)
    assert "Самая долгая операция" in out and "21,99" in out
    assert "1 с ошибкой" in out                       # плитка доверия
    assert "Условия прогона" in out                   # предупреждение окружения


def test_run_report_badges(app, tmp_path):
    f = tmp_path / "a.xlsx"
    f.write_bytes(b"x")
    results = [_op("A", 1.0, runs=[1.0, 185.0], run_statuses=["ok", "timeout"], n_timeouts=1),
               _op("B", 1.0, n_unverified=1, runs_independent=False, below_floor=True)]
    out = app._generate_html_report(results, f, 1.0, "v", [], [], None, None, None, None)
    for text in ("таймаут×1", "без подтверждения×1", "зависимые повторы", "&lt;порога"):
        assert text in out


def test_comparison_leads_with_regressions(app):
    base = [_op("A", 1.0, runs=[1.0, 1.01, 0.99, 1.02, 1.0, 0.98])]
    slow = [_op("A", 2.0, runs=[2.0, 2.02, 1.98, 2.01, 2.0, 1.99])]
    ds = [{"path": "b", "version": "2026.3.1", "data": {"results": base, "measure_schema": 8}},
          {"path": "n", "version": EVIL, "data": {"results": slow, "measure_schema": 8, "system": {"os": EVIL}}}]
    out = app._generate_comparison_html(ds, "b")
    _no_raw(out)
    assert "Регрессии: 1" in out
    assert out.index("Регрессии: 1") < out.index('id="timeChart"')   # вывод — до графиков
    assert "+100,0 %" in out


def test_trends_render_and_escape(app):
    runs = [{"path": None, "ts_raw": "", "ts_disp": f"0{i}.10 10:00", "version": EVIL, "schema": 8,
             "results": {"A": _op("A", 1.0 + i / 10)}} for i in range(1, 3)]
    out = app._generate_trends_html(runs)
    _no_raw(out)
    assert '<canvas id="trend0"' in out


def test_batch_and_custom_render(app, tmp_path):
    out = app._generate_batch_summary_html([
        {"file": "a.msi", "version": EVIL, "success": False, "error": EVIL},
        {"file": "b.msi", "version": "2026.3.2", "success": True, "open_elapsed": 7.7,
         "vlookup_elapsed": 1.8, "peak_ram": 3800.0, "peak_cpu": 700.0}])
    _no_raw(out)
    html_text = r7_reports.render("custom.html", **r7_reports.custom_model(
        {"filename": EVIL, "open_elapsed": 7.7, "vlookup_elapsed": None, "vlookup_error": EVIL,
         "timestamp": "06.10.2026", "cache_cleared": False, "data_ready": False}))
    _no_raw(html_text)
    assert "таймаут загрузки" in html_text


def test_script_payload_cannot_close_tag():
    s = r7_reports.json_for_script({"x": "</script><b>"})
    assert "</script" not in s and json.loads(str(s)) == {"x": "</script><b>"}


def test_base_defines_both_themes_and_print():
    base = (Path(r7_reports.TEMPLATES_DIR) / "base.html").read_text(encoding="utf-8")
    assert re.search(r'@media \(prefers-color-scheme: dark\)\s*\{\s*:root:not\(\[data-theme="light"\]\)', base)
    assert ':root[data-theme="dark"]' in base
    assert "@media print" in base and "prefers-reduced-motion" in base


@pytest.mark.parametrize("value, text", [(1234.5, "1 234,5"), (None, "—"), (0.0, "0,0")])
def test_fmt_num(value, text):
    assert r7_reports.fmt_num(value) == text
