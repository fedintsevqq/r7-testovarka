"""Экспорт через «Сохранить как» и конвертер x2t.

Диалог открывается CDP-кликом по «Файл» (запасные пути — хоткей, меню,
WM_COMMAND), тип файла переключается только через UI Automation, конец
экспорта — запись файла, формат файла проверяется (docs/measurement.md).
ExportMixin — методы, которые R7Testovarka получает наследованием.
"""
import os
import time
from pathlib import Path

from r7 import env, windows
from r7.windows import _escape_send_keys


class ExportMixin:
    """«Сохранить как», x2t и проверка файла экспорта — часть R7Testovarka."""

    OP_PDF_GRACE_SEC    = 6.00   # для экспорта в PDF: x2t стартует не сразу после
                                 # Enter в диалоге «Сохранить как»
    OP_MENU_PACE        = 0.12   # пауза на отрисовку меню или диалога
    OP_CDP_PANEL_PACE_SEC = 0.40 # отрисовка полноэкранной панели «Файл» после клика
                                 # по ribbon-вкладке (см. _try_cdp_saveas) — панель
                                 # рисуется не мгновенно, второй клик («Сохранить
                                 # как» внутри неё) раньше этого мог промахнуться
                                 # мимо ещё не отрисованных пунктов. Величина взята
                                 # из живого прогона (27.08.2026,
                                 # tests/manual_saveas_cdp_probe.py), не откалибрована
                                 # на минимум



    @staticmethod
    def _clipboard_seq():
        """Номер состояния буфера обмена Windows: меняется при каждой записи
        в буфер любым приложением. None — API недоступен."""
        try:
            return windows.clipboard_sequence_number() or None
        except Exception:
            return None


    def _confirm_modal_enter(self, pace=None):
        """Подтверждает модалку «Вставить ячейки» — часть, которая обязана
        находиться ВНУТРИ окна замера.

        Диалог сдвига ячеек — HTML-модалка внутри CEF, а не окно ОС: win32gui её
        не видит (тот же случай, что и кнопка «Жирный», см.
        r7_webdriver_connector.py), поэтому дождаться её появления штатным
        _wait_for_window_title нельзя — остаётся слепой Enter. На OP_MENU_PACE
        (0.12 с) это гонка: на нагруженном документе модалка не успевает
        отрисоваться, Enter уходит в сетку, диалог остаётся висеть и ломает все
        последующие операции прогона. Именно это возвращало баг, который PR #4
        считал закрытым.

        Почему здесь ровно один Enter, а повторы — в _flush_pending_modal_confirm.
        Пауза ниже проходит через _pace() и вычитается из замера, и это корректно
        только пока Р7 действительно простаивает, показывая модалку. Сразу после
        подтверждающего Enter начинается настоящая работа (вставка ячеек) — сон и
        вычитание в этот момент отняли бы у результата время, которое Р7 реально
        работал: операция короче секунды выдавала бы 0/below_floor. Поэтому func()
        возвращает управление сразу после подтверждения, а страховочные повторы
        уходят за границу замера.

        Args:
            pace: Пауза на отрисовку модалки; по умолчанию OP_DIALOG_PACE.
        """
        if pace is None:
            pace = self.OP_DIALOG_PACE
        # Р7 в этот момент простаивает, ожидая ввода, — вычитать паузу корректно.
        self._pace(pace)
        self._press('enter')
        # Дальше начинается работа Р7: замер должен идти без наших пауз.
        self._pending_modal_confirm = True


    def _save_as_format(self, ext, find_hwnd, hotkey, press, log_cb=None):
        """Экспорт текущего документа в формат ext через «Сохранить как» — ОДНА
        реализация для вкладки «Производительность» и Batch-режима.

        Раньше это были две копии во вложенных функциях воркеров, без единого
        теста; логика совпадала, но уже расходилась в мелочах (QA-аудит
        29.09.2026, G-01). Внешние действия передаются параметрами, поэтому
        цепочку открытия диалога можно проверить тестами.

        Цепочка открытия диалога, по порядку: CDP-клик по вкладке «Файл» →
        Ctrl+Shift+S → повтор после переустановки фокуса → меню Alt+F →
        WM_COMMAND. Не открылось ничем — RuntimeError("SKIP: …") и НИ ОДНОГО
        нажатия Ctrl+A/Ctrl+V/Enter: без диалога они ушли бы в то окно, что
        в фокусе (живой прогон 25.08.2026 — порча тестового файла). Дальше —
        выбор типа, ввод пути и «Сохранить» через UI Automation
        (_uia_select_saveas_type), предупреждение о потере функций формата,
        для CSV — окно параметров (_confirm_csv_options), затем ожидание
        файла (_wait_for_export_file).

        Ожидание диалога — реакция Р7, остаётся в замере; вычитается только
        безрезультатное ожидание перед запасным путём.

        Args:
            ext: "pdf", "ods", "csv" или "xltx".
            find_hwnd: callable() → HWND главного окна Р7.
            hotkey: callable(*keys) — нажатие сочетания (SpreadsheetOps.hotkey).
            press: callable(key, n=1, pace=0.0) — нажатие клавиши (SpreadsheetOps.press).
            log_cb: функция логирования; по умолчанию self.add_test_log.

        Raises:
            RuntimeError: диалог не открылся (текст начинается с "SKIP:"),
                UIA не справился или файл экспорта не появился.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        tmp_path = str(Path(os.environ.get("TEMP", ".")) /
                       f"temp_export_x2t_{int(time.time())}.{ext}")
        self._export_go_at = None
        # x2t стартует не мгновенно после Enter — просим детектор подождать
        # его дольше обычного, иначе экспорт будет помечен «ниже порога».
        self._op_start_grace = self.OP_PDF_GRACE_SEC

        # Глобальный хоткей Ctrl+Shift+S уходит не туда, если фокус
        # перехватило постороннее окно на рабочем столе оператора —
        # см. _ensure_foreground_click. Проверяем/восстанавливаем
        # фокус ПЕРЕД каждой из трёх попыток открыть диалог.
        #
        # Escape+Ctrl+Home ниже — ТОЛЬКО клавиатура, без кликов по
        # телу документа: после серии CDP-операций (Runtime.evaluate,
        # см. предыдущие 12 тестов) OS-фокус окна и DOM-фокус ВНУТРИ
        # CEF на самом документе могут разъехаться (акселератор не
        # срабатывает, даже когда GetForegroundWindow подтверждает
        # правильное окно — живой прогон 26.08.2026). Клик по телу
        # документа для его восстановления НЕ используется намеренно
        # — на реальной фикстуре строка 1 занята автофильтрами
        # почти целиком, и клик туда открывает их выпадающее меню
        # вместо восстановления фокуса (тоже поймано живым прогоном,
        # тем же оператором). Escape гасит случайно открытое меню,
        # Ctrl+Home — безопасная навигация, не трогает данные.
        _r7_hwnd = find_hwnd()

        # CDP-клик по DOM пробуется первым: в обход синтетической
        # клавиатуры целиком, а не другим способом доставить тот же
        # акселератор — см. _try_cdp_saveas. Сработало — вся цепочка
        # хоткей → меню → WM_COMMAND ниже не нужна.
        if not self._try_cdp_saveas(_r7_hwnd, log_cb=log_cb):
            _focused = self._ensure_foreground_click(_r7_hwnd, log_cb=log_cb)
            log_cb(f"   🔍 Фокус перед Ctrl+Shift+S: {'подтверждён' if _focused else 'НЕ подтверждён'} (hwnd={_r7_hwnd})")
            press('escape')
            hotkey('ctrl', 'home')
            self._pace(self.OP_KEY_PACE)

            log_cb("   🔍 Отправляю Ctrl+Shift+S")
            hotkey('ctrl', 'shift', 's')
            _t_dlg = time.perf_counter()
            if not self._wait_for_window_title(("сохранить как", "save as"), timeout=3.0):
                # Диалог не открылся — эти 3 сек не время Р7, а наша неудача.
                self._paced_total += time.perf_counter() - _t_dlg
                log_cb("   ⚠️ Ctrl+Shift+S не открыл диалог — переустанавливаю фокус и пробую ещё раз")
                _focused = self._ensure_foreground_click(_r7_hwnd, log_cb=log_cb)
                log_cb(f"   🔍 Фокус перед повтором Ctrl+Shift+S: {'подтверждён' if _focused else 'НЕ подтверждён'} (hwnd={_r7_hwnd})")
                press('escape')
                hotkey('ctrl', 'home')
                self._pace(self.OP_KEY_PACE)
                _t_dlg2 = time.perf_counter()
                log_cb("   🔍 Отправляю Ctrl+Shift+S (повтор)")
                hotkey('ctrl', 'shift', 's')
                if not self._wait_for_window_title(("сохранить как", "save as"), timeout=3.0):
                    self._paced_total += time.perf_counter() - _t_dlg2
                    log_cb("   ⚠️ Повтор тоже не открыл диалог, пробуем меню Файл")
                    self._ensure_foreground_click(_r7_hwnd, log_cb=log_cb)
                    hotkey('alt', 'f')
                    self._pace(self.OP_MENU_PACE)
                    press('down', 3, pace=self.OP_MENU_PACE)
                    press('enter')
                    if not self._wait_for_window_title(("сохранить как", "save as"), timeout=3.0):
                        log_cb("   ⚠️ Диалог «Сохранить как» не появился и через меню Файл — пробуем WM_COMMAND")
                        self._ensure_foreground_click(_r7_hwnd, log_cb=log_cb)
                        _t_dlg3 = time.perf_counter()
                        if self._try_wm_command_saveas(_r7_hwnd, log_cb=log_cb):
                            _opened = self._wait_for_window_title(("сохранить как", "save as"), timeout=3.0)
                        else:
                            _opened = False
                        if not _opened:
                            self._paced_total += time.perf_counter() - _t_dlg3
                            log_cb("   ⚠️ WM_COMMAND тоже не открыл диалог")
                            self._dump_visible_window_titles(log_cb)
                            log_cb("   ⏭ SKIP: ни CDP, ни хоткей, ни меню, ни WM_COMMAND не "
                                              "открыли диалог «Сохранить как» — без него Ctrl+A/Ctrl+V/Enter "
                                              "ушли бы в то окно, что сейчас в фокусе (не обязательно Р7)")
                            raise RuntimeError("SKIP: SaveAs dialog not available")

        # Только окно самого Р7: «Сохранить как» другой программы не годится.
        dlg_hwnd = self._find_window_hwnd("сохранить как", "save as",
                                          owner_pids=self._r7_window_owner_pids())
        if dlg_hwnd is None or not self._uia_select_saveas_type(
                dlg_hwnd, ext, tmp_path, log_cb=log_cb):
            raise RuntimeError(
                f"не удалось сохранить в .{ext} через UI Automation — "
                f"тип файла, имя или кнопка «Сохранить» не сработали")

        # Экспорт начинается с нажатия «Сохранить». Всё до него — работа
        # инструмента (открытие диалога, выбор типа, посимвольный ввод пути:
        # 2–3 с с разбросом), а не Р7: в полном прогоне 30.09.2026 PDF давал
        # 88.3 с при конвертации 85.4 с. Дальше вычитается только время,
        # пока окна Р7 ждали нашего ответа.
        _go = getattr(self, "_export_go_at", None)
        _t0 = getattr(self, "_op_started_at", None)
        if _go is not None and _t0 is not None and _go >= _t0:
            self._paced_total = _go - _t0

        self._dismiss_saveas_format_warning(dlg_hwnd, main_hwnd=_r7_hwnd, timeout=3.0, log_cb=log_cb)
        # CSV: ещё одно окно — параметры (кодировка/разделитель).
        # Зеркалится в Batch.
        if ext == "csv":
            self._confirm_csv_options(log_cb=log_cb)
        if not self._wait_for_export_file(tmp_path, log_cb=log_cb):
            raise RuntimeError(
                getattr(self, "_export_fail_reason", None)
                or f"файл экспорта .{ext} не появился за "
                   f"{self.OP_EXPORT_FILE_TIMEOUT_SEC:.0f} сек")
        # Конец замера уже взят из mtime файла — проверка в цифру не попадает.
        fmt_ok, fmt_detail = self._check_export_format(tmp_path, ext)
        if fmt_ok is None:
            raise RuntimeError(f"файл .{ext} записан, но формат не проверить: {fmt_detail}")
        if not fmt_ok:
            raise RuntimeError(f"файл .{ext} записан, но формат не тот: {fmt_detail} — "
                               f"тип в диалоге «Сохранить как», видимо, не переключился")

    def _uia_select_saveas_type(self, dlg_hwnd, ext, target_path, log_cb=None):
        """Проводит диалог «Сохранить как» через UI Automation целиком:
        переключает комбобокс «Тип файла» на нужный формат, вводит целевой
        путь в поле имени и жмёт «Сохранить» — ни одного синтетического
        клавиатурного события через pyautogui, всё через UIA-элементы.

        ПОДТВЕРЖДЕНО ЖИВЫМ ПРОГОНОМ (26.08.2026, tests/manual_saveas_uia_save.py)
        для ods и csv — этот метод повторяет ровно ту последовательность,
        которая там сработала (раньше повторял только её первую половину,
        см. ниже).

        КОРЕНЬ БАГА, из-за которого файл экспорта никогда не появлялся,
        даже когда диалог открывался (см. CLAUDE.md, L2, «продолжение №4»):
        версия этого метода до 27.08.2026 переключала тип и кликала по
        `auto_id="1001"` (поле имени) МЫШЬЮ, а сам путь и Enter отправлял
        вызывающий код (`save_as_format`) обычным `pyperclip.copy` +
        `pyautogui.hotkey('ctrl','a'/'v')` + `Enter` — в расчёте на то, что
        клик мышью оставил ОС-фокус на нужном поле. ЖИВОЙ ПРОГОН
        (`tests/manual_saveas_popup_probe.py`, 27.08.2026) поймал результат:
        Р7 каждый раз сохранял файл под ИСХОДНЫМ именем документа в его
        папке (`test_50k.xlsx` → `test_50k.pdf` рядом с ним), полностью
        игнорируя вставленный путь — то есть Ctrl+A/Ctrl+V в это поле
        реально ничего не меняли. Согласуется с более ранней находкой
        (см. CLAUDE.md, L2, «продолжение №2»): `auto_id="1001"` отражает
        не тот же элемент, что видит пользователь, — `WM_GETTEXT` для него
        всегда пуст, DirectUI рисует текст сам поверх. Повторный прогон
        подряд после первого попадал на диалог подтверждения перезаписи
        (`test_50k.pdf` уже существовал от предыдущей попытки) — этот
        диалог никто не обрабатывал, отсюда таймаут `_wait_for_export_file`
        на пустом месте.

        ПОЧЕМУ `type_keys`, А НЕ ЕЩЁ ОДИН МЫШИНЫЙ СПОСОБ: `type_keys` —
        метод самого элемента `name_edit` в pywinauto, тот же канал UI
        Automation, которым этот элемент был найден и по которому кликнули
        — не полагается на то, что генерический `pyautogui` попадёт туда
        же, куда попал предыдущий клик. Именно так набирает имя файла
        `tests/manual_saveas_uia_save.py`, единственный путь с подтверждённым
        живым результатом на диске.

        auto_id контролов диалога (не текст — независимо от локали):
          FileTypeControlHost — комбобокс типа файла
          1001                — поле имени файла
          1                   — кнопка «Сохранить»

        Матчинг пункта типа — по литералу "(*.<ext>)", который есть только у
        пунктов формата (не у файлов/папок текущей директории, видных в
        том же дереве UIA), и берётся ПЕРВОЕ совпадение: "(*.pdf)" иначе
        зацепил бы и обычный PDF, и «Переносимый документ /A (*.pdf)» —
        первый в списке как раз обычный.

        Args:
            dlg_hwnd: HWND уже открытого диалога «Сохранить как».
            ext: Расширение без точки — "pdf", "ods", "csv", "xltx".
            target_path: Полный путь для сохранения (набирается в поле имени).
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            bool: True — тип выбран, путь набран, «Сохранить» нажата.
            False — что-то из этого не удалось; вызывающий код (save_as_format)
            не должен ничего досылать вслепую после False.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if not env.PYWINAUTO_OK:
            log_cb("   ⚠️ pywinauto недоступен — тип файла не переключается "
                   "(см. requirements.txt)")
            return False

        try:
            app_uia = env._UiaApplication(backend="uia").connect(handle=dlg_hwnd)
            dlg = app_uia.window(handle=dlg_hwnd)
            type_combo = dlg.child_window(auto_id="FileTypeControlHost",
                                           control_type="ComboBox")
            type_combo.expand()
            # Раскрытие списка — не мгновенное (см. tests/manual_saveas_uia_save.py,
            # там же 0.3 с).
            self._pace(self.OP_DIALOG_PACE)

            needle = f"(*.{ext})".lower()
            target = None
            for item in dlg.descendants(control_type="ListItem"):
                nm = item.element_info.name or ""
                if needle in nm.lower():
                    target = item
                    break

            if target is None:
                log_cb(f"   ⚠️ Пункт типа файла для .{ext} не найден в развёрнутом списке")
                try:
                    type_combo.collapse()
                except Exception:  # список уже свёрнут; неудача уже записана выше
                    pass
                return False

            target.click_input()
            self._pace(self.OP_DIALOG_PACE)

            name_edit = dlg.child_window(auto_id="1001", control_type="Edit")
            name_edit.click_input()
            self._pace(self.OP_MENU_PACE)
            name_edit.type_keys("^a", pause=0.02)
            # Экранирование: для type_keys «~» — это Enter, «+» — Shift и т.д.
            # Путь %TEMP% с коротким именем (C:\Users\VLADIM~1\...) нажал бы
            # Enter посреди пути (QA-аудит 29.09.2026, G-08).
            name_edit.type_keys(_escape_send_keys(target_path), with_spaces=True, pause=0.02)
            self._pace(self.OP_MENU_PACE)
            if not self._saveas_name_is(name_edit, target_path, log_cb):
                return False

            save_btn = dlg.child_window(auto_id="1", control_type="Button")
            self._export_go_at = time.perf_counter()   # старт экспорта — см. _save_as_format
            save_btn.click_input()
            if self._saveas_still_open(dlg_hwnd, self.SAVEAS_CLOSE_WAIT_SEC):
                # Нажатие мышью не дошло (ночной прогон 07.10.2026: диалог
                # висел, а инструмент 120 с ждал файл). Повтор — через UI
                # Automation, без мыши; старт экспорта — от этого нажатия.
                log_cb("   ⚠️ «Сохранить» не сработала с первого нажатия — "
                       "нажимаю через UI Automation")
                self._export_go_at = time.perf_counter()
                save_btn.invoke()
                if self._saveas_still_open(dlg_hwnd, self.SAVEAS_CLOSE_WAIT_SEC):
                    log_cb("   ⚠️ Диалог «Сохранить как» не закрылся и после повтора")
                    return False
            return True
        except Exception as e:
            log_cb(f"   ⚠️ UIA-сохранение не удалось: "
                   f"{type(e).__name__}: {e}")
            return False

    # Сколько ждать, что диалог «Сохранить как» закроется после «Сохранить».
    # Обычно — доли секунды; ожидание в цифру экспорта не входит (конец —
    # по mtime файла, начало — _export_go_at).
    SAVEAS_CLOSE_WAIT_SEC = 2.0

    @staticmethod
    def _saveas_still_open(dlg_hwnd, timeout):
        """True — диалог «Сохранить как» спустя timeout всё ещё виден и
        доступен: нажатие «Сохранить» не дошло. Если он открыл своё окно
        (предупреждение о формате, замена файла), сам он недоступен — это
        не «не дошло», а следующий шаг, и повторять нажатие нельзя."""
        if not env.WIN32_OK:
            return False
        deadline = time.perf_counter() + timeout
        while True:
            try:
                if not (windows.is_window(dlg_hwnd) and windows.is_window_visible(dlg_hwnd)):
                    return False
                if not windows.is_window_enabled(dlg_hwnd):
                    return False
            except Exception:
                return False
            if time.perf_counter() >= deadline:
                return True
            time.sleep(0.1)

    @staticmethod
    def _saveas_name_is(name_edit, target_path, log_cb):
        """Поле «Имя файла» содержит ровно target_path. Иначе — одна попытка
        вписать путь напрямую (без клавиш) и проверка ещё раз: клавиши могли
        уйти мимо поля, и «Сохранить» записал бы файл под другим именем."""
        def value():
            try:
                return name_edit.get_value()
            except Exception:
                try:
                    return name_edit.window_text()
                except Exception:
                    return None
        def same(v):
            # UIA диалога текстового редактора отдаёт значение поля с лишними
            # пробелами между символами («E : \T e mp \…», живой прогон
            # 07.10.2026), хотя в поле верный путь. Сравниваем без пробельных
            # символов: путь, отличающийся только ими, — тот же путь.
            return v is not None and "".join(v.split()) == "".join(target_path.split())
        got = value()
        if got is None or got == target_path or same(got):
            return True                     # прочитать нельзя — не мешаем прежнему пути
        log_cb(f"   ⚠️ В поле имени файла «{got}» вместо пути экспорта — вписываю заново")
        try:
            name_edit.set_edit_text(target_path)
        except Exception as e:
            log_cb(f"   ⚠️ Не удалось вписать путь: {type(e).__name__}: {e}")
            return False
        after = value()
        if after != target_path and not same(after):
            log_cb("   ⚠️ Путь экспорта в поле имени так и не установился")
            return False
        return True

    @staticmethod
    def _csv_option_roles(combos):
        """Какой выпадающий список окна параметров CSV за что отвечает.

        Живой дамп 06.10.2026 (Р7 2026.3.2): у трёх QComboBox нет ни имени, ни
        automation_id — только порядок «кодировка, конец строки, разделитель».
        Порядок держать опасно: в другой сборке журнал перепутал бы кодировку
        с разделителем. Поэтому роль — по содержимому списка (кодовые
        страницы, «LF (0x0A …», «Запятая»/«Comma»), порядок — запасной путь.

        Returns:
            dict: {"encoding", "line_end", "delimiter"} → элемент или None.
        """
        def _items(cb):
            try:
                return " | ".join(cb.texts()).lower()
            except Exception:
                return ""

        roles = {"encoding": None, "line_end": None, "delimiter": None}
        rest = []
        for cb in combos:
            items = _items(cb)
            if roles["line_end"] is None and "0x0a" in items:
                roles["line_end"] = cb
            elif roles["delimiter"] is None and ("запятая" in items or "comma" in items):
                roles["delimiter"] = cb
            elif roles["encoding"] is None and ("utf-8" in items or "65001" in items):
                roles["encoding"] = cb
            else:
                rest.append(cb)
        for role, cb in zip(("encoding", "line_end", "delimiter"), combos):
            if roles[role] is None and cb in rest:
                roles[role] = cb
                rest.remove(cb)
        return roles

    def _confirm_csv_options(self, log_cb=None, timeout=None):
        """Подтверждает окно «Выбрать параметры CSV» кнопкой OK (UI Automation).

        После «Сохранить как» в CSV и предупреждения о потере функций Р7
        показывает ещё одно окно — кодировка, BOM, конец строки, разделитель.
        Это отдельное окно ОС (Qt5152QWindowIcon) с виджетами Qt без своих
        HWND: win32gui кнопок не видит, в DOM редактора окна нет. UI
        Automation видит всё (QComboBox, QCheckBox, QPushButton «OK»/«Отмена»)
        — проверено вживую 29.09.2026. Без этого экспорт в CSV никогда не
        доходил до конвертации и кончался таймаутом 120 с.

        Параметры не меняются — берутся значения по умолчанию, чтобы замер
        был воспроизводим; выбранные значения пишутся в лог. Время, пока окно
        ждало ответа, относится к собственным паузам (_paced_total): Р7 в это
        время простаивает, дожидаясь пользователя.

        Returns:
            dict | None: {"encoding", "delimiter", "line_end", "bom"} — что
            было выбрано, либо None, если окно не появилось или OK не нажат.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if timeout is None:
            timeout = self.CSV_OPTIONS_TIMEOUT_SEC
        if not (env.WIN32_OK and env.PYWINAUTO_OK):
            log_cb("   ⚠️ Окно параметров CSV закрыть нечем (нет pywin32/pywinauto)")
            return None
        deadline = time.perf_counter() + timeout
        hwnd = None
        _owners = self._r7_window_owner_pids()
        while time.perf_counter() < deadline:
            hwnd = self._find_window_hwnd(*self.CSV_OPTIONS_TITLES, owner_pids=_owners)
            if hwnd:
                break
            time.sleep(0.05)
        if not hwnd:
            log_cb(f"   ⚠️ Окно «Выбрать параметры CSV» не появилось за {timeout:.0f} с")
            return None
        shown_at = time.perf_counter()
        try:
            dlg = windows.uia_window(hwnd)
            combos = dlg.descendants(control_type="ComboBox")

            def _sel(cb):
                try:
                    return cb.selected_text()
                except Exception:
                    try:
                        return cb.window_text()
                    except Exception:
                        return None
            roles = self._csv_option_roles(combos)
            chosen = {"encoding": _sel(roles["encoding"]) if roles["encoding"] else None,
                      "line_end": _sel(roles["line_end"]) if roles["line_end"] else None,
                      "delimiter": _sel(roles["delimiter"]) if roles["delimiter"] else None,
                      "bom": None}
            try:
                boxes = dlg.descendants(control_type="CheckBox")
                if boxes:
                    chosen["bom"] = bool(boxes[0].get_toggle_state())
            except Exception as e:
                log_cb(f"   ⚠️ Состояние BOM в окне CSV не прочиталось ({type(e).__name__}: {e})")
            ok = [b for b in dlg.descendants(control_type="Button")
                  if (b.window_text() or "").strip().upper() == "OK"]
            if not ok:
                log_cb("   ⚠️ В окне параметров CSV нет кнопки OK")
                return None
            ok[0].invoke()
        except Exception as e:
            log_cb(f"   ⚠️ Окно параметров CSV не подтверждено: {type(e).__name__}: {e}")
            return None
        # Р7 ждал ответа пользователя — это не работа, из замера вычитаем.
        self._paced_total += time.perf_counter() - shown_at
        log_cb(f"   ✅ Окно параметров CSV подтверждено (OK): кодировка "
               f"«{chosen['encoding']}», разделитель «{chosen['delimiter']}», "
               f"конец строки «{chosen['line_end']}», BOM {chosen['bom']}")
        return chosen

    def _dismiss_saveas_format_warning(self, exclude_hwnd, main_hwnd=None, timeout=3.0, log_cb=None):
        """Закрывает диалог-предупреждение о потере функций формата
        («некоторые возможности документа могут быть потеряны»), если он
        появился после «Сохранить» в диалоге «Сохранить как».

        НАЙДЕНО ЖИВЫМ ПРОГОНОМ (26.08.2026): для CSV после подтверждения
        имени файла всплывает ВТОРОЙ диалог — обычный native Win32 (не
        DirectUI), с кнопками OK/Отмена. Кнопка вложена не прямым
        потомком (под DirectUIHWND-обёрткой), поэтому нужен рекурсивный
        обход (`EnumChildWindows`), а не `FindWindowEx` (только прямые
        дети — не находит). Жмём OK: это «сохранить как выбрано»,
        симметрично уже принятому решению пользователя о формате;
        «Отмена» откатила бы весь Save As.

        БАГ, НАЙДЕННЫЙ ЖИВЫМ ПРОГОНОМ 27.08.2026 (пользователь наблюдал
        экран: диалог реально висел с кнопками OK/Отмена, а лог писал
        «кнопка OK — нет»): поиск шёл по подстроке заголовка
        «р7-офис»/«r7-office» с исключением только уже закрытого диалога
        «Сохранить как» (`exclude_hwnd`). Заголовок ГЛАВНОГО окна редактора
        («...— Р7-Офис. Профессиональный (десктопная версия)») ТОЖЕ
        содержит эту подстроку — и он уже виден на экране в момент самого
        первого вызова `_find_window_hwnd`, ДО того как второй диалог вообще
        успевает появиться. Первый же вызов защёлкивался на главном окне,
        `confirm_hwnd is not None` сразу становилось истиной, цикл ожидания
        ни разу не перезапрашивал — и `EnumChildWindows` дальше искал кнопку
        OK у Qt+CEF главного окна, где её в принципе нет. Теперь главное
        окно (`main_hwnd`) исключается из поиска наравне с `exclude_hwnd`.

        Для форматов без потери данных (ods/xltx/pdf) этот диалог, судя по
        живым прогонам, не появляется — вызов с коротким timeout просто
        ничего не находит и возвращает False быстро, без побочных эффектов.

        ПОСЛЕ CSV замечен ЕЩЁ один, третий диалог Р7 (выбор разделителя/
        кодировки CSV) — этот метод его не обрабатывает; см. docstring
        save_as_format про открытый статус CSV.

        Args:
            exclude_hwnd: HWND исходного диалога «Сохранить как» — не
                считается «вторым диалогом», даже если ещё видим.
            main_hwnd: HWND главного окна Р7-Офис — тоже исключается из
                поиска (см. «БАГ» выше). None — старое поведение (только
                exclude_hwnd), оставлено для обратной совместимости с
                вызывающим кодом, который ещё не передаёт это значение.
            timeout: Сколько секунд ждать появления диалога.
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            bool: True — диалог найден и OK нажата; False — не появился
            за timeout, либо кнопка не найдена.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if not env.WIN32_OK:
            return False

        excludes = {exclude_hwnd} | ({main_hwnd} if main_hwnd else set())
        _owners = self._r7_window_owner_pids()   # чужие окна с «Р7-Офис» в заголовке — мимо

        def _find_dialog():
            # Окна редактора (класс Qt…QWindowIcon) — не диалог: Win32-кнопок
            # у них нет. После экспорта в ODS заголовок окна документа
            # становится «temp_export….ods — Р7-Офис», и main_hwnd его не
            # исключал: поиск защёлкивался на нём, настоящий диалог не
            # нажимался, и файл XLTX не появлялся за 120 с (живой прогон
            # 07.10.2026). Такие окна исключаем и ищем дальше.
            while True:
                h = self._find_window_hwnd("р7-офис", "r7-office", exclude=excludes,
                                           owner_pids=_owners)
                if h is None:
                    return None
                try:
                    cls = windows.window_class(h)
                except Exception:  # окно исчезло — ищем дальше без него
                    cls = ""
                if not cls.startswith("Qt"):
                    return h
                excludes.add(h)

        confirm_hwnd = _find_dialog()
        deadline = time.perf_counter() + timeout
        while confirm_hwnd is None and time.perf_counter() < deadline:
            time.sleep(0.05)
            confirm_hwnd = _find_dialog()
        if confirm_hwnd is None:
            return False
        shown_at = time.perf_counter()   # окно ждёт ответа — это не работа Р7

        # Найденное окно уже существует, но его дочерние контролы под
        # DirectUIHWND могут ещё не быть созданы в момент самого первого
        # EnumChildWindows — та же гонка отрисовки, что и с раскрытием
        # списка типов/полем имени в _uia_select_saveas_type (см. её
        # docstring). Живой прогон 27.08.2026: без ретраев кнопка OK через
        # раз не находилась на реально открытом диалоге с этой же кнопкой.
        ok_btn = [None]
        def _find_ok(h, _):
            if ok_btn[0] is not None:
                return
            try:
                if windows.window_class(h) == "Button" and windows.window_text(h) == "OK":
                    ok_btn[0] = h
            except Exception:  # окно исчезло во время перебора — ищем OK дальше
                pass
        btn_deadline = time.perf_counter() + 1.0
        while ok_btn[0] is None:
            windows.enum_child_windows(confirm_hwnd, _find_ok, None)
            if ok_btn[0] is not None or time.perf_counter() >= btn_deadline:
                break
            time.sleep(0.1)

        if not ok_btn[0]:
            try:
                cls = windows.window_class(confirm_hwnd)
                title = windows.window_text(confirm_hwnd)
            except Exception:
                cls, title = "?", "?"
            log_cb(f"   ⚠️ Диалог-предупреждение формата найден (hwnd={confirm_hwnd} "
                   f"class={cls!r} title={title!r}), но кнопка OK — нет")
            return False

        log_cb("   ⚠️ Диалог-предупреждение формата (потеря функций) — жму OK")
        clicked_at = time.perf_counter()
        windows.send_button_click(ok_btn[0])
        self._paced_total += max(0.0, clicked_at - shown_at)
        return True

    def _try_cdp_saveas(self, hwnd, log_cb=None):
        """Открывает диалог «Сохранить как» кликом по DOM через CDP — в
        обход синтетической клавиатуры целиком, а не просто другим способом
        доставить тот же акселератор (в отличие от Alt+F и WM_COMMAND,
        которые всё ещё зависят от того, доходит ли ввод ОС до CEF-
        поверхности редактора — тот самый канал, где ломается сам
        Ctrl+Shift+S).

        ПОДТВЕРЖДЕНО ЖИВЫМ Р7 (27.08.2026, tests/manual_saveas_cdp_probe.py,
        два независимых прогона): вкладка «Файл» — ribbon-таб
        (`<li class="ribtab"><a>Файл</a></li>`), не пункт классического меню
        и не элемент с id вида `fm-btn`/`file` (обе эти эвристики в живом
        дампе дали 0 совпадений) — нашлась только точным совпадением текста
        среди широкого набора тегов. Клик открывает панель «Файл» на весь
        экран; пункт «Сохранить как» внутри нее — тоже `<a>`, без обёртки
        `.dropdown-menu`/`[role="menuitem"]`/`.modal`, под которую заточен
        `click_menu_item` (тоже 0 совпадений там же) — нужен
        `click_ribbon_item` с более широким `_RIBBON_PANEL_SEL`. Полный
        цикл (клик «Файл» → клик «Сохранить как» → появление настоящего
        Win32-диалога) подтверждён от начала до конца.

        Args:
            hwnd: HWND главного окна Р7-Офис. Самим кликом не используется
                (весь механизм — DOM через CDP, не Win32) — принят для
                симметрии с `_try_wm_command_saveas` и на случай будущей
                проверки, что это по-прежнему то же самое окно.
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            bool: True — диалог «Сохранить как» подтверждённо появился
                (через `_wait_for_window_title`). False — CDP недоступен,
                вкладка «Файл» или пункт «Сохранить как» не нашлись в DOM,
                либо диалог не появился за отведённое время. Ничего не
                бросает — любая ошибка гасится и превращается в False,
                чтобы `save_as_format` спокойно откатился на старую цепочку
                хоткей → меню → WM_COMMAND → SKIP.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        log_cb('   🔍 CDP: ищем кнопку "Сохранить как"...')
        try:
            if not self._cdp_ensure_connected(log_cb=log_cb):
                log_cb("   ⚠️ CDP: кнопка не найдена (соединение недоступно)")
                return False
            connector = self._webdriver_connector

            baseline = connector.dump_visible_ui() or []
            res = connector.click_ribbon_item(["файл", "file"], timeout=5)
            if not (res and res.get("clicked")):
                log_cb("   ⚠️ CDP: кнопка не найдена (вкладка «Файл»)")
                return False
            log_cb(f"   ✅ CDP: кнопка найдена, кликаю... "
                   f"({res.get('tag')} {res.get('text')!r})")

            # Панель «Файл» рисуется не мгновенно — без паузы второй клик
            # рискует не найти ещё не отрисованные пункты (см.
            # OP_CDP_PANEL_PACE_SEC). Р7 в этот момент реагирует на первый
            # клик, поэтому пауза вычитается из замера, как и остальные
            # паузы на отрисовку меню/модалок в этом файле.
            self._pace(self.OP_CDP_PANEL_PACE_SEC)

            res2 = connector.click_ribbon_item(
                ["сохранить как", "save as"], baseline=baseline, timeout=5)
            if not (res2 and res2.get("clicked")):
                log_cb('   ⚠️ CDP: кнопка не найдена (пункт «Сохранить как» в панели «Файл»)')
                return False
            log_cb(f"   ✅ CDP: кликаю... ({res2.get('text')!r})")

            if self._wait_for_window_title(("сохранить как", "save as"), timeout=5.0):
                log_cb('   ✅ CDP: диалог "Сохранить как" открыт')
                return True
            log_cb('   ⚠️ CDP: клик прошёл, но диалог "Сохранить как" не появился')
            return False
        except Exception as e:
            log_cb(f"   ⚠️ CDP: ошибка при попытке открыть диалог ({type(e).__name__}: {e})")
            return False

    def _try_wm_command_saveas(self, hwnd, log_cb=None):
        """Пытается открыть диалог «Сохранить как» через классическое меню
        окна (`WM_COMMAND`), в обход синтетических клавиш — запасной путь
        на случай, если ни `Ctrl+Shift+S`, ни навигация `Alt+F` не доводят
        акселератор до CEF-поверхности редактора (см. `save_as_format`,
        живой прогон 26.08.2026 — оба способа воспроизводимо не срабатывали
        даже после перезагрузки машины).

        НЕ ПРОВЕРЕНО ЖИВЫМ Р7: Р7-Офис — приложение Qt+CEF, и панель
        инструментов рисуется самим CEF без нативных Win32-виджетов (та же
        причина, по которой кнопка «Жирный» не находится через `win32gui` —
        см. CLAUDE.md). Неизвестно, использует ли строка меню верхнего окна
        классический `HMENU` или тоже нарисована поверх CEF. Метод не
        гадает — если `GetMenu(hwnd)` не вернул меню, честно возвращает
        `False` с диагностикой в лог, а не изображает успех.

        Args:
            hwnd: HWND главного окна Р7-Офис.
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            bool: True — команда «Сохранить как» найдена в меню и
                `WM_COMMAND` отправлен. НЕ гарантирует, что диалог
                открылся — это, как и после хоткея/Alt+F, проверяет
                вызывающий код через `_wait_for_window_title`.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if not env.WIN32_OK or not hwnd:
            log_cb("   🔍 WM_COMMAND: WIN32_OK=False или hwnd отсутствует — способ недоступен")
            return False
        try:
            menu = windows.get_menu(hwnd)
        except Exception as e:
            log_cb(f"   🔍 WM_COMMAND: GetMenu упал ({e})")
            return False
        if not menu:
            log_cb("   🔍 WM_COMMAND: у окна нет классического HMENU "
                   "(меню, вероятно, рисует сама Р7 поверх CEF) — способ недоступен")
            return False
        try:
            file_menu = None
            for i in range(windows.menu_item_count(menu)):
                text, _wid, submenu = self._menu_item_info(menu, i)
                label = (text or "").replace("&", "").lower()
                if "файл" in label or "file" in label:
                    file_menu = submenu
                    break
            if not file_menu:
                log_cb("   🔍 WM_COMMAND: пункт «Файл» не найден в меню окна")
                return False
            save_as_id = None
            for i in range(windows.menu_item_count(file_menu)):
                text, wid, _sub = self._menu_item_info(file_menu, i)
                label = (text or "").replace("&", "").lower()
                if "сохранить как" in label or "save as" in label:
                    save_as_id = wid
                    break
            if not save_as_id or save_as_id == -1:
                log_cb("   🔍 WM_COMMAND: пункт «Сохранить как» не найден в подменю «Файл»")
                return False
        except Exception as e:
            log_cb(f"   🔍 WM_COMMAND: не удалось разобрать меню ({e})")
            return False
        log_cb(f"   🔍 WM_COMMAND: нашёл «Сохранить как» (id={save_as_id}), отправляю WM_COMMAND")
        try:
            windows.post_command(hwnd, save_as_id)
        except Exception as e:
            log_cb(f"   🔍 WM_COMMAND: PostMessage упал ({e})")
            return False
        return True


