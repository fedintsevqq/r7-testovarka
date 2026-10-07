"""Точки смены уровня (r7/changepoint.py) и отметки на странице трендов."""
import random

import r7_reports
from r7 import changepoint


def _noisy(level, n, rng, sigma=0.01):
    return [level * (1 + rng.gauss(0, sigma)) for _ in range(n)]


def test_known_step_is_found_at_its_index():
    rng = random.Random(7)
    series = _noisy(1.0, 10, rng) + _noisy(1.12, 8, rng)
    cps = changepoint.detect(series)
    assert len(cps) == 1
    cp = cps[0]
    assert cp["index"] == 10 and cp["direction"] == "up"
    assert 9 < cp["pct"] < 15 and cp["p_value"] < changepoint.ALPHA


def test_speedup_is_down():
    rng = random.Random(3)
    cps = changepoint.detect(_noisy(2.0, 8, rng) + _noisy(1.6, 8, rng))
    assert [(c["index"], c["direction"]) for c in cps] == [(8, "down")]
    assert round(cps[0]["pct"]) == -20


def test_flat_noisy_series_gives_no_shift():
    for seed in range(10):
        rng = random.Random(seed)
        assert changepoint.detect(_noisy(1.0, 25, rng, sigma=0.02)) == []


def test_two_steps_found():
    rng = random.Random(11)
    series = _noisy(1.0, 8, rng) + _noisy(1.3, 8, rng) + _noisy(1.0, 8, rng)
    assert [c["index"] for c in changepoint.detect(series)] == [8, 16]


def test_small_significant_shift_is_not_marked():
    rng = random.Random(5)
    series = _noisy(1.0, 12, rng, sigma=0.002) + _noisy(1.02, 12, rng, sigma=0.002)
    assert changepoint.detect(series) == []                    # 2 % < порога 5 %
    assert changepoint.detect(series, min_shift_pct=1.0)[0]["index"] == 12


def test_min_segment_length_is_respected():
    # Выброс в двух последних точках — сегмент короче трёх, сдвига нет.
    assert changepoint.detect([1.0] * 10 + [2.0, 2.0]) == []
    # Ряд короче 2 × 3 точек не режется вовсе.
    assert changepoint.detect([1.0, 1.0, 1.0, 2.0, 2.0]) == []
    cps = changepoint.detect([1.0, 1.01, 0.99, 1.0, 2.0, 2.01, 1.99, 2.0])
    assert all(3 <= c["index"] <= 5 for c in cps)


def test_seeded_result_is_deterministic():
    rng = random.Random(1)
    series = _noisy(1.0, 6, rng, sigma=0.05) + _noisy(1.08, 6, rng, sigma=0.05)
    first = changepoint.detect(series, seed=42)
    assert all(changepoint.detect(series, seed=42) == first for _ in range(3))


def test_single_outlier_does_not_make_a_shift():
    series = [1.0, 1.01, 0.99, 1.0, 1.4, 1.0, 1.01, 0.99, 1.0, 1.0]
    assert changepoint.detect(series) == []


def test_format_shift_text():
    assert changepoint.format_shift({"pct": 8.2}, "2026.3.2.3229") == "сдвиг с 2026.3.2.3229, +8 %"
    assert changepoint.format_shift({"pct": -12.6}, "v") == "сдвиг с v, -13 %"


# ── отметки в модели трендов ─────────────────────────────────────────────

def _run(i, value, version, machine=None, schema=9):
    return {"ts_disp": f"0{i % 10}.10.2026 02:{i:02d}", "version": version, "schema": schema,
            "machine": machine, "fingerprint": None, "fingerprint_fields": None,
            "results": {"ВПР": {"name": "ВПР", "time": value, "mad": 0.01, "n_runs": 6}}}


def _step_runs(machine=None, at=6, n=12, low=1.0, high=1.2):
    rng = random.Random(2)
    return [_run(i, (low if i < at else high) * (1 + rng.gauss(0, 0.005)),
                 "2026.3.1" if i < at else "2026.3.2.3229", machine)
            for i in range(n)]


def test_trends_model_marks_shift_point_and_table():
    model = r7_reports.trends_model(_step_runs())
    chart = model["charts"][0]
    marked = [i for i, p in enumerate(chart["points"]) if p.get("shift")]
    assert marked == [6]
    sh = chart["points"][6]["shift"]
    assert sh["text"].startswith("сдвиг с 2026.3.2.3229, +") and sh["tone"] == "critical"
    assert chart["shifts"] == [sh]
    assert '"shifts"' in str(chart["json"]) and "сдвиг с 2026.3.2.3229" in str(chart["json"])


def test_trends_shift_is_detected_per_machine():
    # Машина A: ровно; машина B: ступенька. Вперемешку по времени.
    flat = [_run(i, 1.0 + 0.001 * (i % 3), "2026.3.1", "A") for i in range(12)]
    step = _step_runs("B", low=2.0, high=2.4)
    runs = [r for pair in zip(flat, step) for r in pair]
    chart = r7_reports.trends_model(runs)["charts"][0]
    marked = [p for p in chart["points"] if p.get("shift")]
    assert len(marked) == 1 and marked[0]["machine"] == "B"
    assert marked[0]["shift"]["machine"] == "B"


def test_trends_no_shift_across_schema_change():
    runs = [_run(i, 1.0 if i < 6 else 1.3, "v", schema=7 if i < 6 else 9) for i in range(12)]
    chart = r7_reports.trends_model(runs)["charts"][0]
    assert chart["shifts"] == []


def test_trends_page_renders_shift_mark(bare_r7):
    bare_r7.TRENDS_CHART_COLORS = r7_reports.SERIES_LIGHT
    out = bare_r7._generate_trends_html(_step_runs())
    assert "сдвиг с 2026.3.2.3229" in out and "shiftMarks" in out and "<th>Сдвиг</th>" in out


def test_trends_old_reports_without_shift_still_render(bare_r7):
    bare_r7.TRENDS_CHART_COLORS = r7_reports.SERIES_LIGHT
    runs = [_run(0, 1.0, "v1"), _run(1, 1.1, "v2")]
    out = bare_r7._generate_trends_html(runs)
    assert 'id="trend0"' in out and "сдвиг с" not in out


def test_format_shift_takes_version_number_from_product_name():
    name = "Р7-Офис. Профессиональный (десктопная версия) 2026.2.2.2923 (x64)"
    assert changepoint.format_shift({"pct": 8.0}, name) == "сдвиг с 2026.2.2.2923, +8 %"


def test_trends_no_shift_marked_before_trusted_schema():
    # До схемы 7 цифры включали паузы инструмента: сдвиг там — смена методики.
    runs = [_run(i, 1.0 if i < 6 else 3.0, "v", schema=None if i < 3 else 6) for i in range(12)]
    model = r7_reports.trends_model(runs)
    assert not any(p.get("shift") for p in model["charts"][0]["points"])
