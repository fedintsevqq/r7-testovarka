"""JS текстового редактора (r7/doc_js.py) в Node с фейковым api документа.

Проверяется то, что не видно поиском подстрок: api находится во фрейме,
mutated выставляется только перед изменяющим вызовом, снимок состояния
читает страницы, блоки, историю и признак вёрстки, откат останавливается
на нужной точке. Нет Node — тесты пропускаются.
"""
import json
import shutil
import subprocess

import pytest

from r7 import doc_js

NODE = shutil.which("node")
# Холодный запуск Node на раннере CI под покрытием доходил до 30 с (#134).
NODE_TIMEOUT_SEC = 120
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js не установлен")

# Окно верхнего уровня без api и iframe глубины 1 с editor (как на живом Р7
# 2026.3.2). Документ: Content — блоки, History — точки, FullRecalc — вёрстка.
PRELUDE = r"""
function makeDoc(o) {
  o = o || {};
  var d = {
    Content: new Array(o.blocks === undefined ? 200 : o.blocks).fill({}),
    History: { Index: o.index === undefined ? -1 : o.index, Points: [] },
    FullRecalc: o.noRecalcField ? {} : { Id: o.recalcId === undefined ? null : o.recalcId },
    Styles: { GetStyleIdByName: function (n) { return n === 'Heading 2' ? 'Heading2' : null; } },
    cursor: 'middle', selected: false, styled: null,
    RemoveSelection: function () { d.selected = false; },
    SelectAll: function () { d.selected = true; },
    MoveCursorToStartPos: function () { d.cursor = 'start'; },
  };
  return d;
}
function point(d) { d.History.Index += 1; d.History.Points.push({}); }
function makeEditor(o) {
  o = o || {};
  var d = makeDoc(o), pages = o.pages || 4;
  var api = {
    WordControl: { m_oLogicDocument: d },
    getCountPages: function () { return pages; },
    asc_getCanUndo: function () { return d.History.Index >= 0; },
    Undo: function () { if (!o.undoStuck && d.History.Index >= 0) d.History.Index -= 1; },
    asc_AddBlankPage: function () { point(d); pages += 1; d.Content.push({}); },
    put_Style: function (n) {
      if (o.styleThrows) throw new Error('style failed');
      d.styled = n; point(d);
    },
    asc_replaceText: function () { api.replaceArgs = Array.prototype.slice.call(arguments);
                                   point(d); return 7; },
    autoSaveGap: 1000,
    asc_setAutoSaveGap: function (v) { api.autoSaveGap = v; },
  };
  if (o.noSelectAll) delete d.SelectAll;
  return api;
}
function frameWin(api) {
  var w = { editor: api, AscCommon: {} };
  w.document = { querySelectorAll: function () { return []; } };
  return w;
}
globalThis.window = globalThis;
globalThis.performance = globalThis.performance || { now: function () { return Date.now(); } };
function install(api, extra) {
  var fw = frameWin(api);
  if (extra) extra(fw);
  globalThis.__fw = fw;
  globalThis.document = { querySelectorAll: function (s) {
    return s === 'iframe' ? [{ contentWindow: fw }] : []; } };
  globalThis.requestAnimationFrame = undefined;
}
"""


def run_js(setup, expr):
    """setup и выражение expr в Node → JSON-результат."""
    script = PRELUDE + setup + "\nconst __r = (" + expr.strip().rstrip(";") + ");\n" \
        "console.log(JSON.stringify({result: __r === undefined ? null : __r,\n" \
        "  doc: globalThis.__api ? { styled: __api.WordControl.m_oLogicDocument.styled,\n" \
        "    cursor: __api.WordControl.m_oLogicDocument.cursor,\n" \
        "    selected: __api.WordControl.m_oLogicDocument.selected,\n" \
        "    replaceArgs: __api.replaceArgs || null, gap: __api.autoSaveGap } : null}));\n"
    proc = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True,
                          encoding="utf-8", timeout=NODE_TIMEOUT_SEC)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _setup(opts="{}", extra="null"):
    return f"globalThis.__api = makeEditor({opts}); install(__api, {extra});"


def test_state_found_in_frame_with_pages_blocks_history():
    st = run_js(_setup("{pages: 9, blocks: 120, index: 3}"), doc_js.DOC_STATE_JS)["result"]
    assert st == {"pages": 9, "blocks": 120, "historyIndex": 3, "historyPoints": 0,
                  "canUndo": True, "recalcBusy": False}


def test_state_recalc_busy_and_unknown():
    busy = run_js(_setup("{recalcId: 17}"), doc_js.DOC_STATE_JS)["result"]
    assert busy["recalcBusy"] is True
    unknown = run_js(_setup("{noRecalcField: true}"), doc_js.DOC_STATE_JS)["result"]
    assert unknown["recalcBusy"] is None


def test_spreadsheet_like_api_is_not_a_document():
    """api без WordControl/getCountPages (табличный) документом не считается."""
    setup = ("globalThis.__api = null; install({ asc_EditSelectAll: function () {} });")
    assert run_js(setup, doc_js.DOC_STATE_JS)["result"] is None


def test_add_pages_one_call_marks_mutated_and_moves_history():
    out = run_js(_setup("{pages: 4, index: -1}"), doc_js.add_blank_pages_js(100))
    res = out["result"]
    assert res["ok"] is True and res["mutated"] is True and res["added"] == 100
    assert res["before"]["pages"] == 4 and res["after"]["pages"] == 104
    assert res["after"]["historyIndex"] == 99
    assert isinstance(res["api_ms"], (int, float))


def test_add_pages_without_method_is_untouched():
    setup = _setup() + " delete __api.asc_AddBlankPage;"
    res = run_js(setup, doc_js.add_blank_pages_js(5))["result"]
    assert res["ok"] is False and res["mutated"] is False
    assert res["reason"] == "no-method:asc_AddBlankPage"


def test_restyle_selects_all_then_applies_known_style_name():
    out = run_js(_setup(), doc_js.restyle_all_js(["heading 2", "Heading 2"]))
    res = out["result"]
    assert res["ok"] is True and res["mutated"] is True and res["style"] == "Heading 2"
    assert out["doc"]["selected"] is True and out["doc"]["styled"] == "Heading 2"


def test_restyle_without_select_all_does_not_touch_document():
    res = run_js(_setup("{noSelectAll: true}"), doc_js.restyle_all_js(["Heading 2"]))["result"]
    assert res["ok"] is False and res["mutated"] is False
    assert res["reason"] == "no-method:SelectAll"


def test_restyle_failure_after_mutation_reports_mutated():
    """put_Style упал — документ мог измениться: mutated=true, клавишами не повторять."""
    res = run_js(_setup("{styleThrows: true}"), doc_js.restyle_all_js(["Heading 2"]))["result"]
    assert res["ok"] is False and res["mutated"] is True and res["reason"] == "exception"


def test_replace_uses_search_settings_when_available():
    extra = ("function (fw) { fw.AscCommon.CSearchSettings = function () {"
             " this.put_Text = function (t) { this.text = t; };"
             " this.put_MatchCase = function (m) { this.mc = m; }; }; }")
    out = run_js(_setup(extra=extra), doc_js.replace_all_js("квартал", "период"))
    res = out["result"]
    assert res["ok"] is True and res["mutated"] is True and res["signature"] == "settings"
    args = out["doc"]["replaceArgs"]
    assert args[0] == {"text": "квартал", "mc": False} and args[1:] == ["период", True]


def test_replace_legacy_signature():
    out = run_js(_setup(), doc_js.replace_all_js("квартал", "период"))
    assert out["result"]["signature"] == "legacy"
    assert out["doc"]["replaceArgs"] == ["квартал", "период", True, False]


def test_undo_to_reaches_target_and_stops_when_stuck():
    res = run_js(_setup("{index: 7}"), doc_js.undo_to_js(2, 50))["result"]
    assert res["reached"] is True and res["steps"] == 5 and res["after"]["historyIndex"] == 2
    stuck = run_js(_setup("{index: 7, undoStuck: true}"), doc_js.undo_to_js(2, 50))["result"]
    assert stuck["reached"] is False and stuck["stuck"] is True


def test_cursor_to_start_outside_history():
    out = run_js(_setup("{index: 4}"), doc_js.DOC_CURSOR_START_JS)
    assert out["result"]["ok"] is True and out["result"]["state"]["historyIndex"] == 4
    assert out["doc"]["cursor"] == "start" and out["doc"]["selected"] is False


def test_api_info_lists_methods():
    info = run_js(_setup(), doc_js.doc_api_info_js())["result"]
    assert info["found"] is True and info["frame"] == 1
    assert info["methods"]["asc_AddBlankPage"] is True
    assert info["methods"]["asc_EditSelectAll"] is False


def test_autosave_suspend_and_restore():
    out = run_js(_setup(), doc_js.DOC_SUSPEND_AUTOSAVE_JS)
    assert out["result"] == {"gap_ms": 1000, "periodic": None} and out["doc"]["gap"] == 0
    back = run_js(_setup() + " __api.autoSaveGap = 0;",
                  doc_js.restore_autosave_js({"gap_ms": 1000, "periodic": False}))
    assert back["result"] is True and back["doc"]["gap"] == 1.0
