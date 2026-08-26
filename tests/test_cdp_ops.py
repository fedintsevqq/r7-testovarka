"""Тесты перевода тест-операций на CDP: _cdp_step / _cdp_sequence, проверки
результата и отложенная верификация (_flush_pending_cdp_verify).

Живой Р7 не нужен: коннектор мокается, JS не исполняется. Проверяется ровно
то, что решает Python — какой статус получила операция и можно ли после неё
безопасно повторить действие клавишами.
"""
import json
from unittest.mock import Mock, patch

import pytest

import r7_Testovarka as r7mod
import r7_webdriver_connector as wdmod


# ── _col_letter ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("index,expected", [
    (1, "A"), (5, "E"), (11, "K"), (16, "P"), (26, "Z"), (27, "AA"), (52, "AZ"),
])
def test_col_letter(index, expected):
    assert r7mod._col_letter(index) == expected


def test_col_letter_clamps_below_one():
    """Смещение 0 не должно давать пустую ссылку — иначе asc_findCell('1')."""
    assert r7mod._col_letter(0) == "A"
    assert r7mod._col_letter(-3) == "A"


# ── вспомогательное ──────────────────────────────────────────────────────

def _connected(**attrs):
    c = Mock()
    c.connected = True
    for k, v in attrs.items():
        setattr(c, k, v)
    return c


def _payload(ok=True, mutated=False, **extra):
    p = {"ok": ok, "mutated": mutated, "method": "asc_test",
         "before": {"sheets": 3, "historyIndex": 1},
         "after": {"sheets": 3, "historyIndex": 1}}
    p.update(extra)
    return p


# ── _cdp_step: классификация ответа ──────────────────────────────────────

def test_step_unavailable_without_connector(bare_r7, log):
    bare_r7._webdriver_connector = None
    status, payload = bare_r7._cdp_step("op", lambda c, t: None, log)
    assert (status, payload) == ("unavailable", None)


def test_step_unavailable_when_ops_disabled(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod.R7Testovarka, "CDP_OPS_ENABLED", False)
    bare_r7._webdriver_connector = _connected()
    status, _ = bare_r7._cdp_step("op", lambda c, t: _payload(), log)
    assert status == "unavailable"


def test_step_ok(bare_r7, log):
    bare_r7._webdriver_connector = _connected()
    status, payload = bare_r7._cdp_step("op", lambda c, t: _payload(), log)
    assert status == "ok"
    assert payload["method"] == "asc_test"


def test_step_exception_is_failed(bare_r7, log):
    bare_r7._webdriver_connector = _connected()

    def boom(c, t):
        raise RuntimeError("ws died")

    status, _ = bare_r7._cdp_step("op", boom, log)
    assert status == "failed"
    assert any("ws died" in m for m in log.messages)


def test_step_none_with_live_connection_is_unknown(bare_r7, log):
    """Таймаут сокета при живом соединении — операция УШЛА в Р7.

    Повторять её клавишами нельзя: JS продолжает работать в рендере, и
    вставка применилась бы дважды.
    """
    bare_r7._webdriver_connector = _connected()
    status, _ = bare_r7._cdp_step("op", lambda c, t: None, log, timeout=5)
    assert status == "unknown"


def test_step_none_with_dead_connection_is_failed(bare_r7, log):
    c = Mock()
    c.connected = False
    bare_r7._webdriver_connector = c
    # соединение живо на момент входа, но обрывается внутри вызова
    bare_r7._webdriver_connector = _connected()

    def drop(_c, _t):
        _c.connected = False
        return None

    status, _ = bare_r7._cdp_step("op", drop, log)
    assert status == "failed"


def test_step_not_ok_without_mutation_is_failed(bare_r7, log):
    bare_r7._webdriver_connector = _connected()
    status, _ = bare_r7._cdp_step(
        "op", lambda c, t: _payload(ok=False, reason="no-method:asc_X"), log)
    assert status == "failed"
    assert any("no-method:asc_X" in m for m in log.messages)


def test_step_not_ok_after_mutation_is_unknown(bare_r7, log):
    """Сбой ПОСЛЕ изменения документа запрещает откат на клавиши."""
    bare_r7._webdriver_connector = _connected()
    status, _ = bare_r7._cdp_step(
        "op", lambda c, t: _payload(ok=False, mutated=True, reason="exception",
                                    error="boom"), log)
    assert status == "unknown"
    assert any("дважды" in m for m in log.messages)


def test_step_unexpected_answer_is_failed(bare_r7, log):
    bare_r7._webdriver_connector = _connected()
    status, _ = bare_r7._cdp_step("op", lambda c, t: "нежданчик", log)
    assert status == "failed"


# ── _cdp_sequence ────────────────────────────────────────────────────────

def _steps(*results):
    """Шаги, отдающие заданные результаты по порядку."""
    out = []
    for i, res in enumerate(results):
        out.append((f"step{i}", (lambda r: (lambda c, t: r))(res), 1.0, 0))
    return out


def test_sequence_all_ok_returns_true(bare_r7, log):
    bare_r7._webdriver_connector = _connected()
    ok = bare_r7._cdp_sequence("op", _steps(_payload(), _payload(mutated=True)),
                               checker=None, log_cb=log)
    assert ok is True


def test_sequence_first_step_failed_falls_back(bare_r7, log):
    """Ни один шаг не тронул документ → откат на pyautogui разрешён."""
    bare_r7._webdriver_connector = _connected()
    ok = bare_r7._cdp_sequence(
        "op", _steps(_payload(ok=False, reason="no-method:asc_X")),
        checker=None, log_cb=log)
    assert ok is False


def test_sequence_failure_after_mutation_blocks_fallback(bare_r7, log):
    """Первый шаг уже изменил документ — повторять операцию клавишами нельзя."""
    bare_r7._webdriver_connector = _connected()
    ok = bare_r7._cdp_sequence(
        "op", _steps(_payload(mutated=True), _payload(ok=False, reason="nope")),
        checker=None, log_cb=log)
    assert ok is True
    assert any("откат на клавиши отменён" in m for m in log.messages)


def test_sequence_unknown_stops_and_blocks_fallback(bare_r7, log):
    bare_r7._webdriver_connector = _connected()
    ok = bare_r7._cdp_sequence("op", _steps(None), checker=None, log_cb=log)
    assert ok is True
    assert any("цепочка прервана" in m for m in log.messages)


def test_sequence_without_connector_returns_false(bare_r7, log):
    bare_r7._webdriver_connector = None
    assert bare_r7._cdp_sequence("op", _steps(_payload()), log_cb=log) is False


def test_sequence_pace_is_subtracted(bare_r7, log, monkeypatch):
    """Пауза между шагами копится в _paced_total, как и в клавиатурной версии."""
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    bare_r7._webdriver_connector = _connected()
    bare_r7._paced_total = 0.0
    steps = [("a", lambda c, t: _payload(), 1.0, 0),
             ("b", lambda c, t: _payload(), 1.0, 0.08)]
    bare_r7._cdp_sequence("op", steps, checker=None, log_cb=log)
    assert bare_r7._paced_total >= 0.0   # _pace вызван; со снятым sleep ≈0


# ── проверки результата ──────────────────────────────────────────────────

def test_check_whole_sheet_selected():
    ok, detail = r7mod.R7Testovarka._cdp_check_whole_sheet_selected(
        {"selection": "A1"}, {"selection": "A1:XFD1048576"})
    assert ok and "A1:XFD1048576" in detail


def test_check_whole_sheet_rejects_small_range():
    ok, _ = r7mod.R7Testovarka._cdp_check_whole_sheet_selected(
        {"selection": "A1"}, {"selection": "A1:E1"})
    assert ok is False


def test_check_whole_sheet_unreadable_selection():
    ok, detail = r7mod.R7Testovarka._cdp_check_whole_sheet_selected({}, {})
    assert ok is False and "прочитать не удалось" in detail


def test_check_sheet_added():
    ok, _ = r7mod.R7Testovarka._cdp_check_sheet_added({"sheets": 3}, {"sheets": 4})
    assert ok is True


def test_check_sheet_added_rejects_unchanged():
    ok, _ = r7mod.R7Testovarka._cdp_check_sheet_added({"sheets": 3}, {"sheets": 3})
    assert ok is False


def test_check_document_changed_by_history_index():
    ok, detail = r7mod.R7Testovarka._cdp_check_document_changed(
        {"historyIndex": 4}, {"historyIndex": 5})
    assert ok is True and "4 → 5" in detail


def test_check_document_changed_by_can_undo():
    ok, _ = r7mod.R7Testovarka._cdp_check_document_changed(
        {"canUndo": False}, {"canUndo": True})
    assert ok is True


def test_check_document_changed_reports_unreadable_history():
    ok, detail = r7mod.R7Testovarka._cdp_check_document_changed({}, {})
    assert ok is False and "прочитать не удалось" in detail


def test_check_document_not_changed():
    ok, detail = r7mod.R7Testovarka._cdp_check_document_changed(
        {"historyIndex": 4, "canUndo": True}, {"historyIndex": 4, "canUndo": True})
    assert ok is False and "не сдвинулась" in detail


def test_check_selection_is_factory():
    check = r7mod.R7Testovarka._cdp_check_selection_is("B1")
    assert check({}, {"selection": "B1"})[0] is True
    assert check({}, {"selection": "C1"})[0] is False


# ── inline-проверка и отложенная перепроверка ────────────────────────────

def test_verify_inline_success_does_not_defer(bare_r7, log):
    payload = _payload(after={"sheets": 4}, before={"sheets": 3})
    bare_r7._cdp_verify_or_defer("op", payload,
                                 r7mod.R7Testovarka._cdp_check_sheet_added, log)
    assert bare_r7._pending_cdp_verify is None
    assert any("проверено" in m for m in log.messages)


def test_verify_defers_when_not_confirmed_inline(bare_r7, log):
    """Асинхронная операция (вставка) к возврату asc_Paste ещё не видна —
    проверка откладывается до закрытия замера, а не считается провалом."""
    payload = _payload(before={"historyIndex": 4}, after={"historyIndex": 4})
    bare_r7._cdp_verify_or_defer("Вставка", payload,
                                 r7mod.R7Testovarka._cdp_check_document_changed, log)
    assert bare_r7._pending_cdp_verify is not None
    assert bare_r7._pending_cdp_verify[0] == "Вставка"


def test_verify_without_checker_just_logs(bare_r7, log):
    bare_r7._cdp_verify_or_defer("Копирование", _payload(), None, log)
    assert bare_r7._pending_cdp_verify is None
    assert any("Копирование" in m for m in log.messages)


def test_flush_verify_confirms_after_measurement(bare_r7, log):
    connector = _connected()
    connector.document_state.return_value = {"historyIndex": 9}
    bare_r7._webdriver_connector = connector
    bare_r7._pending_cdp_verify = ("Вставка", {"historyIndex": 4},
                                   r7mod.R7Testovarka._cdp_check_document_changed)

    bare_r7._flush_pending_cdp_verify(log_cb=log)

    assert bare_r7._pending_cdp_verify is None
    assert any(m.startswith("   ✅ CDP-проверка") for m in log.messages)


def test_flush_verify_reports_failure(bare_r7, log):
    connector = _connected()
    connector.document_state.return_value = {"historyIndex": 4}
    bare_r7._webdriver_connector = connector
    bare_r7._pending_cdp_verify = ("Вставка", {"historyIndex": 4},
                                   r7mod.R7Testovarka._cdp_check_document_changed)

    bare_r7._flush_pending_cdp_verify(log_cb=log)

    assert any("не подтверждено" in m for m in log.messages)


def test_flush_verify_noop_without_pending(bare_r7, log):
    connector = _connected()
    bare_r7._webdriver_connector = connector
    bare_r7._pending_cdp_verify = None

    bare_r7._flush_pending_cdp_verify(log_cb=log)

    connector.document_state.assert_not_called()
    assert log.messages == []


def test_flush_verify_survives_lost_connection(bare_r7, log):
    connector = Mock()
    connector.connected = False
    bare_r7._webdriver_connector = connector
    bare_r7._pending_cdp_verify = ("Вставка", {"historyIndex": 4},
                                   r7mod.R7Testovarka._cdp_check_document_changed)

    bare_r7._flush_pending_cdp_verify(log_cb=log)

    assert bare_r7._pending_cdp_verify is None
    assert any("не подтверждён" in m for m in log.messages)


def test_flush_verify_survives_exception(bare_r7, log):
    connector = _connected()
    connector.document_state.side_effect = RuntimeError("ws closed")
    bare_r7._webdriver_connector = connector
    bare_r7._pending_cdp_verify = ("Вставка", {}, lambda b, a: (True, ""))

    bare_r7._flush_pending_cdp_verify(log_cb=log)

    assert any("ws closed" in m for m in log.messages)


# ── конкретные операции: какие ссылки уходят в api ───────────────────────

def test_copy_paste_builds_expected_ranges(bare_r7, log, monkeypatch):
    """5 ячеек со смещением 15 → копируем A1:E1, вставляем в P1.

    Смещение — это число нажатий «вправо» от A1 в клавиатурной версии,
    поэтому целевой столбец = offset + 1.
    """
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    connector = _connected()
    connector.select_range.return_value = _payload()
    connector.copy.return_value = _payload()
    connector.insert_cells.return_value = _payload(mutated=True)
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0

    assert bare_r7._cdp_copy_paste(5, 15, shift="down", log_cb=log) is True

    refs = [c.args[0] for c in connector.select_range.call_args_list]
    assert refs == ["A1:E1", "P1"]
    connector.copy.assert_called_once()
    connector.insert_cells.assert_called_once()
    assert connector.insert_cells.call_args.args[0] == "down"
    connector.paste.assert_not_called()


def test_copy_paste_hotkey_variant_uses_plain_paste(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    connector = _connected()
    connector.select_range.return_value = _payload()
    connector.copy.return_value = _payload()
    connector.paste.return_value = _payload(mutated=True)
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0

    assert bare_r7._cdp_copy_paste(1, 10, log_cb=log) is True

    refs = [c.args[0] for c in connector.select_range.call_args_list]
    assert refs == ["A1:A1", "K1"]
    connector.paste.assert_called_once()
    connector.insert_cells.assert_not_called()


def test_add_column_goes_to_previous_sheet(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    connector = _connected()
    connector.show_sheet.return_value = _payload()
    connector.select_range.return_value = _payload()
    connector.insert_column.return_value = _payload(mutated=True)
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0

    assert bare_r7._cdp_add_column(log_cb=log) is True

    assert connector.show_sheet.call_args.args[0] == -1
    assert connector.show_sheet.call_args.kwargs["relative"] is True
    assert connector.select_range.call_args.args[0] == "B1"
    connector.insert_column.assert_called_once()


def test_paste_big_adds_sheet_then_pastes(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    connector = _connected()
    connector.add_sheet.return_value = _payload(mutated=True)
    connector.paste.return_value = _payload(mutated=True)
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0

    assert bare_r7._cdp_paste_big(log_cb=log) is True
    connector.add_sheet.assert_called_once()
    connector.paste.assert_called_once()


def test_select_all_falls_back_when_method_missing(bare_r7, log):
    connector = _connected()
    connector.select_all.return_value = _payload(ok=False,
                                                 reason="no-method:asc_EditSelectAll")
    bare_r7._webdriver_connector = connector

    assert bare_r7._cdp_select_all(log_cb=log) is False


def test_select_all_confirms_whole_sheet(bare_r7, log):
    connector = _connected()
    connector.select_all.return_value = _payload(
        before={"selection": "A1"}, after={"selection": "A1:XFD1048576"})
    bare_r7._webdriver_connector = connector

    assert bare_r7._cdp_select_all(log_cb=log) is True
    assert bare_r7._pending_cdp_verify is None


# ── клик по пункту меню ──────────────────────────────────────────────────

def test_click_context_item_passes_baseline(bare_r7, log):
    connector = _connected()
    connector.click_menu_item.return_value = {"clicked": True, "text": "Копировать",
                                              "matched": "копировать"}
    bare_r7._webdriver_connector = connector
    bare_r7._cdp_ui_baseline = [{"text": "Копировать", "tag": "li", "x": 1393, "y": 100}]
    bare_r7._paced_total = 0.0

    assert bare_r7._cdp_click_context_item(("копировать",), log_cb=log) is True
    assert connector.click_menu_item.call_args.kwargs["baseline"] == bare_r7._cdp_ui_baseline


def test_click_context_item_false_when_not_in_dom(bare_r7, log):
    """issue #9: контекстное меню, судя по дампам, вне обходимого DOM —
    неудача штатная, вызывающий код идёт стрелками."""
    connector = _connected()
    connector.click_menu_item.return_value = {"clicked": False, "candidates": 0}
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0

    assert bare_r7._cdp_click_context_item(("вставить ячейки",), log_cb=log) is False


def test_click_context_item_charges_pace(bare_r7, log, monkeypatch):
    """Регрессия CI (25.08.2026): исходная версия мерила реальное настенное
    время вокруг замоканного (мгновенного) click_menu_item и сравнивала с
    0.0 — на быстром раннере GitHub Actions time.time() до и после иногда
    попадает в одну и ту же дискрету таймера, разница честно равна 0.0, и
    строгое ">" падает. Локально на Windows-машине этой session не ловилось
    ни разу за ~300 прогонов — разное разрешение таймера. Детерминированный
    фикс: подменить time.time() на контролируемую последовательность с
    гарантированной ненулевой дельтой, а не полагаться на реальные часы."""
    connector = _connected()
    connector.click_menu_item.return_value = {"clicked": False}
    bare_r7._webdriver_connector = connector
    bare_r7._paced_total = 0.0

    fake_times = iter([100.0, 100.25])  # t0, затем t0 после round-trip
    monkeypatch.setattr(r7mod.time, "time", lambda: next(fake_times))

    bare_r7._cdp_click_context_item(("x",), log_cb=log, charge_pace=True)

    assert bare_r7._paced_total == pytest.approx(0.25)


def test_click_context_item_without_connector(bare_r7, log):
    bare_r7._webdriver_connector = None
    assert bare_r7._cdp_click_context_item(("x",), log_cb=log) is False


# ── диагностика api ──────────────────────────────────────────────────────

def test_api_info_logs_missing_methods(bare_r7, log):
    connector = _connected()
    connector.api_info.return_value = {
        "found": True, "frame": 3,
        "methods": {"asc_Copy": True, "asc_insertCells": False},
        "state": {"sheets": 2, "selection": "A1"},
    }
    bare_r7._webdriver_connector = connector

    bare_r7._cdp_log_api_info(log_cb=log)

    assert any("api редактора найден" in m for m in log.messages)
    assert any("asc_insertCells" in m for m in log.messages)


def test_api_info_reports_api_not_found(bare_r7, log):
    connector = _connected()
    connector.api_info.return_value = {"found": False}
    bare_r7._webdriver_connector = connector

    bare_r7._cdp_log_api_info(log_cb=log)

    assert any("не найден" in m for m in log.messages)


def test_api_info_without_connector(bare_r7, log):
    bare_r7._webdriver_connector = None
    bare_r7._cdp_log_api_info(log_cb=log)
    assert any("недоступны" in m for m in log.messages)


# ── сборка JS в коннекторе ───────────────────────────────────────────────

def test_show_sheet_js_relative_flag():
    rel = wdmod._show_sheet_js(-1, relative=True)
    absolute = wdmod._show_sheet_js(2)
    assert "var idx = -1;" in rel and "if (true)" in rel
    assert "var idx = 2;" in absolute and "if (false)" in absolute


def test_insert_cells_js_carries_option_name_and_fallback():
    js = wdmod._insert_cells_js("InsertColumns", 3)
    assert "'InsertColumns'" in js and "insertOpt(win, 'InsertColumns', 3)" in js


def test_select_range_js_quotes_reference():
    assert '"A1:E1"' in wdmod._select_range_js("A1:E1")


def test_op_js_reports_api_not_found_before_touching_document():
    """Проверка «api не найден» обязана стоять ДО любого изменения — иначе
    вызывающий код не сможет отличить «ничего не сделано» от «сделано наполовину»."""
    js = wdmod._SELECT_ALL_JS
    assert js.index("api-not-found") < js.index("api.asc_EditSelectAll()")


def test_click_by_text_js_embeds_baseline_and_lowercases_wanted():
    """Кириллица уезжает в JS через json.dumps в escape-виде — сравниваем
    так же закодированную строку, а не исходную."""
    js = wdmod._click_by_text_js(["Вставить Ячейки"],
                                 baseline=[{"text": "x", "tag": "li"}])
    assert json.dumps(["вставить ячейки"]) in js
    assert '"tag": "li"' in js


def test_click_by_text_js_prefers_exact_match():
    """Регрессия: «Копировать формат» не должен перехватывать клик,
    адресованный пункту «Копировать»."""
    js = wdmod._click_by_text_js(["копировать"])
    assert "pick(true) || pick(false)" in js


def test_click_by_text_js_defaults_to_context_menu_selector():
    js = wdmod._click_by_text_js(["копировать"])
    assert json.dumps(wdmod._CONTEXT_MENU_SEL) in js
    assert json.dumps(wdmod._RIBBON_PANEL_SEL) not in js


def test_click_by_text_js_accepts_custom_selector():
    """Ribbon-вкладки («Файл») и панели («Сохранить как» внутри неё) не
    попадают под _CONTEXT_MENU_SEL — найдено живым прогоном 27.08.2026
    (tests/manual_saveas_cdp_probe.py, candidates: 0 под старым селектором)."""
    js = wdmod._click_by_text_js(["файл"], selector=wdmod._RIBBON_PANEL_SEL)
    assert json.dumps(wdmod._RIBBON_PANEL_SEL) in js
    assert json.dumps(wdmod._CONTEXT_MENU_SEL) not in js


def test_click_ribbon_item_uses_ribbon_selector(connector):
    with patch.object(connector, "evaluate", return_value={"clicked": True}) as ev:
        result = connector.click_ribbon_item(["файл", "file"])
    assert result == {"clicked": True}
    js = ev.call_args[0][0]
    assert json.dumps(wdmod._RIBBON_PANEL_SEL) in js
    assert json.dumps(["файл", "file"]) in js


def test_click_ribbon_item_passes_baseline_and_timeout(connector):
    baseline = [{"text": "Файл", "tag": "a", "x": 0, "y": 33}]
    with patch.object(connector, "evaluate", return_value={"clicked": False}) as ev:
        connector.click_ribbon_item(["сохранить как"], baseline=baseline, timeout=5)
    js = ev.call_args[0][0]
    assert json.dumps(baseline) in js
    assert ev.call_args.kwargs["timeout"] == 5


# ── подключение перед первой операцией ───────────────────────────────────

def test_ensure_connected_calls_connect_once(bare_r7, log):
    """connect() зовётся лениво из триггера готовности; если файл открылся
    раньше, чем триггер дошёл до CDP, соединения нет — и все операции молча
    ушли бы на клавиши."""
    connector = Mock()
    connector.connected = False
    connector.connect.return_value = True
    bare_r7._webdriver_connector = connector

    assert bare_r7._cdp_ensure_connected(log_cb=log) is True
    connector.connect.assert_called_once()


def test_ensure_connected_noop_when_already_connected(bare_r7, log):
    connector = _connected()
    bare_r7._webdriver_connector = connector

    assert bare_r7._cdp_ensure_connected(log_cb=log) is True
    connector.connect.assert_not_called()


def test_ensure_connected_without_connector(bare_r7, log):
    bare_r7._webdriver_connector = None
    assert bare_r7._cdp_ensure_connected(log_cb=log) is False


def test_ensure_connected_survives_exception(bare_r7, log):
    connector = Mock()
    connector.connected = False
    connector.connect.side_effect = RuntimeError("порт закрыт")
    bare_r7._webdriver_connector = connector

    assert bare_r7._cdp_ensure_connected(log_cb=log) is False
    assert any("порт закрыт" in m for m in log.messages)


def test_sequence_marks_measurement_as_cdp(bare_r7, log):
    """below_floor на CDP-пути означает «api отработал синхронно», а не
    «измерить не смогли» — воркеры смотрят на этот флаг при выводе строки."""
    bare_r7._webdriver_connector = _connected()
    bare_r7._op_via_cdp = False

    bare_r7._cdp_sequence("op", _steps(_payload()), checker=None, log_cb=log)

    assert bare_r7._op_via_cdp is True


def test_sequence_leaves_flag_unset_on_fallback(bare_r7, log):
    bare_r7._webdriver_connector = _connected()
    bare_r7._op_via_cdp = False

    bare_r7._cdp_sequence("op", _steps(_payload(ok=False, reason="no-method")),
                          checker=None, log_cb=log)

    assert bare_r7._op_via_cdp is False


# ── формы записи полного выделения ───────────────────────────────────────

@pytest.mark.parametrize("selection", [
    "1:1048576",        # то, что реально отдаёт живая сборка после Ctrl+A
    "A1:XFD1048576",    # та же область, записанная ячейками
    "A:XFD",            # все столбцы
])
def test_check_whole_sheet_accepts_all_forms(selection):
    """Регрессия прогона 2026-08-25: asc_getActiveRangeStr после
    asc_EditSelectAll отдал «1:1048576», и проверка только по форме
    «A1:...» помечала выполненную операцию как неподтверждённую."""
    ok, _ = r7mod.R7Testovarka._cdp_check_whole_sheet_selected(
        {"selection": "A1"}, {"selection": selection})
    assert ok is True


@pytest.mark.parametrize("selection", ["A1:E1", "1:5", "B2", "A:C"])
def test_check_whole_sheet_rejects_partial_forms(selection):
    ok, _ = r7mod.R7Testovarka._cdp_check_whole_sheet_selected(
        {"selection": "A1"}, {"selection": selection})
    assert ok is False


# ── api_ms: субмиллисекундная метрика вместо детектора простоя ───────────
# Контекст (см. docstring _cdp_sequence и отчёт по нагрузочному тестированию,
# 25.08.2026): детектор простоя (_wait_operation_done) опрашивает CPU окном
# 0.20 с и требует 6 подтверждений подряд — операции короче этого окна дают
# разброс до 20× между прогонами одного файла. api_ms снимается внутри
# страницы через performance.now() и от опроса CPU не зависит вовсе.

def test_sequence_accumulates_api_ms_from_successful_steps(bare_r7, log):
    bare_r7._webdriver_connector = _connected()
    bare_r7._cdp_api_ms = 0.0
    steps = _steps(_payload(api_ms=0.42), _payload(mutated=True, api_ms=1.08))

    bare_r7._cdp_sequence("op", steps, checker=None, log_cb=log)

    assert bare_r7._cdp_api_ms == pytest.approx(1.50)


def test_sequence_ignores_missing_api_ms(bare_r7, log):
    """api_ms отсутствует у ответа api-not-found/no-method (см. _op_js) —
    накопление не должно падать на отсутствующем поле."""
    bare_r7._webdriver_connector = _connected()
    bare_r7._cdp_api_ms = 0.0
    payload_without_api_ms = {"ok": True, "mutated": False, "method": "asc_test",
                              "before": {}, "after": {}}
    steps = _steps(payload_without_api_ms)

    bare_r7._cdp_sequence("op", steps, checker=None, log_cb=log)

    assert bare_r7._cdp_api_ms == 0.0


def test_sequence_ignores_non_numeric_api_ms(bare_r7, log):
    bare_r7._webdriver_connector = _connected()
    bare_r7._cdp_api_ms = 0.0
    steps = _steps(_payload(api_ms="не число"))

    bare_r7._cdp_sequence("op", steps, checker=None, log_cb=log)

    assert bare_r7._cdp_api_ms == 0.0


def test_sequence_does_not_accumulate_failed_step(bare_r7, log):
    """Провалившийся шаг (status != 'ok') не должен вносить свой api_ms —
    его у него и не бывает (см. test_op_js_reports_api_not_found_before...),
    но проверяем поведение явно."""
    bare_r7._webdriver_connector = _connected()
    bare_r7._cdp_api_ms = 0.0
    steps = _steps(_payload(ok=False, reason="no-method:asc_X", api_ms=999))

    bare_r7._cdp_sequence("op", steps, checker=None, log_cb=log)

    assert bare_r7._cdp_api_ms == 0.0


def test_verify_or_defer_includes_api_ms_in_log(bare_r7, log):
    bare_r7._cdp_api_ms = 1.23
    bare_r7._cdp_verify_or_defer("Вставка", _payload(), None, log)

    assert any("api: 1.23 мс" in m for m in log.messages)


def test_verify_or_defer_omits_api_ms_note_when_zero(bare_r7, log):
    """Клавиатурный fallback никогда не заходит в _cdp_verify_or_defer, но
    api_ms=0 (например, все шаги без этого поля) не должен печатать пустую
    или вводящую в заблуждение пометку."""
    bare_r7._cdp_api_ms = 0.0
    bare_r7._cdp_verify_or_defer("Вставка", _payload(), None, log)

    assert not any("api:" in m for m in log.messages)


# ── JS-слой: тайминг вокруг вызова api (_op_js) ───────────────────────────

def test_op_js_measures_around_the_mutating_call():
    """__t0 стартует сразу после снимка «до» и останавливается сразу перед
    снимком «после» — то есть меряет ровно вызов api, а не docState()."""
    js = wdmod._SELECT_ALL_JS
    assert js.index("st.before = docState") < js.index("__t0 = performance.now()")
    assert js.index("__t0 = performance.now()") < js.index("api.asc_EditSelectAll()")
    assert js.index("api.asc_EditSelectAll()") < js.index("st.api_ms = performance.now()")
    assert js.index("st.api_ms = performance.now()") < js.index("st.after = docState")


def test_op_js_api_ms_absent_when_method_missing():
    """no-method — операция не выполнялась, измерять нечего: 'st.api_ms ='
    должно встречаться только в общем catch-блоке (не выполнится для этого
    early return), но не быть выставлено на самом пути no-method."""
    js = wdmod._insert_cells_js("InsertColumns", 3)
    no_method_branch = js[js.index("no-method:asc_insertCells") - 40:
                          js.index("no-method:asc_insertCells") + 60]
    assert "api_ms" not in no_method_branch


def test_op_js_api_ms_set_in_catch_block_for_diagnostics():
    """Исключение ПОСЛЕ старта таймера всё равно даёт api_ms — время до
    сбоя диагностически полезно (кнопка перестала существовать на середине
    операции и т.п.)."""
    js = wdmod._SELECT_ALL_JS
    catch_block = js[js.index("} catch (e) {"):]
    assert "st.api_ms = performance.now() - __t0;" in catch_block


def test_op_js_uses_shared_after_snapshot_anchor():
    """_AFTER_SNAPSHOT_LINE — общий якорь для всех операций; если он
    перестанет встречаться в теле операции ровно один раз, таймер либо не
    остановится, либо остановится не там.

    Сама фраза "st.api_ms = performance.now() - __t0;" в готовом JS
    встречается ДВАЖДЫ — один раз на успешном пути (перед st.after) и один
    раз в общем catch-блоке (диагностика времени до сбоя, см.
    test_op_js_api_ms_set_in_catch_block_for_diagnostics)."""
    for js in (wdmod._SELECT_ALL_JS, wdmod._COPY_JS, wdmod._PASTE_JS,
               wdmod._ADD_SHEET_JS, wdmod._insert_cells_js("InsertColumns", 3),
               wdmod._select_range_js("A1:E1"),
               wdmod._show_sheet_js(-1, relative=True)):
        assert js.count("st.after = docState(api, win);") == 1
        assert js.count("st.api_ms = performance.now() - __t0;") == 2
