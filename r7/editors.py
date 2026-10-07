"""Редакторы Р7, которые умеет мерить инструмент (этап 5, пункт 1).

Одно место для значений режима прогона (`_run_editor`), поля `editor` в
наборах (`[suite] editor`), в полном JSON-отчёте и в selected_tests.json.
Отчёт без поля `editor` — табличный (до этапа 5).
"""
from contextlib import contextmanager

EDITOR_SPREADSHEET = "spreadsheet"
EDITOR_DOCUMENT = "document"
EDITOR_PRESENTATION = "presentation"
EDITORS = (EDITOR_SPREADSHEET, EDITOR_DOCUMENT, EDITOR_PRESENTATION)
DEFAULT_EDITOR = EDITOR_SPREADSHEET

# Воркер прогона по редактору: CLI (`python -m r7 run`) и вкладка
# «Производительность» зовут один и тот же метод R7Testovarka.
EDITOR_WORKERS = {EDITOR_SPREADSHEET: "_spreadsheet_worker",
                  EDITOR_DOCUMENT: "_document_worker",
                  EDITOR_PRESENTATION: "_presentation_worker"}

# Подписи переключателя на вкладке «Производительность».
EDITOR_LABELS = {EDITOR_SPREADSHEET: "Таблица (.xlsx)",
                 EDITOR_DOCUMENT: "Документ (.docx)",
                 EDITOR_PRESENTATION: "Презентация (.pptx)"}

# Расширения тестового файла по редактору — проверка выбора в Batch
# (r7/batch_config.py): .docx в табличном режиме Р7 откроет, но операции
# таблиц на нём честно упадут, и Batch потратит часы впустую.
EDITOR_FILE_SUFFIXES = {EDITOR_SPREADSHEET: (".xlsx", ".xls"),
                        EDITOR_DOCUMENT: (".docx",),
                        EDITOR_PRESENTATION: (".pptx",)}


@contextmanager
def editor_mode(app, editor):
    """Режим прогона `_run_editor` на время блока, прежний — при любом исходе.

    Так же, как _document_worker/_presentation_worker ставят режим вокруг
    общего воркера, им пользуются трасса (r7/trace.py) и Batch (r7/runs.py):
    подмены DocumentRunMixin/PresentationRunMixin — фикстура, готовность,
    операции, снимок и откат истории, ключ `editor` отчёта — включаются сами.

    Raises:
        ValueError: неизвестный редактор.
    """
    if editor not in EDITORS:
        raise ValueError(f"неизвестный редактор: {editor!r}")
    prev = getattr(app, "_run_editor", DEFAULT_EDITOR)
    app._run_editor = editor
    try:
        yield
    finally:
        app._run_editor = prev
