"""Тесты для r7_webdriver_connector.py — CDP/Selenium-коннектор к Р7-Офис.

Все сетевые вызовы (requests.get, websocket.create_connection) и сам
websocket мокаются: тесты проверяют логику коннектора, а не реальное
подключение к CDP-порту.
"""
import json
import socket

import pytest
from unittest.mock import Mock, patch

import r7_webdriver_connector as wdmod


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
