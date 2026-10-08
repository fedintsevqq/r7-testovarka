"""Подготовки тестов вне замера: рабочий лист и выделение, свежий лист и
копия листа в буфере для вставки большого массива, формулы ВПР, столбец
для удаления (правило 3 CLAUDE.md: у каждого теста — своя подготовка).

TestPrepMixin — методы, которые R7Testovarka получает наследованием.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from r7.cdp import _col_letter
from r7.env import pyperclip

LogCb = Callable[[str], object]
# Сведения о рабочем листе: {index, name, rows, cols} (см. _work_sheet).
SheetInfo = dict[str, Any]


class TestPrepHost(Protocol):
    """Что методы TestPrepMixin берут у приложения (R7Testovarka: CdpMixin,
    OpEndMixin, MeasureMixin, WindowsMixin, ExportMixin, FixturesMixin,
    окно журнала) и у самой примеси. Нужен только mypy: так проверяется
    примесь, а не весь класс приложения."""

    CDP_OP_TIMEOUT_SEC: float
    CDP_LONG_OP_TIMEOUT_SEC: float
    OP_KEY_PACE: float

    # Состояние подготовок; до первой подготовки части атрибутов нет —
    # читаются через getattr с умолчанием.
    _prepared_on_ws: bool
    _work_sheet_logged: set[str]
    _paste_sheet_prepared: bool
    _sheet_clip_seq: int | None
    _paste_sheet_base: int | None
    _paste_sheet_mark: tuple[Any, Any] | None
    _vlookup_rows_cache: int
    _vlookup_clip_text: str
    _vlookup_clip_key: tuple[str, int]

    def add_test_log(self, msg: str) -> None: ...

    def _cdp_ops_connector(self) -> Any: ...

    def _cdp_settle(self, connector: Any = None, max_wait: float = 5.0) -> bool: ...

    def _cdp_sequence(self, label: str, steps: Sequence[tuple[Any, ...]],
                      checker: Callable[[Any, Any], Any] | None = None,
                      log_cb: LogCb | None = None) -> Any: ...

    def _cdp_check_document_changed(self, before: Any, after: Any) -> Any: ...

    def _restore_history(self, before: Any, label: str, hwnd: int | None = None,
                         log_cb: LogCb | None = None) -> bool | None: ...

    def _wait_operation_done(self, hwnd: int | None, log_cb: LogCb | None = None,
                             start_grace: float | None = None) -> Any: ...

    def _clipboard_seq(self) -> int | None: ...

    def _note_interference(self, kind: str) -> None: ...

    def _get_xlsx_row_count(self, path: Any) -> int | None: ...

    def _hotkey(self, *keys: str) -> None: ...

    def _press(self, key: str, presses: int = 1) -> None: ...

    def _pace(self, seconds: float) -> None: ...

    def _work_sheet(self, log_cb: LogCb | None = None) -> SheetInfo | None: ...

    def _prepare_on_work_sheet(self, select_ref: str | None = None,
                               log_cb: LogCb | None = None) -> SheetInfo | None: ...

    def _prepare_select_all_on_work_sheet(self, log_cb: LogCb | None = None) -> SheetInfo | None: ...


class TestPrepMixin:
    """Подготовки тестов вне замера — часть R7Testovarka."""

    # Журнал «рабочий лист выбран» пишется раз на лист; до первой подготовки
    # атрибута нет — читается через getattr с умолчанием.
    _work_sheet_logged: set[str]

    def _work_sheet(self: TestPrepHost, log_cb: LogCb | None = None) -> SheetInfo | None:
        """Рабочий лист для тестов правки — детерминированно, по модели книги.

        Самый большой лист без автофильтра, при равенстве — левый. В рабочей
        фикстуре это лист «1» (50 001 строка), лист «2» с автофильтром
        пропускается. Не зависит от того, где оказался курсор после
        предыдущих операций цепочки.

        Returns:
            dict | None: {index, name, rows, cols}, либо None без CDP.
        """
        connector = self._cdp_ops_connector()
        if connector is None:
            return None
        try:
            sheets = connector.sheets_info(timeout=self.CDP_OP_TIMEOUT_SEC)
        except Exception:
            return None
        cand: list[SheetInfo] = [s for s in (sheets or [])
                if s.get("autofilter") is False and isinstance(s.get("rows"), int)]
        if not cand:
            return None
        return max(cand, key=lambda s: (s["rows"], -s["index"]))

    def _prepare_on_work_sheet(self: TestPrepHost, select_ref: str | None = None,
                               log_cb: LogCb | None = None) -> SheetInfo | None:
        """Подготовка теста правки: переход на рабочий лист и выделение.

        Returns:
            dict | None: сведения о листе (см. _work_sheet), None — без CDP;
            тогда тест идёт на активном листе клавиатурным путём.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        self._prepared_on_ws = False     # запасной клавиатурный путь навигирует сам
        ws = self._work_sheet(log_cb)
        seen: set[str] | None = getattr(self, "_work_sheet_logged", None)
        if seen is None:
            seen = self._work_sheet_logged = set()
        if ws is None:
            if "none" not in seen:
                seen.add("none")
                log_cb("   ⚠️ Рабочий лист не выбран (нет CDP) — тесты правки идут "
                       "на активном листе, результат зависит от предыдущих операций")
            return None
        connector = self._cdp_ops_connector()
        # Ответ проверяется: прежде он отбрасывался, и тест шёл на чужом листе
        # или с чужим выделением (аудит 06.10.2026). Исключение здесь
        # _measure_op_repeated превращает в ошибку теста.
        res = connector.show_sheet(ws["index"], timeout=self.CDP_OP_TIMEOUT_SEC)
        if not (res and res.get("ok")):
            raise RuntimeError(f"не открылся рабочий лист «{ws['name']}» "
                               f"({(res or {}).get('reason') or 'нет ответа CDP'})")
        if select_ref:
            res = connector.select_range(select_ref, timeout=self.CDP_LONG_OP_TIMEOUT_SEC)
            if not (res and res.get("ok")):
                raise RuntimeError(f"не выделился диапазон {select_ref} "
                                   f"({(res or {}).get('reason') or 'нет ответа CDP'})")
        if ws["name"] not in seen:
            seen.add(ws["name"])
            log_cb(f"   📄 Рабочий лист тестов правки: «{ws['name']}» "
                   f"({ws['rows']} строк, {ws['cols']} столбцов, без автофильтра)")
        self._prepared_on_ws = True
        return ws

    def _paste_big_prepare(self: TestPrepHost, log_cb: LogCb | None = None) -> None:
        """Подготовка «Вставки большого массива»: в буфере — копия всего
        рабочего листа, активен пустой лист. В замере остаётся одна вставка.

        Раньше в замер входило создание листа (~0.7 с), а содержимое буфера
        зависело от соседей: без теста «Копирование всех ячеек» вставлялось
        то, что оказалось в буфере. Буфер проверяется по номеру состояния
        буфера Windows (_clipboard_seq), запомненному после копирования
        листа: совпал — копия на месте, кто бы ни работал между тестами.

        Лист — новый на КАЖДЫЙ повтор. После отката вставки лист пуст, но уже
        не свеж: вставка в него идёт 22.2 с против 20.3 с на новом листе
        (живой замер 30.09.2026; очистка redo и сборка мусора не помогают).
        Поэтому оставшийся от прошлого повтора лист убирается откатом его
        создания, и создаётся новый: 20.1 / 20.7 / 20.5 / 20.7 с.
        Без CDP — ничего: клавиатурный путь сам жмёт Shift+F11.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        self._paste_sheet_prepared = False
        connector = self._cdp_ops_connector()
        if connector is None:
            return
        seq = self._clipboard_seq()
        if seq is None or seq != getattr(self, "_sheet_clip_seq", None):
            if getattr(self, "_sheet_clip_seq", None) is not None and seq is not None:
                # Копию листа уже делали — буфер перезаписал кто-то другой
                # (ночной прогон 07.10.2026: совпало с удвоением экспорта XLTX).
                self._note_interference("clipboard_foreign")
            if self._prepare_select_all_on_work_sheet(log_cb=log_cb) is None:
                return
            log_cb("   📋 В буфере обмена нет копии листа — копирую рабочий лист (вне замера)")
            res = connector.copy(timeout=self.CDP_LONG_OP_TIMEOUT_SEC)
            if not (isinstance(res, dict) and res.get("ok")):
                return
            self._cdp_settle(connector)
            self._sheet_clip_seq = self._clipboard_seq()
        st = connector.document_state(timeout=self.CDP_OP_TIMEOUT_SEC) or {}
        mark = getattr(self, "_paste_sheet_mark", None)
        base = getattr(self, "_paste_sheet_base", None)
        if (mark is not None and isinstance(base, int)
                and mark == (st.get("active"), st.get("historyIndex"))):
            # Наш лист от прошлого повтора (вставка откатана) — убираем его.
            connector.undo_to(base, timeout=self.CDP_LONG_OP_TIMEOUT_SEC)
        self._paste_sheet_mark = None
        # Новый лист — всегда от рабочего: время и место вставки листа
        # зависят от того, какой лист активен.
        if self._prepare_on_work_sheet("A1", log_cb=log_cb) is None:
            return
        res = connector.add_sheet(timeout=self.CDP_OP_TIMEOUT_SEC)
        if not (isinstance(res, dict) and res.get("ok")):
            return
        after = res.get("after") or {}
        self._paste_sheet_base = (res.get("before") or {}).get("historyIndex")
        self._paste_sheet_mark = (after.get("active"), after.get("historyIndex"))
        self._cdp_settle(connector)
        self._paste_sheet_prepared = True

    def _paste_big_restore(self: TestPrepHost, before: Any, label: str, hwnd: int | None = None,
                           log_cb: LogCb | None = None) -> bool | None:
        """Откат повтора «Вставки большого массива» — удалением листа вставки.

        Общий откат (_restore_history) отменял вставку через историю: 17–22 с
        вне замера на каждый повтор, больше половины всего времени теста.
        Лист создан подготовкой только под эту вставку, и удалить его —
        0,8 с (живая проба 08.10.2026); следующая подготовка всё равно
        создаёт новый лист. Содержимое книги после удаления то же, что до
        подготовки; история правок после удаления очищается (иначе в ней
        копятся удалённые листы со всей вставкой).

        Удаляется только наш лист: активен он, и повтор начался ровно с
        состояния, которое оставила подготовка. Иначе — общий откат.

        Returns:
            bool | None: как у _restore_history.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        mark = getattr(self, "_paste_sheet_mark", None)
        connector = self._cdp_ops_connector()
        if connector is None or mark is None or before is None:
            return self._restore_history(before, label, hwnd, log_cb=log_cb)
        sheet, mark_idx = mark
        st = connector.document_state(timeout=self.CDP_OP_TIMEOUT_SEC) or {}
        if not (isinstance(sheet, int) and st.get("active") == sheet
                and before.get("index") == mark_idx):
            return self._restore_history(before, label, hwnd, log_cb=log_cb)
        t0 = time.perf_counter()
        res = connector.delete_sheet(sheet, timeout=self.CDP_LONG_OP_TIMEOUT_SEC)
        if not (isinstance(res, dict) and res.get("ok")):
            log_cb(f"   ⚠️ {label}: лист вставки не удалился "
                   f"({(res or {}).get('reason') if isinstance(res, dict) else 'нет ответа CDP'})"
                   f" — откатываю через историю")
            return self._restore_history(before, label, hwnd, log_cb=log_cb)
        self._paste_sheet_mark = None
        self._paste_sheet_prepared = False
        # Удалённый лист остаётся в истории правок целиком: через несколько
        # повторов первое открытие панели «Файл» не укладывалось в 5 с, и
        # следующий экспорт падал (живой прогон 08.10.2026). Все правки до
        # этого места уже откатаны, отменять в истории нечего — чистим.
        cleared = connector.clear_history(timeout=self.CDP_OP_TIMEOUT_SEC)
        if not (isinstance(cleared, dict) and cleared.get("ok")):
            log_cb(f"   ⚠️ {label}: историю правок очистить не удалось — удалённые "
                   f"листы вставки остаются в памяти Р7")
        self._wait_operation_done(hwnd, log_cb=log_cb, start_grace=0.3)
        log_cb(f"   🧹 {label}: лист вставки удалён "
               f"({(time.perf_counter() - t0) * 1000:.0f} мс, вне замера)")
        return True

    def _paste_big_cleanup(self: TestPrepHost, log_cb: LogCb | None = None) -> None:
        """После всех повторов «Вставки большого массива» убирает лист,
        созданный _paste_big_prepare (вставка на нём уже откатана).

        Пустой, но «несвежий» лист утяжелял документ для следующих тестов:
        экспорт XLTX шёл 11.5 с против 5.5 с на файле как есть (живой замер
        07.10.2026). Убирается только наш лист — тот же признак, что в
        подготовке: активный лист и позиция в истории совпали с запомненными.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        mark = getattr(self, "_paste_sheet_mark", None)
        base = getattr(self, "_paste_sheet_base", None)
        self._paste_sheet_mark = None
        self._paste_sheet_prepared = False
        connector = self._cdp_ops_connector()
        if connector is None or mark is None or not isinstance(base, int):
            return
        st = connector.document_state(timeout=self.CDP_OP_TIMEOUT_SEC) or {}
        if mark != (st.get("active"), st.get("historyIndex")):
            log_cb("   ⚠️ Лист вставки не убран: документ после теста не тот, "
                   "что оставила подготовка")
            return
        connector.undo_to(base, timeout=self.CDP_LONG_OP_TIMEOUT_SEC)
        self._cdp_settle(connector)
        log_cb("   🧹 Лист вставки убран (вне замера)")

    def _prepare_select_all_on_work_sheet(self: TestPrepHost,
                                          log_cb: LogCb | None = None) -> SheetInfo | None:
        """Подготовка «Копирования всех ячеек»: рабочий лист, весь лист
        выделен. Копирование не должно зависеть от того, что выделил
        предыдущий тест. Без CDP — ничего (тест идёт на активном листе).
        """
        ws = self._prepare_on_work_sheet(log_cb=log_cb)
        if ws is not None:
            self._cdp_ops_connector().select_all(timeout=self.CDP_LONG_OP_TIMEOUT_SEC)
        return ws

    def _vlookup_prepare(self: TestPrepHost, test_file: Any = None,
                         log_cb: LogCb | None = None) -> None:
        """Подготовка ВПР: 50 000 формул в буфер, курсор на свободный столбец.

        Формула =VLOOKUP(A<i>,$A$2:$B$<n>,2,FALSE) для каждой строки данных —
        по строке на ячейку: вставка одной формулы в диапазон заполняет
        только первую ячейку (проверено вживую), а Ctrl+D Р7 не принимает.
        Ищет по первому столбцу того же листа и отдаёт второй: результат
        проверен по модели — AY2=991, AY50001=7, как в столбце B.
        """
        ws = self._prepare_on_work_sheet(log_cb=log_cb)
        if ws is not None:
            rows = ws["rows"]
            self._cdp_ops_connector().select_range(
                f"{_col_letter(ws['cols'] + 2)}2", timeout=self.CDP_OP_TIMEOUT_SEC)
        else:
            rows = getattr(self, "_vlookup_rows_cache", None)
            if rows is None and test_file is not None:
                rows = (self._get_xlsx_row_count(test_file) or 50_000) + 1
                self._vlookup_rows_cache = rows
            rows = rows or 50_001
            # Клавиатурный путь: свободный столбец справа от данных, строка 2.
            self._hotkey('ctrl', 'home')
            self._hotkey('ctrl', 'right')
            self._press('right', presses=2)
            self._press('down')
        key = ("vlookup", rows)
        if getattr(self, "_vlookup_clip_key", None) != key:
            self._vlookup_clip_text = "\r\n".join(
                f"=VLOOKUP(A{i},$A$2:$B${rows},2,FALSE)" for i in range(2, rows + 1))
            self._vlookup_clip_key = key
        pyperclip.copy(self._vlookup_clip_text)

    def _vlookup_op(self: TestPrepHost, log_cb: LogCb | None = None) -> None:
        """ВПР: вставка подготовленных формул — одна операция, в замере."""
        if self._cdp_sequence(
                "ВПР (вставка формул)",
                [("asc_Paste", lambda c, t: c.paste(timeout=t),
                  self.CDP_LONG_OP_TIMEOUT_SEC, 0)],
                self._cdp_check_document_changed, log_cb):
            return
        self._hotkey('ctrl', 'v')

    def _del_column_prepare(self: TestPrepHost, log_cb: LogCb | None = None) -> None:
        """Подготовка удаления столбца: рабочий лист, курсор в B1."""
        if self._prepare_on_work_sheet("B1", log_cb=log_cb) is None:
            self._hotkey('ctrl', 'home')
            self._press('right')

    def _del_column_op(self: TestPrepHost, log_cb: LogCb | None = None) -> None:
        """Удаление столбца B целиком (прежний тест очищал одну ячейку B1)."""
        if self._cdp_sequence(
                "Удаление столбца",
                [("asc_deleteCells(DeleteColumns)",
                  lambda c, t: c.delete_columns(timeout=t),
                  self.CDP_LONG_OP_TIMEOUT_SEC, 0)],
                self._cdp_check_document_changed, log_cb):
            return
        self._hotkey('ctrl', 'space')        # выделить столбец
        self._pace(self.OP_KEY_PACE)
        self._hotkey('ctrl', '-')            # удалить выделенный столбец
