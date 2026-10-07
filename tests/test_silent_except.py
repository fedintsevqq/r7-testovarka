"""Страж: у каждого глухого `except: pass` — причина рядом (до 10, шаг 7).

Проглоченная ошибка без объяснения — то, из-за чего в истории проекта
«3/3», ложные «OK» и пропавшие отчёты находились неделями позже. Пустой
обработчик допустим, только если рядом сказано, почему это безопасно; иначе
— запись в журнал.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [*sorted((ROOT / "r7").rglob("*.py")),
         *(ROOT / n for n in ("r7_ops.py", "r7_reports.py", "r7_webdriver_connector.py",
                              "r7_Testovarka.py"))]


def _silent_without_reason(path):
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.ExceptHandler) and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)):
            continue
        around = lines[max(0, node.lineno - 2):node.body[0].lineno]
        if not any("#" in ln for ln in around):
            bad.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return bad


def test_every_silent_except_explains_why():
    bad = [b for f in FILES for b in _silent_without_reason(f)]
    assert not bad, ("глухой except без причины — добавьте комментарий «почему безопасно» "
                     "или запись в журнал: " + ", ".join(bad))


def test_no_bare_except():
    bad = []
    for f in FILES:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                bad.append(f"{f.relative_to(ROOT)}:{node.lineno}")
    assert not bad, "голый except: запрещён (CLAUDE.md) — " + ", ".join(bad)
