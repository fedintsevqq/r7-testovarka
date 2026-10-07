"""Трасса при регрессии: диагностический повтор вне замера
(docs/plan-to-20.md, этап 3, п. 8; подробности — docs/cdp-operations.md).

Когда сравнение с эталоном показало регрессию, одной цифры мало: нужно
видеть, на что ушло время. Здесь операция выполняется ещё один раз —
отдельно от замера, с трассой движка (Tracing) и профилем V8 (Profiler) —
и рядом с отчётом ложатся два файла:

    <ts>_<операция>.trace.json   — Chrome Trace Event, открыть в ui.perfetto.dev
                                   или во вкладке Performance DevTools;
    <ts>_<операция>.cpuprofile   — профиль V8, открыть там же.

Плюс разбивка по фазам (скрипты, стили и раскладка, отрисовка, сборка
мусора) и самые тяжёлые функции — в полном JSON прогона, ключ
`diagnostics[<операция>]` (необязательный ключ, схема не поднимается — как
`build`: старые читатели его не видят, новые без него работают).

Почему повтор отдельный, а не «трасса на каждом замере»: трасса и профайлер
сами нагружают рендерер (на тяжёлых операциях — на десятки процентов), а
замер должен отражать Р7, а не инструмент (CLAUDE.md, правило 1). Повтор
идёт тем же _measure_one_run, что и обычный: подготовка, ожидание простоя,
конец по _wait_operation_done, откат истории. Его время в результаты и
медиану не входит — оно пишется только в журнал и в diagnostics.

Чистые функции разбора (profile_breakdown, trace_breakdown, phases,
top_functions) не трогают Р7 и проверяются без него. TraceMixin — методы,
которые R7Testovarka получает наследованием; модуль не импортирует tkinter.
"""
import json
import os
import re
import time
from pathlib import Path

from r7.editors import DEFAULT_EDITOR, EDITOR_LABELS, editor_mode


# Категории трассы: таймлайн DevTools (задачи главного потока, раскладка,
# отрисовка, кадры), выполнение V8 и сборка мусора. Без
# disabled-by-default-v8.cpu_profiler: профиль снимает Profiler отдельно, а
# две копии семплера утяжеляют рендерер вдвое.
TRACE_CATEGORIES = (
    "-*",
    "devtools.timeline",
    "disabled-by-default-devtools.timeline",
    "disabled-by-default-devtools.timeline.frame",
    "toplevel",
    "v8.execute",
    "blink.user_timing",
    "disabled-by-default-v8.gc",
)
TOP_FUNCTIONS_N = 15
MAIN_THREAD_NAME = "CrRendererMain"
DIAGNOSTICS_KEY = "diagnostics"
PROBABLE_REGRESSION = "вероятная регрессия"

# Фазы по именам событий трассы — как группирует их панель Performance DevTools.
SCRIPTING_EVENTS = frozenset((
    "EvaluateScript", "v8.evaluateModule", "FunctionCall", "TimerFire", "EventDispatch",
    "FireAnimationFrame", "FireIdleCallback", "RunMicrotasks", "v8.compile",
    "v8.compileModule", "V8.Execute", "v8.run", "v8.callFunction", "XHRReadyStateChange",
    "XHRLoad", "CompileScript", "CompileCode", "OptimizeCode", "CacheScript",
    "v8.produceCache", "v8.parseOnBackground", "ParseScriptOnBackground",
))
RENDERING_EVENTS = frozenset((
    "Layout", "UpdateLayoutTree", "RecalculateStyles", "ScheduleStyleRecalculation",
    "InvalidateLayout", "UpdateLayerTree", "HitTest", "PrePaint", "Layerize",
    "ScrollLayer", "ParseAuthorStyleSheet", "LayoutShift",
))
PAINTING_EVENTS = frozenset((
    "Paint", "PaintImage", "PaintSetup", "CompositeLayers", "RasterTask", "Rasterize",
    "DecodeImage", "Decode Image", "ResizeImage", "Decode LazyPixelRef",
    "Draw LazyPixelRef", "GPUTask", "Commit", "DrawFrame",
))
GC_EVENTS = frozenset(("MinorGC", "MajorGC", "GCEvent"))
GC_PREFIXES = ("V8.GC", "BlinkGC", "CppGC", "MinorGC", "MajorGC")

# Служебные узлы профиля V8 (callFrame.functionName).
PROFILE_IDLE, PROFILE_PROGRAM, PROFILE_GC, PROFILE_ROOT = (
    "(idle)", "(program)", "(garbage collector)", "(root)")

_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def op_slug(name, limit=48):
    """Имя операции для файла: латиница, цифры и дефисы.
    «Вставка 5 ячеек (ПКМ)» → «vstavka-5-yacheek-pkm»."""
    text = "".join(_TRANSLIT.get(ch, ch) for ch in str(name).lower())
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (slug[:limit].rstrip("-")) or "op"


def trace_file_names(report_ts, op_name):
    """(имя .trace.json, имя .cpuprofile) рядом с отчётом прогона."""
    base = f"{report_ts}_{op_slug(op_name)}"
    return f"{base}.trace.json", f"{base}.cpuprofile"


# ── разбор профиля V8 ─────────────────────────────────────────────────────

def _sample_durations_us(profile):
    """[(id узла, длительность семпла, мкс)]: от семпла до следующего,
    у последнего — до endTime профиля."""
    samples = profile.get("samples") or []
    deltas = profile.get("timeDeltas") or []
    if not samples or len(deltas) != len(samples):
        return []
    stamps, t = [], profile.get("startTime") or 0
    for d in deltas:
        t += d
        stamps.append(t)
    end = profile.get("endTime")
    out = []
    for i, node_id in enumerate(samples):
        nxt = stamps[i + 1] if i + 1 < len(stamps) else (end if end is not None else stamps[i])
        out.append((node_id, max(0, nxt - stamps[i])))
    return out


def _self_time_by_node(profile):
    """{id узла: собственное время, мкс} по семплам профиля."""
    acc = {}
    for node_id, dur in _sample_durations_us(profile):
        acc[node_id] = acc.get(node_id, 0) + dur
    return acc


def _nodes(profile):
    return {n.get("id"): n for n in profile.get("nodes") or [] if isinstance(n, dict)}


def profile_breakdown(profile):
    """Время профиля по видам: скрипты, сборка мусора, простой, служебное.

    Returns:
        dict: scripting_ms, gc_ms, idle_ms, program_ms, total_ms; None —
        профиля нет или он пустой.
    """
    if not isinstance(profile, dict):
        return None
    self_us = _self_time_by_node(profile)
    if not self_us:
        return None
    nodes = _nodes(profile)
    out = {"scripting_ms": 0.0, "gc_ms": 0.0, "idle_ms": 0.0, "program_ms": 0.0}
    for node_id, us in self_us.items():
        fname = ((nodes.get(node_id) or {}).get("callFrame") or {}).get("functionName")
        key = {PROFILE_IDLE: "idle_ms", PROFILE_GC: "gc_ms", PROFILE_PROGRAM: "program_ms",
               PROFILE_ROOT: "program_ms"}.get(fname, "scripting_ms")
        out[key] += us / 1000.0
    out["total_ms"] = sum(out.values())
    return {k: round(v, 1) for k, v in out.items()}


def top_functions(profile, n=TOP_FUNCTIONS_N):
    """Самые тяжёлые функции по собственному времени (без простоя и служебных
    узлов). Доля — от всего непростойного времени профиля.

    Returns:
        list[dict]: function, url (имя файла скрипта), line, self_ms, pct.
    """
    if not isinstance(profile, dict):
        return []
    self_us = _self_time_by_node(profile)
    nodes = _nodes(profile)
    by_fn, busy_us = {}, 0
    for node_id, us in self_us.items():
        frame = (nodes.get(node_id) or {}).get("callFrame") or {}
        fname = frame.get("functionName") or ""
        if fname == PROFILE_IDLE:
            continue
        busy_us += us
        if fname in (PROFILE_PROGRAM, PROFILE_ROOT, PROFILE_GC):
            continue
        url = frame.get("url") or ""
        line = frame.get("lineNumber")
        key = (fname or "(анонимная)", url.rsplit("/", 1)[-1],
               None if line is None or line < 0 else line + 1)
        by_fn[key] = by_fn.get(key, 0) + us
    rows = sorted(by_fn.items(), key=lambda kv: -kv[1])[:n]
    return [{"function": f, "url": u, "line": ln, "self_ms": round(us / 1000.0, 1),
             "pct": round(us / busy_us * 100.0, 1) if busy_us else None}
            for (f, u, ln), us in rows]


# ── разбор трассы ─────────────────────────────────────────────────────────

def event_phase(name):
    """Фаза события трассы: scripting, rendering, painting, gc или other."""
    if name in SCRIPTING_EVENTS:
        return "scripting"
    if name in RENDERING_EVENTS:
        return "rendering"
    if name in PAINTING_EVENTS:
        return "painting"
    if name in GC_EVENTS or name.startswith(GC_PREFIXES):
        return "gc"
    return "other"


def _main_threads(events):
    """{(pid, tid)} главных потоков рендерера по метаданным thread_name."""
    return {(e.get("pid"), e.get("tid")) for e in events
            if e.get("ph") == "M" and e.get("name") == "thread_name"
            and (e.get("args") or {}).get("name") == MAIN_THREAD_NAME}


def _complete_events(events, threads):
    """События с длительностью по потокам: X как есть, пары B/E — склеены.
    Returns: {(pid, tid): [(ts, dur, name)]} в мкс."""
    out, open_b = {}, {}
    for e in events:
        key = (e.get("pid"), e.get("tid"))
        if threads and key not in threads:
            continue
        ph, ts = e.get("ph"), e.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        if ph == "X" and isinstance(e.get("dur"), (int, float)):
            out.setdefault(key, []).append((ts, e["dur"], str(e.get("name") or "")))
        elif ph == "B":
            open_b.setdefault(key, []).append((ts, str(e.get("name") or "")))
        elif ph == "E" and open_b.get(key):
            start, name = open_b[key].pop()
            out.setdefault(key, []).append((start, max(0, ts - start), name))
    return out


def _self_times(spans):
    """[(собственное время, имя)] для вложенных событий одного потока:
    время ребёнка вычитается из родителя (как Bottom-Up в DevTools)."""
    spans = sorted(spans, key=lambda s: (s[0], -s[1]))
    child_us = [0.0] * len(spans)
    stack = []              # (конец, индекс)
    for i, (ts, dur, _name) in enumerate(spans):
        while stack and stack[-1][0] <= ts:
            stack.pop()
        if stack:
            parent = stack[-1][1]
            child_us[parent] += min(dur, stack[-1][0] - ts)
        stack.append((ts + dur, i))
    return [(max(0.0, dur - child_us[i]), name) for i, (_ts, dur, name) in enumerate(spans)]


def trace_breakdown(events):
    """Собственное время главного потока рендерера по фазам.

    Главный поток — CrRendererMain из метаданных; если их нет, считаются
    все потоки (и это помечено в main_thread_found).

    Returns:
        dict: scripting_ms, rendering_ms, painting_ms, gc_ms, other_ms,
        span_ms (от первого до последнего события), main_thread_found;
        None — событий нет.
    """
    if not isinstance(events, list) or not events:
        return None
    events = [e for e in events if isinstance(e, dict)]
    threads = _main_threads(events)
    out = {"scripting_ms": 0.0, "rendering_ms": 0.0, "painting_ms": 0.0, "gc_ms": 0.0,
           "other_ms": 0.0}
    for spans in _complete_events(events, threads).values():
        for self_us, name in _self_times(spans):
            out[f"{event_phase(name)}_ms"] += self_us / 1000.0
    stamps = [(e["ts"], e["ts"] + (e.get("dur") or 0)) for e in events
              if e.get("ph") != "M" and isinstance(e.get("ts"), (int, float))
              and isinstance(e.get("dur") or 0, (int, float))]
    span = (max(b for _a, b in stamps) - min(a for a, _b in stamps)) / 1000.0 if stamps else 0.0
    res = {k: round(v, 1) for k, v in out.items()}
    res["span_ms"] = round(span, 1)
    res["main_thread_found"] = bool(threads)
    return res


def phases(events, profile):
    """Разбивка по фазам из трассы и профиля.

    Скрипты — из профиля (собственное время функций V8 точнее, чем события
    таймлайна), раскладка и отрисовка — из трассы, сборка мусора — из
    трассы, если там есть GC-события, иначе из профиля.

    Returns:
        tuple[dict, dict]: (фазы в мс, источник каждой фазы).
    """
    tr, pr = trace_breakdown(events), profile_breakdown(profile)
    out, src = {}, {}

    def put(key, value, source):
        if value is not None:
            out[key], src[key] = value, source

    put("scripting_ms", (pr or {}).get("scripting_ms"), "profile")
    if "scripting_ms" not in out:
        put("scripting_ms", (tr or {}).get("scripting_ms"), "trace")
    put("rendering_ms", (tr or {}).get("rendering_ms"), "trace")
    put("painting_ms", (tr or {}).get("painting_ms"), "trace")
    if tr and tr.get("gc_ms"):
        put("gc_ms", tr["gc_ms"], "trace")
    else:
        put("gc_ms", (pr or {}).get("gc_ms"), "profile")
    put("other_ms", (tr or {}).get("other_ms"), "trace")
    put("idle_ms", (pr or {}).get("idle_ms"), "profile")
    if tr:
        put("trace_scripting_ms", tr.get("scripting_ms"), "trace")
        put("span_ms", tr.get("span_ms"), "trace")
    elif pr:
        put("span_ms", pr.get("total_ms"), "profile")
    return out, src


PHASE_LABELS = (("scripting_ms", "скрипты"), ("rendering_ms", "стили и раскладка"),
                ("painting_ms", "отрисовка"), ("gc_ms", "сборка мусора"))


def diagnostics_summary(diag):
    """Строка для страницы готовности и консоли: фазы и самая тяжёлая функция."""
    if not diag:
        return ""
    if diag.get("error"):
        return f"трасса не снята: {diag['error']}"
    ph = diag.get("phases") or {}
    parts = [f"{label} {ph[key]:.0f} мс" for key, label in PHASE_LABELS
             if isinstance(ph.get(key), (int, float))]
    top = (diag.get("top_functions") or [None])[0]
    if top:
        pct = f" ({top['pct']:.0f} %)" if top.get("pct") is not None else ""
        parts.append(f"тяжелее всего {top['function']}{pct}")
    return "; ".join(parts)


# ── запись файлов и отчёта ────────────────────────────────────────────────

def _write_json(path, data):
    """Атомарная запись JSON: временный файл и os.replace."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def write_trace_files(out_dir, report_ts, op_name, events, profile, meta=None):
    """Пишет .trace.json и .cpuprofile. Returns: (имя трассы | None, имя профиля | None)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_name, profile_name = trace_file_names(report_ts, op_name)
    written_trace = written_profile = None
    if events:
        _write_json(out_dir / trace_name, {"traceEvents": events,
                                           "metadata": dict(meta or {}, op=op_name)})
        written_trace = trace_name
    if profile:
        _write_json(out_dir / profile_name, profile)
        written_profile = profile_name
    return written_trace, written_profile


def attach_to_report(report_path, diagnostics):
    """Добавляет diagnostics[<операция>] в полный JSON прогона (поверх
    прежних записей тех же операций). Returns: True — записано."""
    report_path = Path(report_path)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return False
    merged = dict(data.get(DIAGNOSTICS_KEY) or {})
    merged.update(diagnostics)
    data[DIAGNOSTICS_KEY] = merged
    _write_json(report_path, data)
    return True


def diagnostic_files(report_path, data):
    """Файлы трасс и профилей из diagnostics отчёта, которые есть на диске
    рядом с ним (для пакета улик)."""
    folder = Path(report_path).parent
    out = []
    for diag in ((data or {}).get(DIAGNOSTICS_KEY) or {}).values():
        if not isinstance(diag, dict):
            continue
        for key in ("trace_file", "profile_file"):
            name = diag.get(key)
            if name and (folder / Path(name).name).is_file():
                out.append(folder / Path(name).name)
    return out


def regressed_ops(model):
    """Операции страницы готовности (r7.gate.gate_model) с вердиктом
    «регрессия» или решением «вероятная регрессия» — кандидаты на трассу."""
    return [r["name"] for r in model.get("rows") or []
            if r.get("verdict") == "regression"
            or (r.get("compare") or {}).get("decision") == PROBABLE_REGRESSION]


# ── повтор на живом Р7 ────────────────────────────────────────────────────

class TraceMixin:
    """Диагностический повтор с трассой — часть R7Testovarka (через наследование)."""

    TRACE_PROFILE_INTERVAL_US = 200      # шаг семплера V8, мкс (проверено живым Р7)
    TRACE_STOP_TIMEOUT_SEC = 120.0       # ждать tracingComplete после Tracing.end

    def capture_diagnostic_trace(self, op_name, fn, find_hwnd, out_dir, report_ts,
                                 log_cb=None, stop_event=None):
        """Один повтор операции с трассой и профилем — вне замера.

        Повтор идёт обычным _measure_one_run на отдельном _RunAcc: подготовка,
        ожидание простоя, конец по _wait_operation_done, откат истории — как у
        повтора замера. Трасса и профайлер включаются в конце подготовки
        (до секундомера), выключаются в паузе после повтора (после конца
        замера, до отката). Время повтора в результаты прогона не попадает:
        _RunAcc здесь свой и выбрасывается.

        Args:
            op_name: имя теста.
            fn: функция теста с .prepare (r7_ops.SpreadsheetOps.tests()).
            find_hwnd: поиск окна Р7.
            out_dir: папка отчёта — туда ложатся .trace.json и .cpuprofile.
            report_ts: метка отчёта (performance_full_<ts>.json).

        Returns:
            dict | None: запись diagnostics[op_name]; None — CDP нет.
        """
        from r7.measure import _RunAcc   # лениво: чистые функции модуля нужны r7.gate без Р7
        log_cb = log_cb or self.add_test_log
        connector = self._cdp_ops_connector()
        if connector is None:
            log_cb(f"⚠️ {op_name}: трасса не снята — нет CDP-соединения с Р7")
            return None
        log_cb(f"🔬 {op_name}: диагностический повтор с трассой (вне замера, в медиану "
               f"не входит)")
        state = {"trace": None, "profile": None, "events": None, "profile_data": None,
                 "stopped": False}
        orig_prepare = getattr(fn, "prepare", None)

        def prepare():
            if orig_prepare is not None:
                orig_prepare()

        def stop():
            if state["stopped"]:
                return
            state["stopped"] = True
            if state["profile"]:
                state["profile_data"] = connector.profile_stop()
            if state["trace"]:
                state["events"] = connector.trace_stop(self.TRACE_STOP_TIMEOUT_SEC)

        def diag_op():
            # Трасса и профайлер — прямо перед действием, после ожидания
            # простоя: с ними рендерер не простаивает (сэмплы каждые 200 мкс),
            # и ожидание перед повтором выбирало весь таймаут 180 с (живой
            # прогон 07.10.2026). Время этого повтора всё равно выбрасывается.
            state["trace"] = connector.trace_start(TRACE_CATEGORIES)
            state["profile"] = connector.profile_start(self.TRACE_PROFILE_INTERVAL_US)
            return fn()

        diag_op.prepare = prepare
        acc = _RunAcc()
        try:
            # post_delay — пауза после повтора: замер уже закрыт, откат ещё впереди.
            self._measure_one_run(acc, 0, 1, op_name, diag_op, find_hwnd, log_cb,
                                  stop_event, stop)
        finally:
            stop()
            cleanup = getattr(fn, "cleanup", None)
            if cleanup is not None:
                try:
                    cleanup()
                except Exception as e:
                    log_cb(f"   ⚠️ {op_name}: уборка после трассы не удалась ({e})")
        return self._diagnostic_record(op_name, acc, state, out_dir, report_ts, log_cb)

    def _diagnostic_record(self, op_name, acc, state, out_dir, report_ts, log_cb):
        """Файлы и запись diagnostics по итогам повтора с трассой."""
        events, profile = state["events"], state["profile_data"]
        diag_sec = acc.pass_times[0] if acc.pass_times else None
        rec = {"trace_file": None, "profile_file": None, "phases": {}, "phase_sources": {},
               "top_functions": [], "diagnostic_sec": diag_sec,
               "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "note": "отдельный повтор вне замера, в медиану не входит"}
        if acc.error:
            rec["op_error"] = acc.error
        if not events and not profile:
            rec["error"] = ("CDP не отдал ни трассу, ни профиль" if state["trace"]
                            or state["profile"] else "трасса и профайлер не запустились")
            log_cb(f"   ⚠️ {op_name}: {rec['error']}")
            return rec
        try:
            rec["trace_file"], rec["profile_file"] = write_trace_files(
                out_dir, report_ts, op_name, events, profile,
                meta={"source": "R7-Testovarka", "captured_at": rec["captured_at"]})
        except OSError as e:
            rec["error"] = f"файлы трассы не записаны: {e}"
            log_cb(f"   ⚠️ {op_name}: {rec['error']}")
            return rec
        rec["phases"], rec["phase_sources"] = phases(events, profile)
        rec["top_functions"] = top_functions(profile)
        took = f"{diag_sec:.3f} с, " if diag_sec is not None else ""
        log_cb(f"   🔬 {op_name}: {took}{diagnostics_summary(rec)}; файлы: "
               f"{rec['trace_file'] or '—'}, {rec['profile_file'] or '—'}")
        return rec

    def trace_ops_session(self, op_names, report_ts, out_dir, stop_event=None, editor=None):
        """Р7 на фикстуре редактора → диагностический повтор каждой операции → закрытие.

        Открытие и закрытие — те же, что у вкладки «Сценарии»
        (_scenario_open_r7/_scenario_close_r7): запуск с CDP, ожидание
        готовности, фокус, автосохранение выключено на сессию. Режим
        редактора (r7.editors.editor_mode) стоит на всю сессию: фикстура,
        готовность, операции (_make_run_ops — SpreadsheetOps, DocumentOps,
        PresentationOps), снимок и откат истории — того редактора, чьи
        операции трассируются.

        Args:
            editor: "spreadsheet" | "document" | "presentation"; None — таблицы.

        Returns:
            dict: {имя операции: запись diagnostics}; пустой — Р7 не открылся.
        """
        with editor_mode(self, editor or DEFAULT_EDITOR):
            return self._trace_ops_in_editor(op_names, report_ts, out_dir, stop_event)

    def _trace_ops_in_editor(self, op_names, report_ts, out_dir, stop_event):
        """trace_ops_session в уже выставленном режиме редактора."""
        log_cb = self.add_test_log
        editor = getattr(self, "_run_editor", DEFAULT_EDITOR)
        test_file = self._locate_test_file()
        if not test_file:
            log_cb("❌ Трасса: тестовый файл не найден")
            return {}
        session = self._scenario_open_r7(test_file)
        if session is None:
            log_cb("❌ Трасса: Р7 не открыл файл")
            return {}
        out = {}
        open_name = getattr(self, "OPEN_TEST_NAME", "Повторное открытие файла")
        try:
            ops = dict(self._make_run_ops(session.find_hwnd, log_cb, test_file).tests())
            for name in op_names:
                if stop_event is not None and stop_event.is_set():
                    break
                fn = ops.get(name)
                if fn is None:
                    log_cb(f"ℹ️ {name}: трасса для открытия файла не снимается — это не "
                           f"операция над открытым документом" if name == open_name else
                           f"ℹ️ {name}: такой операции нет у редактора "
                           f"«{EDITOR_LABELS.get(editor, editor)}» — трасса не снята")
                    continue
                rec = self.capture_diagnostic_trace(name, fn, session.find_hwnd, out_dir,
                                                    report_ts, log_cb, stop_event)
                if rec is not None:
                    out[name] = rec
        finally:
            self._scenario_close_r7(session)
        return out
