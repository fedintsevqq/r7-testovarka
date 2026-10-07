"""Сравнение двух прогонов на одной фикстуре — ночной контур (этап 5 плана).

Инструмент должен ловить свои регрессии сам: ночной прогон сравнивается с
предыдущим тем же критерием, что и «Сравнить версии» (compare_runs по
действительным повторам), и отчёт помечается, если хоть одна операция дала
вердикт «РЕГРЕССИЯ». Чистые функции: прогон и запись файлов — в
tests/nightly_local.py, CI — в .github/workflows/perf.yml.
"""
import json
from pathlib import Path

import r7_reports
from r7 import fingerprint
from r7.stats import COMPARISON_MIN_EFFECT_PCT, compare_runs

REGRESSION, SPEEDUP = "РЕГРЕССИЯ", "УСКОРЕНИЕ"

# Причины, по которым регрессия не считается тревогой (цифры несравнимы).
BLOCK_SCHEMA, BLOCK_MACHINE = "схемы замера разные", "другой стенд"


def load_report(path):
    """Содержимое performance_full_*.json (dict)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_reports(prev, cur, min_effect_pct=COMPARISON_MIN_EFFECT_PCT):
    """Построчное сравнение двух отчётов.

    Returns:
        dict: rows — по операции текущего прогона {name, prev, cur, pct,
        verdict, note}; regressions/speedups — имена; schema_mismatch — схемы
        замера разные (цифры несравнимы, вердикты всё равно посчитаны, но
        флагом служить не должны); fingerprint_mismatch — отчёты с разных
        машин (оба с отпечатком, хэши разные), fingerprint_diff — чем
        отличаются; missing — операции только в одном прогоне.
    """
    prev_by = {r["name"]: r for r in prev.get("results", []) if isinstance(r, dict) and "name" in r}
    cur_by = {r["name"]: r for r in cur.get("results", []) if isinstance(r, dict) and "name" in r}
    rows, regressions, speedups = [], [], []
    for name, c in cur_by.items():
        p = prev_by.get(name)
        pt, ct = r7_reports.comparable_time(p), r7_reports.comparable_time(c)
        row = {"name": name, "prev": pt, "cur": ct, "pct": None, "verdict": None, "note": ""}
        if p is None:
            row["note"] = "новая операция"
        elif ct is None:
            row["note"] = "ошибка сейчас: " + str(c.get("error") or "нет времени")
        elif pt is None:
            row["note"] = "в прошлом прогоне ошибка: " + str(p.get("error") or "нет времени")
        else:
            row["pct"] = (ct - pt) / pt * 100
            res = compare_runs(r7_reports.valid_runs(p), r7_reports.valid_runs(c),
                               min_effect_pct=min_effect_pct)
            row["verdict"] = res["verdict"]
            if res["verdict"] == REGRESSION:
                regressions.append(name)
            elif res["verdict"] == SPEEDUP:
                speedups.append(name)
        rows.append(row)
    prev_fp, prev_fields = fingerprint.report_fingerprint(prev)
    cur_fp, cur_fields = fingerprint.report_fingerprint(cur)
    fp_mismatch = bool(prev_fp and cur_fp and prev_fp != cur_fp)
    return {
        "rows": rows, "regressions": regressions, "speedups": speedups,
        "schema_mismatch": prev.get("measure_schema", 1) != cur.get("measure_schema", 1),
        "fingerprint_mismatch": fp_mismatch,
        "fingerprint_diff": fingerprint.diff_fields(prev_fields, cur_fields) if fp_mismatch else [],
        "version_changed": prev.get("version") != cur.get("version"),
        "missing": sorted(set(prev_by) ^ set(cur_by)),
    }


def block_reason(cmp):
    """Почему регрессия не станет тревогой: BLOCK_SCHEMA, BLOCK_MACHINE или
    None — сравнение честное."""
    if cmp.get("schema_mismatch"):
        return BLOCK_SCHEMA
    if cmp.get("fingerprint_mismatch"):
        return BLOCK_MACHINE
    return None


def format_comparison(cmp, prev_label, cur_label):
    """Текстовая сводка для журнала и файла nightly_last.txt."""
    lines = [f"Сравнение: {prev_label} → {cur_label}"]
    if cmp["schema_mismatch"]:
        lines.append("⚠️ схемы замера разные — цифры несравнимы, флаг регрессии не ставится")
    if cmp.get("fingerprint_mismatch"):
        diff = fingerprint.describe_fields(cmp.get("fingerprint_diff") or [])
        lines.append("⚠️ другой стенд" + (f" (отличаются: {diff})" if diff else "")
                     + " — цифры несравнимы, флаг регрессии не ставится")
    if cmp["version_changed"]:
        lines.append("ℹ️ версия Р7 сменилась — различия могут быть от Р7, а не от инструмента")
    lines.append(f"{'операция':44} {'было':>8} {'стало':>8} {'Δ%':>7}  вердикт")
    for r in cmp["rows"]:
        if r["pct"] is None:
            lines.append(f"{r['name'][:44]:44} {'—':>8} {'—':>8} {'':>7}  {r['note']}")
            continue
        mark = "  <<" if r["verdict"] in (REGRESSION, SPEEDUP) else ""
        lines.append(f"{r['name'][:44]:44} {r['prev']:8.3f} {r['cur']:8.3f} "
                     f"{r['pct']:+7.1f}  {r['verdict']}{mark}")
    if cmp["missing"]:
        lines.append(f"только в одном прогоне: {', '.join(cmp['missing'])}")
    lines.append(f"регрессий: {len(cmp['regressions'])}, ускорений: {len(cmp['speedups'])}")
    return "\n".join(lines)


def is_alarm(cmp):
    """Ставить ли флаг: есть регрессия, схема замера одна и стенд тот же
    (см. block_reason)."""
    return bool(cmp["regressions"]) and block_reason(cmp) is None


def previous_report(folder, current):
    """Последний ночной отчёт в folder до current (по имени — в нём время)."""
    current = Path(current)
    older = sorted(p for p in Path(folder).glob("nightly_*.json") if p.name < current.name)
    return older[-1] if older else None
