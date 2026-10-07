"""Пакет улик для бага: zip из двух отчётов, страницы сравнения, окружения,
хвоста журнала и готового текста тикета (docs/plan-to-20.md, этап 2, п. 4).

Раньше материалы для разработчика собирались вручную, как для
`Reports/bugreport_x2t_ods/`: JSON двух прогонов, скриншот, описание шагов,
цифры из HTML. Здесь то же самое делает одна функция build_evidence_pack:
на входе два performance_full_*.json (база и проверяемая сборка), на выходе
`evidence_<время>.zip` с ticket.md — текстом тикета, который можно вставить в
трекер как есть.

Модуль не импортирует tkinter: страницу сравнения рендерит переданный
render_html (в приложении — R7Testovarka._generate_comparison_html), без него
— r7_reports напрямую. Статистика — та же compare_runs, что на странице
сравнения, поэтому вердикты в тикете и на странице совпадают.
"""
import json
import zipfile
from datetime import datetime
from pathlib import Path

import r7_reports
from r7 import config, logfile, trace
from r7.compare_files import fmt_report_ts
from r7.stats import COMPARISON_MIN_EFFECT_PCT, MIN_RUNS_FOR_COMPARISON, compare_runs

LOG_TAIL_LINES = 2000
EVIDENCE_DIR_NAME = "evidence"          # Reports/evidence/
TICKET_NAME = "ticket.md"
COMPARISON_NAME = "comparison.html"
ENVIRONMENT_NAME = "environment.json"
LOG_TAIL_NAME = "r7-testovarka.tail.log"
REGRESSION = "РЕГРЕССИЯ"
# Имена записи открытия в results (r7/perf.py, r7/runs.py) и имя теста.
OPEN_OP_NAMES = ("Открытие файла", "Повторное открытие файла")


# ── чтение отчётов ────────────────────────────────────────────────────────

def load_report(path):
    """Содержимое performance_full_*.json; не словарь — ValueError."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{Path(path).name}: не отчёт performance_full (верхний уровень не объект)")
    return data


def report_label(data, fallback=""):
    """Подпись сборки: версия и номер сборки, если в отчёте есть ключ
    `build` (этап 2, п. 1); иначе строка версии как есть."""
    version = str(data.get("version") or fallback or "?")
    build = data.get("build")
    if build and str(build) not in version:
        return f"{version} (сборка {build})"
    return version


def _fingerprint(data):
    """Отпечаток стенда (этап 2, п. 2) — где бы его ни положили."""
    system = data.get("system") or {}
    env_info = system.get("environment") or {}
    for holder in (data, system, env_info):
        if isinstance(holder, dict) and holder.get("fingerprint"):
            return str(holder["fingerprint"])
    return None


def stand_summary(data):
    """Стенд из блока system отчёта: CPU, RAM, ОС, план питания, масштаб,
    отпечаток. Старые отчёты без части ключей дают None в соответствующем поле."""
    system = data.get("system") or {}
    env_info = system.get("environment") or {}
    if not isinstance(env_info, dict):
        env_info = {}
    return {
        "cpu": system.get("cpu_model"),
        "cores": system.get("cpu_cores_logical"),
        "ram_gb": system.get("ram_total_gb"),
        "os": system.get("os"),
        "power_plan": env_info.get("power_plan"),
        "dpi_pct": system.get("dpi_scale_pct"),
        "fingerprint": _fingerprint(data),
        "warnings": [str(w) for w in (env_info.get("warnings") or [])],
    }


# ── сравнение операций ────────────────────────────────────────────────────

def _results_by_name(data):
    return {r["name"]: r for r in data.get("results") or []
            if isinstance(r, dict) and r.get("name")}


def op_comparisons(base_data, cur_data):
    """По каждой операции, которая есть в обоих отчётах: медианы, MAD, число
    повторов, Δ %, p и вердикт compare_runs. Порядок — как в базовом отчёте.

    Старый JSON без `runs` даёт вердикт «недостаточно прогонов» — compare_runs
    требует MIN_RUNS_FOR_COMPARISON повторов на сторону.
    """
    base_ops, cur_ops = _results_by_name(base_data), _results_by_name(cur_data)
    out = []
    for name, base_r in base_ops.items():
        cur_r = cur_ops.get(name)
        if cur_r is None:
            continue
        base_t = r7_reports.comparable_time(base_r)
        cur_t = r7_reports.comparable_time(cur_r)
        b_runs, c_runs = r7_reports.valid_runs(base_r), r7_reports.valid_runs(cur_r)
        row = {"op": name, "base_median": base_t, "cur_median": cur_t,
               "base_mad": base_r.get("mad"), "cur_mad": cur_r.get("mad"),
               "n_base": len(b_runs), "n_cur": len(c_runs),
               "delta_pct": None, "p_value": None, "verdict": "нет данных"}
        if base_t and cur_t is not None:
            row["delta_pct"] = round((cur_t - base_t) / base_t * 100.0, 1)
        if base_t is None or cur_t is None:
            row["verdict"] = "нет данных"
        elif base_r.get("runs_independent") is False or cur_r.get("runs_independent") is False:
            row["verdict"] = "зависимые повторы"
        else:
            res = compare_runs(b_runs, c_runs)
            row["verdict"] = res["verdict"]
            row["p_value"] = res.get("p_value")
            if res.get("effect_pct") is not None:
                row["delta_pct"] = res["effect_pct"]
        out.append(row)
    return out


def regressions_of(comparisons):
    """Регрессии, самая сильная первой."""
    return sorted((c for c in comparisons if c["verdict"] == REGRESSION),
                  key=lambda c: -(c["delta_pct"] or 0))


# ── текст тикета ──────────────────────────────────────────────────────────

def _pct(value):
    return "—" if value is None else f"{value:+.0f} %"


def _sec(value, mad=None):
    if value is None:
        return "—"
    text = r7_reports.fmt_sec(value)
    if mad is not None:
        text += f" (MAD {r7_reports.fmt_sec(mad)})"
    return text


def _p(value):
    return "—" if value is None else r7_reports.fmt_num(value, 4)


def ticket_model(base_data, cur_data, comparisons, attachments, base_label=None,
                 cur_label=None):
    """Данные тикета без текста: заголовок, вводная, сборки, стенд, шаги,
    цифры, условия, вложения. Чистая функция над словарями — проверяется
    без диска."""
    base_label = base_label or report_label(base_data, "база")
    cur_label = cur_label or report_label(cur_data, "проверяемая")
    regressions = regressions_of(comparisons)
    if regressions:
        ops = ", ".join(c["op"] for c in regressions)
        effects = ", ".join(_pct(c["delta_pct"]) for c in regressions)
        title = f"Регрессия {ops}: {base_label} → {cur_label} ({effects})"
        lead = []
        for c in regressions:
            lead.append(f"«{c['op']}» на {cur_label} медленнее, чем на {base_label}: "
                        f"медиана {_sec(c['cur_median'])} с против {_sec(c['base_median'])} с "
                        f"({_pct(c['delta_pct'])}), p={_p(c['p_value'])}, "
                        f"повторов {c['n_base']}/{c['n_cur']}.")
        lead = " ".join(lead)
    else:
        title = f"Сравнение {base_label} → {cur_label}: регрессий нет"
        lead = (f"Статистически значимых замедлений между сборками не найдено "
                f"(критерий Манна-Уитни, порог эффекта {COMPARISON_MIN_EFFECT_PCT:.0f} %, "
                f"минимум {MIN_RUNS_FOR_COMPARISON} повторов на сторону).")

    base_stand, cur_stand = stand_summary(base_data), stand_summary(cur_data)
    stand_notes = []
    if base_stand["fingerprint"] and cur_stand["fingerprint"] \
            and base_stand["fingerprint"] != cur_stand["fingerprint"]:
        stand_notes.append("Отпечатки стендов разные — прогоны сняты на разных машинах, "
                           "цифры сравнимы только с оговоркой.")
    for key, label in (("cpu", "CPU"), ("os", "ОС"), ("dpi_pct", "масштаб экрана")):
        if base_stand[key] and cur_stand[key] and base_stand[key] != cur_stand[key]:
            stand_notes.append(f"У базы и проверяемой сборки разный {label}: "
                               f"{base_stand[key]} против {cur_stand[key]}.")

    schema_note = r7_reports.schema_warning(
        d.get("measure_schema") for d in (base_data, cur_data))

    steps_ops = regressions or comparisons[:1]
    steps = [f"Открыть в Р7-Офис {cur_label} файл `{Path(str(cur_data.get('test_file') or '?')).name}` "
             f"и дождаться, пока документ загрузится."]
    for c in steps_ops:
        n = max(c["n_cur"], 1)
        if c["op"] in OPEN_OP_NAMES:
            # Открытие — независимые холодные старты, откатывать нечего.
            steps.append(f"Закрыть Р7-Офис и открыть файл заново, повторов: {n}; каждый "
                         f"повтор — новый запуск Р7. Замер — инструментом R7-Testovarka: "
                         f"от запуска до готовности документа.")
            continue
        steps.append(f"Выполнить операцию «{c['op']}», повторов подряд: {n}; "
                     f"правку откатывать после каждого повтора. Замер — инструментом "
                     f"R7-Testovarka: конец операции по ответу редактора, свои паузы вычтены.")
    steps.append(f"Повторить то же на {base_label} и сравнить медианы.")

    return {
        "title": title, "lead": lead,
        "builds": [_build_line("База", base_data, base_label),
                   _build_line("Проверяемая", cur_data, cur_label)],
        "stand": {"base": base_stand, "cur": cur_stand, "notes": stand_notes},
        "steps": steps,
        "rows": comparisons,
        "conditions": {"base": base_stand["warnings"], "cur": cur_stand["warnings"],
                       "schema": schema_note},
        "attachments": list(attachments),
        "base_label": base_label, "cur_label": cur_label,
    }


def _build_line(role, data, label):
    parts = [f"{role}: {label}"]
    ts = fmt_report_ts(str(data.get("timestamp") or ""))
    if ts:
        parts.append(f"прогон {ts}")
    if data.get("tool_version"):
        parts.append(f"R7-Testovarka {data['tool_version']}")
    parts.append(f"схема замера {data.get('measure_schema') or 1}")
    return ", ".join(parts)


def _stand_lines(stand):
    cores = f", {stand['cores']} лог. ядер" if stand.get("cores") else ""
    ram = f"{stand['ram_gb']} ГБ" if stand.get("ram_gb") is not None else "—"
    return [f"CPU: {stand.get('cpu') or '—'}{cores}",
            f"RAM: {ram}",
            f"ОС: {stand.get('os') or '—'}",
            f"План питания: {stand.get('power_plan') or '—'}",
            f"Масштаб экрана: {str(stand['dpi_pct']) + ' %' if stand.get('dpi_pct') else '—'}",
            f"Отпечаток стенда: {stand.get('fingerprint') or '—'}"]


def render_ticket(model):
    """Markdown тикета из ticket_model."""
    out = [f"# {model['title']}", "", model["lead"], "", "## Сборки", ""]
    out += [f"- {line}" for line in model["builds"]]
    out += ["", "## Стенд", ""]
    stand = model["stand"]
    same = {k: v for k, v in stand["base"].items() if k != "warnings"} == \
        {k: v for k, v in stand["cur"].items() if k != "warnings"}
    if same:
        out += [f"- {line}" for line in _stand_lines(stand["cur"])]
    else:
        out.append(f"База ({model['base_label']}):")
        out += [f"- {line}" for line in _stand_lines(stand["base"])]
        out.append("")
        out.append(f"Проверяемая ({model['cur_label']}):")
        out += [f"- {line}" for line in _stand_lines(stand["cur"])]
    for note in stand["notes"]:
        out += ["", f"> {note}"]
    out += ["", "## Шаги", ""]
    out += [f"{i}. {step}" for i, step in enumerate(model["steps"], 1)]
    out += ["", "## Цифры", "",
            f"| Операция | {model['base_label']}, с | {model['cur_label']}, с | Повторов | Δ | p | Вердикт |",
            "|---|---|---|---|---|---|---|"]
    for c in model["rows"]:
        op = c["op"].replace("|", "¦")
        verdict = f"**{c['verdict']}**" if c["verdict"] == REGRESSION else c["verdict"]
        out.append(f"| {op} | {_sec(c['base_median'], c['base_mad'])} | "
                   f"{_sec(c['cur_median'], c['cur_mad'])} | {c['n_base']}/{c['n_cur']} | "
                   f"{_pct(c['delta_pct'])} | {_p(c['p_value'])} | {verdict} |")
    if not model["rows"]:
        out.append("| — | — | — | — | — | — | общих операций в отчётах нет |")
    out += ["", f"Медиана по годным повторам (без прогрева, таймаутов и неподтверждённых), "
                f"MAD — разброс вокруг неё. Вердикт — критерий Манна-Уитни при p < 0,05 и "
                f"эффекте больше {COMPARISON_MIN_EFFECT_PCT:.0f} %; нужно не меньше "
                f"{MIN_RUNS_FOR_COMPARISON} повторов на сторону.",
            "", "## Условия прогона", ""]
    cond = model["conditions"]
    for role, warnings in (("База", cond["base"]), ("Проверяемая", cond["cur"])):
        if warnings:
            out.append(f"- {role}: " + "; ".join(warnings))
        else:
            out.append(f"- {role}: предупреждений не было")
    if cond["schema"]:
        out.append(f"- {cond['schema']}")
    out += ["", "## Вложения", ""]
    # Пометка «журнал не найден» — не имя файла, без обратных кавычек.
    out += [f"- {name}" if name.startswith("(") else f"- `{name}`"
            for name in model["attachments"]]
    out.append("")
    return "\n".join(out)


# ── сборка пакета ─────────────────────────────────────────────────────────

def default_render_html(datasets, base_path_str):
    """Страница сравнения без приложения — тот же шаблон и та же статистика,
    что у R7Testovarka._generate_comparison_html."""
    model = r7_reports.comparison_model(datasets, base_path_str, compare_runs,
                                        MIN_RUNS_FOR_COMPARISON)
    return r7_reports.render("comparison.html", **model)


def log_tail(path, lines=LOG_TAIL_LINES):
    """Последние lines строк журнала или None, если файла нет или он не читается."""
    if not path:
        return None
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rows = text.splitlines()
    return "\n".join(rows[-lines:]) + "\n"


def default_log_file():
    """Журнал программы: открытый setup_logging, иначе штатный путь."""
    return logfile.get_log_path() or (config.BASE_DIR / logfile.LOG_DIR / logfile.LOG_FILE_NAME)


def _member_names(base_json, cur_json):
    """Имена JSON внутри архива: одинаковые basename получают префиксы."""
    base_name, cur_name = Path(base_json).name, Path(cur_json).name
    if base_name == cur_name:
        return "base_" + base_name, "cur_" + cur_name
    return base_name, cur_name


def build_evidence_pack(base_json, cur_json, out_dir, log_file=None, extra_files=(),
                        render_html=None, labels=None):
    """Собирает evidence_<время>.zip в out_dir.

    Args:
        base_json, cur_json: performance_full_*.json базы и проверяемой сборки.
        out_dir: папка архива (создаётся).
        log_file: журнал программы; None — штатный Reports/logs/r7-testovarka.log.
            Нет файла — хвост в архив не входит, тикет это отмечает.
        extra_files: дополнительные файлы (скриншоты, дампы) — по базовому имени.
        render_html: callable(datasets, base_path_str) -> HTML страницы сравнения;
            None — default_render_html.
        labels: (подпись базы, подпись проверяемой) — имена из окна сравнения;
            None — версия из отчёта.

    Returns:
        Path: путь к архиву.
    """
    base_json, cur_json = Path(base_json), Path(cur_json)
    base_data, cur_data = load_report(base_json), load_report(cur_json)
    base_label, cur_label = labels or (None, None)
    base_label = base_label or report_label(base_data, base_json.stem)
    cur_label = cur_label or report_label(cur_data, cur_json.stem)
    render = render_html or default_render_html

    datasets = [{"path": str(base_json), "version": base_label, "data": base_data},
                {"path": str(cur_json), "version": cur_label, "data": cur_data}]
    html = render(datasets, str(base_json))
    comparisons = op_comparisons(base_data, cur_data)
    environment = {
        "base": _environment_block(base_json, base_data, base_label),
        "current": _environment_block(cur_json, cur_data, cur_label),
    }
    tail = log_tail(log_file if log_file is not None else default_log_file())

    base_name, cur_name = _member_names(base_json, cur_json)
    extras = [Path(p) for p in extra_files if Path(p).is_file()]
    # Трассы и профили диагностического повтора (r7/trace.py), если они
    # сняты: по ним разработчик видит, на что ушло время, без своего стенда.
    seen = {p.name for p in extras} | {base_name, cur_name}
    for path, data in ((cur_json, cur_data), (base_json, base_data)):
        for f in trace.diagnostic_files(path, data):
            if f.name not in seen:
                seen.add(f.name)
                extras.append(f)
    attachments = [base_name, cur_name, COMPARISON_NAME, ENVIRONMENT_NAME]
    if tail is not None:
        attachments.append(LOG_TAIL_NAME)
    else:
        attachments.append("(журнал программы не найден — хвост не приложен)")
    attachments += [p.name for p in extras]
    attachments.append(TICKET_NAME)

    model = ticket_model(base_data, cur_data, comparisons, attachments,
                         base_label=base_label, cur_label=cur_label)
    ticket = render_ticket(model)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = out_dir / f"evidence_{ts}.zip"
    n = 1
    while zip_path.exists():                   # два пакета в одну секунду
        n += 1
        zip_path = out_dir / f"evidence_{ts}_{n}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # writestr, а не write: у файла из старой резервной копии mtime может
        # быть раньше 1980 года, и zipfile отказывается его класть.
        zf.writestr(base_name, base_json.read_bytes())
        zf.writestr(cur_name, cur_json.read_bytes())
        zf.writestr(COMPARISON_NAME, html)
        zf.writestr(ENVIRONMENT_NAME, json.dumps(environment, ensure_ascii=False, indent=2))
        if tail is not None:
            zf.writestr(LOG_TAIL_NAME, tail)
        for p in extras:
            zf.writestr(p.name, p.read_bytes())
        zf.writestr(TICKET_NAME, ticket)
    # Текст тикета — ещё и рядом с архивом: открыть и вставить в трекер
    # быстрее, чем распаковывать.
    try:
        zip_path.with_name(zip_path.stem + "_ticket.md").write_text(ticket, encoding="utf-8")
    except OSError:
        pass                                    # архив уже на месте, тикет внутри него
    return zip_path


def _environment_block(path, data, label):
    return {"file": path.name, "label": label, "version": data.get("version"),
            "build": data.get("build"), "timestamp": data.get("timestamp"),
            "tool_version": data.get("tool_version"),
            "measure_schema": data.get("measure_schema") or 1,
            "test_file": data.get("test_file"),
            "system": data.get("system"), "summary": data.get("summary")}
