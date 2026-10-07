"""Окна Р7 (r7.windows) — прямые тесты на поддельном win32gui
(plan-to-10, шаг 3): отзывчивость окна, Esc только в Р7, клик по заголовку,
кнопки диалогов, геометрия окна, диагностика заголовков."""
import sys
from types import SimpleNamespace

import pytest

import r7_Testovarka as r7mod
import r7.windows as rw

R = r7mod.R7Testovarka


class _Gui:
    """win32gui: окна hwnd → заголовок, дочерние — (текст, класс)."""

    def __init__(self):
        self.titles, self.hidden, self.children = {}, set(), {}
        self.foreground, self.sent, self.posted, self.moved = None, [], [], []
        self.fail = set()          # имена функций, которые бросают
        self.refuse_focus = False

    def _maybe_fail(self, name):
        if name in self.fail:
            raise OSError(name)

    def GetForegroundWindow(self):
        self._maybe_fail("GetForegroundWindow")
        return self.foreground

    def SetForegroundWindow(self, h):
        self._maybe_fail("SetForegroundWindow")
        if not self.refuse_focus:
            self.foreground = h

    def IsWindowVisible(self, h):
        return h not in self.hidden

    def GetWindowText(self, h):
        if h in self.children:
            return self.children[h][0]
        return self.titles[h]

    def GetClassName(self, h):
        return self.children[h][1]

    def EnumWindows(self, cb, extra):
        for h in list(self.titles):
            cb(h, extra)

    def EnumChildWindows(self, parent, cb, extra):
        self._maybe_fail("EnumChildWindows")
        for h in list(self.children):
            cb(h, extra)

    def SendMessage(self, h, msg, w, l):
        self._maybe_fail("SendMessage")
        self.sent.append((h, msg))

    def PostMessage(self, h, msg, w, l):
        self._maybe_fail("PostMessage")
        self.posted.append((h, msg))

    def SendMessageTimeout(self, h, msg, w, l, flags, ms):
        self._maybe_fail("SendMessageTimeout")
        return self.responsive_result

    def GetWindowRect(self, h):
        return (100, 200, 900, 800)

    def ShowWindow(self, h, cmd):
        self._maybe_fail("ShowWindow")

    def MoveWindow(self, h, x, y, w, hgt, repaint):
        self.moved.append((h, w, hgt))


@pytest.fixture
def gui(monkeypatch):
    g = _Gui()
    g.responsive_result = (1, 0)
    monkeypatch.setitem(sys.modules, "win32gui", g)
    monkeypatch.setattr(rw, "win32gui", g)
    monkeypatch.setattr(rw, "time", SimpleNamespace(sleep=lambda s: None,
                                                    perf_counter=_ticks()))
    monkeypatch.setattr(r7mod.env, "WIN32_OK", True)
    monkeypatch.setattr(r7mod.env, "PYAUTOGUI_OK", True)
    return g


def _ticks(step=0.1):
    t = [0.0]

    def perf_counter():
        t[0] += step
        return t[0]
    return perf_counter


@pytest.fixture
def app(log):
    a = R.__new__(R)
    a.add_test_log = log
    a._is_r7_window = lambda h: h in (10, 11)
    return a


# ── отзывчивость окна ────────────────────────────────────────────────────

def test_window_responsive(app, gui, monkeypatch):
    assert app._window_responsive(10, 40) is True
    gui.responsive_result = (0, 0)                         # таймаут
    assert app._window_responsive(10) is False
    gui.fail.add("SendMessageTimeout")                     # окно висит
    assert app._window_responsive(10) is False
    assert app._window_responsive(None) is True            # окна нет — решает CPU
    monkeypatch.setattr(r7mod.env, "WIN32_OK", False)
    assert app._window_responsive(10) is True


# ── заголовки окон ───────────────────────────────────────────────────────

def test_win_title_contains_and_wait(app, gui):
    gui.titles = {1: "Сохранить как", 2: "Скрытое окно"}
    gui.hidden = {2}
    assert app._win_title_contains("сохранить") is True
    assert app._win_title_contains("скрытое") is False
    assert app._wait_for_window_title(["сохранить"], timeout=1) is True
    assert app._wait_for_window_title(["нет такого"], timeout=0.5) is False


def test_win_title_contains_without_win32(app, monkeypatch):
    monkeypatch.setattr(r7mod.env, "WIN32_OK", False)
    assert app._win_title_contains("x") is False


def test_dump_visible_window_titles(app, gui, log):
    gui.titles = {i: f"окно {i}" for i in range(5)}
    gui.titles[9] = ""
    app._dump_visible_window_titles(log, limit=3)
    assert "окно 0" in log.messages[-1] and "(+2 ещё)" in log.messages[-1]


def test_dump_visible_window_titles_without_win32(app, monkeypatch, log):
    monkeypatch.setattr(r7mod.env, "WIN32_OK", False)
    app._dump_visible_window_titles(log)
    assert "дамп недоступен" in log.messages[-1]


# ── Esc только в окно Р7 на переднем плане ──────────────────────────────

def test_press_esc_only_when_r7_in_front(app, gui):
    pressed = []
    app._press = pressed.append
    gui.foreground = 10
    assert app._press_esc_in_r7(10) is True and pressed == ["esc"]
    gui.foreground, gui.refuse_focus = 99, True            # фокус не отдали — не жмём
    assert app._press_esc_in_r7(10) is False and pressed == ["esc"]
    gui.fail.add("GetForegroundWindow")
    assert app._press_esc_in_r7(10) is False
    assert app._press_esc_in_r7(None) is False


# ── клик по заголовку для фокуса ─────────────────────────────────────────

def test_foreground_click_hits_title_bar(app, gui, monkeypatch):
    clicks = []
    monkeypatch.setattr(rw, "pyautogui", SimpleNamespace(click=lambda x, y: clicks.append((x, y))))
    assert app._ensure_foreground_click(10) is True
    assert clicks == [(500, 210)]                          # середина, 10 px от верха — не документ


def test_foreground_click_retries_and_counts_interference(app, gui, monkeypatch, log):
    monkeypatch.setattr(rw, "pyautogui", SimpleNamespace(click=lambda x, y: None))
    gui.refuse_focus = True
    gui.fail.add("SetForegroundWindow")
    assert app._ensure_foreground_click(10, log_cb=log, attempts=2) is False
    assert sum("не в фокусе" in m for m in log.messages) == 2
    assert app._interference == {"focus_lost": 2}


def test_foreground_click_refuses_foreign_window(app, gui, log):
    assert app._ensure_foreground_click(55, log_cb=log) is False
    assert "не принадлежит Р7" in log.messages[-1]
    assert app._ensure_foreground_click(None, log_cb=log) is False


# ── кнопки диалогов ──────────────────────────────────────────────────────

def test_click_priority_button_by_keyword_order(app, gui, log):
    gui.children = {1: ("Сохранить", "Button"), 2: ("Не сохранять", "Button")}
    clicked, text = app._click_priority_button(5, ("не сохранять", "нет"), log_cb=log)
    assert (clicked, text) == (True, "Не сохранять")
    assert gui.sent == [(2, rw.win32con.BM_CLICK)]         # «Сохранить» не тронут


def test_click_priority_button_falls_back_to_mouse_messages(app, gui, log):
    gui.children = {2: ("Нет", "Button")}
    gui.fail.add("SendMessage")
    assert app._click_priority_button(5, ("нет",), log_cb=log) == (True, "Нет")
    assert [m for _h, m in gui.posted] == [rw.win32con.WM_LBUTTONDOWN, rw.win32con.WM_LBUTTONUP]


def test_click_priority_button_nothing_found_dumps_children(app, gui, log):
    gui.children = {3: ("Справка", "Button")}
    assert app._click_priority_button(5, ("нет",), log_cb=log) == (False, None)
    assert any("Справка" in m for m in log.messages)
    gui.fail.add("EnumChildWindows")                       # окно уже закрыто
    assert app._click_priority_button(5, ("нет",), log_cb=log) == (False, None)


# ── геометрия окна ───────────────────────────────────────────────────────

def test_fix_geometry_fits_small_screen(app, gui, monkeypatch, log):
    monkeypatch.setattr(rw, "win32api", SimpleNamespace(
        GetSystemMetrics=lambda i: 1366 if i == rw.win32con.SM_CXSCREEN else 768))
    assert app._fix_r7_window_geometry(10, log_cb=log) == {"width": 1366, "height": 768}
    assert gui.moved == [(10, 1366, 768)] and "меньше цели" in log.messages[-1]


def test_fix_geometry_full_size_and_error(app, gui, monkeypatch, log):
    monkeypatch.setattr(rw, "win32api", SimpleNamespace(GetSystemMetrics=lambda i: 4000))
    assert app._fix_r7_window_geometry(10, log_cb=log) == {"width": R.R7_WINDOW_W,
                                                           "height": R.R7_WINDOW_H}
    gui.fail.add("ShowWindow")
    assert app._fix_r7_window_geometry(10, log_cb=log) is None
    assert "Не удалось зафиксировать" in log.messages[-1]
