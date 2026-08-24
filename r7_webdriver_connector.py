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
    try {
      if (el.offsetParent === null && getComputedStyle(el).position !== 'fixed') return false;
      var r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch (e) { return false; }
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
      var txt = (el.textContent || '').trim().replace(/\s+/g, ' ');
      if (!txt || txt.length > 60) continue;
      seen.push(el);
      out.push({
        text: txt,
        tag: el.tagName.toLowerCase(),
        cls: (el.className && el.className.toString().slice(0, 40)) || '',
        id: el.id || ''
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

    def __init__(self, port=DEFAULT_CDP_PORT, log_cb=None):
        self.port = port
        self.log_cb = log_cb or (lambda msg: None)
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
        """
        try:
            resp = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=1.0)
            resp.raise_for_status()
            targets = resp.json()
        except Exception:
            return None
        for t in targets:
            if t.get("type") != "page" or not t.get("webSocketDebuggerUrl"):
                continue
            if "doctype=" in t.get("url", ""):
                return t
        return None

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

    def evaluate(self, js):
        """Выполняет произвольное JS-выражение в контексте страницы Р7.

        Вынесено из bold_button_state(), который раньше был единственным
        потребителем и жёстко подставлял _FIND_BOLD_BUTTON_JS. Понадобилось
        для закрытия HTML-модалки «Сохранить изменения?» при выходе из Р7:
        её кнопки — тоже DOM, а не Win32-виджеты, поэтому win32gui их не
        видит ровно по той же причине, что и кнопку «Жирный».

        Args:
            js: JS-выражение (не statement!) — результат возвращается
                вызывающему. Должно быть безопасно для многократного вызова.

        Returns:
            Любое JSON-сериализуемое значение, вернувшееся из JS, либо None
            при ошибке выполнения/отсутствии соединения. None означает
            "неизвестно", а не "false".
        """
        if self._backend == "selenium":
            return self._eval_selenium(js)
        if self._backend == "cdp":
            return self._eval_cdp(js)
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

    def _eval_cdp(self, js):
        try:
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
