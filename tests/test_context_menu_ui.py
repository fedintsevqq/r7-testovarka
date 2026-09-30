"""Запасные пути тестов правки через интерфейс Р7 (30.09.2026): контекстное
меню у выделения, диалог «Вставить ячейки», Esc на модалке пересчёта без CDP,
перезапуск под .venv.

Живой прогон на Р7 2026.3.2 показал, что прежние пути не работали: меню
открывалось правым кликом там, где стоит мышь; стрелки меню не двигают;
Enter на «Вставить ▸» подменю не выбирает; в диалоге «Вставить ячейки» по
умолчанию выбран сдвиг вправо; Alt+I в этой сборке ничего не открывает.
"""
import json
import shutil
import subprocess
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import r7_webdriver_connector as wd


class Keys:
    """Записывает нажатия вместо pyautogui."""

    def __init__(self):
        self.sent = []

    def hotkey(self, *keys):
        self.sent.append("+".join(keys))

    def press(self, key, n=1, pace=0.0):
        self.sent.extend([key] * n)


@pytest.fixture
def keys():
    return Keys()


@pytest.fixture
def ui(bare_r7, monkeypatch):
    bare_r7.add_test_log = Mock()
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    return bare_r7


def _connector(**kw):
    c = Mock()
    c.connected = True
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ── _context_menu_pick ───────────────────────────────────────────────────

def test_menu_pick_opens_menu_by_shift_f10_not_mouse(ui, keys, log):
    ui._webdriver_connector = _connector(
        click_context_menu_path=Mock(return_value={"clicked": True}),
        context_menu_open=Mock(return_value=False))
    ui._context_menu_pick(["Копировать"], keys.hotkey, log)
    assert keys.sent == ["shift+f10"]


def test_menu_pick_waits_for_menu_and_charges_only_click(ui, keys, log, monkeypatch):
    """Ожидание меню — работа Р7 (доделывает прошлый шаг), не вычитается;
    вычитается только round-trip удачного клика."""
    clock = iter([0.0, 0.0, 5.0, 5.0, 5.2, 5.3, 5.4])
    monkeypatch.setattr(r7mod.time, "perf_counter", lambda: next(clock))
    ui._webdriver_connector = _connector(
        click_context_menu_path=Mock(side_effect=[
            {"clicked": False, "reason": "menu-not-open"},
            {"clicked": True}]),
        context_menu_open=Mock(return_value=False))
    ui._context_menu_pick(["Вставить", "Вставить"], keys.hotkey, log)
    assert ui._paced_total == pytest.approx(0.2)


def test_menu_pick_waits_until_menu_closed(ui, keys, log):
    """Клик отложен: следующий Shift+F10, пока меню открыто, терялся."""
    opened = Mock(side_effect=[True, True, False])
    ui._webdriver_connector = _connector(
        click_context_menu_path=Mock(return_value={"clicked": True}),
        context_menu_open=opened)
    ui._context_menu_pick(["Копировать"], keys.hotkey, log)
    assert opened.call_count == 3


def test_menu_pick_missing_item_closes_menu_and_raises(ui, keys, log):
    ui._webdriver_connector = _connector(
        click_context_menu_path=Mock(return_value={
            "clicked": False, "reason": "disabled:Ячейки со сдвигом вниз",
            "items": ["Строку"]}))
    with pytest.raises(RuntimeError, match="сдвигом вниз"):
        ui._context_menu_pick(["Добавить", "Ячейки со сдвигом вниз"], keys.hotkey, log)
    assert keys.sent == ["shift+f10", "esc"]


# ── _context_menu_copy_paste ─────────────────────────────────────────────

def test_pkm_without_cdp_raises_before_any_key(ui, keys):
    with pytest.raises(RuntimeError, match="без CDP"):
        ui._context_menu_copy_paste(5, 15, keys.hotkey, keys.press)
    assert keys.sent == []


def test_pkm_chain_matches_cdp_semantics(ui, keys, monkeypatch):
    picks = []
    ui._webdriver_connector = _connector()
    monkeypatch.setattr(ui, "_context_menu_pick",
                        lambda path, hotkey, log_cb=None: picks.append((path, list(keys.sent))))
    ui._context_menu_copy_paste(3, 10, keys.hotkey, keys.press)
    assert [p for p, _ in picks] == [
        ["Копировать"],
        ["Добавить", "Ячейки со сдвигом вниз"],
        ["Вставить", "Вставить"]]
    # копия A1:C1, затем столбец 11 (вправо 10 раз) и выделение трёх ячеек
    assert picks[0][1] == ["ctrl+home", "shift+right", "shift+right"]
    assert picks[1][1][3:] == ["right"] * 10 + ["shift+right", "shift+right"]


# ── _add_column_ui ───────────────────────────────────────────────────────

def test_add_column_dialog_selects_column_after_focus(ui, keys):
    state = Mock(side_effect=[
        None,                                   # диалога ещё нет
        {"focused": False, "choice": 0},        # есть, фокус ещё в сетке
        {"focused": True, "choice": 0},
        {"focused": True, "choice": 3}])
    ui._webdriver_connector = _connector(insert_cells_dialog_state=state)
    ui._add_column_ui('hotkey', keys.hotkey, keys.press)
    assert keys.sent == ["ctrl+pageup", "right", "ctrl+shift+=",
                         "tab", "tab", "tab", "space", "enter"]


def test_add_column_dialog_wrong_choice_cancels(ui, keys, monkeypatch):
    clock = iter(x * 0.1 for x in range(1000))
    monkeypatch.setattr(r7mod.time, "perf_counter", lambda: next(clock))
    ui._webdriver_connector = _connector(insert_cells_dialog_state=Mock(
        return_value={"focused": True, "choice": 0}))
    with pytest.raises(RuntimeError, match="Столбец"):
        ui._add_column_ui('hotkey', keys.hotkey, keys.press)
    assert keys.sent[-1] == "esc" and "enter" not in keys.sent


def test_add_column_menu_without_cdp_raises_before_any_key(ui, keys):
    with pytest.raises(RuntimeError, match="без CDP"):
        ui._add_column_ui('menu', keys.hotkey, keys.press)
    assert keys.sent == []


def test_add_column_menu_uses_context_menu_not_alt_i(ui, keys, monkeypatch):
    picks = []
    ui._webdriver_connector = _connector()
    monkeypatch.setattr(ui, "_context_menu_pick",
                        lambda path, hotkey, log_cb=None: picks.append(path))
    ui._add_column_ui('menu', keys.hotkey, keys.press)
    assert picks == [["Добавить", "Столбец"]]
    assert "alt+i" not in keys.sent


# ── Esc без CDP ──────────────────────────────────────────────────────────

def test_press_esc_refuses_when_r7_not_foreground(ui, monkeypatch):
    sent = []
    monkeypatch.setattr(r7mod, "PYAUTOGUI_OK", True)
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    monkeypatch.setattr(r7mod.pyautogui, "press", lambda k: sent.append(k), raising=False)
    fake = Mock()
    fake.GetForegroundWindow.return_value = 999
    monkeypatch.setattr(r7mod, "win32gui", fake, raising=False)
    assert ui._press_esc_in_r7(123) is False
    assert sent == []


# ── Перезапуск под .venv ─────────────────────────────────────────────────

def _venv(tmp_path):
    exe = tmp_path / ".venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    return exe


def test_relaunch_when_cdp_packages_missing(tmp_path, monkeypatch):
    exe = _venv(tmp_path)
    monkeypatch.setattr(r7mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", False)
    monkeypatch.setattr(r7mod.sys, "executable", r"C:\Python314\python.exe")
    monkeypatch.delenv("R7_NO_VENV_RELAUNCH", raising=False)
    assert r7mod._venv_python_for_relaunch() == exe


@pytest.mark.parametrize("case", ["ok", "guard", "same", "no_venv"])
def test_no_relaunch(case, tmp_path, monkeypatch):
    exe = _venv(tmp_path)
    monkeypatch.setattr(r7mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", case == "ok")
    monkeypatch.setattr(r7mod, "PYWINAUTO_OK", True)
    monkeypatch.setattr(r7mod.sys, "executable",
                        str(exe) if case == "same" else r"C:\Python314\python.exe")
    if case == "guard":
        monkeypatch.setenv("R7_NO_VENV_RELAUNCH", "1")
    else:
        monkeypatch.delenv("R7_NO_VENV_RELAUNCH", raising=False)
    if case == "no_venv":
        exe.unlink()
    assert r7mod._venv_python_for_relaunch() is None


# ── JS коннектора в Node ─────────────────────────────────────────────────

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js не установлен")

# Фейковый DOM меню: li > a, подменю — li.dropdown-submenu > .dropdown-menu.
MENU_PRELUDE = r"""
globalThis.__clicks = [];
function A(text, o) { o = o || {}; this.textContent = text; this._w = o.w === undefined ? 60 : o.w;
  this.parentNode = { classList: { contains: function (c) { return c === 'disabled' && !!o.off; } },
                      querySelector: function () { return o.sub || null; } }; }
A.prototype.getBoundingClientRect = function () { return { width: this._w, height: this._w ? 12 : 0 }; };
A.prototype.click = function () { __clicks.push(this.textContent); };
A.prototype.dispatchEvent = function () {};
function Sub(items) { this._items = items; }
Sub.prototype.querySelectorAll = function () { return this._items; };
globalThis.MouseEvent = function () {};
globalThis.setTimeout = function (f) { f(); };
globalThis.getComputedStyle = function () { return { visibility: 'visible', display: 'block' }; };
globalThis.frames = [];
globalThis.window = globalThis;
function setMenu(items) {
  globalThis.document = { querySelectorAll: function () { return items; } };
}
"""


def run_menu_js(setup, expr):
    script = MENU_PRELUDE + setup + "\nconst __r = (" + expr + ");\n" \
        "console.log(JSON.stringify({result: __r, clicks: __clicks}));\n"
    proc = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True,
                          encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@needs_node
def test_js_submenu_exact_label_among_similar():
    setup = """
      var sub = new Sub([new A('Вставить только формулу'), new A('Вставить')]);
      setMenu([new A('Копировать'), new A('Вставить', {sub: sub})]);
    """
    out = run_menu_js(setup, wd._click_context_menu_path_js(["Вставить", "Вставить"]))
    assert out["result"]["clicked"] is True
    assert out["clicks"] == ["Вставить"]


@needs_node
def test_js_disabled_submenu_item_not_clicked():
    setup = """
      var sub = new Sub([new A('Ячейки со сдвигом вниз', {off: true})]);
      setMenu([new A('Добавить', {sub: sub})]);
    """
    out = run_menu_js(setup, wd._click_context_menu_path_js(["Добавить", "Ячейки со сдвигом вниз"]))
    assert out["result"]["clicked"] is False
    assert out["result"]["reason"].startswith("disabled:")
    assert out["clicks"] == []


@needs_node
def test_js_hidden_menu_reports_not_open():
    """У скрытых меню тулбара ширина 0 — они не считаются раскрытым меню."""
    out = run_menu_js("setMenu([new A('Копировать', {w: 0})]);",
                      wd._click_context_menu_path_js(["Копировать"]))
    assert out["result"]["reason"] == "menu-not-open"
    assert out["clicks"] == []

