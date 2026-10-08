"""Операции презентации (r7_pptx_ops.PresentationOps) и режим «презентация»
воркера (r7/pptx_run.py): CDP прошёл — клавиш нет; не прошёл — ошибка
прогона, а не клавиши вслепую; результат не подтвердился — повтор
«unverified»; снимок и откат истории, готовность по числу слайдов и маркер
«Добавить слайд»; документы и таблицы не задеты."""
import pytest

import r7_pptx_ops
from r7 import doc_run, pptx_js, pptx_run
from r7.op_end import OpEndMixin
from r7.pptx_fixtures import PPTX_FIXTURE_NAME
from r7.readiness import ReadinessMixin


def _state(index=-1, slides=50, theme="R7 Testovarka", transitions=0):
    return {"pages": slides, "slides": slides, "historyIndex": index,
            "historyPoints": index + 1, "canUndo": index >= 0, "recalcBusy": None,
            "theme": theme, "transitions": transitions}


class PresConn:
    """Фейковый коннектор: состояния презентации по очереди (последнее
    повторяется), ответ операции и отката."""
    connected = True

    def __init__(self, states=(), op=None, undo=None, probe=None):
        self.states, self.op, self.undo, self.probe = list(states), op, undo, probe
        self.evals = []

    def evaluate(self, js, timeout=None):
        self.evals.append(js)
        if js == pptx_js.PPTX_STATE_JS:
            if len(self.states) > 1:
                return self.states.pop(0)
            return self.states[0] if self.states else None
        if js == pptx_js.PPTX_FIRST_SLIDE_JS:
            return {"ok": True}
        if js == pptx_js.PPTX_READY_PROBE_JS:
            return self.probe
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
def pres_app(bare_r7, monkeypatch):
    """Голый R7Testovarka в режиме «презентация», клавиши записываются."""
    a = bare_r7
    a._run_editor = doc_run.EDITOR_PRESENTATION
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
    return r7_pptx_ops.PresentationOps(app, find_hwnd=lambda: 1, log_cb=app.logs.append,
                                       test_file="s.pptx")


def _op_reply(ok=True, mutated=True, before=None, after=None, **extra):
    return {"ok": ok, "mutated": mutated, "api_ms": 8.0, "method": "AddSlide",
            "before": before or _state(-1, 50), "after": after or _state(49, 100), **extra}


# ── CDP прошёл / не прошёл ───────────────────────────────────────────────

def test_add_slides_via_cdp_presses_no_keys_and_is_verified(pres_app):
    pres_app._webdriver_connector = PresConn(op=_op_reply())
    _ops(pres_app).add_slides()
    assert pres_app.keys == []
    assert pres_app._op_via_cdp is True and pres_app._cdp_api_ms == 8.0
    assert pres_app._pending_cdp_verify is None and pres_app._op_unverified is None


EDITS = [lambda o: o.add_slides(), lambda o: o.duplicate_all(),
         lambda o: o.change_theme(), lambda o: o.transition_all()]


@pytest.mark.parametrize("call", EDITS)
def test_cdp_failure_untouched_raises_without_keys(pres_app, call):
    pres_app._webdriver_connector = PresConn(op={"ok": False, "mutated": False,
                                                 "reason": "no-method:x"})
    with pytest.raises(RuntimeError, match="клавишами не повторяю"):
        call(_ops(pres_app))
    assert pres_app.keys == []


@pytest.mark.parametrize("call", EDITS)
def test_no_cdp_raises_without_keys(pres_app, call):
    pres_app._webdriver_connector = None
    with pytest.raises(RuntimeError, match="нет CDP"):
        call(_ops(pres_app))
    assert pres_app.keys == []


def test_failure_after_mutation_marks_run_unverified(pres_app):
    pres_app._webdriver_connector = PresConn(op={"ok": False, "mutated": True,
                                                 "reason": "exception", "error": "boom"})
    _ops(pres_app).change_theme()
    assert pres_app.keys == [] and pres_app._op_unverified


def test_no_answer_with_live_connection_is_unverified(pres_app):
    pres_app._webdriver_connector = PresConn(op=None)
    _ops(pres_app).transition_all()
    assert pres_app.keys == [] and "неизвестен" in pres_app._op_unverified


def test_theme_applied_later_confirmed_on_flush(pres_app):
    """Тема могла догрузиться после вызова — проверка повторяется после замера."""
    pres_app._webdriver_connector = PresConn(
        states=[_state(0, theme="Basic")], op=_op_reply(after=_state(-1)))
    _ops(pres_app).change_theme()
    assert pres_app._pending_cdp_verify is not None and pres_app._op_unverified is None
    pres_app._flush_pending_cdp_verify()
    assert pres_app._op_unverified is None
    assert any("«R7 Testovarka» → «Basic»" in m for m in pres_app.logs)


def test_duplicate_only_current_slide_is_unverified(pres_app):
    pres_app._webdriver_connector = PresConn(states=[_state(0, 51)],
                                             op=_op_reply(after=_state(0, 51)))
    _ops(pres_app).duplicate_all()
    pres_app._flush_pending_cdp_verify()
    assert "ожидалось 100" in pres_app._op_unverified


# ── проверки ─────────────────────────────────────────────────────────────

def test_check_slides_added_cases():
    check = pptx_run.check_slides_added(50)
    assert check(_state(-1, 50), _state(49, 100))[0] is True
    assert check(_state(-1, 50), _state(-1, 100))[0] is False        # история на месте
    assert "ожидалось +50" in check(_state(-1, 50), _state(49, 60))[1]
    assert check({"historyIndex": -1}, {"historyIndex": 3})[0] is False   # слайдов нет


def test_check_slides_doubled_cases():
    assert pptx_run.check_slides_doubled(_state(-1, 50), _state(0, 100))[0] is True
    assert pptx_run.check_slides_doubled(_state(-1, 50), _state(0, 51))[0] is False


def test_check_theme_changed_cases():
    check = pptx_run.check_theme_changed
    assert check(_state(-1), _state(0, theme="Basic"))[0] is True
    assert "осталась" in check(_state(-1), _state(0))[1]
    assert check(_state(-1, theme=None), _state(0, theme=None))[0] is True
    assert check(_state(-1), _state(-1, theme="Basic"))[0] is False


def test_check_transitions_all_cases():
    check = pptx_run.check_transitions_all
    assert check(_state(-1), _state(0, transitions=50))[0] is True
    assert "у 1 слайдов из 50" in check(_state(-1), _state(0, transitions=1))[1]
    assert check(_state(-1), _state(0, transitions=None))[0] is True


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
    tests = r7_pptx_ops.PresentationOps(app, lambda: 1, lambda m: None, "s.pptx").tests()
    assert [n for n, _ in tests] == r7_pptx_ops.PRESENTATION_TEST_DEFINITIONS[1:]
    for _name, func in tests:
        func.prepare()
        func()
    names = [c[0] for c in app.calls]
    assert names.count("_editor_prepare") == len(tests)
    assert [c[1][0] for c in app.calls if c[0] == "_save_as_format"] == ["pdf", "pptx"]
    assert ("_hotkey" not in names) and ("_press" not in names)
    assert ("_pptx_cdp_add_slides", (r7_pptx_ops.SLIDES_TO_ADD,)) in app.calls
    assert ("_pptx_cdp_change_theme", (r7_pptx_ops.THEME_CANDIDATES,)) in app.calls


def test_export_names_are_non_mutating(bare_r7):
    for name in (r7_pptx_ops.EXPORT_PDF_TEST, r7_pptx_ops.EXPORT_PPTX_TEST):
        assert not bare_r7._op_expects_change(name)
    for name in r7_pptx_ops.PRESENTATION_TEST_DEFINITIONS[1:5]:
        assert bare_r7._op_expects_change(name)


# ── снимок и откат истории ───────────────────────────────────────────────

def test_history_snapshot_reads_presentation_state(pres_app):
    pres_app._webdriver_connector = PresConn(states=[_state(4, 60)])
    assert pres_app._history_snapshot() == {"index": 4, "pages": 60, "blocks": None}
    pres_app._webdriver_connector = PresConn(states=[])
    assert pres_app._history_snapshot() is None


def test_restore_undoes_with_presentation_prelude(pres_app):
    conn = PresConn(states=[_state(49, 100), _state(-1, 50)],
                    undo={"reached": True, "steps": 50, "undo_ms": 30.0})
    pres_app._webdriver_connector = conn
    waits = []
    pres_app._wait_operation_done = lambda hwnd, log_cb=None, start_grace=None: waits.append(1)
    assert pres_app._restore_history({"index": -1}, "Добавление", hwnd=1) is True
    undo_js = [js for js in conn.evals if "st.reached" in js]
    assert len(undo_js) == 1 and "target: -1" in undo_js[0]
    assert pptx_js.PPTX_API_PRELUDE in undo_js[0]
    assert waits == [1]


# ── готовность после открытия ────────────────────────────────────────────

def _fake_ready(monkeypatch, ready_at=50.0, marker="bold"):
    def fake(self, hwnd, timeout=120, log_cb=None):
        self._ready_at, self._ready_marker = ready_at, marker
        return True
    monkeypatch.setattr(ReadinessMixin, "_wait_until_r7_ready", fake)


def test_ready_waits_for_stable_slide_count(pres_app, monkeypatch):
    """Признака пересчёта нет — готово, когда число слайдов устоялось."""
    _fake_ready(monkeypatch)
    pres_app._webdriver_connector = PresConn(states=[_state(slides=s) for s in (20, 50, 50)])
    start = pres_app.clock.t
    assert pres_app._wait_until_r7_ready(lambda: 1) is True
    assert pres_app._ready_marker == "bold+layout"
    assert pres_app._ready_at == pytest.approx(start + pres_app.DOC_LAYOUT_POLL_SEC)
    assert any("слайдов 50" in m for m in pres_app.logs)


def test_ready_keeps_moment_when_slides_already_loaded(pres_app, monkeypatch):
    _fake_ready(monkeypatch)
    pres_app._webdriver_connector = PresConn(states=[_state()])
    pres_app._wait_until_r7_ready(lambda: 1)
    assert (pres_app._ready_at, pres_app._ready_marker) == (50.0, "bold")


def test_ready_probe_uses_add_slide_button(pres_app):
    probe = {"found": True, "disabled": False, "enabledAt": 1.0, "now": 2.0}
    conn = PresConn(probe=probe)
    pres_app._webdriver_connector = conn
    assert pres_app._bold_ready_probe() == probe
    assert conn.evals == [pptx_js.PPTX_READY_PROBE_JS]
    pres_app._webdriver_connector = None
    assert pres_app._bold_ready_probe() is None


def test_ready_probe_in_spreadsheet_and_document_modes_is_bold(bare_r7):
    class BoldConn:
        connected = True

        def bold_ready_probe(self, timeout=None):
            return {"found": True, "bold": True}

        def evaluate(self, js, timeout=None):
            raise AssertionError("проба презентации в чужом режиме")
    bare_r7._webdriver_connector = BoldConn()
    assert bare_r7._bold_ready_probe() == {"found": True, "bold": True}
    bare_r7._run_editor = doc_run.EDITOR_DOCUMENT
    assert bare_r7._bold_ready_probe() == {"found": True, "bold": True}


def test_ready_marker_label_names_probed_button(bare_r7):
    # Журнал называет ту кнопку, которую проверяла проба готовности.
    assert bare_r7._ready_marker_label() == "кнопка «Жирный»"
    bare_r7._run_editor = doc_run.EDITOR_PRESENTATION
    assert bare_r7._ready_marker_label() == "кнопка «Добавить слайд»"
    bare_r7._run_editor = doc_run.EDITOR_DOCUMENT
    assert bare_r7._ready_marker_label() == "кнопка «Жирный»"


# ── конец операции: пинг + устоявшееся число слайдов ─────────────────────

def test_op_end_keeps_ping_end_when_slides_stable(pres_app, monkeypatch):
    monkeypatch.setattr(OpEndMixin, "_wait_renderer_idle",
                        lambda self, log_cb=None: (105.0, "ok"))
    pres_app._webdriver_connector = PresConn(states=[_state(slides=100)])
    assert pres_app._wait_renderer_idle() == (105.0, "ok")


# ── прочее режима ────────────────────────────────────────────────────────

def test_make_run_ops_and_editor_names(pres_app):
    assert isinstance(pres_app._make_run_ops(lambda: 1, print, "s.pptx"),
                      r7_pptx_ops.PresentationOps)
    names = pres_app.editor_test_names()
    assert names["presentation"] == r7_pptx_ops.PRESENTATION_TEST_DEFINITIONS
    assert names["document"][1:] != names["presentation"][1:]
    assert set(names) == set(doc_run.EDITORS)
    pres_app._run_editor = doc_run.EDITOR_DOCUMENT
    assert type(pres_app._make_run_ops(lambda: 1, print, "d.docx")).__name__ == "DocumentOps"


def test_locate_generates_presentation_fixture(pres_app, tmp_path, monkeypatch):
    from r7 import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.chdir(tmp_path)
    pres_app.test_files_folder = tmp_path / "TestFiles"
    path = pres_app._locate_test_file()
    assert path == pres_app.test_files_folder / PPTX_FIXTURE_NAME and path.is_file()
    assert pres_app._locate_test_file() == path              # второй раз — найдена


def test_editor_prepare_waits_count_then_selects_first_slide(pres_app):
    conn = PresConn(states=[_state(slides=40), _state(), _state()])
    pres_app._webdriver_connector = conn
    pres_app._editor_prepare()
    assert conn.evals[-1] == pptx_js.PPTX_FIRST_SLIDE_JS
    assert conn.evals.count(pptx_js.PPTX_STATE_JS) == 3


def test_api_info_logged_with_slides_and_themes(pres_app):
    pres_app._webdriver_connector = PresConn(op={"found": True, "frame": 1, "themes": 12,
                                                 "state": _state(), "methods": {"AddSlide": True,
                                                                                "Undo": False}})
    pres_app._cdp_ensure_connected = lambda log_cb=None: True
    pres_app._cdp_log_api_info()
    line = next(m for m in pres_app.logs if "api презентации найден" in m)
    assert "слайдов 50" in line and "тем в редакторе 12" in line
    assert any("нет методов: Undo" in m for m in pres_app.logs)


def test_autosave_suspend_and_restore_in_presentation_mode(pres_app):
    conn = PresConn(op={"gap_ms": 1000, "periodic": True})
    pres_app._webdriver_connector = conn
    pres_app._suspend_autosave()
    assert conn.evals[-1] == pptx_js.PPTX_SUSPEND_AUTOSAVE_JS
    assert pres_app._autosave_state == {"gap_ms": 1000, "periodic": True}
    conn.op = True
    pres_app._restore_autosave()
    assert pptx_js.PPTX_API_PRELUDE in conn.evals[-1]
    assert pres_app._autosave_state is None
