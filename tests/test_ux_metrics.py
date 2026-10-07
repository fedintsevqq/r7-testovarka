"""Что видит пользователь (схема 10): первый кадр, longtask, JS-куча.

Главное, что здесь сторожится: метрики снимаются ВНЕ замера. Взвод — до
секундомера, сбор — после _wait_operation_done; время операции от них не
меняется, при мусоре от CDP поля None, а клавиши не шлются никогда.
JS коннектора исполняется в Node с фейковыми rAF и PerformanceObserver.
"""
import inspect
import json
import shutil
import subprocess

import pytest

import r7_reports
import r7_Testovarka as r7mod
import r7_webdriver_connector as wd
from r7 import measure as r7measure
from r7 import ux_metrics

# ── чистые функции ───────────────────────────────────────────────────────

def test_ux_from_marks_computes_fields():
    ux = ux_metrics.ux_from_marks(
        {"t0": 100.0, "tEnd": 130.0, "frame": 162.25, "longtask": True, "longest": 87.0},
        heap_before=100 * 2 ** 20, heap_after=112.5 * 2 ** 20)
    assert ux == {"ux_first_frame_ms": 62.2, "ux_longest_task_ms": 87.0,
                  "js_heap_mb": 112.5, "js_heap_delta_mb": 12.5}


@pytest.mark.parametrize("marks", [
    None, "мусор", 42, [], {},
    {"t0": "1", "frame": "2"},
    {"t0": 100.0, "frame": 50.0},                    # кадр раньше начала
    {"t0": float("nan"), "frame": 1.0},
    {"t0": True, "frame": 2.0},
    {"t0": 1.0, "frame": 2.0, "longtask": "yes", "longest": 5},   # не bool True
    {"t0": None, "frame": 2.0, "longtask": True, "longest": 5},   # операция не отметилась
])
def test_ux_from_garbage_gives_none(marks):
    ux = ux_metrics.ux_from_marks(marks)
    assert ux["ux_longest_task_ms"] is None
    if not (isinstance(marks, dict) and marks.get("t0") == 1.0):
        assert ux["ux_first_frame_ms"] is None
    assert ux["js_heap_mb"] is None and ux["js_heap_delta_mb"] is None


def test_longtask_supported_but_none_happened_is_zero():
    ux = ux_metrics.ux_from_marks({"t0": 1.0, "frame": 20.0, "longtask": True, "longest": 0})
    assert ux["ux_longest_task_ms"] == 0.0


def test_aggregate_uses_stats_indices_and_skips_none():
    rows = [{"ux_first_frame_ms": 900.0, "ux_longest_task_ms": None, "js_heap_mb": 1.0,
             "js_heap_delta_mb": 0.0},
            {"ux_first_frame_ms": 10.0, "ux_longest_task_ms": None, "js_heap_mb": 2.0,
             "js_heap_delta_mb": 1.0},
            None,
            {"ux_first_frame_ms": 30.0, "ux_longest_task_ms": None, "js_heap_mb": 4.0,
             "js_heap_delta_mb": 3.0}]
    agg = ux_metrics.aggregate_ux(rows, [1, 2, 3])
    assert agg == {"ux_first_frame_ms": 20.0, "ux_longest_task_ms": None,
                   "js_heap_mb": 3.0, "js_heap_delta_mb": 2.0}


# ── цикл повторов с фейковым коннектором ─────────────────────────────────

class _Clock:
    def __init__(self):
        self.t = 1000.0

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Watch:
    def start(self):
        pass

    def stop(self):
        return {"cpu_sec": 1.0, "cpu_peak_core_pct": 100.0,
                "cpu_avg_core_pct": 50.0, "ram_peak_mb": 500.0, "cpu_freq_min_pct": None}


class FakeConnector:
    """Коннектор: ответы ux_arm/ux_collect/performance_metrics по сценарию;
    каждый вызов двигает часы на rtt — если бы он попал в замер, время
    операции бы выросло."""

    connected = True

    def __init__(self, clock, order, arm=None, collect=None, metrics=None, rtt=0.5,
                 raise_on=()):
        self.clock, self.order = clock, order
        self.arm, self.collect, self.metrics = arm, collect, metrics
        self.rtt = rtt
        self.raise_on = raise_on

    def _call(self, name, value):
        self.order.append(name)
        self.clock.t += self.rtt
        if name in self.raise_on:
            raise RuntimeError(f"{name}: сокет")
        return value

    def ping(self, timeout=None):
        return False                  # _cdp_settle: «редактор не ответил» — сразу дальше

    def ux_arm(self, timeout=None):
        return self._call("arm", self.arm)

    def ux_collect(self, timeout=None):
        return self._call("collect", self.collect)

    def performance_metrics(self, timeout=None):
        return self._call("metrics", self.metrics)


@pytest.fixture
def loop(bare_r7, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(r7mod.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(r7mod.time, "sleep", clock.sleep)
    monkeypatch.setattr(r7measure, "_disk_snapshot", lambda: None)
    order = []
    r = bare_r7
    r._cached_cpu_count = 4

    def func():
        order.append("op")
        clock.t += 2.0                                 # операция — ровно 2 с

    def wait_done(hwnd, log_cb=None, start_grace=None):
        if start_grace == 0.3:
            return clock.t, "below_floor"
        order.append("op_end")
        return clock.t, "ok"

    def keys(*a, **k):
        raise AssertionError("клавиши в цикле метрик слать нельзя")

    r._hotkey = keys
    r._press = keys
    r._wait_operation_done = wait_done
    r._op_watch = lambda: _Watch()
    r._dismiss_info_alerts = lambda log_cb=None, max_alerts=3: []
    r._x2t_since = lambda mark: []
    r._get_r7_processes = lambda log_cb=None, fresh=False: []
    r._sample_r7_resources = lambda procs, measure_cpu=True: None
    r._history_snapshot = lambda log_cb=None: None

    def restore(before, label, hwnd=None, log_cb=None):
        order.append("restore")
        return True
    r._restore_history = restore

    def run(connector, runs=3):
        r._webdriver_connector = connector
        return r._measure_op_repeated("Выделение всех ячеек (Ctrl+A)", func, runs, None,
                                      lambda m: None, None, post_delay=lambda: None)
    return {"r": r, "clock": clock, "order": order, "run": run}


GOOD_MARKS = {"t0": 10.0, "tEnd": 1500.0, "frame": 1540.0, "longtask": True,
              "longest": 320.0, "heap": None}


def test_metrics_collected_after_op_end_and_time_unchanged(loop):
    conn = FakeConnector(loop["clock"], loop["order"],
                         arm={"armed": True, "longtask": True, "heap": None},
                         collect=GOOD_MARKS,
                         metrics={"JSHeapUsedSize": 300 * 2 ** 20})
    res = loop["run"](conn)
    assert res["runs"] == pytest.approx([2.0, 2.0, 2.0])     # round-trip'ы не в замере
    assert res["ux_first_frame_ms"] == 1530.0
    assert res["ux_longest_task_ms"] == 320.0
    assert res["js_heap_mb"] == 300.0 and res["js_heap_delta_mb"] == 0.0
    assert len(res["run_ux"]) == 3
    # порядок одного повтора: взвод → операция → конец → сбор → откат
    first = loop["order"][:loop["order"].index("restore") + 1]
    assert first == ["arm", "metrics", "op", "op_end", "collect", "metrics", "restore"]


@pytest.mark.parametrize("arm,collect,metrics", [
    (None, None, None),
    ("мусор", 17, ["x"]),
    ({"armed": True}, {"t0": "a", "frame": None, "longtask": 1}, {"JSHeapUsedSize": "много"}),
    ({"armed": False}, GOOD_MARKS, None),         # не взвелось — метки не читаем
])
def test_garbage_from_cdp_gives_none_fields_and_same_time(loop, arm, collect, metrics):
    conn = FakeConnector(loop["clock"], loop["order"], arm=arm, collect=collect,
                         metrics=metrics)
    res = loop["run"](conn)
    assert res["runs"] == pytest.approx([2.0, 2.0, 2.0])
    assert res["error"] is None
    for key in ux_metrics.UX_KEYS:
        assert res[key] is None, key


def test_cdp_exceptions_do_not_break_run(loop):
    conn = FakeConnector(loop["clock"], loop["order"], arm={"armed": True},
                         collect=GOOD_MARKS, raise_on=("arm", "collect", "metrics"))
    res = loop["run"](conn)
    assert res["runs"] == pytest.approx([2.0, 2.0, 2.0]) and res["error"] is None
    assert res["ux_first_frame_ms"] is None and res["js_heap_mb"] is None


def test_without_cdp_no_calls_and_none_fields(loop):
    res = loop["run"](None)
    assert res["runs"] == pytest.approx([2.0, 2.0, 2.0])
    assert "arm" not in loop["order"] and "collect" not in loop["order"]
    assert all(res[k] is None for k in ux_metrics.UX_KEYS)
    assert res["run_ux"] == [None, None, None]


def test_failed_op_record_has_ux_keys(bare_r7):
    from r7.measure import _RunAcc
    bare_r7._aggregate_x2t = lambda runs, idx, log_cb: None
    rec = bare_r7._failed_op_record("A", _RunAcc(), lambda m: None)
    assert all(k in rec and rec[k] is None for k in ux_metrics.UX_KEYS)


# ── стражи по исходнику: сбор после конца операции, взвод до секундомера ──

def test_source_order_arm_before_stopwatch_collect_after_op_end():
    src = inspect.getsource(r7mod.R7Testovarka._measure_one_run)
    arm = src.index("self._ux_arm()")
    start = src.index("start = time.perf_counter()")
    op_end = src.index("self._wait_operation_done(find_hwnd, log_cb=log_cb))")
    wait_end = src.index("wait_end = time.perf_counter()")
    verify = src.index("self._flush_pending_cdp_verify(log_cb=log_cb)")
    collect = src.index("acc.run_ux.append(self._ux_collect(ux_state, log_cb))")
    restore = src.index("self._restore_history(hist_before")
    assert arm < start < op_end < wait_end < verify < collect < restore


def test_mark_hook_is_after_api_ms_in_every_op_js():
    for js in (wd._SELECT_ALL_JS, wd._COPY_JS, wd._PASTE_JS, wd._ADD_SHEET_JS,
               wd._DELETE_COLUMNS_JS, wd._select_range_js("A1"), wd._show_sheet_js(0),
               wd._insert_cells_js("InsertColumns", 3)):
        api_ms = js.index("st.api_ms = performance.now() - __t0;")
        mark = js.index("__uxMark(win, __t0);")
        after = js.index("st.after = docState(api, win);")
        assert api_ms < mark < after


# ── JS в Node ────────────────────────────────────────────────────────────

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js не установлен")

JS_PRELUDE = r"""
globalThis.window = globalThis;
globalThis.document = { querySelectorAll: function () { return []; } };
var __rafq = [];
globalThis.requestAnimationFrame = function (cb) { __rafq.push(cb); return __rafq.length; };
function flushFrame() { var q = __rafq; __rafq = []; q.forEach(function (cb) { cb(0); }); }
var __obs = [];
function FakePO(cb) { this.cb = cb; this.pending = []; __obs.push(this); }
FakePO.supportedEntryTypes = SUPPORTED;
FakePO.prototype.observe = function (o) { this.opts = o; };
FakePO.prototype.takeRecords = function () { var p = this.pending; this.pending = []; return p; };
globalThis.PerformanceObserver = FakePO;
function longTask(start, dur) { __obs.forEach(function (o) { o.pending.push({startTime: start, duration: dur}); }); }
var H = { Index: 0, Points: [], Can_Undo: function () { return true; } };
window.AscCommon = { History: H };
window.Asc = { editor: {
  asc_EditSelectAll: function () {},
  asc_getWorksheetsCount: function () { return 1; },
  asc_getActiveWorksheetIndex: function () { return 0; },
  asc_getActiveRangeStr: function () { return 'A1'; },
  asc_Paste: function () { H.Index += 1; return true; },
  asc_findCell: function () {},
} };
"""


def run_node(body, supported=("longtask",)):
    script = (JS_PRELUDE.replace("SUPPORTED", json.dumps(list(supported)))
              + body + "\n")
    proc = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True,
                          encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@needs_node
def test_js_arm_op_collect_roundtrip():
    out = run_node(f"""
      var arm = {wd._UX_ARM_JS.strip()};
      var op = {wd._PASTE_JS.strip()};
      longTask(performance.now() - 10000, 400);       // до операции — не в счёт
      longTask(performance.now() + 1, 120);
      var noFrame = window.__r7ux.frame;
      flushFrame(); flushFrame();
      var col = {wd._UX_COLLECT_JS.strip()};
      console.log(JSON.stringify({{arm: arm, op: op, col: col, noFrame: noFrame,
                                   armedAfter: window.__r7ux.armed}}));
    """)
    assert out["arm"]["armed"] is True and out["arm"]["longtask"] is True
    assert out["op"]["ok"] is True and isinstance(out["op"]["api_ms"], (int, float))
    col = out["col"]
    assert out["noFrame"] is None                        # кадр — только после двух rAF
    assert isinstance(col["frame"], (int, float)) and col["frame"] >= col["t0"]
    assert col["longtask"] is True and col["longest"] == 120
    assert out["armedAfter"] is False


@needs_node
def test_js_unarmed_op_leaves_no_marks_and_no_raf():
    out = run_node(f"""
      var op = {wd._PASTE_JS.strip()};
      console.log(JSON.stringify({{op: op, ux: window.__r7ux || null, raf: __rafq.length}}));
    """)
    assert out["op"]["ok"] is True
    assert out["ux"] is None and out["raf"] == 0


@needs_node
def test_js_after_collect_ops_are_not_marked():
    """Откат и подготовка следующего повтора идут через тот же _op_js — после
    сбора окно разоружено, и их кадры в метки не попадают."""
    out = run_node(f"""
      {wd._UX_ARM_JS.strip()};
      {wd._UX_COLLECT_JS.strip()};
      var op = {wd._PASTE_JS.strip()};
      console.log(JSON.stringify({{t0: window.__r7ux.t0, raf: __rafq.length}}));
    """)
    assert out == {"t0": None, "raf": 0}


@needs_node
def test_js_longtask_unsupported_gives_none():
    out = run_node(f"""
      var arm = {wd._UX_ARM_JS.strip()};
      {wd._PASTE_JS.strip()};
      flushFrame(); flushFrame();
      var col = {wd._UX_COLLECT_JS.strip()};
      console.log(JSON.stringify({{arm: arm, col: col}}));
    """, supported=())
    assert out["arm"]["longtask"] is False
    assert out["col"]["longest"] is None
    ux = ux_metrics.ux_from_marks(out["col"])
    assert ux["ux_longest_task_ms"] is None and ux["ux_first_frame_ms"] is not None


@needs_node
def test_js_sequence_frame_from_last_step_start_from_first():
    out = run_node(f"""
      {wd._UX_ARM_JS.strip()};
      {wd._select_range_js("A1").strip()};
      var t0 = window.__r7ux.t0;
      {wd._PASTE_JS.strip()};
      flushFrame(); flushFrame();
      console.log(JSON.stringify({{t0first: t0, u: window.__r7ux}}));
    """)
    u = out["u"]
    assert u["t0"] == out["t0first"] and u["seq"] == 2
    assert u["frame"] >= u["tEnd"]


# ── HTML: вторичные колонки и терпимость к старым отчётам ────────────────

def _op(name, t, **extra):
    r = {"name": name, "time": t, "error": None, "runs": [t, t], "n_runs": 2,
         "run_statuses": ["ok", "ok"], "mad": 0.0, "min": t, "max": t,
         "ram": 900.0, "cpu": 80.0, "cpu_sec": 1.0}
    r.update(extra)
    return r


def test_run_report_shows_ux_columns_and_throttle(tmp_path):
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app._applied_r7_window_size = None
    app._run_environment = None
    f = tmp_path / "a.xlsx"
    f.write_bytes(b"x")
    results = [_op("A", 1.0, ux_first_frame_ms=1234.5, ux_longest_task_ms=321.0,
                   js_heap_mb=456.0, run_notes=[[], ["throttle"]],
                   run_cpu_freq_pct=[99.0, 61.0], n_throttled=1),
               _op("Старый", 2.0)]
    out = app._generate_html_report(
        results, f, 1.0, "v", [], [], None, None, None, None,
        system={"environment": {"power_plan": "Высокая производительность",
                                "power_plan_before": "Сбалансированная",
                                "power_plan_during": "Высокая производительность"}})
    assert "Кадр, мс" in out and "234,5" in out and "321,0" in out and "456" in out
    assert "троттлинг×1" in out and "throttled" in out and "61 % номинальной" in out
    assert "Высокая производительность (до прогона: Сбалансированная)" in out


def test_comparison_tolerates_files_with_and_without_ux_keys(tmp_path):
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app._applied_r7_window_size = None
    old = [_op("A", 1.0)]
    new = [_op("A", 1.0, ux_first_frame_ms=10.0, ux_longest_task_ms=None, js_heap_mb=1.0,
               run_ux=[None, None], run_notes=[[], []], run_cpu_freq_pct=[None, None])]
    ds = [{"path": "b", "version": "1", "data": {"results": old, "measure_schema": 9}},
          {"path": "n", "version": "2", "data": {"results": new, "measure_schema": 10}}]
    out = app._generate_comparison_html(ds, "b")
    assert "A" in out


def test_power_plan_text_old_report():
    assert r7_reports._power_plan_text({"power_plan": "Сбалансированная"}) == "Сбалансированная"
    assert r7_reports._power_plan_text({}) == "—"
