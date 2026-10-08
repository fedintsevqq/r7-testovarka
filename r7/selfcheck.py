"""Проверка сборки без окна и без Р7: `R7-Testovarka.exe --self-check`.

Нужна CI-сборке .exe (docs/plan-to-10.md, шаг 5): PyInstaller молча
теряет модуль, импортируемый не напрямую, или папку шаблонов, и это
выясняется только у пользователя при первом отчёте. Код выхода 0 — всё на
месте, 1 — чего-то нет (что именно — в выводе).
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
from collections.abc import Callable
from pathlib import Path

REQUIRED = ("r7_reports", "r7_ops", "r7_webdriver_connector", "jinja2", "psutil",
            "win32gui", "pywinauto", "openpyxl", "pyautogui")


def run(out: Callable[[str], object] = print) -> int:
    problems: list[str] = []
    import r7
    for mod in sorted(m.name for m in pkgutil.walk_packages(r7.__path__, "r7.")):
        try:
            importlib.import_module(mod)
        except Exception as e:
            problems.append(f"модуль {mod}: {type(e).__name__}: {e}")
    for name in REQUIRED:
        try:
            importlib.import_module(name)
        except Exception as e:
            problems.append(f"зависимость {name}: {type(e).__name__}: {e}")

    import r7_reports
    tdir = Path(r7_reports.TEMPLATES_DIR)
    for tpl in ("base.html", "run.html", "comparison.html", "trends.html", "batch.html",
                "custom.html", "gate.html"):
        if not (tdir / tpl).is_file():
            problems.append(f"шаблон отчёта не найден: {tdir / tpl}")
    if not problems:
        try:
            html = r7_reports.render("custom.html", **r7_reports.custom_model(
                {"filename": "self-check.xlsx", "open_elapsed": 1.0, "vlookup_elapsed": None,
                 "vlookup_error": None, "timestamp": "self-check", "cache_cleared": False,
                 "data_ready": True}))
            if "self-check.xlsx" not in html:
                problems.append("пробный отчёт собрался без данных")
        except Exception as e:
            problems.append(f"пробный отчёт не собрался: {type(e).__name__}: {e}")

    from r7 import env
    frozen = "exe (PyInstaller)" if getattr(sys, "frozen", False) else f"Python {sys.version.split()[0]}"
    out(f"R7-Testovarka self-check: {frozen}, CDP {'есть' if env.WEBDRIVER_OK else 'НЕТ'}, "
        f"шаблоны {tdir}")
    for p in problems:
        out(f"  ✗ {p}")
    out("OK" if not problems else f"ПРОБЛЕМ: {len(problems)}")
    return 0 if not problems else 1
