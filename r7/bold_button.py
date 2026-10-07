"""Маркер готовности документа — кнопка «Жирный» на панели Р7.

Через CDP — по DOM редактора (основной путь), запасной — через win32gui по
дочерним окнам. Подробности — docs/readiness.md. BoldButtonMixin — методы,
которые R7Testovarka получает наследованием.
"""
import time
from r7 import env


class BoldButtonMixin:
    """Кнопка «Жирный» как маркер готовности — часть R7Testovarka."""

    # Доп. триггер: кнопка «Жирный» на панели инструментов. Работает только
    # если она существует как отдельное нативное окно Win32 (класс "Button"
    # или "ToolbarButton") — см. предупреждение в docstring _wait_for_bold_button.
    # На практике это условие не выполняется ни для CEF-панели (текущая
    # сборка — HTML в одном render-окне), ни для классического Win32
    # ToolbarWindow32 (общий контрол сам рисует кнопки, у них тоже нет
    # отдельного HWND) — реалистичного билда, где сработает эта ветка, не
    # определено; см. docstring _is_bold_button_visible.
    # Однобуквенные метки ("b", "ж") сознательно из спецификации — риск: на
    # гипотетической сборке, где EnumChildWindows всё же находит что-то
    # подходящее по классу, ЛЮБАЯ кнопка с такой короткой подписью (не
    # обязательно именно "Жирный") будет принята без дополнительной проверки.
    BOLD_BUTTON_LABELS     = ("b", "ж", "жирный", "bold")  # регистронезависимо
    BOLD_BUTTON_CLASSES    = ("Button", "ToolbarButton")
    BOLD_BUTTON_POLL_SEC   = 0.1
    BOLD_BUTTON_TIMEOUT_SEC = 3.0
    # Верхняя граница именно на ПОДКЛЮЧЕНИЕ к CDP-порту (connector.connect()
    # внутри _wait_for_bold_button_cdp), не на весь бюджет BOLD_BUTTON_TIMEOUT_SEC.
    # _prepare_webdriver_launch создаёт коннектор всегда, когда порт 8080
    # свободен, — независимо от того, откроет ли его сама сборка Р7. Если
    # не откроет, connect() без этой границы опрашивал бы /json циклом до
    # 2 с впустую (порт закрыт => _pick_target() сразу None => sleep(poll_sec)
    # по кругу), а _wait_for_bold_button_cdp вызывается уже ПОСЛЕ того, как
    # остальные признаки готовности совпали — то есть это время инфлировало
    # бы прямо замер «Открытие файла» на каждой сборке без реального CDP.
    BOLD_BUTTON_CDP_CONNECT_TIMEOUT_SEC = 0.5
    BOLD_STABLE_SEC = 0.5        # кнопка «Жирный» должна простоять доступной столько
    BOLD_PROBE_TIMEOUT_SEC = 0.3 # таймаут одной пробы кнопки (рендерер занят — не ждём)


    def _early_connector(self):
        """Коннектор запуска, подключённый уже во время открытия файла.

        Пока редактор грузится, цели в /json может ещё не быть — подключение
        пробуется не чаще раза в секунду с коротким таймаутом, чтобы не
        тормозить цикл детектора готовности.

        Returns:
            R7WebDriverConnector | None
        """
        connector = self._webdriver_connector
        if connector is None:
            return None
        if getattr(connector, "connected", False):
            return connector
        now = time.perf_counter()
        if now - getattr(self, "_early_connect_at", 0.0) < 1.0:
            return None
        self._early_connect_at = now
        try:
            return connector if connector.connect(timeout=0.2) else None
        except Exception:
            return None

    def _bold_ready_probe(self):
        """Проба кнопки «Жирный» — основной маркер готовности документа.

        См. R7WebDriverConnector.bold_ready_probe. Ошибки не фатальны: None
        означает «не знаем», детектор продолжает по CPU.

        Returns:
            dict | None
        """
        connector = self._early_connector()
        if connector is None or not hasattr(connector, "bold_ready_probe"):
            return None
        try:
            return connector.bold_ready_probe(timeout=self.BOLD_PROBE_TIMEOUT_SEC)
        except Exception:
            return None

    def _find_bold_button_hwnd(self, hwnd):
        """Ищет окно кнопки «Жирный» среди ВСЕХ потомков hwnd (рекурсивно,
        через EnumChildWindows — не FindWindowEx с проверкой только прямых
        детей: реальная кнопка, если она вообще существует как нативное
        окно, почти наверняка вложена глубже одного уровня).

        Совпадением считается окно с классом из BOLD_BUTTON_CLASSES и
        текстом из BOLD_BUTTON_LABELS (без учёта регистра) — независимо от
        текущего состояния enabled/disabled, это отдельная проверка.

        Args:
            hwnd: Окно Р7-Офис, в поддереве которого искать кнопку.

        Returns:
            int | None: hwnd найденной кнопки, либо None.
        """
        if not (env.WIN32_OK and hwnd):
            return None

        import win32gui

        needles = set(self.BOLD_BUTTON_LABELS)
        found = [None]

        def _walk(h, _):
            if found[0] is not None:
                return
            try:
                cls = win32gui.GetClassName(h)
                if cls not in self.BOLD_BUTTON_CLASSES:
                    return
                # "&" — маркер мнемоники Win32 (подчёркивает следующую букву
                # при Alt), не часть подписи: подпись "&B" на экране выглядит
                # как "B". Без снятия "&" сравнение "&b" == "b" не совпало бы,
                # и кнопка с настоящим акселератором осталась бы незамеченной.
                text = win32gui.GetWindowText(h).replace("&", "").strip().lower()
                if text in needles:
                    found[0] = h
            except Exception:  # окно исчезло во время перебора — ищем кнопку дальше
                pass

        try:
            win32gui.EnumChildWindows(hwnd, _walk, None)
        except Exception:
            return None
        return found[0]

    def _is_bold_button_visible(self, hwnd):
        """Проверяет, доступна ли на панели инструментов Р7 кнопка «Жирный»
        (найдена через _find_bold_button_hwnd и IsWindowEnabled() — True).

        ПРОВЕРЕНО ЭКСПЕРИМЕНТАЛЬНО (не предположение): на установленной здесь
        сборке (2026.2.2.x) панель инструментов — не набор нативных Win32-
        виджетов, а HTML внутри окна рендера CEF. Полный дамп дерева дочерних
        окон главного hwnd через win32gui.EnumChildWindows дал 40 узлов, все
        классов Qt5152QWindowIcon / CefBrowserWindow / Chrome_WidgetWin_0 /
        Chrome_RenderWidgetHostHWND — ни одного "Button" или "ToolbarButton".
        Сама кнопка реально существует, но в DOM: запуск с флагом
        --ascdesktop-support-debug-info открывает отладочный порт Chrome
        DevTools Protocol (см. вывод "DevTools listening on ws://..." в
        консоли), через который она была найдена как
        <button id="id-toolbar-btn-bold" class="btn btn-toolbar"> внутри
        iframe apps/spreadsheeteditor/main/ — на 3 уровня вложенности глубже
        top-level окна, недостижима никаким Win32 API в принципе.

        Метод оставлен на случай другой версии/сборки Р7, где панель
        нарисована классическими Win32-виджетами; _wait_until_r7_ready
        корректно откатывается на CPU+WM_NULL, если кнопка не находится (и,
        как показывает проверка выше, на этой сборке будет откатываться
        всегда).

        Args:
            hwnd: Окно Р7-Офис, в поддереве которого искать кнопку.

        Returns:
            bool: True, если кнопка найдена и включена.
        """
        btn = self._find_bold_button_hwnd(hwnd)
        if btn is None:
            return False
        try:
            import win32gui
            return bool(win32gui.IsWindowEnabled(btn))
        except Exception:
            return False

    def _wait_for_bold_button(self, hwnd, timeout=None):
        """Ждёт, пока кнопка «Жирный» на панели инструментов Р7 станет
        доступна, либо не истечёт timeout.

        Сначала дешёвая разовая проверка существования окна вообще
        (_find_bold_button_hwnd). Если его нет — как на CEF-сборках, см.
        _is_bold_button_visible, — возвращает False немедленно, не тратя
        timeout впустую: кнопки нет, ждать нечего. Иначе опрашивает
        _is_bold_button_visible каждые BOLD_BUTTON_POLL_SEC, пока кнопка не
        станет доступна — окно ищется заново на каждом опросе (не
        кэшируется), это устойчивее к случаю, если панель успеет
        перестроиться, и стоит того при бюджете в единицы секунд.

        Отдельная, ограниченная по времени фаза внутри _wait_until_r7_ready —
        не основной цикл опроса готовности. Вызывается не более одного раза
        за вызов _wait_until_r7_ready (см. его docstring).

        Args:
            hwnd: Окно Р7-Офис.
            timeout: Секунд ожидания; по умолчанию BOLD_BUTTON_TIMEOUT_SEC.

        Returns:
            bool: True, если кнопка стала доступна в пределах timeout.
        """
        # Сначала — дешёвая разовая проверка существования окна вообще.
        # Если его нет (как на CEF-сборках, см. _is_bold_button_visible),
        # выходим сразу: незачем тратить timeout на опрос несуществующего
        # окна, IsWindowEnabled() на каждой итерации всё равно даст ту же
        # ошибку.
        if self._find_bold_button_hwnd(hwnd) is None:
            return False

        if timeout is None:
            timeout = self.BOLD_BUTTON_TIMEOUT_SEC
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self._is_bold_button_visible(hwnd):
                return True
            time.sleep(self.BOLD_BUTTON_POLL_SEC)
        return False

    def _wait_for_bold_button_cdp(self, timeout, log_cb):
        """Пробует подтвердить готовность через CDP-коннектор текущего
        запуска (self._webdriver_connector). Основной триггер — пробуется
        ПЕРЕД win32gui-версией (_wait_for_bold_button) в _wait_until_r7_ready:
        в отличие от неё, реально видит DOM внутри CEF-рендера.

        Любая ошибка (нет соединения, порт не открылся, исключение
        Selenium/websocket) не фатальна — метод просто возвращает False, и
        вызывающий код откатывается на win32gui/CPU-логику.

        Args:
            timeout: Секунд на подключение и опрос кнопки суммарно.
            log_cb: Функция логирования.

        Returns:
            bool: True, если кнопка «Жирный» доступна (найдена и не disabled).
        """
        connector = self._webdriver_connector
        if connector is None:
            # Диагностика: без этой строки в логе неотличимы два разных
            # случая — "коннектор создан, но CDP не ответил" и "коннектор
            # вообще не был создан при запуске" (WEBDRIVER_OK=False, либо
            # все кандидаты портов заняты — см. _prepare_webdriver_launch).
            log_cb(
                f"🔌 WebDriver: CDP-коннектор не создан для этого запуска "
                f"(WEBDRIVER_OK={env.WEBDRIVER_OK}, "
                f"порт={self._current_webdriver_port}) — пропускаю CDP-триггер"
            )
            return False

        deadline = time.perf_counter() + timeout
        try:
            log_cb(f"🔌 WebDriver: попытка подключения к CDP на порту {connector.port}...")
            connect_timeout = max(0.1, min(self.BOLD_BUTTON_CDP_CONNECT_TIMEOUT_SEC, timeout))
            if not connector.connect(timeout=connect_timeout):
                log_cb(f"⚠️ CDP недоступен на порту {connector.port} (порт не открылся "
                       f"или Р7 запущен без --ascdesktop-support-debug-info), использую fallback")
                return False

            cdp_start = time.perf_counter()
            while time.perf_counter() < deadline:
                state = connector.bold_button_state()
                if state and state.get("found") and not state.get("disabled"):
                    elapsed = time.perf_counter() - cdp_start
                    log_cb(f"✅ Кнопка 'Жирный' доступна (CDP, {elapsed:.2f} с)")
                    return True
                time.sleep(self.BOLD_BUTTON_POLL_SEC)

            log_cb("⚠️ CDP: кнопка не стала доступна за отведённое время, использую fallback")
            return False
        except Exception as e:
            log_cb(f"⚠️ CDP недоступен, использую fallback ({type(e).__name__}: {e})")
            return False
