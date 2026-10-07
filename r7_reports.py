"""HTML-отчёты R7-Testovarka: модели страниц и рендер шаблонов Jinja2.

Пять отчётов инструмента (прогон, сравнение версий, тренды, сводка Batch,
тест своего файла) строятся здесь из данных, которые им передают методы
`R7Testovarka`. Шаблоны — `templates/html/*.html`, общая основа —
`base.html` (токены цвета, светлая и тёмная темы, шрифты, печать).

Почему отдельный модуль и Jinja2, а не f-строки (06.10.2026):
- автоэкранирование: имя файла, версия, ОС, текст ошибки — всё, что
  приходит снаружи, экранируется само, пропуск `html.escape` невозможен;
- одна основа на пять страниц вместо пяти копий стилей;
- модели страниц — чистые функции над словарями, тестируются без Tk.

JSON для `<script>` по-прежнему идёт через `json_for_script` (защита от
`</script` внутри строки), результат помечается `Markup`.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from r7 import fingerprint, noise
from r7.build_meta import build_summary
from r7.calibration import format_calibration
from r7.stats import adjust_family

# Цвета серий — те же, что SERIES_COLORS в r7_Testovarka (эталонная
# категориальная палитра скилла dataviz, проверена validate_palette.js на
# светлой и тёмной поверхности). Светлые значения уходят в JSON графиков,
# тёмные подставляет скрипт страницы при тёмной теме.
SERIES_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                "#e87ba4", "#008300", "#4a3aa7", "#e34948")
SERIES_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500",
               "#d55181", "#008300", "#9085e9", "#e66767")
SERIES_OTHER = "#8a8a86"

TEMPLATES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "templates" / "html"

_env = None


def _environment():
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(default=True, default_for_string=True),
            trim_blocks=True, lstrip_blocks=True,
        )
        _env.filters["sec"] = fmt_sec
        _env.filters["num"] = fmt_num
        _env.filters["mb"] = fmt_mb
        _env.filters["pct"] = fmt_pct
        _env.filters["ms"] = fmt_ms
    return _env


def json_for_script(obj, **kwargs):
    """json.dumps для вставки внутрь <script>: «</» → «<\\/», иначе строка
    с «</script» закрыла бы тег раньше времени. Результат — Markup."""
    return Markup(json.dumps(obj, ensure_ascii=False, **kwargs).replace("</", r"<\/"))


def render(template_name, **context):
    """Рендерит шаблон отчёта с общими полями (дата, год)."""
    context.setdefault("generated_at", datetime.now().strftime("%d.%m.%Y %H:%M"))
    context.setdefault("series_light", list(SERIES_LIGHT))
    context.setdefault("series_dark", list(SERIES_DARK))
    return _environment().get_template(template_name).render(**context)


# ── Форматирование чисел (единицы — как в журнале инструмента) ───────────

def fmt_num(value, digits=1):
    """1234.5 → «1 234,5»; None → «—». Пробел тысяч — неразрывный узкий."""
    if value is None:
        return "—"
    try:
        s = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
    return s.replace(",", " ").replace(".", ",")


def fmt_sec(value, digits=3):
    return "—" if value is None else fmt_num(value, digits)


def fmt_ms(value):
    return "—" if value is None else fmt_num(value, 1)


def fmt_mb(value):
    return "—" if value is None else fmt_num(value, 0)


def fmt_pct(value, digits=0):
    return "—" if value is None else fmt_num(value, digits)


def _signed(value, digits):
    v = round(value, digits) + 0.0   # −0,0 → +0,0
    return f"{v:+.{digits}f}".replace(".", ",")


def fmt_effect_ci(effect, low=None, high=None):
    """«+12 % [+7; +18]» — сдвиг медианы и 95 %-интервал. Целые проценты,
    когда сдвиг по модулю ≥ 10, иначе одна десятая: «+1,2 % [+0,4; +2,0]».
    Без интервала — только сдвиг; None — «—»."""
    if effect is None:
        return "—"
    digits = 0 if abs(effect) >= 10 else 1
    text = f"{_signed(effect, digits)} %"
    if low is not None and high is not None:
        text += f" [{_signed(low, digits)}; {_signed(high, digits)}]"
    return text


def fmt_p(p):
    """p-значение для таблиц: «0,004», «< 0,001», «—»."""
    if p is None:
        return "—"
    if p < 0.001:
        return "< 0,001"
    return f"{p:.3f}".replace(".", ",")


def repeats_word(n):
    """«повтор», «повтора», «повторов» по числу."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "повтор"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "повтора"
    return "повторов"


def mde_text(n, mde_pct):
    """«7 повторов ловят сдвиг от 5 %» — минимальный обнаружимый эффект."""
    if not n or mde_pct is None:
        return None
    digits = 0 if mde_pct >= 10 else 1
    verb = "ловит" if repeats_word(n) == "повтор" else "ловят"
    return f"{n} {repeats_word(n)} {verb} сдвиг от {fmt_num(mde_pct, digits)} %"


def cpu_all_cores_sub(peak_core_pct, cpu_count):
    """Подпись к пику CPU: тот же пик в доле всей машины.

    Пик меряется в % одного ядра (сумма по процессам Р7), поэтому бывает
    601 % — шесть ядер заняты полностью. На 16 ядрах это 38 % машины: обе
    цифры из одного замера, вторая — первая, делённая на число ядер."""
    if not cpu_count:
        return None
    if peak_core_pct is None:
        return f"{cpu_count} логических ядер"
    return f"{fmt_pct(peak_core_pct / cpu_count)} % всех {cpu_count} ядер"


def comparable_time(result):
    """Время операции для сравнения и трендов; None — сравнивать нечего
    (провал пишется как time=0.0 с error, частичный — медианой с error)."""
    if not result or result.get("error"):
        return None
    t = result.get("time")
    if t is None or t <= 0:
        return None
    return t


def valid_runs(result):
    """Повторы для вердикта — те же, что вошли в медиану: без таймаутов,
    без неподтверждённых и без прогрева, если его отбросила статистика."""
    result = result or {}
    runs = result.get("runs") or []
    statuses = result.get("run_statuses")
    if not statuses or len(statuses) != len(runs):
        statuses = ["ok"] * len(runs)
    valid = [(i, t) for i, (t, st) in enumerate(zip(runs, statuses))
             if st not in ("timeout", "unverified")]
    if result.get("first_run_discarded") and valid and valid[0][0] == 0:
        valid = valid[1:]
    return [t for _i, t in valid]


SCHEMA_WARNING = ("В сравнении смешаны файлы разных версий схемы замера ({versions}) — "
                  "версии по-разному определяют простой Р7 и итоговое время "
                  "(1 — среднее, 2 — медиана с порогом CPU по всей машине, 3–4 — медиана "
                  "с порогом по одному ядру, в 4 переделаны ВПР, ПКМ и удаление столбца, "
                  "7 — конец операции по ответу редактора, 8 — неподтверждённые прогоны "
                  "вне медианы). Числа из разных версий несопоставимы напрямую.")


def schema_warning(schemas):
    schemas = {s or 1 for s in schemas}
    if len(schemas) <= 1:
        return None
    return SCHEMA_WARNING.format(versions=", ".join(str(v) for v in sorted(schemas)))


def fingerprint_warning(datas):
    """Предупреждение «отчёты с разных машин» по полным JSON (как
    schema_warning по схемам); отчёты без отпечатка не считаются чужими."""
    return fingerprint.mismatch_warning([fingerprint.report_fingerprint(d) for d in datas])


def build_rows(build, env=None):
    """Строки блока «Стенд» про сборку Р7 и калибровку из `build` отчёта и
    его окружения; отчёт старой версии даёт прочерки, а не ошибку."""
    b = build_summary({"build": build})
    env = env or {}
    exe = None
    if b["sha_short"]:
        exe = f"sha256 {b['sha_short']}" + (f", от {b['exe_date']}" if b["exe_date"] else "")
    elif b["exe_date"]:
        exe = f"от {b['exe_date']}"
    rows = [("Сборка Р7", b["build_number"] or "—"),
            ("DesktopEditors.exe", exe or "—")]
    if b["installer_file"]:
        rows.append(("Дистрибутив", b["installer_file"]))
    if b["changelog_url"]:
        rows.append(("Changelog", b["changelog_url"]))
    rows.append(("Индекс стенда", format_calibration(env.get("calibration")) or "—"))
    rows.append(("Отпечаток машины", env.get("fingerprint_hash") or "—"))
    return rows


# ── Метки прогона операции ────────────────────────────────────────────────

def result_badges(r):
    """Метки к строке операции: что ограничивает доверие к цифре."""
    badges = []
    if r.get("n_timeouts"):
        badges.append({"tone": "critical", "text": f"таймаут×{r['n_timeouts']}",
                       "title": "Прогоны с таймаутом исключены из медианы"})
    if r.get("n_unverified"):
        badges.append({"tone": "critical", "text": f"без подтверждения×{r['n_unverified']}",
                       "title": "CDP не подтвердил результат этих прогонов — они исключены из медианы"})
    if r.get("runs_independent") is False:
        badges.append({"tone": "warning", "text": "зависимые повторы",
                       "title": "Правки прогонов не удалось откатить: повторы шли на "
                                "накопленном документе и зависят друг от друга"})
    if r.get("below_floor"):
        badges.append({"tone": "warning", "text": "<порога",
                       "title": "Р7-Офис не был занят дольше порога — операция быстрее, "
                                "чем инструмент умеет измерять"})
    if r.get("disk_note"):
        badges.append({"tone": "warning", "text": "диск", "title": r["disk_note"]})
    if r.get("n_throttled"):
        badges.append({"tone": "warning", "text": f"троттлинг×{r['n_throttled']}",
                       "title": "Частота CPU в этих повторах опускалась ниже порога от "
                                "номинальной — в медиану они вошли, но могут быть медленнее"})
    if r.get("first_run_discarded"):
        badges.append({"tone": "neutral", "text": "1-й отброшен",
                       "title": "Первый прогон — прогрев, в медиану не вошёл"})
    return badges


def _disk_text(disk):
    if not disk:
        return None
    try:
        return (f"чтение {fmt_mb(disk.get('sys_read_mb'))} МБ, запись "
                f"{fmt_mb(disk.get('sys_write_mb'))} МБ")
    except Exception:
        return None


def _x2t_text(x2t):
    if not x2t or not x2t.get("count"):
        return None
    txt = f"запусков {x2t['count']}, {fmt_sec(x2t.get('sec'), 2)} с"
    if x2t.get("failed_codes"):
        txt += ", упал: " + ", ".join(x2t["failed_codes"])
    return txt


def _run_flags(r, n):
    """Пометки повторов (схема 10): троттлинг CPU. Старый отчёт — без пометок."""
    notes = r.get("run_notes") or []
    freqs = r.get("run_cpu_freq_pct") or []
    out = []
    for i in range(n):
        throttled = i < len(notes) and "throttle" in (notes[i] or [])
        f = freqs[i] if i < len(freqs) else None
        out.append({"throttled": throttled,
                    "note": (f"частота CPU до {fmt_num(f, 0)} % номинальной"
                             if throttled and f is not None else None)})
    return out


def _power_plan_text(env):
    """План питания прогона; если инструмент переключал план — и прежний."""
    plan = env.get("power_plan_during") or env.get("power_plan")
    before = env.get("power_plan_before")
    if not plan:
        return "—"
    if before and before != plan:
        return f"{plan} (до прогона: {before})"
    return plan


# ── Отчёт одного прогона ─────────────────────────────────────────────────

def run_report_model(results, test_file, open_elapsed, version, system=None,
                     summary=None, cpu_count=None, schema=None, tool_version=None,
                     build=None):
    """Модель страницы прогона: итоги наверху, предупреждения, график,
    таблица операций с раскрывающимися деталями.

    Args:
        results: список записей операций (как в performance_full_*.json).
        test_file: Path тестового файла.
        open_elapsed: время открытия файла, с.
        version: строка версии Р7.
        system: dict из _build_system_info (ОС, CPU, RAM, окружение).
        summary: dict сводки (peak_ram_mb, leak_detection, …).
        cpu_count: число логических ядер.
        tool_version: версия R7-Testovarka (r7.version.__version__); None —
            отчёт старой версии, строка «—».
        schema: MEASURE_SCHEMA_VERSION.
        build: объект `build` отчёта (r7.build_meta); None — старый отчёт.
    """
    system = system or {}
    summary = summary or {}
    env = system.get("environment") or {}
    ops = [r for r in results if r.get("name") != "Открытие файла"]
    open_r = next((r for r in results if r.get("name") == "Открытие файла"), None)

    ok_ops = [r for r in ops if comparable_time(r) is not None]
    slowest = max(ok_ops, key=lambda r: r["time"]) if ok_ops else None
    total_ops_time = sum(r["time"] for r in ok_ops) if ok_ops else None
    errors = [r for r in ops if r.get("error")]
    n_timeouts = sum(r.get("n_timeouts") or 0 for r in results)
    n_unverified = sum(r.get("n_unverified") or 0 for r in results)
    n_dependent = sum(1 for r in results if r.get("runs_independent") is False)

    peak_ram = summary.get("peak_ram_mb")
    if peak_ram is None:
        rams = [r.get("ram") for r in results if r.get("ram") is not None]
        peak_ram = max(rams) if rams else None
    peak_cpu_core = None
    cores = [r.get("cpu_peak_core_pct") for r in results if r.get("cpu_peak_core_pct") is not None]
    if cores:
        peak_cpu_core = max(cores)

    # Доверие к цифрам — одна плитка со статусом.
    if errors:
        trust = {"tone": "critical", "text": f"{len(errors)} с ошибкой"}
    elif n_timeouts or n_unverified:
        trust = {"tone": "warning",
                 "text": ", ".join(x for x in (
                     f"таймаутов {n_timeouts}" if n_timeouts else "",
                     f"без подтверждения {n_unverified}" if n_unverified else "") if x)}
    elif n_dependent:
        trust = {"tone": "warning", "text": f"зависимые повторы: {n_dependent}"}
    else:
        trust = {"tone": "good", "text": "все прогоны подтверждены"}

    tiles = [
        {"label": "Открытие файла", "value": fmt_sec(open_elapsed, 2), "unit": "с",
         # Ключи cold_start_ms/warm_start_ms — прежние (старые отчёты и тренды),
         # подписи — по смыслу фаз: до окна Р7 и от окна до готового документа.
         "sub": (f"запуск Р7 {fmt_num((open_r.get('cold_start_ms') or 0) / 1000, 2)} с, "
                 f"загрузка документа {fmt_num((open_r.get('warm_start_ms') or 0) / 1000, 2)} с")
         if open_r and open_r.get("cold_start_ms") is not None else None},
        {"label": "Самая долгая операция",
         "value": fmt_sec(slowest["time"], 2) if slowest else "—", "unit": "с",
         "sub": slowest["name"] if slowest else "нет операций без ошибок"},
        {"label": "Сумма времени операций", "value": fmt_sec(total_ops_time, 1), "unit": "с",
         "sub": f"{len(ok_ops)} из {len(ops)} операций"},
        {"label": "Пик RAM Р7", "value": fmt_mb(peak_ram), "unit": "МБ", "sub": None},
        {"label": "Пик CPU", "value": fmt_pct(peak_cpu_core), "unit": "% ядра",
         "sub": cpu_all_cores_sub(peak_cpu_core, cpu_count)},
        {"label": "Доверие к цифрам", "value": None, "status": trust, "sub": None},
    ]

    warnings = list(env.get("warnings") or [])
    if open_r and open_r.get("disk_note"):
        warnings.append(f"Открытие: {open_r['disk_note']}")
    for r in results:
        for a in r.get("r7_alerts") or []:
            warnings.append(f"{r['name']}: Р7 показал окно «{a}»")
    leak = summary.get("leak_detection") or {}
    if leak.get("leak"):
        warnings.append(f"Утечка памяти: {leak.get('verdict') or 'обнаружена'}")

    rows = []
    for r in results:
        runs = r.get("runs") or []
        statuses = r.get("run_statuses") or ["ok"] * len(runs)
        run_flags = _run_flags(r, len(runs))
        rows.append({
            "name": r["name"],
            "is_open": r.get("name") == "Открытие файла",
            "error": r.get("error"),
            "time": fmt_sec(r.get("time")) if comparable_time(r) is not None or not r.get("error") else "—",
            "mad": fmt_sec(r.get("mad")) if r.get("mad") is not None and len(runs) > 1 else None,
            "n": r.get("n_runs", len(runs)), "total": len(runs),
            "range": (f"{fmt_sec(r.get('min'))}–{fmt_sec(r.get('max'))}"
                      if len(runs) > 1 and r.get("min") is not None else None),
            "api_ms": fmt_ms(r.get("api_ms")) if r.get("api_ms") is not None else None,
            "cpu_sec": fmt_sec(r.get("cpu_sec"), 2) if r.get("cpu_sec") is not None else "—",
            "cpu_core": fmt_pct(r.get("cpu")) if r.get("cpu") is not None else "—",
            "ram": fmt_mb(r.get("ram")) if r.get("ram") is not None else "—",
            # Схема 10 — вторичные колонки; старые отчёты дают прочерк.
            "ux_frame": fmt_ms(r.get("ux_first_frame_ms")),
            "ux_task": fmt_ms(r.get("ux_longest_task_ms")),
            "js_heap": fmt_mb(r.get("js_heap_mb")),
            "disk": _disk_text(r.get("disk")),
            "badges": result_badges(r),
            "runs": [{"i": i + 1, "time": fmt_sec(t), "status": st, **run_flags[i]}
                     for i, (t, st) in enumerate(zip(runs, statuses))],
            "x2t": _x2t_text(r.get("x2t")),
            "alerts": r.get("r7_alerts") or [],
            "ready_markers": r.get("ready_markers"),
        })

    chart = {
        "labels": [r["name"] for r in results],
        "times": [round(r["time"], 3) if comparable_time(r) is not None else None for r in results],
        "rams": [r.get("ram") for r in results],
        "cpus": [r.get("cpu") for r in results],
    }

    meta = [
        ("Версия Р7-Офис", version or "—"),
        *build_rows(build, env)[:2],
        ("Файл", f"{test_file.name}"
                 + (f" ({fmt_num(test_file.stat().st_size / 2**20, 1)} МБ)"
                    if test_file.exists() else "")),
        ("Схема замера", str(schema) if schema else "—"),
        ("Версия инструмента", tool_version or "—"),
        ("ОС", system.get("os") or "—"),
        ("Процессор", system.get("cpu_model") or "—"),
        ("RAM стенда", f"{fmt_num(system.get('ram_total_gb'), 1)} ГБ" if system.get("ram_total_gb") else "—"),
        ("Масштаб экрана", f"{system['dpi_scale_pct']} %" if system.get("dpi_scale_pct") else "—"),
        ("План питания", _power_plan_text(env)),
        *build_rows(build, env)[2:],
    ]

    return {
        "title": "Отчёт о прогоне",
        "version": version, "file_name": test_file.name,
        "tiles": tiles, "warnings": warnings, "rows": rows, "meta": meta,
        "chart_json": json_for_script(chart),
        "n_ops": len(ops), "n_ok": len(ok_ops), "errors": errors,
    }


# ── Сравнение версий ──────────────────────────────────────────────────────

VERDICT_TONE = {"РЕГРЕССИЯ": "critical", "УСКОРЕНИЕ": "good", "без изменений": "neutral",
                "эквивалентно": "neutral", "не определено": "warning",
                "вероятная регрессия": "warning", "вероятное ускорение": "warning"}


def _compare_column(base, ds, op_names, compare_fn, min_runs, noise_profile):
    """Вердикты одной сравниваемой колонки против базы, с поправкой
    Бенджамини-Хохберга на все её операции. {op: результат} — только
    операции, где вердикт вообще выносится; причины отказа — в cells."""
    raw = {}
    for op in op_names:
        base_r, r = base["lookup"].get(op), ds["lookup"].get(op)
        if comparable_time(base_r) is None or comparable_time(r) is None:
            continue
        if base_r.get("runs_independent") is False or r.get("runs_independent") is False:
            continue
        b_runs, n_runs = valid_runs(base_r), valid_runs(r)
        if len(b_runs) < min_runs or len(n_runs) < min_runs:
            continue
        thr, _source, cv = noise.threshold_for(noise_profile, op)
        raw[op] = compare_fn(b_runs, n_runs, threshold_pct=thr, noise_cv_pct=cv)
    return adjust_family(raw)


def _verdict_title(res):
    """Подсказка к вердикту: интервал, порог, p, сдвиг Ходжеса-Лемана, MDE."""
    if res.get("effect_pct") is None:
        return f"n={res['n_base']}/{res['n_new']}"
    effect = fmt_effect_ci(res["effect_pct"], res.get("ci_low_pct"), res.get("ci_high_pct"))
    parts = [f"сдвиг медианы {effect}"]
    if res.get("threshold_pct") is not None:
        parts.append(f"порог ±{fmt_num(res['threshold_pct'], 1)} %")
    p_raw = res.get("p_raw", res.get("p_value"))
    parts.append(f"p={fmt_p(p_raw)}" + (" (точный)" if res.get("p_exact") else ""))
    if res.get("p_adjusted") is not None:
        parts.append(f"p скорр.={fmt_p(res['p_adjusted'])} (БХ, {res['family_size']} сравнений)")
    if res.get("hl_shift_pct") is not None:
        parts.append(f"Ходжес-Леман {_signed(res['hl_shift_pct'], 1)} %")
    mde = mde_text(min(res["n_base"], res["n_new"]), res.get("mde_pct"))
    if mde:
        parts.append(mde)
    parts.append(f"n={res['n_base']}/{res['n_new']}")
    return ", ".join(parts)


def _fill_verdict_cell(cell, res):
    """Вердикт, тон, подсказка и интервал ячейки сравнения."""
    decision = res.get("decision") or res["verdict"]
    cell["verdict"] = decision
    cell["verdict_tone"] = VERDICT_TONE.get(decision, "neutral")
    cell["verdict_title"] = _verdict_title(res)
    if res.get("ci_low_pct") is not None:
        cell["ci"] = fmt_effect_ci(res["effect_pct"], res["ci_low_pct"], res["ci_high_pct"])


def _change_item(op, version, res, base_t, t):
    """Строка вывода о регрессии или ускорении; None — эффекта нет."""
    if res.get("effect_pct") is None:
        return None
    effect = fmt_effect_ci(res["effect_pct"], res.get("ci_low_pct"), res.get("ci_high_pct"))
    p_adj = res.get("p_adjusted")
    p_text = f"p скорр.={fmt_p(p_adj)}" if p_adj is not None else f"p={res['p_value']}"
    thr = res.get("threshold_pct")
    thr_text = "" if thr is None else f", порог ±{fmt_num(thr, 1)} %"
    return {"op": op, "version": version, "effect": effect,
            "p": p_adj if p_adj is not None else res["p_value"],
            "base_time": fmt_sec(base_t), "time": fmt_sec(t),
            "text": (f"{op}: {version} {effect} к базе "
                     f"({fmt_sec(base_t)} → {fmt_sec(t)} с{thr_text}, {p_text})")}


def comparison_model(datasets, base_path, compare_fn, min_runs, noise_profile=None):
    """Модель страницы сравнения 2–8 прогонов.

    Вердикт — по 95 %-интервалу изменения медианы против порога теста и p с
    поправкой Бенджамини-Хохберга на операции одной колонки
    (docs/statistics.md).

    Args:
        datasets: [{path, version, data}], data — содержимое performance_full.
        base_path: path базового прогона.
        compare_fn: compare_runs(base_times, new_times, threshold_pct=…,
            noise_cv_pct=…) → dict с вердиктом.
        min_runs: минимум повторов для вердикта.
        noise_profile: запись профиля шума машины базового прогона
            (r7.noise.noise_for_report) или None — порог 10 % для всех.
    """
    seen, op_names = set(), []
    for ds in datasets:
        for r in ds["data"].get("results", []):
            if r["name"] not in seen:
                op_names.append(r["name"])
                seen.add(r["name"])
    for ds in datasets:
        ds["lookup"] = {r["name"]: r for r in ds["data"].get("results", [])}
    base = next(ds for ds in datasets if ds["path"] == base_path)

    versions = []
    for i, ds in enumerate(datasets):
        b = build_summary(ds["data"])
        versions.append({"label": ds["version"], "is_base": ds["path"] == base_path,
                         "color": SERIES_LIGHT[i % len(SERIES_LIGHT)], "index": i,
                         # Номер сборки под именем версии; None — старый отчёт.
                         "build": b["build_number"], "sha": b["sha_short"],
                         "exe_date": b["exe_date"]})

    # Вердикты — сначала вывод.
    regressions, speedups, no_data = [], [], 0
    column_results = {i: _compare_column(base, ds, op_names, compare_fn, min_runs, noise_profile)
                      for i, ds in enumerate(datasets) if ds["path"] != base_path}
    cells = {}   # (op, idx) → {time, delta, verdict}
    for op in op_names:
        base_r = base["lookup"].get(op)
        base_t = comparable_time(base_r)
        for col, ds in enumerate(datasets):
            r = ds["lookup"].get(op)
            t = comparable_time(r)
            cell = {"time": fmt_sec(t) if t is not None else None,
                    "error": (r or {}).get("error"), "delta": None, "delta_tone": "neutral",
                    "ci": None, "verdict": None, "verdict_tone": "neutral", "verdict_title": None}
            if ds["path"] != base_path:
                if t is not None and base_t:
                    pct = (t - base_t) / base_t * 100
                    cell["delta"] = f"{pct:+.1f} %".replace(".", ",")
                    cell["delta_tone"] = ("neutral" if abs(pct) <= 5
                                          else "good" if pct < 0 else "critical")
                if t is None or base_t is None:
                    cell["verdict_title"] = "Операция с ошибкой — сравнивать нечего"
                elif (base_r.get("runs_independent") is False
                      or r.get("runs_independent") is False):
                    cell["verdict_title"] = ("Зависимые повторы (правки не откатывались) — "
                                             "статистический вердикт не выносится")
                else:
                    b_runs, n_runs = valid_runs(base_r), valid_runs(r)
                    if len(b_runs) < min_runs or len(n_runs) < min_runs:
                        cell["verdict_title"] = (f"Нужно минимум {min_runs} повторов на каждую "
                                                 f"версию — есть {len(b_runs)} и {len(n_runs)}")
                    else:
                        res = column_results[col][op]
                        _fill_verdict_cell(cell, res)
                        item = _change_item(op, ds["version"], res, base_t, t)
                        if item and res["verdict"] == "РЕГРЕССИЯ":
                            regressions.append(item)
                        elif item and res["verdict"] == "УСКОРЕНИЕ":
                            speedups.append(item)
                if cell["verdict"] is None and cell["time"] is None:
                    no_data += 1
            cells[(op, col)] = cell

    rows = []
    for op in op_names:
        thr, source, cv = noise.threshold_for(noise_profile, op)
        rows.append({"op": op, "cells": [cells[(op, i)] for i in range(len(datasets))],
                     "threshold": f"±{fmt_num(thr, 1)} %",
                     "threshold_title": (f"{source}: CV {fmt_num(cv, 2)} %" if cv is not None
                                         else source)})

    chart = {
        "labels": op_names,
        "time": [{"label": v["label"] + (" (база)" if v["is_base"] else ""),
                  "data": [round(ds["lookup"][op]["time"], 3)
                           if comparable_time(ds["lookup"].get(op)) is not None else None
                           for op in op_names],
                  "backgroundColor": v["color"], "borderRadius": 3}
                 for v, ds in zip(versions, datasets)],
        "ram": [{"label": v["label"], "data": [ds["lookup"][op].get("ram") if op in ds["lookup"] else None
                                               for op in op_names],
                 "backgroundColor": v["color"], "borderRadius": 3}
                for v, ds in zip(versions, datasets)],
        "cpu": [{"label": v["label"], "data": [ds["lookup"][op].get("cpu") if op in ds["lookup"] else None
                                               for op in op_names],
                 "backgroundColor": v["color"], "borderRadius": 3}
                for v, ds in zip(versions, datasets)],
    }

    systems = []
    for v, ds in zip(versions, datasets):
        s = ds["data"].get("system") or {}
        summ = ds["data"].get("summary") or {}
        env = s.get("environment") if isinstance(s.get("environment"), dict) else {}
        fp_hash, _fp = fingerprint.report_fingerprint(ds["data"])
        systems.append({"version": v, "os": s.get("os"), "ram_total": s.get("ram_total_gb"),
                        "cpu": s.get("cpu_model"), "peak_ram": summ.get("peak_ram_mb"),
                        "peak_cpu": summ.get("peak_cpu_pct"),
                        "timestamp": ds["data"].get("timestamp"),
                        "schema": ds["data"].get("measure_schema") or 1,
                        "machine": fp_hash,
                        "calibration": format_calibration(env.get("calibration"))})

    conclusion_tone = "critical" if regressions else "good" if speedups else "neutral"
    return {
        "title": "Сравнение версий",
        "versions": versions, "rows": rows, "systems": systems,
        "regressions": regressions, "speedups": speedups, "no_data": no_data,
        "conclusion_tone": conclusion_tone,
        "schema_warning": schema_warning(ds["data"].get("measure_schema") for ds in datasets),
        "fingerprint_warning": fingerprint_warning(ds["data"] for ds in datasets),
        "chart_json": json_for_script(chart),
        "min_runs": min_runs,
        "base_label": base["version"],
        "noise_note": noise.describe_profile(noise_profile),
        "has_noise_profile": bool(noise_profile),
    }


# ── Тренды ────────────────────────────────────────────────────────────────

LOCAL_MACHINE_LABEL = "эта папка"


def run_machine_label(run):
    """Подпись машины прогона в трендах: подпапка общей папки команды или
    «эта папка» для локального отчёта (ключа machine у старых вызовов нет)."""
    return run.get("machine") or LOCAL_MACHINE_LABEL


def trends_model(runs, palette=SERIES_LIGHT, other=SERIES_OTHER):
    """Модель страницы трендов: график на операцию, точки по версиям,
    полоса MAD, границы смены версии. Прогоны с разных машин (разные
    fingerprint_hash) помечаются предупреждением и подписью машины у точки;
    список machines — для фильтра на странице."""
    op_names, seen = [], set()
    for run in runs:
        for name in run["results"]:
            if name not in seen:
                op_names.append(name)
                seen.add(name)
    versions = []
    for run in runs:
        if run["version"] not in versions:
            versions.append(run["version"])
    old = max(0, len(versions) - len(palette))
    color_of = {v: (other if i < old else palette[i - old]) for i, v in enumerate(versions)}
    fp_warning = fingerprint.mismatch_warning(
        [(run.get("fingerprint"), run.get("fingerprint_fields")) for run in runs])
    multi_machine = fp_warning is not None
    machines = []
    for run in runs:
        label = run_machine_label(run)
        if label not in machines:
            machines.append(label)

    charts = []
    for idx, op in enumerate(op_names):
        points = []
        for run in runs:
            r = run["results"].get(op)
            v = comparable_time(r)
            if v is None:
                continue
            mad = r.get("mad")
            points.append({"label": run["ts_disp"], "value": round(v, 3),
                           "mad": round(mad, 3) if mad is not None else None,
                           "version": run["version"], "color": color_of[run["version"]],
                           "n": r.get("n_runs"), "schema": run.get("schema") or 1,
                           "machine": run_machine_label(run),
                           "fingerprint": run.get("fingerprint")})
        if len(points) < 2:
            continue
        values = [p["value"] for p in points]
        first, last = values[0], values[-1]
        change = (last - first) / first * 100 if first else None
        charts.append({
            "id": f"trend{idx}", "op": op, "n_points": len(points),
            "last": fmt_sec(last), "last_version": points[-1]["version"],
            "min": fmt_sec(min(values)), "max": fmt_sec(max(values)),
            "change": f"{change:+.1f} %".replace(".", ",") if change is not None else None,
            "change_tone": ("neutral" if change is None or abs(change) <= 5
                            else "good" if change < 0 else "critical"),
            "has_mad": any(p["mad"] is not None for p in points),
            "points": points,
            "json": json_for_script({
                "labels": [p["label"] for p in points],
                "values": values,
                "madLow": [round(p["value"] - p["mad"], 3) if p["mad"] is not None else None for p in points],
                "madHigh": [round(p["value"] + p["mad"], 3) if p["mad"] is not None else None for p in points],
                "colors": [p["color"] for p in points],
                "versions": [p["version"] for p in points],
                "machines": [p["machine"] for p in points],
                "op": op,
            }),
        })

    return {
        "title": "Тренды производительности",
        "n_runs": len(runs),
        "versions": [{"label": v, "color": color_of[v], "old": i < old} for i, v in enumerate(versions)],
        "charts": charts,
        "schema_warning": schema_warning(run.get("schema") for run in runs),
        "fingerprint_warning": fp_warning,
        "multi_machine": multi_machine,
        "machines": machines,
        "machines_json": json_for_script(machines),
        "period": (f"{runs[0]['ts_disp']} — {runs[-1]['ts_disp']}" if runs else ""),
    }


# ── Сводка Batch ──────────────────────────────────────────────────────────

def batch_model(batch_results):
    """Модель сводки Batch: по версии — открытие, ВПР, пик RAM и CPU."""
    def best_worst(key, lower_is_better=True):
        vals = [(r.get(key), i) for i, r in enumerate(batch_results) if r.get(key) is not None]
        if not vals:
            return -1, -1
        b = min(vals)[1] if lower_is_better else max(vals)[1]
        w = max(vals)[1] if lower_is_better else min(vals)[1]
        return b, w

    marks = {k: best_worst(k) for k in ("open_elapsed", "vlookup_elapsed", "peak_ram", "peak_cpu")}
    rows = []
    for i, r in enumerate(batch_results):
        def cell(key, fmt):
            v = r.get(key)
            b, w = marks[key]
            return {"text": fmt(v), "tone": ("good" if i == b and b != w else
                                             "critical" if i == w and b != w else "neutral")}
        rows.append({
            "version": r.get("version"), "file": r.get("file"),
            "success": bool(r.get("success")), "error": r.get("error"),
            "open": cell("open_elapsed", lambda v: fmt_sec(v, 2)),
            "vlookup": cell("vlookup_elapsed", lambda v: fmt_sec(v, 2)),
            "ram": cell("peak_ram", fmt_mb),
            "cpu": cell("peak_cpu", fmt_pct),
        })
    ok = sum(1 for r in batch_results if r.get("success"))
    best_open = next((r for r in batch_results if batch_results.index(r) == marks["open_elapsed"][0]), None)
    tiles = [
        {"label": "Версий проверено", "value": str(len(batch_results)), "unit": "",
         "sub": f"успешно {ok}, с ошибкой {len(batch_results) - ok}"},
        {"label": "Быстрее всех открывает", "value": fmt_sec(best_open.get("open_elapsed"), 2) if best_open else "—",
         "unit": "с", "sub": best_open.get("version") if best_open else None},
    ]
    chart = {"labels": [r.get("version") for r in batch_results],
             "open": [r.get("open_elapsed") for r in batch_results],
             "vlookup": [r.get("vlookup_elapsed") for r in batch_results],
             "ram": [r.get("peak_ram") for r in batch_results]}
    return {"title": "Сводка Batch", "rows": rows, "tiles": tiles,
            "chart_json": json_for_script(chart), "n": len(batch_results), "ok": ok}


# ── Тест своего файла ─────────────────────────────────────────────────────

def custom_model(result):
    """Модель отчёта по своему файлу: открытие и ВПР."""
    vlookup_rows = result.get("vlookup_rows") or 0
    rows_n = result.get("real_rows") or result.get("rows") or 0
    data_ready = result.get("data_ready")
    warnings = []
    if data_ready is False:
        warnings.append("Данные могли загрузиться не полностью: сработал таймаут ожидания, "
                        "результаты могут быть занижены.")
    if not result.get("cache_cleared"):
        warnings.append("Кэш не очищался перед тестом (psutil недоступен) — открытие могло быть тёплым.")
    tiles = [
        {"label": "Открытие файла", "value": fmt_sec(result.get("open_elapsed")), "unit": "с",
         "sub": ("данные загружены" if data_ready else "таймаут загрузки" if data_ready is False else None)},
        {"label": f"ВПР, {fmt_num(vlookup_rows, 0)} строк",
         "value": fmt_sec(result.get("vlookup_elapsed")) if result.get("vlookup_elapsed") is not None else "—",
         "unit": "с", "sub": result.get("vlookup_error") or None,
         "status": {"tone": "critical", "text": "ошибка"} if result.get("vlookup_error") else None},
        {"label": "Размер файла", "value": fmt_num(result.get("file_size_mb"), 2) if result.get("file_size_mb") else "—",
         "unit": "МБ", "sub": f"{fmt_num(rows_n, 0)} строк × {result.get('cols') or 0} столбцов"},
    ]
    chart = {"labels": ["Открытие файла", f"ВПР ({fmt_num(vlookup_rows, 0)} строк)"],
             "values": [result.get("open_elapsed"), result.get("vlookup_elapsed")]}
    return {"title": "Тест своего файла", "file_name": result.get("filename"),
            "timestamp": result.get("timestamp"), "tiles": tiles, "warnings": warnings,
            "open": fmt_sec(result.get("open_elapsed")), "rows_n": fmt_num(rows_n, 0),
            "vlookup": fmt_sec(result.get("vlookup_elapsed")) if result.get("vlookup_elapsed") is not None else "—",
            "vlookup_rows": fmt_num(vlookup_rows, 0), "vlookup_error": result.get("vlookup_error"),
            "data_ready": data_ready, "chart_json": json_for_script(chart)}
