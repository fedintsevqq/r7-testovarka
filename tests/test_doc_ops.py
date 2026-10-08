"""Операции документа (r7_doc_ops.DocumentOps) и режим «документ» воркера
(r7/doc_run.py): CDP прошёл — клавиш нет; не прошёл — ошибка прогона, а не
клавиши вслепую; результат не подтвердился — повтор «unverified»; снимок и
откат истории, готовность и конец операции по досчитанной вёрстке."""
import pytest

import r7_doc_ops
from r7 import doc_js
from r7 import doc_run
from r7.doc_fixtures import DOC_FIXTURE_NAME
from r7.op_end import OpEndMixin
from r7.readiness import ReadinessMixin


def _state(index=-1, pages=100, blocks=600, busy=False):
    return {"pages": pages, "blocks": blocks, "historyIndex": index, "historyPoints": index + 1,
            "canUndo": index >= 0, "recalcBusy": busy}


class DocConn:
    """Фейковый коннектор: состояния документа по очереди (последнее
    повторяется), ответ операции и отката."""
    connected = True

    def __init__(self, states=(), op=None, undo=None):
        self.states, self.op, self.undo = list(states), op, undo
        self.evals = []

    def evaluate(self, js, timeout=None):
        self.evals.append(js)
        if js == doc_js.DOC_STATE_JS:
            if len(self.states) > 1:
                return self.states.pop(0)
            return self.states[0] if self.states else None
        if js == doc_js.DOC_CURSOR_START_JS:
            return {"ok": True}
        if "st.reached" in js:
            return self.undo
        return self.op

    def ping(self, timeout=None):
        return True


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def perf_counter(self):
        return self.t

    def sleep(self, sec):
        self.t += sec


@pytest.fixture
def doc_app(bare_r7, monkeypatch):
    """Голый R7Testovarka в режиме «документ», клавиши записываются."""
    a = bare_r7
    a._run_editor = doc_run.EDITOR_DOCUMENT
    a.keys, a.logs = [], []
    a.add_test_log = a.logs.append
    a._hotkey = lambda *k: a.keys.append(k)
    a._press = lambda k: a.keys.append((k,))
    a._op_unverified = None
    clock = Clock()
    monkeypatch.setattr(doc_run.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(doc_run.time, "sleep", clock.sleep)
    a.clock = clock
    return a


def _ops(app):
    return r7_doc_ops.DocumentOps(app, find_hwnd=lambda: 1, log_cb=app.logs.append,
                                  test_file="d.docx")


def _op_reply(ok=True, mutated=True, before=None, after=None, **extra):
    return {"ok": ok, "mutated": mutated, "api_ms": 12.5, "method": "asc_AddBlankPage",
            "before": before or _state(-1, 100), "after": after or _state(99, 200), **extra}


# ── CDP прошёл / не прошёл ───────────────────────────────────────────────

def test_add_pages_via_cdp_presses_no_keys_and_is_verified(doc_app):
    doc_app._webdriver_connector = DocConn(op=_op_reply())
    _ops(doc_app).add_pages()
    assert doc_app.keys == []
    assert doc_app._op_via_cdp is True and doc_app._cdp_api_ms == 12.5
    assert doc_app._pending_cdp_verify is None and doc_app._op_unverified is None


@pytest.mark.parametrize("call", [
    lambda o: o.add_pages(), lambda o: o.restyle_all(), lambda o: o.replace_all("a", "b")])
def test_cdp_failure_untouched_raises_without_keys(doc_app, call):
    doc_app._webdriver_connector = DocConn(op={"ok": False, "mutated": False,
                                               "reason": "no-method:x"})
    with pytest.raises(RuntimeError, match="клавишами не повторяю"):
        call(_ops(doc_app))
    assert doc_app.keys == []


def test_no_cdp_raises_without_keys(doc_app):
    doc_app._webdriver_connector = None
    with pytest.raises(RuntimeError, match="нет CDP"):
        _ops(doc_app).restyle_all()
    assert doc_app.keys == []


def test_failure_after_mutation_marks_run_unverified(doc_app):
    """Сбой ПОСЛЕ правки: клавишами не повторять, повтор в медиану не идёт."""
    doc_app._webdriver_connector = DocConn(op={"ok": False, "mutated": True,
                                               "reason": "exception", "error": "boom"})
    _ops(doc_app).restyle_all()
    assert doc_app.keys == [] and doc_app._op_unverified


def test_no_answer_with_live_connection_is_unverified(doc_app):
    doc_app._webdriver_connector = DocConn(op=None)
    _ops(doc_app).replace_all("квартал", "период")
    assert doc_app.keys == [] and "неизвестен" in doc_app._op_unverified


def test_history_not_moved_is_unverified_after_flush(doc_app):
    """«mutated», но история не сдвинулась ни сразу, ни после замера."""
    doc_app._webdriver_connector = DocConn(states=[_state(-1)],
                                           op=_op_reply(after=_state(-1, 100)))
    _ops(doc_app).restyle_all()
    assert doc_app._pending_cdp_verify is not None
    doc_app._flush_pending_cdp_verify()
    assert "не сдвинулась" in doc_app._op_unverified


def test_pages_counted_after_layout_confirm_on_flush(doc_app):
    """Сразу после вызова страницы ещё не досчитаны — проверка после замера."""
    doc_app._webdriver_connector = DocConn(states=[_state(99, 200)],
                                           op=_op_reply(after=_state(99, 100)))
    _ops(doc_app).add_pages()
    assert doc_app._pending_cdp_verify is not None and doc_app._op_unverified is None
    doc_app._flush_pending_cdp_verify()
    assert doc_app._op_unverified is None
    assert any("страниц было 100, стало 200" in m for m in doc_app.logs)


def test_flush_pages_still_not_grown_is_unverified(doc_app):
    doc_app._webdriver_connector = DocConn(states=[_state(99, 101)],
                                           op=_op_reply(after=_state(99, 100)))
    _ops(doc_app).add_pages()
    doc_app._flush_pending_cdp_verify()
    assert "ожидалось не меньше +50" in doc_app._op_unverified


def test_check_pages_added_cases():
    check = doc_run.check_pages_added(100)
    assert check(_state(-1, 100), _state(99, 200))[0] is True
    assert check(_state(-1, 100), _state(-1, 200))[0] is False      # история на месте
    assert check(_state(-1, 100), _state(99, 120))[0] is False
    assert check({"historyIndex": -1}, {"historyIndex": 3})[0] is False   # страниц нет


# ── список тестов ────────────────────────────────────────────────────────

class RecApp:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if not name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: self.calls.append((name, a)) or True


def test_tests_order_names_and_prepare():
    app = RecApp()
    tests = r7_doc_ops.DocumentOps(app, lambda: 1, lambda m: None, "d.docx").tests()
    assert [n for n, _ in tests] == r7_doc_ops.DOCUMENT_TEST_DEFINITIONS[1:]
    for _name, func in tests:
        func.prepare()
        func()
    names = [c[0] for c in app.calls]
    assert names.count("_doc_prepare") == len(tests)
    exports = [c[1][0] for c in app.calls if c[0] == "_save_as_format"]
    assert exports == ["pdf", "docx"]
    assert ("_hotkey" not in names) and ("_press" not in names)


def test_export_names_are_non_mutating(bare_r7):
    for name in (r7_doc_ops.EXPORT_PDF_TEST, r7_doc_ops.EXPORT_DOCX_TEST):
        assert not bare_r7._op_expects_change(name)
    for name in (r7_doc_ops.ADD_PAGES_TEST, r7_doc_ops.RESTYLE_TEST, r7_doc_ops.REPLACE_TEST):
        assert bare_r7._op_expects_change(name)


# ── снимок и откат истории ───────────────────────────────────────────────

def test_history_snapshot_reads_document_state(doc_app):
    doc_app._webdriver_connector = DocConn(states=[_state(4, 120)])
    assert doc_app._history_snapshot() == {"index": 4, "pages": 120, "blocks": 600}
    doc_app._webdriver_connector = DocConn(states=[])
    assert doc_app._history_snapshot() is None


def test_spreadsheet_mode_keeps_spreadsheet_snapshot(bare_r7):
    class SheetConn:
        connected = True

        def document_state(self, timeout=None):
            return {"historyIndex": 2, "active": 0, "selection": "A1"}
    bare_r7._webdriver_connector = SheetConn()
    assert bare_r7._history_snapshot() == {"index": 2, "active": 0, "selection": "A1"}


def test_restore_undoes_to_snapshot_and_waits(doc_app):
    conn = DocConn(states=[_state(99, 200), _state(-1, 100)],
                   undo={"reached": True, "steps": 100, "undo_ms": 40.0})
    doc_app._webdriver_connector = conn
    waits = []
    doc_app._wait_operation_done = lambda hwnd, log_cb=None, start_grace=None: waits.append(1)
    assert doc_app._restore_history({"index": -1}, "Вставка", hwnd=1) is True
    undo_js = [js for js in conn.evals if "st.reached" in js]
    assert len(undo_js) == 1 and "target: -1" in undo_js[0]
    assert waits == [1]


def test_restore_reports_failure_and_missing_cdp(doc_app):
    doc_app._webdriver_connector = DocConn(states=[_state(99)], undo={"reached": False})
    doc_app._wait_operation_done = lambda *a, **k: None
    assert doc_app._restore_history({"index": -1}, "Вставка") is False
    assert doc_app._restore_history(None, "Вставка") is None
    doc_app._webdriver_connector = None
    assert doc_app._restore_history({"index": -1}, "Вставка") is False


# ── готовность после открытия: вёрстка ───────────────────────────────────

def _fake_ready(monkeypatch, ready_at=50.0, marker="bold"):
    def fake(self, hwnd, timeout=120, log_cb=None):
        self._ready_at, self._ready_marker = ready_at, marker
        return True
    monkeypatch.setattr(ReadinessMixin, "_wait_until_r7_ready", fake)


def test_ready_waits_for_layout_and_moves_ready_moment(doc_app, monkeypatch):
    _fake_ready(monkeypatch)
    doc_app._webdriver_connector = DocConn(states=[
        _state(pages=40, busy=True), _state(pages=80, busy=True), _state(pages=100)])
    start = doc_app.clock.t
    assert doc_app._wait_until_r7_ready(lambda: 1) is True
    assert doc_app._ready_marker == "bold+layout"
    assert doc_app._ready_at == pytest.approx(start + 2 * doc_app.DOC_LAYOUT_POLL_SEC)


def test_ready_keeps_moment_when_layout_already_done(doc_app, monkeypatch):
    _fake_ready(monkeypatch)
    doc_app._webdriver_connector = DocConn(states=[_state(pages=100)])
    doc_app._wait_until_r7_ready(lambda: 1)
    assert (doc_app._ready_at, doc_app._ready_marker) == (50.0, "bold")


def test_ready_without_recalc_field_needs_stable_page_count(doc_app, monkeypatch):
    _fake_ready(monkeypatch)
    unknown = [dict(_state(pages=p), recalcBusy=None) for p in (60, 90, 90)]
    doc_app._webdriver_connector = DocConn(states=unknown)
    start = doc_app.clock.t
    doc_app._wait_until_r7_ready(lambda: 1)
    assert doc_app._ready_at == pytest.approx(start + doc_app.DOC_LAYOUT_POLL_SEC)
    assert doc_app._ready_marker == "bold+layout"


def test_ready_spreadsheet_mode_untouched(bare_r7, monkeypatch):
    _fake_ready(monkeypatch)
    bare_r7._webdriver_connector = DocConn(states=[_state(pages=1, busy=True)])
    bare_r7.add_test_log = lambda m: None
    bare_r7._wait_until_r7_ready(lambda: 1)
    assert (bare_r7._ready_at, bare_r7._ready_marker) == (50.0, "bold")
    assert bare_r7._webdriver_connector.evals == []


# ── конец операции: пинг + вёрстка ───────────────────────────────────────

def _ping_returns_its_start(monkeypatch, slow_for=0.0):
    calls = []

    def fake_idle(self, log_cb=None):
        calls.append(self.clock.t)
        return self.clock.t + slow_for, "ok"
    monkeypatch.setattr(OpEndMixin, "_wait_renderer_idle", fake_idle)
    return calls


def test_op_end_counts_layout_tail_before_ping(doc_app, monkeypatch):
    # Вёрстка досчитывается после возврата api — конец по её досчёту, пинг
    # идёт уже после и медленных ответов не видит.
    calls = _ping_returns_its_start(monkeypatch)
    doc_app._webdriver_connector = DocConn(states=[
        _state(pages=150, busy=True), _state(pages=200)])
    start = doc_app.clock.t
    end, status = doc_app._wait_renderer_idle()
    assert status == "ok" and len(calls) == 1
    assert calls[0] > start + doc_app.DOC_LAYOUT_POLL_SEC          # пинг — после вёрстки
    assert end == pytest.approx(start + doc_app.DOC_LAYOUT_POLL_SEC)


def test_op_end_layout_done_at_return_keeps_api_moment(doc_app, monkeypatch):
    _ping_returns_its_start(monkeypatch)
    doc_app._webdriver_connector = DocConn(states=[_state(pages=200)])
    start = doc_app.clock.t
    end, _status = doc_app._wait_renderer_idle()
    assert end == pytest.approx(start)          # подтверждающие чтения — не работа Р7


def test_op_end_slow_ping_after_layout_moves_end(doc_app, monkeypatch):
    _ping_returns_its_start(monkeypatch, slow_for=0.4)
    doc_app._webdriver_connector = DocConn(states=[_state(pages=200)])
    start = doc_app.clock.t
    end, _status = doc_app._wait_renderer_idle()
    assert end > start + 0.4


def test_op_end_ignores_single_idle_read_between_layout_chunks(doc_app, monkeypatch):
    # Одно чтение «FullRecalc нет» между порциями вёрстки — ещё не конец.
    calls = []

    def fake_idle(self, log_cb=None):
        calls.append(self.clock.t)
        return self.clock.t, "ok"
    monkeypatch.setattr(OpEndMixin, "_wait_renderer_idle", fake_idle)
    doc_app._webdriver_connector = DocConn(states=[
        _state(pages=101, busy=True), _state(pages=101), _state(pages=120, busy=True),
        _state(pages=146)])
    start = doc_app.clock.t
    end, status = doc_app._wait_renderer_idle()
    assert status == "ok"
    assert end >= start + 3 * doc_app.DOC_LAYOUT_POLL_SEC - 1e-9


def test_op_end_logs_when_state_unreadable(doc_app, monkeypatch):
    monkeypatch.setattr(OpEndMixin, "_wait_renderer_idle",
                        lambda self, log_cb=None: (105.0, "ok"))
    doc_app._webdriver_connector = DocConn(states=[])
    logs = []
    assert doc_app._wait_renderer_idle(logs.append) == (105.0, "ok")
    assert any("без досчёта вёрстки" in m for m in logs)


def test_op_end_keeps_ping_end_when_layout_done(doc_app, monkeypatch):
    monkeypatch.setattr(OpEndMixin, "_wait_renderer_idle",
                        lambda self, log_cb=None: (105.0, "ok"))
    doc_app._webdriver_connector = DocConn(states=[_state(pages=200)])
    assert doc_app._wait_renderer_idle() == (105.0, "ok")


# ── прочее режима ────────────────────────────────────────────────────────

def test_make_run_ops_and_editor_names(doc_app):
    assert isinstance(doc_app._make_run_ops(lambda: 1, print, "d.docx"), r7_doc_ops.DocumentOps)
    names = doc_app.editor_test_names()
    assert names["document"] == r7_doc_ops.DOCUMENT_TEST_DEFINITIONS
    assert names["spreadsheet"] == list(type(doc_app).TEST_DEFINITIONS)


def test_locate_generates_document_fixture(doc_app, tmp_path, monkeypatch):
    from r7 import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.chdir(tmp_path)
    doc_app.test_files_folder = tmp_path / "TestFiles"
    path = doc_app._locate_test_file()
    assert path == doc_app.test_files_folder / DOC_FIXTURE_NAME and path.is_file()
    assert doc_app._locate_test_file() == path              # второй раз — найдена


def test_doc_prepare_waits_layout_then_moves_cursor(doc_app):
    conn = DocConn(states=[_state(busy=True), _state()])
    doc_app._webdriver_connector = conn
    doc_app._doc_prepare()
    assert conn.evals[-1] == doc_js.DOC_CURSOR_START_JS
    assert conn.evals.count(doc_js.DOC_STATE_JS) == 3     # итог подтверждён вторым чтением


def test_autosave_suspend_and_restore_in_document_mode(doc_app):
    conn = DocConn(op={"gap_ms": 1000, "periodic": True})
    doc_app._webdriver_connector = conn
    doc_app._suspend_autosave()
    assert doc_app._autosave_state == {"gap_ms": 1000, "periodic": True}
    conn.op = True
    doc_app._restore_autosave()
    assert doc_app._autosave_state is None and any("возвращено" in m for m in doc_app.logs)
