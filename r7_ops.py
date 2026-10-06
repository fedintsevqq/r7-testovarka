"""Тест-операции табличного редактора — один набор для обоих воркеров.

Прежде каждая операция (Ctrl+A, вставка большого массива, новый лист,
столбец, вставка ячеек, ВПР, удаление столбца, экспорт) жила вложенной
функцией дважды: в `_spreadsheet_worker` (вкладка «Производительность») и
в `_batch_run_single_version` (Batch). Копии приходилось зеркалить вручную,
а юнит-тесты вложенный код не видели — так проходили незамеченными
регрессии (docs/plan-to-8.md, этап 1).

Здесь — те же операции без изменений по сути: тот же CDP-путь, тот же
клавиатурный запасной путь, те же паузы через `_pace` (вычитаются из
замера). Оба воркера берут список `(имя теста, функция)` из `tests()`.

Модуль ничего не импортирует из r7_Testovarka: при запуске двойным кликом
тот работает как `__main__`, и обратный импорт загрузил бы вторую копию.
Всё нужное приходит через объект приложения `app`.
"""


def _with_prepare(func, prepare, cleanup=None):
    """Привязывает к тест-функции подготовку, которую _measure_op_repeated
    выполняет перед каждым повтором ВНЕ замера (рабочий лист, выделение,
    буфер обмена), и, если нужно, уборку после всех повторов (тоже вне
    замера). Возвращает саму func — удобно прямо в списке операций."""
    func.prepare = prepare
    if cleanup is not None:
        func.cleanup = cleanup
    return func


class SpreadsheetOps:
    """Операции над открытым документом Р7.

    Args:
        app: экземпляр R7Testovarka — источник CDP-операций (`_cdp_*`),
            клавиш (`_hotkey`/`_press`, только в окно Р7), пауз (`_pace`) и
            подготовок (`_prepare_on_work_sheet` и др.).
        find_hwnd: поиск окна Р7 (для экспорта).
        log_cb: журнал воркера.
        test_file: тестовый файл (подготовка ВПР читает его размеры).
    """

    def __init__(self, app, find_hwnd, log_cb, test_file):
        self.app = app
        self.find_hwnd = find_hwnd
        self.log_cb = log_cb
        self.test_file = test_file
        self.key_pace = app.OP_KEY_PACE

    # ── клавиши (только в окно Р7, см. _ensure_r7_foreground) ──────────────

    def hotkey(self, *keys):
        """Сочетание клавиш без задержек между нажатиями: interval попадал
        бы в замер. Нужная пауза — явно через _pace."""
        self.app._hotkey(*keys)

    def press(self, key, presses=1, pace=0.0):
        """Клавиша один или несколько раз; pace — пауза между нажатиями
        через _pace (вычитается из замера), нужна в навигации по меню."""
        for _ in range(presses):
            self.app._press(key)
            if pace:
                self.app._pace(pace)

    # ── операции ───────────────────────────────────────────────────────────

    def select_all(self):
        """Ctrl+A с укороченным предохранителем.

        Выделив весь лист, Р7 пересчитывает агрегаты статусной строки по всем
        ячейкам и на большом файле держит CPU занятым десятками секунд. С
        общими OP_MAX_WAIT_SEC=180 это выглядело как зависание инструмента,
        поэтому ожидание ограничено OP_SELECT_ALL_MAX_SEC: не успел — честный
        timeout, прогон идёт дальше.
        """
        app = self.app
        app._op_max_wait = app.OP_SELECT_ALL_MAX_SEC
        if app._cdp_select_all(log_cb=self.log_cb):
            return
        self.hotkey('ctrl', 'a')

    def copy_all(self):
        """Ctrl+C по текущему выделению (после подготовки — по всему листу)."""
        if self.app._cdp_copy(log_cb=self.log_cb):
            return
        self.hotkey('ctrl', 'c')

    def paste_big(self):
        """Вставка большого массива на свежий лист (подготовка — _paste_big_prepare)."""
        app = self.app
        if app._cdp_paste_big(log_cb=self.log_cb, key_pace=self.key_pace):
            return
        if not getattr(app, "_paste_sheet_prepared", False):
            self.hotkey('shift', 'f11')
            app._pace(self.key_pace)          # даём создаться новому листу
        self.hotkey('ctrl', 'v')

    def add_sheet(self):
        """Новый лист: asc_addWorksheet через CDP либо Shift+F11."""
        if self.app._cdp_add_sheet(log_cb=self.log_cb):
            return
        self.hotkey('shift', 'f11')

    def add_column(self, method='hotkey'):
        """Столбец перед B. На CDP-пути оба варианта («горячие клавиши» и
        «меню Вставка») — один вызов asc_insertCells, меню там нет; запасной
        путь — диалог «Вставить ячейки» или контекстное меню."""
        app = self.app
        if app._cdp_add_column(log_cb=self.log_cb, key_pace=self.key_pace):
            return
        app._add_column_ui(method, self.hotkey, self.press, log_cb=self.log_cb)

    def copy_paste_hotkey(self, cell_count, paste_offset):
        """Скопировать A1:<N>1 и вставить правее хоткеями (CDP: мимо фокуса)."""
        app = self.app
        if app._cdp_copy_paste(cell_count, paste_offset, log_cb=self.log_cb,
                               key_pace=self.key_pace):
            return
        self.hotkey('ctrl', 'home')
        for _ in range(cell_count - 1):
            app._hotkey('shift', 'right')
        self.hotkey('ctrl', 'c')
        app._pace(self.key_pace)              # даём буферу обмена наполниться
        app._press('right', presses=paste_offset)
        self.hotkey('ctrl', 'v')

    def copy_paste_context(self, cell_count, paste_offset):
        """То же, но вставка со сдвигом вниз (CDP: asc_insertCells + asc_Paste);
        запасной путь — контекстное меню у выделения."""
        app = self.app
        if app._cdp_copy_paste(cell_count, paste_offset, shift="down",
                               log_cb=self.log_cb, key_pace=self.key_pace):
            return
        app._context_menu_copy_paste(cell_count, paste_offset, self.hotkey, self.press,
                                     log_cb=self.log_cb)

    def vlookup(self):
        """ВПР по 50K строк: вставка подготовленных формул (_vlookup_prepare/_vlookup_op)."""
        self.app._vlookup_op(log_cb=self.log_cb)

    def del_column(self):
        """Удаление столбца B целиком (_del_column_op)."""
        self.app._del_column_op(log_cb=self.log_cb)

    def save_as_format(self, ext):
        """Экспорт через «Сохранить как» (_save_as_format)."""
        self.app._save_as_format(ext, self.find_hwnd, self.hotkey, self.press,
                                 log_cb=self.log_cb)

    # ── список тестов ──────────────────────────────────────────────────────

    def _prep_ws(self, ref=None):
        return self.app._prepare_on_work_sheet(ref, log_cb=self.log_cb)

    def tests(self):
        """[(имя теста, функция с .prepare)] в порядке TEST_DEFINITIONS (без
        «Повторного открытия файла» — его воркеры меряют сами).

        У каждого теста правки своя подготовка вне замера: лист и выделение
        заданы явно, результат не зависит от соседних тестов и от того, какие
        из них отмечены (30.09.2026). Ctrl+A и Ctrl+C — тоже на рабочем листе:
        иначе копировался лист, с которым Р7 открыл файл (в фикстуре — «2» с
        автофильтром).
        """
        app, log = self.app, self.log_cb
        return [
            # Связанному методу атрибут .prepare не присвоить — поэтому лямбды.
            ("Выделение всех ячеек (Ctrl+A)",
             _with_prepare(lambda: self.select_all(), lambda: self._prep_ws("A1"))),
            ("Копирование всех ячеек (Ctrl+C)",
             _with_prepare(lambda: self.copy_all(),
                           lambda: app._prepare_select_all_on_work_sheet(log_cb=log))),
            ("Вставка большого массива (Ctrl+V)",
             _with_prepare(lambda: self.paste_big(), lambda: app._paste_big_prepare(log_cb=log),
                           cleanup=lambda: app._paste_big_cleanup(log_cb=log))),
            ("Добавление нового листа",
             _with_prepare(lambda: self.add_sheet(), lambda: self._prep_ws("A1"))),
            ("Добавление столбца (горячие клавиши)",
             _with_prepare(lambda: self.add_column('hotkey'), lambda: self._prep_ws("B1"))),
            ("Добавление столбца (меню Вставка)",
             _with_prepare(lambda: self.add_column('menu'), lambda: self._prep_ws("B1"))),
            ("Вставка 1 ячейки (горячие клавиши)",
             _with_prepare(lambda: self.copy_paste_hotkey(1, 10), self._prep_ws)),
            ("Вставка 5 ячеек (горячие клавиши)",
             _with_prepare(lambda: self.copy_paste_hotkey(5, 15), self._prep_ws)),
            ("Вставка 1 ячейки (ПКМ)",
             _with_prepare(lambda: self.copy_paste_context(1, 10), self._prep_ws)),
            ("Вставка 5 ячеек (ПКМ)",
             _with_prepare(lambda: self.copy_paste_context(5, 15), self._prep_ws)),
            ("Функция ВПР (50K строк)",
             _with_prepare(lambda: self.vlookup(),
                           lambda: app._vlookup_prepare(self.test_file, log_cb=log))),
            ("Удаление столбца (Del)",
             _with_prepare(lambda: self.del_column(), lambda: app._del_column_prepare(log_cb=log))),
            ("Сохранение в PDF (конвертация x2t)", lambda: self.save_as_format('pdf')),
            ("Сохранение в ODS (конвертация x2t)", lambda: self.save_as_format('ods')),
            ("Сохранение в CSV (конвертация x2t)", lambda: self.save_as_format('csv')),
            ("Сохранение в XLTX (конвертация x2t)", lambda: self.save_as_format('xltx')),
        ]
