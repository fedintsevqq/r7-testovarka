"""Редакторы Р7, которые умеет мерить инструмент (этап 5, пункт 1).

Одно место для значений режима прогона (`_run_editor`), поля `editor` в
наборах (`[suite] editor`) и в полном JSON-отчёте. Отчёт без поля `editor`
— табличный (до этапа 5).
"""

EDITOR_SPREADSHEET = "spreadsheet"
EDITOR_DOCUMENT = "document"
EDITOR_PRESENTATION = "presentation"
EDITORS = (EDITOR_SPREADSHEET, EDITOR_DOCUMENT, EDITOR_PRESENTATION)
DEFAULT_EDITOR = EDITOR_SPREADSHEET
