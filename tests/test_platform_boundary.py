"""Граница Windows-кода (docs/plan-to-20.md, раздел «Linux и macOS»).

Правило CLAUDE.md: win32*, реестр (winreg), pywinauto и ctypes.windll живут
только в r7/env.py, r7/windows.py, r7/versions.py и r7/x2t_files.py за
флагами *_OK. Остальные модули, где такие вызовы есть сейчас, перечислены в
LEGACY_OFFENDERS: код не переносится разом, а уходит по мере правок.

Тест-храповик: новый модуль с Windows-вызовом падает сразу, а модуль из
списка, переставший их делать, требует вычеркнуть себя — список только
сокращается.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Где Windows-вызовам место.
ALLOWED = {"r7/env.py", "r7/windows.py", "r7/versions.py", "r7/x2t_files.py"}

# Вызовы есть сейчас (07.10.2026), переносить по мере правок.
LEGACY_OFFENDERS = {
    "r7/bold_button.py",
    "r7/close_wait.py",
    "r7/crash_recovery.py",
    "r7/dialogs.py",
    "r7/export.py",
    "r7/perf.py",
}

# Модули Windows: pywin32 (win32gui, win32con, …, pywintypes), реестр,
# UI Automation.
# win32gui, win32con, _win32sysloader; но не флаг win32_ok.
_WIN32_NAME = re.compile(r"^_?win32[a-z]+$")
_WIN_MODULES = {"pywintypes", "winreg", "pywinauto", "comtypes"}
# ctypes.windll / ctypes.WinDLL / oledll — прямые вызовы Win32 API.
_WIN_ATTRS = {"windll", "WinDLL", "oledll", "OleDLL", "WINFUNCTYPE"}


def _is_win_module(name):
    top = (name or "").split(".")[0]
    return bool(_WIN32_NAME.match(top)) or top in _WIN_MODULES


def _windows_uses(path):
    """Строки, где модуль импортирует Windows-пакет или обращается к нему
    (в том числе через env.win32gui)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits += [node.lineno for a in node.names if _is_win_module(a.name)]
        elif isinstance(node, ast.ImportFrom):
            # from win32com import client; from r7.env import win32gui
            if ((node.level == 0 and _is_win_module(node.module))
                    or any(_is_win_module(a.name) for a in node.names)):
                hits.append(node.lineno)
        elif isinstance(node, ast.Attribute):
            if node.attr in _WIN_ATTRS or _is_win_module(node.attr):
                hits.append(node.lineno)
    return hits


def _product_modules():
    files = list((ROOT / "r7").rglob("*.py"))
    files += list(ROOT.glob("*.py"))
    return {p.relative_to(ROOT).as_posix(): p for p in files if "__pycache__" not in p.parts}


def _offenders():
    return {rel for rel, p in _product_modules().items()
            if rel not in ALLOWED and _windows_uses(p)}


def test_no_new_windows_calls_outside_boundary():
    new = sorted(_offenders() - LEGACY_OFFENDERS)
    assert not new, (
        "Windows-вызовы (win32*, winreg, pywinauto, ctypes.windll) вне r7/env.py, "
        f"r7/windows.py, r7/versions.py, r7/x2t_files.py: {new}. Перенесите их туда "
        "за флаг *_OK (правило CLAUDE.md, раздел «Переносимость»)")


def test_legacy_list_only_shrinks():
    cleaned = sorted(LEGACY_OFFENDERS - _offenders())
    assert not cleaned, (
        f"Эти модули больше не зовут Windows напрямую — вычеркните их из "
        f"LEGACY_OFFENDERS: {cleaned}")


def test_allowed_and_legacy_do_not_overlap():
    assert not (ALLOWED & LEGACY_OFFENDERS)


def test_detector_sees_typical_calls(tmp_path):
    src = tmp_path / "m.py"
    src.write_text("import ctypes\n"
                   "def f():\n"
                   "    import win32gui\n"
                   "    from pywinauto import Application\n"
                   "    return ctypes.windll.shell32, env.win32pdh\n", encoding="utf-8")
    assert sorted(_windows_uses(src)) == [3, 4, 5, 5]


def test_detector_ignores_flags_and_strings(tmp_path):
    src = tmp_path / "m.py"
    src.write_text('# win32gui в комментарии\n'
                   'NAMES = ("win32gui", "pywinauto")\n'
                   'def g(win32_ok):\n'
                   '    return env.WIN32_OK and win32_ok\n', encoding="utf-8")
    assert _windows_uses(src) == []
