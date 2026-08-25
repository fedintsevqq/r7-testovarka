"""Тесты страницы трендов (этап 2, M5): чтение накопленных
performance_full_*.json и построение HTML с Chart.js.

_load_trends_runs читает реальные файлы (tmp_path — не трогает репозиторий),
_generate_trends_html — чистая функция от уже загруженных данных, тестируется
без диска вовсе.
"""
import json
import re

import r7_Testovarka as r7mod


def _write_run(folder, filename, timestamp, version, results,
               measure_schema=None, mtime_offset=0):
    data = {"timestamp": timestamp, "version": version, "results": results}
    if measure_schema is not None:
        data["measure_schema"] = measure_schema
    path = folder / filename
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if mtime_offset:
        import os
        st = path.stat()
        os.utime(path, (st.st_atime, st.st_mtime + mtime_offset))
    return path


def _op(name, time_val, mad=None):
    r = {"name": name, "time": time_val}
    if mad is not None:
        r["mad"] = mad
    return r


# ── _load_trends_runs ──────────────────────────────────────────────────────

def test_load_trends_runs_reads_and_sorts_by_mtime(bare_r7, tmp_path):
    bare_r7.reports_folder = tmp_path
    _write_run(tmp_path, "performance_full_a.json", "20260101_100000",
              "v1", [_op("Ctrl+A", 1.0)], mtime_offset=0)
    _write_run(tmp_path, "performance_full_b.json", "20260102_100000",
              "v2", [_op("Ctrl+A", 2.0)], mtime_offset=10)

    runs = bare_r7._load_trends_runs()

    assert [r["version"] for r in runs] == ["v1", "v2"]


def test_load_trends_runs_timestamp_display_format(bare_r7, tmp_path):
    bare_r7.reports_folder = tmp_path
    _write_run(tmp_path, "performance_full_a.json", "20260315_143022",
              "v1", [_op("Ctrl+A", 1.0)])

    runs = bare_r7._load_trends_runs()

    assert runs[0]["ts_disp"] == "15.03.2026 14:30"


def test_load_trends_runs_skips_corrupted_file(bare_r7, tmp_path):
    bare_r7.reports_folder = tmp_path
    (tmp_path / "performance_full_bad.json").write_text("{ not json", encoding="utf-8")
    _write_run(tmp_path, "performance_full_ok.json", "20260101_100000",
              "v1", [_op("Ctrl+A", 1.0)], mtime_offset=1)

    runs = bare_r7._load_trends_runs()

    assert len(runs) == 1
    assert runs[0]["version"] == "v1"


def test_load_trends_runs_defaults_schema_to_one(bare_r7, tmp_path):
    _write_run(tmp_path, "performance_full_a.json", "20260101_100000",
              "v1", [_op("Ctrl+A", 1.0)])  # без measure_schema
    bare_r7.reports_folder = tmp_path

    runs = bare_r7._load_trends_runs()

    assert runs[0]["schema"] == 1


def test_load_trends_runs_reads_explicit_schema(bare_r7, tmp_path):
    _write_run(tmp_path, "performance_full_a.json", "20260101_100000",
              "v1", [_op("Ctrl+A", 1.0)], measure_schema=2)
    bare_r7.reports_folder = tmp_path

    runs = bare_r7._load_trends_runs()

    assert runs[0]["schema"] == 2


def test_load_trends_runs_indexes_results_by_name(bare_r7, tmp_path):
    _write_run(tmp_path, "performance_full_a.json", "20260101_100000",
              "v1", [_op("Ctrl+A", 1.0), _op("Ctrl+C", 2.0)])
    bare_r7.reports_folder = tmp_path

    runs = bare_r7._load_trends_runs()

    assert set(runs[0]["results"]) == {"Ctrl+A", "Ctrl+C"}
    assert runs[0]["results"]["Ctrl+A"]["time"] == 1.0


def test_load_trends_runs_ignores_malformed_result_entries(bare_r7, tmp_path):
    """Результат без "name" (повреждён вручную или сторонним инструментом) не
    должен ронять загрузку всего файла."""
    _write_run(tmp_path, "performance_full_a.json", "20260101_100000",
              "v1", [_op("Ctrl+A", 1.0), {"time": 5.0}, "не словарь"])
    bare_r7.reports_folder = tmp_path

    runs = bare_r7._load_trends_runs()

    assert set(runs[0]["results"]) == {"Ctrl+A"}


# ── _generate_trends_html ──────────────────────────────────────────────────

def _run(ts_disp, version, schema, results):
    return {"path": None, "ts_raw": "", "ts_disp": ts_disp, "version": version,
           "schema": schema, "results": results}


def test_generate_trends_html_builds_one_chart_per_operation(bare_r7):
    runs = [
        _run("01.01.2026 10:00", "v1", 2, {"Ctrl+A": _op("Ctrl+A", 1.0),
                                           "Ctrl+C": _op("Ctrl+C", 2.0)}),
        _run("02.01.2026 10:00", "v1", 2, {"Ctrl+A": _op("Ctrl+A", 1.1),
                                           "Ctrl+C": _op("Ctrl+C", 2.1)}),
    ]
    out = bare_r7._generate_trends_html(runs)
    assert out.count("new Chart(") == 2
    assert "Ctrl+A" in out and "Ctrl+C" in out


def test_generate_trends_html_skips_operation_seen_once():
    """Операция, встретившаяся только в одном прогоне, не даёт тренда —
    график из одной точки бесполезен и не должен строиться."""
    bare = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    runs = [
        _run("01.01.2026 10:00", "v1", 2, {"Ctrl+A": _op("Ctrl+A", 1.0),
                                           "Единожды": _op("Единожды", 5.0)}),
        _run("02.01.2026 10:00", "v1", 2, {"Ctrl+A": _op("Ctrl+A", 1.1)}),
    ]
    out = bare._generate_trends_html(runs)
    assert out.count("new Chart(") == 1
    assert "Единожды" not in out


def test_generate_trends_html_no_charts_message(bare_r7):
    runs = [
        _run("01.01.2026 10:00", "v1", 2, {"X": _op("X", 1.0)}),
        _run("02.01.2026 10:00", "v1", 2, {}),  # X не повторился нигде
    ]
    out = bare_r7._generate_trends_html(runs)
    assert out.count("new Chart(") == 0
    assert "не из чего" in out


def test_generate_trends_html_mad_band_only_when_present(bare_r7):
    """MAD-полоса рисуется только для точек, где mad реально посчитан
    (measure_schema=2) — версия 1 не имеет этого поля вовсе."""
    runs = [
        _run("01.01.2026", "v1", 1, {"Ctrl+A": _op("Ctrl+A", 1.0)}),        # без mad
        _run("02.01.2026", "v1", 2, {"Ctrl+A": _op("Ctrl+A", 1.1, mad=0.05)}),
    ]
    out = bare_r7._generate_trends_html(runs)
    assert json.dumps("MAD-полоса")[1:-1] in out


def test_generate_trends_html_no_mad_band_when_absent_everywhere(bare_r7):
    runs = [
        _run("01.01.2026", "v1", 1, {"Ctrl+A": _op("Ctrl+A", 1.0)}),
        _run("02.01.2026", "v1", 1, {"Ctrl+A": _op("Ctrl+A", 1.1)}),
    ]
    out = bare_r7._generate_trends_html(runs)
    assert json.dumps("MAD-полоса")[1:-1] not in out


def test_generate_trends_html_warns_on_mixed_schema(bare_r7):
    runs = [
        _run("01.01.2026", "v1", 1, {"Ctrl+A": _op("Ctrl+A", 1.0)}),
        _run("02.01.2026", "v2", 2, {"Ctrl+A": _op("Ctrl+A", 1.1)}),
    ]
    out = bare_r7._generate_trends_html(runs)
    assert "смешаны файлы разных версий схемы замера" in out


def test_generate_trends_html_no_warning_on_uniform_schema(bare_r7):
    runs = [
        _run("01.01.2026", "v1", 2, {"Ctrl+A": _op("Ctrl+A", 1.0)}),
        _run("02.01.2026", "v2", 2, {"Ctrl+A": _op("Ctrl+A", 1.1)}),
    ]
    out = bare_r7._generate_trends_html(runs)
    assert "смешаны файлы" not in out


def test_generate_trends_html_version_legend_deduplicated(bare_r7):
    runs = [
        _run("01.01.2026", "v1", 2, {"Ctrl+A": _op("Ctrl+A", 1.0)}),
        _run("02.01.2026", "v1", 2, {"Ctrl+A": _op("Ctrl+A", 1.1)}),
        _run("03.01.2026", "v2", 2, {"Ctrl+A": _op("Ctrl+A", 1.2)}),
    ]
    out = bare_r7._generate_trends_html(runs)
    assert out.count('class="legend-item"') == 2  # v1, v2 — не 3 (по числу прогонов)


def test_generate_trends_html_escapes_operation_names(bare_r7):
    runs = [
        _run("01.01.2026", "v1", 2, {"<script>": _op("<script>", 1.0)}),
        _run("02.01.2026", "v1", 2, {"<script>": _op("<script>", 1.1)}),
    ]
    out = bare_r7._generate_trends_html(runs)
    assert "<script>x" not in out.lower().replace("<script src", "")
    assert "&lt;script&gt;" in out


def test_generate_trends_html_valid_json_payload_embedded(bare_r7):
    """Данные графика должны быть валидным JSON внутри <script> — иначе
    страница откроется с ошибкой в консоли браузера вместо графика."""
    runs = [
        _run("01.01.2026", "v1", 2, {"Ctrl+A": _op("Ctrl+A", 1.234, mad=0.01)}),
        _run("02.01.2026", "v1", 2, {"Ctrl+A": _op("Ctrl+A", 1.3, mad=0.02)}),
    ]
    out = bare_r7._generate_trends_html(runs)
    m = re.search(r"datasets: (\[.*?\])\s*\},\s*\n\s*options:", out, re.S)
    assert m is not None
    parsed = json.loads(m.group(1))
    assert isinstance(parsed, list) and len(parsed) >= 1

    labels_m = re.search(r"labels: (\[.*?\]),\s*\n\s*datasets:", out, re.S)
    assert labels_m is not None
    labels = json.loads(labels_m.group(1))
    assert labels == ["01.01.2026", "02.01.2026"]
