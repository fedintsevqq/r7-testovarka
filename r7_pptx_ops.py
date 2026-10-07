"""Тест-операции редактора презентаций Р7 (.pptx) — этап 5, пункт 1 плана «до 20».

Контракт тот же, что у r7_ops.SpreadsheetOps и r7_doc_ops.DocumentOps:
tests() отдаёт [(имя теста, функция с .prepare)], воркер меряет каждую
через _measure_op_repeated. Подготовка (число слайдов устоялось, текущий —
первый слайд) идёт вне замера, в замере — один вызов api.

Клавиатурного запасного пути у правок нет: «Добавление 50 слайдов»
клавишами — полсотни нажатий в замере, тема и переходы — слепая работа с
панелями (правило 8 CLAUDE.md). Нет CDP или api не выполнил операцию — тест
честно падает с причиной в отчёте. Экспорт идёт общим «Сохранить как»
(_save_as_format), как у таблиц и документов.

Модуль ничего не импортирует из r7_Testovarka (см. r7_ops).
"""
from r7_ops import _with_prepare

OPEN_TEST_NAME = "Повторное открытие файла"   # то же имя, что у таблиц (gate, наборы)

SLIDES_TO_ADD = 50
# Индексы тем редактора по приоритету (ThemeLoader.Themes.EditorThemes): 0 —
# обычно «Пустая»/«Office», берём следующие. Своя тема фикстуры («R7
# Testovarka») не совпадает ни с одной, так что смена — всегда смена.
THEME_CANDIDATES = (1, 2, 3, 4, 0)
TRANSITION_DURATION_MS = 700

ADD_SLIDES_TEST = f"Добавление {SLIDES_TO_ADD} слайдов"
DUPLICATE_TEST = "Дублирование всех слайдов"
THEME_TEST = "Смена темы"
TRANSITION_TEST = "Переход ко всем слайдам"
EXPORT_PDF_TEST = "Сохранение в PDF (презентация, x2t)"
EXPORT_PPTX_TEST = "Сохранение в PPTX (презентация, x2t)"

# Порядок — порядок прогона. Экспорт PPTX последним: после «Сохранить как»
# Р7 продолжает работу уже с сохранённой копией.
PRESENTATION_TEST_DEFINITIONS = [
    OPEN_TEST_NAME,
    ADD_SLIDES_TEST,
    DUPLICATE_TEST,
    THEME_TEST,
    TRANSITION_TEST,
    EXPORT_PDF_TEST,
    EXPORT_PPTX_TEST,
]

# Повторов по умолчанию (экспорт — дольше, как у таблиц и документов).
DEFAULT_PPTX_RUNS = {name: (3 if name.startswith("Сохранение в") else 5)
                     for name in PRESENTATION_TEST_DEFINITIONS}


class PresentationOps:
    """Операции над открытой презентацией Р7.

    Args:
        app: экземпляр R7Testovarka — CDP-операции (`_pptx_cdp_*`,
            r7/pptx_run.py), подготовка (`_editor_prepare`), экспорт
            (`_save_as_format`), клавиши только для экспорта.
        find_hwnd: поиск окна Р7 (для экспорта).
        log_cb: журнал воркера.
        test_file: тестовая презентация.
    """

    def __init__(self, app, find_hwnd, log_cb, test_file):
        self.app = app
        self.find_hwnd = find_hwnd
        self.log_cb = log_cb
        self.test_file = test_file

    # ── клавиши — только цепочке «Сохранить как» (её запасные пути) ────────

    def hotkey(self, *keys):
        self.app._hotkey(*keys)

    def press(self, key, presses=1, pace=0.0):
        for _ in range(presses):
            self.app._press(key)
            if pace:
                self.app._pace(pace)

    # ── операции ───────────────────────────────────────────────────────────

    def _require(self, done, what):
        """CDP не выполнил операцию — ошибка прогона, а не клавиши вслепую."""
        if not done:
            raise RuntimeError(f"{what}: нет CDP или api презентации не выполнил "
                               f"операцию; клавишами не повторяю (правило 8)")

    def add_slides(self, count=SLIDES_TO_ADD):
        """N слайдов после первого одним вызовом JS."""
        self._require(self.app._pptx_cdp_add_slides(count, log_cb=self.log_cb),
                      f"добавление {count} слайдов")

    def duplicate_all(self):
        """Выделить все слайды и продублировать их."""
        self._require(self.app._pptx_cdp_duplicate_all(log_cb=self.log_cb),
                      "дублирование слайдов")

    def change_theme(self, candidates=THEME_CANDIDATES):
        """Применить другую тему редактора ко всей презентации."""
        self._require(self.app._pptx_cdp_change_theme(candidates, log_cb=self.log_cb),
                      "смена темы")

    def transition_all(self, duration_ms=TRANSITION_DURATION_MS):
        """Переход «Выцветание» на все слайды одним ApplySlideTransition."""
        self._require(self.app._pptx_cdp_transition_all(duration_ms, log_cb=self.log_cb),
                      "переход ко всем слайдам")

    def save_as_format(self, ext):
        """Экспорт через «Сохранить как» (_save_as_format, тип — по ext)."""
        self.app._save_as_format(ext, self.find_hwnd, self.hotkey, self.press,
                                 log_cb=self.log_cb)

    # ── список тестов ──────────────────────────────────────────────────────

    def _prep(self):
        return self.app._editor_prepare(log_cb=self.log_cb)

    def tests(self):
        """[(имя теста, функция с .prepare)] в порядке PRESENTATION_TEST_DEFINITIONS,
        без открытия файла (его меряет воркер)."""
        return [
            (ADD_SLIDES_TEST, _with_prepare(lambda: self.add_slides(), self._prep)),
            (DUPLICATE_TEST, _with_prepare(lambda: self.duplicate_all(), self._prep)),
            (THEME_TEST, _with_prepare(lambda: self.change_theme(), self._prep)),
            (TRANSITION_TEST, _with_prepare(lambda: self.transition_all(), self._prep)),
            (EXPORT_PDF_TEST, _with_prepare(lambda: self.save_as_format("pdf"), self._prep)),
            (EXPORT_PPTX_TEST, _with_prepare(lambda: self.save_as_format("pptx"), self._prep)),
        ]
