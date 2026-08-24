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
        """Возвращает dict цели из http://127.0.0.1:{port}/json (первую с
        type == 'page'), либо None, если порт не отвечает или подходящей
        цели нет. Вызывается только когда WEBDRIVER_OK (проверено в connect()).
        """
        try:
            resp = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=1.0)
            resp.raise_for_status()
            targets = resp.json()
        except Exception:
            return None
        for t in targets:
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
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
            self._driver = driver
            return True
        except Exception as e:
            self.log_cb(f"ℹ️ WebDriver: Selenium attach не удался ({type(e).__name__}: {e}) — пробую голый CDP")
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
        if self._backend == "selenium":
            return self._eval_selenium()
        if self._backend == "cdp":
            return self._eval_cdp()
        return None

    def _eval_selenium(self):
        try:
            result = self._driver.execute_script(f"return {_FIND_BOLD_BUTTON_JS}")
            return result
        except Exception as e:
            self.log_cb(f"⚠️ WebDriver(selenium): ошибка опроса ({type(e).__name__}: {e})")
            return None

    def _eval_cdp(self):
        try:
            self._ws_msg_id += 1
            msg = {
                "id": self._ws_msg_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": _FIND_BOLD_BUTTON_JS,
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
            self.log_cb(f"⚠️ WebDriver(cdp): ошибка опроса ({type(e).__name__}: {e})")
            return None

    # ── Завершение ───────────────────────────────────────────────────────
    def close(self):
        """Освобождает ресурсы. Безопасно вызывать даже без успешного
        connect() (например, если запуск Р7 в этом прогоне вообще не
        добавлял debug-флаги). Не поднимает исключений — вызывается из
        finally на стороне r7_Testovarka.py.
        """
        if self._driver is not None:
            try:
                self._driver.quit()
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
