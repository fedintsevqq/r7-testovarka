"""Сэндвич A-B-A в Batch (этап 3 плана, п. 5): дрейф стенда за время прогона.

Batch по нескольким версиям идёт часами. Если за это время стенд «уехал»
(прогрелся, Windows начала обновляться, антивирус взялся за диск), разница
между версиями — уже не только Р7. Проверка: базовую версию (первую в
списке, A) ставим и меряем ещё раз в конце. Если A в начале и A в конце
различаются сильнее шума — вся сводка Batch помечается.

Чистые функции: решение о дрейфе — здесь, установка и прогон — в
r7/runs.py (_batch_worker).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import r7_reports
from r7 import noise
from r7.stats import COMPARISON_MIN_EFFECT_PCT, compare_runs

REGRESSION, SPEEDUP = "РЕГРЕССИЯ", "УСКОРЕНИЕ"
# Запасной порог для операций, где повторов меньше, чем нужно compare_runs
# (экспорт в Batch — 3 повтора): медианы A1 и A2 разошлись больше — дрейф.
DRIFT_FALLBACK_PCT = COMPARISON_MIN_EFFECT_PCT
REPEAT_SUFFIX = " (повтор A)"
DRIFT_WARNING = "стенд дрейфовал за время Batch, сравнение ненадёжно"


def should_repeat_base(aba: object, versions: Sequence[Any]) -> bool:
    """Повторять ли базовую версию: опция включена и версий хотя бы две."""
    return bool(aba) and len(versions) >= 2


def check_drift(first: Mapping[str, Any] | None, repeat: Mapping[str, Any] | None,
                min_effect_pct: float = COMPARISON_MIN_EFFECT_PCT,
                fallback_pct: float = DRIFT_FALLBACK_PCT,
                noise_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Сравнивает два прогона базовой версии (итоги версии из Batch, ключ
    results — записи операций, как в полном JSON).

    Операция дрейфует, если compare_runs даёт РЕГРЕССИЯ/УСКОРЕНИЕ (значимо и
    больше порога эффекта) или, когда повторов на критерий не хватает, но
    их хотя бы два с каждой стороны, медианы разошлись больше fallback_pct.
    Одиночный замер (открытие файла в Batch — одно на версию) не сравнивается:
    на стенде с медленным диском одно открытие гуляет 9 → 14 с само по себе.

    noise_profile — профиль шума стенда (noise.load_noise_profile): у
    операции из профиля и порог эффекта, и запасной порог — её порог по шуму
    (как у сравнения версий и бисекта). Без него экспорт в XLTX, который на
    стенде конвертируется то за 5, то за 11 с (CV 16 %, порог 49 %), давал
    «дрейф» в каждом A-B-A (живой Batch 08.10.2026).

    Returns:
        dict: drift (True/False; None — повтор не удался, проверить нельзя),
        rows [{name, a1, a2, pct, verdict, drift}], drifted — имена,
        compared — сколько операций сравнено, warning — текст или None.
    """
    if not first or not repeat or not first.get("success") or not repeat.get("success"):
        return {"drift": None, "rows": [], "drifted": [], "compared": 0,
                "warning": "повтор базовой версии не удался — дрейф стенда не проверен"}
    a1_by = {r["name"]: r for r in first.get("results") or [] if isinstance(r, dict) and "name" in r}
    rows: list[dict[str, Any]] = []
    drifted: list[str] = []
    for r2 in repeat.get("results") or []:
        if not isinstance(r2, dict) or r2.get("name") not in a1_by:
            continue
        name, r1 = r2["name"], a1_by[r2["name"]]
        t1, t2 = r7_reports.comparable_time(r1), r7_reports.comparable_time(r2)
        if t1 is None or t2 is None:
            continue
        runs1, runs2 = r7_reports.valid_runs(r1), r7_reports.valid_runs(r2)
        if len(runs1) < 2 or len(runs2) < 2:
            continue
        pct = (t2 - t1) / t1 * 100.0
        thr, source, _cv = noise.threshold_for(noise_profile, name, min_effect_pct)
        res = compare_runs(runs1, runs2, min_effect_pct=thr)
        verdict = res["verdict"]
        if verdict in (REGRESSION, SPEEDUP):
            is_drift = True
        elif res.get("p_value") is None:
            # Критерию не хватило повторов — решает запасной порог.
            is_drift = abs(pct) > (thr if source == noise.SOURCE_NOISE else fallback_pct)
            verdict = f"{verdict}; Δ медиан {pct:+.1f} %"
        else:
            is_drift = False
        rows.append({"name": name, "a1": t1, "a2": t2, "pct": round(pct, 1),
                     "verdict": verdict, "drift": is_drift, "threshold_pct": round(thr, 2)})
        if is_drift:
            drifted.append(name)
    drift = bool(drifted)
    return {"drift": drift, "rows": rows, "drifted": drifted, "compared": len(rows),
            "warning": DRIFT_WARNING if drift else None}
