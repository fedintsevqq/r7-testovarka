"""Тесты CLI-скрипта run_crash_recovery.py (этап 3, M4 — обёртка над
run_crash_recovery_scenario).

Живой Р7 и реальные win32-окна не нужны: connector-объекты и win32gui
мокаются (та же техника monkeypatch.setattr("win32gui.X", ...), что и в
tests/test_close_and_dialogs.py для _close_update_dialog_if_exists —
run_crash_recovery.py сознательно использует тот же приём для поиска
диалога восстановления).
"""
import itertools
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import run_crash_recovery as cli


# ── _resolve_file_path ────────────────────────────────────────────────────
# Регрессия, найдена живым прогоном (25.08.2026): при вызове из Git Bash с
# кириллическим путём в --file MSYS2 передаёт argv в форме Юникода,
# отличной от той, в которой имя реально лежит на NTFS — path.exists()
# даёт False на существующем файле, хотя строки визуально совпадают.

def test_resolve_file_path_returns_as_is_when_already_correct(tmp_path):
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    assert cli._resolve_file_path(str(f)) == f


def test_resolve_file_path_fixes_nfd_vs_nfc_mismatch(tmp_path):
    import unicodedata
    name_nfc = unicodedata.normalize("NFC", "файл-Р7.xlsx")
    name_nfd = unicodedata.normalize("NFD", "файл-Р7.xlsx")
    assert name_nfc != name_nfd  # предпосылка теста: формы реально разные

    real_file = tmp_path / name_nfc
    real_file.write_text("x")

    # argv "испорчен" в NFD, реальный файл создан в NFC — как в живом баге.
    resolved = cli._resolve_file_path(str(tmp_path / name_nfd))

    assert resolved.exists()
    assert resolved.read_text() == "x"


def test_resolve_file_path_returns_original_when_nothing_matches(tmp_path):
    missing = tmp_path / "нет-такого.xlsx"
    resolved = cli._resolve_file_path(str(missing))
    assert resolved == missing  # вызывающий код сам покажет "файл не найден"


# ── _build_edits ──────────────────────────────────────────────────────────

def test_build_edits_word_uses_insert_text_and_bold():
    edits = cli._build_edits(Path("a.docx"), 3)
    conn = Mock()
    for edit in edits:
        edit(conn)
    assert conn.insert_text.call_count == 3
    assert conn.set_bold.call_count == 1
    assert conn.add_sheet.call_count == 0
    assert conn.add_slide.call_count == 0


def test_build_edits_word_texts_are_distinct():
    edits = cli._build_edits(Path("a.docx"), 3)
    conn = Mock()
    for edit in edits[:-1]:  # последняя правка — set_bold, без аргумента текста
        edit(conn)
    texts = [call.args[0] for call in conn.insert_text.call_args_list]
    assert len(set(texts)) == 3  # не одна и та же строка трижды


def test_build_edits_slide_uses_add_slide():
    edits = cli._build_edits(Path("a.pptx"), 4)
    conn = Mock()
    for edit in edits:
        edit(conn)
    assert conn.add_slide.call_count == 4
    assert conn.insert_text.call_count == 0


def test_build_edits_xlsx_uses_add_sheet():
    edits = cli._build_edits(Path("a.xlsx"), 5)
    conn = Mock()
    for edit in edits:
        edit(conn)
    assert conn.add_sheet.call_count == 5
    assert conn.insert_text.call_count == 0
    assert conn.add_slide.call_count == 0


def test_build_edits_unknown_extension_falls_back_to_cell():
    edits = cli._build_edits(Path("a.unknown"), 2)
    conn = Mock()
    for edit in edits:
        edit(conn)
    assert conn.add_sheet.call_count == 2


def test_build_edits_zero_ops_gives_empty_list_for_xlsx():
    assert cli._build_edits(Path("a.xlsx"), 0) == []


def test_build_edits_zero_ops_word_still_has_bold_call():
    """Для Word bold добавляется один раз безусловно (форматирует то, что
    уже вставлено) — при ops=0 список правок не пуст, просто без insert_text."""
    edits = cli._build_edits(Path("a.docx"), 0)
    conn = Mock()
    for edit in edits:
        edit(conn)
    assert conn.insert_text.call_count == 0
    assert conn.set_bold.call_count == 1


# ── _build_verify_recovered ──────────────────────────────────────────────

def test_verify_word_returns_expected_ops_when_history_advanced():
    verify = cli._build_verify_recovered(Path("a.docx"), 5)
    conn = Mock()
    conn.word_state.return_value = {"historyPoints": 3}
    assert verify(conn) == 5


def test_verify_word_returns_zero_without_history():
    verify = cli._build_verify_recovered(Path("a.docx"), 5)
    conn = Mock()
    conn.word_state.return_value = {"historyPoints": 0}
    assert verify(conn) == 0


def test_verify_word_returns_zero_when_state_is_none():
    verify = cli._build_verify_recovered(Path("a.docx"), 5)
    conn = Mock()
    conn.word_state.return_value = None
    assert verify(conn) == 0


def test_verify_slide_counts_added_slides():
    verify = cli._build_verify_recovered(Path("a.pptx"), 5)
    conn = Mock()
    conn.slide_state.return_value = {"slideCount": 4}  # было 1, стало 4 -> +3
    assert verify(conn) == 3


def test_verify_slide_clamped_to_expected_ops():
    """Число слайдов не может дать больше "восстановленных", чем реально
    запрашивалось правок — иначе дрейф slideCount выглядел бы как
    восстановление, которого не было."""
    verify = cli._build_verify_recovered(Path("a.pptx"), 2)
    conn = Mock()
    conn.slide_state.return_value = {"slideCount": 10}
    assert verify(conn) == 2


def test_verify_slide_never_negative():
    verify = cli._build_verify_recovered(Path("a.pptx"), 5)
    conn = Mock()
    conn.slide_state.return_value = {"slideCount": 0}
    assert verify(conn) == 0


def test_verify_xlsx_counts_added_sheets():
    verify = cli._build_verify_recovered(Path("a.xlsx"), 5)
    conn = Mock()
    conn.document_state.return_value = {"sheets": 3}  # было 1 -> +2
    assert verify(conn) == 2


def test_verify_xlsx_returns_zero_when_state_is_none():
    verify = cli._build_verify_recovered(Path("a.xlsx"), 5)
    conn = Mock()
    conn.document_state.return_value = None
    assert verify(conn) == 0


# ── verdict_ok ────────────────────────────────────────────────────────────

def _full_result(**overrides):
    base = {
        "connected_before_crash": True,
        "process_died_cleanly": True,
        "connected_after_crash": True,
        "recovered_count": 3,
    }
    base.update(overrides)
    return base


def test_verdict_ok_true_on_full_success():
    assert cli.verdict_ok(_full_result()) is True


def test_verdict_ok_false_when_process_did_not_die_cleanly():
    assert cli.verdict_ok(_full_result(process_died_cleanly=False)) is False


def test_verdict_ok_false_when_never_connected_before_crash():
    assert cli.verdict_ok(_full_result(connected_before_crash=False)) is False


def test_verdict_ok_false_when_never_reconnected():
    assert cli.verdict_ok(_full_result(connected_after_crash=False)) is False


def test_verdict_ok_false_when_recovered_count_is_none():
    assert cli.verdict_ok(_full_result(recovered_count=None)) is False


def test_verdict_ok_false_when_recovered_count_is_zero():
    assert cli.verdict_ok(_full_result(recovered_count=0)) is False


# ── build_report ──────────────────────────────────────────────────────────

def test_build_report_marks_success_verdict():
    report = cli.build_report(Path("a.xlsx"), 5, 30.0, _full_result(),
                              {"dialog_seen": False}, 12.3, ["строка лога"])
    assert report["verdict"] == "Успешно"
    assert report["file"] == "a.xlsx"
    assert report["ops_requested"] == 5
    assert report["total_elapsed_sec"] == 12.3
    assert report["log"] == ["строка лога"]


def test_build_report_marks_failure_verdict():
    report = cli.build_report(Path("a.xlsx"), 5, 30.0,
                              _full_result(recovered_count=0),
                              {"dialog_seen": False}, 1.0, [])
    assert report["verdict"] == "Ошибка"


def test_build_report_excludes_proc_handle():
    """result["proc"] — объект Popen, не JSON-сериализуемый — не должен
    попасть в отчёт как есть."""
    result = _full_result()
    result["proc"] = object()
    report = cli.build_report(Path("a.xlsx"), 5, 30.0, result, {}, 1.0, [])
    assert "proc" not in report["scenario"]
    json.dumps(report, ensure_ascii=False)  # не должно упасть


def test_build_report_is_json_serializable_with_realistic_data():
    result = _full_result()
    result["proc"] = None
    result["time_to_reconnect_sec"] = 0.5
    dialog = {"dialog_seen": True, "dialog_title": "Восстановление документов",
             "clicked": True, "button_text": "Восстановить", "elapsed_sec": 2.1}
    report = cli.build_report(Path("a.docx"), 3, 30.0, result, dialog, 20.5,
                              ["строка 1", "строка 2"])
    dumped = json.dumps(report, ensure_ascii=False)
    assert "Восстановить" in dumped


# ── диалог восстановления ─────────────────────────────────────────────
# Реальный текст и кнопки — RECOVERY_DIALOG_TITLES/RECOVERY_BUTTON_PRIORITY
# — подтверждены живым прогоном 25.08.2026 (см. докстринг модуля). Три
# уровня тестов: _cdp_click_on_any_target (мокает requests/websocket),
# _find_and_handle_recovery_dialog_win32 (мокает win32gui — запасной
# путь, для этой сборки диалог отдельного окна не имеет), и
# _find_and_handle_recovery_dialog — оркестратор (мокает оба уровня, не
# трогает ни сеть, ни win32gui напрямую).

@pytest.fixture
def bare_app():
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app._cached_r7_path = None
    app._r7_pids = None
    return app


@pytest.fixture
def log():
    messages = []
    return (lambda m: messages.append(m)), messages


# ── _cdp_click_on_any_target ────────────────────────────────────────────

class _FakeWs:
    def __init__(self, response_value):
        self._response_value = response_value
        self.sent = []
        self.closed = False

    def send(self, data):
        self.sent.append(data)

    def recv(self):
        return json.dumps({"id": 1, "result": {"result": {"value": self._response_value}}})

    def close(self):
        self.closed = True


def test_cdp_click_returns_none_when_no_targets_appear(monkeypatch, log):
    log_cb, messages = log
    fake_requests = Mock()
    fake_requests.get.return_value = Mock(json=Mock(return_value=[]))
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "websocket", Mock())
    monkeypatch.setattr(cli.time, "sleep", Mock())
    times = itertools.chain([100.0, 100.0], itertools.repeat(200.0))
    monkeypatch.setattr(cli.time, "time", lambda: next(times))

    result = cli._cdp_click_on_any_target(8080, ["продолжить редактирование"], log_cb, timeout=5)

    assert result is None


def test_cdp_click_connects_to_first_target_and_clicks(monkeypatch, log):
    log_cb, messages = log
    target = {"type": "page", "webSocketDebuggerUrl": "ws://127.0.0.1:8080/devtools/page/1",
             "title": "Hello Documents"}
    fake_requests = Mock()
    fake_requests.get.return_value = Mock(json=Mock(return_value=[target]))
    fake_ws_value = {"clicked": True, "text": "Продолжить редактирование",
                     "matched": "продолжить редактирование", "candidates": 1}
    fake_ws_module = Mock()
    fake_ws_module.create_connection = Mock(return_value=_FakeWs(fake_ws_value))
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "websocket", fake_ws_module)

    result = cli._cdp_click_on_any_target(8080, ["продолжить редактирование"], log_cb, timeout=5)

    assert result == fake_ws_value
    fake_ws_module.create_connection.assert_called_once_with(
        "ws://127.0.0.1:8080/devtools/page/1", timeout=3.0)


def test_cdp_click_retries_when_click_reports_not_clicked(monkeypatch, log):
    """Цель есть, но клик по тексту ничего не нашёл (clicked: False) —
    не должно считаться успехом, цикл продолжает опрос до таймаута."""
    log_cb, messages = log
    target = {"type": "page", "webSocketDebuggerUrl": "ws://x", "title": "Hello Documents"}
    fake_requests = Mock()
    fake_requests.get.return_value = Mock(json=Mock(return_value=[target]))
    fake_ws_module = Mock()
    fake_ws_module.create_connection = Mock(
        return_value=_FakeWs({"clicked": False, "candidates": 0}))
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "websocket", fake_ws_module)
    monkeypatch.setattr(cli.time, "sleep", Mock())
    times = itertools.chain([100.0, 100.0, 100.0], itertools.repeat(200.0))
    monkeypatch.setattr(cli.time, "time", lambda: next(times))

    result = cli._cdp_click_on_any_target(8080, ["продолжить редактирование"], log_cb, timeout=5)

    assert result is None


def test_cdp_click_survives_websocket_exception_and_keeps_polling(monkeypatch, log):
    log_cb, messages = log
    target = {"type": "page", "webSocketDebuggerUrl": "ws://x", "title": "Hello Documents"}
    fake_requests = Mock()
    fake_requests.get.return_value = Mock(json=Mock(return_value=[target]))
    fake_ws_module = Mock()
    fake_ws_module.create_connection = Mock(side_effect=RuntimeError("connection refused"))
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "websocket", fake_ws_module)
    monkeypatch.setattr(cli.time, "sleep", Mock())
    times = itertools.chain([100.0, 100.0], itertools.repeat(200.0))
    monkeypatch.setattr(cli.time, "time", lambda: next(times))

    result = cli._cdp_click_on_any_target(8080, ["продолжить редактирование"], log_cb, timeout=5)

    assert result is None
    assert any("не удался" in m for m in messages)


def test_cdp_click_returns_none_when_dependencies_missing(monkeypatch, log):
    log_cb, messages = log
    monkeypatch.setitem(sys.modules, "requests", None)

    result = cli._cdp_click_on_any_target(8080, ["продолжить редактирование"], log_cb, timeout=5)

    assert result is None
    assert any("недоступны" in m for m in messages)


def test_cdp_click_ignores_non_page_targets(monkeypatch, log):
    log_cb, messages = log
    non_page = {"type": "background_page", "webSocketDebuggerUrl": "ws://x"}
    fake_requests = Mock()
    fake_requests.get.return_value = Mock(json=Mock(return_value=[non_page]))
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "websocket", Mock())
    monkeypatch.setattr(cli.time, "sleep", Mock())
    times = itertools.chain([100.0, 100.0], itertools.repeat(200.0))
    monkeypatch.setattr(cli.time, "time", lambda: next(times))

    result = cli._cdp_click_on_any_target(8080, ["продолжить редактирование"], log_cb, timeout=5)

    assert result is None


# ── _find_and_handle_recovery_dialog_win32 (запасной путь) ──────────────

def test_win32_returns_not_seen_when_no_window_matches(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    bare_app._get_r7_processes = Mock(return_value=[])
    monkeypatch.setattr("win32gui.EnumWindows", Mock(side_effect=lambda cb, extra: None))
    monkeypatch.setattr(cli.time, "sleep", Mock())
    times = itertools.chain([100.0, 100.0], itertools.repeat(200.0))
    monkeypatch.setattr(cli.time, "time", lambda: next(times))

    result = cli._find_and_handle_recovery_dialog_win32(bare_app, log_cb, timeout=5)

    assert result["dialog_seen"] is False
    assert result["clicked"] is False


def test_win32_skips_windows_owned_by_foreign_process(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    fake_r7_process = Mock()
    fake_r7_process.pid = 555
    fake_r7_process.name.return_value = "editors.exe"
    bare_app._get_r7_processes = Mock(return_value=[fake_r7_process])

    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 999))  # чужой PID
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="Обнаружен файл блокировки"))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: cb(777, extra)))
    monkeypatch.setattr(cli.time, "sleep", Mock())
    times = itertools.chain([100.0, 100.0], itertools.repeat(200.0))
    monkeypatch.setattr(cli.time, "time", lambda: next(times))

    result = cli._find_and_handle_recovery_dialog_win32(bare_app, log_cb, timeout=5)

    assert result["dialog_seen"] is False  # заголовок совпал, но PID чужой


def test_win32_finds_and_clicks_button(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    fake_r7_process = Mock()
    fake_r7_process.pid = 555
    fake_r7_process.name.return_value = "editors.exe"
    bare_app._get_r7_processes = Mock(return_value=[fake_r7_process])
    bare_app._click_priority_button = Mock(return_value=(True, "Продолжить редактирование"))

    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 555))
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="Обнаружен файл блокировки"))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: cb(777, extra)))

    result = cli._find_and_handle_recovery_dialog_win32(bare_app, log_cb, timeout=5)

    assert result["dialog_seen"] is True
    assert result["clicked"] is True
    assert result["button_text"] == "Продолжить редактирование"
    bare_app._click_priority_button.assert_called_once()
    called_hwnd, called_keywords = bare_app._click_priority_button.call_args[0][:2]
    assert called_hwnd == 777
    assert "продолжить редактирование" in called_keywords


def test_win32_reports_seen_but_not_clicked_when_no_button_matches(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    fake_r7_process = Mock()
    fake_r7_process.pid = 555
    fake_r7_process.name.return_value = "editors.exe"
    bare_app._get_r7_processes = Mock(return_value=[fake_r7_process])
    bare_app._click_priority_button = Mock(return_value=(False, None))

    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 555))
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="Document Recovery"))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: cb(888, extra)))

    result = cli._find_and_handle_recovery_dialog_win32(bare_app, log_cb, timeout=5)

    assert result["dialog_seen"] is True
    assert result["clicked"] is False


def test_win32_returns_empty_result_when_win32_unavailable(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(r7mod, "WIN32_OK", False)

    result = cli._find_and_handle_recovery_dialog_win32(bare_app, log_cb, timeout=5)

    assert result["dialog_seen"] is False


# ── _find_and_handle_recovery_dialog (оркестратор) ───────────────────────

def test_orchestrator_returns_cdp_result_without_touching_win32(bare_app, log, monkeypatch):
    log_cb, messages = log
    cdp_value = {"clicked": True, "text": "Продолжить редактирование"}
    monkeypatch.setattr(cli, "_cdp_click_on_any_target", Mock(return_value=cdp_value))
    win32_mock = Mock()
    monkeypatch.setattr(cli, "_find_and_handle_recovery_dialog_win32", win32_mock)

    result = cli._find_and_handle_recovery_dialog(bare_app, log_cb, timeout=10)

    assert result["dialog_seen"] is True
    assert result["clicked"] is True
    assert result["button_text"] == "Продолжить редактирование"
    assert result["method"] == "cdp"
    win32_mock.assert_not_called()


def test_orchestrator_falls_back_to_win32_when_cdp_finds_nothing(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(cli, "_cdp_click_on_any_target", Mock(return_value=None))
    win32_result = {"dialog_seen": True, "dialog_title": "Обнаружен файл блокировки",
                    "clicked": True, "button_text": "Продолжить редактирование",
                    "elapsed_sec": 0.1}
    monkeypatch.setattr(cli, "_find_and_handle_recovery_dialog_win32",
                        Mock(return_value=win32_result))

    result = cli._find_and_handle_recovery_dialog(bare_app, log_cb, timeout=10)

    assert result["dialog_seen"] is True
    assert result["method"] == "win32"


def test_orchestrator_reports_not_seen_when_neither_path_finds_anything(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(cli, "_cdp_click_on_any_target", Mock(return_value=None))
    monkeypatch.setattr(cli, "_find_and_handle_recovery_dialog_win32", Mock(return_value={
        "dialog_seen": False, "dialog_title": None, "clicked": False,
        "button_text": None, "elapsed_sec": 0.1,
    }))

    result = cli._find_and_handle_recovery_dialog(bare_app, log_cb, timeout=10)

    assert result["dialog_seen"] is False
    assert result["method"] is None
    assert any("не появился" in m for m in messages)


def test_orchestrator_uses_default_cdp_port_when_unset(bare_app, log, monkeypatch):
    log_cb, messages = log
    cdp_mock = Mock(return_value=None)
    monkeypatch.setattr(cli, "_cdp_click_on_any_target", cdp_mock)
    monkeypatch.setattr(cli, "_find_and_handle_recovery_dialog_win32", Mock(return_value={
        "dialog_seen": False, "dialog_title": None, "clicked": False,
        "button_text": None, "elapsed_sec": 0.1,
    }))

    cli._find_and_handle_recovery_dialog(bare_app, log_cb, timeout=10)

    called_port = cdp_mock.call_args[0][0]
    assert called_port == r7mod.DEFAULT_CDP_PORT


# ── main(): exit codes и обработка ошибок без живого Р7 ───────────────────

def test_main_returns_1_when_file_missing(tmp_path, capsys):
    missing = tmp_path / "no_such_file.xlsx"
    rc = cli.main(["--file", str(missing)])
    assert rc == 1
    assert "не найден" in capsys.readouterr().out


def test_main_returns_1_when_webdriver_not_ok(tmp_path, monkeypatch, capsys):
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", False)

    rc = cli.main(["--file", str(f)])

    assert rc == 1
    assert "WEBDRIVER_OK" in capsys.readouterr().out


def test_main_returns_1_when_r7_not_found(tmp_path, monkeypatch, capsys):
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", True)
    monkeypatch.setattr(cli, "_make_bare_app", lambda: Mock(_find_r7_path=Mock(return_value=None)))

    rc = cli.main(["--file", str(f)])

    assert rc == 1
    assert "не найден" in capsys.readouterr().out


def test_main_returns_1_when_scenario_raises(tmp_path, monkeypatch, capsys):
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", True)
    fake_app = Mock()
    fake_app._find_r7_path.return_value = "r7.exe"
    monkeypatch.setattr(cli, "_make_bare_app", lambda: fake_app)
    monkeypatch.setattr(r7mod, "run_crash_recovery_scenario",
                        Mock(side_effect=RuntimeError("недоступен CDP-порт")))

    rc = cli.main(["--file", str(f)])

    assert rc == 1
    assert "исключением" in capsys.readouterr().out


def test_main_writes_report_and_returns_0_on_success(tmp_path, monkeypatch, capsys):
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", True)
    fake_app = Mock()
    fake_app._find_r7_path.return_value = "r7.exe"
    monkeypatch.setattr(cli, "_make_bare_app", lambda: fake_app)

    fake_proc = Mock()
    scenario_result = {
        "connected_before_crash": True, "process_died_cleanly": True,
        "connected_after_crash": True, "recovered_count": 5,
        "time_to_reconnect_sec": 0.3, "proc": fake_proc,
    }
    monkeypatch.setattr(r7mod, "run_crash_recovery_scenario",
                        Mock(return_value=scenario_result))

    rc = cli.main(["--file", str(f), "--ops", "5"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Успешно" in out
    reports = list((tmp_path / "Reports").glob("crash_recovery_*.json"))
    assert len(reports) == 1
    saved = json.loads(reports[0].read_text(encoding="utf-8"))
    assert saved["verdict"] == "Успешно"
    fake_proc.terminate.assert_called_once()


def test_main_returns_1_on_failed_recovery(tmp_path, monkeypatch, capsys):
    f = tmp_path / "a.xlsx"
    f.write_text("x")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", True)
    fake_app = Mock()
    fake_app._find_r7_path.return_value = "r7.exe"
    monkeypatch.setattr(cli, "_make_bare_app", lambda: fake_app)

    scenario_result = {
        "connected_before_crash": True, "process_died_cleanly": False,
        "connected_after_crash": True, "recovered_count": None,
        "time_to_reconnect_sec": 0.3, "proc": None,
    }
    monkeypatch.setattr(r7mod, "run_crash_recovery_scenario",
                        Mock(return_value=scenario_result))

    rc = cli.main(["--file", str(f)])

    assert rc == 1
    assert "Ошибка" in capsys.readouterr().out
