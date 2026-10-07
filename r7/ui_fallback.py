"""Запасные пути тестов правки через интерфейс Р7: контекстное меню у
выделения (Shift+F10, пункт по подписи через DOM) и диалог «Вставить ячейки».

Без CDP тесты «ПКМ» и «меню Вставка» честно падают, а не жмут вслепую
(docs/ui-fallback.md). UiFallbackMixin — методы, которые R7Testovarka
получает наследованием.
"""
import time


class UiFallbackMixin:
    """Запасные пути через интерфейс Р7 — часть R7Testovarka."""

    def _ui_menu_connector(self):
        """Подключённый коннектор запуска для кликов по меню Р7.

        В отличие от _cdp_ops_connector, не зависит от CDP_OPS_ENABLED:
        выключатель переводит ОПЕРАЦИИ на клавиатурный путь, а контекстное
        меню на этом пути без DOM не выбрать вовсе.
        """
        connector = self._webdriver_connector
        if connector is not None and getattr(connector, "connected", False):
            return connector
        return None

    def _context_menu_pick(self, path, hotkey, log_cb=None):
        """Открывает контекстное меню у выделения (Shift+F10) и нажимает пункт.

        Shift+F10, а не правый клик: pyautogui.click(button='right') без
        координат кликал туда, где стоит мышь, — выделение сбрасывалось на
        случайную ячейку. Время от открытия меню до клика — ожидание
        отрисовки меню. Пауз вслепую нет: клавиши Р7 обрабатывает по очереди,
        и меню откроется только когда он доделает предыдущий шаг (вставку,
        копирование). Поэтому ожидание меню НЕ вычитается — в нём идёт работа
        Р7. Вычитается только round-trip удачного клика: меню уже открыто,
        Р7 простаивает, а сам клик отложен (setTimeout) и в round-trip не
        входит — работа после клика остаётся в замере.

        Raises:
            RuntimeError: меню не открылось, пункт не найден или недоступен —
                меню закрывается Esc, дальше ничего вслепую не нажимается.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        connector = self._ui_menu_connector()
        hotkey('shift', 'f10')
        deadline = time.perf_counter() + self.OP_CONTEXT_MENU_WAIT_SEC
        res = None
        while True:
            t0 = time.perf_counter()
            try:
                res = connector.click_context_menu_path(
                    path, timeout=self.CDP_OP_TIMEOUT_SEC)
            except Exception as e:
                res = {"clicked": False, "reason": f"ошибка CDP: {e}"}
            if isinstance(res, dict) and res.get("clicked"):
                self._paced_total += time.perf_counter() - t0
                break
            if (not isinstance(res, dict) or res.get("reason") != "menu-not-open"
                    or time.perf_counter() >= deadline):
                break
            time.sleep(self.OP_POLL_SEC)
        caption = " ▸ ".join(path)
        if isinstance(res, dict) and res.get("clicked"):
            log_cb(f"   🖱 Контекстное меню: «{caption}»")
            # Клик отложен: пока меню не закрылось, следующий Shift+F10
            # теряется (живой прогон 30.09.2026 — третье меню не открылось).
            # Ожидание не вычитается: Р7 в нём уже выполняет пункт.
            close_deadline = time.perf_counter() + self.OP_CONTEXT_MENU_WAIT_SEC
            while (connector.context_menu_open(timeout=self.CDP_OP_TIMEOUT_SEC)
                   and time.perf_counter() < close_deadline):
                time.sleep(self.OP_POLL_SEC)
            return
        hotkey('esc')
        reason = res.get("reason") if isinstance(res, dict) else "CDP не ответил"
        items = res.get("items") if isinstance(res, dict) else None
        raise RuntimeError(
            f"в контекстном меню не нажат пункт «{caption}» ({reason})"
            + (f"; в меню: {', '.join(items)}" if items else ""))

    def _context_menu_copy_paste(self, cell_count, paste_offset, hotkey, press,
                                 log_cb=None):
        """Тест «Вставка N ячеек (ПКМ)» через контекстное меню — запасной путь,
        если одна api-операция не прошла. Повторяет CDP-вариант
        (_cdp_copy_paste с shift="down"): копия A1:<N>1, на месте вставки N
        ячеек со сдвигом вниз, в них — копия.

        Меню Р7 2026.3.2 (проверено вживую 30.09.2026): «Копировать»;
        «Добавить ▸ Ячейки со сдвигом вниз» — без диалога; «Вставить ▸
        Вставить» — подменю с вариантами вставки, нужен первый, обычный.
        Прежняя цепочка (стрелки вниз + Enter + Enter для модалки) не
        работала: стрелки меню не двигают, Enter на пункте с подменю его
        не выбирает, а лишние Enter уходили в модалку пересчёта («Да» —
        ручной пересчёт) или в сетку.

        Общий для _spreadsheet_worker и _batch_run_single_version. Внешние
        действия — параметрами, чтобы цепочку можно было проверить без Р7.

        Raises:
            RuntimeError: CDP недоступен — без DOM пункт меню не выбрать, а
                слепых нажатий инструмент не делает; либо пункт не найден.
        """
        if self._ui_menu_connector() is None:
            raise RuntimeError(self._NO_CDP_MENU_ERROR)
        hotkey('ctrl', 'home')
        for _ in range(cell_count - 1):
            hotkey('shift', 'right')
        # Пауз между шагами нет: клавиши встают в очередь Р7 за работой
        # предыдущего клика, а следующее меню ждётся по DOM (_context_menu_pick).
        self._context_menu_pick(["Копировать"], hotkey, log_cb)
        # Из выделения A1:<N>1 «вправо» уводит активную ячейку от A1 —
        # цель та же, что у CDP-пути: столбец paste_offset + 1.
        press('right', paste_offset)
        for _ in range(cell_count - 1):
            hotkey('shift', 'right')
        self._context_menu_pick(["Добавить", "Ячейки со сдвигом вниз"], hotkey, log_cb)
        self._context_menu_pick(["Вставить", "Вставить"], hotkey, log_cb)

    def _add_column_ui(self, method, hotkey, press, log_cb=None):
        """Тесты «Добавление столбца» клавишами и меню — запасной путь, если
        api-операция не прошла. Общий для обоих воркеров.

        Р7 2026.3.2 (проверено вживую 30.09.2026):
          * «горячие клавиши»: Ctrl+Shift+= на одной ячейке открывает диалог
            «Вставить ячейки» (сдвиг вправо / сдвиг вниз / строку / столбец,
            OK / Отмена), по умолчанию выбран сдвиг вправо. Прежний код на
            этом останавливался — столбец не вставлялся. Стрелки переключатель
            не двигают; Tab переходит к следующему варианту, пробел выбирает,
            Enter — OK;
          * «меню»: Alt+I в этой сборке ничего не открывает, и следующая «c»
            начинала правку ячейки. Столбец вставляет пункт контекстного меню
            «Добавить ▸ Столбец».

        Raises:
            RuntimeError: диалог не открылся (при CDP) или пункт меню не
                нажат — без подтверждения ничего вслепую не нажимается.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if method != 'hotkey' and self._ui_menu_connector() is None:
            raise RuntimeError(self._NO_CDP_MENU_ERROR)
        if not getattr(self, "_prepared_on_ws", False):
            # Подготовка через CDP не прошла — идём к ячейке клавишами.
            hotkey('ctrl', 'pageup')
            self._pace(self.OP_KEY_PACE)      # переключение листа
            press('right')                    # одна ячейка — будет диалог
        if method != 'hotkey':
            self._context_menu_pick(["Добавить", "Столбец"], hotkey, log_cb)
            return
        hotkey('ctrl', 'shift', '=')
        connector = self._ui_menu_connector()
        if connector is not None:
            # Ожидание диалога не вычитается (как и ожидание меню): Р7 рисует
            # его сам; дальше он простаивает до нашего выбора.
            # Ждём и сам диалог, и фокус в нём: фокус приходит через 0.1–0.15 с
            # после появления, и Tab до этого уходят в сетку (живой прогон
            # 30.09.2026: выбор так и остался на сдвиге вправо).
            deadline = time.perf_counter() + self.OP_CONTEXT_MENU_WAIT_SEC
            while not (connector.insert_cells_dialog_state(
                    timeout=self.CDP_OP_TIMEOUT_SEC) or {}).get("focused"):
                if time.perf_counter() >= deadline:
                    raise RuntimeError("диалог «Вставить ячейки» не открылся после Ctrl+Shift+=")
                time.sleep(self.OP_POLL_SEC)
        else:
            # Без CDP диалог не увидеть; на одной ячейке он открывается всегда.
            self._pace(self.OP_DIALOG_PACE)
        for _ in range(3):                    # сдвиг вправо → вниз → строку → столбец
            press('tab')
        press('space')
        if connector is not None:
            # Клавиши и CDP идут разными каналами — выбор ждём коротким опросом.
            # Р7 всё это время простаивает с открытым диалогом.
            t0 = time.perf_counter()
            choice = None
            while time.perf_counter() - t0 < self.OP_DIALOG_PACE:
                choice = (connector.insert_cells_dialog_state(
                    timeout=self.CDP_OP_TIMEOUT_SEC) or {}).get("choice")
                if choice == 3:
                    break
                time.sleep(self.OP_POLL_SEC)
            self._paced_total += time.perf_counter() - t0
            if choice != 3:
                press('esc')
                raise RuntimeError(f"в диалоге «Вставить ячейки» выбран вариант "
                                   f"{choice}, а не «Столбец» — диалог отменён")
        press('enter')                        # OK

    def _cdp_click_context_item(self, wanted, log_cb=None, charge_pace=True):
        """Пробует нажать пункт раскрытого контекстного меню по его подписи.

        Нужен на pyautogui-пути: меню там обходится стрелками вслепую (`down`
        N раз + Enter), и лишний пункт уводит счётчик — ровно та проблема, из-за
        которой заведён issue #9. Если меню нарисовано в DOM, точное попадание
        по подписи надёжнее счёта стрелок.

        По issue #9 контекстное меню Р7, судя по дампам, рисуется нативным
        оверлеем CEF и в обходимом DOM не появляется — поэтому неудача здесь
        штатная, вызывающий код молча продолжает стрелками.

        Args:
            wanted: Подписи (подстроки, регистр не важен) в порядке приоритета.
            log_cb: Функция логирования; по умолчанию self.add_test_log.
            charge_pace: Отнести длительность round-trip в _paced_total. По
                умолчанию True: метод вызывается при раскрытом меню, когда Р7
                гарантированно простаивает.

        Returns:
            bool: True, если пункт найден и нажат.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        connector = self._cdp_ops_connector()
        if connector is None:
            return False
        t0 = time.perf_counter()
        try:
            # Базовый снимок вычитается на стороне JS: без него клик может уйти
            # в статичное overflow-меню тулбара с такими же подписями (issue #9).
            res = connector.click_menu_item(
                wanted, baseline=getattr(self, "_cdp_ui_baseline", None),
                timeout=self.CDP_OP_TIMEOUT_SEC)
        except Exception:
            res = None
        finally:
            if charge_pace:
                self._paced_total += time.perf_counter() - t0
        if isinstance(res, dict) and res.get("clicked"):
            log_cb(f"   🧩 CDP: нажат пункт меню {res.get('text')!r} "
                   f"(совпало с {res.get('matched')!r})")
            return True
        return False
