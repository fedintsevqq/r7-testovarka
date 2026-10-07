"""JS для текстового редактора Р7 (документы .docx) через CDP — этап 5, пункт 1.

Пролог табличного редактора (_API_PRELUDE в r7_webdriver_connector) ищет api
по asc_EditSelectAll — признаку Asc.spreadsheet_api. У документа свой api:
ПРОВЕРЕНО НА ЖИВОМ Р7 2026.3.2 (07.10.2026, сгенерированный docx на 200
абзацев): `w.editor` (он же `w.Asc.editor`) в iframe глубины 1,
`getCountPages()`, `WordControl.m_oLogicDocument` с `.Content.length` и
`.History.Index`, `Undo`, `asc_AddBlankPage`, `put_Style`, `asc_replaceText`,
`asc_getCanUndo`. Поэтому здесь свой apiOf: api с WordControl и
getCountPages, а табличный пролог не трогается.

Операции собираются тем же _op_js, что и у таблиц: те же поля ok / mutated /
api_ms / before / after и отметка __uxMark. Правило 7 CLAUDE.md: mutated
выставляется непосредственно перед первым изменяющим вызовом, изменяющий
вызов — последний.

Не подтверждено живым прогоном (проверить перед доверием цифрам, см.
docs/document-ops.md): поле FullRecalc.Id (признак незаконченной вёрстки),
сигнатура asc_replaceText(CSearchSettings, текст, всё) и перемещение
курсора MoveCursorToStartPos. Каждое из них читается осторожно: нет поля —
null, и вызывающий код переходит на запасной признак.
"""
import json

from r7_webdriver_connector import _FRAME_WALK_JS, _need, _op_js

# Документ — это api с вёрсткой страниц. Кандидаты — в порядке, в котором
# их видел живой прогон; DE-контроллер — путь прежних сборок (Н6).
DOC_API_PRELUDE = r"""
  function apiOf(win) {
    var cands = [];
    try { if (win.editor) cands.push(win.editor); } catch (e) {}
    try { if (win.Asc && win.Asc.editor) cands.push(win.Asc.editor); } catch (e) {}
    try {
      if (win.DE && win.DE.getController) cands.push(win.DE.getController('Main').api);
    } catch (e) {}
    for (var i = 0; i < cands.length; i++) {
      try {
        var a = cands[i];
        if (a && typeof a.getCountPages === 'function' && a.WordControl
            && a.WordControl.m_oLogicDocument) return a;
      } catch (e) {}
    }
    return null;
  }
""" + _FRAME_WALK_JS + r"""
  function logicDoc(api) {
    try { return api.WordControl.m_oLogicDocument; } catch (e) { return null; }
  }
  // Снимок документа: страницы, блоки верхнего уровня (абзацы и таблицы),
  // история правок, идёт ли ещё вёрстка. Только синхронные геттеры.
  function docState(api, win) {
    var st = { pages: null, blocks: null, historyIndex: null, historyPoints: null,
               canUndo: null, recalcBusy: null };
    var d = logicDoc(api);
    try { st.pages = api.getCountPages(); } catch (e) {}
    try { if (d && d.Content) st.blocks = d.Content.length; } catch (e) {}
    try {
      var H = (d && d.History) || (win.AscCommon && win.AscCommon.History);
      if (H) {
        if (typeof H.Index === 'number') st.historyIndex = H.Index;
        if (H.Points && typeof H.Points.length === 'number') st.historyPoints = H.Points.length;
      }
    } catch (e) {}
    try { if (typeof api.asc_getCanUndo === 'function') st.canUndo = !!api.asc_getCanUndo(); } catch (e) {}
    // Вёрстка большого документа идёт порциями по таймеру: Id таймера
    // есть — страницы ещё досчитываются. Нет поля — null («неизвестно»).
    try {
      if (d && d.FullRecalc && 'Id' in d.FullRecalc)
        st.recalcBusy = d.FullRecalc.Id !== null && d.FullRecalc.Id !== undefined;
    } catch (e) {}
    return st;
  }
  // Курсор в начало документа без выделения — вне истории правок.
  function cursorToStart(api) {
    var d = logicDoc(api);
    if (!d) return false;
    try { if (typeof d.RemoveSelection === 'function') d.RemoveSelection(); } catch (e) {}
    var names = ['MoveCursorToStartPos', 'Cursor_MoveToStartPos'];
    for (var i = 0; i < names.length; i++) {
      try {
        if (typeof d[names[i]] === 'function') { d[names[i]](false); return true; }
      } catch (e) {}
    }
    return false;
  }
"""

DOC_STATE_JS = (
    "(function () {\n"
    + DOC_API_PRELUDE
    + "  var f = findApi(window, 0);\n"
    "  if (!f) return null;\n"
    "  try { return docState(f.api, f.win); } catch (e) { return null; }\n"
    "})()\n"
)

# Подготовка теста — ВНЕ замера: курсор в начало, выделение снято.
DOC_CURSOR_START_JS = (
    "(function () {\n"
    + DOC_API_PRELUDE
    + "  var f = findApi(window, 0);\n"
    "  if (!f) return { ok: false, reason: 'api-not-found' };\n"
    "  var ok = false;\n"
    "  try { ok = cursorToStart(f.api); } catch (e) {}\n"
    "  return { ok: ok, state: docState(f.api, f.win) };\n"
    "})()\n"
)

DOC_API_METHODS = ("getCountPages", "asc_AddBlankPage", "put_Style", "asc_replaceText",
                   "asc_findText", "Undo", "asc_getCanUndo", "asc_EditSelectAll",
                   "EditSelectAll", "SelectAll")


def doc_api_info_js():
    """Диагностика: найден ли api документа и какие нужные методы у него есть."""
    return (
        "(function () {\n"
        + DOC_API_PRELUDE
        + "  var f = findApi(window, 0);\n"
        "  if (!f) return { found: false };\n"
        "  var api = f.api, out = {};\n"
        "  var names = " + json.dumps(list(DOC_API_METHODS)) + ";\n"
        "  for (var i = 0; i < names.length; i++) {\n"
        "    try { out[names[i]] = typeof api[names[i]] === 'function'; }\n"
        "    catch (e) { out[names[i]] = false; }\n"
        "  }\n"
        "  var st = null;\n"
        "  try { st = docState(api, f.win); } catch (e) {}\n"
        "  return { found: true, frame: f.depth, methods: out, state: st };\n"
        "})()\n"
    )


def add_blank_pages_js(count):
    """«Вставка N страниц»: N вызовов asc_AddBlankPage одним вызовом JS —
    в замере одно действие. Курсор ставит подготовка (начало документа:
    перевёрстка идёт по всем страницам после вставки)."""
    n = max(1, int(count))
    return _op_js(
        _need("asc_AddBlankPage")
        + "    st.mutated = true;\n"
          "    for (var i = 0; i < %d; i++) api.asc_AddBlankPage();\n"
          "    st.ok = true;\n"
          "    st.added = %d;\n"
          "    st.method = 'asc_AddBlankPage';\n"
          "    st.after = docState(api, win);\n"
          "    return st;\n" % (n, n),
        prelude=DOC_API_PRELUDE,
    )


def restyle_all_js(style_names):
    """«Смена стиля всего документа»: выделить всё (не меняет документ),
    затем put_Style — единственный изменяющий вызов, последний.

    Args:
        style_names: имена стиля по приоритету ("heading 2", "Heading 2", …):
            берётся первое, которое знает таблица стилей документа; ни одно —
            первое из списка (put_Style сам решит, что с ним делать).
    """
    names = json.dumps(list(style_names))
    return _op_js(
        _need("put_Style")
        + "    var d = logicDoc(api), names = %s, style = names[0];\n"
          "    try {\n"
          "      if (d && d.Styles && typeof d.Styles.GetStyleIdByName === 'function') {\n"
          "        for (var i = 0; i < names.length; i++) {\n"
          "          var sid = d.Styles.GetStyleIdByName(names[i]);\n"
          "          if (sid !== null && sid !== undefined) { style = names[i]; break; }\n"
          "        }\n"
          "      }\n"
          "    } catch (e) {}\n"
          "    var sel = false;\n"
          "    if (typeof api.asc_EditSelectAll === 'function') { api.asc_EditSelectAll(); sel = true; }\n"
          "    else if (typeof api.EditSelectAll === 'function') { api.EditSelectAll(); sel = true; }\n"
          "    else if (typeof api.SelectAll === 'function') { api.SelectAll(); sel = true; }\n"
          "    else if (d && typeof d.SelectAll === 'function') { d.SelectAll(); sel = true; }\n"
          "    if (!sel) { st.reason = 'no-method:SelectAll'; return st; }\n"
          "    st.style = style;\n"
          "    st.mutated = true;\n"
          "    api.put_Style(style);\n"
          "    st.ok = true;\n"
          "    st.method = 'put_Style';\n"
          "    st.after = docState(api, win);\n"
          "    return st;\n" % names,
        prelude=DOC_API_PRELUDE,
    )


def replace_all_js(find_text, replace_with):
    """«Поиск и замена»: заменить все вхождения одним asc_replaceText.

    Сборки на базе свежего sdkjs принимают объект CSearchSettings, старые —
    строку и флаг регистра; пробуем по наличию AscCommon.CSearchSettings.
    """
    find, repl = json.dumps(find_text), json.dumps(replace_with)
    return _op_js(
        _need("asc_replaceText")
        + "    var props = null, SS = win.AscCommon && win.AscCommon.CSearchSettings;\n"
          "    if (typeof SS === 'function') {\n"
          "      props = new SS();\n"
          "      if (typeof props.put_Text === 'function') props.put_Text(%s);\n"
          "      if (typeof props.put_MatchCase === 'function') props.put_MatchCase(false);\n"
          "    }\n"
          "    st.signature = props ? 'settings' : 'legacy';\n"
          "    st.mutated = true;\n"
          "    st.result = props ? api.asc_replaceText(props, %s, true)\n"
          "                      : api.asc_replaceText(%s, %s, true, false);\n"
          "    st.ok = true;\n"
          "    st.method = 'asc_replaceText';\n"
          "    st.after = docState(api, win);\n"
          "    return st;\n" % (find, repl, find, repl),
        prelude=DOC_API_PRELUDE,
    )


def undo_to_js(target_index, max_steps):
    """Откат документа до позиции target_index в истории правок: Undo по шагу,
    остановка, если Index перестал уменьшаться (как _undo_to_js таблиц)."""
    return (
        "(function () {\n"
        + DOC_API_PRELUDE
        + "  var f = findApi(window, 0);\n"
        "  if (!f) return { ok: false, reason: 'api-not-found' };\n"
        "  var api = f.api, win = f.win;\n"
        "  var undo = typeof api.Undo === 'function' ? 'Undo'\n"
        "           : (typeof api.asc_Undo === 'function' ? 'asc_Undo' : null);\n"
        "  if (!undo) return { ok: false, reason: 'no-method:Undo' };\n"
        "  var st = { ok: true, steps: 0, target: %d };\n"
        "  st.before = docState(api, win);\n"
        "  var t0 = performance.now();\n"
        "  for (var i = 0; i < %d; i++) {\n"
        "    var cur = docState(api, win).historyIndex;\n"
        "    if (typeof cur !== 'number' || cur <= %d) break;\n"
        "    try { api[undo](); } catch (e) { st.error = String(e); break; }\n"
        "    st.steps++;\n"
        "    if (docState(api, win).historyIndex >= cur) { st.stuck = true; break; }\n"
        "  }\n"
        "  st.undo_ms = performance.now() - t0;\n"
        "  st.after = docState(api, win);\n"
        "  st.reached = typeof st.after.historyIndex === 'number' && st.after.historyIndex <= %d;\n"
        "  return st;\n"
        "})()\n"
    ) % (int(target_index), int(max_steps), int(target_index), int(target_index))


# Автосохранение — те же вызовы api, что у таблиц (suspend_autosave в
# коннекторе), но через пролог документа.
DOC_SUSPEND_AUTOSAVE_JS = (
    "(function () {\n" + DOC_API_PRELUDE +
    "  var f = findApi(window, 0); if (!f) return null;\n"
    "  var a = f.api, st = { gap_ms: null, periodic: null };\n"
    "  try { if (typeof a.autoSaveGap === 'number') st.gap_ms = a.autoSaveGap; } catch (e) {}\n"
    "  try { if (typeof a.asc_setAutoSaveGap === 'function') a.asc_setAutoSaveGap(0); } catch (e) {}\n"
    "  try { if (typeof a.asc_R7GetIsPeriodicAutosave === 'function') {\n"
    "    st.periodic = !!a.asc_R7GetIsPeriodicAutosave();\n"
    "    if (st.periodic) a.asc_R7SetIsPeriodicAutosave(false);\n"
    "  } } catch (e) {}\n"
    "  return st;\n"
    "})()\n")


def restore_autosave_js(state):
    """Вернуть автосохранение, отключённое DOC_SUSPEND_AUTOSAVE_JS."""
    gap_s = float((state or {}).get("gap_ms") or 0) / 1000.0
    periodic = "true" if (state or {}).get("periodic") else "false"
    return ("(function () {\n" + DOC_API_PRELUDE +
            "  var f = findApi(window, 0); if (!f) return false;\n"
            "  var a = f.api;\n"
            "  try { if (%r > 0 && typeof a.asc_setAutoSaveGap === 'function') "
            "a.asc_setAutoSaveGap(%r); } catch (e) {}\n"
            "  try { if (%s && typeof a.asc_R7SetIsPeriodicAutosave === 'function') "
            "a.asc_R7SetIsPeriodicAutosave(true); } catch (e) {}\n"
            "  return true;\n"
            "})()\n") % (gap_s, gap_s, periodic)
