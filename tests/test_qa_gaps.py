"""Тесты по QA-отчёту о пробелах покрытия (Reports/qa/COVERAGE-GAP-REPORT.md,
29.09.2026). Номера G-NN — пункты реестра пробелов из отчёта.

Живой Р7 не нужен: psutil, win32, subprocess и коннектор мокаются.
"""
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod


# ── Фейковые процессы для psutil.process_iter ────────────────────────────

class _Proc:
    def __init__(self, pid, name):
        self.pid = pid
        self._name = name
        self.info = {"pid": pid, "name": name}
        self.terminated = False
        self.killed = False

    def name(self):
        return self._name

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_procs(monkeypatch):
    """Подменяет psutil так, чтобы _get_r7_processes и _terminate_r7_processes
    работали на заданном списке процессов."""
    procs = []
    monkeypatch.setattr(r7mod, "PSUTIL_OK", True)
    monkeypatch.setattr(r7mod.psutil, "process_iter", lambda attrs=None: list(procs))
    monkeypatch.setattr(r7mod.psutil, "wait_procs", lambda ps, timeout=None: (ps, []))
    monkeypatch.setattr(r7mod.psutil, "Process",
                        lambda pid: next((p for p in procs if p.pid == pid), _Proc(pid, "python.exe")))
    return procs


# ── G-03: _kill_r7_processes_for_test находит главный процесс editors.exe ──

def test_kill_for_test_terminates_main_editors_process(bare_r7, fake_procs):
    fake_procs.extend([
        _Proc(101, "editors.exe"),             # главный — перезапускает рендереры
        _Proc(102, "editors_helper.exe"),
        _Proc(103, "x2t.exe"),
        _Proc(201, "R7-Testovarka.exe"),       # сам инструмент — не трогать
        _Proc(202, "R7Manager.exe"),
        _Proc(203, "chrome.exe"),
    ])
    assert bare_r7._kill_r7_processes_for_test() == 3
    by_name = {p.name(): p for p in fake_procs}
    assert by_name["editors.exe"].terminated
    assert by_name["editors_helper.exe"].terminated
    assert by_name["x2t.exe"].terminated
    assert not by_name["R7-Testovarka.exe"].terminated
    assert not by_name["R7Manager.exe"].terminated
    assert not by_name["chrome.exe"].terminated


def test_kill_for_test_without_r7_returns_zero(bare_r7, fake_procs):
    fake_procs.append(_Proc(203, "chrome.exe"))
    assert bare_r7._kill_r7_processes_for_test() == 0


# ── G-06: установка и удаление версий ────────────────────────────────────

GUID = "{0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9}"


@pytest.mark.parametrize("uninstall, expected", [
    (f"MsiExec.exe /I{GUID}", f"MsiExec.exe /X{GUID} /quiet /norestart"),
    (f"msiexec /i{GUID.lower()}", f"msiexec /X{GUID.lower()} /quiet /norestart"),
    (f"MsiExec.exe /X{GUID}", f"MsiExec.exe /X{GUID} /quiet /norestart"),
    (r'"C:\Program Files\R7\unins000.exe"', r'"C:\Program Files\R7\unins000.exe" /quiet /norestart'),
])
def test_build_uninstall_command_turns_repair_into_removal(bare_r7, uninstall, expected):
    """/I{GUID} с /quiet — тихий РЕМОНТ, а не удаление: его надо менять на /X."""
    assert bare_r7._build_uninstall_command({"uninstall_string": uninstall}) == expected


def test_build_uninstall_command_prefers_quiet_string(bare_r7):
    info = {"uninstall_string": f"MsiExec.exe /I{GUID}",
            "quiet_uninstall_string": f"MsiExec.exe /X{GUID} /qn"}
    assert bare_r7._build_uninstall_command(info) == f"MsiExec.exe /X{GUID} /qn"


class _FakeProc:
    def __init__(self, returncode=0, timeout=False):
        self.returncode = returncode
        self._timeout = timeout
        self.killed = False

    def wait(self, timeout=None):
        if self._timeout:
            raise r7mod.subprocess.TimeoutExpired("x", timeout)
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.fixture
def installer_env(bare_r7, monkeypatch):
    env = {"proc": _FakeProc(), "rmtree": [], "popen": []}
    bare_r7.status_var = Mock()
    bare_r7.current_version_info = {"uninstall_string": f"MsiExec.exe /I{GUID}"}
    bare_r7.detect_current_version = Mock()

    def popen(cmd, shell=False):
        env["popen"].append((cmd, shell))
        return env["proc"]
    monkeypatch.setattr(r7mod.subprocess, "Popen", popen)
    monkeypatch.setattr(r7mod.shutil, "rmtree", lambda p, ignore_errors=False: env["rmtree"].append(p))
    monkeypatch.setattr(r7mod.os.path, "exists", lambda p: True)
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    return env


@pytest.mark.parametrize("code, ok", [(0, True), (3010, True), (1, False), (1603, False)])
def test_uninstall_removes_program_dirs_only_on_success(bare_r7, installer_env, code, ok):
    installer_env["proc"].returncode = code
    assert bare_r7.uninstall_current_version() is ok
    assert bool(installer_env["rmtree"]) is ok          # при ошибке — ни одного rmtree
    cmd, shell = installer_env["popen"][0]
    assert "/X" in cmd and shell is False


def test_uninstall_timeout_kills_and_keeps_dirs(bare_r7, installer_env):
    installer_env["proc"] = _FakeProc(timeout=True)
    assert bare_r7.uninstall_current_version() is False
    assert installer_env["proc"].killed
    assert installer_env["rmtree"] == []


def test_uninstall_without_installed_version_is_noop(bare_r7, installer_env):
    bare_r7.current_version_info = None
    assert bare_r7.uninstall_current_version() is True
    assert installer_env["popen"] == []


@pytest.mark.parametrize("code, ok", [(0, True), (3010, True), (1603, False)])
def test_install_msi_success_codes(bare_r7, installer_env, tmp_path, code, ok):
    installer_env["proc"].returncode = code
    dist = tmp_path / "r7-office_2026.msi"
    assert bare_r7.install_version(dist) is ok
    cmd, shell = installer_env["popen"][0]
    assert cmd == ["msiexec", "/i", str(dist), "/norestart", "/quiet"] and shell is False
    assert bare_r7.detect_current_version.called is ok


def test_install_timeout_kills_installer(bare_r7, installer_env, tmp_path):
    installer_env["proc"] = _FakeProc(timeout=True)
    assert bare_r7.install_version(tmp_path / "r7.exe", quiet=False) is False
    assert installer_env["proc"].killed


# ── G-14: _load_test_selection не роняет запуск на битом файле ───────────

@pytest.mark.parametrize("content, expected", [
    ('{"Ctrl+A": true, "Ctrl+C": false}',                       # старый формат
     {"Ctrl+A": {"enabled": True, "runs": r7mod.DEFAULT_TEST_RUNS},
      "Ctrl+C": {"enabled": False, "runs": r7mod.DEFAULT_TEST_RUNS}}),
    ('{"Ctrl+A": {"enabled": false, "runs": 3}}',                # новый формат
     {"Ctrl+A": {"enabled": False, "runs": 3}}),
    ('{"Ctrl+A": {"enabled": true, "runs": "5"}}',               # число строкой
     {"Ctrl+A": {"enabled": True, "runs": 5}}),
    ('{"Ctrl+A": {"enabled": true, "runs": "abc"}}',             # мусор — дефолт
     {"Ctrl+A": {"enabled": True, "runs": r7mod.DEFAULT_TEST_RUNS}}),
    ('{"Ctrl+A": {"runs": 0}}',                                  # меньше 1 — 1
     {"Ctrl+A": {"enabled": True, "runs": 1}}),
    ('["не", "словарь"]', {}),
    ('{битый json', {}),
])
def test_load_test_selection_formats(bare_r7, tmp_path, monkeypatch, content, expected):
    monkeypatch.setattr(r7mod, "BASE_DIR", tmp_path)
    (tmp_path / "selected_tests.json").write_text(content, encoding="utf-8")
    assert bare_r7._load_test_selection() == expected


def test_load_test_selection_without_file(bare_r7, tmp_path, monkeypatch):
    monkeypatch.setattr(r7mod, "BASE_DIR", tmp_path)
    assert bare_r7._load_test_selection() == {}


# ── G-16: выбор CDP-порта — одна реализация на всех ─────────────────────

@pytest.mark.parametrize("busy, expected_port", [
    (set(), 8080),
    ({8080}, 8081),
    ({8080, 8081}, 8082),
    ({8080, 8081, 8082}, None),
])
def test_pick_cdp_port_boundaries(monkeypatch, busy, expected_port):
    monkeypatch.setattr(r7mod.R7Testovarka, "_cdp_port_free", staticmethod(lambda p, timeout=0.2: p not in busy))
    picked = r7mod._pick_cdp_port()
    if expected_port is None:
        assert picked is None
    else:
        port, args = picked
        assert port == expected_port
        # Запасным портам нужен явный --remote-debugging-port, основному — нет.
        assert any("remote-debugging-port" in a for a in args) is (port != 8080)


@pytest.mark.parametrize("busy, expected_port", [(set(), 8080), ({8080}, 8081), ({8080, 8081, 8082}, None)])
def test_prepare_webdriver_launch_uses_picked_port(bare_r7, monkeypatch, log, busy, expected_port):
    monkeypatch.setattr(r7mod, "WEBDRIVER_OK", True)
    monkeypatch.setattr(r7mod.R7Testovarka, "_cdp_port_free", staticmethod(lambda p, timeout=0.2: p not in busy))
    args = bare_r7._prepare_webdriver_launch(log_cb=log, filename_hint="f.xlsx")
    assert bare_r7._current_webdriver_port == expected_port
    if expected_port is None:
        assert args == [] and bare_r7._webdriver_connector is None
    else:
        assert bare_r7._webdriver_connector.port == expected_port
        assert bare_r7._webdriver_connector.filename_hint == "f.xlsx"


# ── G-13: _clear_r7_cache удаляет только временные объекты Р7 ────────────

def test_clear_r7_cache_scope(bare_r7, tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path))
    (tmp_path / "r7_tmp_1").mkdir()
    (tmp_path / "R7-cache.bin").write_bytes(b"x")
    (tmp_path / "editors_tmp").mkdir()
    (tmp_path / "chrome_cache").mkdir()          # чужое — не трогать
    (tmp_path / "my_r7_file.txt").write_text("x")  # r7 не в начале имени — не трогать
    assert bare_r7._clear_r7_cache() == 3
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["chrome_cache", "my_r7_file.txt"]


# ── G-13: _purge_os_file_cache — привилегия, вызов ядра, отказы ──────────

ERROR_NOT_ALL_ASSIGNED = 1300
STATUS_PRIVILEGE_NOT_HELD = -0x3FFFFF9F   # 0xC0000061 как знаковый NTSTATUS


@pytest.fixture
def fake_windll(bare_r7, monkeypatch):
    """Подменяет ctypes.WinDLL: advapi32/kernel32/ntdll — Mock'и, по умолчанию
    все вызовы успешны. Настоящий standby-список ОС не трогается."""
    import ctypes

    dlls = {
        "advapi32": Mock(),
        "kernel32": Mock(),
        "ntdll": Mock(),
    }
    dlls["advapi32"].OpenProcessToken.return_value = 1
    dlls["advapi32"].LookupPrivilegeValueW.return_value = 1
    dlls["advapi32"].AdjustTokenPrivileges.return_value = 1
    dlls["kernel32"].GetCurrentProcess.return_value = -1
    dlls["ntdll"].NtSetSystemInformation.return_value = 0
    dlls["opened_with"] = {}

    def windll(name, **kw):
        dlls["opened_with"][name] = kw
        return dlls[name]

    monkeypatch.setattr(ctypes, "WinDLL", windll)
    monkeypatch.setattr(bare_r7, "PURGE_OS_FILE_CACHE", True, raising=False)
    return dlls


def test_purge_os_file_cache_success(bare_r7, fake_windll):
    log = []
    assert bare_r7._purge_os_file_cache(log_cb=log.append) is True

    advapi, kernel, ntdll = (fake_windll[k] for k in ("advapi32", "kernel32", "ntdll"))
    # Включается именно привилегия профилирования, с SE_PRIVILEGE_ENABLED.
    assert advapi.LookupPrivilegeValueW.call_args.args[1] == "SeProfileSingleProcessPrivilege"
    tp = advapi.AdjustTokenPrivileges.call_args.args[2]._obj
    assert (tp.PrivilegeCount, tp.Attributes) == (1, 0x2)
    # SystemMemoryListInformation (80) + MemoryPurgeStandbyList (4).
    info_class, cmd_ref, size = ntdll.NtSetSystemInformation.call_args.args
    assert info_class == 80
    assert cmd_ref._obj.value == 4
    assert size == 4
    kernel.CloseHandle.assert_called_once()
    assert any(m.startswith("🧊") for m in log)
    # Без use_last_error get_last_error() не увидит ERROR_NOT_ALL_ASSIGNED.
    assert fake_windll["opened_with"]["advapi32"].get("use_last_error") is True
    assert fake_windll["opened_with"]["kernel32"].get("use_last_error") is True


def test_purge_os_file_cache_disabled_does_nothing(bare_r7, fake_windll, monkeypatch):
    monkeypatch.setattr(bare_r7, "PURGE_OS_FILE_CACHE", False, raising=False)
    log = []
    assert bare_r7._purge_os_file_cache(log_cb=log.append) is False
    fake_windll["advapi32"].OpenProcessToken.assert_not_called()
    fake_windll["ntdll"].NtSetSystemInformation.assert_not_called()
    assert log == []


def test_purge_os_file_cache_skipped_outside_windows(bare_r7, fake_windll, monkeypatch):
    from types import SimpleNamespace
    # Только взгляд модуля на os: глобальный os.name сломал бы pathlib в pytest.
    monkeypatch.setattr(r7mod, "os", SimpleNamespace(name="posix"))
    assert bare_r7._purge_os_file_cache(log_cb=lambda m: None) is False
    fake_windll["ntdll"].NtSetSystemInformation.assert_not_called()


def test_purge_os_file_cache_without_admin_privilege(bare_r7, fake_windll):
    """Не админ: AdjustTokenPrivileges «успешен», но ставит
    ERROR_NOT_ALL_ASSIGNED. Ядро не вызывать, токен закрыть, предупредить."""
    import ctypes

    def adjust(*args):
        ctypes.set_last_error(ERROR_NOT_ALL_ASSIGNED)
        return 1

    fake_windll["advapi32"].AdjustTokenPrivileges.side_effect = adjust
    log = []
    assert bare_r7._purge_os_file_cache(log_cb=log.append) is False
    fake_windll["ntdll"].NtSetSystemInformation.assert_not_called()
    fake_windll["kernel32"].CloseHandle.assert_called_once()
    assert any("не сброшен" in m and "AdjustTokenPrivileges" in m for m in log)


def test_purge_os_file_cache_kernel_refuses(bare_r7, fake_windll):
    fake_windll["ntdll"].NtSetSystemInformation.return_value = STATUS_PRIVILEGE_NOT_HELD
    log = []
    assert bare_r7._purge_os_file_cache(log_cb=log.append) is False
    msg = next(m for m in log if "не сброшен" in m)
    assert str(0xC0000061) in msg            # NTSTATUS в логе беззнаковый
    assert not any("🧊" in m for m in log)
    fake_windll["kernel32"].CloseHandle.assert_called_once()


def test_purge_os_file_cache_lookup_privilege_fails(bare_r7, fake_windll):
    fake_windll["advapi32"].LookupPrivilegeValueW.return_value = 0
    log = []
    assert bare_r7._purge_os_file_cache(log_cb=log.append) is False
    fake_windll["kernel32"].CloseHandle.assert_called_once()   # finally закрыл токен
    fake_windll["advapi32"].AdjustTokenPrivileges.assert_not_called()
    fake_windll["ntdll"].NtSetSystemInformation.assert_not_called()
    assert any("LookupPrivilegeValue" in m for m in log)


def test_purge_os_file_cache_token_open_fails(bare_r7, fake_windll):
    fake_windll["advapi32"].OpenProcessToken.return_value = 0
    log = []
    assert bare_r7._purge_os_file_cache(log_cb=log.append) is False
    # Токен не открыт — закрывать нечего, до ядра не дошли.
    fake_windll["kernel32"].CloseHandle.assert_not_called()
    fake_windll["ntdll"].NtSetSystemInformation.assert_not_called()
    assert any("OpenProcessToken" in m for m in log)


# ── G-02: _measure_op_repeated — поведение, а не текст исходника ──────────

class _Clock:
    def __init__(self):
        self.t = 1000.0

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Watch:
    def start(self):
        pass

    def stop(self):
        return {"cpu_sec": 1.0, "cpu_peak_core_pct": 100.0,
                "cpu_avg_core_pct": 50.0, "ram_peak_mb": 500.0}


@pytest.fixture
def op_env(bare_r7, monkeypatch):
    """Сценарий прогонов: у каждого прогона длительность операции и статус
    детектора. func() двигает часы на длительность; детектор возвращает
    конец операции. Все внешние зависимости цикла — заглушки."""
    clock = _Clock()
    monkeypatch.setattr(r7mod.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(r7mod.time, "sleep", clock.sleep)
    monkeypatch.setattr(r7mod, "_disk_snapshot", lambda: None)
    env = {"clock": clock, "plan": [], "calls": 0, "restores": 0,
           "restore_ok": True, "history": [], "raise_on": None, "cdp_ms": None}
    r = bare_r7
    r._cached_cpu_count = 4

    def func():
        k = env["calls"]
        env["calls"] += 1
        if env["raise_on"] == k:
            raise RuntimeError("операция упала")
        dur, _status = env["plan"][k]
        clock.t += dur
        if env["cdp_ms"] is not None:
            r._op_via_cdp = True
            r._cdp_api_ms = env["cdp_ms"][k]

    def wait_done(hwnd, log_cb=None, start_grace=None):
        if start_grace == 0.3:                        # ожидание простоя до секундомера
            return clock.t, "below_floor"
        _dur, status = env["plan"][env["calls"] - 1]
        if status == "timeout":
            clock.t += 180.0
            return None, "timeout"
        return clock.t, status

    r._wait_operation_done = wait_done
    r._op_watch = lambda: _Watch()
    r._dismiss_info_alerts = lambda log_cb=None, max_alerts=3: []
    r._x2t_since = lambda mark: []
    r._get_r7_processes = lambda log_cb=None, fresh=False: []
    r._sample_r7_resources = lambda procs, measure_cpu=True: None
    hist = iter(range(10_000))
    r._history_snapshot = lambda log_cb=None: (
        env["history"].pop(0) if env["history"] else {"index": next(hist)})

    def restore(before, label, hwnd=None, log_cb=None):
        env["restores"] += 1
        return env["restore_ok"]
    r._restore_history = restore
    env["func"] = func
    env["r"] = r
    env["run"] = lambda runs, name="Выделение всех ячеек (Ctrl+A)", stop=None: r._measure_op_repeated(
        name, func, runs, None, lambda m: None, stop, post_delay=lambda: None)
    return env


def test_repeat_loop_three_runs_outlier_does_not_move_median(op_env):
    """Полный прогон 30.09.2026: Ctrl+A 0.84 / 1.88 / 0.84 — медиана по двум
    последним давала 1.36. Медиана трёх выброс игнорирует."""
    op_env["plan"] = [(0.84, "below_floor"), (1.88, "ok"), (0.84, "below_floor")]
    res = op_env["run"](3)
    assert res["first_run_discarded"] is False and res["n_runs"] == 3
    assert res["median"] == pytest.approx(0.84)


def test_repeat_loop_median_discards_warmup(op_env):
    op_env["plan"] = [(9.0, "ok")] + [(d, "ok") for d in (1, 2, 3, 4, 5, 6)]
    res = op_env["run"](7)
    assert res["runs"] == pytest.approx([9, 1, 2, 3, 4, 5, 6])
    assert res["first_run_discarded"] is True and res["n_runs"] == 6
    assert res["time"] == pytest.approx(3.5) == res["median"]
    assert res["mad"] == pytest.approx(1.5)
    assert res["run_statuses"] == ["ok"] * 7
    assert res["error"] is None and res["n_timeouts"] == 0
    assert res["runs_independent"] is True
    assert op_env["restores"] == 6                  # между повторами, не после последнего


@pytest.mark.parametrize("runs, n_stats, discarded",
                         [(1, 1, False), (2, 2, False), (3, 3, False), (4, 3, True)])
def test_repeat_loop_warmup_boundaries(op_env, runs, n_stats, discarded):
    op_env["plan"] = [(2.0, "ok"), (1.0, "ok"), (1.1, "ok"), (1.2, "ok")][:runs]
    res = op_env["run"](runs)
    assert res["n_runs"] == n_stats and res["first_run_discarded"] is discarded


def test_repeat_loop_excludes_timeouts_from_median(op_env):
    op_env["plan"] = [(1.0, "ok"), (1.0, "ok"), (5.0, "timeout"), (3.0, "ok")]
    res = op_env["run"](4)
    assert res["run_statuses"] == ["ok", "ok", "timeout", "ok"]
    assert res["n_timeouts"] == 1
    # Валидных 3 — прогрев остаётся: медиана по [1, 1, 3], без 185 с таймаута.
    assert res["time"] == pytest.approx(1.0)
    assert res["runs"][2] == pytest.approx(185.0)   # время таймаута хранится, но не в медиане


def test_repeat_loop_error_keeps_completed_runs(op_env):
    op_env["plan"] = [(1.0, "ok"), (2.0, "ok"), (3.0, "ok")]
    op_env["raise_on"] = 2
    res = op_env["run"](7)
    assert op_env["calls"] == 3                     # после ошибки цикл остановлен
    assert res["error"] == "операция упала"
    assert res["runs"] == pytest.approx([1.0, 2.0])


def test_repeat_loop_error_on_first_run_returns_empty(op_env):
    op_env["raise_on"] = 0
    res = op_env["run"](3)
    assert res["runs"] == [] and res["time"] == 0.0 and res["error"] == "операция упала"


def test_repeat_loop_stop_event_between_runs(op_env):
    import threading
    stop = threading.Event()
    op_env["plan"] = [(1.0, "ok")] * 5

    def func_then_stop():
        op_env["func"]()
        if op_env["calls"] == 2:
            stop.set()
    res = op_env["r"]._measure_op_repeated("Выделение всех ячеек (Ctrl+A)", func_then_stop, 5,
                                           None, lambda m: None, stop, post_delay=lambda: None)
    assert op_env["calls"] == 2 and len(res["runs"]) == 2
    assert op_env["restores"] == 1          # после остановки откат не делается


def test_repeat_loop_flags_op_that_did_not_change_document(op_env):
    """Правка, после которой история не сдвинулась, — ошибка, а не цифра."""
    op_env["plan"] = [(0.3, "below_floor")] * 3
    op_env["history"] = [{"index": 5}, {"index": 5}]     # до и после — одинаково
    res = op_env["run"](3, name="Функция ВПР (50K строк)")
    assert "не изменила документ" in res["error"]
    assert res["runs"] == []


def test_repeat_loop_marks_dependent_runs_when_restore_fails(op_env):
    op_env["plan"] = [(1.0, "ok")] * 3
    op_env["restore_ok"] = False
    assert op_env["run"](3)["runs_independent"] is False


def test_repeat_loop_api_ms_and_below_floor(op_env):
    op_env["plan"] = [(0.2, "below_floor"), (0.3, "ok"), (0.2, "below_floor")]
    op_env["cdp_ms"] = [10.0, 20.0, 30.0]
    res = op_env["run"](3)
    assert res["api_ms"] == pytest.approx(20.0)     # среднее по прогонам через CDP
    # На CDP-пути Р7 работал внутри вызова — цифра реальная, «<порога» нет
    # (в отчёте 30.09.2026 пометка висела на 28-секундной вставке).
    assert res["below_floor"] is False
    assert res["cpu_sec"] == pytest.approx(1.0) and res["ram"] == 500.0


def test_repeat_loop_below_floor_marked_on_keyboard_path(op_env):
    op_env["plan"] = [(0.2, "below_floor"), (0.3, "ok"), (0.2, "below_floor")]
    assert op_env["run"](3)["below_floor"] is True


# ── G-05: исключение в воркере больше не оставляет Р7 работать ────────────

def test_emergency_close_tries_graceful_then_terminates(bare_r7, log):
    calls = []
    bare_r7._close_r7_gracefully = lambda h, log_cb=None, timeout=10: calls.append(("close", h))
    bare_r7._terminate_r7_processes = lambda log_cb=None: calls.append(("terminate",)) or True
    assert bare_r7._emergency_close_r7(lambda: 77, log_cb=log) is True
    assert calls == [("close", 77), ("terminate",)]


def test_emergency_close_survives_failing_graceful_close(bare_r7, log):
    def boom(h, log_cb=None, timeout=10):
        raise RuntimeError("CDP оборвался")
    bare_r7._close_r7_gracefully = boom
    bare_r7._terminate_r7_processes = Mock(return_value=True)
    assert bare_r7._emergency_close_r7(lambda: 77, log_cb=log) is True
    bare_r7._terminate_r7_processes.assert_called_once()


def test_emergency_close_without_window_still_terminates(bare_r7, log):
    bare_r7._close_r7_gracefully = Mock()
    bare_r7._terminate_r7_processes = Mock(return_value=True)
    bare_r7._emergency_close_r7(lambda: None, log_cb=log)
    bare_r7._close_r7_gracefully.assert_not_called()
    bare_r7._terminate_r7_processes.assert_called_once()


@pytest.mark.parametrize("worker", ["_spreadsheet_worker", "_batch_run_single_version"])
def test_workers_close_r7_in_finally_when_not_closed(worker):
    """Воркеры — длинные функции с вложенными замыканиями, целиком их не
    вызвать без живого Р7; проверяем контракт: finally зовёт аварийное
    закрытие, если штатное не прошло."""
    import inspect
    src = inspect.getsource(getattr(r7mod.R7Testovarka, worker))
    fin = src[src.rindex("finally:"):]
    assert "if not _r7_closed:" in fin and "self._emergency_close_r7(" in fin
    assert src.count("_r7_closed = True") == 1


# ── G-01: _save_as_format — цепочка открытия «Сохранить как» ─────────────

@pytest.fixture
def saveas_env(bare_r7, monkeypatch, tmp_path):
    """Заглушки всех внешних действий экспорта. dialog_opens — ответы
    _wait_for_window_title на каждую попытку: CDP → хоткей → повтор →
    меню → WM_COMMAND."""
    monkeypatch.setenv("TEMP", str(tmp_path))
    r = bare_r7
    env = {"hotkeys": [], "presses": [], "dialog_opens": [], "clip": [],
           "cdp": False, "wm": True, "uia": True, "export": True}
    monkeypatch.setattr(r7mod.pyperclip, "copy", env["clip"].append)
    r._try_cdp_saveas = lambda hwnd, log_cb=None: env["cdp"]
    r._ensure_foreground_click = lambda hwnd, log_cb=None: True
    r._wait_for_window_title = lambda subs, timeout=3.0: env["dialog_opens"].pop(0)
    r._try_wm_command_saveas = lambda hwnd, log_cb=None: env["wm"]
    r._dump_visible_window_titles = lambda log_cb=None: None
    r._find_window_hwnd = lambda *a, **k: 4242
    r._pace = lambda s: None
    r._uia_select_saveas_type = Mock(side_effect=lambda *a, **k: env["uia"])
    r._dismiss_saveas_format_warning = Mock(return_value=False)
    r._confirm_csv_options = Mock(return_value={"delimiter": "Запятая"})
    r._export_fail_reason = None
    r._wait_for_export_file = Mock(side_effect=lambda p, log_cb=None: env["export"])
    env["call"] = lambda ext="pdf": r._save_as_format(
        ext, lambda: 7, lambda *k: env["hotkeys"].append(k),
        lambda key, n=1, pace=0.0: env["presses"].append((key, n)), log_cb=lambda m: None)
    env["r"] = r
    return env


@pytest.mark.parametrize("cdp, opens, wm, expected_hotkeys", [
    (True,  [],                           True, []),                                    # CDP-клик
    (False, [True],                       True, [("ctrl", "shift", "s")]),              # хоткей
    (False, [False, True],                True, [("ctrl", "shift", "s")] * 2),          # повтор
    (False, [False, False, True],         True, [("ctrl", "shift", "s")] * 2 + [("alt", "f")]),
    (False, [False, False, False, True],  True, [("ctrl", "shift", "s")] * 2 + [("alt", "f")]),  # WM_COMMAND
])
def test_saveas_dialog_open_chain(saveas_env, cdp, opens, wm, expected_hotkeys):
    e = saveas_env
    e["cdp"], e["dialog_opens"], e["wm"] = cdp, list(opens), wm
    e["call"]("pdf")
    hk = [k for k in e["hotkeys"] if k != ("ctrl", "home")]
    assert hk == expected_hotkeys
    e["r"]._uia_select_saveas_type.assert_called_once()
    assert e["r"]._uia_select_saveas_type.call_args.args[1] == "pdf"


@pytest.mark.parametrize("wm", [True, False])
def test_saveas_no_dialog_sends_no_typing(saveas_env, wm):
    """Главный инвариант: диалог не открылся — ни пути в буфер, ни Ctrl+A,
    ни Ctrl+V, ни UIA-ввода. Иначе ввод уходит в окно, что в фокусе."""
    e = saveas_env
    e["dialog_opens"], e["wm"] = [False, False, False, False], wm
    with pytest.raises(RuntimeError, match="^SKIP"):
        e["call"]("ods")
    assert ("ctrl", "a") not in e["hotkeys"] and ("ctrl", "v") not in e["hotkeys"]
    assert e["clip"] == []
    e["r"]._uia_select_saveas_type.assert_not_called()
    # Единственный Enter — внутри навигации по меню Файл, до проверки диалога.
    assert [p for p in e["presses"] if p[0] == "enter"] == [("enter", 1)]


def test_saveas_uia_failure_is_error(saveas_env):
    e = saveas_env
    e["cdp"], e["uia"] = True, False
    with pytest.raises(RuntimeError, match="UI Automation"):
        e["call"]("xltx")
    e["r"]._wait_for_export_file.assert_not_called()


@pytest.mark.parametrize("ext, csv_confirm", [("csv", True), ("pdf", False), ("ods", False)])
def test_saveas_confirms_csv_options_only_for_csv(saveas_env, ext, csv_confirm):
    e = saveas_env
    e["cdp"] = True
    e["call"](ext)
    assert e["r"]._confirm_csv_options.called is csv_confirm


def test_saveas_export_failure_carries_reason(saveas_env):
    e = saveas_env
    e["cdp"], e["export"] = True, False
    e["r"]._export_fail_reason = "конвертер x2t упал с кодом 0xc0000409"
    with pytest.raises(RuntimeError, match="0xc0000409"):
        e["call"]("ods")


def test_saveas_target_path_in_temp_with_extension(saveas_env, tmp_path):
    e = saveas_env
    e["cdp"] = True
    e["call"]("ods")
    target = e["r"]._uia_select_saveas_type.call_args.args[2]
    assert target.startswith(str(tmp_path)) and target.endswith(".ods")
    assert e["r"]._op_start_grace == e["r"].OP_PDF_GRACE_SEC


# ── G-08: _uia_select_saveas_type — выбор типа, путь, «Сохранить» ────────

def test_escape_send_keys_specials():
    assert r7mod._escape_send_keys(r"C:\Users\VLADIM~1\a+b(1)%.ods") == \
        r"C:\Users\VLADIM{~}1\a{+}b{(}1{)}{%}.ods"
    assert r7mod._escape_send_keys("plain.csv") == "plain.csv"


class _Item:
    def __init__(self, name):
        self.element_info = type("EI", (), {"name": name})()
        self.clicked = False

    def click_input(self):
        self.clicked = True


class _Ctl:
    def __init__(self):
        self.calls = []

    def expand(self):
        self.calls.append("expand")

    def collapse(self):
        self.calls.append("collapse")

    def click_input(self):
        self.calls.append("click")

    def type_keys(self, keys, **kw):
        self.calls.append(("type", keys))


@pytest.fixture
def uia_env(bare_r7, monkeypatch):
    items = [_Item("Книга Excel (*.xlsx)"), _Item("Шаблон ODS (*.ots)"),
             _Item("Таблица ODF (*.ods)"), _Item("PDF (*.pdf)"),
             _Item("PDF/A (*.pdf)"), _Item("CSV (*.csv)")]
    ctls = {"FileTypeControlHost": _Ctl(), "1001": _Ctl(), "1": _Ctl()}

    class _Dlg:
        def child_window(self, auto_id=None, control_type=None):
            return ctls[auto_id]

        def descendants(self, control_type=None):
            return items

    class _App:
        def __init__(self, backend=None):
            pass

        def connect(self, handle=None):
            return self

        def window(self, handle=None):
            return _Dlg()

    monkeypatch.setattr(r7mod, "PYWINAUTO_OK", True)
    monkeypatch.setattr(r7mod, "_UiaApplication", _App)
    bare_r7._pace = lambda s: None
    return {"items": items, "ctls": ctls, "r": bare_r7}


@pytest.mark.parametrize("ext, picked", [("ods", "Таблица ODF (*.ods)"),   # не «(*.ots)»
                                          ("pdf", "PDF (*.pdf)"),           # первый, не PDF/A
                                          ("csv", "CSV (*.csv)")])
def test_uia_picks_exact_type_and_saves(uia_env, log, ext, picked):
    target = r"C:\Users\VLADIM~1\Temp\temp_export_x2t_1." + ext
    assert uia_env["r"]._uia_select_saveas_type(1, ext, target, log_cb=log) is True
    chosen = [i.element_info.name for i in uia_env["items"] if i.clicked]
    assert chosen == [picked]
    typed = [c for c in uia_env["ctls"]["1001"].calls if isinstance(c, tuple)]
    assert typed == [("type", "^a"), ("type", r7mod._escape_send_keys(target))]
    assert "{~}" in typed[1][1]                               # «~» не превратится в Enter
    assert uia_env["ctls"]["1"].calls == ["click"]            # «Сохранить» (auto_id=1)


def test_uia_missing_type_collapses_and_fails(uia_env, log):
    assert uia_env["r"]._uia_select_saveas_type(1, "xltx", "x.xltx", log_cb=log) is False
    assert uia_env["ctls"]["FileTypeControlHost"].calls == ["expand", "collapse"]
    assert uia_env["ctls"]["1"].calls == []                   # «Сохранить» не жали


def test_uia_unavailable(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "PYWINAUTO_OK", False)
    assert bare_r7._uia_select_saveas_type(1, "ods", "x.ods", log_cb=log) is False


def test_uia_exception_is_reported_not_raised(uia_env, log):
    def boom(*a, **k):
        raise RuntimeError("элемент не найден")
    uia_env["ctls"]["FileTypeControlHost"].expand = boom
    assert uia_env["r"]._uia_select_saveas_type(1, "ods", "x.ods", log_cb=log) is False
    assert any("UIA-сохранение не удалось" in m for m in log.messages)


# ── G-09: датчики детекторов — поиск процессов Р7 и ветки x2t ─────────────

class _PsProc(_Proc):
    def __init__(self, pid, name, deny=False):
        super().__init__(pid, name)
        self._deny = deny

    def name(self):
        if self._deny:
            raise r7mod.psutil.AccessDenied(self.pid)
        return self._name


@pytest.fixture
def ps_env(bare_r7, monkeypatch):
    procs = {}
    scans = []
    monkeypatch.setattr(r7mod, "PSUTIL_OK", True)

    def process_iter(attrs=None):
        scans.append(1)
        return list(procs.values())

    def process(pid):
        if pid not in procs:
            if pid in (r7mod.os.getpid(),):
                return _Proc(pid, "python.exe")
            raise r7mod.psutil.NoSuchProcess(pid)
        return procs[pid]
    monkeypatch.setattr(r7mod.psutil, "process_iter", process_iter)
    monkeypatch.setattr(r7mod.psutil, "Process", process)
    bare_r7._r7_pids = None
    return {"procs": procs, "scans": scans, "r": bare_r7}


def test_get_r7_processes_full_scan_matches_names(ps_env):
    for pid, name in [(1, "editors.exe"), (2, "editors_helper.exe"), (3, "x2t.exe"),
                      (4, "R7-Testovarka.exe"), (5, "chrome.exe")]:
        ps_env["procs"][pid] = _PsProc(pid, name)
    found = sorted(p.pid for p in ps_env["r"]._get_r7_processes(log_cb=lambda m: None))
    assert found == [1, 2, 3]


def test_get_r7_processes_cache_misses_new_x2t_unless_fresh(ps_env):
    """Быстрый путь по кэшу PID не видит x2t, запущенный после заполнения
    кэша, — поэтому детекторы и наблюдатель ресурсов зовут fresh=True."""
    r = ps_env["r"]
    ps_env["procs"][1] = _PsProc(1, "editors.exe")
    assert [p.pid for p in r._get_r7_processes(log_cb=lambda m: None)] == [1]
    ps_env["procs"][9] = _PsProc(9, "x2t.exe")                  # конвертер стартовал
    assert [p.pid for p in r._get_r7_processes(log_cb=lambda m: None)] == [1]   # кэш
    fresh = sorted(p.pid for p in r._get_r7_processes(log_cb=lambda m: None, fresh=True))
    assert fresh == [1, 9]


def test_get_r7_processes_cache_rescans_when_all_cached_dead(ps_env):
    r = ps_env["r"]
    ps_env["procs"][1] = _PsProc(1, "editors.exe")
    r._get_r7_processes(log_cb=lambda m: None)
    del ps_env["procs"][1]                                      # Р7 перезапущен
    ps_env["procs"][2] = _PsProc(2, "editors.exe")
    assert [p.pid for p in r._get_r7_processes(log_cb=lambda m: None)] == [2]


def test_get_r7_processes_without_psutil(bare_r7, monkeypatch):
    monkeypatch.setattr(r7mod, "PSUTIL_OK", False)
    assert bare_r7._get_r7_processes(log_cb=lambda m: None) == []


class _DetClock:
    def __init__(self):
        self.t = 100.0

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _LiveProc:
    """Процесс Р7 для детекторов: CPU 0, x2t живёт до dies_at."""
    def __init__(self, clock, pid, name, dies_at=None):
        self.clock, self.pid, self._name, self.dies_at = clock, pid, name, dies_at

    def name(self):
        return self._name

    def cpu_percent(self, interval=None):
        if self.dies_at is not None and self.clock.t >= self.dies_at:
            raise r7mod.psutil.NoSuchProcess(self.pid)
        return 0.0

    def is_running(self):
        return self.dies_at is None or self.clock.t < self.dies_at

    def num_threads(self):
        return 4

    def parent(self):
        return None


@pytest.fixture
def det_env(bare_r7, monkeypatch):
    clock = _DetClock()
    monkeypatch.setattr(r7mod.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(r7mod.time, "sleep", clock.sleep)
    monkeypatch.setattr(r7mod, "PSUTIL_OK", True)
    monkeypatch.setattr(r7mod, "WIN32_OK", False)
    bare_r7._op_start_grace = None
    bare_r7._op_max_wait = None
    bare_r7._ready_at = None
    bare_r7._webdriver_connector = None
    return {"clock": clock, "r": bare_r7}


def test_operation_busy_while_x2t_alive(det_env, log):
    """CPU 0, но жив конвертер x2t — операция не закончена, пока он жив."""
    c = det_env["clock"]
    start = c.t
    procs = [_LiveProc(c, 1, "editors.exe"), _LiveProc(c, 9, "x2t.exe", dies_at=start + 1.5)]
    det_env["r"]._get_r7_processes = lambda log_cb=None, fresh=False: [
        p for p in procs if p.is_running()]
    done, status = det_env["r"]._wait_operation_done(None, log_cb=log)
    assert status == "ok"
    assert done - start == pytest.approx(1.5, abs=0.1)


def test_ready_returns_false_immediately_when_r7_dies(det_env, log):
    """Все процессы Р7 исчезли — ответ сразу, а не через 120 с таймаута."""
    c = det_env["clock"]
    start = c.t
    procs = [_LiveProc(c, 1, "editors.exe", dies_at=start + 2.0)]
    det_env["r"]._get_r7_processes = lambda log_cb=None, fresh=False: [
        p for p in procs if p.is_running()]
    assert det_env["r"]._wait_until_r7_ready(None, timeout=120, log_cb=log) is False
    assert c.t - start < 5
    assert any("исчезли" in m for m in log.messages)


# ── G-11: Batch-режим — оркестрация по версиям ───────────────────────────

@pytest.fixture
def batch_env(bare_r7, tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    r = bare_r7
    env = {"calls": [], "install_ok": {}, "run_result": {"open_elapsed": 7.5, "vlookup_elapsed": 2.8,
                                                         "results": [], "json_path": "x.json"},
           "done": None, "progress": [], "stop": threading.Event(), "pause": threading.Event()}
    r._capture_environment = lambda log_cb=None: {}
    r.current_version_info = None

    def uninstall():
        env["calls"].append("uninstall")
        return True

    def install(dist):
        env["calls"].append(("install", dist.name))
        return env["install_ok"].get(dist.name, True)

    def detect():
        env["calls"].append("detect")
        r.current_version_info = {"name": "Р7 " + env["calls"][-2][1]}

    def clear():
        env["calls"].append("clear_cache")
        return 0

    def run_one(test_file, ver, log_cb, stop_event, pause_event):
        env["calls"].append(("run", ver))
        return dict(env["run_result"]) if env["run_result"] is not None else None

    r.uninstall_current_version = uninstall
    r.install_version = install
    r.detect_current_version = detect
    r._clear_r7_cache = clear
    r._batch_run_single_version = run_one
    env["versions"] = [tmp_path / "r7-office_2026.1.1.1849_x64.msi",
                       tmp_path / "r7-office_2026.2.2.2923_x64.exe"]

    def go(stop_on_error=False, cleanup=True):
        r._batch_worker(env["versions"], tmp_path / "f.xlsx", stop_on_error, cleanup,
                        lambda m: None, lambda m: None, lambda f, m: None,
                        env["progress"].append,
                        lambda res, err: env.__setitem__("done", (res, err)),
                        env["stop"], env["pause"])
        return env["done"]
    env["go"] = go
    return env


def test_batch_main_flow_order_and_results(batch_env):
    results, errors = batch_env["go"]()
    names = [v.name for v in batch_env["versions"]]
    assert batch_env["calls"] == [
        "uninstall", ("install", names[0]), "detect", "clear_cache", ("run", "Р7 " + names[0]),
        "uninstall", ("install", names[1]), "detect", "clear_cache", ("run", "Р7 " + names[1]),
    ]
    assert errors == 0 and [r["success"] for r in results] == [True, True]
    assert results[0]["open_elapsed"] == 7.5 and results[0]["file"] == names[0]
    assert batch_env["progress"] == [1, 2]


def test_batch_failed_install_skips_run_and_continues(batch_env):
    first = batch_env["versions"][0].name
    batch_env["install_ok"][first] = False
    results, errors = batch_env["go"]()
    assert errors == 1
    assert results[0]["success"] is False and "Установка" in results[0]["error"]
    assert ("run", "Р7 " + first) not in batch_env["calls"]
    assert results[1]["success"] is True                      # вторая версия прогнана


def test_batch_stop_on_error(batch_env):
    batch_env["install_ok"][batch_env["versions"][0].name] = False
    results, errors = batch_env["go"](stop_on_error=True)
    assert errors == 1 and len(results) == 1
    assert sum(1 for c in batch_env["calls"] if c == "uninstall") == 1


def test_batch_stop_before_start(batch_env):
    batch_env["stop"].set()
    results, errors = batch_env["go"]()
    assert results == [] and batch_env["calls"] == []


def test_batch_empty_run_result_is_error(batch_env):
    batch_env["run_result"] = None
    results, errors = batch_env["go"]()
    assert errors == 2 and all("не вернул" in r["error"] for r in results)


def test_batch_without_cleanup_keeps_cache(batch_env):
    batch_env["go"](cleanup=False)
    assert "clear_cache" not in batch_env["calls"]


def test_batch_summary_html_escapes_and_handles_failures(bare_r7):
    rows = [
        {"version": "<script>alert(1)</script>", "file": "a.msi", "success": True, "error": None,
         "open_elapsed": 7.5, "vlookup_elapsed": 2.8, "peak_ram": 900.0, "avg_ram": 800.0,
         "peak_cpu": 150.0},
        {"version": "2026.2", "file": "b.exe", "success": False, "error": "Установка <упала>",
         "open_elapsed": None, "vlookup_elapsed": None, "peak_ram": None, "avg_ram": None,
         "peak_cpu": None},
    ]
    html_text = bare_r7._generate_batch_summary_html(rows)
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text
    assert "Установка <упала>" not in html_text


# ── G-12: писатель JSON и его читатели — сквозной тест ───────────────────

def _op_result(name, runs):
    import statistics
    stats = runs[1:]
    return {"name": name, "time": statistics.median(stats), "error": None,
            "runs": runs, "run_statuses": ["ok"] * len(runs),
            "median": statistics.median(stats), "mad": 0.01, "n_runs": len(stats),
            "first_run_discarded": True, "n_timeouts": 0, "ram": 500.0, "cpu": 50.0}


def test_full_report_round_trip_to_trends_and_comparison(bare_r7, tmp_path, monkeypatch):
    import json
    bare_r7.reports_folder = tmp_path
    bare_r7._run_environment = {"system_cpu_pct": 1.0}
    bare_r7._cached_cpu_count = 16
    monkeypatch.setattr(bare_r7, "_get_dpi_scale_pct", lambda: 100)
    paths = []
    for ts, ver, base in (("20260929_100000", "2026.2", 1.0), ("20260929_110000", "2026.3", 1.5)):
        results = [_op_result("Копирование всех ячеек (Ctrl+C)", [base * 1.2] + [base] * 6),
                   _op_result("Удаление столбца <b>", [0.3] * 7)]
        data = bare_r7._build_full_report(ts, ver, tmp_path / "f.xlsx", results,
                                          {"peak_ram_mb": 900.0, "leak_detection": None})
        p = tmp_path / f"performance_full_{ts}.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        paths.append(p)
        assert data["measure_schema"] == r7mod.MEASURE_SCHEMA_VERSION
        assert data["system"]["cpu_cores_logical"] == 16
        assert data["system"]["environment"] == {"system_cpu_pct": 1.0}

    runs = bare_r7._load_trends_runs()
    assert [r["version"] for r in runs] == ["2026.2", "2026.3"]
    assert runs[1]["schema"] == r7mod.MEASURE_SCHEMA_VERSION
    assert runs[1]["results"]["Копирование всех ячеек (Ctrl+C)"]["time"] == pytest.approx(1.5)

    datasets = [{"path": str(p), "version": v, "data": json.loads(p.read_text(encoding="utf-8"))}
                for p, v in zip(paths, ("2026.2", "2026.3"))]
    page = bare_r7._generate_comparison_html(datasets, str(paths[0]))
    assert "РЕГРЕССИЯ" in page                     # +50% на 6 прогонах — вердикт выносится
    # В HTML-таблице имя операции экранировано; внутри JSON в <script> «<b>»
    # безопасен — там опасно только «</», его экранирует _json_for_script.
    assert "&lt;b&gt;" in page
    assert "<td>Удаление столбца <b>" not in page


# ── Экспорты в полном прогоне — DEFAULT_FORMAT_TEST_RUNS повторов ────────

def test_export_tests_cover_all_formats_including_pdf():
    cls = r7mod.R7Testovarka
    assert cls.EXPORT_TESTS <= set(cls.TEST_DEFINITIONS)
    assert "Сохранение в PDF (конвертация x2t)" in cls.EXPORT_TESTS
    assert cls.EXTRA_FORMAT_TESTS <= cls.EXPORT_TESTS
    assert cls.DEFAULT_FORMAT_TEST_RUNS == 3
