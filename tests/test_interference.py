"""Вмешательства стенда в прогон (фокус, буфер обмена) — в «Условия прогона»."""
from unittest.mock import Mock

import r7_Testovarka as r7mod


def _app():
    a = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    a._applied_r7_window_size = None
    a._cached_cpu_count = 4
    return a


def test_counts_become_report_warnings():
    a = _app()
    a._run_environment = {"warnings": ["ноутбук работает от батареи"]}
    a._note_interference("focus_lost")
    a._note_interference("focus_lost")
    a._note_interference("clipboard_foreign")
    info = a._build_system_info()
    assert info["interference"] == {"focus_lost": 2, "clipboard_foreign": 1}
    w = info["environment"]["warnings"]
    assert w[0] == "ноутбук работает от батареи"
    assert any("теряло фокус 2 раз" in x for x in w) and any("буфер обмена" in x for x in w)
    assert a._run_environment["warnings"] == ["ноутбук работает от батареи"]  # исходник не тронут


def test_no_interference_keeps_environment_as_is():
    a = _app()
    a._run_environment = None
    info = a._build_system_info()
    assert info["environment"] is None and info["interference"] == {}


def test_foreign_clipboard_counted_only_after_our_copy(bare_r7):
    conn = Mock(connected=True)
    conn.copy.return_value = {"ok": True}
    conn.document_state.return_value = {}
    conn.add_sheet.return_value = {"ok": False}
    bare_r7._webdriver_connector = conn
    bare_r7.add_test_log = lambda m: None
    bare_r7._prepare_select_all_on_work_sheet = lambda log_cb=None: {"name": "1"}
    bare_r7._prepare_on_work_sheet = lambda ref=None, log_cb=None: None
    bare_r7._cdp_settle = lambda *a, **k: True
    seqs = iter([1, 2, 5, 6])
    bare_r7._clipboard_seq = lambda: next(seqs)
    bare_r7._paste_big_prepare()                  # первая копия — норма
    assert getattr(bare_r7, "_interference", {}) == {}
    bare_r7._paste_big_prepare()                  # буфер сменился не нами
    assert bare_r7._interference == {"clipboard_foreign": 1}
