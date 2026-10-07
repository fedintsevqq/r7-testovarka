"""Перезапуск от имени администратора (r7.elevation): параметры в кавычках,
без второго exe в сборке PyInstaller, отказ в UAC — False, а не тихий выход."""
import pytest

from r7 import elevation


def test_script_arguments_are_quoted_and_absolute(tmp_path):
    script = tmp_path / "папка с пробелом" / "r7_Testovarka.py"
    params = elevation.relaunch_arguments([str(script), "--flag", "знач ение"], frozen=False)
    assert params == f'"{script}" --flag "знач ение"'


def test_frozen_passes_only_extra_arguments():
    params = elevation.relaunch_arguments([r"C:\Tools\R7-Testovarka.exe", "--x"], frozen=True)
    assert params == "--x"


def test_frozen_without_arguments_is_empty():
    assert elevation.relaunch_arguments([r"C:\Tools\R7-Testovarka.exe"], frozen=True) == ""


def test_relaunch_calls_runas_with_executable():
    calls = []

    def shell_execute(hwnd, verb, file, params, cwd, show):
        calls.append((hwnd, verb, file, params, cwd, show))
        return 42
    ok = elevation.relaunch_as_admin([r"C:\p q\r7_Testovarka.py"], r"C:\py\python.exe",
                                     frozen=False, shell_execute=shell_execute)
    assert ok is True
    hwnd, verb, file, params, cwd, show = calls[0]
    assert (hwnd, verb, file, cwd, show) == (None, "runas", r"C:\py\python.exe", None, 1)
    assert params == r'"C:\p q\r7_Testovarka.py"'


@pytest.mark.parametrize("result", [5, 0, 32, 2, -1])
def test_uac_refusal_or_error_returns_false(result):
    assert elevation.relaunch_as_admin(["a.py"], "python.exe", False,
                                       shell_execute=lambda *a: result) is False


def test_shell_execute_exception_returns_false():
    def shell_execute(*a):
        raise OSError("нет shell32")
    assert elevation.relaunch_as_admin(["a.py"], "python.exe", False,
                                       shell_execute=shell_execute) is False


def test_default_shell_execute_comes_from_windows_boundary(monkeypatch):
    """Без подмены ShellExecuteW берётся из r7.windows (граница Windows-кода)."""
    import r7.windows as r7windows
    calls = []
    monkeypatch.setattr(r7windows, "shell_execute_function",
                        lambda: lambda *a: calls.append(a) or 42)
    assert elevation.relaunch_as_admin(["a.py"], "python.exe", False) is True
    assert calls and calls[0][1] == "runas"
