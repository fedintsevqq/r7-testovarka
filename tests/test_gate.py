"""Экран «релиз готов / не готов» (r7/gate.py): вердикты по бюджету,
эталону и ошибкам, страница gate.html, JUnit XML."""
import xml.etree.ElementTree as ET

import r7_Testovarka as r7mod
from r7 import gate
from r7.gate import BUDGET, ERROR, NOT_MEASURED, OK, REGRESSION, gate_model, gate_page, junit_xml
from r7.suites import Suite

EVIL = "<script>alert(1)</script>"
STEADY = [1.00, 1.01, 0.99, 1.02, 1.00, 0.98]
SLOW = [1.50, 1.52, 1.49, 1.51, 1.50, 1.48]
FAST = [0.50, 0.52, 0.49, 0.51, 0.50, 0.48]
OPEN = gate.OPEN_TEST_NAME


def _op(name, runs, error=None, **extra):
    t = sorted(runs)[len(runs) // 2] if runs else 0.0
    r = {"name": name, "time": 0.0 if error else t, "mad": 0.01, "n_runs": len(runs),
         "runs": runs, "run_statuses": ["ok"] * len(runs), "first_run_discarded": False,
         "error": error}
    r.update(extra)
    return r


def _suite(tests, budgets=None, min_effect_pct=10.0):
    return Suite("t", "набор для теста", tests, budgets or {}, min_effect_pct)


def _report(ops, schema=9, version="2026.3.2"):
    return {"measure_schema": schema, "version": version, "results": ops}


def test_open_test_name_matches_application():
    """Открытие в наборе зовётся как тест, в результатах — как запись отчёта."""
    assert gate.OPEN_TEST_NAME == r7mod.R7Testovarka.OPEN_TEST_NAME
    assert gate.result_name(OPEN) == "Открытие файла"
    assert gate.result_name("A") == "A"


def test_all_ok_is_ready():
    m = gate_model([_op("Открытие файла", [9.0, 9.1, 9.2]), _op("A", STEADY)],
                   _suite({OPEN: 3, "A": 6}, {OPEN: 12.0}))
    assert m["ready"] and m["verdict"] == "Релиз готов" and m["tone"] == "good"
    assert [r["verdict"] for r in m["rows"]] == [OK, OK]
    assert m["rows"][0]["median"] == 9.1 and m["rows"][0]["budget"] == 12.0
    assert m["counts"][OK] == 2 and not m["problems"] and not m["warnings"]


def test_budget_exceeded_is_not_ready():
    m = gate_model([_op("A", STEADY)], _suite({"A": 6}, {"A": 0.5}))
    row = m["rows"][0]
    assert not m["ready"] and m["verdict"] == "Не готов" and m["tone"] == "critical"
    assert row["verdict"] == BUDGET and row["verdict_text"] == "выше бюджета"
    assert row["reasons"] == ["медиана 1.00 с выше бюджета 0.5 с"]
    assert m["problems"] == ["A: медиана 1.00 с выше бюджета 0.5 с"]


def test_regression_against_baseline():
    m = gate_model([_op("A", SLOW), _op("B", STEADY)], _suite({"A": 6, "B": 6}),
                   baseline=_report([_op("A", STEADY), _op("B", STEADY)]), schema=9,
                   version="2026.3.3")
    a, b = m["rows"]
    assert a["verdict"] == REGRESSION and round(a["delta_pct"]) == 50
    assert a["baseline_median"] == 1.0 and a["compare"]["verdict"] == "РЕГРЕССИЯ"
    assert a["reasons"][0].startswith("регрессия к эталону: +50.0 %")
    assert b["verdict"] == OK and b["delta_pct"] == 0.0
    assert not m["ready"]


def test_budget_and_regression_both_listed():
    m = gate_model([_op("A", SLOW)], _suite({"A": 6}, {"A": 1.2}),
                   baseline=_report([_op("A", STEADY)]))
    row = m["rows"][0]
    assert row["verdict"] == BUDGET and len(row["reasons"]) == 2


def test_speedup_is_ok_with_note():
    m = gate_model([_op("A", FAST)], _suite({"A": 6}), baseline=_report([_op("A", STEADY)]))
    row = m["rows"][0]
    assert m["ready"] and row["verdict"] == OK
    assert row["note"].startswith("быстрее эталона на 50.0 %")
    assert row["delta_text"] == "-50,0 %"


def test_too_few_runs_gives_no_verdict_but_delta():
    m = gate_model([_op("A", [1.5, 1.5, 1.5])], _suite({"A": 3}),
                   baseline=_report([_op("A", STEADY)]))
    row = m["rows"][0]
    assert row["verdict"] == OK and round(row["delta_pct"]) == 50
    assert "нужно по 5" in row["note"]


def test_error_and_not_measured():
    m = gate_model([_op("A", [], error="x2t упал")], _suite({"A": 3, "B": 3}))
    a, b = m["rows"]
    assert a["verdict"] == ERROR and a["reasons"] == ["x2t упал"] and a["median_text"] == "0,00"
    assert b["verdict"] == NOT_MEASURED and "в отчёт не попала" in b["reasons"][0]
    assert m["counts"] == {OK: 0, BUDGET: 0, REGRESSION: 0, ERROR: 1, NOT_MEASURED: 1}


def test_baseline_without_operation_or_with_error():
    m = gate_model([_op("A", STEADY), _op("B", STEADY)], _suite({"A": 6, "B": 6}),
                   baseline=_report([_op("B", [], error="упал")]))
    a, b = m["rows"]
    assert a["note"] == "в эталоне нет этой операции" and a["verdict"] == OK
    assert b["note"].startswith("в эталоне операция с ошибкой") and b["baseline_median"] is None


def test_baseline_warnings_schema_and_same_version():
    m = gate_model([_op("A", STEADY)], _suite({"A": 6}),
                   baseline=_report([_op("A", STEADY)], schema=8, version="v1"),
                   schema=9, version="v1")
    assert len(m["warnings"]) == 2
    assert "Схема замера эталона (8) и прогона (9)" in m["warnings"][0]
    assert "той же версии" in m["warnings"][1]
    assert m["baseline_version"] == "v1"


def test_no_baseline_means_no_warnings_and_no_baseline_fields():
    m = gate_model([_op("A", STEADY)], _suite({"A": 6}), baseline_name="x.json")
    assert m["warnings"] == [] and m["baseline_name"] is None


# ── Страница ──────────────────────────────────────────────────────────────

def test_page_renders_and_escapes():
    m = gate_model([_op(EVIL, [], error=EVIL), _op("A", SLOW)],
                   Suite(EVIL, EVIL, {EVIL: 3, "A": 6}, {"A": 0.5}),
                   baseline=_report([_op("A", STEADY)], version=EVIL), version=EVIL,
                   report_name=EVIL, baseline_name=EVIL)
    html = gate_page(m)
    assert EVIL not in html and "&lt;script&gt;" in html
    assert "Не готов" in html and html.index("Не готов") < html.index('id="ops"')
    assert "выше бюджета" in html and "регрессия" in html and "ошибка" in html
    assert 'id="how"' in html and "Манна-Уитни" in html
    assert "10.0 %" in html or "10,0 %" in html


def test_page_ready_mentions_baseline():
    m = gate_model([_op("A", STEADY)], _suite({"A": 6}), baseline=_report([_op("A", STEADY)]),
                   baseline_name="base.json", version="2026.3.3")
    html = gate_page(m)
    assert "Релиз готов" in html and "без регрессий к эталону" in html and "base.json" in html


# ── JUnit ─────────────────────────────────────────────────────────────────

def test_junit_counts_and_wellformed():
    m = gate_model([_op("A", STEADY), _op("B", SLOW), _op("C", [], error="упал")],
                   _suite({"A": 6, "B": 6, "C": 3, "D": 3}, {"A": 5.0}),
                   baseline=_report([_op("B", STEADY)]))
    xml = junit_xml(m, all_tests=["A", "B", "C", "D", "E", "F"])
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(xml)
    assert root.tag == "testsuite" and root.get("name") == "r7-testovarka t"
    assert (root.get("tests"), root.get("failures"), root.get("errors"), root.get("skipped")) == \
        ("6", "1", "2", "2")
    cases = {c.get("name"): c for c in root.findall("testcase")}
    assert set(cases) == {"A", "B", "C", "D", "E", "F"}
    assert cases["A"].find("failure") is None and cases["A"].get("time") == "1.000"
    assert cases["B"].find("failure").get("type") == REGRESSION
    assert "регрессия" in cases["B"].find("failure").get("message")
    assert cases["C"].find("error").get("type") == ERROR and cases["C"].find("error").text == "упал"
    assert cases["D"].find("error").get("type") == NOT_MEASURED
    assert cases["E"].find("skipped") is not None and cases["F"].find("skipped") is not None


def test_junit_escapes_markup_in_names():
    m = gate_model([_op(EVIL, [], error=EVIL)], _suite({EVIL: 3}))
    root = ET.fromstring(junit_xml(m))
    case = root.find("testcase")
    assert case.get("name") == EVIL and case.find("error").text == EVIL


# ── этап 3: порог по шуму, интервал, поправка на набор ───────────────────

BASE7 = [2.000, 2.001, 1.999, 2.002, 2.000, 1.998, 2.001]
PLUS5 = [round(b * 1.05, 4) for b in BASE7]


def test_noise_profile_threshold_replaces_suite_threshold():
    """Набор пишет 10 %, но профиль шума даёт Ctrl+V порог 2 % — сдвиг 5 %
    ловится только с профилем."""
    baseline = _report([_op("A", BASE7)])
    plain = gate_model([_op("A", PLUS5)], _suite({"A": 7}), baseline=baseline)
    assert plain["ready"] and plain["rows"][0]["threshold_text"] == "±10,0 %"
    profile = {"fingerprint_hash": "h", "created": "07.10", "tests": {"A": {"cv_pct": 0.1}}}
    m = gate_model([_op("A", PLUS5)], _suite({"A": 7}), baseline=baseline, noise_profile=profile)
    row = m["rows"][0]
    assert not m["ready"] and row["verdict"] == REGRESSION
    assert row["threshold_text"] == "±2,0 %" and row["threshold_source"] == "шум стенда"
    assert row["reasons"][0].startswith("регрессия к эталону: +5.0 % [+")
    assert "порог 2.0 %" in row["reasons"][0] and "p скорр." in row["reasons"][0]
    assert row["ci_text"].startswith("+5,0 % [+") and row["p_adj_text"] != "—"
    assert "профиля шума стенда h" in m["noise_note"] and m["family_size"] == 1
    out = gate_page(m)
    assert "Порог" in out and "±2,0 %" in out and "p скорр." in out


def test_gate_family_correction_turns_lone_weak_regression_into_note():
    """Пять повторов на сторону, одна регрессия среди 17 операций: после
    поправки Бенджамини-Хохберга p = 0,135 — не регрессия, а «не определено»."""
    names = [f"op{i}" for i in range(17)]
    base5 = [1.00, 1.01, 0.99, 1.02, 0.98]
    cur = [_op("op0", [1.50, 1.51, 1.49, 1.52, 1.48])] + [_op(n, base5) for n in names[1:]]
    m = gate_model(cur, _suite({n: 5 for n in names}),
                   baseline=_report([_op(n, base5) for n in names]))
    row = m["rows"][0]
    assert m["family_size"] == 17
    assert row["verdict"] == OK and row["compare"]["decision"] == "не определено"
    assert "не определено" in row["note"]
    assert m["rows"][1]["compare"]["decision"] == "эквивалентно"


def test_no_baseline_has_no_threshold_or_noise_note():
    m = gate_model([_op("A", STEADY)], _suite({"A": 6}))
    assert m["noise_note"] is None and m["rows"][0]["threshold_text"] == "—"
    assert m["rows"][0]["ci_text"] is None
