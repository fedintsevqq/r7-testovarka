"""Проваленная операция в сравнении и трендах (аудит проглоченных ошибок
06.10.2026).

Провал пишется как time=0.0 с полем error, частичный результат — медианой
удачных повторов, тоже с error. Сравнение брало time как есть: провал
новой версии выглядел как «−100%» зелёным, провал базы ронял страницу
делением на ноль, тренды рисовали точку со временем 0.
"""
import pytest

import r7_Testovarka as r7mod


def _ok(name, t, runs=None):
    r = {"name": name, "time": t, "error": None}
    if runs is not None:
        r["runs"] = runs
    return r


def _failed(name, error="окно Р7 не найдено", t=0.0):
    return {"name": name, "time": t, "error": error}


def _dataset(path, version, results, os_name="Windows 10"):
    return {"path": path, "version": version,
            "data": {"results": results, "system": {"os": os_name},
                     "summary": {}, "timestamp": "06.10.2026 20:00"}}


@pytest.fixture
def app():
    return r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)


# ── _comparable_time ──────────────────────────────────────────────────────

@pytest.mark.parametrize("result, expected", [
    (None, None),
    (_ok("A", 1.5), 1.5),
    (_failed("A"), None),
    (_failed("A", t=1.2), None),          # частичный: медиана удачных + error
    ({"name": "A", "time": 0.0}, None),   # старый файл без поля error
    ({"name": "A", "time": None}, None),
])
def test_comparable_time(result, expected):
    assert r7mod.R7Testovarka._comparable_time(result) == expected


# ── Сравнение версий ──────────────────────────────────────────────────────

def test_failed_base_does_not_crash_comparison(app):
    datasets = [_dataset("base.json", "2026.3.1", [_failed("Ctrl+A")]),
                _dataset("new.json", "2026.3.2", [_ok("Ctrl+A", 1.0)])]
    out = app._generate_comparison_html(datasets, "base.json")   # было: ZeroDivisionError
    assert "ошибка" in out
    assert "окно Р7 не найдено" in out


def test_failed_new_version_is_not_shown_as_speedup(app):
    datasets = [_dataset("base.json", "2026.3.1", [_ok("Ctrl+A", 2.0)]),
                _dataset("new.json", "2026.3.2", [_failed("Ctrl+A")])]
    out = app._generate_comparison_html(datasets, "base.json")
    assert "-100.0%" not in out
    assert "delta-better'>" not in out
    assert "ошибка" in out


def test_partial_result_excluded_from_delta_and_verdict(app):
    datasets = [_dataset("base.json", "2026.3.1", [_ok("Ctrl+A", 2.0, runs=[2.0] * 6)]),
                _dataset("new.json", "2026.3.2",
                         [dict(_failed("Ctrl+A", "прогон 3: таймаут", t=1.0), runs=[1.0] * 6)])]
    out = app._generate_comparison_html(datasets, "base.json")
    assert "-50.0%" not in out
    assert "УСКОРЕНИЕ" not in out


def test_comparison_escapes_system_info(app):
    datasets = [_dataset("base.json", "v1", [_ok("A", 1.0)], os_name="<script>x</script>"),
                _dataset("new.json", "v2", [_ok("A", 1.0)])]
    out = app._generate_comparison_html(datasets, "base.json")
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;x&lt;/script&gt;" in out


# ── Тренды ────────────────────────────────────────────────────────────────

def _run(ts, results):
    return {"path": None, "ts_raw": "", "ts_disp": ts, "version": "v1", "schema": 7,
            "results": results}


def test_trend_skips_failed_points(app):
    runs = [_run("01.10.2026 10:00", {"A": _ok("A", 1.0)}),
            _run("02.10.2026 10:00", {"A": _failed("A")}),
            _run("03.10.2026 10:00", {"A": _ok("A", 1.1)})]
    out = app._generate_trends_html(runs)
    assert "02.10.2026 10:00" not in out          # точки провала на графике нет
    assert out.count('<canvas id="trend') == 1


def test_trend_with_only_one_good_point_has_no_chart(app):
    runs = [_run("01.10.2026 10:00", {"A": _ok("A", 1.0)}),
            _run("02.10.2026 10:00", {"A": _failed("A")})]
    assert app._generate_trends_html(runs).count('<canvas id="trend') == 0


# ── compare_runs ──────────────────────────────────────────────────────────

def test_compare_runs_zero_base_is_no_data():
    result = r7mod.compare_runs([0.0] * 6, [1.0] * 6)
    assert result["verdict"] == "нет данных"
    assert result["effect_pct"] is None


def test_no_verdict_for_dependent_runs(app):
    """Повторы без отката правок — выборки зависимы, Манн-Уитни неприменим."""
    base = dict(_ok("A", 2.0, runs=[2.0, 2.1, 2.0, 2.2, 2.1, 2.0]))
    new = dict(_ok("A", 1.0, runs=[1.0, 1.1, 1.0, 1.2, 1.1, 1.0]), runs_independent=False)
    datasets = [_dataset("base.json", "v1", [base]), _dataset("new.json", "v2", [new])]
    out = app._generate_comparison_html(datasets, "base.json")
    assert "УСКОРЕНИЕ" not in out
    assert "Зависимые повторы" in out


def test_verdict_for_independent_runs(app):
    base = _ok("A", 2.0, runs=[2.0, 2.1, 2.0, 2.2, 2.1, 2.0])
    new = _ok("A", 1.0, runs=[1.0, 1.1, 1.0, 1.2, 1.1, 1.0])
    datasets = [_dataset("base.json", "v1", [base]), _dataset("new.json", "v2", [new])]
    assert "УСКОРЕНИЕ" in app._generate_comparison_html(datasets, "base.json")
