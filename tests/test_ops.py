"""Общие тест-операции обоих воркеров (r7_ops.SpreadsheetOps, этап 1).

Прежде операции жили вложенными функциями в _spreadsheet_worker и
_batch_run_single_version, и юнит-тесты их не видели. Теперь — один класс:
здесь проверяется CDP-путь, клавиатурный запасной путь и список тестов.
"""
import pytest

import r7_ops
import r7_Testovarka as r7mod


class FakeApp:
    """Пишет вызовы в calls; _cdp_* возвращают cdp_ok."""
    OP_KEY_PACE = r7mod.R7Testovarka.OP_KEY_PACE
    OP_SELECT_ALL_MAX_SEC = r7mod.R7Testovarka.OP_SELECT_ALL_MAX_SEC

    def __init__(self, cdp_ok):
        self.cdp_ok = cdp_ok
        self.calls = []
        self._paste_sheet_prepared = False

    def __getattr__(self, name):
        if not name.startswith("_"):
            raise AttributeError(name)

        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name.startswith("_cdp_"):
                return self.cdp_ok
            return None
        return method

    def names(self):
        return [c[0] for c in self.calls]

    def keys(self):
        return [c[1] for c in self.calls if c[0] in ("_hotkey", "_press")]


def _log(msg):
    pass


def _ops(cdp_ok):
    app = FakeApp(cdp_ok)
    return app, r7_ops.SpreadsheetOps(app, find_hwnd=lambda: 1, log_cb=_log, test_file="f.xlsx")


OPS = [
    ("select_all", (), "_cdp_select_all", [("ctrl", "a")]),
    ("copy_all", (), "_cdp_copy", [("ctrl", "c")]),
    ("paste_big", (), "_cdp_paste_big", [("shift", "f11"), ("ctrl", "v")]),
    ("add_sheet", (), "_cdp_add_sheet", [("shift", "f11")]),
    ("copy_paste_hotkey", (5, 15), "_cdp_copy_paste",
     [("ctrl", "home")] + [("shift", "right")] * 4 + [("ctrl", "c"), ("right",), ("ctrl", "v")]),
]


@pytest.mark.parametrize("op, args, cdp, _keys", OPS)
def test_cdp_success_presses_no_keys(op, args, cdp, _keys):
    app, ops = _ops(cdp_ok=True)
    getattr(ops, op)(*args)
    assert cdp in app.names()
    assert app.keys() == []


@pytest.mark.parametrize("op, args, cdp, keys", OPS)
def test_cdp_failure_falls_back_to_keys(op, args, cdp, keys):
    app, ops = _ops(cdp_ok=False)
    getattr(ops, op)(*args)
    assert app.names()[0] == cdp
    assert app.keys() == keys


def test_paste_big_skips_new_sheet_when_prepared():
    """Подготовка уже создала свежий лист — запасной путь только вставляет."""
    app, ops = _ops(cdp_ok=False)
    app._paste_sheet_prepared = True
    ops.paste_big()
    assert app.keys() == [("ctrl", "v")]


def test_paste_big_paces_after_new_sheet():
    app, ops = _ops(cdp_ok=False)
    ops.paste_big()
    paces = [c for c in app.calls if c[0] == "_pace"]
    assert paces == [("_pace", (app.OP_KEY_PACE,), {})]


def test_select_all_shortens_max_wait():
    app, ops = _ops(cdp_ok=True)
    ops.select_all()
    assert app.__dict__["_op_max_wait"] == app.OP_SELECT_ALL_MAX_SEC


@pytest.mark.parametrize("method", ["hotkey", "menu"])
def test_add_column_fallback_goes_to_ui(method):
    app, ops = _ops(cdp_ok=False)
    ops.add_column(method)
    name, args, kwargs = app.calls[-1]
    assert name == "_add_column_ui"
    assert args == (method, ops.hotkey, ops.press)
    assert kwargs["log_cb"] is _log


def test_add_column_cdp_skips_ui():
    app, ops = _ops(cdp_ok=True)
    ops.add_column("menu")
    assert "_add_column_ui" not in app.names()


def test_copy_paste_context_shifts_down_and_falls_back_to_menu():
    app, ops = _ops(cdp_ok=False)
    ops.copy_paste_context(1, 10)
    cdp = next(c for c in app.calls if c[0] == "_cdp_copy_paste")
    assert cdp[1] == (1, 10) and cdp[2]["shift"] == "down"
    assert app.calls[-1][0] == "_context_menu_copy_paste"


def test_press_paces_between_presses():
    app, ops = _ops(cdp_ok=True)
    ops.press("down", presses=3, pace=0.05)
    assert app.names() == ["_press", "_pace"] * 3


def test_save_as_format_passes_window_and_keys():
    app, ops = _ops(cdp_ok=True)
    ops.save_as_format("csv")
    name, args, kwargs = app.calls[-1]
    assert name == "_save_as_format"
    assert args == ("csv", ops.find_hwnd, ops.hotkey, ops.press)
    assert kwargs["log_cb"] is _log


def test_tests_match_definitions_without_open_test():
    _, ops = _ops(cdp_ok=True)
    names = [n for n, _ in ops.tests()]
    expected = [n for n in r7mod.R7Testovarka.TEST_DEFINITIONS
                if n != r7mod.R7Testovarka.OPEN_TEST_NAME]
    assert names == expected


def test_edit_tests_have_prepare_exports_do_not():
    """У каждого теста правки — подготовка вне замера; экспорт идёт по
    открытому документу как есть."""
    _, ops = _ops(cdp_ok=True)
    for name, func in ops.tests():
        if name in r7mod.R7Testovarka.EXPORT_TESTS:
            assert not hasattr(func, "prepare"), name
        else:
            assert callable(getattr(func, "prepare", None)), name


def test_prepares_run_on_work_sheet_with_log():
    app, ops = _ops(cdp_ok=True)
    for _, func in ops.tests():
        if hasattr(func, "prepare"):
            func.prepare()
    prep = {c[0] for c in app.calls}
    assert {"_prepare_on_work_sheet", "_prepare_select_all_on_work_sheet",
            "_paste_big_prepare", "_vlookup_prepare", "_del_column_prepare"} <= prep
    for c in app.calls:
        assert c[2].get("log_cb") is _log, c[0]
    vl = next(c for c in app.calls if c[0] == "_vlookup_prepare")
    assert vl[1] == ("f.xlsx",)


def test_each_test_runs_its_operation():
    """Функция каждого теста зовёт свою операцию (не соседнюю)."""
    expected = {
        "Выделение всех ячеек (Ctrl+A)": "_cdp_select_all",
        "Копирование всех ячеек (Ctrl+C)": "_cdp_copy",
        "Вставка большого массива (Ctrl+V)": "_cdp_paste_big",
        "Добавление нового листа": "_cdp_add_sheet",
        "Добавление столбца (горячие клавиши)": "_cdp_add_column",
        "Добавление столбца (меню Вставка)": "_cdp_add_column",
        "Вставка 1 ячейки (горячие клавиши)": "_cdp_copy_paste",
        "Вставка 5 ячеек (горячие клавиши)": "_cdp_copy_paste",
        "Вставка 1 ячейки (ПКМ)": "_cdp_copy_paste",
        "Вставка 5 ячеек (ПКМ)": "_cdp_copy_paste",
        "Функция ВПР (50K строк)": "_vlookup_op",
        "Удаление столбца (Del)": "_del_column_op",
    }
    names = [n for n, _ in _ops(cdp_ok=True)[1].tests()]
    for name in names:
        app, ops = _ops(cdp_ok=True)
        dict(ops.tests())[name]()
        if name in expected:
            assert app.calls[0][0] == expected[name], name
        else:
            assert app.calls[0][0] == "_save_as_format"
            assert app.calls[0][1][0] in name.lower()
