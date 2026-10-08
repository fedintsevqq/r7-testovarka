"""Наборы тестов как данные: suites/*.toml (docs/plan-to-20.md, этап 2, пункт 5).

Набор говорит, какие тесты гонять и сколько раз, какие медианы считать
бюджетом (абсолютный потолок в секундах) и с каким порогом сравнивать с
эталоном. Формат файла:

    [suite]
    name = "smoke"
    description = "Дымовой прогон, ~5 минут"
    editor = "spreadsheet"                # необязательно: "spreadsheet" | "document" | "presentation"

    [tests]                               # имя — точно как в TEST_DEFINITIONS
    "Повторное открытие файла" = 3        # число повторов, RUNS_MIN..RUNS_MAX

    [budgets]                             # необязательно
    "Повторное открытие файла" = 12.0     # секунд; медиана выше — «Не готов»

    [compare]                             # необязательно
    min_effect_pct = 10                   # порог практической значимости compare_runs

Список тестов живёт в R7Testovarka.TEST_DEFINITIONS (таблицы) и
r7_doc_ops.DOCUMENT_TEST_DEFINITIONS (документы),
r7_pptx_ops.PRESENTATION_TEST_DEFINITIONS (презентации), а r7.* ничего не
берёт из r7_Testovarka, поэтому вызывающий передаёт допустимые имена сам: списком
(только таблицы, как прежде) или словарём «редактор → имена»
(R7Testovarka.editor_test_names()).
Ошибки — SuiteError с текстом по-русски: что не так и что допустимо.
"""
from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from r7 import config
from r7.config import DEFAULT_TEST_RUNS, RUNS_MAX, RUNS_MIN
from r7.editors import DEFAULT_EDITOR, EDITORS   # редактор набора, [suite] editor
from r7.stats import COMPARISON_MIN_EFFECT_PCT

SUITES_SUBDIR = "suites"
SECTIONS = ("suite", "tests", "budgets", "compare")

# Допустимые имена тестов: список (только таблицы) или «редактор → имена».
ValidNames = Sequence[str] | Mapping[str, Sequence[str]]


class SuiteError(ValueError):
    """Набор не читается или противоречит списку тестов."""


@dataclass(frozen=True)
class Suite:
    """Набор тестов: что гонять, сколько раз, какие потолки."""
    name: str
    description: str
    tests: dict[str, int]                           # имя теста → повторов
    budgets: dict[str, float] = field(default_factory=dict)  # имя теста → потолок медианы, с
    min_effect_pct: float = COMPARISON_MIN_EFFECT_PCT
    path: Path | None = None
    editor: str = DEFAULT_EDITOR                    # r7.editors.EDITORS

    @property
    def total_runs(self) -> int:
        return sum(self.tests.values())


def suites_dir() -> Path:
    """Папка наборов рядом с программой — читается при вызове, чтобы тесты
    могли подменить config.BASE_DIR."""
    return config.BASE_DIR / SUITES_SUBDIR


def list_suites(folder: str | os.PathLike[str] | None = None) -> list[Path]:
    """Файлы наборов (*.toml) по имени; нет папки — пустой список."""
    root = Path(folder) if folder else suites_dir()
    return sorted(root.glob("*.toml")) if root.is_dir() else []


def load_suite(path: str | os.PathLike[str], valid_names: ValidNames) -> Suite:
    """Читает и проверяет набор.

    Args:
        path: путь к .toml.
        valid_names: допустимые имена тестов: список (таблицы,
            R7Testovarka.TEST_DEFINITIONS) или словарь «редактор → имена»
            (R7Testovarka.editor_test_names()).

    Raises:
        SuiteError: файла нет, TOML битый, неизвестный тест, плохие повторы
            или бюджет.
    """
    suite_path = Path(path)
    try:
        with open(suite_path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise SuiteError(f"файл набора не найден: {suite_path}") from None
    except tomllib.TOMLDecodeError as e:
        raise SuiteError(f"набор {suite_path.name}: ошибка в TOML — {e}") from None
    except OSError as e:
        raise SuiteError(f"набор {suite_path.name} не прочитан: {e}") from None
    return parse_suite(data, valid_names, path=suite_path)


def parse_suite(data: object, valid_names: ValidNames, path: Path | None = None) -> Suite:
    """Suite из уже разобранного TOML (dict). Чистая функция для тестов."""
    label = path.name if path else "набор"
    if not isinstance(data, dict):
        raise SuiteError(f"{label}: ожидался TOML-документ с разделами {_sections()}")
    unknown_sections = [k for k in data if k not in SECTIONS]
    if unknown_sections:
        raise SuiteError(f"{label}: неизвестный раздел [{unknown_sections[0]}]; "
                         f"допустимы {_sections()}")

    head = data.get("suite") or {}
    if not isinstance(head, dict):
        raise SuiteError(f"{label}: [suite] должен быть разделом с name и description")
    name = str(head.get("name") or (path.stem if path else "")).strip()
    if not name:
        raise SuiteError(f"{label}: в [suite] нет name")

    editor = _parse_editor(label, head.get("editor"))
    tests = _parse_tests(label, data.get("tests"), _names_for(label, valid_names, editor))
    budgets = _parse_budgets(label, data.get("budgets"), tests)
    min_effect_pct = _parse_min_effect(label, data.get("compare"))
    return Suite(name=name, description=str(head.get("description") or "").strip(),
                 tests=tests, budgets=budgets, min_effect_pct=min_effect_pct, path=path,
                 editor=editor)


def _parse_editor(label: str, value: object) -> str:
    if value is None:
        return DEFAULT_EDITOR
    if value not in EDITORS:
        raise SuiteError(f"{label}: editor = {value!r} — допустимы "
                         + ", ".join(f'"{e}"' for e in EDITORS))
    return str(value)


def _names_for(label: str, valid_names: ValidNames, editor: str) -> list[str]:
    """Допустимые имена тестов редактора набора."""
    if isinstance(valid_names, Mapping):
        names = valid_names.get(editor)
    else:
        names = valid_names if editor == DEFAULT_EDITOR else None
    if names is None:
        raise SuiteError(f"{label}: для редактора «{editor}» список тестов не передан")
    return list(names)


def _sections() -> str:
    return ", ".join(f"[{s}]" for s in SECTIONS)


def _parse_tests(label: str, tests: object, valid: list[str]) -> dict[str, int]:
    if not isinstance(tests, dict) or not tests:
        raise SuiteError(f"{label}: в [tests] нет ни одного теста. Допустимые имена:\n  "
                         + "\n  ".join(valid))
    unknown = [n for n in tests if n not in valid]
    if unknown:
        raise SuiteError(f"{label}: неизвестные тесты: {', '.join(repr(n) for n in unknown)}. "
                         f"Имя должно совпадать с TEST_DEFINITIONS буква в букву. "
                         f"Допустимые имена:\n  " + "\n  ".join(valid))
    return {n: _runs_value(label, n, v) for n, v in tests.items()}


def _runs_value(label: str, name: str, value: object) -> int:
    """Повторы теста: целое, обрезанное до RUNS_MIN..RUNS_MAX — как поле «×N»
    в интерфейсе. Не целое (строка, дробь, true) — ошибка."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SuiteError(f"{label}: повторы теста «{name}» должны быть целым числом "
                         f"{RUNS_MIN}..{RUNS_MAX}, а не {value!r}")
    return max(RUNS_MIN, min(RUNS_MAX, value))


def _parse_budgets(label: str, budgets: object, tests: Mapping[str, int]) -> dict[str, float]:
    if budgets is None:
        return {}
    if not isinstance(budgets, dict):
        raise SuiteError(f"{label}: [budgets] должен быть разделом «имя теста = секунды»")
    out: dict[str, float] = {}
    for name, value in budgets.items():
        if name not in tests:
            raise SuiteError(f"{label}: бюджет для «{name}», которого нет в [tests] — "
                             f"проверять было бы нечего")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise SuiteError(f"{label}: бюджет теста «{name}» — число секунд больше нуля, "
                             f"а не {value!r}")
        out[name] = float(value)
    return out


def _parse_min_effect(label: str, compare: object) -> float:
    if compare is None:
        return COMPARISON_MIN_EFFECT_PCT
    if not isinstance(compare, dict):
        raise SuiteError(f"{label}: [compare] должен быть разделом с min_effect_pct")
    value = compare.get("min_effect_pct", COMPARISON_MIN_EFFECT_PCT)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise SuiteError(f"{label}: min_effect_pct — процент (число ≥ 0), а не {value!r}")
    return float(value)


def suite_to_selection(suite: Suite, all_names: Sequence[str] = ()) -> dict[str, dict[str, Any]]:
    """Структура selected_tests.json для набора: {имя: {"enabled", "runs"}}.

    Интерфейс читает именно её (_load_test_selection), так что набор можно
    загрузить как выбор тестов. Тесты из all_names, которых нет в наборе,
    выключены с повторами по умолчанию.
    """
    out: dict[str, dict[str, Any]] = {}
    for name in list(all_names) + [n for n in suite.tests if n not in all_names]:
        if name in suite.tests:
            out[name] = {"enabled": True, "runs": suite.tests[name]}
        else:
            out[name] = {"enabled": False, "runs": DEFAULT_TEST_RUNS}
    return out
