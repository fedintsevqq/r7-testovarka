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


# ── другой стенд (отпечаток машины, этап 2 плана) ───────────────────────

def _fp_env(**over):
    from r7 import fingerprint
    base = dict(cpu_model="i7", cpu_logical=8, ram_gb=16, os_name="Win10", dpi_scale_pct=100,
                r7_data_drive="C:", reports_drive="C:", power_plan="High")
    fp = fingerprint.collect(**{**base, **over})
    return {"environment": {"fingerprint": fp, "fingerprint_hash": fingerprint.fingerprint_hash(fp)}}


def test_other_machine_blocks_alarm_with_its_own_reason():
    from r7.nightly import BLOCK_MACHINE, BLOCK_SCHEMA, block_reason
    prev = {**_rep([_op("Ctrl+A", STEADY)]), "system": _fp_env()}
    cur = {**_rep([_op("Ctrl+A", SLOW)]), "system": _fp_env(cpu_model="Ryzen", ram_gb=32)}
    cmp = compare_reports(prev, cur)
    assert cmp["regressions"] == ["Ctrl+A"] and cmp["fingerprint_mismatch"]
    assert cmp["fingerprint_diff"] == ["cpu_model", "ram_gb"]
    assert not cmp["schema_mismatch"] and not is_alarm(cmp)
    assert block_reason(cmp) == BLOCK_MACHINE
    text = format_comparison(cmp, "a", "b")
    assert "другой стенд" in text and "процессор, RAM" in text
    # Схема важнее: при обоих расхождениях причина — схема.
    cmp2 = compare_reports({**prev, "measure_schema": 8}, cur)
    assert block_reason(cmp2) == BLOCK_SCHEMA


def test_same_machine_or_old_report_does_not_block():
    from r7.nightly import block_reason
    prev = {**_rep([_op("Ctrl+A", STEADY)]), "system": _fp_env()}
    cur = {**_rep([_op("Ctrl+A", SLOW)]), "system": _fp_env()}
    assert is_alarm(compare_reports(prev, cur)) and block_reason(compare_reports(prev, cur)) is None
    old = _rep([_op("Ctrl+A", STEADY)])                       # без отпечатка
    cmp = compare_reports(old, cur)
    assert not cmp["fingerprint_mismatch"] and is_alarm(cmp)


# ── база из K прошлых ночей (этап 3 плана, п. 4) ────────────────────────

def _write(folder, name, rep):
    p = folder / name
    p.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return p


def _night(day, mode="full", hour="0200"):
    return f"nightly_202610{day:02d}_{hour}_{mode}.json"


def test_baseline_takes_last_k_same_mode_oldest_first(tmp_path):
    from r7.nightly import baseline_reports
    for d in range(1, 9):
        _write(tmp_path, _night(d), _rep([_op("A", STEADY)]))
    _write(tmp_path, _night(8, "quick", "0300"), _rep([_op("A", STEADY)]))
    cur = _write(tmp_path, _night(9), _rep([_op("A", STEADY)]))
    base = baseline_reports(tmp_path, cur, k=5)
    assert [p.name for p in base] == [_night(d) for d in range(4, 9)]


def test_baseline_skips_other_schema_and_other_machine(tmp_path):
    from r7.nightly import baseline_reports
    here, there = _fp_env(), _fp_env(cpu_model="Ryzen")
    _write(tmp_path, _night(1), {**_rep([_op("A", STEADY)]), "system": here})
    _write(tmp_path, _night(2), _rep([_op("A", STEADY)]))                       # без отпечатка — тот же стенд
    _write(tmp_path, _night(3), {**_rep([_op("A", STEADY)]), "system": there})  # другой стенд
    _write(tmp_path, _night(4), {**_rep([_op("A", STEADY)], schema=8), "system": here})
    (tmp_path / _night(5)).write_text("{битый", encoding="utf-8")
    cur = _write(tmp_path, _night(6), {**_rep([_op("A", STEADY)]), "system": here})
    base = baseline_reports(tmp_path, cur, k=5)
    assert [p.name for p in base] == [_night(1), _night(2)]      # меньше K — сколько есть


def test_baseline_empty_when_nothing_comparable(tmp_path):
    from r7.nightly import baseline_reports
    cur = _write(tmp_path, _night(2), _rep([_op("A", STEADY)]))
    assert baseline_reports(tmp_path, cur) == []
    _write(tmp_path, _night(1), _rep([_op("A", STEADY)], schema=7))
    assert baseline_reports(tmp_path, cur) == []


def test_pooled_baseline_concatenates_valid_runs_and_takes_median_of_medians():
    from r7.nightly import pooled_baseline
    r1 = _op("A", [1.0, 1.0, 1.0, 1.0, 1.0])
    r2 = {**_op("A", [9.0, 2.0, 2.0, 2.0, 2.0]), "time": 2.0, "first_run_discarded": True}
    r3 = {**_op("A", [3.0, 3.0, 3.0, 50.0]), "time": 3.0,
          "run_statuses": ["ok", "ok", "ok", "timeout"]}
    pooled = pooled_baseline([_rep([r1]), _rep([r2]), _rep([r3, _op("B", [], error="упал")])])
    a = next(r for r in pooled["results"] if r["name"] == "A")
    assert a["time"] == 2.0 and a["n_reports"] == 3
    assert sorted(a["runs"]) == [1.0] * 5 + [2.0] * 4 + [3.0] * 3   # без прогрева и таймаута
    b = next(r for r in pooled["results"] if r["name"] == "B")
    assert b["error"] == "упал" and pooled["baseline_size"] == 3


def test_one_noisy_night_in_baseline_does_not_fake_a_speedup():
    """Прошлая ночь была медленной: против неё сегодня «ускорение», против
    базы из пяти ночей — без изменений."""
    from r7.nightly import compare_with_baseline
    nights = [_rep([_op("A", STEADY)]) for _ in range(4)] + [_rep([_op("A", SLOW)])]
    cur = _rep([_op("A", STEADY)])
    assert compare_reports(nights[-1], cur)["speedups"] == ["A"]
    cmp = compare_with_baseline(nights, cur)
    assert cmp["speedups"] == [] and cmp["regressions"] == [] and cmp["baseline_size"] == 5
    assert cmp["rows"][0]["prev"] == 1.0


def test_regression_against_baseline_still_alarms():
    from r7.nightly import compare_with_baseline
    nights = [_rep([_op("A", STEADY)]) for _ in range(3)]
    cmp = compare_with_baseline(nights, _rep([_op("A", SLOW)]))
    assert cmp["regressions"] == ["A"] and is_alarm(cmp)


def test_baseline_label():
    from pathlib import Path
    from r7.nightly import baseline_label
    assert baseline_label([Path("a.json")]) == "a.json"
    assert baseline_label([Path("a.json"), Path("b.json")]) == "медиана 2 прошлых (a.json … b.json)"


def test_nightly_local_compare_only_uses_baseline(tmp_path, capsys):
    """--compare-only без Р7: сводка называет базу из прошлых ночей."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "nightly_local_under_test", Path(__file__).with_name("nightly_local.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for d in range(1, 4):
        _write(tmp_path, _night(d), _rep([_op("A", STEADY)]))
    _write(tmp_path, _night(4), _rep([_op("A", SLOW)]))
    assert mod.main(["--compare-only", "--dir", str(tmp_path)]) == 2
    text = (tmp_path / "nightly_last.txt").read_text(encoding="utf-8")
    assert "медиана 3 прошлых" in text and "РЕГРЕССИЯ" in text
