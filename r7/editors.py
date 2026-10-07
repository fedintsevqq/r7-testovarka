"""Редакторы Р7, которые умеет мерить инструмент (этап 5, пункт 1).

Одно место для значений режима прогона (`_run_editor`), поля `editor` в
наборах (`[suite] editor`), в полном JSON-отчёте и в selected_tests.json.
Отчёт без поля `editor` — табличный (до этапа 5).
"""

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
