"""Палитра графиков отчётов (скилл dataviz, 06.10.2026)."""
import re

import pytest

import r7_Testovarka as r7mod
from r7 import config as r7config  # noqa: E402

OLD_PALETTE = ("#3498db", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6",
               "#1abc9c", "#e67e22", "#c0392b", "#16a085", "#f1c40f")


def test_palette_is_eight_distinct_colors():
    assert len(r7config.SERIES_COLORS) == 8
    assert len(set(r7config.SERIES_COLORS)) == 8
    assert r7config.SERIES_OTHER_COLOR not in r7config.SERIES_COLORS


def test_series_rgba():
    assert r7config._series_rgba("#2a78d6", 0.15) == "rgba(42,120,214,0.15)"


def _run(ts, version):
    return {"path": None, "ts_raw": "", "ts_disp": ts, "version": version, "schema": 8,
            "results": {"A": {"name": "A", "time": 1.0, "error": None}}}


def _legend_colors(html_text):
    return dict((v, c) for c, v in re.findall(
        r'legend-dot" style="background:(#[0-9a-f]{6})"></span>\s*<span[^>]*>([^<]+)</span>',
        html_text))


@pytest.fixture
def app():
    return r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)


def test_trends_older_versions_grey_newest_keep_palette(app):
    """Десять версий: две старые — серые, восемь последних — по порядку
    палитры, без повторов цвета по кругу."""
    versions = [f"2026.{i}" for i in range(10)]
    runs = [_run(f"0{i}.10.2026 10:00", v) for i, v in enumerate(versions)]
    colors = _legend_colors(app._generate_trends_html(runs))
    assert colors["2026.0"] == colors["2026.1"] == r7config.SERIES_OTHER_COLOR
    assert [colors[v] for v in versions[2:]] == list(r7config.SERIES_COLORS)


def test_trends_few_versions_use_first_slots(app):
    runs = [_run("01.10.2026 10:00", "v1"), _run("02.10.2026 10:00", "v2")]
    colors = _legend_colors(app._generate_trends_html(runs))
    assert [colors["v1"], colors["v2"]] == list(r7config.SERIES_COLORS[:2])


def test_comparison_uses_new_palette_only(app):
    ds = [{"path": f"{i}.json", "version": f"v{i}",
           "data": {"results": [{"name": "A", "time": 1.0 + i, "error": None}],
                    "system": {}, "summary": {}, "timestamp": ""}} for i in range(3)]
    out = app._generate_comparison_html(ds, "0.json").lower()
    # Цвета серий в данных графиков (красный в CSS «хуже» — законный статус).
    series = re.findall(r'"(?:backgroundcolor|bordercolor)": "(#[0-9a-f]{6})"', out)
    assert series and set(series) == set(r7config.SERIES_COLORS[:3])
    assert not set(series) & set(OLD_PALETTE)
