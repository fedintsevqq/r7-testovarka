"""Окно «Выбрать параметры CSV» (QA-аудит G-15).

Структура — из живого дампа UIA на Р7 2026.3.2 (06.10.2026): три QComboBox
без имени и automation_id (кодировка, конец строки, разделитель — в этом
порядке), флажок BOM, кнопки «OK»/«Отмена». Прежде был тест только на
«окно не появилось».
"""
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod

ENCODINGS = ["Arabic (ISO 8859-6) 28596", "Cyrillic (Windows) 1251",
             "Unicode (UTF-8) 65001", "Unicode (UTF-16) 1200"]
LINE_ENDS = ["LF (0x0A \\n)", "CR (0x0D \\r)", "CRLF (0x0D 0x0A \\r\\n"]
DELIMITERS = ["Запятая", "Точка с запятой", "Двоеточие", "Табуляция", "Пробел", "Другое"]


class _Combo:
    def __init__(self, items, selected):
        self.items, self.selected = items, selected

    def texts(self):
        return list(self.items)

    def selected_text(self):
        return self.selected


class _Check:
    def __init__(self, state):
        self.state = state

    def get_toggle_state(self):
        return self.state


class _Button:
    def __init__(self, text):
        self.text = text
        self.invoked = False

    def window_text(self):
        return self.text

    def invoke(self):
        self.invoked = True


class _Dialog:
    def __init__(self, combos, checks, buttons):
        self.by_type = {"ComboBox": combos, "CheckBox": checks, "Button": buttons}

    def descendants(self, control_type=None):
        return list(self.by_type.get(control_type, []))


def _live_dialog(order=(0, 1, 2), ok=True):
    combos = [_Combo(ENCODINGS, "Unicode (UTF-8) 65001"),
              _Combo(LINE_ENDS, "LF (0x0A \\n)"),
              _Combo(DELIMITERS, "Запятая")]
    buttons = [_Button("Закрыть"), _Button("Отмена")] + ([_Button("OK")] if ok else [])
    return _Dialog([combos[i] for i in order], [_Check(1)], buttons)


@pytest.fixture
def env(bare_r7, monkeypatch):
    import pywinauto
    state = {"dialog": _live_dialog()}
    desktop = Mock()
    desktop.return_value.window.side_effect = lambda handle: state["dialog"]
    monkeypatch.setattr(pywinauto, "Desktop", desktop)
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    monkeypatch.setattr(r7mod, "PYWINAUTO_OK", True)
    bare_r7._r7_window_owner_pids = lambda: {4242}
    bare_r7._find_window_hwnd = lambda *a, **k: 555
    bare_r7._paced_total = 0.0
    state["log"] = []
    state["call"] = lambda: bare_r7._confirm_csv_options(log_cb=state["log"].append, timeout=1)
    return state


def test_live_dialog_values_and_ok(env):
    chosen = env["call"]()
    assert chosen == {"encoding": "Unicode (UTF-8) 65001", "line_end": "LF (0x0A \\n)",
                      "delimiter": "Запятая", "bom": True}
    ok = [b for b in env["dialog"].by_type["Button"] if b.text == "OK"][0]
    assert ok.invoked
    assert not any(b.invoked for b in env["dialog"].by_type["Button"] if b.text != "OK")


@pytest.mark.parametrize("order", [(2, 0, 1), (1, 2, 0), (2, 1, 0)])
def test_roles_by_content_not_order(env, order):
    """Другая сборка переставит списки — журнал не должен перепутать
    кодировку с разделителем."""
    env["dialog"] = _live_dialog(order=order)
    chosen = env["call"]()
    assert chosen["encoding"] == "Unicode (UTF-8) 65001"
    assert chosen["line_end"] == "LF (0x0A \\n)"
    assert chosen["delimiter"] == "Запятая"


def test_unknown_lists_fall_back_to_order():
    combos = [_Combo(["a"], "a"), _Combo(["b"], "b"), _Combo(["c"], "c")]
    roles = r7mod.R7Testovarka._csv_option_roles(combos)
    assert [roles[k] for k in ("encoding", "line_end", "delimiter")] == combos


def test_no_ok_button_returns_none(env):
    env["dialog"] = _live_dialog(ok=False)
    assert env["call"]() is None
    assert any("нет кнопки OK" in m for m in env["log"])


def test_wait_for_answer_is_subtracted(env, bare_r7, monkeypatch):
    """Окно ждало ответа — Р7 простаивал, это время вычитается из замера."""
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(r7mod.time, "perf_counter", lambda: float(next(ticks)))
    assert bare_r7._confirm_csv_options(log_cb=env["log"].append, timeout=100) is not None
    assert bare_r7._paced_total > 0.0
