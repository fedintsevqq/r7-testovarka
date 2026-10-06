"""Состояние прогона (r7.run_state): один прогон за раз — вкладка, Batch или
тест своего файла (этап 4 плана). Без Tk."""
import threading

import pytest

from r7.run_state import (BATCH, CUSTOM, PERF, REFUSALS, RunState, RunStateMixin,
                          missing_packages)

KINDS = (PERF, BATCH, CUSTOM)


def test_idle_state_allows_any_start():
    st = RunState()
    for kind in KINDS:
        assert st.refusal(kind) is None


@pytest.mark.parametrize("active", KINDS)
@pytest.mark.parametrize("wanted", KINDS)
def test_every_pair_is_refused_with_its_own_message(active, wanted):
    """Все девять сочетаний отказываются, и у каждого — свой текст для окна."""
    st = RunState()
    assert st.try_start(active) is None
    title, text = st.try_start(wanted)
    assert (title, text) == REFUSALS[(wanted, active)]
    assert st.active == active                  # отказ состояние не меняет


def test_cross_mode_refusal_explains_keyboard_conflict():
    st = RunState()
    st.try_start(BATCH)
    assert "клавиатурой" in st.refusal(PERF)[1]
    assert "клавиатурой" in st.refusal(CUSTOM)[1]


def test_finish_releases_only_own_kind_and_is_idempotent():
    st = RunState()
    st.try_start(PERF)
    st.finish(BATCH)                            # чужой — не трогает
    assert st.is_running(PERF)
    st.finish(PERF)
    st.finish(PERF)                             # finally может сработать дважды
    assert st.active is None and st.try_start(BATCH) is None


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        RunState().try_start("soak")


def test_try_start_is_atomic_under_contention():
    """Двойной щелчок по «Запустить» из разных потоков — стартует один."""
    st = RunState()
    barrier = threading.Barrier(16)
    wins = []

    def go(kind):
        barrier.wait()
        if st.try_start(kind) is None:
            wins.append(kind)

    threads = [threading.Thread(target=go, args=(KINDS[i % 3],)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and st.active == wins[0]


class _App(RunStateMixin):
    pass


def test_mixin_creates_state_lazily_for_objects_without_init():
    app = _App()
    assert app._perf_running is False and app.run_state.active is None


def test_legacy_flags_map_onto_state():
    app = _App()
    app._batch_running = True
    assert app.run_state.active == BATCH and app._batch_running is True
    assert app.run_state.refusal(PERF)[0] == "Выполняется Batch-режим"
    app._perf_running = False                   # сброс чужого флага не освобождает Batch
    assert app._batch_running is True
    app._batch_running = False
    assert app.run_state.active is None


def test_missing_packages_lists_only_absent_ones():
    assert missing_packages(True, True, True, True) == []
    assert missing_packages(False, True, False, True) == ["pyautogui", "openpyxl"]
    assert missing_packages(True, False, True, False) == ["pyperclip", "pywin32"]


def test_batch_start_failure_releases_state(bare_r7, monkeypatch):
    """Окно Batch не открылось — состояние освобождено, иначе приложение
    считало бы Batch идущим до перезапуска."""
    import r7.ui.batch as ui_batch

    def boom(*a, **k):
        raise RuntimeError("Tk сломан")
    monkeypatch.setattr(ui_batch.BatchUiMixin, "_open_batch_progress", boom)
    with pytest.raises(RuntimeError):
        bare_r7._start_batch_run([], None, False, False)
    assert bare_r7.run_state.active is None


def test_batch_start_refused_while_custom_running(bare_r7, monkeypatch):
    import r7.ui.batch as ui_batch
    shown = []
    monkeypatch.setattr(ui_batch.messagebox, "showwarning", lambda *a, **k: shown.append(a))
    opened = []
    monkeypatch.setattr(ui_batch.BatchUiMixin, "_open_batch_progress",
                        lambda self, *a: opened.append(a))
    bare_r7.run_state.try_start(CUSTOM)
    bare_r7._start_batch_run([], None, False, False)
    assert opened == [] and shown[0][0] == "Выполняется тест своего файла"


# ── Установка и прогоны (INSTALL) ────────────────────────────────────────

def test_install_and_runs_exclude_each_other():
    from r7.run_state import INSTALL
    st = RunState()
    st.try_start(PERF)
    assert st.try_start(INSTALL)[0] == "Выполняется тест производительности"
    st.finish(PERF)
    st.try_start(INSTALL)
    for kind in (PERF, BATCH, CUSTOM, INSTALL):
        assert st.try_start(kind)[0] == "Идёт установка версии"


# ── _start_run: один цикл для всех фоновых прогонов ──────────────────────

class _SyncThread:
    """threading глазами модуля: поток выполняется сразу при start()."""
    class Thread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    def __getattr__(self, name):
        return getattr(threading, name)


@pytest.fixture
def launcher(bare_r7, monkeypatch):
    import r7.ui.main_window as mw
    monkeypatch.setattr(mw, "threading", _SyncThread())
    calls = []
    bare_r7._ui_call = lambda fn: calls.append(fn)
    shown = []
    monkeypatch.setattr(mw.messagebox, "showwarning", lambda *a, **k: shown.append((a, k)))
    return bare_r7, calls, shown


def test_start_run_releases_state_when_target_raises(launcher):
    app, ui_calls, _ = launcher
    done = []

    def boom():
        assert app.run_state.is_running(BATCH)
        raise RuntimeError("воркер упал")
    with pytest.raises(RuntimeError):
        app._start_run(BATCH, boom, on_done=lambda: done.append(1))
    assert app.run_state.active is None
    ui_calls[0]()
    assert done == [1]                          # on_done — через главный поток


def test_start_run_releases_state_when_before_fails(launcher):
    app, _, _ = launcher

    def before():
        raise RuntimeError("окно не открылось")
    with pytest.raises(RuntimeError):
        app._start_run(PERF, lambda: None, before=before)
    assert app.run_state.active is None


def test_start_run_refusal_shown_over_parent_dialog(launcher):
    app, _, shown = launcher
    app.run_state.try_start(PERF)
    ran = []
    assert app._start_run(CUSTOM, lambda: ran.append(1), parent="dlg") is False
    assert ran == [] and shown[0][1] == {"parent": "dlg"}
    assert shown[0][0][0] == "Выполняется тест производительности"
