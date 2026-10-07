"""Тест-операции текстового редактора Р7 (.docx) — этап 5, пункт 1 плана «до 20».

Контракт тот же, что у r7_ops.SpreadsheetOps: tests() отдаёт
[(имя теста, функция с .prepare)], воркер меряет каждую через
_measure_op_repeated. Подготовка (курсор в начало документа, вёрстка
досчитана) идёт вне замера, в замере — один вызов api.

Клавиатурного запасного пути у правок документа нет: «Вставка 100 страниц»
клавишами — сто нажатий, время которых ушло бы в замер, а «Поиск и
замена» без CDP требует слепой работы с диалогом (правило 8 CLAUDE.md).
Нет CDP или api не выполнил операцию — тест честно падает с причиной в
отчёте. Экспорт идёт общим путём «Сохранить как» (_save_as_format), как у
таблиц.

Модуль ничего не импортирует из r7_Testovarka (см. r7_ops).
"""
from r7.doc_fixtures import REPLACE_WORD, REPLACE_WITH
from r7_ops import _with_prepare

OPEN_TEST_NAME = "Повторное открытие файла"   # то же имя, что у таблиц (gate, наборы)

PAGES_TO_INSERT = 100
RESTYLE_NAMES = ("heading 2", "Heading 2", "Заголовок 2")

ADD_PAGES_TEST = f"Вставка {PAGES_TO_INSERT} страниц (разрывы)"
RESTYLE_TEST = "Смена стиля всего документа"
REPLACE_TEST = "Поиск и замена по документу"
EXPORT_PDF_TEST = "Сохранение в PDF (документ, x2t)"
EXPORT_DOCX_TEST = "Сохранение в DOCX (документ, x2t)"

# Порядок — порядок прогона. Экспорт DOCX последним: после «Сохранить как»
# Р7 продолжает работу уже с сохранённой копией.
DOCUMENT_TEST_DEFINITIONS = [
    OPEN_TEST_NAME,
    ADD_PAGES_TEST,
    RESTYLE_TEST,
    REPLACE_TEST,
    EXPORT_PDF_TEST,
    EXPORT_DOCX_TEST,
]

# Сколько повторов по умолчанию (экспорт — дольше, как у таблиц).
DEFAULT_DOC_RUNS = {name: (3 if name.startswith("Сохранение в") else 5)
                    for name in DOCUMENT_TEST_DEFINITIONS}


class DocumentOps:
    """Операции над открытым документом Р7.

    Args:
        app: экземпляр R7Testovarka — CDP-операции документа (`_doc_cdp_*`,
            r7/doc_run.py), подготовка (`_doc_prepare`), экспорт
            (`_save_as_format`), клавиши только для экспорта.
        find_hwnd: поиск окна Р7 (для экспорта).
        log_cb: журнал воркера.
        test_file: тестовый документ.
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
            raise RuntimeError(f"{what}: нет CDP или api документа не выполнил "
                               f"операцию; клавишами не повторяю (правило 8)")

    def add_pages(self, count=PAGES_TO_INSERT):
        """N пустых страниц в начало документа одним вызовом JS."""
        self._require(self.app._doc_cdp_add_pages(count, log_cb=self.log_cb),
                      f"вставка {count} страниц")

    def restyle_all(self, names=RESTYLE_NAMES):
        """Выделить всё и применить стиль заголовка ко всему документу."""
        self._require(self.app._doc_cdp_restyle_all(names, log_cb=self.log_cb),
                      "смена стиля документа")

    def replace_all(self, find_text, replace_with):
        """Заменить все вхождения слова одним asc_replaceText."""
        self._require(self.app._doc_cdp_replace_all(find_text, replace_with,
                                                    log_cb=self.log_cb),
                      "поиск и замена")

    def save_as_format(self, ext):
        """Экспорт через «Сохранить как» (_save_as_format, тип — по ext)."""
        self.app._save_as_format(ext, self.find_hwnd, self.hotkey, self.press,
                                 log_cb=self.log_cb)

    # ── список тестов ──────────────────────────────────────────────────────

    def _prep(self):
        return self.app._doc_prepare(log_cb=self.log_cb)

    def tests(self):
        """[(имя теста, функция с .prepare)] в порядке DOCUMENT_TEST_DEFINITIONS,
        без открытия файла (его меряет воркер)."""
        return [
            (ADD_PAGES_TEST, _with_prepare(lambda: self.add_pages(), self._prep)),
            (RESTYLE_TEST, _with_prepare(lambda: self.restyle_all(), self._prep)),
            (REPLACE_TEST, _with_prepare(
                lambda: self.replace_all(REPLACE_WORD, REPLACE_WITH), self._prep)),
            (EXPORT_PDF_TEST, _with_prepare(lambda: self.save_as_format("pdf"), self._prep)),
            (EXPORT_DOCX_TEST, _with_prepare(lambda: self.save_as_format("docx"), self._prep)),
        ]
