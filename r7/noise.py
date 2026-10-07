"""Профиль шума стенда из A/A-прогонов (этап 3 плана, пункт 1).

Одна и та же версия Р7 меряется дважды подряд (tests/nightly_local.py --aa);
по каждому тесту из повторов обоих прогонов считается робастный CV. Порог
сравнения теста = max(NOISE_K × CV, NOISE_FLOOR_PCT): у Ctrl+V с разбросом
0,1 % порог 2 %, у открытия с 2,9 % — 8,7 %. Без профиля — прежние 10 %
(COMPARISON_MIN_EFFECT_PCT).

Профиль — отдельный файл Reports/noise_profile.json, формат полного JSON
прогона не меняется. Ключ — fingerprint_hash машины (r7/fingerprint.py):
шум одного стенда к другому не переносится. Подробности — docs/statistics.md.

Формат файла:

    {"format": 1,
     "machines": {"<fingerprint_hash>": {
         "fingerprint_hash": "...", "fingerprint": {...},
         "created": "2026-10-07 21:30:00", "version": "2026.3.2.3229",
         "reports": ["<timestamp A>", "<timestamp B>"],
         "tests": {"<операция>": {"cv_pct": 0.8, "n": 12, "median": 1.234,
                                  "aa_delta_pct": 0.3,
                                  "timestamps": ["<A>", "<B>"]}}}}}

Чистые функции, кроме чтения и записи файла; Tk и psutil не нужны.
"""
import json
import os
import statistics
import time
from pathlib import Path

from r7 import fingerprint
from r7.stats import COMPARISON_MIN_EFFECT_PCT, robust_cv_pct

NOISE_PROFILE_NAME = "noise_profile.json"
PROFILE_FORMAT = 1
NOISE_K = 3.0            # порог = 3 CV: сдвиг медианы на 3σ одного повтора
                         # шум стенда сам почти не даёт
NOISE_FLOOR_PCT = 2.0    # нижняя граница порога: меньше 2 % — уже не
                         # изменение в Р7, а дрейф диска и частоты CPU
NOISE_MIN_RUNS = 4       # меньше повторов в двух прогонах вместе — CV не считается

SOURCE_NOISE, SOURCE_DEFAULT = "шум стенда", "по умолчанию"


class NoiseProfileError(ValueError):
    """A/A-прогоны нельзя свести в профиль (разные стенды, нет отпечатка)."""


def _by_name(data):
    return {r["name"]: r for r in (data or {}).get("results") or []
            if isinstance(r, dict) and r.get("name")}


def profile_from_reports(report_a, report_b, created=None):
    """Запись профиля для машины из двух полных JSON одной версии.

    По каждой операции, у которой есть действительные повторы в обоих
    прогонах: повторы объединяются, CV = MAD × 1,4826 / медиана; разница
    медиан двух прогонов — aa_delta_pct (контроль: на шумном стенде она
    сама по себе больше порога).

    Raises:
        NoiseProfileError: у отчёта нет отпечатка машины или отпечатки разные.
    """
    import r7_reports   # здесь, а не наверху: r7_reports сам читает пороги отсюда
    hash_a, fp_a = fingerprint.report_fingerprint(report_a)
    hash_b, _fp_b = fingerprint.report_fingerprint(report_b)
    if not hash_a or not hash_b:
        raise NoiseProfileError("в отчёте нет отпечатка машины — профиль шума не к "
                                "чему привязать (нужен отчёт версии с fingerprint)")
    if hash_a != hash_b:
        raise NoiseProfileError(f"A/A-прогоны с разных стендов ({hash_a} и {hash_b})")
    ts = [report_a.get("timestamp"), report_b.get("timestamp")]
    tests = {}
    ops_b = _by_name(report_b)
    for name, ra in _by_name(report_a).items():
        rb = ops_b.get(name)
        ta, tb = r7_reports.comparable_time(ra), r7_reports.comparable_time(rb)
        if rb is None or ta is None or tb is None:
            continue
        runs = r7_reports.valid_runs(ra) + r7_reports.valid_runs(rb)
        cv = robust_cv_pct(runs)
        if len(runs) < NOISE_MIN_RUNS or cv is None:
            continue
        tests[name] = {"cv_pct": round(cv, 3), "n": len(runs),
                       "median": round(statistics.median(runs), 4),
                       "aa_delta_pct": round((tb - ta) / ta * 100.0, 2),
                       "timestamps": ts}
    return {"fingerprint_hash": hash_a, "fingerprint": fp_a,
            "created": created or time.strftime("%Y-%m-%d %H:%M:%S"),
            "version": report_a.get("version"), "reports": ts, "tests": tests}


def merge_profile(doc, entry):
    """Новый документ профиля, где запись машины entry заменяет прежнюю.

    Последний A/A-прогон машины — её текущий шум: стенд меняется (диск,
    драйверы), старые цифры не усредняются с новыми. Другие машины (общая
    папка команды) остаются как были.
    """
    machines = dict((doc or {}).get("machines") or {})
    machines[entry["fingerprint_hash"]] = entry
    return {"format": PROFILE_FORMAT, "machines": machines}


def profile_path(folder):
    return Path(folder) / NOISE_PROFILE_NAME


def read_profile_doc(folder):
    """Весь файл профиля (dict) или пустой документ — файла нет или он битый."""
    try:
        doc = json.loads(profile_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"format": PROFILE_FORMAT, "machines": {}}
    if not isinstance(doc, dict) or not isinstance(doc.get("machines"), dict):
        return {"format": PROFILE_FORMAT, "machines": {}}
    return doc


def save_profile_doc(folder, doc):
    """Пишет профиль через временный файл: прерванная запись не оставит
    полупустой JSON вместо старого профиля."""
    path = profile_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_noise_profile(folder, fingerprint_hash):
    """Запись профиля машины или None.

    None — хэша нет (старый отчёт), файла нет, он битый или в нём нет этой
    машины. Записи тестов без числового cv_pct отбрасываются: порог из
    битой записи хуже порога по умолчанию.
    """
    if not fingerprint_hash or folder is None:
        return None
    entry = read_profile_doc(folder)["machines"].get(fingerprint_hash)
    if not isinstance(entry, dict) or not isinstance(entry.get("tests"), dict):
        return None
    tests = {name: t for name, t in entry["tests"].items()
             if isinstance(t, dict) and isinstance(t.get("cv_pct"), (int, float))
             and not isinstance(t.get("cv_pct"), bool) and t["cv_pct"] >= 0}
    return {**entry, "tests": tests}


def noise_for_report(folder, report):
    """Профиль машины, на которой снят report (полный JSON), или None."""
    fp_hash, _fp = fingerprint.report_fingerprint(report)
    return load_noise_profile(folder, fp_hash)


def threshold_from_cv(cv_pct, k=NOISE_K, floor=NOISE_FLOOR_PCT):
    """Порог теста по его CV: max(k × CV, floor), %."""
    return max(k * cv_pct, floor)


def threshold_for(profile, name, default_pct=COMPARISON_MIN_EFFECT_PCT):
    """(порог %, источник, CV %) для операции name.

    Есть тест в профиле — порог по шуму (SOURCE_NOISE) и его CV; нет —
    default_pct (SOURCE_DEFAULT) и CV None.
    """
    t = ((profile or {}).get("tests") or {}).get(name)
    if t is None:
        return default_pct, SOURCE_DEFAULT, None
    return threshold_from_cv(t["cv_pct"]), SOURCE_NOISE, t["cv_pct"]


def describe_profile(profile):
    """Строка для отчёта: откуда пороги."""
    if not profile:
        return (f"Профиля шума для этого стенда нет — порог {COMPARISON_MIN_EFFECT_PCT:g} % "
                f"для всех операций. Снять профиль: tests/nightly_local.py --aa.")
    return (f"Пороги — из профиля шума стенда {profile.get('fingerprint_hash')} "
            f"(A/A-прогон {profile.get('created') or '—'}, версия "
            f"{profile.get('version') or '—'}): max({NOISE_K:g} × CV, "
            f"{NOISE_FLOOR_PCT:g} %); операции без профиля — "
            f"{COMPARISON_MIN_EFFECT_PCT:g} %.")


def format_profile(entry):
    """Текст профиля для журнала: по тесту CV, n, порог и сколько ловят
    повторы одного прогона (половина объединённых A/A-повторов)."""
    import r7_reports   # см. profile_from_reports
    from r7.stats import min_detectable_effect_pct
    lines = [f"Профиль шума стенда {entry.get('fingerprint_hash')} "
             f"({entry.get('created')}, версия {entry.get('version') or '—'})",
             f"{'операция':44} {'n':>3} {'CV %':>6} {'A/A Δ%':>7} {'порог %':>8}  чувствительность"]
    for name, t in sorted((entry.get("tests") or {}).items()):
        thr = threshold_from_cv(t["cv_pct"])
        side = max(1, t["n"] // 2)
        mde = min_detectable_effect_pct(t["cv_pct"], side, side, thr)
        lines.append(f"{name[:44]:44} {t['n']:>3} {t['cv_pct']:>6.2f} "
                     f"{t.get('aa_delta_pct', 0.0):>+7.2f} {thr:>8.1f}  "
                     f"{r7_reports.mde_text(side, mde) or '—'}")
    return "\n".join(lines)
