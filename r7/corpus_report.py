"""Страницы корпуса файлов: матрица прогона «файл × шаг» и сравнение
прогонов «файл × версия» (docs/corpus.md).

Модели — чистые функции над corpus_*.json (r7/corpus.py); вид — шаблоны
templates/html/corpus.html и corpus_compare.html через r7_reports.render
(автоэкранирование: имя файла клиента экранируется само).

Вердикт ячейки сравнения — compare_runs по годным повторам (r7_reports.
valid_runs), порог — из профиля шума стенда для той же операции вкладки
(открытие, экспорт), иначе 10 %; поправка Бенджамини-Хохберга — на семью
«все ячейки одной пары база → версия».
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import r7_reports
from r7 import corpus, noise
from r7.stats import (EQUIVALENT, LIKELY_REGRESSION, LIKELY_SPEEDUP, MIN_RUNS_FOR_COMPARISON,
                      REGRESSION, SPEEDUP, UNDETERMINED, adjust_family, compare_runs)

Record = Mapping[str, Any]

OPEN_RESULT_NAME = "Открытие файла"      # имя записи открытия (perf._open_result)
MB = 1024 * 1024

DECISION_TONE = {REGRESSION: "critical", LIKELY_REGRESSION: "warning", SPEEDUP: "good",
                 LIKELY_SPEEDUP: "good", EQUIVALENT: "neutral", UNDETERMINED: "warning"}
# Порядок итогов по версии на странице и в консоли.
SUMMARY_ORDER = (REGRESSION, LIKELY_REGRESSION, SPEEDUP, LIKELY_SPEEDUP, EQUIVALENT,
                 UNDETERMINED, "мало повторов", "ошибка", "нет данных")


def noise_name(step_key: str) -> str:
    """Имя операции в профиле шума: открытие и экспорт — как у вкладки."""
    if step_key == corpus.STEP_OPEN:
        return OPEN_RESULT_NAME
    if step_key == corpus.STEP_RECALC:
        return corpus.RECALC_OP_NAME
    return corpus.export_op_name(step_key.split(":", 1)[1])


def column_keys(files: Sequence[Record]) -> list[str]:
    """Ключи шагов всех файлов по порядку: open, recalc, экспорт по EXPORT_FORMATS."""
    present = {k for f in files for k in (f.get("steps") or {})}
    for f in files:
        plan = f.get("plan") or {}
        if plan:
            present.update(corpus.step_keys(corpus.Plan(
                steps=tuple(plan.get("steps") or ()), formats=tuple(plan.get("formats") or ()))))
    order = [corpus.STEP_OPEN, corpus.STEP_RECALC,
             *(f"{corpus.STEP_EXPORT}:{fmt}" for fmt in corpus.EXPORT_FORMATS)]
    return [k for k in order if k in present] + sorted(present - set(order))


def file_label(f: Record) -> str:
    return str(f.get("rel") or f.get("name") or f.get("id") or "?")


# ── Матрица одного прогона ───────────────────────────────────────────────

def step_cell(rec: Record | None, planned: bool = True) -> dict[str, Any]:
    """Ячейка «файл × шаг»: медиана, MAD и годные повторы; ошибка — красным."""
    if rec is None:
        return {"text": "—", "sub": None, "tone": "neutral",
                "title": "шаг не планировался" if not planned else "шаг не выполнен"}
    runs = r7_reports.valid_runs(rec)
    total = len(rec.get("runs") or [])
    err = rec.get("error")
    if not runs or (r7_reports.comparable_time(rec) is None and err):
        return {"text": "ошибка", "sub": None, "tone": "critical", "title": err or "нет повторов"}
    sub = f"MAD {r7_reports.fmt_sec(rec.get('mad'))}, {len(runs)}/{total}"
    flags = []
    if rec.get("n_timeouts"):
        flags.append(f"таймаутов {rec['n_timeouts']}")
    if rec.get("n_unverified"):
        flags.append(f"не подтверждено {rec['n_unverified']}")
    if err:
        flags.append(str(err))
    return {"text": r7_reports.fmt_sec(rec.get("time")), "sub": sub,
            "tone": "warning" if flags else "neutral", "title": "; ".join(flags) or None}


def matrix_model(report: Record) -> dict[str, Any]:
    """Модель страницы прогона корпуса: строки — файлы, столбцы — шаги."""
    files = list(report.get("files") or [])
    keys = column_keys(files)
    rows, n_err, n_cells = [], 0, 0
    for f in files:
        steps = f.get("steps") or {}
        planned = set(corpus.step_keys(corpus.Plan(
            steps=tuple((f.get("plan") or {}).get("steps") or ()),
            formats=tuple((f.get("plan") or {}).get("formats") or ()))))
        cells = [step_cell(steps.get(k), k in planned) for k in keys]
        n_cells += sum(1 for k in keys if k in steps)
        bad = bool(f.get("error")) or any(c["tone"] == "critical" for c in cells)
        n_err += bad
        rows.append({"label": file_label(f), "id": f.get("id"), "ext": f.get("ext"),
                     "size": r7_reports.fmt_num((f.get("size_bytes") or 0) / MB, 1),
                     "notes": f.get("notes"), "error": f.get("error"),
                     "elapsed": f.get("elapsed_sec"), "cells": cells, "bad": bad})
    plan = report.get("plan") or {}
    tiles = [
        {"label": "Файлов", "value": str(len(files)), "unit": "",
         "sub": "имена скрыты" if report.get("hide_names") else None},
        {"label": "Замерено шагов", "value": str(n_cells), "unit": "", "sub": None},
        {"label": "Файлов с ошибкой", "value": str(n_err), "unit": "",
         "status": {"tone": "critical" if n_err else "good",
                    "text": "есть ошибки" if n_err else "без ошибок"}},
        {"label": "Повторы", "value": None, "unit": "",
         "sub": (f"открытие {plan.get('open_runs')}, пересчёт {plan.get('recalc_runs')}, "
                 f"экспорт {plan.get('export_runs')}") if plan else None},
    ]
    return {"title": "Корпус файлов", "version": report.get("version") or "—",
            "timestamp": report.get("timestamp"), "tool_version": report.get("tool_version"),
            "columns": [corpus.step_title(k) for k in keys], "rows": rows, "tiles": tiles,
            "warnings": list(report.get("warnings") or [])
            + (["Прогон остановлен до конца корпуса."] if report.get("stopped") else []),
            "hide_names": bool(report.get("hide_names")),
            "build_rows": r7_reports.build_rows(
                report.get("build"), ((report.get("system") or {}).get("environment") or {}))}


def matrix_text(model: Record) -> str:
    """Матрица для консоли: файл, ячейки шагов."""
    width = max([len(r["label"]) for r in model["rows"]] + [10])
    width = min(width, 48)
    lines = [f"Корпус: {len(model['rows'])} файлов, версия {model['version']}",
             f"{'файл':{width}} " + " ".join(f"{c:>13}" for c in model["columns"])]
    for r in model["rows"]:
        cells = " ".join(f"{c['text']:>13}" for c in r["cells"])
        lines.append(f"{r['label'][:width]:{width}} {cells}" + ("  <<" if r["bad"] else ""))
        if r["error"]:
            lines.append(f"  • {r['error']}")
    lines.extend(f"  ⚠️ {w}" for w in model["warnings"])
    return "\n".join(lines)


def matrix_page(model: Mapping[str, Any]) -> str:
    return r7_reports.render("corpus.html", **model)


# ── Сравнение прогонов: «файл × версия» ──────────────────────────────────

def match_files(base: Sequence[Record], other: Sequence[Record]) -> dict[int, int]:
    """{индекс в base: индекс в other}: сначала по id (содержимое), затем по
    пути — файл, который переобезличили, остаётся тем же файлом корпуса."""
    out: dict[int, int] = {}
    used: set[int] = set()
    by_id = {f.get("id"): j for j, f in enumerate(other) if f.get("id")}
    for i, f in enumerate(base):
        j = by_id.get(f.get("id"))
        if j is not None and j not in used:
            out[i] = j
            used.add(j)
    by_rel = {file_label(f): j for j, f in enumerate(other) if j not in used}
    for i, f in enumerate(base):
        if i in out:
            continue
        j = by_rel.get(file_label(f))
        if j is not None and j not in used:
            out[i] = j
            used.add(j)
    return out


def _compare_cell(base_rec: Record | None, new_rec: Record | None,
                  profile: Record | None, step_key: str) -> dict[str, Any]:
    """Сырой результат сравнения одной ячейки (до поправки на семью)."""
    if base_rec is None or new_rec is None:
        return {"state": "нет данных", "missing": "base" if base_rec is None else "new"}
    b, n = r7_reports.valid_runs(base_rec), r7_reports.valid_runs(new_rec)
    tb, tn = r7_reports.comparable_time(base_rec), r7_reports.comparable_time(new_rec)
    if not b or not n or tb is None or tn is None:
        side = "базе" if (not b or tb is None) else "версии"
        err = (base_rec if side == "базе" else new_rec).get("error")
        return {"state": "ошибка", "note": f"ошибка в {side}" + (f": {err}" if err else "")}
    thr, src, cv = noise.threshold_for(profile, noise_name(step_key))
    res = compare_runs(b, n, threshold_pct=thr, noise_cv_pct=cv)
    res["threshold_source"] = src
    res["median_delta_pct"] = round((tn - tb) / tb * 100.0, 1)
    if len(b) < MIN_RUNS_FOR_COMPARISON or len(n) < MIN_RUNS_FOR_COMPARISON:
        return {"state": "мало повторов", "result": res}
    return {"state": "вердикт", "result": res}


def _cell_view(raw: Record, base_rec: Record | None, new_rec: Record | None) -> dict[str, Any]:
    """Ячейка версии для шаблона: медиана, Δ, вердикт и тон."""
    median = r7_reports.fmt_sec(r7_reports.comparable_time(new_rec)) if new_rec else "—"
    state = raw["state"]
    if state in ("нет данных", "ошибка"):
        note = raw.get("note") or ("нет в базе" if raw.get("missing") == "base"
                                   else "нет в этом прогоне")
        return {"median": median, "delta": "—", "verdict": state, "summary": state,
                "tone": "critical" if state == "ошибка" else "neutral", "note": note}
    res = raw["result"]
    if state == "мало повторов":
        return {"median": median, "delta": r7_reports.fmt_effect_ci(res["median_delta_pct"]),
                "verdict": "мало повторов", "summary": "мало повторов", "tone": "neutral",
                "note": (f"для вердикта нужно не меньше {MIN_RUNS_FOR_COMPARISON} годных "
                         f"повторов с каждой стороны (есть {res['n_base']} и {res['n_new']})")}
    decision = res.get("decision") or res.get("verdict")
    return {"median": median,
            "delta": r7_reports.fmt_effect_ci(res.get("effect_pct"), res.get("ci_low_pct"),
                                              res.get("ci_high_pct")),
            "verdict": decision, "summary": decision,
            "tone": DECISION_TONE.get(decision, "neutral"),
            "note": (f"порог {r7_reports.fmt_num(res.get('threshold_pct'), 1)} % "
                     f"({res.get('threshold_source')}), p {r7_reports.fmt_p(res.get('p_adjusted'))}"
                     f" после поправки на {res.get('family_size')} ячеек")}


def _pair(base: Record, other: Record, keys: Sequence[str],
          profile: Record | None) -> dict[tuple[int, str], dict[str, Any]]:
    """Ячейки пары база → версия с поправкой на семью по всем её ячейкам."""
    bfiles, ofiles = list(base.get("files") or []), list(other.get("files") or [])
    match = match_files(bfiles, ofiles)
    raw: dict[tuple[int, str], dict[str, Any]] = {}
    for i, bf in enumerate(bfiles):
        of = ofiles[match[i]] if i in match else None
        for k in keys:
            brec = (bf.get("steps") or {}).get(k)
            orec = (of.get("steps") or {}).get(k) if of else None
            if brec is None and orec is None:
                continue
            raw[(i, k)] = _compare_cell(brec, orec, profile, k)
    family = {key: r["result"] for key, r in raw.items() if r["state"] == "вердикт"}
    for key, adj in adjust_family(family).items():
        raw[key] = {**raw[key], "result": adj}
    return {key: {**r, "_new": (ofiles[match[key[0]]].get("steps") or {}).get(key[1])
                  if key[0] in match else None} for key, r in raw.items()}


def version_label(report: Record, index: int) -> str:
    v = report.get("version") or "?"
    ts = report.get("timestamp") or ""
    return f"{v} ({ts})" if ts else f"{v} #{index + 1}"


def compare_model(reports: Sequence[Record], profile: Record | None = None) -> dict[str, Any]:
    """Модель страницы «файл × версия». Первый отчёт — база, остальные
    сравниваются с ней. Файлы, которых нет в базе, в сравнение не входят
    (о них — предупреждение)."""
    if len(reports) < 2:
        raise corpus.CorpusError("для сравнения нужно не меньше двух отчётов корпуса")
    base, others = reports[0], list(reports[1:])
    bfiles = list(base.get("files") or [])
    keys = column_keys([f for r in reports for f in (r.get("files") or [])])
    pairs = [_pair(base, o, keys, profile) for o in others]
    rows = []
    for i, bf in enumerate(bfiles):
        file_keys = [k for k in keys if any((i, k) in p for p in pairs)
                     or k in (bf.get("steps") or {})]
        for n, k in enumerate(file_keys):
            brec = (bf.get("steps") or {}).get(k)
            cells = []
            for p in pairs:
                raw = p.get((i, k)) or {"state": "нет данных", "missing": "new", "_new": None}
                cells.append(_cell_view(raw, brec, raw.get("_new")))
            rows.append({"file": file_label(bf) if n == 0 else None, "rowspan": len(file_keys),
                         "id": bf.get("id"), "step": corpus.step_title(k),
                         "base": step_cell(brec), "cells": cells})
    versions = [{"label": version_label(r, i), "is_base": i == 0,
                 "hide_names": bool(r.get("hide_names"))} for i, r in enumerate(reports)]
    summaries = []
    for j in range(len(others)):
        counts: dict[str, int] = {}
        for r in rows:
            s = r["cells"][j]["summary"]
            counts[s] = counts.get(s, 0) + 1
        summaries.append({"label": versions[j + 1]["label"],
                          "counts": [{"text": s, "n": counts[s],
                                     "tone": DECISION_TONE.get(s, "neutral")}
                                    for s in SUMMARY_ORDER if counts.get(s)],
                          "regressions": counts.get(REGRESSION, 0)})
    return {"title": "Корпус: сравнение версий", "versions": versions, "rows": rows,
            "summaries": summaries, "warnings": _compare_warnings(reports),
            "noise_note": noise.describe_profile(profile)}


def _compare_warnings(reports: Sequence[Record]) -> list[str]:
    out = []
    for w in (r7_reports.fingerprint_warning(reports),
              r7_reports.schema_warning(r.get("measure_schema") for r in reports)):
        if w:
            out.append(w)
    base_ids = {f.get("id") for f in reports[0].get("files") or []}
    base_rels = {file_label(f) for f in reports[0].get("files") or []}
    for i, r in enumerate(reports[1:], start=1):
        extra = [file_label(f) for f in r.get("files") or []
                 if f.get("id") not in base_ids and file_label(f) not in base_rels]
        if extra:
            out.append(f"{version_label(r, i)}: файлов нет в базе, в сравнение не вошли — "
                       + ", ".join(extra))
    if len({bool(r.get("hide_names")) for r in reports}) > 1:
        out.append("Часть отчётов с именами, часть — с id: файлы сопоставлены по содержимому "
                   "(id), переобезличенные файлы могут не совпасть.")
    return out


def has_regression(model: Record) -> bool:
    return any(s["regressions"] for s in model["summaries"])


def compare_text(model: Record) -> str:
    """Итоги сравнения для консоли: по версии — счёт вердиктов и регрессии."""
    lines = [f"База: {model['versions'][0]['label']}"]
    for j, s in enumerate(model["summaries"]):
        counts = ", ".join(f"{it['text']}: {it['n']}" for it in s["counts"]) or "сравнивать нечего"
        lines.append(f"{s['label']}: {counts}")
        file = None
        for r in model["rows"]:
            file = r["file"] or file
            c = r["cells"][j]
            if c["verdict"] in (REGRESSION, LIKELY_REGRESSION, "ошибка"):
                lines.append(f"  << {file} · {r['step']}: {c['verdict']} {c['delta']} "
                             f"({r['base']['text']} → {c['median']})")
    lines.extend(f"  ⚠️ {w}" for w in model["warnings"])
    return "\n".join(lines)


def compare_page(model: Mapping[str, Any]) -> str:
    return r7_reports.render("corpus_compare.html", **model)
