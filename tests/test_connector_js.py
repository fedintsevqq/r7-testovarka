"""Исполнение JavaScript коннектора в Node с фейковым DOM и api редактора
(QA-аудит 29.09.2026, G-07).

Раньше ~1000 строк JS внутри строковых констант r7_webdriver_connector.py
проверялись только поиском подстрок: синтаксическую ошибку, перепутанный
порядок «mutated = true» и изменяющего вызова (от него зависит, разрешён ли
откат на клавиши — иначе правка применится дважды) или ошибку в вычитании
базового снимка такие тесты не ловят. Здесь JS реально выполняется, и
проверяется результат.

Фейки минимальные, без jsdom: объекты с querySelectorAll,
getBoundingClientRect, textContent и getComputedStyle — ровно то, чем
пользуется JS коннектора. Нет Node — тесты пропускаются.
"""
import json
import shutil
import subprocess

import pytest

import r7_webdriver_connector as wd

NODE = shutil.which("node")
# Холодный запуск Node на раннере CI под покрытием доходил до 30 с (#134).
NODE_TIMEOUT_SEC = 120
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js не установлен")

# Фейковое окружение браузера: элементы, документ, окна и api редактора.
PRELUDE = r"""
globalThis.__clicks = [];
function El(tag, text, o) {
  o = o || {};
  this.tagName = tag.toUpperCase(); this.textContent = text || '';
  this.className = o.cls || ''; this.id = o.id || '';
  this.style = { display: o.display || '' };
  this.offsetParent = o.hidden ? null : {};
  this._fixed = !!o.fixed; this._hidden = !!o.hidden;
  this._rect = o.rect || { left: 10, top: 10, width: 60, height: 12 };
  this._attrs = o.attrs || {}; this._kids = o.kids || [];
  this.innerText = o.innerText || text || '';
}
El.prototype.getBoundingClientRect = function () { return this._rect; };
El.prototype.click = function () { __clicks.push(this.textContent); };
El.prototype.getAttribute = function (n) { return (n in this._attrs) ? this._attrs[n] : null; };
El.prototype.querySelectorAll = function (sel) {
  if (sel === 'button') return this._kids.filter(function (k) { return k.tagName === 'BUTTON'; });
  return [];
};
function Doc(nodes, frames) {
  this._nodes = nodes || []; this._frames = frames || [];
  var self = this;
  this.defaultView = { getComputedStyle: styleOf };
  this._nodes.forEach(function (n) { n.ownerDocument = self; });
}
Doc.prototype.querySelectorAll = function (sel) {
  if (sel === 'iframe') return this._frames;
  if (sel === '.asc-window.alert') return this._nodes.filter(function (n) { return /asc-window/.test(n.className) && /alert/.test(n.className); });
  return this._nodes;
};
function styleOf(el) {
  return { display: el._hidden ? 'none' : (el.style.display || 'block'),
           visibility: 'visible', opacity: '1', position: el._fixed ? 'fixed' : 'static' };
}
globalThis.getComputedStyle = styleOf;
globalThis.window = globalThis;
globalThis.document = new Doc([], []);
function makeApi(opts) {
  opts = opts || {};
  var H = { Index: opts.index === undefined ? -1 : opts.index, Points: [],
            Can_Undo: function () { return H.Index >= 0; } };
  var api = {
    asc_EditSelectAll: function () {},
    asc_getWorksheetsCount: function () { return 1; },
    asc_getActiveWorksheetIndex: function () { return 0; },
    asc_getActiveRangeStr: function () { return 'A1'; },
  };
  if (!opts.noPaste) api.asc_Paste = function () {
    if (opts.pasteThrows) throw new Error('paste failed');
    H.Index += 1; H.Points.push({}); return true;
  };
  api.asc_Undo = function () { if (!opts.undoStuck && H.Index >= 0) H.Index -= 1; };
  window.AscCommon = { History: H };
  window.Asc = { editor: api };
  return { api: api, H: H };
}
"""


def run_js(setup, expr):
    """Выполняет setup (JS) и выражение expr в Node, возвращает JSON-результат."""
    script = PRELUDE + setup + "\nconst __r = (" + expr.strip().rstrip(";") + ");\n" \
        "console.log(JSON.stringify({result: __r === undefined ? null : __r, clicks: __clicks}));\n"
    proc = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True,
                          encoding="utf-8", timeout=NODE_TIMEOUT_SEC)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ── _op_js: флаг mutated, api_ms, отказы ─────────────────────────────────

def test_paste_op_success_moves_history_and_times_call():
    out = run_js("makeApi({index: 2});", wd._PASTE_JS)["result"]
    assert out["ok"] is True and out["mutated"] is True
    assert out["before"]["historyIndex"] == 2 and out["after"]["historyIndex"] == 3
    assert isinstance(out["api_ms"], (int, float)) and out["api_ms"] >= 0


def test_paste_op_exception_after_mutation_forbids_keyboard_fallback():
    """mutated выставляется ДО вызова: исключение внутри asc_Paste означает,
    что документ мог измениться, и повтор клавишами запрещён."""
    out = run_js("makeApi({pasteThrows: true});", wd._PASTE_JS)["result"]
    assert out["ok"] is False and out["mutated"] is True
    assert out["reason"] == "exception" and "paste failed" in out["error"]
    assert "api_ms" in out                       # время до сбоя сохраняется


def test_op_without_api_reports_not_found():
    out = run_js("", wd._PASTE_JS)["result"]
    assert out == {"ok": False, "mutated": False, "reason": "api-not-found"}


def test_op_without_method_does_not_mutate():
    out = run_js("makeApi({noPaste: true});", wd._PASTE_JS)["result"]
    assert out["reason"] == "no-method:asc_Paste" and out["mutated"] is False
    assert "api_ms" not in out                   # операция не выполнялась — мерить нечего


def test_api_found_inside_iframe():
    setup = """
      var inner = {document: new Doc([], [])};
      var saved = makeApi({index: 0});
      delete window.Asc;
      inner.Asc = {editor: saved.api}; inner.AscCommon = window.AscCommon;
      document = new Doc([], [{contentWindow: inner}]);
      globalThis.document = document;
    """
    out = run_js(setup, wd._PASTE_JS)["result"]
    assert out["ok"] is True and out["frame"] == 1


# ── _undo_to_js: откат до позиции истории ────────────────────────────────

def test_undo_to_target_index():
    out = run_js("makeApi({index: 4});", wd._undo_to_js(1, 50))["result"]
    assert out["steps"] == 3 and out["reached"] is True
    assert out["after"]["historyIndex"] == 1


def test_undo_stops_when_stuck():
    out = run_js("makeApi({index: 4, undoStuck: true});", wd._undo_to_js(1, 50))["result"]
    assert out["stuck"] is True and out["reached"] is False and out["steps"] == 1


# ── _click_by_text_js: точное совпадение, базовый снимок, видимость ───────

def _menu_setup(items):
    js_items = ",".join(
        "new El('li', %s, %s)" % (json.dumps(t), json.dumps(o)) for t, o in items)
    return "document = new Doc([%s], []); globalThis.document = document;" % js_items


def test_click_prefers_exact_label_over_substring():
    setup = _menu_setup([("Копировать формат", {}), ("Копировать", {"rect": {"left": 10, "top": 40, "width": 60, "height": 12}})])
    res = run_js(setup, wd._click_by_text_js(["копировать"]))
    assert res["clicks"] == ["Копировать"] and res["result"]["exact"] is True


def test_click_skips_baseline_items():
    """Статичное overflow-меню тулбара (в базовом снимке) не кликается, даже
    с точной подписью — клик уходит свежему пункту контекстного меню."""
    base = [{"text": "Вставить", "tag": "li", "id": "", "cls": "", "x": 10, "y": 10}]
    setup = _menu_setup([("Вставить", {}), ("Вставить ячейки", {"rect": {"left": 300, "top": 200, "width": 60, "height": 12}})])
    res = run_js(setup, wd._click_by_text_js(["вставить"], baseline=base))
    assert res["clicks"] == ["Вставить ячейки"]


def test_click_ignores_hidden_items():
    setup = _menu_setup([("Копировать", {"hidden": True}), ("Копировать ячейки", {})])
    res = run_js(setup, wd._click_by_text_js(["копировать"]))
    assert res["clicks"] == ["Копировать ячейки"]


def test_click_nothing_matched():
    res = run_js(_menu_setup([("Удалить", {})]), wd._click_by_text_js(["копировать"]))
    assert res["clicks"] == [] and res["result"]["clicked"] is False


# ── dismiss_info_alert: только окна с одной кнопкой OK ───────────────────

def _alert_js(connector_method):
    sent = []
    c = wd.R7WebDriverConnector(port=1, log_cb=lambda *_: None)
    c.evaluate = lambda js, timeout=None: sent.append(js)
    connector_method(c)
    return sent[0]


def test_info_alert_single_ok_is_clicked():
    setup = """
      var ok = new El('button', 'OK', {attrs: {result: 'ok'}});
      var w = new El('div', 'Нельзя сохранить', {cls: 'asc-window modal alert', fixed: true, hidden: false, kids: [ok]});
      w.offsetParent = null;  // position: fixed — offsetParent всегда null
      document = new Doc([w], []); globalThis.document = document;
    """
    res = run_js(setup, _alert_js(lambda c: c.dismiss_info_alert()))
    assert res["clicks"] == ["OK"] and res["result"]["clicked"] is True


def test_info_alert_with_choice_is_not_touched():
    setup = """
      var y = new El('button', 'Да', {attrs: {result: 'yes'}});
      var n = new El('button', 'Нет', {attrs: {result: 'no'}});
      var w = new El('div', 'Включить режим вручную?', {cls: 'asc-window modal alert', kids: [y, n]});
      document = new Doc([w], []); globalThis.document = document;
    """
    res = run_js(setup, _alert_js(lambda c: c.dismiss_info_alert()))
    assert res["clicks"] == [] and res["result"]["clicked"] is False


def test_heavy_calc_prompt_answers_no():
    setup = """
      var y = new El('button', 'Да', {attrs: {result: 'yes'}});
      var n = new El('button', 'Нет', {attrs: {result: 'no'}});
      var w = new El('div', 'Автоматический пересчёт может занять время.', {cls: 'asc-window modal alert', kids: [y, n]});
      w.querySelector = function (s) { return /no/.test(s) ? n : null; };
      document = new Doc([w], []); globalThis.document = document;
    """
    res = run_js(setup, _alert_js(lambda c: c.dismiss_heavy_calc_prompt()))
    assert res["clicks"] == ["Нет"] and res["result"]["clicked"] is True


# ── _delete_sheet_js: удаление только активного листа ────────────────────

def test_delete_sheet_only_when_active():
    setup = ("var m = makeApi({index: 3}); globalThis.__del = [];"
             "m.api.asc_getActiveWorksheetIndex = function () { return 2; };"
             "m.api.asc_deleteWorksheet = function (a) { __del.push(a); };")
    out = run_js(setup, wd._delete_sheet_js(2))["result"]
    assert out["ok"] is True and out["mutated"] is True and out["method"] == "asc_deleteWorksheet"
    other = run_js(setup, wd._delete_sheet_js(1))["result"]
    assert not other.get("ok") and other["reason"] == "not-active" and not other.get("mutated")


def test_clear_history_empties_history_without_mutation():
    out = run_js("var m = makeApi({index: 3}); m.H.Clear = function () { m.H.Index = -1; m.H.Points = []; };",
                 wd._CLEAR_HISTORY_JS)["result"]
    assert out["ok"] is True and not out.get("mutated")
    assert out["after"]["historyIndex"] == -1
    bare = run_js("makeApi({index: 3});", wd._CLEAR_HISTORY_JS)["result"]
    assert not bare.get("ok") and bare["reason"] == "no-history"
