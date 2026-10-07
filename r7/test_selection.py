"""Файл выбора тестов selected_tests.json: разбор трёх форматов и запись.

Форматы, которые читаются:

  * самый старый — {тест: bool};
  * плоский — {тест: {"enabled": bool, "runs": int}}; его же пишет
    r7/suites.py (suite_to_selection). Оба плоских — выбор для таблиц;
  * по редакторам (вкладка «Производительность» с переключателем) —
    {"spreadsheet": {...}, "document": {...}, "presentation": {...},
     "_editor": "document"}: у каждого редактора свой плоский выбор, в
    "_editor" — какой редактор был выбран последним.

Пишется только формат по редакторам. Битый файл, список вместо словаря,
мусор в записи — значения по умолчанию, запуск программы не падает
(QA-аудит 29.09.2026, G-14). Модуль без tkinter — проверяется без окна.
"""
from r7.config import DEFAULT_TEST_RUNS
from r7.editors import DEFAULT_EDITOR, EDITORS

SELECTION_FILE = "selected_tests.json"
EDITOR_KEY = "_editor"


def _parse_entry(value):
    """Одна запись теста → {"enabled", "runs"}; bool — самый старый формат."""
    if not isinstance(value, dict):
        return {"enabled": bool(value), "runs": DEFAULT_TEST_RUNS}
    try:
        runs = int(value.get("runs", DEFAULT_TEST_RUNS))
    except (TypeError, ValueError):
        runs = DEFAULT_TEST_RUNS
    return {"enabled": bool(value.get("enabled", True)), "runs": max(1, runs)}


def parse_section(raw):
    """Плоский выбор одного редактора: {тест: {"enabled", "runs"}}.
    Не словарь — пусто."""
    if not isinstance(raw, dict):
        return {}
    return {name: _parse_entry(value) for name, value in raw.items()}


def is_per_editor(raw):
    """Формат по редакторам: есть "_editor" или ключ-редактор. Тестов с
    такими именами нет, так что с плоским форматом не спутать."""
    return isinstance(raw, dict) and (EDITOR_KEY in raw or any(e in raw for e in EDITORS))


def parse_selection(raw):
    """Содержимое selected_tests.json → {"editor": str, "sections": {редактор: выбор}}.

    В "sections" есть все редакторы (пустой выбор — тесты по умолчанию).
    """
    sections = {e: {} for e in EDITORS}
    editor = DEFAULT_EDITOR
    if is_per_editor(raw):
        for e in EDITORS:
            sections[e] = parse_section(raw.get(e))
        if raw.get(EDITOR_KEY) in EDITORS:
            editor = raw[EDITOR_KEY]
    else:
        sections[DEFAULT_EDITOR] = parse_section(raw)
    return {"editor": editor, "sections": sections}


def build_selection(sections, editor):
    """Что записать в selected_tests.json: выбор каждого редактора и
    последний выбранный редактор. Исходные словари не меняются."""
    data = {EDITOR_KEY: editor if editor in EDITORS else DEFAULT_EDITOR}
    for e in EDITORS:
        data[e] = {name: dict(entry) for name, entry in (sections.get(e) or {}).items()}
    return data
