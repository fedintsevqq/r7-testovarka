"""Анализ точности замеров 30.09.2026 (measure_schema 7).

Живая трассировка на Р7 2026.3.2 показала, что цифры искажали не сами
замеры, а условия вокруг них: опрос CPU принимал фоновую сборку мусора за
продолжение операции, тесты зависели от листа, оставленного соседями,
повтор вставки шёл на «грязный» лист, экспорт включал работу инструмента.
"""
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
from r7 import config as r7config  # noqa: E402


class FakeClock:
    def __init__(self, t0=100.0):
        self.t = t0

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += s


class PingConnector:
    """Коннектор, у которого ping занимает заданное время (по фейковым часам)."""

    connected = True

    def __init__(self, clock, durations, default=0.002):
        self.clock, self.durations, self.default = clock, list(durations), default
        self.pings = 0

    def ping(self, timeout=None):
        self.pings += 1
        self.clock.t += self.durations.pop(0) if self.durations else self.default
        return True


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(r7mod.time, "perf_counter", c.perf_counter)
    monkeypatch.setattr(r7mod.time, "sleep", c.sleep)
    return c


@pytest.fixture
def r7(bare_r7):
    bare_r7._op_max_wait = None
    bare_r7._op_start_grace = None
    bare_r7._dismiss_heavy_calc_prompt = lambda log_cb=None: False
    bare_r7._last_prompt_wait_sec = None
    return bare_r7


# ── конец операции по ответу редактора ───────────────────────────────────

def test_renderer_idle_no_tail_ends_at_call_return(r7, clock, log):
    r7._webdriver_connector = PingConnector(clock, [])
    start = clock.t
    end, status = r7._wait_renderer_idle(log)
    assert status == "ok" and end == start


def test_renderer_idle_counts_busy_until_slow_ping_returns(r7, clock, log):
    """После копирования редактор занят ещё ~0.12 с — это часть операции."""
    r7._webdriver_connector = PingConnector(clock, [0.117])
    start = clock.t
    end, status = r7._wait_renderer_idle(log)
    assert status == "ok" and end - start == pytest.approx(0.117)


def test_renderer_idle_catches_deferred_task(r7, clock, log):
    """Отложенная работа стартует сразу после вызова: первый пинг 3 мс,
    второй — 2.7 с (так ведёт себя автосохранение)."""
    r7._webdriver_connector = PingConnector(clock, [0.003, 2.7])
    start = clock.t
    end, _status = r7._wait_renderer_idle(log)
    assert end - start == pytest.approx(0.003 + r7.OP_PING_GAP_SEC + 2.7)


def test_renderer_idle_none_without_connector(r7, log):
    r7._webdriver_connector = None
    assert r7._wait_renderer_idle(log) is None


def test_renderer_idle_timeout(r7, clock, log):
    r7._op_max_wait = 5
    r7._webdriver_connector = PingConnector(clock, [3.0, 3.0])
    end, status = r7._wait_renderer_idle(log)
    assert (end, status) == (None, "timeout")


def test_renderer_idle_modal_wait_is_subtracted(r7, clock, log):
    """Модалка пересчёта после операции: ожидание ответа — не работа Р7."""
    answers = iter([True, False])

    def dismiss(log_cb=None):
        clicked = next(answers)
        r7._last_prompt_wait_sec = 0.25 if clicked else None
        return clicked

    r7._dismiss_heavy_calc_prompt = dismiss
    r7._paced_total = 0.0
    r7._webdriver_connector = PingConnector(clock, [0.002] * 7 + [1.5])
    start = clock.t
    end, status = r7._wait_renderer_idle(log)
    assert status == "ok"
    assert r7._paced_total == pytest.approx(0.25)
    assert end > start + 1.5          # конец — после пересчёта


def test_detector_uses_ping_on_cdp_path(r7, clock, log):
    r7._op_via_cdp = True
    r7._webdriver_connector = PingConnector(clock, [])
    start = clock.t
    assert r7._wait_operation_done(None, log_cb=log) == (start, "ok")


def test_detector_keeps_cpu_path_for_explicit_grace(r7, clock, log, monkeypatch):
    """Экспорт просит своё окно старта — пинг не используется."""
    r7._op_via_cdp = True
    r7._op_start_grace = 0.2
    conn = PingConnector(clock, [])
    r7._webdriver_connector = conn
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    monkeypatch.setattr(r7mod.env, "WIN32_OK", False)
    _end, status = r7._wait_operation_done(None, log_cb=log)
    assert status == "below_floor" and conn.pings == 0


def test_settle_waits_for_fast_ping(r7, clock):
    conn = PingConnector(clock, [0.12, 0.02])
    r7._webdriver_connector = conn
    assert r7._cdp_settle() is True
    assert conn.pings == 3


# ── подготовка «Вставки большого массива» ────────────────────────────────

def _paste_env(r7, monkeypatch, seq_now, state):
    conn = Mock()
    conn.connected = True
    conn.ping.return_value = True
    conn.copy.return_value = {"ok": True}
    conn.document_state.return_value = state
    conn.add_sheet.return_value = {"ok": True, "before": {"historyIndex": 4},
                                   "after": {"active": 3, "historyIndex": 5}}
    r7._webdriver_connector = conn
    r7.add_test_log = Mock()
    monkeypatch.setattr(r7, "_clipboard_seq", lambda: seq_now)
    monkeypatch.setattr(r7, "_prepare_on_work_sheet",
                        lambda ref=None, log_cb=None: {"name": "1"})
    monkeypatch.setattr(r7, "_prepare_select_all_on_work_sheet",
                        lambda log_cb=None: {"name": "1"})
    return conn


def test_paste_prepare_copies_sheet_when_clipboard_changed(r7, monkeypatch):
    """Тест копирования снят или буфер занят другим — копия делается вне замера."""
    conn = _paste_env(r7, monkeypatch, seq_now=77, state={"active": 1, "historyIndex": 4})
    r7._sheet_clip_seq = 70
    r7._paste_big_prepare()
    conn.copy.assert_called_once()
    assert r7._sheet_clip_seq == 77 and r7._paste_sheet_prepared is True


def test_paste_prepare_keeps_clipboard_when_unchanged(r7, monkeypatch):
    conn = _paste_env(r7, monkeypatch, seq_now=77, state={"active": 1, "historyIndex": 4})
    r7._sheet_clip_seq = 77
    r7._paste_big_prepare()
    conn.copy.assert_not_called()
    conn.add_sheet.assert_called_once()
    assert r7._paste_sheet_mark == (3, 5) and r7._paste_sheet_base == 4


def test_paste_prepare_removes_stale_sheet_before_new_one(r7, monkeypatch):
    """Повтор: вставка откатана, лист пуст, но «грязный» (вставка в него
    22.2 с против 20.3 с) — откатываем и его создание, берём новый лист."""
    conn = _paste_env(r7, monkeypatch, seq_now=77, state={"active": 3, "historyIndex": 5})
    r7._sheet_clip_seq = 77
    r7._paste_sheet_mark, r7._paste_sheet_base = (3, 5), 4
    r7._paste_big_prepare()
    assert conn.undo_to.call_args.args[0] == 4
    conn.add_sheet.assert_called_once()


def test_paste_cleanup_removes_our_sheet(r7, monkeypatch):
    """После всех повторов лист вставки убирается — иначе он утяжелял
    документ для следующих тестов (XLTX 11.5 с против 5.5 с, 07.10.2026)."""
    conn = _paste_env(r7, monkeypatch, seq_now=77, state={"active": 3, "historyIndex": 5})
    r7._paste_sheet_mark, r7._paste_sheet_base = (3, 5), 4
    r7._paste_big_cleanup()
    assert conn.undo_to.call_args.args[0] == 4
    assert r7._paste_sheet_mark is None and r7._paste_sheet_prepared is False


def test_paste_cleanup_keeps_foreign_document_state(r7, monkeypatch):
    """Документ не тот, что оставила подготовка (откат не удался) — не
    откатываем вслепую: undo_to снял бы чужие правки."""
    conn = _paste_env(r7, monkeypatch, seq_now=77, state={"active": 3, "historyIndex": 6})
    r7._paste_sheet_mark, r7._paste_sheet_base = (3, 5), 4
    r7._paste_big_cleanup()
    conn.undo_to.assert_not_called()
    assert "не убран" in r7.add_test_log.call_args.args[0]


def test_paste_cleanup_without_prepared_sheet_does_nothing(r7, monkeypatch):
    conn = _paste_env(r7, monkeypatch, seq_now=77, state={"active": 1, "historyIndex": 4})
    r7._paste_sheet_mark, r7._paste_sheet_base = None, None
    r7._paste_big_cleanup()
    conn.undo_to.assert_not_called()


def test_paste_prepare_without_cdp_leaves_keyboard_path(r7):
    r7._webdriver_connector = None
    r7._paste_big_prepare()
    assert r7._paste_sheet_prepared is False


# ── открытие: диск и статистика ──────────────────────────────────────────

def _open(sec, cpu=2.9):
    return {"x2t": {"count": 1, "sec": sec, "cpu_sec": cpu}}


def test_open_disk_note_when_x2t_wait_varies(bare_r7):
    note, waits = bare_r7._open_disk_wait_note([_open(4.0), _open(8.4), _open(4.1)])
    assert waits == [pytest.approx(1.1), pytest.approx(5.5), pytest.approx(1.2)]
    assert "диск" in note


def test_open_disk_note_absent_when_stable(bare_r7):
    note, _waits = bare_r7._open_disk_wait_note([_open(4.0), _open(4.2), _open(3.9)])
    assert note is None


def test_open_disk_note_needs_two_opens(bare_r7):
    assert bare_r7._open_disk_wait_note([_open(9.0)])[0] is None
    assert bare_r7._open_disk_wait_note([{"x2t": {}}, {"x2t": None}]) == (None, [None, None])


def test_open_stats_keep_first_run(bare_r7):
    """Открытия — независимые холодные старты, прогрева у первого нет."""
    times, statuses = [9.1, 9.0, 13.6, 9.2, 9.1], ["ok"] * 5
    stats, discarded, _ = bare_r7._select_stats_runs(times, statuses, discard_warmup=False)
    assert stats == times and discarded is False
    stats, discarded, _ = bare_r7._select_stats_runs(times, statuses)
    assert stats == times[1:] and discarded is True


def test_default_open_runs_is_odd():
    assert r7mod.R7Testovarka.DEFAULT_OPEN_RUNS % 2 == 1


# ── модалка пересчёта: точное ожидание ───────────────────────────────────

def test_heavy_calc_wait_taken_from_page_clock(bare_r7, log):
    conn = Mock()
    conn.connected = True
    conn.dismiss_heavy_calc_prompt.return_value = {"clicked": True, "text": "…", "waited_ms": 412}
    bare_r7._webdriver_connector = conn
    assert bare_r7._dismiss_heavy_calc_prompt(log) is True
    assert bare_r7._last_prompt_wait_sec == pytest.approx(0.412)


def test_heavy_calc_wait_unknown_falls_back(bare_r7, log):
    conn = Mock()
    conn.connected = True
    conn.dismiss_heavy_calc_prompt.return_value = {"clicked": True, "text": "…", "waited_ms": None}
    bare_r7._webdriver_connector = conn
    assert bare_r7._dismiss_heavy_calc_prompt(log) is True
    assert bare_r7._last_prompt_wait_sec is None


# ── экспорт: секундомер с «Сохранить» ────────────────────────────────────

def test_export_stopwatch_starts_at_save_click(bare_r7, log, monkeypatch, tmp_path):
    """Открытие диалога, выбор типа и ввод пути — работа инструмента (2–3 с),
    в замер экспорта они не входят."""
    r7 = bare_r7
    r7._op_started_at = 1000.0
    r7._paced_total = 0.4
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(r7, "_try_cdp_saveas", lambda hwnd, log_cb=None: True)
    monkeypatch.setattr(r7, "_find_window_hwnd", lambda *a, **k: 555)

    def uia(dlg, ext, path, log_cb=None):
        r7._export_go_at = 1003.2
        return True

    monkeypatch.setattr(r7, "_uia_select_saveas_type", uia)
    monkeypatch.setattr(r7, "_dismiss_saveas_format_warning", lambda *a, **k: False)
    monkeypatch.setattr(r7, "_wait_for_export_file", lambda p, log_cb=None: True)
    monkeypatch.setattr(r7, "_check_export_format", lambda path, ext: (True, ""))
    r7._save_as_format("pdf", lambda: 1, Mock(), Mock(), log_cb=log)
    assert r7._paced_total == pytest.approx(3.2)


def test_schema_version():
    assert r7config.MEASURE_SCHEMA_VERSION == 9


# ── окна Р7 ищутся только среди окон процессов Р7 ─────────────────────────

def test_find_window_ignores_foreign_window_with_same_title(bare_r7, monkeypatch):
    """Полный прогон 30.09.2026: окно-предупреждение искалось по «р7-офис» в
    заголовке и нашлась вкладка Chrome «Техническая поддержка Р7-Офис»."""
    import sys
    import types
    titles = {10: "Техническая поддержка Р7-Офис - Google Chrome", 20: "Р7-Офис"}
    owners = {10: 111, 20: 222}
    fake_gui = types.SimpleNamespace(
        IsWindowVisible=lambda h: True,
        GetWindowText=lambda h: titles[h],
        EnumWindows=lambda cb, extra: [cb(h, extra) for h in titles])
    fake_proc = types.SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, owners[h]))
    monkeypatch.setattr(r7mod.env, "WIN32_OK", True)
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_proc)
    assert bare_r7._find_window_hwnd("р7-офис") == 10                      # как было
    assert bare_r7._find_window_hwnd("р7-офис", owner_pids={222}) == 20
    assert bare_r7._find_window_hwnd("р7-офис", owner_pids={333}) is None


def _fake_windows(monkeypatch, titles, owners, names, visible=None):
    import sys
    import types
    visible = visible or {}
    fake_gui = types.SimpleNamespace(
        IsWindowVisible=lambda h: visible.get(h, True),
        GetWindowText=lambda h: titles[h],
        EnumWindows=lambda cb, extra: [cb(h, extra) for h in titles])
    fake_proc = types.SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, owners[h]))
    monkeypatch.setattr(r7mod.env, "WIN32_OK", True)
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", True)
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_proc)
    monkeypatch.setattr(r7mod.psutil, "Process",
                        lambda pid: types.SimpleNamespace(name=lambda: names[pid]))


@pytest.fixture
def real_window_check(bare_r7):
    """bare_r7 с настоящей проверкой владельца окна (conftest её отключает)."""
    del bare_r7._is_r7_window
    return bare_r7


def test_r7_window_lookup_skips_browser_tab(real_window_check, monkeypatch):
    """30.09.2026: тест своего файла нашёл вкладку Chrome «Техническая
    поддержка Р7-Офис» и закрыл браузер вместо Р7."""
    _fake_windows(monkeypatch,
                  titles={10: "Техническая поддержка Р7-Офис - Google Chrome",
                          20: "book.xlsx - Р7-Офис. Профессиональный"},
                  owners={10: 111, 20: 222},
                  names={111: "chrome.exe", 222: "editors.exe"})
    r7 = real_window_check
    assert r7._is_r7_window(10) is False and r7._is_r7_window(20) is True
    assert r7._find_r7_window("book") == 20
    assert r7._find_r7_window() == 20


def test_r7_window_lookup_none_when_only_foreign(real_window_check, monkeypatch):
    _fake_windows(monkeypatch, titles={10: "Р7-Офис — форум - Google Chrome"},
                  owners={10: 111}, names={111: "chrome.exe"})
    assert real_window_check._find_r7_window("book") is None


def test_r7_window_lookup_prefers_document_window(real_window_check, monkeypatch):
    _fake_windows(monkeypatch,
                  titles={5: "Р7-Офис", 20: "book.xlsx - Р7-Офис. Профессиональный"},
                  owners={5: 222, 20: 222}, names={222: "editors.exe"})
    assert real_window_check._find_r7_window("book") == 20


def test_close_never_sends_wm_close_to_foreign_window(real_window_check, monkeypatch, log):
    _fake_windows(monkeypatch, titles={10: "Техническая поддержка Р7-Офис - Google Chrome"},
                  owners={10: 111}, names={111: "chrome.exe"})
    r7 = real_window_check
    killed = []
    monkeypatch.setattr(r7, "_terminate_r7_processes", lambda log_cb=None: killed.append(1))
    assert r7._close_r7_gracefully(10, log_cb=log) is False
    assert killed == [1]                       # Р7 завершён напрямую, чужое окно не тронуто
    assert any("не принадлежит" in m for m in log.messages)


def test_foreground_click_refuses_foreign_window(real_window_check, monkeypatch, log):
    _fake_windows(monkeypatch, titles={10: "Р7-Офис - Google Chrome"},
                  owners={10: 111}, names={111: "chrome.exe"})
    clicks = []
    monkeypatch.setattr(r7mod.env.pyautogui, "click", lambda *a, **k: clicks.append(a), raising=False)
    assert real_window_check._ensure_foreground_click(10, log_cb=log) is False
    assert clicks == []


# ── свободное место на дисках, куда пишет Р7 ─────────────────────────────

def test_low_disk_space_warning(bare_r7, log, monkeypatch):
    """На стенде с 1.4 ГБ свободных на C: экспорт в XLTX упал в конвертере."""
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    monkeypatch.setattr(bare_r7, "_work_disks_free_gb", lambda: {"C:": 1.4, "E:": 900.0})
    env = bare_r7._capture_environment(log_cb=log)
    assert env["disk_free_gb"] == {"C:": 1.4, "E:": 900.0}
    low = [w for w in env["warnings"] if "свободно" in w]
    assert len(low) == 1 and "C:" in low[0]


def test_work_disks_free_gb_one_value_per_drive(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    free = r7mod.R7Testovarka._work_disks_free_gb()
    assert len(free) == 1 and all(v >= 0 for v in free.values())


# ── дампы упавшего x2t ───────────────────────────────────────────────────

class _Tracker:
    def __init__(self, runs):
        import threading
        self._lock = threading.Lock()
        self.runs = runs


def _dumps_env(bare_r7, tmp_path, monkeypatch, runs):
    monkeypatch.setattr(r7mod.R7Testovarka, "_crash_dump_dir", staticmethod(lambda: tmp_path))
    bare_r7._x2t_tracker = _Tracker(runs)
    bare_r7.add_test_log = Mock()
    for name in ("x2t.exe.101.dmp", "x2t.exe.202.dmp", "x2t.exe.303.dmp", "editors.exe.101.dmp"):
        (tmp_path / name).write_bytes(b"x" * 1024)


def test_crash_dumps_removed_only_for_crashed_x2t(bare_r7, tmp_path, monkeypatch, log):
    """За день прогонов с ODS в CrashDumps набралось 10 дампов x2t, 2.6 ГБ."""
    _dumps_env(bare_r7, tmp_path, monkeypatch, [
        {"pid": 101, "exit_code": 0xC0000409},    # упал — дамп наш
        {"pid": 202, "exit_code": 0},             # отработал — дампа не было
        {"pid": 404, "exit_code": None}])         # ещё идёт
    assert bare_r7._cleanup_x2t_crash_dumps(log_cb=log) == 1
    left = sorted(p.name for p in tmp_path.iterdir())
    # Чужие дампы (не упавшие у нас PID, другие программы) не тронуты.
    assert left == ["editors.exe.101.dmp", "x2t.exe.202.dmp", "x2t.exe.303.dmp"]
    assert any("дампы" in m for m in log.messages)


def test_crash_dumps_second_call_is_quiet(bare_r7, tmp_path, monkeypatch, log):
    _dumps_env(bare_r7, tmp_path, monkeypatch, [{"pid": 101, "exit_code": 0x50}])
    assert bare_r7._cleanup_x2t_crash_dumps(log_cb=log) == 1
    assert bare_r7._cleanup_x2t_crash_dumps(log_cb=log) == 0
    assert len([m for m in log.messages if "дампы" in m]) == 1


def test_crash_dumps_without_tracker(bare_r7, log):
    bare_r7._x2t_tracker = None
    assert bare_r7._cleanup_x2t_crash_dumps(log_cb=log) == 0


def test_temp_cleanup_also_removes_dumps(bare_r7, tmp_path, monkeypatch, log):
    """Очистка временных файлов экспорта уже стоит во всех местах, где нужно
    (в том числе после закрытия Р7), — дампы чистятся вместе с ней."""
    called = []
    monkeypatch.setattr(bare_r7, "_cleanup_x2t_crash_dumps",
                        lambda log_cb=None: called.append(1) or 0)
    monkeypatch.setenv("TEMP", str(tmp_path))
    bare_r7._cleanup_x2t_temp_pdfs(log_cb=log)
    assert called == [1]
