"""Тесты для r7_webdriver_connector.py — CDP/Selenium-коннектор к Р7-Офис.

Все сетевые вызовы (requests.get, websocket.create_connection) и сам
websocket мокаются: тесты проверяют логику коннектора, а не реальное
подключение к CDP-порту.
"""
import json
import socket
import sys
import types

from unittest.mock import Mock, patch

import r7_webdriver_connector as wdmod


class _FakeSeleniumDriver:
    """Минимальный дублёр selenium.webdriver.Chrome для тестов
    _try_connect_selenium/_switch_to_target — без реального selenium."""

    def __init__(self, handles_and_urls):
        self._urls = dict(handles_and_urls)
        self._current = None
        self.window_handles = list(self._urls.keys())
        self.switch_to = self
        self.stop_client = Mock()

    def window(self, handle):
        if handle not in self._urls:
            raise RuntimeError(f"no such window: {handle}")
        self._current = handle

    @property
    def current_url(self):
        return self._urls[self._current] if self._current is not None else None

    def execute_script(self, js):
        return 1


def _install_fake_selenium(monkeypatch, chrome_factory):
    """Регистрирует поддельный пакет selenium.webdriver(.chrome.options) в
    sys.modules, чтобы `from selenium import webdriver` и
    `from selenium.webdriver.chrome.options import Options` внутри
    _try_connect_selenium сработали без установленного selenium."""
    selenium_mod = types.ModuleType("selenium")
    webdriver_mod = types.ModuleType("selenium.webdriver")
    webdriver_mod.Chrome = chrome_factory
    selenium_mod.webdriver = webdriver_mod
    chrome_pkg = types.ModuleType("selenium.webdriver.chrome")
    options_mod = types.ModuleType("selenium.webdriver.chrome.options")

    class _FakeOptions:
        def __init__(self):
            self.experimental = {}

        def add_experimental_option(self, key, value):
            self.experimental[key] = value

    options_mod.Options = _FakeOptions
    chrome_pkg.options = options_mod

    monkeypatch.setitem(sys.modules, "selenium", selenium_mod)
    monkeypatch.setitem(sys.modules, "selenium.webdriver", webdriver_mod)
    monkeypatch.setitem(sys.modules, "selenium.webdriver.chrome", chrome_pkg)
    monkeypatch.setitem(sys.modules, "selenium.webdriver.chrome.options", options_mod)


# ── _is_ws_closed ────────────────────────────────────────────────────────

def test_is_ws_closed_true_for_websocket_connection_closed_exception():
    import websocket
    exc = websocket.WebSocketConnectionClosedException("gone")
    assert wdmod._is_ws_closed(exc) is True


def test_is_ws_closed_false_for_generic_exception():
    assert wdmod._is_ws_closed(ValueError("boom")) is False


def test_is_ws_closed_false_for_websocket_timeout():
    """Таймаут опроса — сокет жив, соединение НЕ считается оборванным.

    Регрессия: если таймаут ошибочно считать обрывом, медленный ответ CEF
    (документ ещё грузится) навсегда хоронит CDP на весь запуск Р7, и
    закрытие модалки «Сохранить изменения?» на выходе перестаёт работать.
    """
    import websocket
    exc = websocket.WebSocketTimeoutException("timed out")
    assert wdmod._is_ws_closed(exc) is False


# ── r7_launch_debug_args / get_free_port ────────────────────────────────

def test_launch_debug_args_default_has_only_ascdesktop_flag():
    args = wdmod.r7_launch_debug_args()
    assert args == ["--ascdesktop-support-debug-info"]


def test_launch_debug_args_with_port_appends_remote_debugging_port():
    args = wdmod.r7_launch_debug_args(port=8123)
    assert args == [
        "--ascdesktop-support-debug-info",
        "--remote-debugging-port=8123",
    ]


def test_get_free_port_returns_bindable_port():
    port = wdmod.get_free_port()
    assert isinstance(port, int)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))  # не должно бросить — порт был свободен


# ── connect() ────────────────────────────────────────────────────────────

def test_connect_fails_fast_when_webdriver_not_ok(connector, monkeypatch):
    monkeypatch.setattr(wdmod, "WEBDRIVER_OK", False)
    assert connector.connect(timeout=1.0) is False


def test_connect_idempotent_when_already_connected(connector):
    connector._backend = "cdp"
    with patch.object(connector, "_pick_target") as pick:
        assert connector.connect(timeout=1.0) is True
        pick.assert_not_called()  # второй connect() не переподключается


def test_connect_uses_cdp_backend_when_selenium_unavailable(connector):
    target = {"webSocketDebuggerUrl": "ws://127.0.0.1:8080/devtools/page/1"}
    with patch.object(connector, "_pick_target", return_value=target), \
         patch.object(connector, "_try_connect_selenium", return_value=False), \
         patch.object(connector, "_try_connect_cdp", return_value=True):
        assert connector.connect(timeout=1.0) is True
        assert connector._backend == "cdp"


def test_connect_returns_false_when_target_never_appears(connector):
    with patch.object(connector, "_pick_target", return_value=None):
        assert connector.connect(timeout=0.2, poll_sec=0.05) is False
    assert connector._backend is None


# ── _pick_target ─────────────────────────────────────────────────────────

def test_pick_target_filters_out_splash_screen(connector):
    """/json отдаёт две цели одновременно — сплэш и редактор.

    Регрессия: наивный выбор «первая цель с type==page» подключился бы к
    сплэшу («Hello Documents»), у которого кнопки «Жирный» нет и не будет.
    """
    splash = {"type": "page", "url": "app://.../index.html?waitingloader=yes",
              "webSocketDebuggerUrl": "ws://splash"}
    editor = {"type": "page", "url": "app://.../edit.html?doctype=spreadsheet",
              "webSocketDebuggerUrl": "ws://editor"}
    fake_resp = Mock()
    fake_resp.json.return_value = [splash, editor]
    fake_resp.raise_for_status = Mock()
    with patch.object(wdmod.requests, "get", return_value=fake_resp):
        target = connector._pick_target()
    assert target is editor


def test_pick_target_returns_none_when_editor_not_loaded_yet(connector):
    splash = {"type": "page", "url": "app://.../index.html?waitingloader=yes",
              "webSocketDebuggerUrl": "ws://splash"}
    fake_resp = Mock()
    fake_resp.json.return_value = [splash]
    fake_resp.raise_for_status = Mock()
    with patch.object(wdmod.requests, "get", return_value=fake_resp):
        assert connector._pick_target() is None


def test_pick_target_returns_none_on_request_error(connector):
    with patch.object(wdmod.requests, "get", side_effect=OSError("refused")):
        assert connector._pick_target() is None


# ── _try_connect_selenium ────────────────────────────────────────────────

def test_try_connect_selenium_returns_false_when_selenium_not_installed(connector):
    with patch.dict("sys.modules", {"selenium": None}):
        assert connector._try_connect_selenium({}) is False


def test_try_connect_selenium_switches_to_tab_matching_target_url(connector, monkeypatch):
    """Регрессия: attach через debuggerAddress цепляется к "текущему" окну
    chromedriver произвольно — на порту одновременно живут сплэш и редактор
    (см. _pick_target). Без переключения на target["url"] Selenium-бэкенд мог
    молча опрашивать сплэш и никогда не находить кнопку «Жирный»."""
    editor_url = "app://.../edit.html?doctype=spreadsheet"
    splash_url = "app://.../index.html?waitingloader=yes"
    driver = _FakeSeleniumDriver({"h-splash": splash_url, "h-editor": editor_url})
    _install_fake_selenium(monkeypatch, Mock(return_value=driver))

    target = {"url": editor_url, "webSocketDebuggerUrl": "ws://editor"}
    result = connector._try_connect_selenium(target)

    assert result is True
    assert connector._driver is driver
    assert driver._current == "h-editor"


def test_try_connect_selenium_falls_back_to_doctype_when_url_drifted(connector, monkeypatch):
    """URL мог обновиться между _pick_target() и attach (документ ещё
    грузится) — второй проход по тому же критерию "doctype=" всё равно
    находит редактор, не полагаясь на точное совпадение строки."""
    stale_target_url = "app://.../edit.html?doctype=spreadsheet&loading=1"
    actual_editor_url = "app://.../edit.html?doctype=spreadsheet&loaded=1"
    driver = _FakeSeleniumDriver({
        "h-splash": "app://.../index.html?waitingloader=yes",
        "h-editor": actual_editor_url,
    })
    _install_fake_selenium(monkeypatch, Mock(return_value=driver))

    target = {"url": stale_target_url, "webSocketDebuggerUrl": "ws://editor"}
    result = connector._try_connect_selenium(target)

    assert result is True
    assert driver._current == "h-editor"


def test_try_connect_selenium_fails_and_detaches_when_no_editor_tab_found(connector, monkeypatch):
    driver = _FakeSeleniumDriver({"h-splash": "app://.../index.html?waitingloader=yes"})
    _install_fake_selenium(monkeypatch, Mock(return_value=driver))

    target = {"url": "app://.../edit.html?doctype=spreadsheet", "webSocketDebuggerUrl": "ws://editor"}
    result = connector._try_connect_selenium(target)

    assert result is False
    assert connector._driver is None
    driver.stop_client.assert_called_once()


def test_switch_to_target_returns_false_when_window_handles_unavailable(connector):
    class _Broken:
        @property
        def window_handles(self):
            raise RuntimeError("no session")

    assert connector._switch_to_target(_Broken(), {"url": "x"}) is False


# ── evaluate() dispatch ──────────────────────────────────────────────────

def test_evaluate_returns_none_when_not_connected(connector):
    assert connector.evaluate("1+1") is None


def test_bold_button_state_returns_none_when_not_connected(connector):
    assert connector.bold_button_state() is None


def test_dismiss_save_dialog_returns_none_when_not_connected(connector):
    assert connector.dismiss_save_dialog() is None


# ── _eval_selenium — регрессия на ASI ───────────────────────────────────

def test_eval_selenium_wraps_expression_to_avoid_asi_bug(connector):
    """execute_script(f"return {js}") ловил ASI: JS вставляет ';' сразу после
    'return', если следующий токен — перевод строки (все выражения модуля —
    многострочные IIFE в тройных кавычках). Итог — execute_script всегда
    возвращал undefined. Тест фиксирует правильную форму: return (<js>);
    """
    connector._backend = "selenium"
    connector._driver = Mock()
    connector._driver.execute_script.return_value = {"found": True}

    js = "\n(function () { return 42; })()\n"
    result = connector._eval_selenium(js)

    assert result == {"found": True}
    called_with = connector._driver.execute_script.call_args[0][0]
    assert called_with == f"return ({js.strip()});"
    assert not called_with.startswith("return\n")


def test_eval_selenium_returns_none_and_logs_on_exception(connector):
    connector._backend = "selenium"
    connector._driver = Mock()
    connector._driver.execute_script.side_effect = RuntimeError("boom")
    assert connector._eval_selenium("1") is None
    connector.log_cb.assert_called()


# ── _eval_cdp ────────────────────────────────────────────────────────────

def _ws_with_responses(*payloads):
    """Фейковый websocket.WebSocket: send() ничего не делает, recv() отдаёт
    payloads по очереди (уже сериализованные JSON-строки)."""
    ws = Mock()
    ws.send = Mock()
    ws.recv = Mock(side_effect=payloads)
    return ws


def test_eval_cdp_sends_runtime_evaluate_and_returns_value(connector):
    connector._backend = "cdp"
    connector._ws = _ws_with_responses(
        json.dumps({"id": 1, "result": {"result": {"value": {"found": True}}}}),
    )
    result = connector._eval_cdp("1+1")
    assert result == {"found": True}
    sent = json.loads(connector._ws.send.call_args[0][0])
    assert sent["method"] == "Runtime.evaluate"
    assert sent["params"]["expression"] == "1+1"


def test_eval_cdp_skips_messages_with_mismatched_id(connector):
    """CDP может прислать события раньше ответа на наш id — их нужно пропускать."""
    connector._backend = "cdp"
    connector._ws = _ws_with_responses(
        json.dumps({"method": "Runtime.consoleAPICalled", "params": {}}),
        json.dumps({"id": 1, "result": {"result": {"value": 7}}}),
    )
    assert connector._eval_cdp("7") == 7


def test_eval_cdp_returns_none_on_js_exception(connector):
    connector._backend = "cdp"
    connector._ws = _ws_with_responses(
        json.dumps({"id": 1, "result": {}, "exceptionDetails": {"text": "ReferenceError"}}),
    )
    assert connector._eval_cdp("undefinedVar") is None


def test_eval_cdp_preserves_connection_on_socket_timeout(connector):
    """Таймаут recv не должен рвать соединение (см. _is_ws_closed)."""
    connector._backend = "cdp"
    connector._ws = Mock()
    connector._ws.send = Mock()
    connector._ws.recv = Mock(side_effect=socket.timeout("timed out"))

    result = connector._eval_cdp("1")

    assert result is None
    assert connector.connected is True
    connector._ws.close.assert_not_called()


def test_eval_cdp_marks_disconnected_on_connection_error(connector):
    connector._backend = "cdp"
    ws = Mock()
    ws.send = Mock()
    ws.recv = Mock(side_effect=ConnectionAbortedError("gone"))
    connector._ws = ws

    result = connector._eval_cdp("1")

    assert result is None
    assert connector.connected is False
    ws.close.assert_called_once()
    assert connector._ws is None


def test_eval_cdp_marks_disconnected_on_websocket_closed_exception(connector):
    import websocket
    connector._backend = "cdp"
    connector._ws = Mock()
    connector._ws.send = Mock()
    connector._ws.recv = Mock(side_effect=websocket.WebSocketConnectionClosedException("gone"))

    assert connector._eval_cdp("1") is None
    assert connector.connected is False


def test_mark_disconnected_logs_only_once(connector):
    connector._backend = "cdp"
    connector._ws = Mock()
    connector._mark_disconnected("first")
    connector._mark_disconnected("second")
    disconnect_msgs = [c for c in connector.log_cb.call_args_list
                        if "потеряно" in c[0][0]]
    assert len(disconnect_msgs) == 1


def test_evaluate_returns_none_after_disconnect_without_new_ws_calls(connector):
    """После обрыва evaluate() должен сразу отдавать None, не трогая ws."""
    connector._backend = "cdp"
    connector._ws = Mock()
    connector._mark_disconnected("simulated")
    assert connector.evaluate("1+1") is None


# ── close() ──────────────────────────────────────────────────────────────

def test_close_calls_stop_client_not_quit(connector):
    """quit() на debuggerAddress-сессии закрыл бы сам Р7 как побочный эффект —
    close() должен отцепляться через stop_client(), не quit()."""
    driver = Mock()
    connector._driver = driver
    connector.close()
    driver.stop_client.assert_called_once()
    driver.quit.assert_not_called()


def test_close_is_safe_without_prior_connect(connector):
    connector.close()  # не должно бросать
    assert connector.connected is False


def test_close_swallows_exceptions_from_ws_close(connector):
    connector._ws = Mock()
    connector._ws.close.side_effect = RuntimeError("already closed")
    connector.close()  # не должно бросать
    assert connector._ws is None


# ── _pick_target: фильтр по filename_hint (H5) ───────────────────────────
# Реальные URL-цели /json ПРОВЕРЕНЫ НА ЖИВОМ Р7 (25.08.2026, два документа
# открыты в одном экземпляре): target["title"] у ОБОИХ — одна и та же
# строка "R7-OFFICE Documents", а настоящее имя файла лежит в query-
# параметре "title=" самого URL. Фикстуры ниже используют этот же вид URL.

def _doc_target(filename, ws="ws://x"):
    return {
        "type": "page",
        "title": "R7-OFFICE Documents",   # одинаково у всех целей — не различитель
        "url": ("file:///C:/Program%20Files/R7-Office/Editors/editors/web-apps/"
               f"apps/api/documents/index.html?placement=desktop&doctype=spreadsheet"
               f"&lang=ru-RU&username=Vladimir&location=RU&title={filename}&desktop=true"),
        "webSocketDebuggerUrl": ws,
    }


def test_pick_target_without_hint_returns_first_candidate(connector):
    """filename_hint=None (по умолчанию) — прежнее поведение: первая
    подходящая цель, без фильтрации. Ни один из существующих вызывающих
    мест (документ всегда один) не должен измениться."""
    assert connector.filename_hint is None
    a, b = _doc_target("a.xlsx", "ws://a"), _doc_target("b.xlsx", "ws://b")
    fake_resp = Mock()
    fake_resp.json.return_value = [a, b]
    fake_resp.raise_for_status = Mock()
    with patch.object(wdmod.requests, "get", return_value=fake_resp):
        assert connector._pick_target() is a


def test_pick_target_filters_by_filename_hint():
    """Нужный документ находится независимо от порядка целей в /json."""
    connector = wdmod.R7WebDriverConnector(port=8080, log_cb=Mock(),
                                           filename_hint="test_50k.xlsx")
    wanted = _doc_target("test_50k.xlsx", "ws://wanted")
    other = _doc_target("test_10k.xlsx", "ws://other")
    fake_resp = Mock()
    fake_resp.json.return_value = [other, wanted]   # нужный — не первый
    fake_resp.raise_for_status = Mock()
    with patch.object(wdmod.requests, "get", return_value=fake_resp):
        target = connector._pick_target()
    assert target is wanted
    assert connector._last_target_filename == "test_50k.xlsx"


def test_pick_target_hint_no_match_falls_back_with_warning():
    """Ни одна цель не совпала с подсказкой — не None (это заставило бы
    connect() решить, что редактор не загрузился, и поллить впустую до
    таймаута), а первая подходящая, с явным предупреждением в лог."""
    log = Mock()
    connector = wdmod.R7WebDriverConnector(port=8080, log_cb=log,
                                           filename_hint="missing.xlsx")
    present = _doc_target("test_10k.xlsx")
    fake_resp = Mock()
    fake_resp.json.return_value = [present]
    fake_resp.raise_for_status = Mock()
    with patch.object(wdmod.requests, "get", return_value=fake_resp):
        target = connector._pick_target()
    assert target is present
    assert any("missing.xlsx" in str(c) for c in log.call_args_list)
    assert connector._last_target_filename == "test_10k.xlsx"


def test_pick_target_hint_ignores_splash_screen():
    """Фильтр по имени работает поверх старого фильтра по doctype= — сплэш
    без doctype= в URL не должен пройти, даже если как-то получит query-
    параметр title=, совпадающий с подсказкой."""
    connector = wdmod.R7WebDriverConnector(port=8080, log_cb=Mock(),
                                           filename_hint="test_10k.xlsx")
    splash = {"type": "page", "webSocketDebuggerUrl": "ws://splash",
              "url": "app://.../index.html?waitingloader=yes&title=test_10k.xlsx"}
    editor = _doc_target("test_10k.xlsx", "ws://editor")
    fake_resp = Mock()
    fake_resp.json.return_value = [splash, editor]
    fake_resp.raise_for_status = Mock()
    with patch.object(wdmod.requests, "get", return_value=fake_resp):
        target = connector._pick_target()
    assert target is editor


# ── _target_filename ──────────────────────────────────────────────────────

def test_target_filename_extracts_query_param():
    target = _doc_target("test_50k.xlsx")
    assert wdmod.R7WebDriverConnector._target_filename(target) == "test_50k.xlsx"


def test_target_filename_none_without_title_param():
    target = {"url": "app://.../edit.html?doctype=spreadsheet"}
    assert wdmod.R7WebDriverConnector._target_filename(target) is None


def test_target_filename_none_on_empty_url():
    assert wdmod.R7WebDriverConnector._target_filename({}) is None


# ── connect(): лог совпадения цели ────────────────────────────────────────
#
# Тесты ниже НЕ мокают _pick_target целиком (в отличие от остальных тестов
# connect() в этом файле) — только requests.get. Мок _pick_target() обошёл бы
# именно тот код (_last_target_filename), который эти тесты проверяют:
# f-строка лога содержит self.filename_hint отдельно от matched, поэтому
# тест на замоканном _pick_target прошёл бы, даже если matched всегда "?"
# (regression поймана: filename_hint в тексте лога маскировал сломанный matched).

def test_connect_logs_matched_filename_from_pick_target_cache():
    """matched в логе берётся из self._last_target_filename, выставленного
    _pick_target() — а не из filename_hint (та же строка, но другая
    переменная — см. комментарий выше)."""
    log = Mock()
    connector = wdmod.R7WebDriverConnector(port=8080, log_cb=log,
                                           filename_hint="test_50k.xlsx")
    fake_resp = Mock()
    fake_resp.json.return_value = [_doc_target("test_50k.xlsx")]
    fake_resp.raise_for_status = Mock()
    with patch.object(wdmod.requests, "get", return_value=fake_resp), \
         patch.object(connector, "_try_connect_selenium", return_value=False), \
         patch.object(connector, "_try_connect_cdp", return_value=True):
        assert connector.connect(timeout=1.0) is True
    assert connector._last_target_filename == "test_50k.xlsx"
    assert any("🔍 CDP-цель: test_50k.xlsx" in str(c) for c in log.call_args_list)


def test_connect_logs_fallback_filename_distinct_from_hint():
    """Регрессионный случай: подсказка не совпала ни с одной целью, log
    показывает ИМЯ ФАКТИЧЕСКИ ВЫБРАННОЙ цели (matched), не filename_hint —
    только так и видно из лога, что подключились не туда, куда просили."""
    log = Mock()
    connector = wdmod.R7WebDriverConnector(port=8080, log_cb=log,
                                           filename_hint="missing.xlsx")
    fake_resp = Mock()
    fake_resp.json.return_value = [_doc_target("test_10k.xlsx")]
    fake_resp.raise_for_status = Mock()
    with patch.object(wdmod.requests, "get", return_value=fake_resp), \
         patch.object(connector, "_try_connect_selenium", return_value=False), \
         patch.object(connector, "_try_connect_cdp", return_value=True):
        assert connector.connect(timeout=1.0) is True
    assert connector._last_target_filename == "test_10k.xlsx"
    assert any("🔍 CDP-цель: test_10k.xlsx (по файлу missing.xlsx)" in str(c)
              for c in log.call_args_list)


def test_connect_silent_about_target_when_no_hint(connector):
    """Однодокументный сценарий (подавляющее большинство вызовов сегодня) —
    без подсказки лог не засоряется строкой про выбор цели."""
    target = _doc_target("любой.xlsx")
    with patch.object(connector, "_pick_target", return_value=target), \
         patch.object(connector, "_try_connect_selenium", return_value=False), \
         patch.object(connector, "_try_connect_cdp", return_value=True):
        assert connector.connect(timeout=1.0) is True
    assert not any("CDP-цель" in str(c) for c in connector.log_cb.call_args_list)
