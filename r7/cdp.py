"""Операции через api редактора (CDP): шаги и цепочки asc_*, проверки
результата, откат истории между повторами, подготовки тестов, автосохранение.

Правило 7 CLAUDE.md: изменяющий документ шаг — последний в цепочке; откат
на клавиши — только если документ гарантированно не тронут (mutated из JS).
None от evaluate() может значить, что операция уже ушла в Р7. CdpMixin —
методы, которые R7Testovarka получает наследованием; пороги CDP_* пока
остаются константами R7Testovarka.
"""
import re
import time

from r7.env import pyperclip


def _col_letter(index):
    """Буквенное имя столбца по его номеру: 1 → A, 5 → E, 27 → AA.

    Нужно для CDP-пути тестов вставки: клавиатурная версия ходит по листу
    стрелками (Ctrl+Home, затем N раз «вправо»), а api адресует ячейки
    ссылками вида "A1:E1" — их и собирает эта функция.

    Args:
        index: Номер столбца, начиная с 1. Значения меньше 1 приводятся к 1.

    Returns:
        str: Буквенное имя столбца.
    """
    if index < 1:
        index = 1
    name = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        name = chr(ord("A") + rem) + name
    return name


class CdpMixin:
    """CDP-операции, проверки, откат и подготовки тестов — часть R7Testovarka."""

    def _history_snapshot(self, log_cb=None):
        """Состояние документа перед повтором: позиция в истории правок,
        активный лист и выделение.

        Выделение и лист в историю правок не пишутся, и одного отката по
        History.Index мало: после первого прогона «Выделения всех ячеек» весь
        лист уже выделен, следующие asc_EditSelectAll ничего не делали (api
        0 мс), а асинхронный хвост первого прогона попадал во второй — 0.000
        и 1.235 с на одной операции (живой прогон 29.09.2026).

        Returns:
            dict | None: {"index", "active", "selection"}, либо None, если CDP
            недоступен или api не отдал историю — откатить прогон нечем.
        """
        connector = self._cdp_ops_connector()
        if connector is None:
            return None
        try:
            st = connector.document_state(timeout=self.CDP_OP_TIMEOUT_SEC)
        except Exception:
            return None
        idx = (st or {}).get("historyIndex")
        if not isinstance(idx, int):
            return None
        return {"index": idx, "active": st.get("active"), "selection": st.get("selection")}

    def _restore_history(self, before, label, hwnd=None, log_cb=None):
        """Откатывает правки повтора, чтобы следующий повтор той же операции
        работал с тем же документом (аудит 29.09.2026, пункт 4).

        Вызывается ВНЕ замера. После отката ждёт, пока Р7 освободится: отмена
        большой вставки — тоже работа, и она не должна попасть в следующий
        замер.

        После отката возвращаются активный лист и выделение (см.
        _history_snapshot) — их откат по истории не трогает.

        Args:
            before: результат _history_snapshot() до прогона.
            label: имя операции для лога.
            hwnd: окно Р7 или функция его поиска — для ожидания простоя.
            log_cb: функция логирования.

        Returns:
            bool | None: True — документ возвращён (или не менялся);
            False — откатить не удалось; None — CDP недоступен, откат
            невозможен в принципе.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if before is None:
            if not getattr(self, "_restore_unavailable_logged", False):
                self._restore_unavailable_logged = True
                log_cb("   ⚠️ Откат правок между прогонами недоступен (нет CDP): "
                       "прогоны работают с накопленными изменениями документа, "
                       "цифры повторов зависимы")
            return None
        connector = self._cdp_ops_connector()
        if connector is None:
            return False
        try:
            after = connector.document_state(timeout=self.CDP_OP_TIMEOUT_SEC)
        except Exception:
            after = None
        before_idx = before["index"]
        cur = (after or {}).get("historyIndex")
        if not (isinstance(cur, int) and cur <= before_idx):
            try:
                res = connector.undo_to(before_idx, timeout=self.OP_MAX_WAIT_SEC)
            except Exception as e:
                log_cb(f"   ⚠️ {label}: откат правок упал — {e}")
                return False
            if not (res and res.get("reached")):
                log_cb(f"   ⚠️ {label}: документ не вернулся к исходному состоянию "
                       f"(ответ: {res}) — следующие замеры идут на изменённом документе")
                return False
            log_cb(f"   ↩️ {label}: отменено шагов {res.get('steps')} "
                   f"({res.get('undo_ms', 0):.0f} мс, вне замера)")
            after = (res or {}).get("after") or after

        # Лист и выделение — вне истории правок, возвращаем отдельно.
        view_ok = True
        want_active, want_sel = before.get("active"), before.get("selection")
        try:
            if isinstance(want_active, int) and (after or {}).get("active") != want_active:
                connector.show_sheet(want_active, timeout=self.CDP_OP_TIMEOUT_SEC)
            if want_sel and (after or {}).get("selection") != want_sel:
                r = connector.select_range(want_sel, timeout=self.CDP_LONG_OP_TIMEOUT_SEC)
                view_ok = bool(r and r.get("ok"))
        except Exception:
            view_ok = False
        if not view_ok:
            log_cb(f"   ⚠️ {label}: выделение {want_sel} не восстановлено — "
                   f"следующий повтор начнётся с другого выделения")
        self._wait_operation_done(hwnd, log_cb=log_cb, start_grace=0.3)
        return view_ok

    def _suspend_autosave(self, log_cb=None):
        """Отключает автосохранение Р7 на время прогона (пункт 11 аудита).

        Иначе запись файла восстановления (через ~1 с после каждой правки) и
        периодическое автосохранение (раз в 10 мин) попадают посреди замера.
        Состояние запоминается в self._autosave_state для _restore_autosave.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        self._autosave_state = None
        connector = self._cdp_ops_connector()
        if connector is None:
            log_cb("   ⚠️ Автосохранение Р7 не отключено (нет CDP) — его запись "
                   "может попасть в замеры")
            return
        try:
            state = connector.suspend_autosave(timeout=self.CDP_OP_TIMEOUT_SEC)
        except Exception as e:
            state = None
            log_cb(f"   ⚠️ Автосохранение Р7 не отключено ({type(e).__name__}: {e}) — "
                   f"его запись может попасть в замеры")
        else:
            if not state:
                # Прежде этот случай проходил молча (аудит 06.10.2026).
                log_cb("   ⚠️ Автосохранение Р7 не отключено (api не ответил) — его "
                       "запись может попасть в замеры")
        if state:
            self._autosave_state = state
            log_cb(f"💾 Автосохранение Р7 отключено на время замеров "
                   f"(было: правки раз в {(state.get('gap_ms') or 0) / 1000:.0f} с, "
                   f"периодическое {'вкл' if state.get('periodic') else 'выкл'})")

    def _restore_autosave(self, log_cb=None):
        """Возвращает автосохранение Р7 — ДО закрытия Р7, пока жив CDP.

        Периодическое автосохранение — настройка пользователя (localStorage),
        оставить её выключенной нельзя. Повторный вызов ничего не делает.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        state = getattr(self, "_autosave_state", None)
        if not state:
            return
        self._autosave_state = None
        connector = self._cdp_ops_connector()
        ok = False
        if connector is not None:
            try:
                ok = connector.restore_autosave(state, timeout=self.CDP_OP_TIMEOUT_SEC)
            except Exception:
                ok = False
        if ok:
            # CEF пишет localStorage на диск с задержкой, а Р7 закрывается
            # сразу следом: без паузы восстановленный флаг терялся, и
            # периодическое автосохранение пользователя оставалось выключенным
            # (живой прогон 29.09.2026; с паузой 1.5 с флаг пережил перезапуск).
            time.sleep(self.AUTOSAVE_RESTORE_FLUSH_SEC)
            log_cb("💾 Автосохранение Р7 возвращено")
        elif state.get("periodic"):
            log_cb("❌ Не удалось вернуть периодическое автосохранение Р7 — включите "
                   "его вручную: Файл → Дополнительные параметры")

    def _work_sheet(self, log_cb=None):
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
        cand = [s for s in (sheets or [])
                if s.get("autofilter") is False and isinstance(s.get("rows"), int)]
        if not cand:
            return None
        return max(cand, key=lambda s: (s["rows"], -s["index"]))

    def _prepare_on_work_sheet(self, select_ref=None, log_cb=None):
        """Подготовка теста правки: переход на рабочий лист и выделение.

        Returns:
            dict | None: сведения о листе (см. _work_sheet), None — без CDP;
            тогда тест идёт на активном листе клавиатурным путём.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        self._prepared_on_ws = False     # запасной клавиатурный путь навигирует сам
        ws = self._work_sheet(log_cb)
        seen = getattr(self, "_work_sheet_logged", None)
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

    def _paste_big_prepare(self, log_cb=None):
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

    def _paste_big_cleanup(self, log_cb=None):
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

    def _prepare_select_all_on_work_sheet(self, log_cb=None):
        """Подготовка «Копирования всех ячеек»: рабочий лист, весь лист
        выделен. Копирование не должно зависеть от того, что выделил
        предыдущий тест. Без CDP — ничего (тест идёт на активном листе).
        """
        ws = self._prepare_on_work_sheet(log_cb=log_cb)
        if ws is not None:
            self._cdp_ops_connector().select_all(timeout=self.CDP_LONG_OP_TIMEOUT_SEC)
        return ws

    def _vlookup_prepare(self, test_file=None, log_cb=None):
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

    def _vlookup_op(self, log_cb=None):
        """ВПР: вставка подготовленных формул — одна операция, в замере."""
        if self._cdp_sequence(
                "ВПР (вставка формул)",
                [("asc_Paste", lambda c, t: c.paste(timeout=t),
                  self.CDP_LONG_OP_TIMEOUT_SEC, 0)],
                self._cdp_check_document_changed, log_cb):
            return
        self._hotkey('ctrl', 'v')

    def _del_column_prepare(self, log_cb=None):
        """Подготовка удаления столбца: рабочий лист, курсор в B1."""
        if self._prepare_on_work_sheet("B1", log_cb=log_cb) is None:
            self._hotkey('ctrl', 'home')
            self._press('right')

    def _del_column_op(self, log_cb=None):
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

    def _cdp_ops_connector(self):
        """Коннектор, готовый выполнять операции, либо None.

        Returns:
            R7WebDriverConnector | None
        """
        if not self.CDP_OPS_ENABLED:
            return None
        connector = self._webdriver_connector
        if connector is None or not getattr(connector, "connected", False):
            return None
        return connector

    def _cdp_step(self, caption, fn, log_cb, timeout=None):
        """Выполняет один вызов коннектора и классифицирует результат.

        Args:
            caption: Подпись шага для лога (обычно имя asc_-метода).
            fn: callable(connector, timeout) -> dict | None.
            log_cb: Функция логирования.
            timeout: Таймаут ожидания ответа CDP, сек.

        Returns:
            tuple[str, dict | None]: статус и ответ JS. Статусы:
              "ok"          — выполнено, можно идти дальше;
              "failed"      — не выполнено И документ не тронут, безопасно
                              повторить операцию клавишами;
              "unknown"     — ответа нет или сбой уже после изменения
                              документа: повторять клавишами НЕЛЬЗЯ;
              "unavailable" — CDP в этом запуске недоступен.
        """
        connector = self._cdp_ops_connector()
        if connector is None:
            return ("unavailable", None)
        try:
            res = fn(connector, timeout)
        except Exception as e:
            # Где именно упало — до отправки JS или после — не узнать, а
            # повтор клавишами разрешён, только если документ гарантированно
            # не тронут (правило 7). Поэтому «неизвестно», а не «не выполнено».
            log_cb(f"   ⚠️ CDP «{caption}»: исключение — {type(e).__name__}: {e}; "
                   f"клавишами не повторяю")
            return ("unknown", None)

        if res is None:
            # None от evaluate() означает «неизвестно», а не «не выполнено».
            # Если соединение живо — почти наверняка вызов не уложился в
            # таймаут сокета, а JS всё это время работал: операция ушла в Р7,
            # и дублировать её клавишами нельзя.
            if getattr(connector, "connected", False):
                waited = f"за {timeout:.0f} с" if timeout else "в отведённое время"
                log_cb(f"   ⚠️ CDP «{caption}»: ответ не пришёл {waited}, "
                       f"соединение живо — считаю операцию отправленной")
                return ("unknown", None)
            log_cb(f"   ⚠️ CDP «{caption}»: соединение потеряно — откат на клавиши")
            return ("failed", None)

        if not isinstance(res, dict):
            log_cb(f"   ⚠️ CDP «{caption}»: неожиданный ответ {res!r} — откат на клавиши")
            return ("failed", None)

        if res.get("ok"):
            return ("ok", res)

        reason = res.get("reason") or "?"
        detail = res.get("error")
        suffix = f": {detail}" if detail else ""
        if res.get("mutated"):
            log_cb(f"   ⚠️ CDP «{caption}»: сбой уже ПОСЛЕ изменения документа "
                   f"({reason}{suffix}) — повтор клавишами отменён, иначе правка "
                   f"применилась бы дважды")
            return ("unknown", res)
        log_cb(f"   ⚠️ CDP «{caption}»: не выполнено ({reason}{suffix}) — откат на клавиши")
        return ("failed", res)

    def _cdp_sequence(self, label, steps, checker=None, log_cb=None):
        """Выполняет операцию как цепочку вызовов api и ставит её на проверку.

        Args:
            label: Название операции для лога.
            steps: Список кортежей (подпись, fn, timeout, pace_before), где
                fn — callable(connector, timeout) -> dict | None, а
                pace_before — пауза перед шагом (через _pace, вычитается из
                замера). Изменяющий документ шаг обязан быть последним —
                см. комментарий к блоку выше.
            checker: callable(before, after) -> (bool, str) — чем подтверждать
                результат; None — операция не проверяется (например, копирование
                в буфер: в документе оно ничего не меняет).
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Побочный эффект: суммирует api_ms всех успешных шагов в
        self._cdp_api_ms (сбрасывается вызывающим кодом перед замером, рядом
        с _paced_total и _op_via_cdp — см. run_test_with_runs/measure). Это
        синхронное время внутри рендерера (performance.now(), <1 мс
        разрешение), не зависящее от опроса CPU детектором простоя
        (_wait_operation_done, окно 0.20 с) — тот на операциях короче своего
        окна усреднения даёт разброс до 20× между прогонами одного файла.
        Складываются шаги вместе (не берётся последний): последовательность
        вроде «выделить → скопировать → выделить → вставить» — это несколько
        отдельных вызовов api, и «синхронное время операции» — сумма всех, а
        не только последнего.

        Returns:
            bool: True — операция выполнена (или отправлена) через CDP, вызывающий
            код НЕ должен повторять её клавишами. False — через CDP ничего не
            произошло, нужен обычный pyautogui-путь.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if self._cdp_ops_connector() is None:
            return False

        mutated_already = False
        last_payload = None
        for caption, fn, timeout, pace_before in steps:
            if pace_before:
                # Не слепая пауза, а ожидание редактора: точнее и остаётся
                # в замере как работа Р7 (см. _cdp_settle).
                self._cdp_settle()
            status, payload = self._cdp_step(caption, fn, log_cb, timeout)
            if status == "ok":
                last_payload = payload
                ms = payload.get("api_ms")
                if isinstance(ms, (int, float)):
                    self._cdp_api_ms += ms
                if payload.get("mutated"):
                    mutated_already = True
                continue
            if status == "unknown":
                log_cb(f"   ⚠️ CDP «{label}»: цепочка прервана на шаге «{caption}», "
                       f"клавишами не повторяю — прогон не войдёт в статистику")
                self._op_via_cdp = True
                self._op_unverified = f"CDP «{caption}»: результат неизвестен"
                return True
            # failed / unavailable
            if mutated_already:
                log_cb(f"   ⚠️ CDP «{label}»: шаг «{caption}» не выполнен, но документ "
                       f"уже изменён предыдущим шагом — откат на клавиши отменён")
                self._op_via_cdp = True
                self._op_unverified = f"CDP «{caption}»: цепочка оборвана после правки"
                return True
            return False

        self._op_via_cdp = True
        self._cdp_verify_or_defer(label, last_payload, checker, log_cb)
        return True

    def _cdp_verify_or_defer(self, label, payload, checker, log_cb):
        """Проверяет результат операции по снимкам состояния из ответа JS.

        Снимок «после» снят внутри той же страницы сразу за вызовом api, то
        есть бесплатно (без ещё одного round-trip). Но часть операций Р7
        доводит асинхронно — вставка из системного буфера, например, уходит в
        нативный код и к моменту возврата asc_Paste документ ещё не изменён.
        Поэтому неподтвердившаяся проверка не считается провалом сразу, а
        откладывается до _flush_pending_cdp_verify — тот перечитывает
        состояние уже ПОСЛЕ закрытия окна замера, и его round-trip в цифру
        не попадает.

        Args:
            label: Название операции.
            payload: Ответ JS последнего шага (с полями before/after).
            checker: callable(before, after) -> (bool, str), либо None.
            log_cb: Функция логирования.
        """
        self._pending_cdp_verify = None
        # Сумма api_ms всех шагов последовательности (_cdp_sequence уже
        # накопила её к этому моменту) — синхронное время внутри рендерера,
        # не зависящее от опроса CPU детектором простоя. См. docstring
        # _cdp_sequence.
        api_ms_note = (f" [api: {self._cdp_api_ms:.2f} мс]"
                       if self._cdp_api_ms > 0 else "")
        if not isinstance(payload, dict):
            log_cb(f"   🧩 CDP «{label}»: выполнено{api_ms_note}")
            return
        method = payload.get("method") or "api"
        if checker is None:
            log_cb(f"   🧩 CDP «{label}»: выполнено ({method}){api_ms_note}")
            return
        before, after = payload.get("before"), payload.get("after")
        ok, detail = checker(before, after)
        if ok:
            log_cb(f"   🧩 CDP «{label}»: выполнено ({method}){api_ms_note}, "
                   f"проверено — {detail}")
            return
        log_cb(f"   🧩 CDP «{label}»: выполнено ({method}){api_ms_note}, "
               f"сразу не подтвердилось ({detail}) — перепроверю после замера")
        self._pending_cdp_verify = (label, before, checker)

    def _flush_pending_cdp_verify(self, log_cb=None):
        """Доводит отложенную проверку CDP-операции — уже ВНЕ окна замера.

        Вызывать в обоих воркерах сразу после _wait_operation_done, рядом с
        _flush_pending_modal_confirm (правило зеркалирования из CLAUDE.md).
        Round-trip по websocket здесь стоит миллисекунды, но в замер он
        попадать не должен — отсюда и отложенность.

        Args:
            log_cb: Функция логирования; по умолчанию self.add_test_log.
        """
        pending = getattr(self, "_pending_cdp_verify", None)
        if not pending:
            return
        self._pending_cdp_verify = None
        if log_cb is None:
            log_cb = self.add_test_log
        label, before, checker = pending
        connector = self._cdp_ops_connector()
        if connector is None:
            log_cb(f"   ⚠️ CDP-проверка «{label}»: соединение недоступно, "
                   f"результат операции не подтверждён")
            self._op_unverified = f"проверка «{label}»: нет соединения"
            return
        try:
            after = connector.document_state(timeout=self.CDP_OP_TIMEOUT_SEC)
        except Exception as e:
            log_cb(f"   ⚠️ CDP-проверка «{label}»: {type(e).__name__}: {e}")
            self._op_unverified = f"проверка «{label}»: {type(e).__name__}"
            return
        if after is None:
            log_cb(f"   ⚠️ CDP-проверка «{label}»: состояние документа прочитать "
                   f"не удалось")
            self._op_unverified = f"проверка «{label}»: состояние не прочитано"
            return
        ok, detail = checker(before, after)
        if ok:
            log_cb(f"   ✅ CDP-проверка «{label}»: {detail}")
        else:
            log_cb(f"   ⚠️ CDP-проверка «{label}»: не подтверждено — {detail}")
            self._op_unverified = f"проверка «{label}»: {detail}"

    @classmethod
    def _cdp_check_whole_sheet_selected(cls, before, after):
        """Ctrl+A: выделение стало полным листом.

        Форм записи у полного выделения несколько, и они зависят от того, как
        Р7 свернул диапазон. На живой сборке (2026.2.2.x, прогон 2026-08-25)
        asc_getActiveRangeStr после asc_EditSelectAll отдаёт «1:1048576» —
        диапазон СТРОК, без букв столбцов. Проверка только по форме
        «A1:XFD1048576» этого не узнавала и честно выполненную операцию
        помечала как неподтверждённую.
        """
        sel = (after or {}).get("selection")
        if not isinstance(sel, str):
            return (False, "выделение прочитать не удалось")
        s = sel.strip().upper()

        # «1:1048576» — все строки листа
        m = re.match(r"^(\d+):(\d+)$", s)
        if m and int(m.group(2)) - int(m.group(1)) + 1 >= cls.CDP_WHOLE_SHEET_MIN_ROWS:
            return (True, f"выделено {sel} (все строки листа)")

        # «A1:XFD1048576» — тот же лист, записанный ячейками
        m = re.match(r"^A1:[A-Z]+(\d+)$", s)
        if m and int(m.group(1)) >= cls.CDP_WHOLE_SHEET_MIN_ROWS:
            return (True, f"выделено {sel}")

        # «A:XFD» — все столбцы листа
        if re.match(r"^A:[A-Z]{2,3}$", s):
            return (True, f"выделено {sel} (все столбцы листа)")

        return (False, f"выделено {sel!r} — это не весь лист")

    @staticmethod
    def _cdp_check_selection_is(expected):
        """Фабрика проверки «выделен ровно этот диапазон»."""
        def _check(before, after):
            sel = (after or {}).get("selection")
            if not isinstance(sel, str):
                return (False, "выделение прочитать не удалось")
            if sel.strip().upper() == expected.upper():
                return (True, f"выделено {sel}")
            return (False, f"выделено {sel!r}, ожидалось {expected!r}")
        return _check

    @staticmethod
    def _cdp_check_sheet_added(before, after):
        """Новый лист: число листов выросло ровно на один."""
        b = (before or {}).get("sheets")
        a = (after or {}).get("sheets")
        if not isinstance(b, int) or not isinstance(a, int):
            return (False, "число листов прочитать не удалось")
        if a == b + 1:
            return (True, f"листов было {b}, стало {a}")
        return (False, f"листов было {b}, стало {a}")

    @staticmethod
    def _cdp_check_document_changed(before, after):
        """Документ изменился: сдвинулась история правок.

        Универсальный признак для операций, у которых нет дешёвого прямого
        подтверждения (вставка, insertCells): любая принятая правка создаёт
        точку в History, а до первой правки Can_Undo() == false.
        """
        b, a = (before or {}), (after or {})
        for key, human in (("historyIndex", "позиция в истории"),
                           ("historyPoints", "число точек истории")):
            bv, av = b.get(key), a.get(key)
            if isinstance(bv, int) and isinstance(av, int) and av != bv:
                return (True, f"{human}: {bv} → {av}")
        if not b.get("canUndo") and a.get("canUndo"):
            return (True, "появилась возможность отменить правку")
        if b.get("historyIndex") is None and b.get("historyPoints") is None:
            return (False, "историю правок прочитать не удалось")
        return (False, "история правок не сдвинулась")

    def _cdp_ensure_connected(self, log_cb=None):
        """Подключает коннектор текущего запуска, если он ещё не подключён.

        Нужно потому, что connect() зовётся лениво: коннектор создаётся при
        запуске Р7 (_prepare_webdriver_launch), а подключается впервые внутри
        _wait_for_bold_button_cdp — и только если триггер готовности вообще
        дошёл до CDP-ветки. Открылся файл быстро, признаки готовности совпали
        раньше — соединения нет, и все операции молча ушли бы на клавиши.
        connect() идемпотентен: для уже подключённого это no-op.

        Args:
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            bool: True, если соединение есть.
        """
        connector = self._webdriver_connector
        if connector is None:
            return False
        if getattr(connector, "connected", False):
            return True
        try:
            return bool(connector.connect(timeout=self.CDP_CONNECT_TIMEOUT_SEC))
        except Exception as e:
            (log_cb or self.add_test_log)(
                f"⚠️ CDP: подключиться не удалось ({type(e).__name__}: {e})")
            return False

    def _cdp_log_api_info(self, log_cb=None):
        """Один раз за запуск Р7 пишет в лог, найден ли внутренний api и какие
        методы у него есть.

        Без этой строки «CDP-операция не сработала» неотличимо от «api не
        нашёлся вовсе»: первое чинится в JS, второе означает, что сборка Р7
        держит api под другим именем и весь перевод тестов на CDP в этом
        запуске просто не работает.

        Args:
            log_cb: Функция логирования; по умолчанию self.add_test_log.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        self._cdp_ensure_connected(log_cb)
        connector = self._cdp_ops_connector()
        if connector is None:
            log_cb("🧩 CDP-операции недоступны в этом запуске — тесты пойдут "
                   "клавишами (pyautogui)")
            return
        try:
            info = connector.api_info(timeout=self.CDP_OP_TIMEOUT_SEC)
        except Exception as e:
            log_cb(f"⚠️ CDP: опрос api редактора не удался ({type(e).__name__}: {e})")
            return
        if not isinstance(info, dict):
            log_cb("⚠️ CDP: ответ на опрос api не получен — операции пойдут клавишами")
            return
        if not info.get("found"):
            log_cb("⚠️ CDP: внутренний api редактора (Asc.spreadsheet_api) не найден "
                   "в DOM — все операции пойдут клавишами")
            return
        state = info.get("state") or {}
        log_cb(f"🧩 CDP: api редактора найден (iframe глубины {info.get('frame')}), "
               f"листов {state.get('sheets')}, выделено {state.get('selection')!r}")
        missing = sorted(n for n, present in (info.get("methods") or {}).items()
                         if not present)
        if missing:
            log_cb(f"   ⚠️ у api нет методов: {', '.join(missing)} — "
                   f"соответствующие тесты пойдут клавишами")

    def _cdp_select_all(self, log_cb=None):
        """Ctrl+A → asc_EditSelectAll."""
        return self._cdp_sequence(
            "Выделение всех ячеек",
            [("asc_EditSelectAll", lambda c, t: c.select_all(timeout=t),
              self.CDP_LONG_OP_TIMEOUT_SEC, 0)],
            self._cdp_check_whole_sheet_selected, log_cb)

    def _cdp_copy(self, log_cb=None):
        """Ctrl+C → asc_Copy.

        Проверять нечем: документ не меняется, а содержимое системного буфера
        из DOM не прочитать. Подтверждением служит сам ответ asc_Copy (ok=false,
        если метод вернул false) — дальше вставка либо сработает, либо нет, и
        это увидит проверка вставки.
        """
        done = self._cdp_sequence(
            "Копирование выделения",
            [("asc_Copy", lambda c, t: c.copy(timeout=t),
              self.CDP_LONG_OP_TIMEOUT_SEC, 0)],
            None, log_cb)
        # Подготовка теста выделила весь рабочий лист — в буфере его копия.
        self._pending_sheet_clip_mark = bool(done) and getattr(self, "_prepared_on_ws", False)
        return done

    def _cdp_add_sheet(self, log_cb=None):
        """Shift+F11 → asc_addWorksheet."""
        return self._cdp_sequence(
            "Добавление нового листа",
            [("asc_addWorksheet", lambda c, t: c.add_sheet(timeout=t),
              self.CDP_OP_TIMEOUT_SEC, 0)],
            self._cdp_check_sheet_added, log_cb)

    def _cdp_paste_big(self, log_cb=None, key_pace=None):
        """Ctrl+V → asc_Paste: одна вставка на подготовленный пустой лист.

        Лист и буфер готовит _paste_big_prepare вне замера. Если подготовка
        не прошла (лист не создан), создаём его здесь — как раньше.
        key_pace оставлен в сигнатуре для вызывающего кода, паузы нет.
        """
        steps = [("asc_Paste", lambda c, t: c.paste(timeout=t),
                  self.CDP_LONG_OP_TIMEOUT_SEC, 0)]
        if not getattr(self, "_paste_sheet_prepared", False):
            steps.insert(0, ("asc_addWorksheet", lambda c, t: c.add_sheet(timeout=t),
                             self.CDP_OP_TIMEOUT_SEC, 0))
            steps[1] = steps[1][:3] + (self.OP_KEY_PACE,)
        return self._cdp_sequence(
            "Вставка большого массива", steps,
            self._cdp_check_document_changed, log_cb)

    def _cdp_add_column(self, log_cb=None, key_pace=None):
        """Добавление столбца → asc_insertCells(InsertColumns).

        Клавиатурная версия сначала уходит на предыдущий лист (Ctrl+PageUp) и
        сдвигает курсор вправо — здесь то же самое, но детерминированно:
        переход на лист левее активного и выделение ровно B1 (клавиатурный
        `press('right')` сдвигал курсор от того места, где он оказался после
        предыдущего теста, то есть от разного).

        Оба теста — «(горячие клавиши)» и «(меню Вставка)» — на этом пути
        выполняются одним и тем же вызовом api: меню как такового здесь нет.
        Разница между ними остаётся только на pyautogui-пути.
        """
        # Лист и ячейку B1 выбирает подготовка (_prepare_on_work_sheet("B1"))
        # вне замера. Прежний шаг «лист левее активного» уводил два теста
        # добавления столбца на РАЗНЫЕ листы: 1.6 с и 3.3 с за одно и то же
        # действие (полный прогон 30.09.2026).
        steps = [("asc_insertCells(InsertColumns)", lambda c, t: c.insert_column(timeout=t),
                  self.CDP_LONG_OP_TIMEOUT_SEC, 0)]
        if not getattr(self, "_prepared_on_ws", False):
            steps.insert(0, ("asc_findCell(B1)", lambda c, t: c.select_range("B1", timeout=t),
                             self.CDP_OP_TIMEOUT_SEC, 0))
        return self._cdp_sequence(
            "Добавление столбца", steps,
            self._cdp_check_document_changed, log_cb)

    def _cdp_copy_paste(self, cell_count, paste_offset, shift=None,
                        log_cb=None, key_pace=None):
        """Копирование N ячеек и вставка со смещением — оба варианта тестов
        «Вставка N ячеек».

        Args:
            cell_count: Сколько ячеек копируем (диапазон A1:<буква>1).
            paste_offset: На сколько столбцов вправо уходим перед вставкой —
                ровно столько раз клавиатурная версия жмёт «вправо» от A1.
            shift: None — обычная вставка (вариант «горячие клавиши»);
                "down"/"right" — вставка ячеек со сдвигом, то есть то, что на
                клавиатурном пути делает пункт контекстного меню «Вставить
                ячейки» и следующая за ним модалка выбора сдвига (вариант
                «ПКМ»). Модалки на этом пути не возникает — подтверждать
                Enter'ом нечего.
            log_cb: Функция логирования.
            key_pace: Пауза после копирования; по умолчанию OP_KEY_PACE — та
                же, что и в клавиатурной версии.

        Returns:
            bool: True — операция ушла в Р7 через CDP.
        """
        if key_pace is None:
            key_pace = self.OP_KEY_PACE
        src = f"A1:{_col_letter(max(1, cell_count))}1"
        paste_step = ("asc_Paste", lambda c, t: c.paste(timeout=t),
                      self.CDP_LONG_OP_TIMEOUT_SEC, 0)
        if shift is None:
            dst = f"{_col_letter(paste_offset + 1)}1"
            label = f"Вставка {cell_count} ячеек (буфер)"
            tail = [paste_step]
        else:
            # «Вставить скопированные ячейки» = сдвиг + вставка копии. Раньше
            # здесь был только asc_insertCells, то есть вставка ПУСТЫХ ячеек,
            # а скопированное никуда не шло. Диапазон сдвига — ровно N ячеек.
            # Шагов, меняющих документ, два: если упадёт вставка после сдвига,
            # _cdp_sequence запретит откат на клавиши (mutated_already).
            dst = (f"{_col_letter(paste_offset + 1)}1:"
                   f"{_col_letter(paste_offset + max(1, cell_count))}1")
            label = f"Вставка {cell_count} скопированных ячеек (со сдвигом {shift})"
            tail = [(f"asc_insertCells({shift})",
                     lambda c, t: c.insert_cells(shift, timeout=t),
                     self.CDP_LONG_OP_TIMEOUT_SEC, 0),
                    paste_step]
        return self._cdp_sequence(
            label,
            [(f"asc_findCell({src})", lambda c, t: c.select_range(src, timeout=t),
              self.CDP_OP_TIMEOUT_SEC, 0),
             ("asc_Copy", lambda c, t: c.copy(timeout=t),
              self.CDP_LONG_OP_TIMEOUT_SEC, 0),
             (f"asc_findCell({dst})", lambda c, t: c.select_range(dst, timeout=t),
              self.CDP_OP_TIMEOUT_SEC, key_pace)] + tail,
            self._cdp_check_document_changed, log_cb)
