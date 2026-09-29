"""Тесты правок по аудиту точности замеров (29.09.2026).

Детекторы простоя гоняются на фейковых часах: time.perf_counter и
time.sleep подменены так, что sleep двигает часы, а CPU процессов Р7
задаётся сценарием. Живой Р7 не нужен.
"""
import pytest

import r7_Testovarka as r7mod


class FakeClock:
    def __init__(self, t0=1000.0):
        self.t = t0

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += s


class ScriptedProc:
    """Фейковый psutil.Process: cpu_percent() отдаёт значение по часам."""

    def __init__(self, clock, busy_until, busy_pct=100.0, idle_pct=0.0,
                 pid=4242, name="editors.exe"):
        self.clock = clock
        self.busy_until = busy_until
        self.busy_pct = busy_pct
        self.idle_pct = idle_pct
        self.pid = pid
        self._name = name

    def name(self):
        return self._name

    def cpu_percent(self, interval=None):
        return self.busy_pct if self.clock.t <= self.busy_until else self.idle_pct

    def is_running(self):
        return True


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(r7mod.time, "perf_counter", c.perf_counter)
    monkeypatch.setattr(r7mod.time, "sleep", c.sleep)
    return c


@pytest.fixture
def detector_env(bare_r7, monkeypatch):
    monkeypatch.setattr(r7mod, "PSUTIL_OK", True)
    monkeypatch.setattr(r7mod, "WIN32_OK", False)   # _window_responsive → True
    bare_r7._cached_cpu_count = 16
    bare_r7._ready_at = None
    bare_r7._op_start_grace = None
    bare_r7._op_max_wait = None
    monkeypatch.setattr(bare_r7, "_wait_for_bold_button_cdp", lambda *a, **k: False,
                        raising=False)
    monkeypatch.setattr(bare_r7, "_wait_for_bold_button", lambda *a, **k: False,
                        raising=False)
    monkeypatch.setattr(bare_r7, "_find_bold_button_hwnd", lambda *a, **k: None,
                        raising=False)
    return bare_r7


# ── Пункт 1: порог в % одного ядра, а не всей машины ──────────────────────

def test_single_thread_busy_is_busy_on_many_cores(detector_env, clock, log):
    """Один занятый поток на 16 ядрах = 6.25% машины. По старому
    нормированному порогу 4% это ещё «занято», но на 32 ядрах (3.1%) уже нет.
    Новый порог в % ядра от числа ядер не зависит."""
    detector_env._cached_cpu_count = 64
    proc = ScriptedProc(clock, busy_until=clock.t + 2.0, busy_pct=100.0)
    detector_env._get_r7_processes = lambda log_cb=None: [proc]
    start = clock.t
    done, status = detector_env._wait_operation_done(None, log_cb=log)
    assert status == "ok"
    assert done - start == pytest.approx(2.0, abs=0.25)


def test_background_noise_below_core_threshold_is_idle(detector_env, clock, log):
    proc = ScriptedProc(clock, busy_until=0, idle_pct=detector_env.OP_BUSY_CORE_PCT - 1)
    detector_env._get_r7_processes = lambda log_cb=None: [proc]
    _done, status = detector_env._wait_operation_done(None, log_cb=log)
    assert status == "below_floor"


# ── Пункт 2: готовность документа = начало простоя ────────────────────────

def test_ready_at_is_start_of_idle_not_return_time(detector_env, clock, log):
    load_s = 5.0
    proc = ScriptedProc(clock, busy_until=clock.t + load_s)
    detector_env._get_r7_processes = lambda log_cb=None: [proc]
    start = clock.t
    assert detector_env._wait_until_r7_ready(None, timeout=60, log_cb=log) is True
    returned_at = clock.t
    ready = detector_env._ready_at - start
    # Точность — один шаг опроса; накопление READY_IDLE_SAMPLES (~3 с) в
    # результат не попадает.
    assert ready == pytest.approx(load_s, abs=detector_env.READY_POLL_SEC + 1e-9)
    assert returned_at - detector_env._ready_at >= (
        detector_env.READY_IDLE_SAMPLES - 1) * detector_env.READY_POLL_SEC


def test_ready_at_set_on_timeout(detector_env, clock, log):
    proc = ScriptedProc(clock, busy_until=clock.t + 1e9)
    detector_env._get_r7_processes = lambda log_cb=None: [proc]
    assert detector_env._wait_until_r7_ready(None, timeout=2, log_cb=log) is False
    assert detector_env._ready_at is not None


def test_split_open_timing_setup_not_subtracted_by_default():
    res = r7mod.R7Testovarka._split_open_timing(0.0, 2.0, 10.0)
    assert res["total_open_ms"] == pytest.approx(10000.0)


# ── Пункт 5: таймауты вне статистики ─────────────────────────────────────

def test_select_stats_runs_excludes_timeouts(bare_r7):
    times = [1.0, 1.1, 180.0, 1.2]
    statuses = ["ok", "ok", "timeout", "ok"]
    stats, discarded, n_to = bare_r7._select_stats_runs(times, statuses)
    assert stats == [1.1, 1.2]
    assert discarded is True
    assert n_to == 1


def test_select_stats_runs_first_timeout_not_counted_as_warmup(bare_r7):
    stats, discarded, n_to = bare_r7._select_stats_runs(
        [20.0, 1.0, 1.1], ["timeout", "ok", "ok"])
    # Прогрев — первый прогон, а он и так исключён как таймаут; валидные
    # прогоны не трогаем.
    assert stats == [1.0, 1.1]
    assert discarded is False
    assert n_to == 1


def test_select_stats_runs_all_timeouts_keeps_all(bare_r7):
    stats, _d, n_to = bare_r7._select_stats_runs([20.0, 20.0], ["timeout", "timeout"])
    assert stats == [20.0, 20.0]
    assert n_to == 2


def test_valid_runs_filters_timeouts_and_accepts_old_files():
    V = r7mod.R7Testovarka._valid_runs
    assert V({"runs": [1, 2, 3], "run_statuses": ["ok", "timeout", "ok"]}) == [1, 3]
    assert V({"runs": [1, 2]}) == [1, 2]      # measure_schema < 3
    assert V(None) == []


def test_repeat_loop_uses_stats_selection_helper():
    import inspect
    src = inspect.getsource(r7mod.R7Testovarka._measure_op_repeated)
    assert "self._stats_indices(run_statuses)" in src
    assert "run_statuses.append(status)" in src


# ── Пункт 6: экспорт, дождавшийся файла, — не below_floor ──────────────────

def test_resolve_op_end_uses_file_time_on_below_floor(bare_r7):
    bare_r7._op_completed_at = 50.0
    assert bare_r7._resolve_op_end(60.0, "below_floor") == (50.0, "ok")


def test_resolve_op_end_keeps_detector_when_r7_was_busy(bare_r7):
    bare_r7._op_completed_at = 50.0
    assert bare_r7._resolve_op_end(55.0, "ok") == (55.0, "ok")


def test_resolve_op_end_noop_without_file(bare_r7):
    bare_r7._op_completed_at = None
    assert bare_r7._resolve_op_end(10.0, "below_floor") == (10.0, "below_floor")


def test_both_workers_use_shared_repeat_loop():
    """Пункт 13: одна реализация цикла повторов на оба режима."""
    import inspect
    for fn in (r7mod.R7Testovarka._spreadsheet_worker,
               r7mod.R7Testovarka._batch_run_single_version):
        assert "self._measure_op_repeated(" in inspect.getsource(fn)
    src = inspect.getsource(r7mod.R7Testovarka._measure_op_repeated)
    assert "self._resolve_op_end(" in src
    assert "self._op_completed_at = None" in src
    assert r7mod.R7Testovarka.BATCH_TEST_RUNS - 1 >= r7mod.MIN_RUNS_FOR_COMPARISON


def test_wait_for_export_file_reports_mtime(bare_r7, tmp_path, monkeypatch, log):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.7 data")
    import os, time as _t
    old = _t.time() - 5.0
    os.utime(f, (old, old))
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    before = _t.perf_counter()
    assert bare_r7._wait_for_export_file(str(f), timeout=5, log_cb=log) is True
    # Файл дописан ~5 с назад — конец экспорта в прошлом, а не «сейчас».
    assert before - bare_r7._op_completed_at == pytest.approx(5.0, abs=0.5)


# ── Пункт 4: повторы одной операции — на одном и том же документе ─────────

class _HistConnector:
    connected = True

    def __init__(self, index):
        self.index = index
        self.undo_calls = []

    def document_state(self, timeout=None):
        return {"historyIndex": self.index}

    def undo_to(self, target, timeout=None):
        self.undo_calls.append(target)
        steps = self.index - target
        self.index = target
        return {"ok": True, "reached": True, "steps": steps, "undo_ms": 1.0}


@pytest.fixture
def hist_env(bare_r7, monkeypatch):
    monkeypatch.setattr(bare_r7, "_wait_operation_done",
                        lambda *a, **k: (0.0, "below_floor"), raising=False)
    return bare_r7


def test_restore_history_undoes_to_snapshot(hist_env, log):
    c = _HistConnector(index=4)
    hist_env._webdriver_connector = c
    before = hist_env._history_snapshot(log_cb=log)
    c.index = 7                      # прогон добавил 3 точки истории
    assert hist_env._restore_history(before, "op", log_cb=log) is True
    assert c.undo_calls == [4]
    assert c.index == 4


def test_restore_history_noop_when_unchanged(hist_env, log):
    c = _HistConnector(index=4)
    hist_env._webdriver_connector = c
    assert hist_env._restore_history({"index": 4}, "op", log_cb=log) is True
    assert c.undo_calls == []


def test_restore_history_without_cdp_reports_dependency_once(hist_env, log):
    hist_env._webdriver_connector = None
    hist_env._restore_unavailable_logged = False
    assert hist_env._restore_history(None, "op", log_cb=log) is None
    assert hist_env._restore_history(None, "op", log_cb=log) is None
    assert sum("Откат правок" in m for m in log.messages) == 1


def test_restore_history_reports_failure(hist_env, log):
    c = _HistConnector(index=5)
    c.undo_to = lambda target, timeout=None: {"ok": True, "reached": False, "steps": 50}
    hist_env._webdriver_connector = c
    assert hist_env._restore_history({"index": 2}, "op", log_cb=log) is False


def test_worker_restores_only_between_repeats():
    """Откат только между повторами: после последнего правка остаётся —
    на неё опираются следующие операции цепочки (ВПР ищет по листу из
    «Вставки большого массива»)."""
    import inspect
    src = inspect.getsource(r7mod.R7Testovarka._measure_op_repeated)
    assert "if i < runs - 1 and not" in src
    assert "self._restore_history(hist_before" in src


def test_undo_js_targets_index():
    import r7_webdriver_connector as wd
    js = wd._undo_to_js(3, 50)
    assert "asc_Undo" in js and "cur <= 3" in js and "i < 50" in js


def test_open_test_is_first_definition():
    names = r7mod.R7Testovarka.TEST_DEFINITIONS
    assert names[0] == r7mod.R7Testovarka.OPEN_TEST_NAME


# ── Пункт 10: ресурсы за окно операции, а не снимок после неё ─────────────

class _CpuProc:
    def __init__(self, pid, cpu_s):
        self.pid = pid
        self.cpu_s = cpu_s
        self.alive = True

    def cpu_times(self):
        if not self.alive:
            raise r7mod.psutil.NoSuchProcess(self.pid)
        return type("T", (), {"user": self.cpu_s, "system": 0.0})()

    def cpu_percent(self, interval=None):
        return 50.0

    def memory_info(self):
        return type("M", (), {"rss": 100 * 1024 * 1024})()


def test_op_watch_counts_cpu_seconds_of_window_only():
    editor = _CpuProc(1, cpu_s=10.0)       # 10 с набежали ДО операции
    procs = [editor]
    w = r7mod.OpResourceWatch(lambda: list(procs), interval=0.01)
    w.start()
    editor.cpu_s = 12.5                    # операция: +2.5 с
    x2t = _CpuProc(2, cpu_s=0.0)           # конвертер родился внутри окна
    procs.append(x2t)
    w._scan()
    x2t.cpu_s = 1.0
    w._poll()
    x2t.alive = False                      # и умер до конца окна
    procs.remove(x2t)
    res = w.stop()
    assert res["cpu_sec"] == pytest.approx(3.5)
    assert res["ram_peak_mb"] == pytest.approx(200.0)


def test_aggregate_uses_stats_runs(bare_r7):
    bare_r7._cached_cpu_count = 4
    run_res = [
        {"cpu_sec": 9.0, "cpu_peak_core_pct": 400.0, "cpu_avg_core_pct": 300.0, "ram_peak_mb": 900.0},
        {"cpu_sec": 1.0, "cpu_peak_core_pct": 100.0, "cpu_avg_core_pct": 80.0, "ram_peak_mb": 500.0},
        {"cpu_sec": 2.0, "cpu_peak_core_pct": 120.0, "cpu_avg_core_pct": 100.0, "ram_peak_mb": 510.0},
    ]
    agg = bare_r7._aggregate_op_resources(run_res, [1, 2])   # прогрев отброшен
    assert agg["cpu_sec"] == pytest.approx(1.5)
    assert agg["cpu_peak_core_pct"] == 120.0
    assert agg["ram"] == 510.0
    assert agg["cpu_normalized"] == pytest.approx(90.0 / 4, abs=0.1)


def test_ready_idle_not_before_last_unresponsive_check(detector_env, clock, log, monkeypatch):
    """Регрессия живого прогона: цикл с неотзывчивым окном длится до 0.45 с,
    и начало CPU-окна попадало в период, когда окно ещё не отвечало."""
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    unresponsive_until = clock.t + 1.0

    def responsive(hwnd, timeout_ms=None):
        if clock.t < unresponsive_until:
            clock.t += (timeout_ms or detector_env.READY_RESPONSIVE_MS) / 1000
            return False
        return True
    monkeypatch.setattr(detector_env, "_window_responsive", responsive)
    proc = ScriptedProc(clock, busy_until=0)       # CPU простаивает с самого начала
    detector_env._get_r7_processes = lambda log_cb=None: [proc]
    start = clock.t
    assert detector_env._wait_until_r7_ready(1, timeout=60, log_cb=log) is True
    # Последняя проверка с неотзывчивым окном закончилась на 0.90 с, первая
    # отзывчивая — на 1.05 с: истинный момент лежит между ними.
    assert 0.9 - 1e-9 <= detector_env._ready_at - start <= 1.05 + 1e-9


# ── Пункт 11: автосохранение и окружение ─────────────────────────────────

def test_autosave_suspend_then_restore(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    from unittest.mock import Mock
    c = Mock()
    c.connected = True
    c.suspend_autosave.return_value = {"gap_ms": 1000, "periodic": True}
    c.restore_autosave.return_value = True
    bare_r7._webdriver_connector = c
    bare_r7._suspend_autosave(log_cb=log)
    bare_r7._restore_autosave(log_cb=log)
    bare_r7._restore_autosave(log_cb=log)          # повтор — no-op
    c.restore_autosave.assert_called_once()
    assert c.restore_autosave.call_args[0][0] == {"gap_ms": 1000, "periodic": True}


def test_autosave_restore_failure_is_loud(bare_r7, log):
    from unittest.mock import Mock
    c = Mock()
    c.connected = True
    c.restore_autosave.return_value = False
    bare_r7._webdriver_connector = c
    bare_r7._autosave_state = {"gap_ms": 1000, "periodic": True}
    bare_r7._restore_autosave(log_cb=log)
    assert any("вручную" in m for m in log.messages)


def test_restore_autosave_js_only_reenables_what_was_on():
    import r7_webdriver_connector as wd
    c = wd.R7WebDriverConnector(port=1, log_cb=lambda *_: None)
    sent = []
    c.evaluate = lambda js, timeout=None: sent.append(js) or True
    c.restore_autosave({"gap_ms": 1000, "periodic": False})
    assert "asc_setAutoSaveGap(1.0)" in sent[0]
    assert "if (false &&" in sent[0]


def test_environment_warns_on_busy_system(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "PSUTIL_OK", True)
    monkeypatch.setattr(r7mod.psutil, "process_iter", lambda attrs=None: [])
    monkeypatch.setattr(r7mod.psutil, "cpu_percent", lambda interval=None: 35.0)
    monkeypatch.setattr(r7mod.subprocess, "run", lambda *a, **k: type(
        "R", (), {"stdout": "GUID: x  (Сбалансированная)".encode("cp866")})())
    env = bare_r7._capture_environment(log_cb=log)
    assert env["power_plan"] == "Сбалансированная"
    assert len(env["warnings"]) == 2


# ── Пункт 15: из паузы вычитается только простой Р7 ──────────────────────

def test_pause_fully_charged_when_r7_idle(bare_r7):
    # 0.5 с паузы, Р7 потратил 0.01 с CPU (2% ядра — шум) → вычитаем всё.
    assert bare_r7._idle_share_of_pause(0.5, 10.0, 10.01) == pytest.approx(0.5)


def test_pause_partly_charged_when_r7_busy(bare_r7):
    # Р7 работал 0.3 с из 0.5 → вычесть можно только 0.2.
    assert bare_r7._idle_share_of_pause(0.5, 10.0, 10.3) == pytest.approx(0.2)


def test_pause_not_charged_when_r7_fully_busy(bare_r7):
    # Многопоточная работа: CPU больше длительности паузы → вычитать нечего.
    assert bare_r7._idle_share_of_pause(0.5, 10.0, 11.2) == 0.0


def test_pause_falls_back_without_cpu_data(bare_r7):
    assert bare_r7._idle_share_of_pause(0.5, None, None) == 0.5


# ── Пункт 16: переходный процесс после открытия не читается как тренд ─────

def test_detect_leak_ignores_settling_after_open():
    # Первые 20% ряда RAM оседает с 2000 до 1000 МБ, дальше растёт на 60 МБ/ч.
    samples = []
    for i in range(100):
        t = i * 60.0
        heap = 2000 - i * 50 if i < 20 else 1000 + (t / 3600.0) * 60
        samples.append({"t": t, "heap_mb": heap, "doc_count": 10})
    res = r7mod.detect_leak(samples)
    assert res["leak"] is True
    assert res["slope_mb_per_hour"] == pytest.approx(60.0, rel=0.05)
    old = r7mod.detect_leak(samples, warmup_frac=0.0)
    assert old["slope_mb_per_hour"] < 60.0   # оседание тянуло наклон вниз


# ── Модалка тяжёлого пересчёта (Р7 2026.3+) ──────────────────────────────

class _PromptConnector:
    """Модалка висит с shown_at до клика; после клика Р7 считает recalc_s."""
    connected = True

    def __init__(self, clock, proc, shown_at, recalc_s):
        self.clock, self.proc = clock, proc
        self.shown_at, self.recalc_s = shown_at, recalc_s
        self.clicked_at = None

    def dismiss_heavy_calc_prompt(self, timeout=None):
        if self.clicked_at is None and self.clock.t >= self.shown_at:
            self.clicked_at = self.clock.t
            self.proc.busy_until = self.clock.t + self.recalc_s
            return {"clicked": True, "text": "Автоматический пересчёт"}
        return {"clicked": False}


def test_ready_excludes_prompt_wait_and_includes_recalc(detector_env, clock, log):
    start = clock.t
    proc = ScriptedProc(clock, busy_until=start + 3.0)
    detector_env._get_r7_processes = lambda log_cb=None: [proc]
    # Модалка появляется в момент окончания загрузки, но «замечаем» мы её
    # только через 2 с (как если бы опрос был реже) — это ожидание пользователя.
    c = _PromptConnector(clock, proc, shown_at=start + 5.0, recalc_s=1.0)
    detector_env._webdriver_connector = c
    assert detector_env._wait_until_r7_ready(None, timeout=60, log_cb=log) is True
    # 3 с загрузки + 1 с пересчёта; ~2 с ожидания ответа вычтены.
    assert detector_env._ready_at - start == pytest.approx(4.0, abs=0.35)
    assert c.clicked_at is not None


def test_op_detector_charges_prompt_wait_to_pace(detector_env, clock, log):
    detector_env._paced_total = 0.0
    start = clock.t
    proc = ScriptedProc(clock, busy_until=start + 1.0)
    detector_env._get_r7_processes = lambda log_cb=None: [proc]
    c = _PromptConnector(clock, proc, shown_at=start + 1.2, recalc_s=2.0)
    detector_env._webdriver_connector = c
    done, status = detector_env._wait_operation_done(None, log_cb=log)
    assert status == "ok"
    assert c.clicked_at is not None
    # Замер закрывается ПОСЛЕ пересчёта, ожидание ответа ушло в _paced_total.
    assert done - start >= 1.0 + 2.0 - 0.01
    assert 0.0 <= detector_env._paced_total < 0.6


def test_dismiss_prompt_js_clicks_no():
    import r7_webdriver_connector as wd
    c = wd.R7WebDriverConnector(port=1, log_cb=lambda *_: None)
    sent = []
    c.evaluate = lambda js, timeout=None: sent.append(js) or {"clicked": False}
    c.dismiss_heavy_calc_prompt()
    assert 'button[result=\\"no\\"]' in sent[0] or 'button[result="no"]' in sent[0]
    assert ".asc-window.alert" in sent[0]


def test_restore_history_returns_selection_and_sheet(hist_env, log):
    """Выделение и лист вне истории правок: после «Выделить всё» второй
    повтор ничего не делал, пока выделение не вернули (живой прогон)."""
    from unittest.mock import Mock
    c = _HistConnector(index=4)
    state = {"historyIndex": 4, "active": 0, "selection": "A1"}
    c.document_state = lambda timeout=None: dict(state)
    c.show_sheet = Mock(return_value={"ok": True})
    c.select_range = Mock(return_value={"ok": True})
    hist_env._webdriver_connector = c
    before = hist_env._history_snapshot(log_cb=log)
    assert before == {"index": 4, "active": 0, "selection": "A1"}
    state.update(active=2, selection="1:1048576")    # прогон сменил лист и выделение
    assert hist_env._restore_history(before, "op", log_cb=log) is True
    c.show_sheet.assert_called_once()
    assert c.show_sheet.call_args[0][0] == 0
    assert c.select_range.call_args[0][0] == "A1"


def test_repeat_loop_waits_for_idle_before_timer():
    import inspect
    src = inspect.getsource(r7mod.R7Testovarka._measure_op_repeated)
    settle = src.index("self._wait_operation_done(find_hwnd, log_cb=log_cb, start_grace=0.3)")
    assert settle < src.index("start = time.perf_counter()")


# ── Предохранитель «операция не изменила документ» ───────────────────────

def test_op_expects_change_classification(bare_r7):
    assert bare_r7._op_expects_change("Функция ВПР (50K строк)")
    assert bare_r7._op_expects_change("Вставка большого массива (Ctrl+V)")
    assert not bare_r7._op_expects_change("Выделение всех ячеек (Ctrl+A)")
    assert not bare_r7._op_expects_change("Копирование всех ячеек (Ctrl+C)")
    assert not bare_r7._op_expects_change("Сохранение в PDF (конвертация x2t)")


def test_repeat_loop_flags_no_effect_ops():
    import inspect
    src = inspect.getsource(r7mod.R7Testovarka._measure_op_repeated)
    assert "self._op_expects_change(name)" in src
    assert 'hist_after["index"] == hist_before["index"]' in src


# ── Тесты правки: ВПР, ПКМ, удаление столбца (переделаны 29.09.2026) ─────

def _ws_connector(sheets):
    from unittest.mock import Mock
    c = Mock()
    c.connected = True
    c.sheets_info.return_value = sheets
    c.show_sheet.return_value = {"ok": True}
    c.select_range.return_value = {"ok": True}
    return c


FIXTURE_SHEETS = [
    {"index": 0, "name": "test", "autofilter": False, "rows": 28, "cols": 10},
    {"index": 1, "name": "1", "autofilter": False, "rows": 50001, "cols": 49},
    {"index": 2, "name": "2", "autofilter": True, "rows": 50001, "cols": 55},
]


def test_work_sheet_skips_autofilter_and_prefers_biggest(bare_r7):
    bare_r7._webdriver_connector = _ws_connector(FIXTURE_SHEETS)
    assert bare_r7._work_sheet()["name"] == "1"


def test_work_sheet_tie_prefers_leftmost(bare_r7):
    sheets = FIXTURE_SHEETS + [{"index": 3, "name": "copy", "autofilter": False,
                                "rows": 50001, "cols": 55}]
    bare_r7._webdriver_connector = _ws_connector(sheets)
    assert bare_r7._work_sheet()["index"] == 1


def test_vlookup_prepare_one_formula_per_row(bare_r7, log, monkeypatch):
    c = _ws_connector(FIXTURE_SHEETS)
    bare_r7._webdriver_connector = c
    copied = []
    monkeypatch.setattr(r7mod.pyperclip, "copy", copied.append)
    bare_r7._vlookup_prepare(log_cb=log)
    c.show_sheet.assert_called_once()
    assert c.show_sheet.call_args[0][0] == 1
    assert c.select_range.call_args[0][0] == "AY2"     # 49 столбцов данных + 1 пустой
    lines = copied[0].split("\r\n")
    assert len(lines) == 50000
    assert lines[0] == "=VLOOKUP(A2,$A$2:$B$50001,2,FALSE)"
    assert lines[-1] == "=VLOOKUP(A50001,$A$2:$B$50001,2,FALSE)"


def test_del_column_uses_delete_columns(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    c = _ws_connector(FIXTURE_SHEETS)
    c.delete_columns.return_value = {"ok": True, "mutated": True, "method": "asc_deleteCells",
                                     "before": {"historyIndex": 0}, "after": {"historyIndex": 1}}
    bare_r7._webdriver_connector = c
    bare_r7._del_column_prepare(log_cb=log)
    assert c.select_range.call_args[0][0] == "B1"
    bare_r7._del_column_op(log_cb=log)
    c.delete_columns.assert_called_once()


def test_delete_columns_js():
    import r7_webdriver_connector as wd
    assert "asc_deleteCells" in wd._DELETE_COLUMNS_JS
    assert "st.mutated = true" in wd._DELETE_COLUMNS_JS
    assert "api_ms" in wd._DELETE_COLUMNS_JS          # таймер внедрён _op_js


def test_prepare_runs_before_timer_in_repeat_loop():
    import inspect
    src = inspect.getsource(r7mod.R7Testovarka._measure_op_repeated)
    assert src.index('getattr(func, "prepare", None)') < src.index("start = time.perf_counter()")


def test_edit_tests_have_prepare_in_both_workers():
    import inspect
    for fn in (r7mod.R7Testovarka._spreadsheet_worker,
               r7mod.R7Testovarka._batch_run_single_version):
        src = inspect.getsource(fn)
        assert "_vlookup_prepare(" in src
        assert "_del_column_prepare" in src
        assert src.count("_with_prepare(") >= 6


# ── Основной маркер открытия — кнопка «Жирный» ──────────────────────────

class _BoldConnector:
    """Кнопка доступна в интервалах enabled=[(from, to)]; наблюдатель в
    странице пишет момент последнего включения (как _BOLD_READY_PROBE_JS)."""
    connected = True

    def __init__(self, clock, enabled, installed_at=0.0):
        self.clock, self.enabled, self.installed_at = clock, enabled, installed_at

    def _state(self, t):
        for a, b in self.enabled:
            if a <= t < b:
                return a
        return None

    def bold_ready_probe(self, timeout=None):
        t = self.clock.t
        on_since = self._state(t)
        mark = None
        if on_since is not None:
            mark = on_since * 1000 if on_since >= self.installed_at else None
        return {"found": True, "disabled": on_since is None, "enabledAt": mark,
                "now": t * 1000}

    def dismiss_heavy_calc_prompt(self, timeout=None):
        return {"clicked": False}


def _bursty_proc(clock, start, burst_every=2.0, burst_len=0.3):
    """Р7 после загрузки просыпается раз в 2 с — CPU-детектору не набрать 3 с тишины."""
    p = ScriptedProc(clock, busy_until=0)
    p.cpu_percent = lambda interval=None: (
        200.0 if (clock.t - start) % burst_every < burst_len else 0.0)
    return p


def test_ready_marker_is_bold_enable_moment(detector_env, clock, log):
    start = clock.t
    detector_env._get_r7_processes = lambda log_cb=None: [_bursty_proc(clock, start)]
    detector_env._webdriver_connector = _BoldConnector(clock, [(start + 5.0, 1e12)])
    assert detector_env._wait_until_r7_ready(None, timeout=60, log_cb=log) is True
    assert detector_env._ready_marker == "bold"
    assert detector_env._ready_at - start == pytest.approx(5.0, abs=1e-6)


def test_cpu_path_does_not_declare_ready_while_bold_disabled(detector_env, clock, log):
    start = clock.t
    detector_env._get_r7_processes = lambda log_cb=None: [ScriptedProc(clock, busy_until=start + 1)]
    detector_env._webdriver_connector = _BoldConnector(clock, [])   # кнопка всё время недоступна
    assert detector_env._wait_until_r7_ready(None, timeout=10, log_cb=log) is False
    assert detector_env._ready_marker == "timeout"


def test_bold_flicker_before_prompt_not_counted(detector_env, clock, log):
    """Кнопка на миг включилась (0.2 с) и снова выключилась — засчитывается
    только устойчивое включение."""
    start = clock.t
    detector_env._get_r7_processes = lambda log_cb=None: [_bursty_proc(clock, start)]
    detector_env._webdriver_connector = _BoldConnector(
        clock, [(start + 3.0, start + 3.2), (start + 6.0, 1e12)])
    assert detector_env._wait_until_r7_ready(None, timeout=60, log_cb=log) is True
    assert detector_env._ready_at - start == pytest.approx(6.0, abs=1e-6)


def test_late_observer_marks_upper_bound(detector_env, clock, log):
    start = clock.t
    detector_env._get_r7_processes = lambda log_cb=None: [_bursty_proc(clock, start)]
    detector_env._webdriver_connector = _BoldConnector(clock, [(start - 1.0, 1e12)],
                                                       installed_at=start + 10)
    assert detector_env._wait_until_r7_ready(None, timeout=60, log_cb=log) is True
    assert detector_env._ready_marker == "bold_late"


def test_cpu_fallback_without_cdp(detector_env, clock, log):
    start = clock.t
    detector_env._get_r7_processes = lambda log_cb=None: [ScriptedProc(clock, busy_until=start + 2)]
    detector_env._webdriver_connector = None
    assert detector_env._wait_until_r7_ready(None, timeout=60, log_cb=log) is True
    assert detector_env._ready_marker == "cpu"


def test_bold_probe_js_uses_observer():
    import r7_webdriver_connector as wd
    js = wd._BOLD_READY_PROBE_JS
    assert "MutationObserver" in js and "__r7BoldEnabledAt" in js
    assert "Date.now()" in js


def test_ws_timeout_is_not_disconnect_and_logged_once():
    """WebSocketTimeoutException — таймаут, а не обрыв: соединение живо,
    предупреждение — один раз (проба кнопки при занятом рендерере)."""
    import r7_webdriver_connector as wd
    from unittest.mock import Mock

    class WebSocketTimeoutException(Exception):
        pass

    logs = []
    c = wd.R7WebDriverConnector(port=1, log_cb=logs.append)
    c._backend = "cdp"
    ws = Mock()
    ws.gettimeout.return_value = 2.0
    ws.recv.side_effect = WebSocketTimeoutException("Connection timed out")
    c._ws = ws
    for _ in range(3):
        assert c.evaluate("1", timeout=0.3) is None
    assert c.connected
    assert sum("таймаут" in m for m in logs) == 1


# ── x2t: жизненный цикл, коды завершения, ранний выход экспорта ──────────

class _FakeX2t:
    def __init__(self, pid, cmd):
        self.pid, self._cmd = pid, cmd

    def name(self):
        return "x2t.exe"

    def cmdline(self):
        return self._cmd

    def cpu_times(self):
        return type("T", (), {"user": 1.5, "system": 0.5})()

    def is_running(self):
        return True


def _tracker_env(monkeypatch, tmp_path, exit_codes):
    xml = tmp_path / "params_from.xml"
    xml.write_text("\ufeff<?xml version='1.0'?><TaskQueueDataConvert>"
                   "<m_sFileFrom>C:/in.xlsx</m_sFileFrom><m_sFileTo>C:/out.ods</m_sFileTo>"
                   "<m_nFormatTo>8195</m_nFormatTo></TaskQueueDataConvert>", encoding="utf-8")
    pids = {"cur": {1, 2}}
    monkeypatch.setattr(r7mod.psutil, "pids", lambda: set(pids["cur"]))
    monkeypatch.setattr(r7mod.psutil, "Process",
                        lambda pid: _FakeX2t(pid, ["x2t", str(xml)]))
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    codes = iter(exit_codes)
    monkeypatch.setattr(r7mod.win32api, "OpenProcess", lambda *a: object())
    monkeypatch.setattr(r7mod.win32api, "CloseHandle", lambda h: None)
    monkeypatch.setattr(r7mod.win32process, "GetExitCodeProcess", lambda h: next(codes))
    return pids


def test_x2t_tracker_records_params_and_crash(monkeypatch, tmp_path):
    pids = _tracker_env(monkeypatch, tmp_path, [259, 0xC0000409])
    logs = []
    t = r7mod.X2tTracker(log_cb=logs.append)
    mark = r7mod.time.perf_counter()
    pids["cur"].add(77)
    t._poll()                       # запуск; код 259 = ещё жив
    t._poll()                       # завершился с 0xC0000409
    runs = t.since(mark)
    assert len(runs) == 1
    assert runs[0]["format_to"] == "8195" and runs[0]["file_to"] == "C:/out.ods"
    assert runs[0]["exit_code"] == 0xC0000409
    s = r7mod.X2tTracker.summarize(runs)
    assert s["count"] == 1 and s["failed_codes"] == ["0xc0000409"]
    assert any("x2t упал" in m and "0xc0000409" in m for m in logs)


def test_export_wait_fails_fast_on_x2t_crash(bare_r7, log, tmp_path, monkeypatch):
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    bare_r7._op_started_at = 0.0

    class _T:
        def since(self, mark):
            return [{"start": 1.0, "end": 2.0, "exit_code": 0xC0000409}]
    bare_r7._x2t_tracker = _T()
    bare_r7.ALERT_AFTER_X2T_CRASH_SEC = 0.0      # окна ошибки в этом тесте нет
    t0 = r7mod.time.perf_counter()
    assert bare_r7._wait_for_export_file(str(tmp_path / "none.ods"), timeout=60, log_cb=log) is False
    assert r7mod.time.perf_counter() - t0 < 5          # не ждали 60 с
    assert "0xc0000409" in bare_r7._export_fail_reason


def test_aggregate_x2t_none_without_runs(bare_r7):
    empty = {"count": 0, "sec": 0.0, "cpu_sec": 0.0, "failed_codes": [], "formats": []}
    assert bare_r7._aggregate_x2t([empty, empty], [1], None) is None


def test_aggregate_x2t_median_and_failures(bare_r7, log):
    rows = [{"count": 1, "sec": 9.0, "cpu_sec": 8.0, "failed_codes": [], "formats": ["513"]},
            {"count": 1, "sec": 2.0, "cpu_sec": 1.5, "failed_codes": [], "formats": ["513"]},
            {"count": 1, "sec": 3.0, "cpu_sec": 2.5, "failed_codes": ["0xc0000409"], "formats": ["513"]}]
    agg = bare_r7._aggregate_x2t(rows, [1, 2], log)
    assert agg["sec"] == pytest.approx(2.5)
    assert agg["failed_codes"] == ["0xc0000409"]
    assert agg["runs_per_rep"] == [1, 1, 1]


def test_get_r7_processes_no_longer_swallows_x2t_log():
    import inspect
    src = inspect.getsource(r7mod.R7Testovarka._get_r7_processes)
    assert "_x2t_logged_pids.add" not in src


def test_export_wait_does_not_fail_while_retry_runs(bare_r7, log, tmp_path, monkeypatch):
    """Первый x2t упал, но Р7 уже запустил второй — ждём его, не сдаёмся."""
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    bare_r7._op_started_at = 0.0
    out = tmp_path / "x.ods"
    state = {"n": 0}

    class _T:
        def since(self, mark):
            state["n"] += 1
            if state["n"] == 3:
                out.write_bytes(b"PK ods")        # повтор дописал файл
            return [{"start": 1.0, "end": 2.0, "exit_code": 0xC0000409},
                    {"start": 1.5, "end": None, "exit_code": None}]
    bare_r7._x2t_tracker = _T()
    assert bare_r7._wait_for_export_file(str(out), timeout=5, log_cb=log) is True


# ── Окно «Нельзя сохранить…» и прочие информационные окна Р7 ─────────────

class _AlertConnector:
    connected = True

    def __init__(self, texts):
        self.texts = list(texts)

    def dismiss_info_alert(self, timeout=None):
        if self.texts:
            return {"clicked": True, "text": self.texts.pop(0)}
        return {"clicked": False}


def test_dismiss_info_alerts_collects_texts(bare_r7, log):
    bare_r7._webdriver_connector = _AlertConnector(["Нельзя сохранить или создать этот файл."])
    assert bare_r7._dismiss_info_alerts(log) == ["Нельзя сохранить или создать этот файл."]
    assert bare_r7._dismiss_info_alerts(log) == []
    assert any("закрыто кнопкой OK" in m for m in log.messages)


def test_export_crash_reason_includes_alert_and_memory_hint(bare_r7, log, tmp_path, monkeypatch):
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    bare_r7._op_started_at = 0.0

    class _T:
        def since(self, mark):
            return [{"start": 1.0, "end": 2.0, "exit_code": 0xC0000409}]
    bare_r7._x2t_tracker = _T()
    bare_r7._webdriver_connector = _AlertConnector(["Нельзя сохранить или создать этот файл."])
    assert bare_r7._wait_for_export_file(str(tmp_path / "none.ods"), timeout=60, log_cb=log) is False
    reason = bare_r7._export_fail_reason
    assert "0xc0000409" in reason and "X2T_MEMORY_LIMIT" in reason
    assert "Нельзя сохранить" in reason


def test_info_alert_js_only_single_ok_button():
    import inspect
    import r7_webdriver_connector as wd
    src = inspect.getsource(wd.R7WebDriverConnector.dismiss_info_alert)
    assert "btns.length !== 1" in src and "result') !== 'ok'" in src
    assert "getComputedStyle" in src          # не offsetParent: модалка position: fixed


def test_repeat_loop_dismisses_alerts_before_and_after_run():
    import inspect
    src = inspect.getsource(r7mod.R7Testovarka._measure_op_repeated)
    assert src.count("self._dismiss_info_alerts(log_cb)") >= 3
    assert src.index("self._dismiss_info_alerts(log_cb)") < src.index("start = time.perf_counter()")


# ── Снимок упавшего x2t — не конвертер ───────────────────────────────────

class _Proc:
    def __init__(self, threads=4, parent_name="editors.exe"):
        self._t, self._pn = threads, parent_name

    def num_threads(self):
        return self._t

    def parent(self):
        pn = self._pn
        return type("P", (), {"name": lambda self: pn})()


def test_crash_snapshot_detection():
    assert r7mod._is_crash_snapshot(_Proc(threads=0)) is True          # потоков нет
    assert r7mod._is_crash_snapshot(_Proc(parent_name="x2t.exe")) is True
    assert r7mod._is_crash_snapshot(_Proc()) is False                  # живой конвертер


def test_tracker_skips_crash_snapshot(monkeypatch, tmp_path):
    pids = _tracker_env(monkeypatch, tmp_path, [259])
    snap = _FakeX2t(88, ["x2t", "x.xml"])
    snap.num_threads = lambda: 0
    monkeypatch.setattr(r7mod.psutil, "Process", lambda pid: snap)
    logs = []
    t = r7mod.X2tTracker(log_cb=logs.append)
    mark = r7mod.time.perf_counter()
    pids["cur"].add(88)
    t._poll()
    assert t.since(mark) == []
    assert any("снимок упавшего x2t" in m for m in logs)


# ── Окно «Выбрать параметры CSV» ─────────────────────────────────────────

def test_csv_options_absent_returns_none(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "WIN32_OK", True)
    monkeypatch.setattr(r7mod, "PYWINAUTO_OK", True)
    monkeypatch.setattr(bare_r7, "_find_window_hwnd", lambda *a, **k: None, raising=False)
    assert bare_r7._confirm_csv_options(log_cb=log, timeout=0.0) is None
    assert any("не появилось" in m for m in log.messages)


def test_csv_options_confirmed_in_both_workers():
    import inspect
    for fn in (r7mod.R7Testovarka._spreadsheet_worker,
               r7mod.R7Testovarka._batch_run_single_version):
        src = inspect.getsource(fn)
        assert 'if ext == "csv":' in src and "_confirm_csv_options(" in src
