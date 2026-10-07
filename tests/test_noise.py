"""Профиль шума стенда из A/A-прогонов (r7/noise.py) и его использование:
ночное сравнение, A/A-режим tests/nightly_local.py, страница сравнения,
экран готовности."""
import json
import sys
from pathlib import Path

import pytest

import r7_reports
from r7 import fingerprint, noise
from r7.nightly import aa_check, compare_reports, format_comparison
from r7.stats import compare_runs

ROOT = Path(__file__).resolve().parent.parent

BASE = [2.000, 2.001, 1.999, 2.002, 2.000, 1.998, 2.001]
PLUS5 = [round(b * 1.05, 4) for b in BASE]
OPEN_A = [9.0, 9.3, 8.8, 9.5, 9.1]
OPEN_B = [9.2, 8.9, 9.4, 9.0, 9.6]


def _op(name, runs, error=None):
    t = sorted(runs)[len(runs) // 2] if runs else 0.0
    return {"name": name, "time": 0.0 if error else t, "runs": runs,
            "run_statuses": ["ok"] * len(runs), "first_run_discarded": False, "error": error}


def _env(**over):
    base = dict(cpu_model="Intel i5", cpu_logical=8, ram_gb=16, os_name="Windows 10",
                dpi_scale_pct=100, r7_data_drive="C:", reports_drive="E:", power_plan="Сбаланс.")
    fp = fingerprint.collect(**{**base, **over})
    return {"environment": {"fingerprint": fp, "fingerprint_hash": fingerprint.fingerprint_hash(fp)}}


def _rep(ops, ts="2026-10-07 21:00:00", **env_over):
    return {"measure_schema": 9, "version": "2026.3.2", "timestamp": ts,
            "system": _env(**env_over), "results": ops}


def _aa_pair():
    a = _rep([_op("Ctrl+V", BASE), _op("Открытие файла", OPEN_A),
              _op("Экспорт", [], error="x2t упал")], ts="A")
    b = _rep([_op("Ctrl+V", list(reversed(BASE))), _op("Открытие файла", OPEN_B),
              _op("Экспорт", [3.0] * 3)], ts="B")
    return a, b


# ── сборка профиля ────────────────────────────────────────────────────────

def test_profile_from_reports_pools_runs_and_skips_errors():
    a, b = _aa_pair()
    entry = noise.profile_from_reports(a, b, created="2026-10-07 22:00:00")
    fp_hash, _ = fingerprint.report_fingerprint(a)
    assert entry["fingerprint_hash"] == fp_hash and entry["reports"] == ["A", "B"]
    assert set(entry["tests"]) == {"Ctrl+V", "Открытие файла"}   # экспорт с ошибкой — нет
    ctrl = entry["tests"]["Ctrl+V"]
    assert ctrl["n"] == 14 and ctrl["aa_delta_pct"] == 0.0 and ctrl["timestamps"] == ["A", "B"]
    assert ctrl["cv_pct"] < 0.2
    opened = entry["tests"]["Открытие файла"]
    assert opened["cv_pct"] > 2.0 and opened["n"] == 10


def test_profile_needs_fingerprint_and_same_machine():
    a, b = _aa_pair()
    old = {k: v for k, v in a.items() if k != "system"}
    with pytest.raises(noise.NoiseProfileError, match="отпечатка"):
        noise.profile_from_reports(old, b)
    other = {**b, "system": _env(cpu_model="Ryzen")}
    with pytest.raises(noise.NoiseProfileError, match="разных стендов"):
        noise.profile_from_reports(a, other)


def test_threshold_is_k_cv_with_floor():
    assert noise.threshold_from_cv(0.1) == noise.NOISE_FLOOR_PCT
    assert noise.threshold_from_cv(2.9) == pytest.approx(8.7)
    profile = {"tests": {"A": {"cv_pct": 2.9}}}
    assert noise.threshold_for(profile, "A") == (pytest.approx(8.7), noise.SOURCE_NOISE, 2.9)
    assert noise.threshold_for(profile, "B") == (10.0, noise.SOURCE_DEFAULT, None)
    assert noise.threshold_for(None, "A", 7.0) == (7.0, noise.SOURCE_DEFAULT, None)


# ── файл профиля ──────────────────────────────────────────────────────────

def test_save_and_load_roundtrip_keeps_other_machines(tmp_path):
    a, b = _aa_pair()
    entry = noise.profile_from_reports(a, b)
    other = {"fingerprint_hash": "other", "tests": {"X": {"cv_pct": 1.0, "n": 10}}}
    doc = noise.merge_profile(noise.merge_profile(None, other), entry)
    noise.save_profile_doc(tmp_path, doc)
    loaded = noise.load_noise_profile(tmp_path, entry["fingerprint_hash"])
    assert loaded["tests"]["Ctrl+V"]["cv_pct"] == entry["tests"]["Ctrl+V"]["cv_pct"]
    assert noise.load_noise_profile(tmp_path, "other")["tests"]["X"]["cv_pct"] == 1.0
    assert noise.noise_for_report(tmp_path, a)["fingerprint_hash"] == entry["fingerprint_hash"]
    assert not (tmp_path / "noise_profile.json.tmp").exists()


def test_new_aa_replaces_machine_entry(tmp_path):
    a, b = _aa_pair()
    first = noise.profile_from_reports(a, b)
    second = {**first, "tests": {"Ctrl+V": {"cv_pct": 5.0, "n": 14}}}
    doc = noise.merge_profile(noise.merge_profile(None, first), second)
    assert list(doc["machines"]) == [first["fingerprint_hash"]]
    assert doc["machines"][first["fingerprint_hash"]]["tests"] == second["tests"]


@pytest.mark.parametrize("content", [None, "", "{битый", "[]", '{"machines": []}',
                                     '{"format": 1, "machines": {"h": "строка"}}'])
def test_load_is_tolerant_to_missing_or_broken_file(tmp_path, content):
    if content is not None:
        (tmp_path / noise.NOISE_PROFILE_NAME).write_text(content, encoding="utf-8")
    assert noise.load_noise_profile(tmp_path, "h") is None
    assert noise.read_profile_doc(tmp_path)["machines"] in ({}, {"h": "строка"})


def test_load_drops_bad_test_entries_and_handles_no_hash(tmp_path):
    doc = {"format": 1, "machines": {"h": {"tests": {
        "ok": {"cv_pct": 1.5}, "neg": {"cv_pct": -1}, "str": {"cv_pct": "1"},
        "bool": {"cv_pct": True}, "list": [1]}}}}
    (tmp_path / noise.NOISE_PROFILE_NAME).write_text(json.dumps(doc), encoding="utf-8")
    assert set(noise.load_noise_profile(tmp_path, "h")["tests"]) == {"ok"}
    assert noise.load_noise_profile(tmp_path, None) is None
    assert noise.load_noise_profile(None, "h") is None
    assert noise.noise_for_report(tmp_path, {"results": []}) is None


def test_describe_and_format_profile():
    assert "нет" in noise.describe_profile(None) and "--aa" in noise.describe_profile(None)
    a, b = _aa_pair()
    entry = noise.profile_from_reports(a, b)
    assert entry["fingerprint_hash"] in noise.describe_profile(entry)
    text = noise.format_profile(entry)
    assert "Ctrl+V" in text and "7 повторов ловят сдвиг от" in text
    assert "5 повторов ловят сдвиг от" in text


# ── ночное сравнение ──────────────────────────────────────────────────────

def test_nightly_uses_noise_threshold_per_test():
    prev = _rep([_op("Ctrl+V", BASE)])
    cur = _rep([_op("Ctrl+V", PLUS5)])
    plain = compare_reports(prev, cur)
    assert plain["regressions"] == [] and plain["rows"][0]["threshold"] == 10.0
    assert plain["rows"][0]["threshold_source"] == noise.SOURCE_DEFAULT
    profile = {"fingerprint_hash": "h", "tests": {"Ctrl+V": {"cv_pct": 0.1}}}
    tuned = compare_reports(prev, cur, noise_profile=profile)
    row = tuned["rows"][0]
    assert tuned["regressions"] == ["Ctrl+V"] and row["threshold"] == 2.0
    assert row["ci_low"] > 2.0 and row["p_adj"] < 0.05 and row["decision"] == "РЕГРЕССИЯ"
    assert tuned["family_size"] == 1


def test_nightly_rows_have_interval_and_text_shows_it():
    prev = _rep([_op("A", BASE), _op("B", BASE)])
    cur = _rep([_op("A", [b * 1.5 for b in BASE]), _op("B", list(BASE))])
    cmp = compare_reports(prev, cur)
    a, b = cmp["rows"]
    assert a["verdict"] == "РЕГРЕССИЯ" and a["n"] == (7, 7)
    assert b["decision"] == "эквивалентно" and b["verdict"] == "без изменений"
    assert cmp["family_size"] == 2
    text = format_comparison(cmp, "x", "y")
    assert "+50 % [+" in text and "эквивалентно" in text
    assert "Бенджамини-Хохберга на 2 сравнений" in text
    assert "Профиля шума для этого стенда нет" in text


def test_aa_check_same_version_gives_no_changes():
    a, b = _aa_pair()
    entry, cmp = aa_check(a, b)
    assert set(entry["tests"]) == {"Ctrl+V", "Открытие файла"}
    assert cmp["regressions"] == [] and cmp["speedups"] == []
    decisions = {r["name"]: r["decision"] for r in cmp["rows"] if r["decision"]}
    assert decisions["Ctrl+V"] == "эквивалентно"


def _load_nightly_local():
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import nightly_local
    finally:
        sys.path.remove(str(ROOT / "tests"))
    return nightly_local


def test_nightly_local_aa_reports_writes_profile(tmp_path):
    a, b = _aa_pair()
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    pb.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    nl = _load_nightly_local()
    code = nl.main(["--aa-reports", str(pa), str(pb), "--dir", str(tmp_path / "n"),
                    "--noise-dir", str(tmp_path)])
    assert code == 0
    fp_hash, _ = fingerprint.report_fingerprint(a)
    assert noise.load_noise_profile(tmp_path, fp_hash)["tests"]["Ctrl+V"]["n"] == 14
    assert "Профиль шума стенда" in (tmp_path / "n" / "aa_last.txt").read_text(encoding="utf-8")


def test_nightly_local_aa_reports_from_different_machines_fails(tmp_path):
    a, b = _aa_pair()
    b = {**b, "system": _env(cpu_model="Ryzen")}
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    pb.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    nl = _load_nightly_local()
    assert nl.main(["--aa-reports", str(pa), str(pb), "--dir", str(tmp_path / "n"),
                    "--noise-dir", str(tmp_path)]) == 1
    assert not (tmp_path / noise.NOISE_PROFILE_NAME).exists()


# ── страница сравнения ────────────────────────────────────────────────────

def _ds(path, version, ops):
    return {"path": path, "version": version, "data": _rep(ops)}


def test_comparison_page_uses_profile_threshold_and_shows_interval():
    ds = [_ds("a", "v1", [_op("Ctrl+V", BASE)]), _ds("b", "v2", [_op("Ctrl+V", PLUS5)])]
    profile = {"fingerprint_hash": "h", "created": "2026-10-07", "version": "v1",
               "tests": {"Ctrl+V": {"cv_pct": 0.1}}}
    plain = r7_reports.comparison_model(ds, "a", compare_runs, 5)
    assert plain["regressions"] == [] and not plain["has_noise_profile"]
    assert plain["rows"][0]["threshold"] == "±10,0 %"
    model = r7_reports.comparison_model(ds, "a", compare_runs, 5, noise_profile=profile)
    assert len(model["regressions"]) == 1 and model["has_noise_profile"]
    cell = model["rows"][0]["cells"][1]
    assert cell["verdict"] == "РЕГРЕССИЯ" and cell["ci"].startswith("+5,0 % [+")
    assert "p скорр." in cell["verdict_title"] and "ловят сдвиг от" in cell["verdict_title"]
    assert "порог ±2,0 %" in model["regressions"][0]["text"]
    out = r7_reports.render("comparison.html", **model)
    assert "±2,0 %" in out and "Пороги" in out and "профиля шума стенда h" in out


def test_comparison_page_undetermined_is_warning_tone():
    noisy = [1.00, 1.30, 0.80, 1.25, 0.95, 1.20, 0.85]
    ds = [_ds("a", "v1", [_op("A", [1.0, 1.01, 0.99, 1.02, 1.0, 0.98, 1.01])]),
          _ds("b", "v2", [_op("A", noisy)])]
    model = r7_reports.comparison_model(ds, "a", compare_runs, 5)
    cell = model["rows"][0]["cells"][1]
    assert cell["verdict"] == "не определено" and cell["verdict_tone"] == "warning"
    assert "не определено" in r7_reports.render("comparison.html", **model)


def test_app_comparison_reads_profile_from_reports_folder(bare_r7, tmp_path):
    ds = [_ds("a", "v1", [_op("Ctrl+V", BASE)]), _ds("b", "v2", [_op("Ctrl+V", PLUS5)])]
    assert "РЕГРЕССИЯ" not in bare_r7._generate_comparison_html(ds, "a")
    fp_hash, _ = fingerprint.report_fingerprint(ds[0]["data"])
    doc = noise.merge_profile(None, {"fingerprint_hash": fp_hash,
                                     "tests": {"Ctrl+V": {"cv_pct": 0.1, "n": 14}}})
    noise.save_profile_doc(tmp_path, doc)
    bare_r7.reports_folder = tmp_path
    assert "РЕГРЕССИЯ" in bare_r7._generate_comparison_html(ds, "a")


# ── форматирование ────────────────────────────────────────────────────────

@pytest.mark.parametrize("args, expected", [
    ((12.3, 7.2, 18.4), "+12 % [+7; +18]"),
    ((12.3, 10.2, 18.4), "+12 % [+10; +18]"),
    ((1.23, 0.4, 2.0), "+1,2 % [+0,4; +2,0]"),
    ((-0.04, -1.0, 1.0), "+0,0 % [-1,0; +1,0]"),
    ((5.0, None, None), "+5,0 %"),
    ((None, 1.0, 2.0), "—"),
])
def test_fmt_effect_ci(args, expected):
    assert r7_reports.fmt_effect_ci(*args) == expected


@pytest.mark.parametrize("n, word", [(1, "повтор"), (3, "повтора"), (5, "повторов"),
                                     (11, "повторов"), (21, "повтор"), (22, "повтора")])
def test_repeats_word(n, word):
    assert r7_reports.repeats_word(n) == word


def test_mde_text_and_fmt_p():
    assert r7_reports.mde_text(7, 4.94) == "7 повторов ловят сдвиг от 4,9 %"
    assert r7_reports.mde_text(7, 15.1) == "7 повторов ловят сдвиг от 15 %"
    assert r7_reports.mde_text(0, 3.0) is None and r7_reports.mde_text(7, None) is None
    assert r7_reports.fmt_p(0.0004) == "< 0,001" and r7_reports.fmt_p(0.0123) == "0,012"
    assert r7_reports.fmt_p(None) == "—"
