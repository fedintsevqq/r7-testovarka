"""Разовый живой поиск кнопки/пункта «Файл» → «Сохранить как» в DOM Р7-Офис
через CDP — готовит почву для _try_cdp_saveas в r7_Testovarka.py.

ЗАЧЕМ. Ctrl+Shift+S, меню Alt+F и WM_COMMAND по нативному HMENU все не
открывают диалог «Сохранить как» в этой среде (см. CLAUDE.md, L2,
«продолжение №3» — воспроизвелось и после перезагрузки машины). Р7-Офис —
Qt+CEF, панель инструментов рисуется в DOM (см. `_wait_for_bold_button_cdp`,
уже подтверждённый живым Р7 путь для кнопки «Жирный»), поэтому кнопка
«Файл» и пункт «Сохранить как» тоже должны быть обычными DOM-элементами,
кликабельными через `Runtime.evaluate` — в обход всей синтетической
клавиатуры целиком (тот же приём, что и `dismiss_save_dialog`/
`click_menu_item`, уже работающие в проекте).

НЕ ПРАВИТ save_as_format — только смотрит, кликает и логирует, что нашлось.
Диалог (если открылся) закрывается через Escape.

ЗАПУСК (закройте Р7-Офис перед стартом):
    .venv/Scripts/python.exe tests/manual_saveas_cdp_probe.py
    .venv/Scripts/python.exe tests/manual_saveas_cdp_probe.py путь/к/файлу.xlsx
"""
import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import subprocess           # noqa: E402
import r7_Testovarka as r7mod  # noqa: E402
import win32gui             # noqa: E402

DEFAULT_FILE = "test_50k.xlsx"

# Первый проход: широкий поиск кандидатов на роль кнопки «Файл» — id/класс
# по конвенции ONLYOFFICE-подобных редакторов (fm-btn — известный id кнопки
# File в апстриме ONLYOFFICE; не подтверждено, что R7 сохранил то же имя
# при ребрендинге, отсюда и живой поиск, а не жёсткий селектор в проде) плюс
# просто по видимому тексту "файл"/"file" среди кнопок и вкладок тулбара.
_FIND_FILE_TAB_JS = r"""
(function () {
  function visible(el) {
    try {
      var cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' ||
          parseFloat(cs.opacity) === 0) return false;
      if (el.offsetParent === null && cs.position !== 'fixed') return false;
      var r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch (e) { return false; }
  }
  function label(el) {
    var txt = (el.textContent || '').trim().replace(/\s+/g, ' ');
    if (txt) return txt;
    try {
      var aria = el.getAttribute('aria-label') || el.getAttribute('title');
      return aria ? aria.trim().replace(/\s+/g, ' ') : '';
    } catch (e) { return ''; }
  }
  var out = [];
  function collect(doc, depth) {
    if (depth > 4 || out.length >= 60) return;
    var nodes;
    try {
      nodes = doc.querySelectorAll(
        'button, a, [role="button"], [role="tab"], li, div[id], span[id]');
    } catch (e) { nodes = []; }
    for (var i = 0; i < nodes.length && out.length < 60; i++) {
      var el = nodes[i];
      if (!visible(el)) continue;
      var id = el.id || '';
      var txt = label(el).toLowerCase();
      var idlow = id.toLowerCase();
      var isCandidate =
        idlow.indexOf('fm-btn') !== -1 ||
        idlow.indexOf('file') !== -1 ||
        (txt === 'файл' || txt === 'file');
      if (!isCandidate) continue;
      var r = el.getBoundingClientRect();
      out.push({
        text: label(el), tag: el.tagName.toLowerCase(),
        cls: (el.className && el.className.toString().slice(0, 60)) || '',
        id: id, x: Math.round(r.left), y: Math.round(r.top), depth: depth
      });
    }
    var frames;
    try { frames = doc.querySelectorAll('iframe'); } catch (e) { return; }
    for (var j = 0; j < frames.length; j++) {
      try { collect(frames[j].contentDocument, depth + 1); } catch (e) {}
    }
  }
  collect(document, 0);
  return out;
})()
"""


def _click_by_selector_or_id_js(id_substring):
    return (
        "(function () {\n"
        "  var NEEDLE = " + json.dumps(id_substring.lower()) + ";\n"
        "  function visible(el) {\n"
        "    try {\n"
        "      var cs = getComputedStyle(el);\n"
        "      if (cs.display === 'none' || cs.visibility === 'hidden' ||\n"
        "          parseFloat(cs.opacity) === 0) return false;\n"
        "      if (el.offsetParent === null && cs.position !== 'fixed') return false;\n"
        "      var r = el.getBoundingClientRect();\n"
        "      return r.width > 0 && r.height > 0;\n"
        "    } catch (e) { return false; }\n"
        "  }\n"
        "  function find(doc, depth) {\n"
        "    if (depth > 4) return null;\n"
        "    var nodes;\n"
        "    try { nodes = doc.querySelectorAll('[id]'); } catch (e) { nodes = []; }\n"
        "    for (var i = 0; i < nodes.length; i++) {\n"
        "      var el = nodes[i];\n"
        "      if (el.id.toLowerCase().indexOf(NEEDLE) !== -1 && visible(el)) return el;\n"
        "    }\n"
        "    var frames;\n"
        "    try { frames = doc.querySelectorAll('iframe'); } catch (e) { return null; }\n"
        "    for (var j = 0; j < frames.length; j++) {\n"
        "      try {\n"
        "        var got = find(frames[j].contentDocument, depth + 1);\n"
        "        if (got) return got;\n"
        "      } catch (e) {}\n"
        "    }\n"
        "    return null;\n"
        "  }\n"
        "  var el = find(document, 0);\n"
        "  if (!el) return { clicked: false };\n"
        "  try { el.click(); return { clicked: true, id: el.id, text: (el.textContent||'').trim() }; }\n"
        "  catch (e) { return { clicked: false, error: String(e) }; }\n"
        "})()\n"
    )


def _click_by_text_contains_js(text_needle, exclude_texts=None):
    """Как _click_by_exact_text_js, но по вхождению подстроки, не точному
    совпадению — панель, открывающаяся по клику на вкладку «Файл», скорее
    всего не подходит под узкий MENU_SEL коннектора (та же причина, что и у
    самой вкладки). exclude_texts — уже виденные на экране ДО клика тексты
    (в нижнем регистре), чтобы не попасть в статичный элемент фона (тот же
    приём, что и baseline в click_menu_item, но по тексту, не позиции —
    здесь открывшаяся панель может занять весь экран, и координаты не
    настолько надёжны для отсечения)."""
    return (
        "(function () {\n"
        "  var NEEDLE = " + json.dumps(text_needle.lower()) + ";\n"
        "  var EXCLUDE = " + json.dumps(sorted(exclude_texts or [])) + ";\n"
        "  function visible(el) {\n"
        "    try {\n"
        "      var cs = getComputedStyle(el);\n"
        "      if (cs.display === 'none' || cs.visibility === 'hidden' ||\n"
        "          parseFloat(cs.opacity) === 0) return false;\n"
        "      if (el.offsetParent === null && cs.position !== 'fixed') return false;\n"
        "      var r = el.getBoundingClientRect();\n"
        "      return r.width > 0 && r.height > 0;\n"
        "    } catch (e) { return false; }\n"
        "  }\n"
        "  var candidates = [];\n"
        "  function collect(doc, depth) {\n"
        "    if (depth > 4) return;\n"
        "    var nodes;\n"
        "    try {\n"
        "      nodes = doc.querySelectorAll('button, a, li, [role], div, span');\n"
        "    } catch (e) { nodes = []; }\n"
        "    for (var i = 0; i < nodes.length; i++) {\n"
        "      var el = nodes[i];\n"
        "      var txt = (el.textContent || '').trim().toLowerCase();\n"
        "      if (txt.length === 0 || txt.length > 80) continue;\n"
        "      if (txt.indexOf(NEEDLE) === -1) continue;\n"
        "      if (EXCLUDE.indexOf(txt) !== -1) continue;\n"
        "      if (!visible(el)) continue;\n"
        "      candidates.push(el);\n"
        "    }\n"
        "    var frames;\n"
        "    try { frames = doc.querySelectorAll('iframe'); } catch (e) { return; }\n"
        "    for (var j = 0; j < frames.length; j++) {\n"
        "      try { collect(frames[j].contentDocument, depth + 1); } catch (e) {}\n"
        "    }\n"
        "  }\n"
        "  collect(document, 0);\n"
        "  if (!candidates.length) return { clicked: false, candidates: 0 };\n"
        "  // Самый глубокий по вложенности DOM-узел из совпавших — как правило,\n"
        "  // конкретный кликабельный лист (span/a), а не обёртка-контейнер.\n"
        "  var best = candidates[candidates.length - 1];\n"
        "  try {\n"
        "    best.click();\n"
        "    return { clicked: true, tag: best.tagName.toLowerCase(),\n"
        "             cls: (best.className||'').toString().slice(0, 60),\n"
        "             text: (best.textContent||'').trim().slice(0, 60),\n"
        "             candidates: candidates.length };\n"
        "  } catch (e) { return { clicked: false, error: String(e), candidates: candidates.length }; }\n"
        "})()\n"
    )


def _click_by_exact_text_js(text_needle):
    """Шире, чем click_menu_item коннектора: тот матчит только
    '.dropdown-menu li, .menu-item, [role="menuitem"], .asc-window button,
    .modal button' — вкладки ribbon-тулбара («Файл» — li.ribtab) под этот
    селектор не попадают, найдено живым прогоном (manual_saveas_cdp_probe.py,
    27.08.2026). Ищет по ТОЧНОМУ (не подстрочному) совпадению текста среди
    button/a/li/[role]/div/span — кликает самый глубокий (последний в
    порядке обхода querySelectorAll) подходящий узел, обычно это конкретный
    <a>/<span> внутри обёртки-<li>, а не сама обёртка."""
    return (
        "(function () {\n"
        "  var NEEDLE = " + json.dumps(text_needle.lower()) + ";\n"
        "  function visible(el) {\n"
        "    try {\n"
        "      var cs = getComputedStyle(el);\n"
        "      if (cs.display === 'none' || cs.visibility === 'hidden' ||\n"
        "          parseFloat(cs.opacity) === 0) return false;\n"
        "      if (el.offsetParent === null && cs.position !== 'fixed') return false;\n"
        "      var r = el.getBoundingClientRect();\n"
        "      return r.width > 0 && r.height > 0;\n"
        "    } catch (e) { return false; }\n"
        "  }\n"
        "  var best = null;\n"
        "  function collect(doc, depth) {\n"
        "    if (depth > 4) return;\n"
        "    var nodes;\n"
        "    try {\n"
        "      nodes = doc.querySelectorAll('button, a, li, [role], div, span');\n"
        "    } catch (e) { nodes = []; }\n"
        "    for (var i = 0; i < nodes.length; i++) {\n"
        "      var el = nodes[i];\n"
        "      var txt = (el.textContent || '').trim().toLowerCase();\n"
        "      if (txt !== NEEDLE) continue;\n"
        "      if (!visible(el)) continue;\n"
        "      best = el;\n"
        "    }\n"
        "    var frames;\n"
        "    try { frames = doc.querySelectorAll('iframe'); } catch (e) { return; }\n"
        "    for (var j = 0; j < frames.length; j++) {\n"
        "      try { collect(frames[j].contentDocument, depth + 1); } catch (e) {}\n"
        "    }\n"
        "  }\n"
        "  collect(document, 0);\n"
        "  if (!best) return { clicked: false };\n"
        "  try {\n"
        "    best.click();\n"
        "    return { clicked: true, tag: best.tagName.toLowerCase(),\n"
        "             cls: (best.className||'').toString().slice(0, 60),\n"
        "             text: (best.textContent||'').trim() };\n"
        "  } catch (e) { return { clicked: false, error: String(e) }; }\n"
        "})()\n"
    )


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def make_app():
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app._paced_total = 0.0
    app._pending_modal_confirm = False
    app._pending_cdp_verify = None
    app._op_via_cdp = False
    app._cdp_api_ms = 0.0
    app._op_start_grace = None
    app._op_max_wait = None
    app._webdriver_connector = None
    app._current_webdriver_port = None
    app._cdp_ui_baseline = None
    app._cdp_dump_seen = set()
    app._r7_pids = None
    app._x2t_logged_pids = set()
    app._cached_r7_path = None
    app._cached_cpu_count = None
    app.add_test_log = log
    return app


def find_hwnd_factory(stem):
    def _find():
        found = [None]

        def _cb(h, _):
            try:
                title = win32gui.GetWindowText(h)
            except Exception:
                return
            if stem in title or "Р7-Офис" in title or "R7-Office" in title:
                found[0] = h

        win32gui.EnumWindows(_cb, None)
        return found[0]

    return _find


def find_dialog_hwnd(substrings):
    needles = [s.lower() for s in substrings]
    found = [None]

    def _cb(h, _):
        if found[0] is not None:
            return
        if not win32gui.IsWindowVisible(h):
            return
        t = win32gui.GetWindowText(h).lower()
        if any(n in t for n in needles):
            found[0] = h

    win32gui.EnumWindows(_cb, None)
    return found[0]


def main(argv):
    raw = argv[1] if len(argv) > 1 else DEFAULT_FILE
    test_file = Path(raw)
    if not test_file.is_absolute():
        test_file = BASE_DIR / raw
    if not test_file.exists() and test_file.parent.exists():
        import unicodedata
        target = unicodedata.normalize("NFC", test_file.name)
        for candidate in test_file.parent.iterdir():
            if unicodedata.normalize("NFC", candidate.name) == target:
                test_file = candidate
                break
    if not test_file.exists():
        log(f"❌ Файл не найден: {test_file}")
        return 2

    app = make_app()
    r7_path = app._find_r7_path()
    if not r7_path:
        log("❌ Р7-Офис не найден")
        return 2

    procs = app._get_r7_processes(log_cb=log)
    if procs:
        pids = ", ".join(str(p.pid) for p in procs)
        log(f"⚠️ Р7-Офис уже запущен (PID: {pids}). Закройте его и повторите.")
        return 2

    log(f"Р7-Офис: {r7_path}")
    log(f"Файл: {test_file}")

    debug_args = app._prepare_webdriver_launch(log_cb=log, filename_hint=test_file.name)
    log(f"Аргументы запуска: {debug_args}, порт {app._current_webdriver_port}")

    find_hwnd = find_hwnd_factory(test_file.stem[:12])
    subprocess.Popen([r7_path, str(test_file), *debug_args])

    deadline = time.time() + 60
    hwnd = None
    while time.time() < deadline:
        hwnd = find_hwnd()
        if hwnd:
            break
        time.sleep(0.3)
    if not hwnd:
        log("❌ Окно Р7 не появилось за 60 сек")
        app._terminate_r7_processes(log_cb=log)
        return 2
    log(f"Окно найдено: {hwnd}")

    found_dialog = False
    try:
        ready = app._wait_until_r7_ready(find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Файл открыт ({'данные загружены' if ready else 'таймаут ожидания'})")

        app._cdp_ensure_connected(log_cb=log)
        connector = app._webdriver_connector
        if connector is None or not connector.connected:
            log("❌ CDP не подключился — дальше идти нечем")
            return 2

        log("🔍 Ищу кандидатов на роль кнопки «Файл» в DOM...")
        candidates = connector.evaluate(_FIND_FILE_TAB_JS, timeout=5)
        if not candidates:
            log("⚠️ Кандидатов не найдено вовсе")
        else:
            log(f"Найдено кандидатов: {len(candidates)}")
            for c in candidates:
                log(f"   id={c['id']!r} tag={c['tag']} cls={c['cls']!r} "
                    f"text={c['text']!r} x={c['x']} y={c['y']} depth={c['depth']}")

        # Дамп ДО клика — вычесть его из результата после клика поможет
        # отличить реально новые элементы (открывшуюся панель) от шума.
        baseline = connector.dump_visible_ui() or []
        log(f"Базовый дамп видимых элементов: {len(baseline)}")

        clicked = None
        for needle in ("fm-btn", "file"):
            res = connector.evaluate(_click_by_selector_or_id_js(needle), timeout=5)
            log(f"   Клик по id~={needle!r}: {res}")
            if res and res.get("clicked"):
                clicked = res
                break

        if not clicked:
            log("⚠️ Не удалось кликнуть ни по одному кандидату через id-эвристику — "
                "пробую click_menu_item по тексту 'файл'/'file'")
            res = connector.click_menu_item(["файл", "file"], timeout=5)
            log(f"   click_menu_item: {res}")
            if res and res.get("clicked"):
                clicked = res

        if not clicked:
            log("⚠️ click_menu_item тоже не нашёл кандидатов (узкий селектор "
                "меню не покрывает ribbon-вкладки) — пробую точный текстовый "
                "поиск по широкому набору тегов")
            for needle in ("файл", "file"):
                res = connector.evaluate(_click_by_exact_text_js(needle), timeout=5)
                log(f"   Клик по точному тексту {needle!r}: {res}")
                if res and res.get("clicked"):
                    clicked = res
                    break

        if not clicked:
            log("❌ Кнопку «Файл» кликнуть не удалось ни одним способом")
            return 1

        time.sleep(0.6)
        after = connector.dump_visible_ui() or []
        base_keys = {(e.get("text"), e.get("tag"), e.get("id")) for e in baseline}
        fresh = [e for e in after if (e.get("text"), e.get("tag"), e.get("id")) not in base_keys]
        log(f"Новых видимых элементов после клика: {len(fresh)}")
        for e in fresh:
            log(f"   text={e['text']!r} tag={e['tag']} id={e['id']!r} cls={e['cls']!r} "
                f"x={e['x']} y={e['y']} depth={e['depth']}")

        log("🔍 Пробую кликнуть «Сохранить как»/«Save as» среди открывшегося...")
        res = connector.click_menu_item(["сохранить как", "save as"], baseline=baseline, timeout=5)
        log(f"   click_menu_item(«Сохранить как»): {res}")
        if not (res and res.get("clicked")):
            base_texts = {(e.get("text") or "").lower() for e in baseline}
            for needle in ("сохранить как", "save as"):
                res = connector.evaluate(
                    _click_by_text_contains_js(needle, exclude_texts=base_texts), timeout=5)
                log(f"   Клик по подстроке {needle!r} (широкий поиск): {res}")
                if res and res.get("clicked"):
                    break

        deadline2 = time.time() + 5.0
        dlg = None
        while time.time() < deadline2:
            dlg = find_dialog_hwnd(("сохранить как", "save as"))
            if dlg:
                break
            time.sleep(0.1)

        if dlg:
            found_dialog = True
            log(f"✅ Диалог «Сохранить как» ОТКРЫЛСЯ через CDP-клик! hwnd={dlg}")
            log("   Закрываю Escape'ом (Win32-диалог реагирует на реальную клавишу,"
                " не на DOM — это ожидаемо, сам диалог уже не CEF)")
            import pyautogui
            pyautogui.press('escape')
            time.sleep(0.3)
        else:
            log("❌ Диалог «Сохранить как» так и не появился через win32gui")
            log("   Видимые окна сейчас:")
            app._dump_visible_window_titles(log, limit=30)

    finally:
        log("🔚 Закрытие Р7-Офис (без сохранения)...")
        try:
            h = find_hwnd()
            if h:
                app._close_r7_gracefully(h, log_cb=log, timeout=15)
        except Exception as e:
            log(f"⚠️ Закрытие не удалось ({type(e).__name__}: {e}) — завершаю процессы")
            app._terminate_r7_processes(log_cb=log)
        app._close_webdriver_connector()
        app._cleanup_x2t_temp_pdfs(log_cb=log)

    return 0 if found_dialog else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
