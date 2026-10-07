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
from r7 import fingerprint, noise
from r7.stats import COMPARISON_MIN_EFFECT_PCT, adjust_family, compare_runs

REGRESSION, SPEEDUP = "РЕГРЕССИЯ", "УСКОРЕНИЕ"

# Причины, по которым регрессия не считается тревогой (цифры несравнимы).
BLOCK_SCHEMA, BLOCK_MACHINE = "схемы замера разные", "другой стенд"


def load_report(path):
    """Содержимое performance_full_*.json (dict)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_reports(prev, cur, min_effect_pct=COMPARISON_MIN_EFFECT_PCT, noise_profile=None):
    """Построчное сравнение двух отчётов.

    Вердикт — по 95 %-интервалу изменения медианы против порога теста и p с
    поправкой Бенджамини-Хохберга на все сравнённые операции
    (docs/statistics.md). Порог — из профиля шума стенда (r7/noise.py), для
    операций без профиля — min_effect_pct.

    Args:
        noise_profile: запись профиля машины (noise.load_noise_profile) или None.

    Returns:
        dict: rows — по операции текущего прогона {name, prev, cur, pct,
        verdict, note} и подробности вердикта: decision («эквивалентно» /
        «не определено» вместо «без изменений»), ci_low/ci_high (%),
        threshold и threshold_source, p, p_adj, mde, n; regressions/speedups —
        имена; schema_mismatch — схемы замера разные (цифры несравнимы,
        вердикты всё равно посчитаны, но флагом служить не должны);
        fingerprint_mismatch — отчёты с разных машин (оба с отпечатком, хэши
        разные), fingerprint_diff — чем отличаются; missing — операции только
        в одном прогоне; noise_note — откуда пороги.
    """
    prev_by = {r["name"]: r for r in prev.get("results", []) if isinstance(r, dict) and "name" in r}
    cur_by = {r["name"]: r for r in cur.get("results", []) if isinstance(r, dict) and "name" in r}
    rows, raw = [], {}
    for name, c in cur_by.items():
        p = prev_by.get(name)
        pt, ct = r7_reports.comparable_time(p), r7_reports.comparable_time(c)
        row = {"name": name, "prev": pt, "cur": ct, "pct": None, "verdict": None, "note": "",
               "decision": None, "ci_low": None, "ci_high": None, "threshold": None,
               "threshold_source": None, "p": None, "p_adj": None, "mde": None, "n": None}
        if p is None:
            row["note"] = "новая операция"
        elif ct is None:
            row["note"] = "ошибка сейчас: " + str(c.get("error") or "нет времени")
        elif pt is None:
            row["note"] = "в прошлом прогоне ошибка: " + str(p.get("error") or "нет времени")
        else:
            row["pct"] = (ct - pt) / pt * 100
            thr, source, cv = noise.threshold_for(noise_profile, name, min_effect_pct)
            row["threshold"], row["threshold_source"] = thr, source
            raw[name] = compare_runs(r7_reports.valid_runs(p), r7_reports.valid_runs(c),
                                     min_effect_pct=min_effect_pct, threshold_pct=thr,
                                     noise_cv_pct=cv)
        rows.append(row)
    final = adjust_family(raw)
    regressions, speedups = [], []
    for row in rows:
        res = final.get(row["name"])
        if res is None:
            continue
        row["verdict"] = res["verdict"]
        row["decision"] = res["decision"] or res["verdict"]
        row["ci_low"], row["ci_high"] = res["ci_low_pct"], res["ci_high_pct"]
        row["p"], row["p_adj"], row["mde"] = res["p_raw"], res["p_adjusted"], res["mde_pct"]
        row["n"] = (res["n_base"], res["n_new"])
        if res["verdict"] == REGRESSION:
            regressions.append(row["name"])
        elif res["verdict"] == SPEEDUP:
            speedups.append(row["name"])
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
        "family_size": sum(1 for r in final.values() if r.get("p_adjusted") is not None),
        "noise_note": noise.describe_profile(noise_profile),
    }


def aa_check(report_a, report_b):
    """A/A-проверка двух прогонов одной версии: запись профиля шума машины
    (noise.profile_from_reports) и сравнение B с A по порогам из неё же.

    Критерий этапа 3: на A/A все операции — не РЕГРЕССИЯ и не УСКОРЕНИЕ.
    Если сравнение что-то нашло, стенд шумит сильнее, чем видно по CV
    (дрейф между прогонами) — профилю верить с осторожностью.

    Returns:
        tuple[dict, dict]: (запись профиля, результат compare_reports).

    Raises:
        noise.NoiseProfileError: нет отпечатка или стенды разные.
    """
    entry = noise.profile_from_reports(report_a, report_b)
    return entry, compare_reports(report_a, report_b, noise_profile=entry)


def block_reason(cmp):
    """Почему регрессия не станет тревогой: BLOCK_SCHEMA, BLOCK_MACHINE или
    None — сравнение честное."""
    if cmp.get("schema_mismatch"):
        return BLOCK_SCHEMA
    if cmp.get("fingerprint_mismatch"):
        return BLOCK_MACHINE
    return None


def _fmt_p(p):
    return "—" if p is None else f"{p:.3f}"


def format_comparison(cmp, prev_label, cur_label):
    """Текстовая сводка для журнала и файла nightly_last.txt.

    Δ% — сдвиг медиан, в скобках 95 %-интервал; порог — по шуму стенда или
    по умолчанию; p скорр. — с поправкой на число сравнений; MDE — какой
    сдвиг эти повторы ловят с вероятностью 80 %.
    """
    lines = [f"Сравнение: {prev_label} → {cur_label}"]
    if cmp["schema_mismatch"]:
        lines.append("⚠️ схемы замера разные — цифры несравнимы, флаг регрессии не ставится")
    if cmp.get("fingerprint_mismatch"):
        diff = fingerprint.describe_fields(cmp.get("fingerprint_diff") or [])
        lines.append("⚠️ другой стенд" + (f" (отличаются: {diff})" if diff else "")
                     + " — цифры несравнимы, флаг регрессии не ставится")
    if cmp["version_changed"]:
        lines.append("ℹ️ версия Р7 сменилась — различия могут быть от Р7, а не от инструмента")
    if cmp.get("noise_note"):
        lines.append("ℹ️ " + cmp["noise_note"])
    lines.append(f"{'операция':44} {'было':>8} {'стало':>8} {'Δ% [95 % интервал]':>22} "
                 f"{'порог':>6} {'p скорр.':>8} {'MDE':>6}  вердикт")
    for r in cmp["rows"]:
        if r["pct"] is None:
            lines.append(f"{r['name'][:44]:44} {'—':>8} {'—':>8} {'':>22} {'':>6} {'':>8} "
                         f"{'':>6}  {r['note']}")
            continue
        mark = "  <<" if r["verdict"] in (REGRESSION, SPEEDUP) else ""
        effect = r7_reports.fmt_effect_ci(r["pct"], r.get("ci_low"), r.get("ci_high"))
        thr = "—" if r.get("threshold") is None else f"{r['threshold']:.1f}"
        mde = "—" if r.get("mde") is None else f"{r['mde']:.1f}"
        lines.append(f"{r['name'][:44]:44} {r['prev']:8.3f} {r['cur']:8.3f} {effect:>22} "
                     f"{thr:>6} {_fmt_p(r.get('p_adj')):>8} {mde:>6}  "
                     f"{r.get('decision') or r['verdict']}{mark}")
    if cmp["missing"]:
        lines.append(f"только в одном прогоне: {', '.join(cmp['missing'])}")
    lines.append(f"регрессий: {len(cmp['regressions'])}, ускорений: {len(cmp['speedups'])}"
                 f" (поправка Бенджамини-Хохберга на {cmp.get('family_size') or 0} сравнений)")
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
