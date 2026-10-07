"""Окна Р7-Офис: поиск, фокус, клавиши, кнопки диалогов, геометрия окна.

Окно Р7 — только окно процесса Р7: заголовка мало, «Р7-Офис» бывает и во
вкладке браузера (поиск по одному заголовку однажды закрыл Chrome
пользователя). Клавиши — только через _hotkey/_press: перед нажатием они
проверяют, что на переднем плане окно Р7. WindowsMixin — методы, которые
R7Testovarka получает наследованием.
"""
import ctypes
import time

from r7 import env
from r7.env import psutil, pyautogui, win32api, win32con, win32gui


def _escape_send_keys(text):
    """Экранирует текст для pywinauto type_keys/send_keys.

    В их синтаксисе ~ — Enter, + ^ % — модификаторы, ( ) — группировка,
    { } — имена клавиш. Каждый такой символ оборачивается в фигурные скобки
    ("{~}"), остальные идут как есть.
    """
    return "".join("{" + ch + "}" if ch in "~+^%(){}" else ch for ch in text)


# ── Тонкие обёртки Win32 для модулей вне границы ──────────────────────────
# Правило «Переносимость» (CLAUDE.md): win32*, pywinauto и ctypes.windll —
# только в r7/env.py, r7/windows.py, r7/versions.py, r7/x2t_files.py. Модули
# вне границы зовут эти функции: один вызов API на функцию, исключения не
# перехватываются — их, как и прежде, ловит вызывающий. Модули pywin32 —
# имена из r7.env этого модуля: тесты подменяют `r7.windows.win32gui` и т. п.
# или атрибуты самого pywin32 ("win32gui.IsWindow").

def is_user_an_admin():
    """shell32.IsUserAnAdmin — зовёт только r7/privileges.py (is_admin)."""
    return ctypes.windll.shell32.IsUserAnAdmin()


def shell_execute_function():
    """shell32.ShellExecuteW как вызываемый объект — перезапуск под UAC
    (r7/elevation.py)."""
    return ctypes.windll.shell32.ShellExecuteW


class WindowsMixin:
    """Поиск окон Р7, фокус и клавиши — часть R7Testovarka (через наследование)."""

    @staticmethod
    def _get_dpi_scale_pct():
        """Текущий множитель масштабирования экрана Windows (100 = 100%).

        `GetScaleFactorForDevice` (shcore.dll) — недокументированный в
        ctypes напрямую, но стабильный публичный Win32 API с Windows 8.1;
        не требует pywin32. Индекс 0 — основной монитор: у стенда с
        несколькими мониторами R7-Офис запускается на нём же (окно
        разворачивается/двигается через win32gui без выбора монитора).

        Returns:
            int | None: Процент масштаба, либо None — не Windows 8.1+,
            либо API недоступен по любой другой причине (виртуалка без
            shcore, ошибка вызова). Отсутствие значения не должно ронять
            прогон — это диагностическое поле отчёта, не условие теста.
        """
        try:
            return int(ctypes.windll.shcore.GetScaleFactorForDevice(0))
        except Exception:
            return None

    def _fix_r7_window_geometry(self, hwnd, log_cb=None):
        """Разворачивает окно Р7-Офис на фиксированный R7_WINDOW_W×R7_WINDOW_H
        вместо простого maximize() — см. комментарий у констант (L3, этап 3).

        Если экран меньше цели по любой из сторон — подгоняет под реальный
        размер экрана (без этого MoveWindow на 1920×1080 на мониторе
        1366×768 обрезал бы окно) и пишет предупреждение в лог: результаты
        такого прогона сравнивать с прогонами на полноразмерном экране
        нельзя, но сам тест не проваливается из-за маленького монитора.

        Args:
            hwnd: Дескриптор окна Р7-Офис.
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            dict | None: {"width", "height"} фактически применённого
            размера, либо None — WIN32_OK=False, hwnd пуст, либо вызов
            win32-API упал.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if not (env.WIN32_OK and hwnd):
            return None
        try:
            screen_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            screen_h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
            target_w = min(self.R7_WINDOW_W, screen_w)
            target_h = min(self.R7_WINDOW_H, screen_h)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.MoveWindow(hwnd, 0, 0, target_w, target_h, True)
            time.sleep(0.3)
            applied = {"width": target_w, "height": target_h}
            if target_w < self.R7_WINDOW_W or target_h < self.R7_WINDOW_H:
                log_cb(f"⚠️ Экран {screen_w}x{screen_h} меньше цели "
                       f"{self.R7_WINDOW_W}x{self.R7_WINDOW_H} — окно Р7 "
                       f"подогнано под {target_w}x{target_h}")
            self._applied_r7_window_size = applied
            return applied
        except Exception as e:
            log_cb(f"⚠️ Не удалось зафиксировать размер окна Р7: {e}")
            return None

    def _press_esc_in_r7(self, hwnd):
        """Шлёт Esc в окно Р7, если оно на переднем плане.

        Слепой ввод в чужое окно недопустим: без подтверждённого фокуса
        клавиша не отправляется.

        Returns:
            bool: True — Esc отправлен в окно Р7.
        """
        if not (env.PYAUTOGUI_OK and env.WIN32_OK and hwnd):
            return False
        try:
            if win32gui.GetForegroundWindow() != hwnd:
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.1)
            if win32gui.GetForegroundWindow() != hwnd:
                return False
            self._press('esc')
            return True
        except Exception:
            return False

    def _wait_for_window_title(self, substrings, timeout=3.0):
        """Ждёт появления видимого окна с подходящим заголовком.

        Время ожидания — это реакция Р7-Офис, поэтому оно НЕ вычитается из
        замера (в отличие от _pace). Вызывающий код вычитает его сам, если
        ожидание оказалось безрезультатным.

        Args:
            substrings: Подстроки заголовка (без учёта регистра).
            timeout: Максимум секунд ожидания.

        Returns:
            bool: True, если окно появилось.
        """
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self._win_title_contains(*substrings):
                return True
            time.sleep(self.OP_POLL_SEC)
        return False

    def _window_responsive(self, hwnd, timeout_ms=None):
        """True, если окно вынуло сообщение из очереди за timeout_ms.

        WM_NULL ничего не делает, но SendMessageTimeout возвращается ровно
        тогда, когда окно прокачало очередь сообщений — это прямое измерение
        занятости UI-потока.

        Прежний зонд через self._hotkey('ctrl','End') так не умел:
        keybd_event только кладёт событие во входную очередь и возвращается
        сразу, не дожидаясь обработки. Поэтому его длительность всегда была
        одинаковой (≈0.3 сек — сумма interval и PAUSE самого pyautogui) и о
        состоянии Р7-Офис не говорила ничего.

        Args:
            hwnd: Дескриптор окна Р7-Офис. None или отсутствие pywin32 → True
                (проверка пропускается, решение принимается по CPU).
            timeout_ms: Порог ожидания; по умолчанию READY_RESPONSIVE_MS.

        Returns:
            bool
        """
        if not env.WIN32_OK or not hwnd:
            return True
        if timeout_ms is None:
            timeout_ms = self.READY_RESPONSIVE_MS
        try:
            res = win32gui.SendMessageTimeout(
                hwnd, win32con.WM_NULL, 0, 0,
                win32con.SMTO_ABORTIFHUNG, int(timeout_ms))
        except Exception:
            # pywintypes.error с ERROR_TIMEOUT — окно не разгребает очередь
            return False
        # pywin32 возвращает (result, lresult); result == 0 — тоже таймаут
        if isinstance(res, tuple):
            return bool(res[0])
        return True

    def _win_title_contains(self, *substrings):
        """Returns True if any visible top-level window's title contains
        any of the given substrings (case-insensitive).

        Args:
            *substrings: One or more strings to search for.

        Returns:
            bool
        """
        if not env.WIN32_OK:
            return False
        import win32gui
        found = []
        needles = [s.lower() for s in substrings]
        def _cb(h, _):
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h).lower()
                if any(n in t for n in needles):
                    found.append(h)
        win32gui.EnumWindows(_cb, found)
        return bool(found)

    def _is_r7_window(self, hwnd):
        """True — окно принадлежит процессу Р7-Офис (по имени процесса-владельца).

        Заголовка мало: «Р7-Офис» есть и в заголовке вкладки браузера
        «Техническая поддержка Р7-Офис - Google Chrome». 30.09.2026 тест
        своего файла принял её за окно Р7 и закрыл Chrome через WM_CLOSE, а
        экспорт слал клики и хоткеи в чужое окно. Любое действие над окном
        (фокус, клик, закрытие) — только после этой проверки.
        Без psutil/pywin32 проверить нечем — False: окно не найдётся, и тест
        честно упадёт, а не будет действовать на чужое (было True до
        аудита 06.10.2026).
        """
        if not (env.WIN32_OK and env.PSUTIL_OK):
            return False
        try:
            import win32process
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            return self._matches_r7_process(psutil.Process(pid).name())
        except Exception:
            return False

    def _find_r7_window(self, stem=None):
        """HWND видимого top-level окна Р7-Офис либо None.

        Окно должно и подходить по заголовку («Р7-Офис»/«R7-Office» или имя
        файла), и принадлежать процессу Р7 (_is_r7_window). Из нескольких
        берётся окно с именем файла в заголовке, иначе верхнее по Z-порядку.
        Общий поиск для вкладки «Производительность», Batch и теста своего
        файла — раньше в каждом была своя копия с проверкой только заголовка.

        Args:
            stem: Имя тестового файла без расширения (или его начало).
        """
        if not env.WIN32_OK:
            return None
        import win32gui
        stem_l = (stem or "").lower()
        with_stem, others = [], []

        def _cb(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            t = win32gui.GetWindowText(h).lower()
            has_stem = bool(stem_l) and stem_l in t
            if not (has_stem or "р7-офис" in t or "r7-office" in t):
                return
            if self._is_r7_window(h):
                (with_stem if has_stem else others).append(h)

        win32gui.EnumWindows(_cb, None)
        return (with_stem or others or [None])[0]

    def _ensure_r7_foreground(self):
        """Бросает RuntimeError, если окно на переднем плане — не Р7.

        Клавиатурный путь слал хоткеи в любое активное окно: SetForegroundWindow
        молча не срабатывает, и Ctrl+- или Enter уходили в чужую программу
        (правило 9, аудит 06.10.2026). Проверка стоит ~6 мкс на нажатие —
        внутри замера это ничто против самого нажатия.
        """
        if not env.WIN32_OK:
            raise RuntimeError("pywin32 недоступен — не проверить, что клавиши уйдут в Р7")
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            # Сразу после закрытия диалога окна на переднем плане какое-то
            # мгновение нет вовсе (ревью #39). Ждём до FG_GAP_WAIT_SEC через
            # _pace — Р7 в это время нашего ввода ждёт, пауза вычитается.
            # Чужое окно на переднем плане — отказ сразу, без ожидания.
            waited = 0.0
            while not hwnd and waited < self.FG_GAP_WAIT_SEC:
                self._pace(self.FG_GAP_POLL_SEC)
                waited += self.FG_GAP_POLL_SEC
                hwnd = win32gui.GetForegroundWindow()
        if hwnd and self._is_r7_window(hwnd):
            return
        try:
            title = win32gui.GetWindowText(hwnd) if hwnd else ""
        except Exception:
            title = ""
        raise RuntimeError(f"На переднем плане не Р7 ({title!r}) — клавиши не отправлены")

    FG_GAP_WAIT_SEC = 0.2    # сколько ждать, если окна на переднем плане нет вовсе

    FG_GAP_POLL_SEC = 0.01

    def _focus_r7_window(self, hwnd, log_cb=None):
        """Выводит окно Р7 на передний план и проверяет, что это удалось.

        SetForegroundWindow молча не срабатывает (блокировка фокуса Windows),
        а focus_window сообщал «удалось», как только окно найдено — и потом
        все клавиатурные тесты прогона падали один за другим на
        _ensure_r7_foreground (ревью #39). Не вышло — реальный клик по
        заголовку окна (_ensure_foreground_click).

        Returns:
            bool: окно Р7 на переднем плане.
        """
        if not (env.WIN32_OK and hwnd):
            return False
        import win32gui
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # Windows отказала в фокусе — ниже проверка и клик
            pass
        try:
            fg = win32gui.GetForegroundWindow()
        except Exception:
            fg = None
        if fg and self._is_r7_window(fg):
            return True
        return bool(self._ensure_foreground_click(hwnd, log_cb=log_cb))

    def _hotkey(self, *keys):
        """pyautogui.hotkey, но только в окно Р7 (см. _ensure_r7_foreground)."""
        self._ensure_r7_foreground()
        pyautogui.hotkey(*keys)

    def _press(self, key, presses=1):
        """pyautogui.press, но только в окно Р7 (см. _ensure_r7_foreground)."""
        self._ensure_r7_foreground()
        pyautogui.press(key, presses=presses)

    def _r7_window_owner_pids(self):
        """PID процессов Р7 для проверки владельца окна.

        Всегда множество: пустое, если Р7 не запущен или узнать нечем. Прежде
        в этих случаях возвращался None, а он для _find_window_hwnd значит
        «владельца не проверять» — поиск «Сохранить как» ловил такой же
        диалог Chrome или Проводника (аудит 06.10.2026). Пустое множество не
        совпадёт ни с одним окном.
        """
        if not env.PSUTIL_OK:
            return set()
        try:
            self._r7_pids = None
            return {p.pid for p in self._get_r7_processes(log_cb=lambda *_a: None)}
        except Exception:
            return set()

    def _find_window_hwnd(self, *substrings, exclude=None, owner_pids=None):
        """Возвращает HWND первого видимого top-level окна, чей заголовок
        содержит одну из подстрок (без учёта регистра) — в отличие от
        `_win_title_contains`, отдаёт сам дескриптор, а не bool (нужен для
        UI Automation и других операций поверх конкретного окна).

        Args:
            *substrings: Подстроки заголовка.
            exclude: HWND (или итерируемое HWND), который нужно пропустить,
                даже если подходит по заголовку — например, уже известный
                диалог, или главное окно Р7 (его заголовок вида
                «...— Р7-Офис. Профессиональный...» тоже содержит
                «р7-офис» и без исключения перехватывает поиск ДРУГОГО
                Р7-диалога — см. `_dismiss_saveas_format_warning`).
            owner_pids: Множество PID — окно должно принадлежать одному из
                этих процессов. Без проверки владельца поиск окна
                «р7-офис» находил вкладку браузера «Техническая поддержка
                Р7-Офис - Google Chrome»: настоящее предупреждение осталось
                без ответа, экспорт в CSV и XLTX сорвался (полный прогон
                30.09.2026). None — владелец не проверяется.

        Returns:
            int | None
        """
        if not env.WIN32_OK:
            return None
        import win32gui
        needles = [s.lower() for s in substrings]
        excluded = {exclude} if isinstance(exclude, int) else set(exclude or ())
        found = [None]
        def _cb(h, _):
            if found[0] is not None or h in excluded:
                return
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h).lower()
                if any(n in t for n in needles):
                    if owner_pids is not None:
                        try:
                            import win32process
                            if win32process.GetWindowThreadProcessId(h)[1] not in owner_pids:
                                return
                        except Exception:
                            return
                    found[0] = h
        win32gui.EnumWindows(_cb, None)
        return found[0]

    def _ensure_foreground_click(self, hwnd, log_cb=None, attempts=3, settle=0.2):
        """Реальный клик + SetForegroundWindow, с проверкой через
        GetForegroundWindow — простого SetForegroundWindow (как в
        `focus_window()` внутри `_spreadsheet_worker`) недостаточно для
        глобальных хоткеев (`Ctrl+Shift+S` и т.п.): на живом рабочем столе
        оператора параллельно открытые приложения (браузер, Steam,
        VPN-клиент и т.п.) иногда успевают перехватить фокус в промежутке
        между предыдущей операцией и следующим хоткеем.

        НАЙДЕНО ЖИВЫМ ПРОГОНОМ (26.08.2026, полный набор из 16 тестов на
        реальной 50К-фикстуре, `tests/manual_full_suite_real_fixture.py`):
        все 4 теста сохранения формата синхронно упали с «Ctrl+Shift+S не
        открыл диалог» — `_dump_visible_window_titles` в том же прогоне
        показал открытые сторонние окна (браузер, Steam, VPN-клиент) в
        списке видимых top-level окон. Остальные 12 тестов (все через CDP
        api, не через глобальные хоткеи) в том же прогоне прошли без
        единой ошибки — проблема специфична именно для операций,
        зависящих от системного фокуса окна, не от Р7 самого по себе.

        Args:
            hwnd: HWND окна, которое должно получить фокус.
            log_cb: Функция логирования; по умолчанию self.add_test_log.
            attempts: Сколько раз пробовать (клик + проверка).
            settle: Пауза после клика перед проверкой GetForegroundWindow.

        Returns:
            bool: True — GetForegroundWindow() совпал с hwnd хотя бы раз.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if not env.WIN32_OK or not hwnd:
            return False
        if not self._is_r7_window(hwnd):
            # Клик и хоткеи — только в окно Р7, не в то, что подошло по заголовку.
            log_cb("   ⚠️ Окно не принадлежит Р7-Офис — клик и фокус пропущены")
            return False
        import win32gui
        for attempt in range(attempts):
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:  # Windows отказала в фокусе — ниже клик по заголовку
                pass
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                # ТОЛЬКО заголовок окна (первые ~10 px сверху), НЕ тело
                # документа. НАЙДЕНО ЖИВЫМ ПРОГОНОМ 26.08.2026 (оператор
                # наблюдал экран): на реальной фикстуре строка 1 почти
                # целиком занята автофильтрами — клик туда открывает
                # выпадающее меню фильтра и показывает предупреждение,
                # а не восстанавливает фокус. Раньше здесь пробовали
                # top+40 (тулбар/лента) и центр окна (тело документа) —
                # оба варианта рискуют попасть в контент документа на
                # файлах с иной структурой, чем тестовые фикстуры без
                # фильтров. Клик по заголовку окна безопасен всегда:
                # вне документа в принципе, при этом достаточен для
                # SetForegroundWindow-эквивалентного эффекта (подтверждено
                # тем же прогоном — GetForegroundWindow совпадал).
                pyautogui.click((left + right) // 2, top + 10)
            except Exception:  # клик не прошёл — ниже проверка фокуса и новая попытка
                pass
            time.sleep(settle)
            try:
                if win32gui.GetForegroundWindow() == hwnd:
                    return True
            except Exception:  # окно исчезло — ниже сообщение и новая попытка
                pass
            log_cb(f"   ⚠️ Окно Р7 не в фокусе (попытка {attempt + 1}/{attempts}) — переустанавливаю")
            self._note_interference("focus_lost")
        return False

    def _note_interference(self, kind):
        """Счётчик вмешательств стенда в прогон (чужое окно перехватило
        фокус, чужая программа записала в буфер обмена) — в отчёт как
        «Условия прогона». Сбрасывается в начале прогона (_capture_environment)."""
        counts = self.__dict__.setdefault("_interference", {})
        counts[kind] = counts.get(kind, 0) + 1

    def _menu_item_info(self, hmenu, index):
        """Текст, ID команды и HSUBMENU пункта меню по позиции.

        `win32gui.GetMenuString`/`GetMenuItemID` не экспортированы этой
        сборкой pywin32 (`AttributeError` при вызове — обнаружено при
        написании теста для `_try_wm_command_saveas`); рабочий путь —
        `GetMenuItemInfo` через буфер `win32gui_struct.EmptyMENUITEMINFO()`.

        Returns:
            (text: str | None, wID: int, hSubMenu: int)
        """
        import win32gui
        import win32gui_struct
        buf, _extra = win32gui_struct.EmptyMENUITEMINFO()
        win32gui.GetMenuItemInfo(hmenu, index, True, buf)
        info = win32gui_struct.UnpackMENUITEMINFO(buf)
        return info.text, info.wID, info.hSubMenu

    def _dump_visible_window_titles(self, log_cb=None, limit=15):
        """Пишет в лог заголовки всех видимых top-level окон.

        Диагностика для случая, когда `_wait_for_window_title` не находит
        ожидаемый диалог: не видно, появилось ли окно вовсе, появилось ли
        с другим заголовком (другая локализация/сборка), или диалог —
        HTML-модалка внутри CEF без своего HWND (та же природа, что и у
        диалога «Сохранить изменения?», см. CLAUDE.md). Без этого дампа
        «диалог не открылся» и «диалог открылся, но не с тем заголовком»
        неразличимы по логу.

        Args:
            log_cb: Функция логирования; по умолчанию self.add_test_log.
            limit: Максимум заголовков в одной строке лога.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if not env.WIN32_OK:
            log_cb("   🔍 Окна: WIN32_OK=False, дамп недоступен")
            return
        import win32gui
        titles = []
        def _cb(h, _):
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h)
                if t:
                    titles.append(t)
        win32gui.EnumWindows(_cb, None)
        shown = titles[:limit]
        more = f" (+{len(titles) - limit} ещё)" if len(titles) > limit else ""
        log_cb(f"   🔍 Видимые окна: {shown}{more}")

    def _click_priority_button(self, hwnd, keyword_priority, log_cb=None):
        """Ищет среди дочерних окон hwnd кнопку, текст которой содержит одно
        из ключевых слов (по приоритету — первое совпавшее слово выигрывает),
        и кликает по ней через Win32-сообщения (BM_CLICK, с запасным путём
        через WM_LBUTTONDOWN/UP) — без pyautogui и без зависимости от фокуса.

        Вынесено из _close_update_dialog_if_exists, чтобы тот же код кликал
        по кнопке диалога «Сохранить изменения?» при закрытии Р7-Офис.

        Args:
            hwnd: Родительское окно, чьи дочерние окна перебираются.
            keyword_priority: Кортеж подстрок (без учёта регистра) в порядке
                приоритета.
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            tuple[bool, str | None]: (кликнули ли, текст найденной кнопки).
        """
        if log_cb is None:
            log_cb = self.add_test_log
        import win32gui
        import win32con

        children = []
        def _collect(h, _):
            try:
                children.append((h, win32gui.GetWindowText(h), win32gui.GetClassName(h)))
            except Exception:  # окно исчезло во время перебора — просто без него
                pass
        try:
            win32gui.EnumChildWindows(hwnd, _collect, None)
        except Exception:  # окно уже закрыто — кнопок нет, вернём False
            pass

        for keyword in keyword_priority:
            for h, text, _cls in children:
                if keyword in text.lower():
                    log_cb(f"   Найдена кнопка: «{text}», нажимаю...")
                    try:
                        win32gui.SendMessage(h, win32con.BM_CLICK, 0, 0)
                        return True, text
                    except Exception:  # BM_CLICK не прошёл — ниже запасной клик мышью
                        pass
                    try:
                        win32gui.PostMessage(h, win32con.WM_LBUTTONDOWN,
                                             win32con.MK_LBUTTON, 0)
                        time.sleep(0.05)
                        win32gui.PostMessage(h, win32con.WM_LBUTTONUP, 0, 0)
                        return True, text
                    except Exception:  # кнопка исчезла — ищем следующую по приоритету
                        pass
        if log_cb is not None and children:
            log_cb("   ⚠️ Кнопки для закрытия не найдены. Дочерние окна для диагностики:")
            for h, text, cls in children:
                if text or cls:
                    log_cb(f"      hwnd={h}  class={cls!r}  text={text!r}")
        return False, None
