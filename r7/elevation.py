"""Перезапуск программы от имени администратора (UAC).

Прежде точка входа звала ShellExecuteW("runas") с " ".join(sys.argv):
путь с пробелом разваливался на части, результат не проверялся (отказ в
UAC — тихий выход без окна), а в сборке PyInstaller sys.argv[0] — сам exe,
и он уходил в параметры второй раз. Здесь — сборка строки параметров и
проверка результата; окно сообщений остаётся в r7_Testovarka (пакет r7 вне
r7/ui tkinter не импортирует).
"""
import os
import subprocess

# ShellExecute возвращает «дескриптор экземпляра»: значение больше 32 —
# успех, меньше или равно — код ошибки (SE_ERR_*, отказ в UAC — 5).
_SHELL_EXECUTE_OK = 32
_SW_SHOWNORMAL = 1


def relaunch_arguments(argv, frozen):
    """Строка параметров для ShellExecute.

    Для exe (PyInstaller) — только argv[1:]: сам exe и есть executable. Для
    скрипта — абсолютный путь к нему и остальные параметры. Кавычки —
    subprocess.list2cmdline, чтобы пути с пробелами доходили целиком.
    """
    rest = list(argv[1:])
    if frozen:
        return subprocess.list2cmdline(rest)
    return subprocess.list2cmdline([os.path.abspath(argv[0]), *rest])


def relaunch_as_admin(argv, executable, frozen, shell_execute=None):
    """Запускает копию программы с повышением прав.

    Args:
        argv: sys.argv.
        executable: sys.executable (python.exe или собранный exe).
        frozen: getattr(sys, "frozen", False).
        shell_execute: подмена ctypes.windll.shell32.ShellExecuteW в тестах.

    Returns:
        bool: True — копия запущена, текущий процесс можно завершать;
        False — пользователь отказал в UAC или запуск не удался, работать
        дальше без прав.
    """
    if shell_execute is None:
        import ctypes
        shell_execute = ctypes.windll.shell32.ShellExecuteW
    params = relaunch_arguments(argv, frozen)
    try:
        result = shell_execute(None, "runas", executable, params, None, _SW_SHOWNORMAL)
    except OSError:
        return False
    try:
        return int(result) > _SHELL_EXECUTE_OK
    except (TypeError, ValueError):
        return False
