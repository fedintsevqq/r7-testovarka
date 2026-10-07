"""Поиск окна Р7 в хелпере живых проверок (tests/manual_cdp_smoke.py):
вкладка браузера с «Р7-Офис» в заголовке — не окно Р7 (правило 9)."""
import sys
import types

import manual_cdp_smoke as smoke


def _fake_os(monkeypatch, windows):
    """windows: [(hwnd, title, pid)]; pid 1 — editors.exe, 2 — chrome.exe."""
    names = {1: "editors.exe", 2: "chrome.exe"}
    by_h = {h: (t, pid) for h, t, pid in windows}
    gui = types.SimpleNamespace(
        GetWindowText=lambda h: by_h[h][0],
        EnumWindows=lambda cb, extra: [cb(h, extra) for h, _t, _p in windows])
    proc = types.SimpleNamespace(GetWindowThreadProcessId=lambda h: (0, by_h[h][1]))
    ps = types.SimpleNamespace(Process=lambda pid: types.SimpleNamespace(name=lambda: names[pid]))
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32process", proc)
    monkeypatch.setitem(sys.modules, "psutil", ps)


def test_browser_tab_with_r7_in_title_is_not_r7_window(monkeypatch):
    _fake_os(monkeypatch, [(10, "Техническая поддержка Р7-Офис - Google Chrome", 2)])
    assert smoke.find_hwnd_factory("live_smoke_1")() is None


def test_r7_window_with_file_name_is_preferred(monkeypatch):
    _fake_os(monkeypatch, [(10, "Р7-Офис - Google Chrome", 2),
                           (20, "Р7-Офис. Профессиональный", 1),
                           (30, "live_smoke_10k.xlsx - Р7-Офис", 1)])
    assert smoke.find_hwnd_factory("live_smoke_1")() == 30
