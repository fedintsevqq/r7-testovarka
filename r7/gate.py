"""Экран «релиз готов / не готов» (docs/plan-to-20.md, этап 2, пункт 6).

Вердикт по набору тестов: у каждой операции — медиана против бюджета из
набора и против эталонного прогона (compare_runs, тот же критерий, что в
«Сравнить версии»). Одна причина против — «Не готов». Чистые функции над
записями performance_full_*.json; страница — r7_reports.render и шаблон
templates/html/gate.html; JUnit XML — для CI.
"""
import xml.etree.ElementTree as ET

import r7_reports
from r7.stats import compare_runs

OK, BUDGET, REGRESSION, ERROR, NOT_MEASURED = "ok", "budget", "regression", "error", "not_measured"
VERDICT_TEXT = {OK: "в норме", BUDGET: "выше бюджета", REGRESSION: "регрессия",
                ERROR: "ошибка", NOT_MEASURED: "не измерено"}
VERDICT_TONE = {OK: "good", BUDGET: "critical", REGRESSION: "critical",
                ERROR: "critical", NOT_MEASURED: "warning"}
READY, NOT_READY = "Релиз готов", "Не готов"

# Открытие файла в наборе зовётся как тест (R7Testovarka.OPEN_TEST_NAME), а
# запись в результатах — прежним именем, чтобы не рвать тренды и сравнение.
OPEN_TEST_NAME = "Повторное открытие файла"
OPEN_RESULT_NAME = "Открытие файла"

HOW_COMPUTED = (
    "Время операции — медиана по повторам без прогрева, таймаутов и неподтверждённых "
    "прогонов (та же цифра, что в отчёте прогона).",
    "Бюджет — абсолютный потолок медианы из набора ([budgets] в suites/*.toml); "
    "медиана выше бюджета — «выше бюджета».",
    "Эталон — прогон из --baseline: регрессия объявляется критерием Манна-Уитни "
    "(p < 0,05) при сдвиге медианы больше порога из набора ([compare] min_effect_pct); "
    "меньше пяти повторов на любой стороне — вердикта нет, только Δ %.",
    "Ошибка — у операции нет ни одного действительного повтора; не измерено — "
    "операция из набора в отчёт не попала (прогон прерван).",
    "«Релиз готов» — все операции набора в норме. Любая причина против — «Не готов».",
)


def result_name(test_name):
    """Имя записи в результатах по имени теста из набора."""
    return OPEN_RESULT_NAME if test_name == OPEN_TEST_NAME else test_name


def gate_model(results, suite, baseline=None, schema=None, version=None,
               report_name=None, baseline_name=None):
    """Модель страницы готовности.

    Args:
        results: записи операций текущего прогона (список dict).
        suite: r7.suites.Suite.
        baseline: полный JSON эталонного прогона (dict) или None.
        schema: measure_schema текущего прогона — для предупреждения о
            несравнимых схемах.
        version, report_name, baseline_name: подписи в шапке.

    Returns:
        dict: ready (bool), verdict, tone, rows (по тестам набора), counts,
        warnings, how (как считалось) и подписи.
    """
    by_name = _by_name(results)
    base_by_name = _by_name((baseline or {}).get("results", []))
    rows = [_row(name, runs, by_name.get(result_name(name)),
                 base_by_name.get(result_name(name)) if baseline else None,
                 suite.budgets.get(name), suite.min_effect_pct, baseline is not None)
            for name, runs in suite.tests.items()]
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICT_TEXT}
    ready = all(r["verdict"] == OK for r in rows)
    warnings = _warnings(baseline, schema, version)
    problems = [f"{r['name']}: {'; '.join(r['reasons'])}" for r in rows if r["verdict"] != OK]
    return {
        "title": "Готовность релиза",
        "ready": ready,
        "verdict": READY if ready else NOT_READY,
        "tone": "good" if ready else "critical",
        "suite_name": suite.name,
        "suite_description": suite.description,
        "version": version,
        "report_name": report_name,
        "baseline_name": baseline_name if baseline is not None else None,
        "baseline_version": (baseline or {}).get("version"),
        "min_effect_pct": suite.min_effect_pct,
        "rows": rows,
        "problems": problems,
        "counts": counts,
        "warnings": warnings,
        "how": list(HOW_COMPUTED),
    }


def _by_name(results):
    return {r["name"]: r for r in results or [] if isinstance(r, dict) and r.get("name")}


def _row(name, runs, r, b, budget, min_effect_pct, has_baseline):
    row = {"name": name, "runs_planned": runs, "median": None, "mad": None, "n": 0,
           "budget": budget, "baseline_median": None, "delta_pct": None, "compare": None,
           "note": None, "verdict": OK, "reasons": []}
    if r is None:
        row["verdict"] = NOT_MEASURED
        row["reasons"].append("операция из набора в отчёт не попала — прогон прерван "
                              "или тест пропущен")
        return _finish(row)
    row["median"], row["mad"], row["n"] = r.get("time"), r.get("mad"), r.get("n_runs") or 0
    t = r7_reports.comparable_time(r)
    if t is None:
        row["verdict"] = ERROR
        row["reasons"].append(str(r.get("error") or "нет ни одного действительного повтора"))
        return _finish(row)
    if budget is not None and t > budget:
        row["verdict"] = BUDGET
        row["reasons"].append(f"медиана {t:.2f} с выше бюджета {budget:g} с")
    if b is not None:
        _compare_with_baseline(row, r, b, t, min_effect_pct)
    elif has_baseline:
        row["note"] = "в эталоне нет этой операции"
    return _finish(row)


def _compare_with_baseline(row, r, b, t, min_effect_pct):
    bt = r7_reports.comparable_time(b)
    row["baseline_median"] = bt
    if bt is None:
        row["note"] = "в эталоне операция с ошибкой — сравнивать не с чем"
        return
    row["delta_pct"] = (t - bt) / bt * 100
    res = compare_runs(r7_reports.valid_runs(b), r7_reports.valid_runs(r),
                       min_effect_pct=min_effect_pct)
    row["compare"] = res
    if res["verdict"] == "РЕГРЕССИЯ":
        if row["verdict"] == OK:
            row["verdict"] = REGRESSION
        row["reasons"].append(f"регрессия к эталону: {res['effect_pct']:+.1f} % "
                              f"(p = {res['p_value']:.3f})")
    elif res["verdict"] == "недостаточно прогонов":
        row["note"] = (f"вердикта сравнения нет: повторов {res['n_base']} в эталоне и "
                       f"{res['n_new']} сейчас, нужно по 5")
    elif res["verdict"] == "УСКОРЕНИЕ":
        row["note"] = f"быстрее эталона на {-res['effect_pct']:.1f} %"


def _finish(row):
    """Подписи для таблицы — из модели, не из шаблона."""
    row["verdict_text"] = VERDICT_TEXT[row["verdict"]]
    row["tone"] = VERDICT_TONE[row["verdict"]]
    row["median_text"] = r7_reports.fmt_sec(row["median"], 2)
    row["mad_text"] = r7_reports.fmt_sec(row["mad"], 2) if row["mad"] is not None else "—"
    row["budget_text"] = r7_reports.fmt_sec(row["budget"], 1) if row["budget"] is not None else "—"
    row["baseline_text"] = r7_reports.fmt_sec(row["baseline_median"], 2)
    d = row["delta_pct"]
    row["delta_text"] = "—" if d is None else f"{d:+.1f} %".replace(".", ",")
    return row


def _warnings(baseline, schema, version):
    out = []
    if baseline is None:
        return out
    base_schema = baseline.get("measure_schema", 1)
    if schema is not None and base_schema != schema:
        out.append(f"Схема замера эталона ({base_schema}) и прогона ({schema}) разные — "
                   f"цифры несравнимы напрямую, вердикт регрессии ненадёжен.")
    if version and baseline.get("version") == version:
        out.append("Эталон снят на той же версии Р7 — разница покажет шум стенда, "
                   "а не изменения в сборке.")
    return out


def gate_page(model):
    """HTML страницы готовности по модели gate_model."""
    return r7_reports.render("gate.html", **model)


def junit_xml(model, all_tests=()):
    """JUnit XML для CI: один testcase на тест. failure — выше бюджета или
    регрессия, error — ошибка операции или не измерено, skipped — тест есть
    в all_tests, но в набор не входит."""
    rows = model["rows"]
    in_suite = {r["name"] for r in rows}
    skipped = [n for n in all_tests if n not in in_suite]
    failures = sum(1 for r in rows if r["verdict"] in (BUDGET, REGRESSION))
    errors = sum(1 for r in rows if r["verdict"] in (ERROR, NOT_MEASURED))
    root = ET.Element("testsuite", name=f"r7-testovarka {model['suite_name']}",
                      tests=str(len(rows) + len(skipped)), failures=str(failures),
                      errors=str(errors), skipped=str(len(skipped)))
    for r in rows:
        case = ET.SubElement(root, "testcase", classname="r7.perf", name=r["name"],
                             time=f"{r['median'] or 0.0:.3f}")
        reasons = "; ".join(r["reasons"])
        if r["verdict"] in (BUDGET, REGRESSION):
            ET.SubElement(case, "failure", message=reasons, type=r["verdict"]).text = reasons
        elif r["verdict"] in (ERROR, NOT_MEASURED):
            ET.SubElement(case, "error", message=reasons, type=r["verdict"]).text = reasons
    for name in skipped:
        case = ET.SubElement(root, "testcase", classname="r7.perf", name=name, time="0.000")
        ET.SubElement(case, "skipped", message="не входит в набор")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
