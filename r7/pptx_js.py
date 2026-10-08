"""JS для редактора презентаций Р7 (.pptx) через CDP — этап 5, пункт 1.

Устроено как r7/doc_js.py. ПРОВЕРЕНО КООРДИНАТОРОМ НА ЖИВОМ Р7 2026.3.2
(07.10.2026, `DesktopEditors.exe --new:slide`, страница CDP, редактор в
iframe глубины 1): `w.editor` (он же `w.Asc.editor`) с `WordControl`,
`getCountPages()` — число слайдов, `WordControl.m_oLogicDocument.Slides`,
`.History.Index`, `asc_getCanUndo()`; есть `AddSlide`, `DublicateSlide`
(так в api), `SelectAllSlides`, `ChangeTheme`, `ApplySlideTransition`,
`SlideTransitionApplyToAll`, `Undo`.

У редактора документов тоже есть WordControl и getCountPages, поэтому
презентация узнаётся по массиву `Slides` логического документа, а пролог
документа такой api отвергает (r7/doc_js.py).

Операции собираются тем же _op_js: поля ok / mutated / api_ms / before /
after. Правило 7: mutated ставится прямо перед изменяющим вызовом, он
последний; выделение всех слайдов документ не меняет.

Не подтверждено живым прогоном (docs/presentation-ops.md): список тем
`ThemeLoader.Themes.EditorThemes` и синхронность `ChangeTheme`, класс
`CAscSlideTransition` и то, что `ApplySlideTransition` берёт все выделенные
слайды, поле `timing.TransitionType` слайда, `WordControl.GoToPage`, кнопка
«Добавить слайд» как маркер готовности. Каждое читается осторожно: нет поля
— null, нет метода — операция не трогает презентацию и честно падает.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from r7 import doc_js
from r7_webdriver_connector import _BOLD_READY_PROBE_JS, _FRAME_WALK_JS, _need, _op_js

PPTX_API_PRELUDE = r"""
  function apiOf(win) {
    var cands = [];
    try { if (win.editor) cands.push(win.editor); } catch (e) {}
    try { if (win.Asc && win.Asc.editor) cands.push(win.Asc.editor); } catch (e) {}
    try {
      if (win.PE && win.PE.getController) cands.push(win.PE.getController('Main').api);
    } catch (e) {}
    for (var i = 0; i < cands.length; i++) {
      try {
        var a = cands[i];
        var d = a && a.WordControl && a.WordControl.m_oLogicDocument;
        if (d && typeof a.getCountPages === 'function' && d.Slides
            && typeof d.Slides.length === 'number') return a;
      } catch (e) {}
    }
    return null;
  }
""" + _FRAME_WALK_JS + r"""
  function logicDoc(api) {
    try { return api.WordControl.m_oLogicDocument; } catch (e) { return null; }
  }
  // Тема — у мастера первого слайда: ChangeTheme добавляет новый мастер, а
  // slideMasters[0] остаётся прежним (живой Р7 2026.3.2, 08.10.2026).
  function themeName(d) {
    try { return d.Slides[0].Layout.Master.Theme.name || null; } catch (e) {}
    try { return d.slideMasters[0].Theme.name || null; } catch (e) { return null; }
  }
  // Сколько слайдов с переходом: transition.TransitionType > 0 (0 — «Нет»).
  // Ни у одного слайда поле не читается — null.
  function transitionsCount(d) {
    var n = 0, seen = false;
    try {
      for (var i = 0; i < d.Slides.length; i++) {
        // Переход — slide.transition (TransitionType), timing у слайда пуст
        // (живой Р7 2026.3.2, 08.10.2026); timing — запасной путь.
        var t = d.Slides[i] && (d.Slides[i].transition || d.Slides[i].timing);
        if (t && typeof t.TransitionType === 'number') {
          seen = true;
          if (t.TransitionType > 0) n++;
        }
      }
    } catch (e) {}
    return seen ? n : null;
  }
  // Снимок презентации. pages — getCountPages (у презентации это слайды):
  // по нему общий код (r7/doc_run.py) ждёт, пока число перестанет меняться.
  // recalcBusy — признака идущего пересчёта у презентации не найдено, null.
  function docState(api, win) {
    var st = { pages: null, slides: null, historyIndex: null, historyPoints: null,
               canUndo: null, recalcBusy: null, theme: null, transitions: null };
    var d = logicDoc(api);
    try { st.pages = api.getCountPages(); } catch (e) {}
    try { if (d && d.Slides) st.slides = d.Slides.length; } catch (e) {}
    try {
      var H = (d && d.History) || (win.AscCommon && win.AscCommon.History);
      if (H) {
        if (typeof H.Index === 'number') st.historyIndex = H.Index;
        if (H.Points && typeof H.Points.length === 'number') st.historyPoints = H.Points.length;
      }
    } catch (e) {}
    try { if (typeof api.asc_getCanUndo === 'function') st.canUndo = !!api.asc_getCanUndo(); } catch (e) {}
    if (d) { st.theme = themeName(d); st.transitions = transitionsCount(d); }
    return st;
  }
"""

PPTX_STATE_JS = (
    "(function () {\n"
    + PPTX_API_PRELUDE
    + "  var f = findApi(window, 0);\n"
    "  if (!f) return null;\n"
    "  try { return docState(f.api, f.win); } catch (e) { return null; }\n"
    "})()\n"
)

# Подготовка теста — ВНЕ замера: первый слайд текущий (новые слайды
# встают после него), выделение объектов снято. В историю правок не идёт.
PPTX_FIRST_SLIDE_JS = (
    "(function () {\n"
    + PPTX_API_PRELUDE
    + "  var f = findApi(window, 0);\n"
    "  if (!f) return { ok: false, reason: 'api-not-found' };\n"
    "  var api = f.api, d = logicDoc(api), ok = false;\n"
    "  try { if (d && typeof d.RemoveSelection === 'function') d.RemoveSelection(); } catch (e) {}\n"
    "  try {\n"
    "    if (api.WordControl && typeof api.WordControl.GoToPage === 'function') {\n"
    "      api.WordControl.GoToPage(0); ok = true;\n"
    "    }\n"
    "  } catch (e) {}\n"
    "  return { ok: ok, state: docState(api, f.win) };\n"
    "})()\n"
)

PPTX_API_METHODS = ("getCountPages", "AddSlide", "DublicateSlide", "DeleteSlide",
                    "SelectAllSlides", "ChangeTheme", "getCurrentTheme",
                    "ApplySlideTransition", "SlideTransitionApplyToAll", "Undo",
                    "asc_getCanUndo")


def pptx_api_info_js() -> str:
    """Диагностика: найден ли api презентации, какие нужные методы есть,
    сколько тем в списке редактора."""
    return (
        "(function () {\n"
        + PPTX_API_PRELUDE
        + "  var f = findApi(window, 0);\n"
        "  if (!f) return { found: false };\n"
        "  var api = f.api, out = {};\n"
        "  var names = " + json.dumps(list(PPTX_API_METHODS)) + ";\n"
        "  for (var i = 0; i < names.length; i++) {\n"
        "    try { out[names[i]] = typeof api[names[i]] === 'function'; }\n"
        "    catch (e) { out[names[i]] = false; }\n"
        "  }\n"
        "  var st = null, themes = null;\n"
        "  try { st = docState(api, f.win); } catch (e) {}\n"
        "  try { themes = api.ThemeLoader.Themes.EditorThemes.length; } catch (e) {}\n"
        "  return { found: true, frame: f.depth, methods: out, state: st, themes: themes };\n"
        "})()\n"
    )


def add_slides_js(count: int) -> str:
    """«Добавление N слайдов»: N вызовов AddSlide одним вызовом JS — в замере
    одно действие. AddSlide без аргумента — макет текущего слайда; слайды
    встают после текущего (подготовка делает текущим первый)."""
    n = max(1, int(count))
    return _op_js(
        _need("AddSlide")
        + "    st.mutated = true;\n"
          "    for (var i = 0; i < %d; i++) api.AddSlide();\n"
          "    st.ok = true;\n"
          "    st.added = %d;\n"
          "    st.method = 'AddSlide';\n"
          "    st.after = docState(api, win);\n"
          "    return st;\n" % (n, n),
        prelude=PPTX_API_PRELUDE,
    )


def duplicate_all_js() -> str:
    """«Дублирование всех слайдов»: выделить все слайды (презентацию не
    меняет), затем DublicateSlide — единственный изменяющий вызов, последний."""
    return _op_js(
        _need("SelectAllSlides")
        + _need("DublicateSlide")
        + "    api.SelectAllSlides();\n"
          "    st.mutated = true;\n"
          "    api.DublicateSlide();\n"
          "    st.ok = true;\n"
          "    st.method = 'DublicateSlide';\n"
          "    st.after = docState(api, win);\n"
          "    return st;\n",
        prelude=PPTX_API_PRELUDE,
    )


def change_theme_js(candidates: Iterable[int]) -> str:
    """«Смена темы»: ChangeTheme(индекс) ко всем слайдам.

    Индекс берётся из candidates — первый, который есть в списке тем
    редактора (ThemeLoader.Themes.EditorThemes) и чьё имя не совпадает с
    текущей темой. Список не читается — первый из candidates.

    Args:
        candidates: индексы тем редактора по приоритету.
    """
    cands = json.dumps([int(c) for c in candidates])
    return _op_js(
        _need("ChangeTheme")
        + "    var ed = null;\n"
          "    try { ed = api.ThemeLoader.Themes.EditorThemes; } catch (e) {}\n"
          "    st.themes = (ed && typeof ed.length === 'number') ? ed.length : null;\n"
          "    var cands = %s, idx = null, name = null;\n"
          "    for (var i = 0; i < cands.length; i++) {\n"
          "      var c = cands[i], nm = null;\n"
          "      if (st.themes !== null && c >= st.themes) continue;\n"
          "      try {\n"
          "        var info = ed && ed[c];\n"
          "        if (info) nm = info.Name || info.name\n"
          "          || (typeof info.get_Name === 'function' ? info.get_Name() : null) || null;\n"
          "      } catch (e) {}\n"
          "      if (nm !== null && nm === st.before.theme) continue;\n"
          "      idx = c; name = nm; break;\n"
          "    }\n"
          "    if (idx === null) { st.reason = 'no-theme'; return st; }\n"
          "    st.theme_index = idx;\n"
          "    st.theme_name = name;\n"
          "    st.mutated = true;\n"
          "    api.ChangeTheme(idx);\n"
          "    st.ok = true;\n"
          "    st.method = 'ChangeTheme';\n"
          "    st.after = docState(api, win);\n"
          "    return st;\n" % cands,
        prelude=PPTX_API_PRELUDE,
    )


def apply_transition_all_js(duration_ms: int = 700) -> str:
    """«Переход ко всем слайдам»: выделить все слайды, затем один
    ApplySlideTransition с «Выцветанием» (Fade). Объект перехода —
    CAscSlideTransition (Asc или AscCommonSlide); нет класса — презентация
    не тронута, операция падает."""
    return _op_js(
        _need("SelectAllSlides")
        + _need("ApplySlideTransition")
        + "    var C = (win.Asc && win.Asc.CAscSlideTransition)\n"
          "         || (win.AscCommonSlide && win.AscCommonSlide.CAscSlideTransition) || null;\n"
          "    if (typeof C !== 'function') { st.reason = 'no-class:CAscSlideTransition'; return st; }\n"
          "    var T = win.Asc && win.Asc.c_oAscSlideTransitionTypes;\n"
          "    var type = (T && typeof T.Fade === 'number') ? T.Fade : 1;\n"
          "    var tr = new C();\n"
          "    if (typeof tr.setDefaultParams === 'function') tr.setDefaultParams();\n"
          "    function put(name, field, v) {\n"
          "      if (typeof tr[name] === 'function') tr[name](v); else tr[field] = v;\n"
          "    }\n"
          "    put('put_TransitionType', 'TransitionType', type);\n"
          "    put('put_TransitionOption', 'TransitionOption', 0);\n"
          "    put('put_TransitionDuration', 'TransitionDuration', %d);\n"
          "    st.transition_type = type;\n"
          "    api.SelectAllSlides();\n"
          "    st.mutated = true;\n"
          "    api.ApplySlideTransition(tr);\n"
          "    st.ok = true;\n"
          "    st.method = 'ApplySlideTransition';\n"
          "    st.after = docState(api, win);\n"
          "    return st;\n" % int(duration_ms),
        prelude=PPTX_API_PRELUDE,
    )


def undo_to_js(target_index: int, max_steps: int) -> str:
    """Откат презентации до позиции target_index — общий откат документа
    (r7/doc_js.py) с прологом презентации."""
    return doc_js.undo_to_js(target_index, max_steps, prelude=PPTX_API_PRELUDE)


PPTX_SUSPEND_AUTOSAVE_JS = doc_js.suspend_autosave_js(PPTX_API_PRELUDE)


def restore_autosave_js(state: Mapping[str, Any] | None) -> str:
    return doc_js.restore_autosave_js(state, prelude=PPTX_API_PRELUDE)


# Маркер готовности презентации. Кнопка «Жирный» в редакторе презентаций
# доступна только при курсоре в тексте, а после открытия выделен слайд, не
# текст: она так и осталась бы недоступной, и общий детектор не перешёл бы
# на CPU-путь до таймаута. Поэтому — та же проба с MutationObserver
# (_BOLD_READY_PROBE_JS), но по кнопке «Добавить слайд»: панель держит её
# выключенной, пока документ грузится. Только <button> и узел с точным id:
# у слотов разметки («slot-btn-addslide») класса disabled не бывает, их
# пропускаем. Кнопки нет — {found: false}, детектор идёт по CPU.
_BOLD_SELECTOR = "'#id-toolbar-btn-bold, [id*=\"toolbar-btn-bold\" i]'"
# Живой Р7 2026.3.2 (08.10.2026): кнопка — безымянный <button> внутри
# div#tlbtn-addslide-0 (вторая копия — #tlbtn-addslide-1 на вкладке «Вставка»).
ADD_SLIDE_SELECTOR = ("'#tlbtn-addslide-0 button, #id-toolbar-button-add-slide, "
                      "button[id*=\"addslide\" i]'")


def _ready_probe_js() -> str:
    if _BOLD_READY_PROBE_JS.count(_BOLD_SELECTOR) != 1:
        raise RuntimeError("проба «Жирного» в коннекторе изменилась — обновите pptx_js")
    return (_BOLD_READY_PROBE_JS.replace(_BOLD_SELECTOR, ADD_SLIDE_SELECTOR)
            .replace("__r7Bold", "__r7SlideBtn"))


PPTX_READY_PROBE_JS = _ready_probe_js()
