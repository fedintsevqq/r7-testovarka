"""Трасса при регрессии (r7/trace.py, docs/plan-to-20.md, этап 3, п. 8).

Без Р7: websocket коннектора — дублёр с заранее заданными ответами и
событиями CDP, повтор — дублёр _measure_one_run с тем же порядком шагов,
что у настоящего (порядок настоящего сторожит отдельный тест по исходнику).
"""
import base64
import gzip
import inspect
import json
import socket
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import r7_webdriver_connector as wdmod
from r7 import evidence, gate, measure, trace
from r7.suites import Suite

# ── дублёр websocket ─────────────────────────────────────────────────────


class FakeWs:
    """Сокет CDP: на каждую команду — ответ из handlers (и события до или
    после него). recv без сообщений — таймаут, как у настоящего сокета."""

    def __init__(self, handlers=None):
        self.handlers = handlers or {}
        self.sent = []
        self.queue = []
        self.timeout = 2.0

    def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        h = self.handlers.get(msg["method"], {})
        if callable(h):
            h = h(msg)
        if h is None:                       # нет ответа — вызов уйдёт в таймаут
            return
        before, result, after = h.get("before", []), h.get("result", {}), h.get("after", [])
        self.queue.extend(before)
        if result is not None:
            self.queue.append({"id": msg["id"], "result": result})
        self.queue.extend(after)

    def recv(self):
        if not self.queue:
            raise socket.timeout("timed out")
        return json.dumps(self.queue.pop(0))

    def gettimeout(self):
        return self.timeout

    def settimeout(self, t):
        self.timeout = t

    def close(self):
        pass

    def methods(self):
        return [m["method"] for m in self.sent]


def _conn(handlers=None):
    c = wdmod.R7WebDriverConnector(log_cb=lambda m: None)
    c._backend = "cdp"
    c._ws = FakeWs(handlers)
    return c


def _event(method, **params):
    return {"method": method, "params": params}


EVENTS = [{"ph": "X", "name": "FunctionCall", "ts": 0, "dur": 10, "pid": 1, "tid": 1}]
PAYLOAD = json.dumps({"traceEvents": EVENTS}).encode("utf-8")


def _io_reader(chunks):
    """Обработчик IO.read: отдаёт куски по очереди, у последнего — eof."""
    left = list(chunks)

    def handler(msg):
        data, b64 = left.pop(0)
        return {"result": {"data": data, "base64Encoded": b64, "eof": not left}}
    return handler


# ── коннектор: трасса ────────────────────────────────────────────────────

def test_trace_stream_base64_chunks_until_eof_and_closes_stream():
    half = len(PAYLOAD) // 2
    chunks = [(base64.b64encode(PAYLOAD[:half]).decode(), True),
              (base64.b64encode(PAYLOAD[half:]).decode(), True)]
    c = _conn({"Tracing.start": {"result": {}},
               # Событие приходит РАНЬШЕ ответа на Tracing.end — его нельзя потерять.
               "Tracing.end": {"before": [_event("Tracing.tracingComplete", stream="s1")]},
               "IO.read": _io_reader(chunks), "IO.close": {"result": {}}})
    assert c.trace_start(["devtools.timeline", "v8.execute"]) is True
    start = c._ws.sent[0]
    assert start["params"] == {"categories": "devtools.timeline,v8.execute",
                               "transferMode": "ReturnAsStream"}
    assert c.trace_stop(timeout=1) == EVENTS
    reads = [m for m in c._ws.sent if m["method"] == "IO.read"]
    assert len(reads) == 2 and all(m["params"]["handle"] == "s1" for m in reads)
    assert c._ws.methods()[-1] == "IO.close"
    assert c._event_watch == frozenset() and c._events == []


def test_trace_stream_plain_text_and_event_after_response():
    text = PAYLOAD.decode("utf-8")
    c = _conn({"Tracing.start": {"result": {}},
               "Tracing.end": {"result": {}, "after": [_event("Tracing.tracingComplete",
                                                              stream="s2")]},
               "IO.read": _io_reader([(text[:5], False), (text[5:], False)]),
               "IO.close": {"result": {}}})
    c.trace_start(["x"])
    assert c.trace_stop(timeout=1) == EVENTS


def test_trace_stream_gzip_and_bare_array():
    raw = gzip.compress(json.dumps(EVENTS).encode("utf-8"))
    assert wdmod.parse_trace_payload(raw) == EVENTS
    assert wdmod.parse_trace_payload(b"[1, 2]") == [1, 2]
    assert wdmod.parse_trace_payload(b"{not json") is None
    assert wdmod.parse_trace_payload(b'{"x": 1}') is None


def test_trace_report_events_mode_collects_data_collected():
    c = _conn({"Tracing.start": {"result": {}, "after": [
                   _event("Tracing.dataCollected", value=EVENTS[:1])]},
               "Tracing.end": {"before": [_event("Tracing.dataCollected", value=[{"n": 2}])],
                               "after": [_event("Tracing.tracingComplete")]}})
    c.trace_start(["x"])
    # Событие после ответа на start осталось в сокете — его заберёт ответ на end.
    assert c.trace_stop(timeout=1) == [*EVENTS, {"n": 2}]


def test_trace_io_read_failure_returns_none_and_still_closes():
    c = _conn({"Tracing.start": {"result": {}},
               "Tracing.end": {"result": {}, "after": [_event("Tracing.tracingComplete",
                                                              stream="s3")]},
               "IO.read": None, "IO.close": {"result": {}}})
    c.TRACE_CALL_TIMEOUT_SEC = 0.1
    c.trace_start(["x"])
    assert c.trace_stop(timeout=1) is None
    assert "IO.close" in c._ws.methods()


def test_trace_without_complete_event_times_out_to_none():
    c = _conn({"Tracing.start": {"result": {}}, "Tracing.end": {"result": {}}})
    c.trace_start(["x"])
    assert c.trace_stop(timeout=0.2) is None
    assert c.connected                         # таймаут — не обрыв


def test_trace_start_refused_clears_watch():
    c = _conn({"Tracing.start": None})
    c.TRACE_CALL_TIMEOUT_SEC = 0.1
    assert c.trace_start(["x"]) is None
    assert c._event_watch == frozenset()


def test_all_trace_calls_none_without_cdp():
    c = wdmod.R7WebDriverConnector(log_cb=lambda m: None)
    assert c.trace_start(["x"]) is None
    assert c.trace_stop(1) is None
    assert c.profile_start() is None
    assert c.profile_stop() is None
    assert c.wait_event("Tracing.tracingComplete", 0.1) is None


def test_trace_calls_never_raise(monkeypatch):
    c = _conn()

    def boom(*a, **k):
        raise RuntimeError("сбой")
    monkeypatch.setattr(c, "_cdp_send", boom)
    assert c.trace_start(["x"]) is None
    assert c.trace_stop(1) is None
    assert c.profile_start() is None
    assert c.profile_stop() is None


def test_wait_event_disconnect_marks_connection_lost():
    c = _conn()

    def closed():
        raise ConnectionAbortedError("обрыв")
    c._ws.recv = closed
    assert c.wait_event("Tracing.tracingComplete", 1) is None
    assert not c.connected


def test_cdp_send_still_drops_events_without_watch():
    """Без подписки поведение прежнее: события мимо id выбрасываются."""
    c = _conn({"Runtime.evaluate": {"before": [_event("Tracing.tracingComplete", stream="z")],
                                    "result": {"result": {"value": 7}}}})
    assert c.evaluate("1") == 7
    assert c._events == []


def test_wait_event_restores_socket_timeout():
    c = _conn()
    c._ws.timeout = 2.0
    c.wait_event("X", 0.05)
    assert c._ws.timeout == 2.0


# ── коннектор: профайлер ─────────────────────────────────────────────────

PROFILE = {"nodes": [{"id": 1, "callFrame": {"functionName": "(root)"}}],
           "startTime": 0, "endTime": 10, "samples": [1], "timeDeltas": [0]}


def test_profile_start_sets_interval_before_start_and_stop_returns_profile():
    c = _conn({"Profiler.enable": {"result": {}}, "Profiler.setSamplingInterval": {"result": {}},
               "Profiler.start": {"result": {}}, "Profiler.stop": {"result": {"profile": PROFILE}},
               "Profiler.disable": {"result": {}}})
    assert c.profile_start(200) is True
    assert c._ws.methods() == ["Profiler.enable", "Profiler.setSamplingInterval", "Profiler.start"]
    assert c._ws.sent[1]["params"] == {"interval": 200}
    assert c.profile_stop() == PROFILE
    assert c._ws.methods()[-1] == "Profiler.disable"


def test_profile_stop_bad_payload_is_none():
    c = _conn({"Profiler.stop": {"result": {"profile": {"nodes": "?"}}},
               "Profiler.disable": {"result": {}}})
    assert c.profile_stop() is None


def test_profile_start_refused_is_none():
    c = _conn({"Profiler.enable": {"result": {}}, "Profiler.setSamplingInterval": None})
    c.TRACE_CALL_TIMEOUT_SEC = 0.1
    assert c.profile_start() is None
    assert "Profiler.start" not in c._ws.methods()


# ── разбор профиля и трассы ──────────────────────────────────────────────

def _profile():
    nodes = [{"id": 1, "callFrame": {"functionName": "(root)"}},
             {"id": 2, "callFrame": {"functionName": "(idle)"}},
             {"id": 3, "callFrame": {"functionName": "asc_Paste", "url": "http://x/sdk-all-min.js",
                                     "lineNumber": 9}},
             {"id": 4, "callFrame": {"functionName": "", "url": "http://x/app.js",
                                     "lineNumber": 0}},
             {"id": 5, "callFrame": {"functionName": "(garbage collector)"}}]
    # Семплы: 3 (1000 мкс), 3 (1000), 4 (500), 5 (500), 2 (2000 до endTime).
    return {"nodes": nodes, "startTime": 0, "endTime": 5000,
            "samples": [3, 3, 4, 5, 2], "timeDeltas": [0, 1000, 1000, 500, 500]}


def test_profile_breakdown_by_kind():
    b = trace.profile_breakdown(_profile())
    assert b == {"scripting_ms": 2.5, "gc_ms": 0.5, "idle_ms": 2.0, "program_ms": 0.0,
                 "total_ms": 5.0}
    assert trace.profile_breakdown({"nodes": [], "samples": []}) is None
    assert trace.profile_breakdown(None) is None


def test_top_functions_self_time_and_share():
    top = trace.top_functions(_profile())
    assert top[0] == {"function": "asc_Paste", "url": "sdk-all-min.js", "line": 10,
                      "self_ms": 2.0, "pct": 66.7}
    assert top[1]["function"] == "(анонимная)" and top[1]["line"] == 1
    assert all(t["function"] not in ("(idle)", "(garbage collector)") for t in top)


def _meta(pid, tid, name):
    return {"ph": "M", "name": "thread_name", "pid": pid, "tid": tid, "args": {"name": name}}


def test_trace_breakdown_self_time_on_main_thread_only():
    events = [
        _meta(1, 10, "CrRendererMain"), _meta(1, 20, "Compositor"),
        {"ph": "X", "name": "RunTask", "ts": 0, "dur": 10_000, "pid": 1, "tid": 10},
        {"ph": "X", "name": "FunctionCall", "ts": 1000, "dur": 6000, "pid": 1, "tid": 10},
        {"ph": "X", "name": "Layout", "ts": 2000, "dur": 2000, "pid": 1, "tid": 10},
        {"ph": "B", "name": "Paint", "ts": 8000, "pid": 1, "tid": 10},
        {"ph": "E", "name": "Paint", "ts": 9000, "pid": 1, "tid": 10},
        {"ph": "X", "name": "V8.GCScavenger", "ts": 9500, "dur": 300, "pid": 1, "tid": 10},
        {"ph": "X", "name": "RasterTask", "ts": 0, "dur": 50_000, "pid": 1, "tid": 20},
    ]
    b = trace.trace_breakdown(events)
    assert b["scripting_ms"] == 4.0          # 6 мс вызова минус 2 мс раскладки внутри
    assert b["rendering_ms"] == 2.0
    assert b["painting_ms"] == 1.0           # растр другого потока не считается
    assert b["gc_ms"] == 0.3
    assert b["other_ms"] == 2.7              # RunTask без детей
    assert b["main_thread_found"] is True
    assert b["span_ms"] == 50.0


def test_trace_breakdown_without_metadata_counts_all_threads():
    events = [{"ph": "X", "name": "Paint", "ts": 0, "dur": 1000, "pid": 1, "tid": 1},
              {"ph": "X", "name": "Paint", "ts": 0, "dur": 1000, "pid": 1, "tid": 2}]
    b = trace.trace_breakdown(events)
    assert b["painting_ms"] == 2.0 and b["main_thread_found"] is False
    assert trace.trace_breakdown([]) is None


def test_phases_sources():
    events = [{"ph": "X", "name": "Layout", "ts": 0, "dur": 1000, "pid": 1, "tid": 1}]
    ph, src = trace.phases(events, _profile())
    assert ph["scripting_ms"] == 2.5 and src["scripting_ms"] == "profile"
    assert ph["rendering_ms"] == 1.0 and src["rendering_ms"] == "trace"
    assert ph["gc_ms"] == 0.5 and src["gc_ms"] == "profile"   # в трассе GC нет
    ph, src = trace.phases(events, None)
    assert src["scripting_ms"] == "trace"
    ph, src = trace.phases(None, _profile())
    assert "rendering_ms" not in ph and ph["span_ms"] == 5.0


def test_op_slug_and_file_names():
    assert trace.op_slug("Вставка 5 ячеек (ПКМ)") == "vstavka-5-yacheek-pkm"
    assert trace.op_slug("Выделение всех ячеек (Ctrl+A)") == "vydelenie-vseh-yacheek-ctrl-a"
    assert trace.op_slug("???") == "op"
    assert trace.trace_file_names("20261007_120000", "Функция ВПР (50K строк)") == (
        "20261007_120000_funktsiya-vpr-50k-strok.trace.json",
        "20261007_120000_funktsiya-vpr-50k-strok.cpuprofile")


def test_diagnostics_summary():
    rec = {"phases": {"scripting_ms": 1200.4, "rendering_ms": 30.0, "gc_ms": 5.0},
           "top_functions": [{"function": "asc_Paste", "pct": 61.2}]}
    s = trace.diagnostics_summary(rec)
    assert s == "скрипты 1200 мс; стили и раскладка 30 мс; сборка мусора 5 мс; " \
                "тяжелее всего asc_Paste (61 %)"
    assert trace.diagnostics_summary({"error": "нет CDP"}) == "трасса не снята: нет CDP"
    assert trace.diagnostics_summary(None) == ""


# ── файлы и отчёт ────────────────────────────────────────────────────────

def test_write_files_attach_to_report_and_list_for_evidence(tmp_path):
    tname, pname = trace.write_trace_files(tmp_path, "T", "Добавление нового листа",
                                           EVENTS, PROFILE, meta={"source": "x"})
    data = json.loads((tmp_path / tname).read_text(encoding="utf-8"))
    assert data["traceEvents"] == EVENTS and data["metadata"]["op"] == "Добавление нового листа"
    assert json.loads((tmp_path / pname).read_text(encoding="utf-8")) == PROFILE
    assert trace.write_trace_files(tmp_path, "T", "op", [], None) == (None, None)

    report = tmp_path / "performance_full_T.json"
    report.write_text(json.dumps({"measure_schema": 10, "results": [],
                                  "diagnostics": {"старая": {"trace_file": "old.json"}}}),
                      encoding="utf-8")
    assert trace.attach_to_report(report, {"Добавление нового листа": {
        "trace_file": tname, "profile_file": pname}})
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["measure_schema"] == 10                      # схема не поднимается
    assert set(saved["diagnostics"]) == {"старая", "Добавление нового листа"}
    files = trace.diagnostic_files(report, saved)
    assert [f.name for f in files] == [tname, pname]          # old.json на диске нет


def test_regressed_ops_takes_regression_and_probable():
    model = {"rows": [{"name": "a", "verdict": "regression", "compare": {"decision": "РЕГРЕССИЯ"}},
                      {"name": "b", "verdict": "ok",
                       "compare": {"decision": trace.PROBABLE_REGRESSION}},
                      {"name": "c", "verdict": "budget", "compare": None},
                      {"name": "d", "verdict": "ok", "compare": {"decision": "не определено"}}]}
    assert trace.regressed_ops(model) == ["a", "b"]


# ── повтор с трассой ─────────────────────────────────────────────────────


class FakeConnector:
    connected = True

    def __init__(self, events=EVENTS, profile=PROFILE, start_ok=True):
        self.calls = []
        self.events, self.profile, self.start_ok = events, profile, start_ok

    def trace_start(self, categories):
        self.calls.append("trace_start")
        return True if self.start_ok else None

    def profile_start(self, interval_us=200):
        self.calls.append(f"profile_start:{interval_us}")
        return True if self.start_ok else None

    def profile_stop(self):
        self.calls.append("profile_stop")
        return self.profile

    def trace_stop(self, timeout=60.0):
        self.calls.append("trace_stop")
        return self.events


class TraceApp(trace.TraceMixin):
    """Приложение с дублёром _measure_one_run: порядок шагов как у
    настоящего (см. test_measure_one_run_order_contract)."""

    def __init__(self, connector, op_raises=False):
        self.connector, self.log, self.order = connector, [], connector.calls
        self.op_raises = op_raises
        self.results = []                     # результаты прогона — трасса их не трогает

    def add_test_log(self, msg):
        self.log.append(msg)

    def _cdp_ops_connector(self):
        return self.connector

    def _measure_one_run(self, acc, i, runs, name, func, find_hwnd, log_cb, stop_event,
                         post_delay):
        func.prepare()
        self.order.append("stopwatch_start")
        try:
            func()
        except Exception as e:
            acc.error = str(e)
            return False
        self.order.append("stopwatch_end")
        acc.pass_times.append(1.25)
        acc.run_statuses.append("ok")
        post_delay()
        self.order.append("restore_history")
        return True


def _op_fn(order, raises=False):
    def fn():
        order.append("op")
        if raises:
            raise RuntimeError("операция упала")
    fn.prepare = lambda: order.append("prepare")
    fn.cleanup = lambda: order.append("cleanup")
    return fn


def test_trace_wraps_single_run_outside_stopwatch(tmp_path):
    conn = FakeConnector()
    app = TraceApp(conn)
    rec = app.capture_diagnostic_trace("Добавление нового листа", _op_fn(conn.calls), None,
                                       tmp_path, "T")
    assert conn.calls == ["prepare", "trace_start", "profile_start:200", "stopwatch_start", "op",
                          "stopwatch_end", "profile_stop", "trace_stop", "restore_history",
                          "cleanup"]
    assert rec["diagnostic_sec"] == 1.25 and "в медиану не входит" in rec["note"]
    assert (tmp_path / rec["trace_file"]).is_file() and (tmp_path / rec["profile_file"]).is_file()
    assert rec["phases"]["scripting_ms"] == 0.0 and rec["phase_sources"]["scripting_ms"] == "profile"
    assert app.results == []
    assert not {"time", "runs", "median"} & set(rec)


def test_trace_stopped_even_when_op_fails(tmp_path):
    conn = FakeConnector()
    app = TraceApp(conn)
    rec = app.capture_diagnostic_trace("op", _op_fn(conn.calls, raises=True), None, tmp_path, "T")
    assert conn.calls[-3:] == ["profile_stop", "trace_stop", "cleanup"]
    assert rec["op_error"] == "операция упала" and rec["diagnostic_sec"] is None


def test_trace_not_started_gives_error_record(tmp_path):
    conn = FakeConnector(start_ok=False)
    rec = TraceApp(conn).capture_diagnostic_trace("op", _op_fn(conn.calls), None, tmp_path, "T")
    assert rec["error"] == "трасса и профайлер не запустились"
    assert "trace_stop" not in conn.calls and list(tmp_path.iterdir()) == []


def test_trace_without_cdp_is_none(tmp_path):
    app = TraceApp(FakeConnector())
    app.connector = None
    assert app.capture_diagnostic_trace("op", _op_fn([]), None, tmp_path, "T") is None
    assert "нет CDP" in app.log[-1]


def test_measure_one_run_order_contract():
    """Порядок в настоящем _measure_one_run, на который опирается трасса:
    подготовка (в ней трасса включается) — до секундомера; пауза после
    повтора (в ней выключается) — после записи времени и до отката."""
    src = inspect.getsource(measure.MeasureMixin._measure_one_run)
    i_prepare = src.index("prepare()")
    i_start = src.index("start = time.perf_counter()")
    i_func = src.index("func()")
    i_append = src.index("acc.pass_times.append(elapsed)")
    i_post = src.index("post_delay()")
    i_restore = src.index("self._restore_history(")
    assert i_prepare < i_start < i_func < i_append < i_post < i_restore


def test_trace_module_does_not_measure():
    """Трасса не трогает статистику: ни _measure_op_repeated, ни _op_record."""
    src = inspect.getsource(trace)
    assert "_measure_op_repeated(" not in src and "_op_record(" not in src
    assert "_RunAcc()" in src


def test_trace_ops_session_opens_traces_and_closes(tmp_path, monkeypatch):
    conn = FakeConnector()
    app = TraceApp(conn)
    session = SimpleNamespace(find_hwnd=lambda: 1)
    closed = []
    app._locate_test_file = lambda: tmp_path / "f.xlsx"
    app._scenario_open_r7 = lambda f: session
    app._scenario_close_r7 = lambda s: closed.append(s)
    ops = {"Добавление нового листа": _op_fn(conn.calls)}

    class FakeOps:
        def __init__(self, *a):
            pass

        def tests(self):
            return list(ops.items())
    import r7_ops
    monkeypatch.setattr(r7_ops, "SpreadsheetOps", FakeOps)
    out = app.trace_ops_session(["Повторное открытие файла", "Добавление нового листа"], "T",
                                tmp_path)
    assert list(out) == ["Добавление нового листа"]
    assert closed == [session]
    assert any("открытия файла" in m for m in app.log)


def test_trace_ops_session_closes_r7_when_capture_raises(tmp_path, monkeypatch):
    app = TraceApp(FakeConnector())
    session = SimpleNamespace(find_hwnd=lambda: 1)
    closed = []
    app._locate_test_file = lambda: tmp_path / "f.xlsx"
    app._scenario_open_r7 = lambda f: session
    app._scenario_close_r7 = lambda s: closed.append(s)

    class FakeOps:
        def __init__(self, *a):
            pass

        def tests(self):
            return [("op", _op_fn([]))]
    import r7_ops
    monkeypatch.setattr(r7_ops, "SpreadsheetOps", FakeOps)

    def boom(*a, **k):
        raise RuntimeError("сбой")
    app.capture_diagnostic_trace = boom
    with pytest.raises(RuntimeError):
        app.trace_ops_session(["op"], "T", tmp_path)
    assert closed == [session]


def test_trace_ops_session_r7_not_opened(tmp_path):
    app = TraceApp(FakeConnector())
    app._locate_test_file = lambda: tmp_path / "f.xlsx"
    app._scenario_open_r7 = lambda f: None
    assert app.trace_ops_session(["op"], "T", tmp_path) == {}


def test_app_class_has_trace_mixin():
    import r7_Testovarka as r7mod
    assert issubclass(r7mod.R7Testovarka, trace.TraceMixin)


# ── страница готовности и пакет улик ─────────────────────────────────────

OP = "Добавление нового листа"


def _gate_model():
    suite = Suite(name="s", description="", tests={OP: 6}, budgets={}, min_effect_pct=10.0)
    res = [{"name": OP, "time": 1.5, "mad": 0.01, "n_runs": 6, "runs": [1.5] * 6,
            "run_statuses": ["ok"] * 6, "error": None}]
    return gate.gate_model(res, suite)


def test_gate_page_links_trace_files():
    model = _gate_model()
    diag = {"trace_file": "T_op.trace.json", "profile_file": "T_op.cpuprofile",
            "phases": {"scripting_ms": 900.0}, "top_functions": [{"function": "f", "pct": 50.0}]}
    out = gate.attach_diagnostics(model, {OP: diag})
    assert "trace" not in model["rows"][0]                   # исходная модель не тронута
    html = gate.gate_page(out)
    assert 'href="T_op.trace.json"' in html and 'href="T_op.cpuprofile"' in html
    assert "скрипты 900 мс" in html and "ui.perfetto.dev" in html
    assert gate.attach_diagnostics(model, {}) is model


def test_evidence_pack_includes_trace_files(tmp_path):
    def report(name, diagnostics=None):
        data = {"measure_schema": 10, "version": name, "results": [
            {"name": OP, "time": 1.0, "runs": [1.0] * 6, "run_statuses": ["ok"] * 6,
             "n_runs": 6, "error": None}]}
        if diagnostics:
            data["diagnostics"] = diagnostics
        p = tmp_path / f"performance_full_{name}.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p
    (tmp_path / "2_op.trace.json").write_text("{}", encoding="utf-8")
    (tmp_path / "2_op.cpuprofile").write_text("{}", encoding="utf-8")
    base = report("1")
    cur = report("2", {OP: {"trace_file": "2_op.trace.json", "profile_file": "2_op.cpuprofile"},
                       "другая": {"trace_file": "нет_на_диске.trace.json"}})
    zip_path = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=tmp_path / "no.log")
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        ticket = zf.read("ticket.md").decode("utf-8")
    assert {"2_op.trace.json", "2_op.cpuprofile"} <= names
    assert "нет_на_диске.trace.json" not in names
    assert "`2_op.trace.json`" in ticket


def test_connector_lock_still_single():
    """Ожидание события берёт тот же замок, что и _cdp_send: фоновые опросы
    не заберут из сокета событие трассы."""
    src = inspect.getsource(wdmod.R7WebDriverConnector.wait_event)
    assert "with self._cdp_lock" in src
    assert isinstance(_conn()._cdp_lock, type(threading.Lock()))


def test_paths_are_relative_names(tmp_path):
    tname, _ = trace.write_trace_files(tmp_path / "sub", "T", "op", EVENTS, None)
    assert Path(tname).name == tname
