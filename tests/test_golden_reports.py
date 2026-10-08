"""Эталонные (golden) тесты HTML-отчётов.

Каждая страница — прогон, сравнение, тренды, сводка Batch, готовность релиза,
свой файл — собирается из зафиксированного JSON в tests/fixtures/golden/ тем
же путём, что в программе (модель r7_reports → шаблон), и сравнивается с
эталонным .html рядом. Любая правка шаблона, модели или статистики, которая
меняет страницу, видна в диффе эталона на ревью.

Из страницы перед сравнением убирается то, что меняется само: дата и время
сборки страницы, версия инструмента, абсолютные пути, концы строк.

Обновить эталоны после намеренной правки:
    R7_UPDATE_GOLDEN=1 .venv/Scripts/python.exe -m pytest tests/test_golden_reports.py
и просмотреть дифф `git diff tests/fixtures/golden/` до коммита.
"""
import json
import os
import re
from pathlib import Path

import pytest

import r7_reports
from r7 import gate
from r7.stats import MIN_RUNS_FOR_COMPARISON, compare_runs
from r7.suites import Suite
from r7.version import __version__

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "golden"
ROOT = Path(__file__).resolve().parent.parent
UPDATE = os.environ.get("R7_UPDATE_GOLDEN") == "1"

_DATE_TIME = re.compile(r"\b\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}\b")


def _load(name):
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def normalize(html, extra_paths=()):
    """Убирает из страницы то, что зависит от момента и места сборки."""
    html = html.replace("\r\n", "\n")
    for p in sorted({str(ROOT), *map(str, extra_paths)}, key=len, reverse=True):
        for form in (p, p.replace("\\", "/")):
            html = html.replace(form, "<путь>")
    html = re.sub(rf"(?<![\d.]){re.escape(__version__)}(?![\d.])", "<версия-инструмента>", html)
    return _DATE_TIME.sub("<дата>", html)


# ── Сборка страниц из фикстур ────────────────────────────────────────────

def page_run(tmp_path):
    d = _load("run.json")
    # Файла нет на диске: страница показывает только имя, без размера.
    model = r7_reports.run_report_model(
        d["results"], tmp_path / d["test_file"], d["open_elapsed"], d["version"],
        system=d["system"], summary=d["summary"], cpu_count=d["cpu_count"],
        schema=d["measure_schema"], tool_version=__version__, build=d["build"])
    return r7_reports.render("run.html", **model)


def page_comparison(tmp_path):
    d = _load("comparison.json")
    model = r7_reports.comparison_model(d["datasets"], d["base_path"], compare_runs,
                                        MIN_RUNS_FOR_COMPARISON,
                                        noise_profile=d["noise_profile"])
    return r7_reports.render("comparison.html", **model)


def page_trends(tmp_path):
    return r7_reports.render("trends.html", **r7_reports.trends_model(_load("trends.json")))


def page_batch(tmp_path):
    return r7_reports.render("batch.html", **r7_reports.batch_model(_load("batch.json")))


def page_gate(tmp_path):
    d = _load("gate.json")
    s = d["suite"]
    suite = Suite(s["name"], s["description"], s["tests"], s["budgets"], s["min_effect_pct"])
    model = gate.gate_model(d["results"], suite, baseline=d["baseline"], schema=d["schema"],
                            version=d["version"], report_name=d["report_name"],
                            baseline_name=d["baseline_name"])
    return gate.gate_page(model)


def page_custom(tmp_path):
    return r7_reports.render("custom.html", **r7_reports.custom_model(_load("custom.json")))


PAGES = {"run": page_run, "comparison": page_comparison, "trends": page_trends,
         "batch": page_batch, "gate": page_gate, "custom": page_custom}


@pytest.mark.parametrize("name", sorted(PAGES))
def test_report_matches_golden(name, tmp_path):
    html = normalize(PAGES[name](tmp_path), extra_paths=[tmp_path])
    golden = GOLDEN_DIR / f"{name}.html"
    if UPDATE or not golden.exists():
        golden.write_text(html, encoding="utf-8", newline="\n")
        if not UPDATE:
            pytest.fail(f"эталона {golden.name} не было — создан, проверьте его и закоммитьте")
        return
    expected = golden.read_text(encoding="utf-8").replace("\r\n", "\n")
    if html != expected:
        # Первая отличающаяся строка — чтобы сразу видеть, что поменялось.
        got, exp = html.split("\n"), expected.split("\n")
        line = next((i for i, (a, b) in enumerate(zip(got, exp)) if a != b), min(len(got), len(exp)))
        pytest.fail(f"{golden.name} отличается с строки {line + 1}:\n"
                    f"  эталон: {exp[line] if line < len(exp) else '<конец>'}\n"
                    f"  сейчас: {got[line] if line < len(got) else '<конец>'}\n"
                    f"Если правка намеренная: R7_UPDATE_GOLDEN=1 pytest {Path(__file__).name}")


def test_pages_are_built_from_fixture_content(tmp_path):
    # Эталон проверяет не пустую страницу: ключевые места фикстур на месте.
    assert "Вставка большого массива (Ctrl+V)" in page_run(tmp_path)
    comparison = page_comparison(tmp_path)
    assert "Регрессии: 1" in comparison
    assert "сдвиг с 2026.3.2.3229" in page_trends(tmp_path)
    assert "Не готов" in page_gate(tmp_path)
    assert "A-B-A" in page_batch(tmp_path)


def test_normalize_hides_volatile_parts(tmp_path):
    raw = (f"Сформировано 07.10.2026 20:31, версия {__version__}, "
           f"{ROOT}\\Reports, {tmp_path}/a.xlsx\r\n")
    assert normalize(raw, extra_paths=[tmp_path]) == (
        "Сформировано <дата>, версия <версия-инструмента>, <путь>\\Reports, <путь>/a.xlsx\n")


def test_comparison_many_versions_stacks_charts_and_scrolls_table():
    """3+ версии: графики RAM и CPU на всю ширину друг под другом (в половине
    ширины 17 операций × 5 версий сливались), таблица прокручивается внутри
    с закреплённой шапкой и колонками «Операция» и «Порог»."""
    import copy
    d = _load("comparison.json")
    two = r7_reports.render("comparison.html", **r7_reports.comparison_model(
        d["datasets"], d["base_path"], compare_runs, MIN_RUNS_FOR_COMPARISON))
    assert 'class="grid-2"' in two and 'class="stack-charts"' not in two
    extra = copy.deepcopy(d["datasets"][-1])
    extra["path"] = "performance_full_20260930_100000.json"
    extra["version"] = "2026.3.3"
    three = r7_reports.render("comparison.html", **r7_reports.comparison_model(
        d["datasets"] + [extra], d["base_path"], compare_runs, MIN_RUNS_FOR_COMPARISON))
    assert 'class="stack-charts"' in three
    assert 'class="table-wrap scroll"' in three and "op stick stick-1" in three
