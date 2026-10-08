"""Сравнение двух прогонов на одной фикстуре — ночной контур (этап 5 плана).

Инструмент должен ловить свои регрессии сам: ночной прогон сравнивается с
медианой последних K сравнимых ночей (baseline_reports, pooled_baseline;
прежде — с одной предыдущей, previous_report) тем же критерием, что и
«Сравнить версии» (compare_runs по действительным повторам), и отчёт
помечается, если хоть одна операция дала
вердикт «РЕГРЕССИЯ». Чистые функции: прогон и запись файлов — в
tests/nightly_local.py, CI — в .github/workflows/perf.yml.
"""
from __future__ import annotations

import json
import os
import statistics
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import r7_reports
from r7 import fingerprint, noise
from r7.stats import COMPARISON_MIN_EFFECT_PCT, adjust_family, compare_runs

REGRESSION, SPEEDUP = "РЕГРЕССИЯ", "УСКОРЕНИЕ"

# Причины, по которым регрессия не считается тревогой (цифры несравнимы).
BLOCK_SCHEMA, BLOCK_MACHINE = "схемы замера разные", "другой стенд"

StrPath = str | os.PathLike[str]


def load_report(path: StrPath) -> Any:
    """Содержимое performance_full_*.json (dict)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_reports(prev: Mapping[str, Any], cur: Mapping[str, Any],
                    min_effect_pct: float = COMPARISON_MIN_EFFECT_PCT,
                    noise_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
    rows: list[dict[str, Any]] = []
    raw: dict[str, dict[str, Any]] = {}
    for name, c in cur_by.items():
        p = prev_by.get(name)
        pt, ct = r7_reports.comparable_time(p), r7_reports.comparable_time(c)
        row: dict[str, Any] = {"name": name, "prev": pt, "cur": ct, "pct": None, "verdict": None, "note": "",
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
    regressions: list[str] = []
    speedups: list[str] = []
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


def aa_check(report_a: Mapping[str, Any], report_b: Mapping[str, Any]
             ) -> tuple[dict[str, Any], dict[str, Any]]:
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


def block_reason(cmp: Mapping[str, Any]) -> str | None:
    """Почему регрессия не станет тревогой: BLOCK_SCHEMA, BLOCK_MACHINE или
    None — сравнение честное."""
    if cmp.get("schema_mismatch"):
        return BLOCK_SCHEMA
    if cmp.get("fingerprint_mismatch"):
        return BLOCK_MACHINE
    return None


def _fmt_p(p: float | None) -> str:
    return "—" if p is None else f"{p:.3f}"


def format_comparison(cmp: Mapping[str, Any], prev_label: str, cur_label: str) -> str:
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
        mark = "  <<" if r["verdict"] in (REGRESSION, SPEEDUP) else (
            "  ?" if str(r.get("decision") or "").startswith("вероятн") else "")
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


def is_alarm(cmp: Mapping[str, Any]) -> bool:
    """Ставить ли флаг: есть регрессия, схема замера одна и стенд тот же
    (см. block_reason)."""
    return bool(cmp["regressions"]) and block_reason(cmp) is None


def previous_report(folder: StrPath, current: StrPath) -> Path | None:
    """Последний ночной отчёт в folder до current (по имени — в нём время)."""
    current = Path(current)
    older = sorted(p for p in Path(folder).glob("nightly_*.json") if p.name < current.name)
    return older[-1] if older else None


# ── База из K прошлых прогонов (этап 3 плана, п. 4) ─────────────────────
#
# Один прошлый отчёт — плохая база: если та ночь сама была шумной, сегодня
# покажется регрессия или ускорение, которых нет. База — последние K
# сравнимых ночей (тот же режим, та же схема замера, тот же стенд).
#
# Как сводятся K отчётов. «Медиана медиан» даёт по одной точке на ночь: при
# K = 5 у compare_runs ровно минимум повторов, а разброс внутри ночи
# теряется. Поэтому для вердикта повторы K ночей склеиваются (действительные,
# те же, что вошли в медиану каждой ночи): выборка базы сама содержит разброс
# от ночи к ночи, и одна шумная ночь в ней — лишь 1/K повторов. Критерий тот
# же compare_runs, порог эффекта тот же. Время базы в строке («было») —
# медиана медиан ночей: одна выбивающаяся ночь его не сдвигает.

BASELINE_K = 5


def _report_mode(path: StrPath) -> str:
    """Режим ночного отчёта по имени (nightly_<дата>_<время>_<режим>.json)."""
    return Path(path).stem.rsplit("_", 1)[-1]


def baseline_reports(folder: StrPath, current: StrPath, k: int = BASELINE_K) -> list[Path]:
    """Последние k ночных отчётов до current, сравнимых с ним.

    Сравнимый — тот же режим (quick/full: разный набор тестов), та же
    схема замера и тот же стенд: хэш отпечатка совпадает или его нет хотя
    бы у одного из двух (старый отчёт считается снятым на той же машине —
    как в block_reason). Нечитаемые файлы пропускаются.

    Returns:
        list[Path]: от старого к новому; меньше k, если сравнимых меньше.
    """
    current = Path(current)
    cur = load_report(current)
    cur_schema = cur.get("measure_schema", 1)
    cur_fp, _ = fingerprint.report_fingerprint(cur)
    mode = _report_mode(current)
    chosen: list[Path] = []
    older = sorted((p for p in Path(folder).glob("nightly_*.json") if p.name < current.name),
                   reverse=True)
    for p in older:
        if len(chosen) >= k:
            break
        if _report_mode(p) != mode:
            continue
        try:
            data = load_report(p)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("measure_schema", 1) != cur_schema:
            continue
        fp, _ = fingerprint.report_fingerprint(data)
        if fp and cur_fp and fp != cur_fp:
            continue
        chosen.append(p)
    return list(reversed(chosen))


def pooled_baseline(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Сводный отчёт-база из нескольких (см. пояснение над BASELINE_K).

    По каждой операции: runs — склеенные действительные повторы всех
    отчётов, где у неё есть время; time — медиана их медиан; n_reports —
    сколько отчётов вошло. Если времени нет ни в одном отчёте, берётся
    запись последнего (с его ошибкой). Схема, версия и окружение — от
    последнего отчёта: база снята на том же стенде (baseline_reports).

    Args:
        reports: список dict-отчётов от старого к новому, непустой.
    """
    names: list[str] = []
    by_name: dict[str, list[dict[str, Any]]] = {}
    for rep in reports:
        for r in rep.get("results", []):
            if isinstance(r, dict) and "name" in r:
                if r["name"] not in by_name:
                    names.append(r["name"])
                    by_name[r["name"]] = []
                by_name[r["name"]].append(r)
    results: list[dict[str, Any]] = []
    for name in names:
        recs = by_name[name]
        usable = [r for r in recs if r7_reports.comparable_time(r) is not None]
        if not usable:
            results.append(dict(recs[-1]))
            continue
        runs = [t for r in usable for t in r7_reports.valid_runs(r)]
        medians = [t for t in (r7_reports.comparable_time(r) for r in usable) if t is not None]
        results.append({"name": name,
                        "time": statistics.median(medians),
                        "runs": runs, "run_statuses": ["ok"] * len(runs),
                        "first_run_discarded": False, "error": None,
                        "n_reports": len(usable)})
    last = reports[-1]
    pooled: dict[str, Any] = {"measure_schema": last.get("measure_schema", 1), "version": last.get("version"),
              "results": results, "baseline_size": len(reports)}
    if "system" in last:
        pooled["system"] = last["system"]
    return pooled


def compare_with_baseline(baseline: Sequence[Mapping[str, Any]], cur: Mapping[str, Any],
                          noise_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """compare_reports против сводной базы из dict-отчётов baseline (от
    старого к новому); noise_profile — пороги тестов (r7/noise.py). В
    результате дополнительно baseline_size."""
    cmp = compare_reports(pooled_baseline(baseline), cur, noise_profile=noise_profile)
    cmp["baseline_size"] = len(baseline)
    return cmp


def baseline_label(paths: Iterable[StrPath]) -> str:
    """Подпись базы для сводки: имя файла или «медиана 5 прошлых (a … b)»."""
    items = [Path(p) for p in paths]
    if len(items) == 1:
        return items[0].name
    return f"медиана {len(items)} прошлых ({items[0].name} … {items[-1].name})"
