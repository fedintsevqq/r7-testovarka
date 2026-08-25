"""
Опциональный CDP/WebDriver-коннектор к Р7-Офис — точное определение готовности
редактора по кнопке «Жирный» в DOM, в дополнение к win32gui-триггеру из
_wait_until_r7_ready (см. коммит 7978206 в r7_Testovarka.py).

ПОЧЕМУ ЭТОТ МОДУЛЬ СУЩЕСТВУЕТ
Коммит 7978206 эмпирически доказал: на установленной сборке Р7-Офис
(2026.2.2.x, Qt+CEF) панель инструментов — не нативные Win32-виджеты, а HTML
внутри CEF-рендера. win32gui.EnumChildWindows не видит кнопку «Жирный» в
принципе — она в DOM, <button id="id-toolbar-btn-bold"> внутри iframe
apps/spreadsheeteditor/main/, на 3 уровня вложенности глубже top-level окна.
Тот же коммит установил, что при запуске с флагом
--ascdesktop-support-debug-info Р7 открывает порт Chrome DevTools Protocol
("DevTools listening on ws://..." в консоли) — через него кнопка реально
находится.

ОБЯЗАТЕЛЬНОЕ УСЛОВИЕ: подключиться можно только к процессу, ЗАПУЩЕННОМУ с
этим флагом. К уже работающему без него процессу CDP-порт задним числом не
подключить — это ограничение CEF, а не этого кода. См. r7_launch_debug_args()
ниже: аргумент должен попасть в командную строку subprocess.Popen в момент
запуска Р7 в r7_Testovarka.py.

ПОРТ — ФИКСИРОВАННЫЙ 8080, НЕ ПРОИЗВОЛЬНЫЙ
Сверено с github.com/fedintsevqq/r7-desktop-selenium (conftest.old — более
ранняя, не-мок версия фикстуры driver; см. её же README.md: текущий
conftest.py заменил реальное подключение на MockDriver, т.е. рабочей
референсной реализации там сейчас фактически нет, только эта более ранняя
попытка). Она запускает Р7 как
`[..., '--ascdesktop-support-debug-info']` — БЕЗ `--remote-debugging-port=`
— и жёстко ждёт порт 8080 (`wait_for_port("127.0.0.1", 8080)`,
`debugger_address = 'localhost:8080'`). Похоже, что этот флаг у Р7 сам
выбирает фиксированный порт, а не принимает свой отдельно —
`--remote-debugging-port` для него не подтверждён нигде. Поэтому
DEFAULT_CDP_PORT ниже = 8080, а не динамический через get_free_port().
get_free_port() оставлен на случай, если реальная сборка всё же уважает
`--remote-debugging-port` (не проверено) — но по умолчанию не используется.

Локатор кнопки подтверждён independently в этом же репозитории
(editors/spreadsheet_editor.py: `BOLD_TEXT = (By.ID, 'id-toolbar-btn-bold')`)
— совпадает с тем, что нашёл коммит 7978206 через ручной CDP-дамп. Это
единственная часть, подтверждённая ДВАЖДЫ независимо, и на неё можно
полагаться с высокой уверенностью.

СПОСОБ ПОДКЛЮЧЕНИЯ — НЕ КАК В conftest.old
conftest.old подключается через `RemoteWebDriver(command_executor=
"http://localhost:8080", ...)` — то есть направляет W3C WebDriver-протокол
Selenium прямо на голый CDP HTTP-эндпоинт, минуя chromedriver. Это
технически сомнительно: голый CDP-эндпоинт не реализует W3C WebDriver wire
protocol (`POST /session` и т.д.) — он отдаёт только `/json`, `/json/version`
и WebSocket для самого CDP. Вероятно, именно поэтому команда r7-office в
итоге откатилась на MockDriver — прямое подключение не заработало надёжно.
Здесь вместо этого используется `add_experimental_option("debuggerAddress",
...)` + `webdriver.Chrome(options=options)` — штатный способ Selenium
подключаться к уже открытому CDP-порту, где chromedriver сам выступает
мостом CDP↔W3C. Риск несовпадения версии chromedriver/CEF (см. ниже) при
этом никуда не девается, но сам протокол подключения — правильный.

ДВА БЭКЕНДА, ОДИН ИНТЕРФЕЙС
1) Selenium (webdriver.Chrome, attach через debuggerAddress) — то, что просил
   пользователь. Требует chromedriver, версия которого совместима с версией
   CEF/Chromium, встроенной в дистрибутив Р7. Р7-Офис нигде не публикует эту
   версию, и она может не совпадать ни с одним публичным релизом chromedriver
   — Selenium в части сборок Р7 может просто отказаться подключаться
   (chromedriver проверяет версию протокола при хендшейке). Это основной
   риск всей Selenium-интеграции — см. connect()/_try_connect_selenium ниже.
2) Голый CDP через websocket (Runtime.evaluate) — без бинарника chromedriver,
   без проверки версий, минимальный оверхед. Не то, что явно просили
   ("WebDriver"), но единственный путь, гарантированно не зависящий от
   совместимости версий.

Коннектор пробует (1), и при любой ошибке (нет chromedriver, версия не
подошла, таймаут хендшейка) молча переключается на (2) — снаружи вызывающий
код всегда получает один и тот же интерфейс и не обязан знать, какой бэкенд
сработал.

Порт 8080 и наличие CDP при --ascdesktop-support-debug-info подтверждены на
живом Р7-Офис (не только по анализу коммита 7978206 и стороннего репозитория,
как было на момент первой версии этого модуля).

ЗАВИСИМОСТИ
- Обязательные для бэкенда (2), голый CDP: requests, websocket-client.
  WEBDRIVER_OK (ниже) — True, только если оба установлены; это минимальный
  рабочий набор, без него модуль CDP-триггер не может дать вообще ничего.
- Опциональные для бэкенда (1), Selenium: selenium, и в PATH — chromedriver
  подходящей версии (webdriver-manager сам скачать правильную версию не
  сможет: он ориентируется на версию системного Chrome, а не CEF внутри Р7).
  Отсутствие selenium НЕ влияет на WEBDRIVER_OK — connect() просто пропустит
  бэкенд (1) и сразу пойдёт в (2).
- Импорт selenium — внутри функции (_try_connect_selenium), не на уровне
  модуля: это единственная опциональная зависимость модуля, requests и
  websocket-client — обязательные и импортируются на уровне модуля вместе с
  json/socket/time, ровно как WIN32_OK/PYAUTOGUI_OK в r7_Testovarka.py
  импортируют pywin32/pyautogui на уровне модуля.
"""

import json
import socket
import time
import urllib.parse


def _is_ws_closed(exc):
    """True, если исключение означает «websocket закрыт/оборван».

    websocket-client поднимает WebSocketConnectionClosedException, которая
    наследуется от Exception, а не от ConnectionError, — по типу её не
    поймать, не импортируя сам пакет (а он опциональный). Смотрим на имя
    класса, чтобы не тащить импорт в модуль, который обязан работать и без
    установленного websocket-client.
    """
    name = type(exc).__name__
    # Таймаут recv СЮДА НЕ ВХОДИТ намеренно. create_connection(timeout=2.0)
    # задаёт таймаут и на приём, поэтому медленный Runtime.evaluate (CEF занят
    # загрузкой большого документа) поднимает WebSocketTimeoutException — но
    # сокет при этом жив. Считать это обрывом значило бы похоронить CDP на весь
    # запуск Р7 из-за одного затянувшегося опроса, и тогда закрытие модалки
    # «Сохранить изменения?» на выходе не сработало бы — то есть ровно то, ради
    # чего CDP и делался обязательным.
    return "WebSocket" in name and "Closed" in name

try:
    import requests  # noqa: F401 — используется в _pick_target
    import websocket  # noqa: F401 — используется в _try_connect_cdp
    WEBDRIVER_OK = True
except ImportError:
    WEBDRIVER_OK = False


# JS, общий для обоих бэкендов: рекурсивно ищет кнопку «Жирный» по всем
# reachable iframe (они same-origin внутри Р7 — app://, поэтому
# contentDocument доступен из top-level контекста без переключения фрейма
# через CDP Page.* / Selenium switch_to.frame). Возвращает null, если кнопка
# не найдена нигде, иначе {found: true, disabled: bool}.
_FIND_BOLD_BUTTON_JS = """
(function () {
  function findBoldBtn(doc) {
    try {
      var el = doc.querySelector('#id-toolbar-btn-bold, [id*="toolbar-btn-bold" i]');
      if (el) return el;
    } catch (e) {}
    var iframes;
    try {
      iframes = doc.querySelectorAll('iframe');
    } catch (e) {
      return null;
    }
    for (var i = 0; i < iframes.length; i++) {
      try {
        var found = findBoldBtn(iframes[i].contentDocument);
        if (found) return found;
      } catch (e) {
        // cross-origin или ещё не загружен — пропускаем
      }
    }
    return null;
  }
  var btn = findBoldBtn(document);
  if (!btn) return null;
  var disabled = btn.disabled === true ||
                 btn.getAttribute('disabled') !== null ||
                 btn.getAttribute('aria-disabled') === 'true' ||
                 (btn.className && btn.className.indexOf('disabled') !== -1);
  return { found: true, disabled: !!disabled };
})()
"""

# Закрытие модалки «Сохранить изменения?», появляющейся при выходе из Р7 после
# правок. Она — такой же HTML внутри CEF, как и панель инструментов, поэтому
# win32gui.EnumChildWindows её кнопок не видит (Qt рисует виджеты сам, не
# заводя дочерних HWND) — единственный надёжный путь к ним лежит через DOM.
#
# Кнопка выбирается СТРОГО по тексту «Не сохранять»/«Don't save». Нажать
# вслепую Enter нельзя: кнопка по умолчанию в этой модалке — «Сохранить», и
# слепое подтверждение перезаписало бы эталонный тестовый файл, который прогон
# только что изменил. Escape тоже не годится — он отменяет само закрытие.
# Поэтому: либо точное попадание по нужной кнопке, либо (в r7_Testovarka.py)
# честное принудительное завершение процесса.
_DISMISS_SAVE_DIALOG_JS = r"""
(function () {
  var WANTED = ['не сохранять', "don't save", 'dont save', 'не зберігати'];
  function visible(el) {
    try {
      if (el.offsetParent === null && getComputedStyle(el).position !== 'fixed') return false;
      var r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch (e) { return false; }
  }
  function scan(doc) {
    var nodes;
    try {
      nodes = doc.querySelectorAll('button, a, .btn, [role="button"]');
    } catch (e) { return null; }
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var txt = (el.textContent || '').trim().toLowerCase();
      if (!txt) continue;
      for (var w = 0; w < WANTED.length; w++) {
        if (txt.indexOf(WANTED[w]) !== -1 && visible(el)) {
          try { el.click(); return { clicked: true, text: txt }; } catch (e) {}
        }
      }
    }
    var iframes;
    try { iframes = doc.querySelectorAll('iframe'); } catch (e) { return null; }
    for (var j = 0; j < iframes.length; j++) {
      try {
        var got = scan(iframes[j].contentDocument);
        if (got) return got;
      } catch (e) {
        // cross-origin или ещё не загружен — пропускаем
      }
    }
    return null;
  }
  return scan(document);
})()
"""

# Диагностика: что за интерактивные элементы сейчас видны на экране. Нужна для
# слепых мест автоматизации — контекстного меню и модалок, по которым код ходит
# стрелками вслепую (`down` N раз + Enter). Стоит меню обзавестись лишним
# пунктом, как счётчик стрелок уезжает и тест жмёт не то. Дамп показывает
# фактические подписи, чтобы чинить это по данным, а не наугад.
#
# Ограничение по 40 элементам — чтобы не утащить в лог всю панель инструментов.
_DUMP_VISIBLE_UI_JS = r"""
(function () {
  function visible(el) {
    // Раньше проверялись только offsetParent и размер прямоугольника —
    // оба этих признака НЕ реагируют на visibility:hidden и opacity:0
    // (offsetParent остаётся ненулевым, а getBoundingClientRect всё равно
    // возвращает реальные ширину/высоту: и то, и другое влияет только на
    // отрисовку, не на раскладку). Если Р7 закрывает меню именно так, а не
    // через display:none, дамп раз за разом находил один и тот же
    // постоянно смонтированный, но фактически скрытый узел — отсюда
    // идентичные дампы для разных, по факту разных, состояний меню
    // (issue #9). display:none по-прежнему ловится через offsetParent.
    try {
      var cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' ||
          parseFloat(cs.opacity) === 0) return false;
      if (el.offsetParent === null && cs.position !== 'fixed') return false;
      var r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch (e) { return false; }
  }
  // textContent пуст у кнопок-иконок (без видимого текста, только
  // title/aria-label) — например, у ряда иконок «Вставить» в верхней части
  // меню вставки. Раньше такие элементы молча выпадали из дампа (см. issue
  // #9: пункты «Вставить»/«Специальная вставка» никогда не попадали в
  // список) — теперь для них используется aria-label/title как подпись.
  function label(el) {
    var txt = (el.textContent || '').trim().replace(/\s+/g, ' ');
    if (txt) return txt;
    try {
      var aria = el.getAttribute('aria-label') || el.getAttribute('title');
      return aria ? aria.trim().replace(/\s+/g, ' ') : '';
    } catch (e) { return ''; }
  }
  // Два прохода, а не один общий селектор: querySelectorAll отдаёт элементы в
  // порядке документа, а не в порядке селектора, поэтому кнопки панели
  // инструментов выбрали бы весь лимит раньше, чем до списка дойдут пункты
  // раскрытого меню — то есть ровно то, ради чего дамп и снимается.
  var MENU_SEL = '.dropdown-menu li, .menu-item, [role="menuitem"], ' +
                 '.asc-window button, .modal button';
  var ANY_SEL  = 'button, .btn, [role="button"]';
  var out = [];
  var seen = [];
  function collect(doc, sel, depth) {
    if (depth > 4 || out.length >= 40) return;
    var nodes;
    try { nodes = doc.querySelectorAll(sel); } catch (e) { nodes = []; }
    for (var i = 0; i < nodes.length && out.length < 40; i++) {
      var el = nodes[i];
      if (seen.indexOf(el) !== -1) continue;
      if (!visible(el)) continue;
      var txt = label(el);
      if (!txt || txt.length > 60) continue;
      seen.push(el);
      // x/y/depth — временная диагностика issue #9: дамп раз за разом
      // находит один и тот же список пунктов независимо от того, где и
      // какое меню реально раскрыто. Координаты и глубина вложенности
      // iframe показывают, находится ли этот узел там, где произошёл
      // right-click (реальный popup), или это статичный элемент где-то
      // в стороне (значит меню рисуется вне обходимого DOM — например,
      // нативным CEF-попапом, а не HTML внутри страницы).
      var r2 = el.getBoundingClientRect();
      out.push({
        text: txt,
        tag: el.tagName.toLowerCase(),
        cls: (el.className && el.className.toString().slice(0, 40)) || '',
        id: el.id || '',
        x: Math.round(r2.left),
        y: Math.round(r2.top),
        depth: depth
      });
    }
    var frames;
    try { frames = doc.querySelectorAll('iframe'); } catch (e) { return; }
    for (var j = 0; j < frames.length; j++) {
      try { collect(frames[j].contentDocument, sel, depth + 1); } catch (e) {}
    }
  }
  collect(document, MENU_SEL, 0);   // сперва меню и модалки
  collect(document, ANY_SEL, 0);    // затем всё остальное, если остался лимит
  return out;
})()
"""

# ── Операции над документом через внутренний API Р7 (sdkjs) ──────────────
#
# ПОЧЕМУ НЕ document.execCommand И НЕ navigator.clipboard
# Сетка Р7-Офис — это <canvas>, а не DOM-документ: строки, столбцы и ячейки
# в разметке не существуют вовсе, редактор рисует их сам. Поэтому
# document.execCommand('selectAll') выделил бы текст HTML-страницы, а не
# ячейки листа, а 'insertColumn'/'insertSheet' в спецификации execCommand
# нет в принципе (там только команды редактирования contenteditable).
# execCommand('paste') отключён из скрипта во всех современных движках, а
# navigator.clipboard.writeText требует secure context и user activation,
# которых у вызова из CDP нет.
#
# Настоящий рычаг — внутренний API редактора: экземпляр Asc.spreadsheet_api,
# тот самый объект, чьи asc_*-методы дёргают кнопки тулбара и пункты меню.
# Имена ниже сверены с установленной сборкой Р7-Офис, файл
# Editors/editors/sdkjs/cell/sdk-all-min.js (2026.2.2.x):
#
#   asc_EditSelectAll()          → wb.selectAll()             — Ctrl+A
#   asc_Copy() / asc_Paste()     → asc_desktop_copypaste()    — Ctrl+C / Ctrl+V
#                                  (на десктопе уходит в нативный буфер обмена,
#                                   то есть ровно туда же, куда и хоткей)
#   asc_addWorksheet(name)                                     — Shift+F11
#   asc_insertCells(opt)         → changeWorksheet("insCell")  — «Вставить ячейки»
#   asc_findCell("A1:E1")        → setSelection()              — поле «Имя»
#   asc_getActiveRangeStr(), asc_getWorksheetsCount(),
#   asc_getActiveWorksheetIndex(), asc_showWorksheet(i)        — чтение состояния
#
# Константы сдвига (Asc.c_oAscInsertOptions в той же сборке):
#   InsertCellsAndShiftRight=1, InsertCellsAndShiftDown=2,
#   InsertColumns=3, InsertRows=4
# В JS они берутся из живого Asc, а числа ниже — запасной вариант на случай,
# если в сборке объекта констант не окажется.
INSERT_SHIFT_RIGHT = 1
INSERT_SHIFT_DOWN = 2
INSERT_COLUMNS = 3
INSERT_ROWS = 4


# Пролог, общий для всех операций: находит экземпляр api и окно, которому он
# принадлежит. Экземпляр создаётся приложением редактора внутри iframe
# (`this.api = new Asc.spreadsheet_api(...)` в app.js), поэтому из top-level
# контекста до него надо спуститься по фреймам — как и до кнопки «Жирный».
# Проверяем несколько известных мест и подтверждаем находку по наличию
# asc_EditSelectAll: так код не зависит от того, под каким именно глобальным
# именем сборка держит api.
_API_PRELUDE = r"""
  function apiOf(win) {
    var cands = [];
    try { if (win.Asc && win.Asc.editor) cands.push(win.Asc.editor); } catch (e) {}
    try { if (win.editor) cands.push(win.editor); } catch (e) {}
    try {
      if (win.SSE && win.SSE.getController) {
        cands.push(win.SSE.getController('Main').api);
      }
    } catch (e) {}
    for (var i = 0; i < cands.length; i++) {
      try {
        if (cands[i] && typeof cands[i].asc_EditSelectAll === 'function') return cands[i];
      } catch (e) {}
    }
    return null;
  }
  function findApi(win, depth) {
    var a = apiOf(win);
    if (a) return { api: a, win: win, depth: depth };
    if (depth > 4) return null;
    var frames;
    try { frames = win.document.querySelectorAll('iframe'); } catch (e) { return null; }
    for (var i = 0; i < frames.length; i++) {
      try {
        var got = findApi(frames[i].contentWindow, depth + 1);
        if (got) return got;
      } catch (e) {}
    }
    return null;
  }
  // Снимок состояния документа. Всё — синхронные геттеры внутри страницы,
  // поэтому снять его до и сразу после операции стоит доли миллисекунды
  // (в отличие от повторного round-trip по websocket).
  function docState(api, win) {
    var st = { sheets: null, active: null, selection: null,
               historyIndex: null, historyPoints: null, canUndo: null };
    try { st.sheets = api.asc_getWorksheetsCount(); } catch (e) {}
    try { st.active = api.asc_getActiveWorksheetIndex(); } catch (e) {}
    try { st.selection = api.asc_getActiveRangeStr(); } catch (e) {}
    try {
      var H = win.AscCommon && win.AscCommon.History;
      if (H) {
        if (typeof H.Index === 'number') st.historyIndex = H.Index;
        if (H.Points && typeof H.Points.length === 'number') st.historyPoints = H.Points.length;
        if (typeof H.Can_Undo === 'function') st.canUndo = !!H.Can_Undo();
      }
    } catch (e) {}
    return st;
  }
  function insertOpt(win, name, fallback) {
    try {
      var opts = win.Asc && win.Asc.c_oAscInsertOptions;
      if (opts && typeof opts[name] === 'number') return opts[name];
    } catch (e) {}
    return fallback;
  }
"""


# Литерал, которым каждый op-body отмечает снятие снимка «после». Один и тот
# же текст во всех семи операциях (см. _SELECT_ALL_JS и соседей) — поэтому
# _op_js может внедрить туда остановку таймера строковой заменой, не трогая
# тела самих операций.
_AFTER_SNAPSHOT_LINE = "    st.after = docState(api, win);\n"


def _op_js(body):
    """Собирает JS одной операции: пролог + поиск api + тело в try/catch.

    Тело обязано возвращать объект с полями:
        ok      — операция выполнена;
        mutated — успел ли код тронуть документ ДО ошибки. Это поле читает
                  r7_Testovarka.py, решая, можно ли откатиться на pyautogui:
                  повторить операцию клавишами безопасно только если
                  документ ещё не изменён, иначе правка применится дважды.
        api_ms  — синхронное время внутри рендерера между снимками «до» и
                  «после», мс (performance.now(), разрешение <1 мс). ЗАЧЕМ:
                  до этого поля единственным источником длительности был
                  детектор простоя Р7 (_wait_operation_done) — окно
                  усреднения CPU 0.20 с и порог 6 подтверждений подряд, то
                  есть разрешение ~0.3 с. Операции через CDP стали короче
                  этого окна, и результат превратился в подбрасывание
                  монеты между «поймали момент занятости» (~секунда) и «не
                  поймали» (below_floor, миллисекунды) — до 20× разброса
                  между двумя прогонами одного файла (см. отчёт по
                  нагрузочному тестированию, 25.08.2026). api_ms не зависит
                  от опроса CPU вообще: это время самого вызова, измеренное
                  на том же потоке, где он выполняется.
                  Отсутствует (не входит в st), если операция не дошла до
                  строки _AFTER_SNAPSHOT_LINE — метод не найден
                  (no-method:...) или api не найден вовсе (api-not-found):
                  в обоих случаях измерять нечего. При исключении ПОСЛЕ
                  старта таймера api_ms всё же выставляется (время до сбоя
                  тоже диагностически полезно) — см. блок catch ниже.

    Args:
        body: JS-инструкции; в области видимости есть api, win, st, __t0.
            Обязан содержать ровно одно вхождение _AFTER_SNAPSHOT_LINE —
            иначе таймер не остановится и api_ms в ответ не попадёт.

    Returns:
        str: выражение (IIFE), готовое для Runtime.evaluate.
    """
    timed_body = body.replace(
        _AFTER_SNAPSHOT_LINE,
        "    st.api_ms = performance.now() - __t0;\n" + _AFTER_SNAPSHOT_LINE,
        1,
    )
    return (
        "(function () {\n"
        + _API_PRELUDE
        + "  var f = findApi(window, 0);\n"
        "  if (!f) return { ok: false, mutated: false, reason: 'api-not-found' };\n"
        "  var api = f.api, win = f.win;\n"
        "  var st = { ok: false, mutated: false, frame: f.depth };\n"
        "  var __t0;\n"
        "  try {\n"
        "    st.before = docState(api, win);\n"
        "    __t0 = performance.now();\n"
        + timed_body
        + "\n  } catch (e) {\n"
        "    if (typeof __t0 === 'number') st.api_ms = performance.now() - __t0;\n"
        "    st.reason = 'exception';\n"
        "    st.error = String((e && e.message) || e);\n"
        "    return st;\n"
        "  }\n"
        "})()\n"
    )


def _need(method):
    """JS-проверка наличия метода в api — до того, как что-то менять."""
    return ("    if (typeof api.%s !== 'function') {\n"
            "      st.reason = 'no-method:%s';\n"
            "      return st;\n"
            "    }\n" % (method, method))


_SELECT_ALL_JS = _op_js(
    _need("asc_EditSelectAll")
    + "    api.asc_EditSelectAll();\n"
      "    st.ok = true;\n"
      "    st.method = 'asc_EditSelectAll';\n"
      "    st.after = docState(api, win);\n"
      "    return st;\n"
)

_COPY_JS = _op_js(
    _need("asc_Copy")
    + "    st.result = api.asc_Copy();\n"
      "    st.ok = st.result !== false;\n"
      "    st.method = 'asc_Copy';\n"
      "    st.after = docState(api, win);\n"
      "    return st;\n"
)

_PASTE_JS = _op_js(
    _need("asc_Paste")
    + "    st.mutated = true;\n"
      "    st.result = api.asc_Paste();\n"
      "    st.ok = st.result !== false;\n"
      "    st.method = 'asc_Paste';\n"
      "    st.after = docState(api, win);\n"
      "    return st;\n"
)

_ADD_SHEET_JS = _op_js(
    _need("asc_addWorksheet")
    + "    st.mutated = true;\n"
      "    st.result = api.asc_addWorksheet();\n"
      "    st.ok = true;\n"
      "    st.method = 'asc_addWorksheet';\n"
      "    st.after = docState(api, win);\n"
      "    return st;\n"
)

_STATE_JS = (
    "(function () {\n"
    + _API_PRELUDE
    + "  var f = findApi(window, 0);\n"
    "  if (!f) return null;\n"
    "  try { return docState(f.api, f.win); } catch (e) { return null; }\n"
    "})()\n"
)


def _insert_cells_js(option_name, fallback):
    """JS вставки ячеек/столбцов: asc_insertCells с константой сдвига."""
    return _op_js(
        _need("asc_insertCells")
        + "    var opt = insertOpt(win, %r, %d);\n"
          "    st.option = opt;\n"
          "    st.mutated = true;\n"
          "    api.asc_insertCells(opt);\n"
          "    st.ok = true;\n"
          "    st.method = 'asc_insertCells';\n"
          "    st.after = docState(api, win);\n"
          "    return st;\n" % (option_name, fallback)
    )


def _select_range_js(ref):
    """JS выделения диапазона по ссылке вида A1:E1 — то же, что ввести её в
    поле «Имя» слева от строки формул (asc_findCell)."""
    return _op_js(
        _need("asc_findCell")
        + "    api.asc_findCell(%s);\n"
          "    st.method = 'asc_findCell';\n"
          "    st.after = docState(api, win);\n"
          "    st.ok = st.after.selection !== null;\n"
          "    return st;\n" % json.dumps(ref)
    )


def _show_sheet_js(target, relative=False):
    """JS переключения на лист (аналог Ctrl+PageUp/PageDown).

    Args:
        target: Индекс листа, либо смещение относительно активного, если
            relative=True (-1 — лист левее, как Ctrl+PageUp).
        relative: Трактовать target как смещение, а не как абсолютный индекс.

    Индекс в любом случае прижимается к границам [0, листов-1]: Ctrl+PageUp на
    первом листе тоже никуда не уходит, а не падает.
    """
    return _op_js(
        _need("asc_showWorksheet")
        + "    var idx = %d;\n"
          "    if (%s) {\n"
          "      var cur = st.before.active;\n"
          "      if (typeof cur !== 'number') { st.reason = 'no-active-sheet'; return st; }\n"
          "      idx = cur + idx;\n"
          "    }\n"
          "    if (idx < 0) idx = 0;\n"
          "    var total = st.before.sheets;\n"
          "    if (typeof total === 'number' && idx > total - 1) idx = total - 1;\n"
          "    st.index = idx;\n"
          "    api.asc_showWorksheet(idx);\n"
          "    st.method = 'asc_showWorksheet';\n"
          "    st.after = docState(api, win);\n"
          "    st.ok = st.after.active === idx;\n"
          "    return st;\n" % (int(target), "true" if relative else "false")
    )


def _click_by_text_js(wanted, baseline=None, tolerance=30):
    """JS клика по пункту меню, чья подпись содержит одну из подстрок.

    Тот же приём, что и в _DISMISS_SAVE_DIALOG_JS, но с двумя отличиями,
    без которых им нельзя пользоваться для контекстного меню:

    1. ПРИОРИТЕТ ПО СПИСКУ, А НЕ ПО ПОРЯДКУ В ДОКУМЕНТЕ. Кандидаты сперва
       собираются целиком, и только потом выбирается первый подошедший под
       wanted[0], затем wanted[1] и т.д. Иначе «Копировать формат», стоящий в
       разметке выше, перехватил бы клик, предназначенный «Копировать».
    2. ВЫЧИТАНИЕ БАЗОВОГО СНИМКА. По issue #9 в дампе видимых элементов
       постоянно висит overflow-меню тулбара — статичный кусок интерфейса с
       такими же подписями, как у пунктов контекстного меню. Клик по нему
       вместо пункта меню — это нажатие произвольной кнопки тулбара посреди
       замера. Поэтому элементы, которые были на экране ещё ДО right-click
       (baseline, см. _capture_cdp_ui_baseline), из кандидатов исключаются:
       совпадение по подписи/тегу/классу И по положению с допуском tolerance.

    Args:
        wanted: Подписи в порядке приоритета (подстроки, регистр не важен).
        baseline: Список элементов базового снимка ({text, tag, cls, id, x, y})
            либо None — тогда ничего не вычитается.
        tolerance: Допуск совпадения координат с базовым снимком, px.

    Returns:
        str: выражение (IIFE) для Runtime.evaluate, возвращающее
        {clicked, text, matched} либо null.
    """
    return r"""
(function () {
  var WANTED = %s;
  var BASE = %s;
  var TOL = %d;
  var MENU_SEL = '.dropdown-menu li, .menu-item, [role="menuitem"], ' +
                 '.asc-window button, .modal button';
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
  function inBaseline(c) {
    for (var i = 0; i < BASE.length; i++) {
      var b = BASE[i];
      if (b.text !== c.text || b.tag !== c.tag) continue;
      if ((b.id || '') !== c.id || (b.cls || '') !== c.cls) continue;
      if (Math.abs((b.x || 0) - c.x) <= TOL && Math.abs((b.y || 0) - c.y) <= TOL) {
        return true;
      }
    }
    return false;
  }
  var found = [];
  function collect(doc, depth) {
    if (depth > 4 || found.length >= 80) return;
    var nodes;
    try { nodes = doc.querySelectorAll(MENU_SEL); } catch (e) { nodes = []; }
    for (var i = 0; i < nodes.length && found.length < 80; i++) {
      var el = nodes[i];
      var txt = label(el);
      if (!txt || txt.length > 60) continue;
      if (!visible(el)) continue;
      var r = el.getBoundingClientRect();
      found.push({
        el: el, text: txt, low: txt.toLowerCase(),
        tag: el.tagName.toLowerCase(),
        cls: (el.className && el.className.toString().slice(0, 40)) || '',
        id: el.id || '',
        x: Math.round(r.left), y: Math.round(r.top)
      });
    }
    var frames;
    try { frames = doc.querySelectorAll('iframe'); } catch (e) { return; }
    for (var j = 0; j < frames.length; j++) {
      try { collect(frames[j].contentDocument, depth + 1); } catch (e) {}
    }
  }
  collect(document, 0);
  var fresh = [];
  for (var k = 0; k < found.length; k++) {
    if (!inBaseline(found[k])) fresh.push(found[k]);
  }
  // Два прохода: сперва точное совпадение подписи, и только потом вхождение
  // подстроки. Иначе «Копировать формат» перехватывал бы клик, адресованный
  // пункту «Копировать», — он тоже содержит эту подстроку.
  function pick(exact) {
    for (var w = 0; w < WANTED.length; w++) {
      for (var n = 0; n < fresh.length; n++) {
        var hit = exact ? (fresh[n].low === WANTED[w])
                        : (fresh[n].low.indexOf(WANTED[w]) !== -1);
        if (!hit) continue;
        try {
          fresh[n].el.click();
          return { clicked: true, text: fresh[n].text, matched: WANTED[w],
                   exact: exact, candidates: fresh.length };
        } catch (e) {}
      }
    }
    return null;
  }
  return pick(true) || pick(false) || { clicked: false, candidates: fresh.length };
})()
""" % (json.dumps([str(w).lower() for w in wanted]),
       json.dumps(baseline or []),
       int(tolerance))


# Порт, на котором Р7 (Qt+CEF) реально поднимает CDP-сервер при передаче
# --ascdesktop-support-debug-info — подтверждено r7-desktop-selenium
# (conftest.old: wait_for_port("127.0.0.1", 8080) + debugger_address =
# 'localhost:8080'). Не параметр запуска — судя по всему, зашит внутри Р7.
DEFAULT_CDP_PORT = 8080


# Флаги командной строки, включающие CDP-порт у CEF-приложения. Должны
# передаваться в subprocess.Popen ТОЛЬКО в момент запуска Р7 (см. docstring
# модуля) — на уже работающий процесс не действуют.
def r7_launch_debug_args(port=None):
    """Аргументы командной строки для subprocess.Popen, открывающие CDP-порт.

    Args:
        port: TCP-порт CDP-сервера. По умолчанию None — тогда добавляется
            только --ascdesktop-support-debug-info (порт фиксирован на
            DEFAULT_CDP_PORT, см. модульный docstring). Явный port добавляет
            --remote-debugging-port=<port> ДОПОЛНИТЕЛЬНО — если реальная
            сборка Р7 его не поддерживает, флаг просто будет проигнорирован
            (безопасно передать на пробу), но полагаться на него как на
            подтверждённое поведение нельзя.

    Returns:
        list[str]: аргументы, добавляемые к [r7_path, file_path] в Popen.
    """
    args = ["--ascdesktop-support-debug-info"]
    if port is not None:
        args.append(f"--remote-debugging-port={port}")
    return args


def get_free_port():
    """Находит свободный TCP-порт на localhost (bind на порт 0 и чтение
    назначенного ОС номера). Вызывать один раз перед запуском Р7 и
    использовать тот же порт в r7_launch_debug_args и R7WebDriverConnector —
    между вызовом этой функции и стартом процесса Р7 порт теоретически может
    быть перехвачен другим процессом (обычная гонка bind-then-use), поэтому
    connect() ниже не должен считать провал подключения фатальной ошибкой.

    Returns:
        int: номер свободного порта.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class R7WebDriverConnector:
    """Подключается к CDP-порту уже запущенного (с флагами выше) процесса
    Р7-Офис и даёт один метод для опроса состояния кнопки «Жирный».

    Использование:
        connector = R7WebDriverConnector(port, log_cb=self.add_test_log)
        if connector.connect(timeout=5):
            state = connector.bold_button_state()   # None | {"found":.., "disabled":..}
            ...
        connector.close()

    Один экземпляр — на один запуск Р7 (порт назначается заново при каждом
    Popen). Не потокобезопасен для параллельных вызовов bold_button_state() —
    вызывается из того же фонового потока, что и остальной опрос готовности
    в _wait_until_r7_ready, поэтому в текущей архитектуре r7_Testovarka.py
    (один поток на один прогон теста) это не проблема.
    """

    def __init__(self, port=DEFAULT_CDP_PORT, log_cb=None, filename_hint=None):
        """Args:
            port: CDP-порт запуска Р7 (см. DEFAULT_CDP_PORT).
            log_cb: Функция логирования; по умолчанию no-op.
            filename_hint: Имя файла (Path(...).name), который должна открыть
                та цель, к которой подключается connect(). Нужен, когда в
                одном экземпляре Р7 открыто больше одного документа: /json
                тогда отдаёт несколько целей с "doctype=" в URL одновременно
                (проверено на живом Р7, 25.08.2026 — см. _pick_target), и
                "первая подходящая" — не то же самое, что "нужная". None —
                прежнее поведение (первая подходящая цель); безопасно для
                всех текущих вызывающих мест, где документ всегда один.
        """
        self.port = port
        self.log_cb = log_cb or (lambda msg: None)
        self.filename_hint = filename_hint
        self._last_target_filename = None  # см. _pick_target/_target_filename —
                                            # кэш последнего совпадения, чтобы
                                            # connect() не парсил URL повторно
        self._backend = None          # "selenium" | "cdp" | None (не подключён)
        self._driver = None           # selenium.webdriver.Chrome, если backend == "selenium"
        self._ws = None                # websocket.WebSocket, если backend == "cdp"
        self._ws_msg_id = 0

    # ── Подключение ──────────────────────────────────────────────────────
    def connect(self, timeout=5.0, poll_sec=0.2):
        """Пытается подключиться: сначала Selenium (то, что запросил
        пользователь), при любой неудаче — голый CDP по websocket. Ждёт
        появления CDP-порта до timeout секунд (CEF открывает его не сразу
        после старта процесса).

        Идемпотентен: если уже подключён (второй вызов на том же
        экземпляре — например, из нескольких опросов bold_button_state()
        подряд в _wait_until_r7_ready), возвращает True немедленно, не
        переподключаясь.

        Args:
            timeout: секунд ожидания появления работоспособного порта.
            poll_sec: интервал между попытками.

        Returns:
            bool: True, если удалось подключиться (или уже было подключено).
        """
        if self._backend is not None:
            return True
        if not WEBDRIVER_OK:
            self.log_cb("⚠️ WebDriver: пакеты 'requests'/'websocket-client' не установлены")
            return False

        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            target = self._pick_target()
            if target is None:
                last_err = "CDP-порт не отвечает (ещё не открылся или Р7 запущен без debug-флагов)"
                time.sleep(poll_sec)
                continue

            if self.filename_hint is not None:
                # _last_target_filename выставлен _pick_target() для этой же
                # target — не парсим URL повторно.
                matched = self._last_target_filename or "?"
                self.log_cb(f"🔍 CDP-цель: {matched} (по файлу {self.filename_hint})")

            if self._try_connect_selenium(target):
                self._backend = "selenium"
                self.log_cb("🔌 WebDriver: подключено через Selenium (attach)")
                return True

            if self._try_connect_cdp(target):
                self._backend = "cdp"
                self.log_cb("🔌 WebDriver: подключено через голый CDP (Selenium недоступен/несовместим)")
                return True

            last_err = "цель CDP найдена, но ни Selenium, ни голый CDP не подключились"
            time.sleep(poll_sec)

        self.log_cb(f"⚠️ WebDriver: не удалось подключиться за {timeout:.1f} с ({last_err})")
        return False

    @staticmethod
    def _target_filename(target):
        """Настоящее имя открытого документа для CDP-цели, либо None.

        Не поле target["title"] — оно у ВСЕХ целей-редакторов одинаковое
        ("R7-OFFICE Documents", проверено на живом Р7), бесполезно для
        различения документов. Имя лежит в query-параметре "title=" самого
        URL цели (например, "...&title=test_50k.xlsx&..."), декодированное
        через urllib.parse — подстрочный поиск сломался бы на именах с
        пробелами/кириллицей (URL-экранирование).

        Args:
            target: Один элемент ответа /json.

        Returns:
            str | None: Имя файла, либо None, если параметра нет в URL.
        """
        query = urllib.parse.urlparse(target.get("url", "")).query
        titles = urllib.parse.parse_qs(query).get("title") or []
        return titles[0] if titles else None

    def _pick_target(self):
        """Возвращает dict цели-редактора из http://127.0.0.1:{port}/json,
        либо None, если порт не отвечает или редактор ещё не загрузился.
        Вызывается только когда WEBDRIVER_OK (проверено в connect()).

        ПРОВЕРЕНО НА ЖИВОЙ Р7 (не предположение): /json на порту 8080 отдаёт
        ДВЕ цели с type == "page" одновременно — сплэш-скрин ("Hello
        Documents", url ...index.html?waitingloader=yes...) и сам редактор
        ("R7-OFFICE Documents", url ...doctype=spreadsheet...). Наивный
        "первая цель с type==page" (как было в первой версии этого метода)
        подключился бы к сплэшу, а не к редактору — кнопки там нет и не
        будет. Фильтруем по "doctype=" в URL — оно есть в реальной странице
        редактора (spreadsheet/document/presentation) и отсутствует в
        сплэше. Пока подходящей цели нет (редактор ещё грузится) —
        возвращаем None, а не откатываемся на первую попавшуюся: вызывающий
        connect() и так поллит до появления, откат на сплэш замаскировал бы
        реальную задержку загрузки под "порт не отвечает".

        ФИЛЬТР ПО self.filename_hint (H5, 25.08.2026): при нескольких
        открытых документах /json отдаёт НЕСКОЛЬКО целей с "doctype=" в URL
        одновременно — по одной на документ. ПРОВЕРЕНО НА ЖИВОЙ Р7: у всех
        таких целей поле "title" — одна и та же генерическая строка
        "R7-OFFICE Documents" (бесполезна для различения), а вот в самом URL
        есть query-параметр "title=<имя_файла>" с настоящим именем открытого
        документа — например, "...&title=test_50k.xlsx&...". Именно этот
        параметр и сравнивается с filename_hint (через urllib.parse, а не
        подстрокой: имя файла может быть URL-экранировано — пробелы,
        кириллица).
        

        Побочный эффект: пишет имя выбранной цели в self._last_target_filename
        — connect() читает его для лога вместо повторного парсинга того же
        URL сразу после (_target_filename делает round-trip по urllib.parse,
        которого он не стоит дважды на одну и ту же цель).
        """
        try:
            resp = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=1.0)
            resp.raise_for_status()
            targets = resp.json()
        except Exception:
            return None

        candidates = [t for t in targets
                     if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
                     and "doctype=" in t.get("url", "")]
        if not candidates:
            return None
        if self.filename_hint is None:
            return candidates[0]

        for t in candidates:
            fn = self._target_filename(t)
            if fn == self.filename_hint:
                self._last_target_filename = fn
                return t

        # Подсказка задана, но ни одна цель ей не соответствует — редактор
        # мог ещё не проставить title= в URL для только что открытого
        # документа, либо документ был переименован/пересохранён. Не
        # возвращаем None (это заставило бы connect() решить, что редактор
        # вообще не загрузился, и поллить впустую до таймаута) — берём
        # первую подходящую цель, как до появления фильтра, но громко
        # предупреждаем: вызывающий код мог получить не тот документ.
        self.log_cb(
            f"⚠️ CDP: среди {len(candidates)} целей нет документа "
            f"{self.filename_hint!r} — подключаюсь к первой найденной "
            f"(может оказаться не тем документом)"
        )
        self._last_target_filename = self._target_filename(candidates[0])
        return candidates[0]

    def _try_connect_selenium(self, target):
        """Пытается подключиться через selenium.webdriver.Chrome с
        debuggerAddress. Ловит ЛЮБОЕ исключение — несовпадение версии
        chromedriver/CEF, отсутствие chromedriver в PATH и т.п. — это
        ожидаемый, а не аварийный путь (см. docstring модуля).
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError:
            return False
        try:
            options = Options()
            options.add_experimental_option("debuggerAddress", f"127.0.0.1:{self.port}")
            driver = webdriver.Chrome(options=options)
            driver.execute_script("return 1")  # быстрая проверка, что сессия реально живая
            if not self._switch_to_target(driver, target):
                # На порту одновременно живут сплэш и редактор (см.
                # _pick_target) — attach через debuggerAddress цепляется к
                # "текущему" окну chromedriver произвольно, независимо от
                # того, какую цель нашёл _pick_target(). Раньше target
                # вообще не использовался: bold_button_state()/
                # dismiss_save_dialog() могли молча опрашивать сплэш и
                # никогда не находить кнопку. Не нашли вкладку редактора —
                # отцепляемся и уходим на голый CDP, а не притворяемся
                # подключёнными не к тому документу.
                self.log_cb("ℹ️ WebDriver: Selenium attach не нашёл вкладку редактора "
                            "среди открытых окон — пробую голый CDP")
                try:
                    driver.stop_client()
                except Exception:
                    pass
                return False
            self._driver = driver
            return True
        except Exception as e:
            self.log_cb(f"ℹ️ WebDriver: Selenium attach не удался ({type(e).__name__}: {e}) — пробую голый CDP")
            return False

    def _switch_to_target(self, driver, target):
        """Переключает driver на вкладку редактора — ту, что нашёл
        _pick_target(), а не ту, что chromedriver считает "текущей" по
        умолчанию при attach через debuggerAddress.

        Формат window handle у chromedriver не гарантированно совпадает с
        id цели из /json (сопоставлять их напрямую ненадёжно), поэтому
        сначала ищем вкладку с ТОЧНО тем же URL, что вернул _pick_target()
        (target["url"]). Если между вызовом _pick_target() и этой проверкой
        URL успел измениться (документ продолжает грузиться) — второй
        проход использует тот же критерий, что и сам _pick_target()
        ("doctype=" в URL), и так же надёжно отличает редактор от сплэша.

        Returns:
            bool: True, если подходящая вкладка найдена и стала активной.
        """
        try:
            handles = driver.window_handles
        except Exception:
            return False
        wanted_url = target.get("url", "")
        for h in handles:
            try:
                driver.switch_to.window(h)
                if wanted_url and driver.current_url == wanted_url:
                    return True
            except Exception:
                continue
        for h in handles:
            try:
                driver.switch_to.window(h)
                if "doctype=" in (driver.current_url or ""):
                    return True
            except Exception:
                continue
        return False

    def _try_connect_cdp(self, target):
        """Открывает websocket напрямую на webSocketDebuggerUrl цели.
        Не требует chromedriver и не проверяет версию протокола. Вызывается
        только когда WEBDRIVER_OK (проверено в connect()).
        """
        try:
            ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=2.0)
            self._ws = ws
            return True
        except Exception as e:
            self.log_cb(f"⚠️ WebDriver: подключение по CDP websocket не удалось ({type(e).__name__}: {e})")
            return False

    # ── Опрос состояния кнопки ──────────────────────────────────────────
    def bold_button_state(self):
        """Выполняет _FIND_BOLD_BUTTON_JS через активный бэкенд.

        Returns:
            dict | None: {"found": bool, "disabled": bool} либо None при
            ошибке выполнения (соединение оборвалось и т.п. — вызывающий
            код должен трактовать это как "неизвестно", не как "не найдено").
        """
        return self.evaluate(_FIND_BOLD_BUTTON_JS)

    def dismiss_save_dialog(self):
        """Жмёт «Не сохранять» в модалке выхода, если она сейчас на экране.

        Кнопка ищется по тексту в DOM (см. _DISMISS_SAVE_DIALOG_JS) — через
        win32gui она недостижима в принципе.

        Returns:
            dict | None: {"clicked": True, "text": ...}, если кнопка нашлась
            и была нажата; None — если модалки нет, кнопка не найдена или
            соединение недоступно. None НЕ означает, что модалки точно нет.
        """
        return self.evaluate(_DISMISS_SAVE_DIALOG_JS)

    def dump_visible_ui(self):
        """Список видимых кнопок и пунктов меню — диагностика слепых мест.

        Returns:
            list[dict] | None: элементы {text, tag, cls, id}, либо None, если
            CDP недоступен.
        """
        return self.evaluate(_DUMP_VISIBLE_UI_JS)

    # ── Операции над документом (см. блок _op_js выше) ───────────────────
    # Все методы ниже возвращают один и тот же тип: dict с полями ok/mutated
    # (плюс before/after — снимки состояния документа), либо None, если CDP
    # недоступен или ответ не пришёл. None означает «неизвестно», а НЕ
    # «не выполнено»: см. _cdp_call в r7_Testovarka.py, где это различие
    # решает, безопасно ли повторить операцию клавишами.

    def document_state(self, timeout=None):
        """Снимок состояния документа: листы, активный лист, выделение,
        позиция в истории правок.

        Нужен, чтобы проверить результат операции уже ПОСЛЕ закрытия окна
        замера (round-trip по websocket иначе попал бы в цифру).

        Returns:
            dict | None: {sheets, active, selection, historyIndex,
            historyPoints, canUndo}, либо None.
        """
        return self.evaluate(_STATE_JS, timeout=timeout)

    def select_all(self, timeout=None):
        """Выделяет все ячейки листа — эквивалент Ctrl+A (asc_EditSelectAll)."""
        return self.evaluate(_SELECT_ALL_JS, timeout=timeout)

    def copy(self, timeout=None):
        """Копирует выделение в буфер обмена — эквивалент Ctrl+C (asc_Copy).

        На десктопной сборке asc_Copy уходит в нативный буфер обмена
        (asc_desktop_copypaste), то есть туда же, куда и настоящий хоткей, —
        поэтому вставить скопированное можно и клавишами, и через paste().
        """
        return self.evaluate(_COPY_JS, timeout=timeout)

    def paste(self, timeout=None):
        """Вставляет из буфера обмена — эквивалент Ctrl+V (asc_Paste)."""
        return self.evaluate(_PASTE_JS, timeout=timeout)

    def add_sheet(self, timeout=None):
        """Добавляет новый лист — эквивалент Shift+F11 (asc_addWorksheet)."""
        return self.evaluate(_ADD_SHEET_JS, timeout=timeout)

    def insert_column(self, timeout=None):
        """Вставляет столбец — эквивалент Ctrl+Shift+= / меню «Вставка»
        (asc_insertCells с c_oAscInsertOptions.InsertColumns)."""
        return self.evaluate(_insert_cells_js("InsertColumns", INSERT_COLUMNS),
                             timeout=timeout)

    def insert_cells(self, shift="down", timeout=None):
        """Вставляет ячейки со сдвигом — то, что делает пункт контекстного
        меню «Вставить ячейки» и следующая за ним модалка выбора сдвига.

        Args:
            shift: "down" (сдвиг вниз) или "right" (сдвиг вправо).
            timeout: Таймаут ожидания ответа CDP, сек.
        """
        if shift == "right":
            name, fallback = "InsertCellsAndShiftRight", INSERT_SHIFT_RIGHT
        else:
            name, fallback = "InsertCellsAndShiftDown", INSERT_SHIFT_DOWN
        return self.evaluate(_insert_cells_js(name, fallback), timeout=timeout)

    def select_range(self, ref, timeout=None):
        """Выделяет диапазон по ссылке ("A1:E1") — то же, что ввести её в
        поле «Имя» слева от строки формул (asc_findCell)."""
        return self.evaluate(_select_range_js(ref), timeout=timeout)

    def show_sheet(self, target, relative=False, timeout=None):
        """Переключается на лист — аналог Ctrl+PageUp/PageDown.

        Args:
            target: Индекс листа, либо смещение от активного при relative=True.
            relative: Трактовать target как смещение (-1 — лист левее).
            timeout: Таймаут ожидания ответа CDP, сек.
        """
        return self.evaluate(_show_sheet_js(int(target), relative=relative),
                             timeout=timeout)

    def click_menu_item(self, wanted, baseline=None, timeout=None):
        """Кликает пункт раскрытого меню, чья подпись содержит одну из
        подстрок `wanted` (регистр не важен, порядок = приоритет).

        Args:
            wanted: Подписи в порядке приоритета.
            baseline: Базовый DOM-снимок, снятый ДО открытия меню — его
                элементы из кандидатов исключаются (см. _click_by_text_js:
                без этого клик может уйти в статичное overflow-меню тулбара,
                issue #9).
            timeout: Таймаут ожидания ответа CDP, сек.

        Returns:
            dict | None: {"clicked": bool, "text": ..., "matched": ...,
            "candidates": N}. clicked=False означает, что подходящего пункта
            в обходимом DOM нет — в том числе когда меню нарисовано нативным
            оверлеем CEF (issue #9). None — CDP недоступен.
        """
        return self.evaluate(_click_by_text_js(wanted, baseline=baseline),
                             timeout=timeout)

    def api_info(self, timeout=None):
        """Диагностика: найден ли внутренний api редактора и какие из нужных
        методов у него есть. Ответ пишется в лог один раз за запуск Р7 —
        без него «CDP-операция не сработала» неотличимо от «api не найден».

        Returns:
            dict | None: {found, frame, methods: {имя: bool}, state}, либо None.
        """
        methods = ("asc_EditSelectAll", "asc_Copy", "asc_Paste",
                   "asc_addWorksheet", "asc_insertCells", "asc_findCell",
                   "asc_showWorksheet", "asc_getWorksheetsCount",
                   "asc_getActiveRangeStr")
        js = (
            "(function () {\n"
            + _API_PRELUDE
            + "  var f = findApi(window, 0);\n"
            "  if (!f) return { found: false };\n"
            "  var api = f.api, out = {};\n"
            "  var names = " + json.dumps(list(methods)) + ";\n"
            "  for (var i = 0; i < names.length; i++) {\n"
            "    try { out[names[i]] = typeof api[names[i]] === 'function'; }\n"
            "    catch (e) { out[names[i]] = false; }\n"
            "  }\n"
            "  var st = null;\n"
            "  try { st = docState(api, f.win); } catch (e) {}\n"
            "  return { found: true, frame: f.depth, methods: out, state: st };\n"
            "})()\n"
        )
        return self.evaluate(js, timeout=timeout)

    def evaluate(self, js, timeout=None):
        """Выполняет произвольное JS-выражение в контексте страницы Р7.

        Вынесено из bold_button_state(), который раньше был единственным
        потребителем и жёстко подставлял _FIND_BOLD_BUTTON_JS. Понадобилось
        для закрытия HTML-модалки «Сохранить изменения?» при выходе из Р7:
        её кнопки — тоже DOM, а не Win32-виджеты, поэтому win32gui их не
        видит ровно по той же причине, что и кнопку «Жирный».

        Args:
            js: JS-выражение (не statement!) — результат возвращается
                вызывающему. Должно быть безопасно для многократного вызова.
            timeout: Сколько ждать ответа, сек (только бэкенд cdp; None —
                таймаут сокета, заданный при подключении). Нужен для
                операций над документом: Runtime.evaluate возвращается
                только когда JS отработал, а asc_EditSelectAll на файле в
                миллионы ячеек считает агрегаты статусной строки заметно
                дольше стандартных 2 с. Селениум-бэкенд аргумент игнорирует.

        Returns:
            Любое JSON-сериализуемое значение, вернувшееся из JS, либо None
            при ошибке выполнения/отсутствии соединения. None означает
            "неизвестно", а не "false".
        """
        if self._backend == "selenium":
            return self._eval_selenium(js)
        if self._backend == "cdp":
            return self._eval_cdp(js, timeout=timeout)
        return None

    def _eval_selenium(self, js):
        try:
            # Скобки и .strip() здесь обязательны, а не косметика. Выражения
            # в этом модуле начинаются с перевода строки (тройные кавычки), и
            # наивное f"return {js}" давало «return\n(function…)()»: JS по
            # правилам ASI вставляет точку с запятой сразу после `return`,
            # IIFE выполняется как отдельное выражение, а execute_script
            # всегда возвращает undefined → None. То есть на машине с
            # установленным selenium (его бэкенд выбирается первым) и
            # bold_button_state, и dismiss_save_dialog молча не работали
            # никогда, а вызывающий код видел это как «кнопка не найдена».
            result = self._driver.execute_script(f"return ({js.strip()});")
            return result
        except Exception as e:
            self.log_cb(f"⚠️ WebDriver(selenium): ошибка опроса ({type(e).__name__}: {e})")
            return None

    def _eval_cdp(self, js, timeout=None):
        # Таймаут сокета поднимается только на время этого вызова и
        # возвращается обратно в finally: держать его большим постоянно
        # значило бы, что оборвавшееся соединение (Р7 закрывается) будет
        # обнаружено с задержкой во весь этот таймаут в каждом опросе.
        prev_timeout = None
        try:
            if timeout is not None and self._ws is not None:
                try:
                    prev_timeout = self._ws.gettimeout()
                    self._ws.settimeout(timeout)
                except Exception:
                    prev_timeout = None
            self._ws_msg_id += 1
            msg = {
                "id": self._ws_msg_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": js,
                    "returnByValue": True,
                    "awaitPromise": False,
                },
            }
            self._ws.send(json.dumps(msg))
            # CDP может прислать не-ответные события раньше ответа на наш id —
            # читаем, пока не встретим сообщение с нужным id, либо не истечёт
            # разумное число попыток.
            for _ in range(20):
                raw = self._ws.recv()
                data = json.loads(raw)
                if data.get("id") == self._ws_msg_id:
                    result = data.get("result", {}).get("result", {})
                    if result.get("subtype") == "error" or "exceptionDetails" in data.get("result", {}):
                        return None
                    return result.get("value")
            return None
        except Exception as e:
            # Обрыв соединения — не «попробуем ещё раз»: websocket уже
            # непригоден, и каждый следующий вызов будет падать так же.
            # Р7 рвёт CDP, когда начинает закрываться, а закрытие как раз и
            # опрашивает нас раз в секунду — без разрыва бэкенда лог наполнялся
            # бы одинаковыми ConnectionAbortedError, и вызывающий код продолжал
            # бы ждать от CDP ответа вместо перехода на win32gui.
            # socket.timeout — подкласс OSError, но это тоже НЕ обрыв
            # (см. _is_ws_closed): просто опрос не уложился в таймаут сокета.
            if isinstance(e, socket.timeout):
                self.log_cb("⚠️ WebDriver(cdp): опрос не уложился в таймаут — "
                            "соединение сохраняем")
                return None
            if isinstance(e, (ConnectionError, OSError, EOFError,
                              json.JSONDecodeError)) or _is_ws_closed(e):
                self._mark_disconnected(f"{type(e).__name__}: {e}")
                return None
            self.log_cb(f"⚠️ WebDriver(cdp): ошибка опроса ({type(e).__name__}: {e})")
            return None
        finally:
            if prev_timeout is not None and self._ws is not None:
                try:
                    self._ws.settimeout(prev_timeout)
                except Exception:
                    pass

    def _mark_disconnected(self, reason):
        """Помечает соединение мёртвым: дальше evaluate() сразу отдаёт None.

        Вызывается при обрыве websocket. Без этого каждый последующий опрос
        повторял бы ту же ошибку в лог, а вызывающий код не понимал бы, что
        CDP больше не ответит никогда, и не переключался на win32gui-путь.

        Args:
            reason: Текст для лога — что именно оборвалось.
        """
        if self._backend is None:
            return          # уже пометили, второй раз не шумим
        self._backend = None
        self.log_cb(f"🔌 WebDriver: CDP-соединение потеряно ({reason}) — "
                    f"дальше только win32gui-путь")
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    @property
    def connected(self):
        """True, пока активен рабочий бэкенд (не оборвано)."""
        return self._backend is not None

    # ── Завершение ───────────────────────────────────────────────────────
    def close(self):
        """Освобождает ресурсы. Безопасно вызывать даже без успешного
        connect() (например, если запуск Р7 в этом прогоне вообще не
        добавлял debug-флаги). Не поднимает исключений — вызывается из
        finally на стороне r7_Testovarka.py.
        """
        if self._driver is not None:
            try:
                # ВАЖНО: не quit(). Сессия подключена к чужому браузеру через
                # debuggerAddress, и quit() закрывает сам браузер — то есть
                # завершает Р7-Офис как побочный эффект «освобождения
                # ресурсов». Сейчас это маскируется тем, что close() зовётся из
                # finally уже после закрытия Р7, но любой вызов раньше по
                # потоку (или прогон, где Р7 намеренно оставили открытым)
                # убивал бы приложение. Нам нужно отцепиться, а не закрыть:
                # достаточно уронить ссылку, отпустив локальный chromedriver.
                self._driver.stop_client()
            except Exception:
                pass
            self._driver = None
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._backend = None
