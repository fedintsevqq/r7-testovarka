"""Ожидание закрытия Р7 по шагам (r7.close_wait): оба пути к «Сохранить
изменения?», интервал попыток CDP и строка итога перед принудительным
завершением."""
import sys
import types
from types import SimpleNamespace

import pytest

import r7.close_wait as cw


class _Clock:
    def __init__(self):
        self.t = 100.0

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def win(monkeypatch):
    """Окна: 1 — главное окно Р7 (pid 7), остальные задаются тестом."""
    state = SimpleNamespace(alive={1}, owners={1: 7}, visible={}, classes={}, titles={})
    gui = types.SimpleNamespace(
        IsWindow=lambda h: h in state.alive,
        IsWindowVisible=lambda h: state.visible.get(h, True),
        EnumWindows=lambda cb, extra: [cb(h, extra) for h in list(state.owners)],
        GetClassName=lambda h: state.classes.get(h, "Qt5QWindowIcon"),
        GetWindowText=lambda h: state.titles.get(h, ""))
    proc = types.SimpleNamespace(GetWindowThreadProcessId=lambda h: (0, state.owners[h]))
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32process", proc)
    clock = _Clock()
    monkeypatch.setattr(cw, "time", SimpleNamespace(perf_counter=clock.perf_counter,
                                                    sleep=clock.sleep))
    state.clock = clock
    return state


def _app(**kw):
    a = SimpleNamespace(CLOSE_CDP_RETRY_SEC=1.0, _webdriver_connector=None,
                        _click_priority_button=lambda w, keys, log_cb=None: (False, None),
                        _cdp_dismiss_save_dialog=lambda: None)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_owner_pid_and_siblings_of_same_process_only(win):
    win.owners.update({2: 7, 3: 99, 4: 7})
    win.visible[4] = False
    assert cw.owner_pid_of(1) == 7
    assert cw.sibling_windows(1, 7) == [2]          # чужой процесс и невидимое — мимо


def test_owner_pid_none_when_window_gone(win):
    assert cw.owner_pid_of(12345) is None


def test_window_gone_is_graceful(win):
    logs = []
    win.alive.clear()
    assert cw.CloseWait(_app(), 1, 7, logs.append, 5, 0.0).run() is True
    assert any("закрыт штатно" in m for m in logs)


def test_win32_dialog_clicked_only_dont_save(win):
    win.owners[2] = 7
    pressed = []

    def click(w, keys, log_cb=None):
        pressed.append((w, keys))
        win.alive.discard(1)                       # «Не сохранять» закрыло Р7
        return True, "Не сохранять"
    logs = []
    w = cw.CloseWait(_app(_click_priority_button=click), 1, 7, logs.append, 5, 0.0)
    assert w.run() is True
    assert pressed == [(2, cw.SAVE_DIALOG_BUTTONS)]
    assert "сохранить" not in cw.SAVE_DIALOG_BUTTONS   # кнопка по умолчанию перезапишет эталон
    assert w.dismissed and any("закрыт кнопкой «Не сохранять»" in m for m in logs)


def test_candidate_window_logged_once(win):
    win.owners[2] = 7
    win.titles[2] = "Р7-Офис"
    logs = []
    cw.CloseWait(_app(), 1, 7, logs.append, 1.0, 0.0).run()
    assert sum("Окно-кандидат" in m for m in logs) == 1


def test_cdp_tries_spaced_by_retry_interval_and_logged_once(win):
    calls = []
    app = _app(_cdp_dismiss_save_dialog=lambda: calls.append(win.clock.t) or "Не сохранять",
               _webdriver_connector=object())
    logs = []
    w = cw.CloseWait(app, 1, 7, logs.append, 3.0, 0.0)
    assert w.run() is False                         # окно так и не исчезло
    assert len(calls) == 3 and all(b - a >= 1.0 - 1e-9 for a, b in zip(calls, calls[1:]))
    assert sum("через CDP" in m for m in logs) == 1
    assert not w.dismissed                          # клик JS — ещё не закрытие
    assert w.summary() == ("   (Win32-кнопка: не найдена; CDP: коннектор есть, "
                           "попыток 3, клик был)")


def test_summary_without_connector(win):
    w = cw.CloseWait(_app(), 1, None, lambda m: None, 0.5, 0.0)
    assert w.run() is False
    assert "коннектор нет" in w.summary() and "клик не прошёл" in w.summary()


def test_second_document_dialog_is_dismissed_too(win):
    # Два несохранённых документа: Р7 спрашивает про каждый отдельным окном.
    win.owners.update({2: 7})
    pressed = []

    def click(w, keys, log_cb=None):
        pressed.append(w)
        win.alive.discard(w)
        win.owners.pop(w, None)
        if w == 2:
            win.owners[3] = 7                      # вопрос про второй документ
            win.alive.add(3)
        else:
            win.alive.discard(1)                   # оба ответа даны — окно закрылось
        return True, "Нет"
    w = cw.CloseWait(_app(_click_priority_button=click), 1, 7, lambda m: None, 5, 0.0)
    assert w.run() is True
    assert pressed == [2, 3]


def test_same_dialog_hwnd_reclicked_only_after_pause(win):
    # Qt может показать следующий вопрос в том же hwnd: жмём снова, но не на
    # каждом шаге цикла, пока окно после первого «Нет» ещё не исчезло.
    win.owners[2] = 7
    pressed = []

    def click(w, keys, log_cb=None):
        pressed.append(win.clock.t)
        if len(pressed) == 2:
            win.alive.discard(1)
        return True, "Нет"
    w = cw.CloseWait(_app(_click_priority_button=click), 1, 7, lambda m: None, 5, 0.0)
    assert w.run() is True
    assert len(pressed) == 2
    assert pressed[1] - pressed[0] >= cw.CLOSE_RECLICK_SEC
