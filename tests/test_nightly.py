"""Ночное сравнение прогонов (r7.nightly, этап 5 плана)."""
import json

from r7.nightly import (compare_reports, format_comparison, is_alarm, previous_report)


def _op(name, runs, error=None):
    t = sorted(runs)[len(runs) // 2] if runs else 0.0
    return {"name": name, "time": 0.0 if error else t, "runs": runs,
            "run_statuses": ["ok"] * len(runs), "first_run_discarded": False, "error": error}


def _rep(ops, schema=9, version="2026.3.2.3229"):
    return {"measure_schema": schema, "version": version, "results": ops}


STEADY = [1.00, 1.01, 0.99, 1.02, 1.00, 0.98]
SLOW = [1.50, 1.52, 1.49, 1.51, 1.50, 1.48]


def test_regression_detected_and_alarmed():
    cmp = compare_reports(_rep([_op("Ctrl+A", STEADY), _op("ВПР", STEADY)]),
                          _rep([_op("Ctrl+A", SLOW), _op("ВПР", STEADY)]))
    assert cmp["regressions"] == ["Ctrl+A"] and is_alarm(cmp)
    row = cmp["rows"][0]
    assert row["verdict"] == "РЕГРЕССИЯ" and round(row["pct"]) == 50


def test_speedup_is_not_alarm():
    cmp = compare_reports(_rep([_op("Ctrl+A", SLOW)]), _rep([_op("Ctrl+A", STEADY)]))
    assert cmp["speedups"] == ["Ctrl+A"] and not is_alarm(cmp)


def test_schema_change_disables_alarm():
    cmp = compare_reports(_rep([_op("Ctrl+A", STEADY)], schema=8),
                          _rep([_op("Ctrl+A", SLOW)], schema=9))
    assert cmp["schema_mismatch"] and cmp["regressions"] and not is_alarm(cmp)
    assert "схемы замера разные" in format_comparison(cmp, "a", "b")


def test_errors_new_and_missing_operations():
    cmp = compare_reports(_rep([_op("A", STEADY), _op("Старая", STEADY)]),
                          _rep([_op("A", [], error="x2t упал"), _op("Новая", STEADY)]))
    notes = {r["name"]: r["note"] for r in cmp["rows"]}
    assert notes["A"].startswith("ошибка сейчас: x2t упал") and notes["Новая"] == "новая операция"
    assert cmp["missing"] == ["Новая", "Старая"] and not is_alarm(cmp)


def test_version_change_is_noted():
    cmp = compare_reports(_rep([_op("A", STEADY)], version="v1"),
                          _rep([_op("A", STEADY)], version="v2"))
    assert cmp["version_changed"] and "версия Р7 сменилась" in format_comparison(cmp, "a", "b")


def test_previous_report_by_name_order(tmp_path):
    names = ["nightly_20261005_0200_full.json", "nightly_20261006_0200_full.json",
             "nightly_20261007_0200_full.json"]
    for n in names:
        (tmp_path / n).write_text(json.dumps(_rep([])), encoding="utf-8")
    assert previous_report(tmp_path, tmp_path / names[2]).name == names[1]
    assert previous_report(tmp_path, tmp_path / names[0]) is None


def test_previous_error_is_labelled_as_previous():
    cmp = compare_reports(_rep([_op("A", [], error="x2t упал")]), _rep([_op("A", STEADY)]))
    assert cmp["rows"][0]["note"].startswith("в прошлом прогоне ошибка")
