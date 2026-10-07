"""JS редактора презентаций (r7/pptx_js.py) в Node с фейковым api презентации.

Как tests/test_doc_js.py: api находится во фрейме и отличается от api
документа, mutated выставляется только перед изменяющим вызовом, снимок
читает слайды, историю, тему и переходы, откат останавливается на нужной
точке, маркер готовности — кнопка «Добавить слайд». Нет Node — пропуск.
"""
import json
import shutil
import subprocess

import pytest

from r7 import doc_js, pptx_js

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js не установлен")

# Окно верхнего уровня без api и iframe глубины 1 с editor (как на живом Р7
# 2026.3.2). Презентация: Slides с timing, History, тема мастера.
PRELUDE = r"""
function makePres(o) {
  o = o || {};
  var d = {
    Slides: [], History: { Index: o.index === undefined ? -1 : o.index, Points: [] },
    slideMasters: [{ Theme: { name: o.theme || 'R7 Testovarka' } }],
    current: 3, selection: 'shape',
    RemoveSelection: function () { d.selection = null; },
  };
  for (var i = 0; i < (o.slides === undefined ? 5 : o.slides); i++)
    d.Slides.push(o.noTiming ? {} : { timing: { TransitionType: 0 } });
  return d;
}
function point(d) { d.History.Index += 1; d.History.Points.push({}); }
function makeEditor(o) {
  o = o || {};
  var d = makePres(o);
  var api = {
    WordControl: { m_oLogicDocument: d, GoToPage: function (i) { d.current = i; } },
    getCountPages: function () { return d.Slides.length; },
    asc_getCanUndo: function () { return d.History.Index >= 0; },
    Undo: function () { if (!o.undoStuck && d.History.Index >= 0) d.History.Index -= 1; },
    AddSlide: function () {
      d.Slides.splice(d.current + 1, 0, { timing: { TransitionType: 0 } }); point(d);
    },
    SelectAllSlides: function () { api.allSelected = true; },
    DublicateSlide: function () {
      var n = api.allSelected ? d.Slides.length : 1;
      for (var i = 0; i < n; i++) d.Slides.push({ timing: { TransitionType: 0 } });
      point(d);
    },
    ThemeLoader: { Themes: { EditorThemes: [{ Name: 'Blank' }, { Name: 'Basic' },
                                            { Name: 'Classic' }] } },
    ChangeTheme: function (i) {
      if (o.themeThrows) throw new Error('theme failed');
      api.themeIdx = i;
      d.slideMasters[0].Theme = { name: api.ThemeLoader.Themes.EditorThemes[i].Name };
      point(d);
    },
    ApplySlideTransition: function (tr) {
      api.lastTr = tr;
      for (var i = 0; i < d.Slides.length; i++)
        if (api.allSelected || i === d.current) d.Slides[i].timing.TransitionType = tr.TransitionType;
      point(d);
    },
    autoSaveGap: 1000,
    asc_setAutoSaveGap: function (v) { api.autoSaveGap = v; },
  };
  if (o.noThemes) delete api.ThemeLoader;
  return api;
}
function Transition() {
  this.setDefaultParams = function () { this.TransitionType = 0; this.TransitionDuration = 0; };
  this.put_TransitionType = function (v) { this.TransitionType = v; };
  this.put_TransitionOption = function (v) { this.TransitionOption = v; };
  this.put_TransitionDuration = function (v) { this.TransitionDuration = v; };
}
function frameWin(api, noClass) {
  var w = { editor: api, AscCommon: {},
            Asc: noClass ? {} : { CAscSlideTransition: Transition,
                                  c_oAscSlideTransitionTypes: { Fade: 1 } } };
  w.document = { querySelectorAll: function () { return []; } };
  return w;
}
globalThis.window = globalThis;
globalThis.performance = globalThis.performance || { now: function () { return Date.now(); } };
function install(api, noClass) {
  var fw = frameWin(api, noClass);
  globalThis.__fw = fw;
  globalThis.document = { querySelectorAll: function (s) {
    return s === 'iframe' ? [{ contentWindow: fw }] : []; } };
  globalThis.requestAnimationFrame = undefined;
}
"""


def run_js(setup, expr):
    """setup и выражение expr в Node → {result, pres} (pres — презентация после)."""
    script = PRELUDE + setup + "\nconst __r = (" + expr.strip().rstrip(";") + ");\n" \
        "const __d = globalThis.__api ? __api.WordControl.m_oLogicDocument : null;\n" \
        "console.log(JSON.stringify({result: __r === undefined ? null : __r,\n" \
        "  pres: __d ? { slides: __d.Slides.length, current: __d.current,\n" \
        "    selection: __d.selection, theme: __d.slideMasters[0].Theme.name,\n" \
        "    themeIdx: __api.themeIdx === undefined ? null : __api.themeIdx,\n" \
        "    allSelected: !!__api.allSelected, tr: __api.lastTr || null,\n" \
        "    gap: __api.autoSaveGap } : null}));\n"
    proc = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True,
                          encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _setup(opts="{}", no_class=False):
    return (f"globalThis.__api = makeEditor({opts}); "
            f"install(__api, {'true' if no_class else 'false'});")


# ── поиск api и снимок ───────────────────────────────────────────────────

def test_state_found_in_frame_with_slides_history_theme_transitions():
    st = run_js(_setup("{slides: 7, index: 2}"), pptx_js.PPTX_STATE_JS)["result"]
    assert st == {"pages": 7, "slides": 7, "historyIndex": 2, "historyPoints": 0,
                  "canUndo": True, "recalcBusy": None, "theme": "R7 Testovarka",
                  "transitions": 0}


def test_state_transitions_unknown_without_timing():
    st = run_js(_setup("{noTiming: true}"), pptx_js.PPTX_STATE_JS)["result"]
    assert st["transitions"] is None and st["slides"] == 5


def test_document_prelude_rejects_presentation_and_back():
    """У обоих редакторов WordControl и getCountPages — различает Slides."""
    assert run_js(_setup(), doc_js.DOC_STATE_JS)["result"] is None
    doc_api = ("globalThis.__api = null; install({ getCountPages: function () { return 3; },"
               " WordControl: { m_oLogicDocument: { Content: [], History: { Index: -1 } } } });")
    assert run_js(doc_api, pptx_js.PPTX_STATE_JS)["result"] is None
    assert run_js(doc_api, doc_js.DOC_STATE_JS)["result"]["pages"] == 3


def test_api_info_lists_methods_and_themes():
    info = run_js(_setup(), pptx_js.pptx_api_info_js())["result"]
    assert info["found"] is True and info["frame"] == 1 and info["themes"] == 3
    assert info["methods"]["AddSlide"] is True and info["methods"]["DublicateSlide"] is True
    assert info["methods"]["SlideTransitionApplyToAll"] is False


def test_first_slide_prepare_outside_history():
    out = run_js(_setup("{index: 4}"), pptx_js.PPTX_FIRST_SLIDE_JS)
    assert out["result"]["ok"] is True and out["result"]["state"]["historyIndex"] == 4
    assert out["pres"]["current"] == 0 and out["pres"]["selection"] is None


# ── операции ─────────────────────────────────────────────────────────────

def test_add_slides_one_call_marks_mutated_and_moves_history():
    res = run_js(_setup("{slides: 50}"), pptx_js.add_slides_js(50))["result"]
    assert res["ok"] is True and res["mutated"] is True and res["added"] == 50
    assert res["before"]["slides"] == 50 and res["after"]["slides"] == 100
    assert res["after"]["historyIndex"] == 49
    assert isinstance(res["api_ms"], (int, float))


def test_add_slides_without_method_is_untouched():
    res = run_js(_setup() + " delete __api.AddSlide;", pptx_js.add_slides_js(3))["result"]
    assert res["ok"] is False and res["mutated"] is False
    assert res["reason"] == "no-method:AddSlide"


def test_duplicate_selects_all_then_duplicates():
    out = run_js(_setup("{slides: 50}"), pptx_js.duplicate_all_js())
    res = out["result"]
    assert res["ok"] is True and res["mutated"] is True and res["method"] == "DublicateSlide"
    assert out["pres"]["allSelected"] is True and res["after"]["slides"] == 100


def test_duplicate_without_select_all_does_not_touch():
    setup = _setup() + " delete __api.SelectAllSlides;"
    res = run_js(setup, pptx_js.duplicate_all_js())["result"]
    assert res["ok"] is False and res["mutated"] is False
    assert res["reason"] == "no-method:SelectAllSlides"


def test_theme_picks_first_candidate_with_other_name():
    out = run_js(_setup("{theme: 'Basic'}"), pptx_js.change_theme_js([1, 2]))
    res = out["result"]
    assert res["ok"] is True and res["mutated"] is True
    assert (res["theme_index"], res["theme_name"], res["themes"]) == (2, "Classic", 3)
    assert out["pres"]["theme"] == "Classic" and res["after"]["historyIndex"] == 0


def test_theme_skips_indexes_beyond_list_and_fails_untouched_without_any():
    res = run_js(_setup(), pptx_js.change_theme_js([7, 1]))["result"]
    assert res["theme_index"] == 1
    none = run_js(_setup(), pptx_js.change_theme_js([7, 9]))["result"]
    assert none["ok"] is False and none["mutated"] is False and none["reason"] == "no-theme"


def test_theme_list_unreadable_uses_first_candidate():
    out = run_js(_setup("{noThemes: true}") + " __api.ThemeLoader = undefined;"
                 " __api.ChangeTheme = function (i) { __api.themeIdx = i;"
                 " __api.WordControl.m_oLogicDocument.History.Index += 1; };",
                 pptx_js.change_theme_js([2, 1]))
    assert out["result"]["ok"] is True and out["result"]["themes"] is None
    assert out["pres"]["themeIdx"] == 2


def test_theme_failure_after_mutation_reports_mutated():
    res = run_js(_setup("{themeThrows: true}"), pptx_js.change_theme_js([1]))["result"]
    assert res["ok"] is False and res["mutated"] is True and res["reason"] == "exception"


def test_transition_fade_applied_to_all_slides():
    out = run_js(_setup("{slides: 6}"), pptx_js.apply_transition_all_js(700))
    res = out["result"]
    assert res["ok"] is True and res["mutated"] is True and res["transition_type"] == 1
    assert res["after"]["transitions"] == 6 and res["before"]["transitions"] == 0
    assert out["pres"]["tr"]["TransitionDuration"] == 700
    assert out["pres"]["tr"]["TransitionOption"] == 0


def test_transition_without_class_does_not_touch():
    res = run_js(_setup(no_class=True), pptx_js.apply_transition_all_js())["result"]
    assert res["ok"] is False and res["mutated"] is False
    assert res["reason"] == "no-class:CAscSlideTransition"


# ── откат, автосохранение ────────────────────────────────────────────────

def test_undo_to_reaches_target_and_stops_when_stuck():
    res = run_js(_setup("{index: 7}"), pptx_js.undo_to_js(2, 50))["result"]
    assert res["reached"] is True and res["steps"] == 5 and res["after"]["historyIndex"] == 2
    stuck = run_js(_setup("{index: 7, undoStuck: true}"), pptx_js.undo_to_js(2, 50))["result"]
    assert stuck["reached"] is False and stuck["stuck"] is True


def test_autosave_suspend_and_restore():
    out = run_js(_setup(), pptx_js.PPTX_SUSPEND_AUTOSAVE_JS)
    assert out["result"] == {"gap_ms": 1000, "periodic": None} and out["pres"]["gap"] == 0
    back = run_js(_setup() + " __api.autoSaveGap = 0;",
                  pptx_js.restore_autosave_js({"gap_ms": 1000, "periodic": False}))
    assert back["result"] is True and back["pres"]["gap"] == 1.0


# ── маркер готовности: кнопка «Добавить слайд» ───────────────────────────

_DOM = r"""
function Btn(id, cls, tag) {
  this.id = id; this.tagName = tag || 'BUTTON'; this.className = cls || ''; this.attrs = {};
  this.getAttribute = function (n) { return n in this.attrs ? this.attrs[n] : null; };
}
var observed = [];
globalThis.MutationObserver = function (cb) {
  this.observe = function (el) { observed.push(el.id); };
};
function installDom(btn) {
  globalThis.document = {
    querySelector: function (sel) {
      if (!btn) return null;
      if (sel.indexOf('toolbar-btn-bold') !== -1) return null;
      return sel.indexOf('#' + btn.id) !== -1 ? btn : null;
    },
    querySelectorAll: function () { return []; },
    defaultView: globalThis,
  };
  if (btn) btn.ownerDocument = globalThis.document;
}
"""


def test_ready_probe_watches_add_slide_button_not_bold():
    js = pptx_js.PPTX_READY_PROBE_JS
    assert "add-slide" in js and "toolbar-btn-bold" not in js and "__r7Bold" not in js
    enabled = run_js(_DOM + "installDom(new Btn('id-toolbar-button-add-slide'));"
                     " globalThis.__api = null;", js)["result"]
    assert enabled["found"] is True and not enabled["disabled"] and enabled["fresh"] is True
    disabled = run_js(_DOM + "installDom(new Btn('id-toolbar-button-add-slide', 'btn disabled'));"
                      " globalThis.__api = null;", js)["result"]
    assert disabled["found"] is True and disabled["disabled"] is True
    missing = run_js(_DOM + "installDom(null); globalThis.__api = null;", js)["result"]
    assert missing["found"] is False
