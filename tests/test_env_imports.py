"""r7/env.py без необязательных пакетов (plan-to-10, шаг 3): флаги ложны,
имена — None, подсказка «Установите …» печатается, импорт не падает.

Модуль грузится заново под другим именем, поэтому настоящий r7.env и его
подмены в остальных тестах не затрагиваются. sys.modules[name] = None
заставляет `import name` бросить ImportError — как на стенде без пакета.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ENV_PATH = Path(__file__).resolve().parent.parent / "r7" / "env.py"

# пакет → (подменяемые модули, флаг, имена, которые станут None, подсказка)
CASES = {
    "pyautogui": (("pyautogui",), "PYAUTOGUI_OK", ("pyautogui",), "pip install pyautogui"),
    "pyperclip": (("pyperclip",), None, ("pyperclip",), "pip install pyperclip"),
    "openpyxl": (("openpyxl",), "EXCEL_OK", ("Workbook", "WriteOnlyCell", "Font", "PatternFill"),
                 "pip install openpyxl"),
    "pywin32": (("win32gui", "win32con", "win32api", "win32process"), "WIN32_OK",
                ("win32gui", "win32con", "win32api", "win32process"), "pip install pywin32"),
    "psutil": (("psutil",), "PSUTIL_OK", ("psutil",), "pip install psutil"),
    "pywinauto": (("pywinauto",), "PYWINAUTO_OK", ("_UiaApplication",), "pip install pywinauto"),
    "webdriver": (("r7_webdriver_connector",), "WEBDRIVER_OK",
                  ("R7WebDriverConnector", "r7_launch_debug_args"), "r7_webdriver_connector"),
}


def _load_env(monkeypatch, missing):
    for mod in missing:
        monkeypatch.setitem(sys.modules, mod, None)
    spec = importlib.util.spec_from_file_location("r7_env_probe", ENV_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("package", sorted(CASES))
def test_env_without_package(package, monkeypatch, capsys):
    modules, flag, names, hint = CASES[package]
    env = _load_env(monkeypatch, modules)
    if flag:
        assert getattr(env, flag) is False
    for name in names:
        assert getattr(env, name) is None
    assert hint in capsys.readouterr().out


def test_env_without_webdriver_keeps_default_port(monkeypatch):
    env = _load_env(monkeypatch, ("r7_webdriver_connector",))
    assert env.DEFAULT_CDP_PORT == 8080


def test_env_with_everything_installed_is_silent_and_sets_pyautogui(monkeypatch, capsys):
    pytest.importorskip("pyautogui")
    pytest.importorskip("pywinauto")
    env = _load_env(monkeypatch, ())
    out = capsys.readouterr().out
    assert "Установите" not in out
    assert env.pyautogui.PAUSE == 0 and env.pyautogui.FAILSAFE is True   # правила CLAUDE.md
    assert env.WIN32_OK and env.PSUTIL_OK and env.EXCEL_OK


def test_env_with_all_optional_packages_missing(monkeypatch):
    """Голый Python без единого необязательного пакета: модуль грузится,
    все флаги ложны — дальше код идёт запасными путями по флагам."""
    env = _load_env(monkeypatch, [m for mods, *_ in CASES.values() for m in mods])
    assert not any((env.PYAUTOGUI_OK, env.EXCEL_OK, env.WIN32_OK, env.PSUTIL_OK,
                    env.PYWINAUTO_OK, env.WEBDRIVER_OK))
