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
from pathlib import Path
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import run_crash_recovery as cli


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


# ── _find_and_handle_recovery_dialog (win32gui мокается) ─────────────────

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


def test_find_dialog_returns_not_seen_when_no_window_matches(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    bare_app._get_r7_processes = Mock(return_value=[])
    monkeypatch.setattr("win32gui.EnumWindows", Mock(side_effect=lambda cb, extra: None))
    monkeypatch.setattr(cli.time, "sleep", Mock())
    # Таймаут условно "истекает" сразу — заставляем цикл выйти после первого прохода.
    times = itertools.chain([100.0, 100.0], itertools.repeat(200.0))
    monkeypatch.setattr(cli.time, "time", lambda: next(times))

    result = cli._find_and_handle_recovery_dialog(bare_app, log_cb, timeout=5)

    assert result["dialog_seen"] is False
    assert result["clicked"] is False
    assert any("не появился" in m for m in messages)


def test_find_dialog_skips_windows_owned_by_foreign_process(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    fake_r7_process = Mock()
    fake_r7_process.pid = 555
    fake_r7_process.name.return_value = "editors.exe"
    bare_app._get_r7_processes = Mock(return_value=[fake_r7_process])

    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 999))  # чужой PID
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="Восстановление документов"))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: cb(777, extra)))
    monkeypatch.setattr(cli.time, "sleep", Mock())
    times = itertools.chain([100.0, 100.0], itertools.repeat(200.0))
    monkeypatch.setattr(cli.time, "time", lambda: next(times))

    result = cli._find_and_handle_recovery_dialog(bare_app, log_cb, timeout=5)

    assert result["dialog_seen"] is False  # заголовок совпал, но PID чужой


def test_find_dialog_finds_and_clicks_button(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    fake_r7_process = Mock()
    fake_r7_process.pid = 555
    fake_r7_process.name.return_value = "editors.exe"
    bare_app._get_r7_processes = Mock(return_value=[fake_r7_process])
    bare_app._click_priority_button = Mock(return_value=(True, "Восстановить"))

    monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda h: (0, 555))
    monkeypatch.setattr("win32gui.IsWindowVisible", Mock(return_value=True))
    monkeypatch.setattr("win32gui.GetWindowText", Mock(return_value="Восстановление документов"))
    monkeypatch.setattr(
        "win32gui.EnumWindows",
        Mock(side_effect=lambda cb, extra: cb(777, extra)))

    result = cli._find_and_handle_recovery_dialog(bare_app, log_cb, timeout=5)

    assert result["dialog_seen"] is True
    assert result["clicked"] is True
    assert result["button_text"] == "Восстановить"
    bare_app._click_priority_button.assert_called_once()
    called_hwnd, called_keywords = bare_app._click_priority_button.call_args[0][:2]
    assert called_hwnd == 777
    assert "восстановить" in called_keywords


def test_find_dialog_reports_seen_but_not_clicked_when_no_button_matches(bare_app, log, monkeypatch):
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

    result = cli._find_and_handle_recovery_dialog(bare_app, log_cb, timeout=5)

    assert result["dialog_seen"] is True
    assert result["clicked"] is False
    assert any("кнопка не найдена" in m for m in messages)


def test_find_dialog_returns_empty_result_when_win32_unavailable(bare_app, log, monkeypatch):
    log_cb, messages = log
    monkeypatch.setattr(r7mod, "WIN32_OK", False)

    result = cli._find_and_handle_recovery_dialog(bare_app, log_cb, timeout=5)

    assert result["dialog_seen"] is False
    assert any("недоступен" in m for m in messages)


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
