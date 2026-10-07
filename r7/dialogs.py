"""Закрытие Р7, блокирующие диалоги и запасные пути через интерфейс.

«Сохранить изменения?» — только «Не сохранять» по тексту (кнопка по
умолчанию перезапишет эталонный файл), не нашлась — завершение процессов.
Контекстное меню — Shift+F10 у выделения, пункт — по подписи через DOM;
без CDP тесты «ПКМ» и «меню Вставка» честно падают (docs/ui-fallback.md,
docs/closing-and-dialogs.md). DialogsMixin — методы, которые R7Testovarka
получает наследованием.
"""
import time

from r7 import env


class DialogsMixin:
    """Закрытие Р7, диалоги и запасные пути через интерфейс — часть R7Testovarka."""

    CLOSE_CDP_RETRY_SEC = 1.00   # как часто опрашивать CDP при закрытии Р7
                                 # (_close_r7_gracefully): реже шага цикла в 0.2 с,
                                 # чтобы не спамить websocket-запросами и логом

    # Пункт из дампа считается «уже был в базовом снимке» только если совпал
    # и ключ (тег/id/класс/текст), И положение на экране — с допуском.
    # Строгое совпадение только по ключу пряталось бы за один и тот же
    # generic-маркап: у Р7 и overflow-меню тулбара, и реальный пункт
    # контекстного меню могут быть `<li id="" class="">` с одинаковым
    # текстом (см. issue #9 — ровно так дамп раньше путал два разных
    # элемента). Допуск нужен ровно настолько, чтобы пережить субпиксельный
    # дрожащий рендер одного и того же попапа между снимками, а не чтобы
    # маскировать элемент, реально сидящий в другом месте экрана.
    CDP_ITEM_POSITION_TOLERANCE_PX = 30


    # ---------------------- Поиск пути Р7 ----------------------
    def _monitor_update_dialog(self, stop_event, log_cb=None, interval=2):
        """Background thread: checks for the update dialog every `interval` seconds.

        Args:
            stop_event: threading.Event — set it to stop the loop.
            log_cb: Callable for log output. Defaults to self.add_test_log.
            interval: Seconds between checks.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        while not stop_event.is_set():
            self._close_update_dialog_if_exists(log_cb=log_cb, search_timeout=1)
            stop_event.wait(timeout=interval)

    def _emergency_close_r7(self, find_hwnd, log_cb=None):
        """Закрывает Р7, если воркер завершился, не дойдя до штатного
        закрытия (исключение, ранний return).

        Раньше шаг «Закрытие» был линейным кодом в конце try, и любое
        необработанное исключение оставляло Р7 работать: занятый порт 8080,
        а при следующем запуске — оверлей восстановления после аварии,
        перехватывающий клавиатуру (QA-аудит 29.09.2026, G-05; однажды
        пришлось убивать процесс вручную спустя 8+ минут). Вызывается из
        finally обоих воркеров. Сначала штатное закрытие (оно само
        завершает процессы, если окно не закрылось), при ошибке — сразу
        принудительное.

        Returns:
            bool: True — процессов Р7 не осталось.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        log_cb("⚠️ Прогон прерван до штатного закрытия — закрываю Р7-Офис")
        try:
            hwnd = find_hwnd() if callable(find_hwnd) else find_hwnd
            if hwnd:
                self._close_r7_gracefully(hwnd, log_cb=log_cb, timeout=15)
        except Exception as e:
            log_cb(f"   ⚠️ Штатное закрытие не удалось: {e}")
        self._r7_pids = None
        return self._terminate_r7_processes(log_cb=log_cb)

    def _close_r7_gracefully(self, hwnd, log_cb=None, timeout=10):
        """Закрывает окно Р7-Офис, адресованное конкретным hwnd.

        Раньше закрытие было слепым: Alt+F4 → Right → Enter уходили тому
        окну, что в этот момент имело фокус, — а фокус мог перехватить
        монитор диалога обновления или случайный клик. Здесь WM_CLOSE
        отправляется напрямую целевому hwnd через Win32-сообщение, клавиатура
        не участвует.

        Если Р7-Офис показывает диалог «Сохранить изменения?», он ищется
        среди top-level окон, принадлежащих тому же процессу (owner PID через
        GetWindowThreadProcessId — не зависит от текста заголовка, который
        отличается между версиями/локалями), и закрывается через
        _click_priority_button — тем же надёжным путём, что и диалог
        обновления, а не вслепую по стрелке и Enter.

        Если окно не исчезло за timeout секунд — например, диалог не
        распознан, — процесс Р7 завершается принудительно через
        _terminate_r7_processes. Раньше в этом случае программа просто
        продолжала бы работу с зависшим Р7 на фоне.

        Args:
            hwnd: Дескриптор закрываемого окна Р7-Офис. None означает, что
                окно не было найдено заранее — сразу переходим к
                принудительному завершению процесса.
            log_cb: Функция логирования; по умолчанию self.add_test_log.
            timeout: Сколько секунд ждать штатного закрытия после WM_CLOSE.

        Returns:
            bool: True — окно закрыто штатно; False — потребовалось
            принудительное завершение процесса (само завершение
            произошло в любом случае).
        """
        if log_cb is None:
            log_cb = self.add_test_log

        if env.WIN32_OK and hwnd and not self._is_r7_window(hwnd):
            # Никогда не шлём WM_CLOSE окну чужого процесса.
            log_cb("⚠️ Переданное окно не принадлежит Р7-Офис — не закрываю его, "
                   "ищу окно Р7 заново")
            hwnd = self._find_r7_window()

        if not (env.WIN32_OK and hwnd):
            log_cb("⚠️ Окно Р7-Офис не найдено — завершаем процесс напрямую")
            self._terminate_r7_processes(log_cb)
            return False

        import win32gui
        import win32con
        import win32process

        # Диалог сохранения — не диалог обновления: узнаём его не по тексту
        # заголовка (тот отличается между версиями и локалями), а по тому,
        # что это НОВОЕ top-level окно того же процесса, появившееся уже
        # после WM_CLOSE.
        SAVE_DIALOG_BUTTONS = ('не сохранять', "don't save", 'нет', 'no')

        try:
            _, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            owner_pid = None

        def _sibling_windows():
            wins = []
            def _enum(h, _):
                if h == hwnd or not win32gui.IsWindowVisible(h):
                    return
                try:
                    _, pid = win32process.GetWindowThreadProcessId(h)
                except Exception:
                    return
                if pid == owner_pid:
                    wins.append(h)
            win32gui.EnumWindows(_enum, None)
            return wins

        # Модальный файловый диалог блокирует закрытие наглухо: пока открыт
        # «Сохранить как», WM_CLOSE главному окну не делает ничего, и весь
        # timeout уходит впустую, а потом kill. Такой диалог остаётся, если
        # тест «Сохранение в PDF» не довёл экспорт до конца (не приняли путь,
        # выскочил вопрос о перезаписи). Снимаем его ДО WM_CLOSE.
        self._cancel_blocking_dialogs(owner_pid, log_cb)

        close_started = time.perf_counter()
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            log_cb("⚠️ Не удалось отправить WM_CLOSE — завершаем процесс напрямую")
            self._terminate_r7_processes(log_cb)
            return False

        # CDP мог ещё ни разу не понадобиться в этом прогоне: коннектор
        # создаётся при запуске Р7, а connect() зовётся лениво из
        # _wait_for_bold_button_cdp, и если триггер готовности ни разу не
        # сработал — соединения нет. connect() идемпотентен, так что для уже
        # подключённого это no-op; таймаут короткий, чтобы не тормозить выход.
        if self._webdriver_connector is not None:
            try:
                self._webdriver_connector.connect(timeout=1.0)
            except Exception as e:
                log_cb(f"   ⚠️ CDP перед закрытием не подключился ({type(e).__name__}: {e}) — "
                       f"закрываю без него")

        deadline = time.perf_counter() + timeout
        dismissed = False
        diag_dumped = False
        cdp_tries = 0
        last_cdp_try = 0.0
        cdp_clicked = False   # только чтобы не повторять строку в логе
        while time.perf_counter() < deadline:
            if not win32gui.IsWindow(hwnd):
                log_cb(f"🔚 Р7-Офис закрыт штатно за {time.perf_counter() - close_started:.1f} сек")
                return True

            if not dismissed:
                # Путь 1 — отдельное окно-диалог того же процесса. Работает,
                # только если сборка Р7 рисует его классическими Win32-виджетами.
                if owner_pid:
                    for w in _sibling_windows():
                        if not diag_dumped:
                            # Сам факт «окно-диалог есть, но кнопку в нём не
                            # нашли» ниже не логируется: _click_priority_button
                            # печатает дамп только когда дочерние окна ЕСТЬ, а у
                            # диалога Qt их нет вовсе (Qt рисует кнопки сам, не
                            # заводя HWND). Поэтому заголовок и класс окна пишем
                            # здесь — именно они отличают Qt-диалог от
                            # HTML-модалки, у которой окна нет совсем.
                            try:
                                log_cb(f"   Окно-кандидат на диалог сохранения: "
                                       f"hwnd={w} class={win32gui.GetClassName(w)!r} "
                                       f"title={win32gui.GetWindowText(w)!r}")
                            # окно закрылось до записи в журнал — это лишь диагностика
                            except Exception:
                                pass
                        clicked, text = self._click_priority_button(
                            w, SAVE_DIALOG_BUTTONS,
                            # Раньше сюда передавался глушитель `lambda _m: None`,
                            # и дамп дочерних окон — единственная диагностика,
                            # объясняющая, почему кнопка не нашлась, — молча
                            # выбрасывался. Пишем его, но один раз за закрытие,
                            # чтобы не залить лог на каждой итерации цикла.
                            log_cb=(log_cb if not diag_dumped else (lambda _m: None)))
                        # Флаг взводим только когда окно реально осмотрели.
                        # Если сейчас siblings пусты, а диалог появится на
                        # следующей итерации — его диагностику терять нельзя.
                        diag_dumped = True
                        if clicked:
                            log_cb(f"   Диалог сохранения закрыт кнопкой «{text}»")
                            dismissed = True
                            break

                # Путь 2 — модалка внутри окна редактора (HTML в CEF). Отдельного
                # окна ОС у неё нет, поэтому путь 1 её не находит вообще: hwnd
                # остаётся жив, siblings пусты, и до этой правки цикл просто
                # крутился весь timeout и уходил в kill — ровно тот симптом,
                # с которого начали («не закрылся за 10 сек»).
                # Опрашиваем не чаще CDP_RETRY_SEC: каждый вызов — round-trip по
                # websocket, а при оборванном соединении ещё и строка в логе;
                # на шаге цикла в 0.2 с это залило бы лог полусотней сообщений.
                if not dismissed and (time.perf_counter() - last_cdp_try) >= self.CLOSE_CDP_RETRY_SEC:
                    last_cdp_try = time.perf_counter()
                    cdp_tries += 1
                    res = self._cdp_dismiss_save_dialog()
                    if res and not cdp_clicked:
                        # Намеренно НЕ ставим dismissed=True: JS сообщает «клик
                        # прошёл», а не «модалка закрылась». Если попали не по той
                        # кнопке (например, по видимому элементу в фоновом
                        # документе), латч навсегда отключил бы и Win32-путь, и
                        # повторные попытки — и закрытие гарантированно свелось бы
                        # к kill. Признак успеха тут ровно один: окно исчезло, его
                        # проверяет IsWindow в начале цикла.
                        cdp_clicked = True
                        log_cb(f"   Нажата кнопка модалки сохранения через CDP: «{res}»")

            time.sleep(0.2)

        # Принудительное завершение — не аварийный путь, а штатный запасной:
        # для бенчмарка терять несохранённые правки тестового файла безопаснее,
        # чем вслепую нажать «Сохранить» и перезаписать эталон.
        log_cb(f"⚠️ Р7-Офис не закрылся за {timeout} сек — завершаем процесс принудительно")
        log_cb(f"   (Win32-кнопка: {'нажата' if dismissed else 'не найдена'}; "
               f"CDP: коннектор {'есть' if self._webdriver_connector else 'нет'}, "
               f"попыток {cdp_tries}, клик {'был' if cdp_clicked else 'не прошёл'})")
        self._terminate_r7_processes(log_cb)
        return False

    def _cancel_blocking_dialogs(self, owner_pid, log_cb=None, max_rounds=3):
        """Отменяет модальные диалоги, из-за которых Р7 не реагирует на WM_CLOSE.

        Речь прежде всего о «Сохранить как»: он остаётся открытым, если тест
        «Сохранение в PDF» не довёл экспорт до конца. Пока он на экране,
        WM_CLOSE главному окну не делает ничего — весь timeout закрытия уходит
        впустую и заканчивается принудительным завершением процесса.

        В отличие от диалога «Сохранить изменения?», здесь безопасно жать
        именно «Отмена»: отмена экспорта в PDF ничего не портит (файл
        временный), тогда как отмена вопроса о сохранении означала бы «не
        закрывать Р7».

        Диалог ищется среди видимых top-level окон процесса Р7 по заголовку —
        в отличие от кнопок, заголовок у такого окна есть и через win32gui
        читается (это настоящее окно ОС, а не HTML-модалка внутри редактора).
        Сначала пробуем кнопку «Отмена», затем WM_CLOSE самому диалогу: для
        файлового диалога это эквивалентно отмене.

        Args:
            owner_pid: PID процесса, чьи окна проверяем. None — проверяем все
                видимые окна, принадлежащие любому процессу Р7.
            log_cb: Функция логирования; по умолчанию self.add_test_log.
            max_rounds: Сколько раз повторить проход — за отменой одного
                диалога может открыться следующий (перезапись → сам «Сохранить
                как»).

        Returns:
            int: сколько диалогов было закрыто.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if not env.WIN32_OK:
            return 0

        import win32gui
        import win32con
        import win32process

        allowed_pids = {owner_pid} if owner_pid else None
        if allowed_pids is None and env.PSUTIL_OK:
            allowed_pids = {p.pid for p in self._get_r7_processes(log_cb=lambda _m: None)}

        closed = 0
        for _ in range(max_rounds):
            targets = []

            def _enum(h, _):
                if not win32gui.IsWindowVisible(h):
                    return
                try:
                    title = win32gui.GetWindowText(h).lower()
                except Exception:
                    return
                if not any(t in title for t in self.BLOCKING_DIALOG_TITLES):
                    return
                if allowed_pids:
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(h)
                    except Exception:
                        return
                    if pid not in allowed_pids:
                        return      # чужой «Сохранить как» — не наш, не трогаем
                targets.append(h)

            try:
                win32gui.EnumWindows(_enum, None)
            except Exception:
                break

            if not targets:
                break

            for h in targets:
                try:
                    title = win32gui.GetWindowText(h)
                except Exception:
                    title = "?"
                clicked, btn = self._click_priority_button(
                    h, self.CANCEL_BUTTONS, log_cb=lambda _m: None)
                if clicked:
                    log_cb(f"   🚪 Блокирующий диалог «{title}» отменён кнопкой «{btn}»")
                else:
                    try:
                        win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
                        log_cb(f"   🚪 Блокирующий диалог «{title}» закрыт через WM_CLOSE")
                    except Exception as e:
                        log_cb(f"   ⚠️ Не удалось закрыть диалог «{title}»: {e}")
                        continue
                closed += 1
            time.sleep(0.3)     # дать диалогу исчезнуть перед следующим проходом

        return closed

    def _capture_cdp_ui_baseline(self, log_cb=None):
        """Снимает базовый DOM-снимок сразу после открытия файла, ДО первой
        операции — чтобы последующие _cdp_dump_ui показывали только элементы,
        появившиеся из-за конкретного действия (right-click и т.п.), а не
        постоянно смонтированные части интерфейса.

        Появилась из-за issue #9: разные вызовы _cdp_dump_ui (в разных
        точках теста — открытие контекстного меню на разных ячейках, разное
        клиповое состояние) возвращали ОДИН И ТОТ ЖЕ список из 12 пунктов на
        неизменных экранных координатах (x=1393 у всех, независимо от того,
        над какой ячейкой был right-click). Это не мог быть настоящий
        всплывающий попап — координаты попапа менялись бы вместе с курсором.
        На деле список совпал с overflow-меню тулбара (кнопка «Условное
        форматирование» в том же дампе — это именно тулбарная кнопка, не
        пункт меню): MENU_SEL цепляет его, потому что он тоже помечен как
        `.dropdown-menu` и остаётся "видимым" по всем текущим критериям
        (offsetParent/rect/computed style), просто свёрнут не через
        display:none. Настоящий контекстный попап, судя по всему, живёт вне
        обходимого DOM (нативный CEF-оверлей) — сравнение с базовым снимком
        не заставит появиться то, чего нет в DOM, зато надёжно уберёт из
        дампа шум вроде этого overflow-меню, если он относится к статичной
        части интерфейса, снятой ДО right-click.

        Безопасно вызывать даже без CDP (коннектор не создан/не подключён —
        self._cdp_ui_baseline останется None, _cdp_dump_ui в этом случае
        просто не диффит и печатает как раньше).

        Сбрасывает self._cdp_dump_seen (дедуп «один дамп на ключ за
        сессию» в _cdp_dump_ui). Без сброса дедуп-множество живёт на весь
        срок жизни self.R7Testovarka, а не на один запуск Р7: при повторном
        прогоне теста в той же сессии GUI (или в Batch по нескольким
        версиям) каждый вызов после первого молча схлопывался бы в
        `if key in seen: return`, а этот метод исправно тратил бы CDP
        round-trip на снимок, который _cdp_dump_ui заведомо не покажет —
        итог: пустая трата времени на каждый запуск, кроме первого в
        сессии. Сброс здесь — на каждый launch Р7 DOM у него всё равно
        свежий, показывать дамп заново для нового запуска корректно.

        Args:
            log_cb: Функция логирования; по умолчанию self.add_test_log.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        self._cdp_ui_baseline = None
        self._cdp_dump_seen = set()
        connector = self._webdriver_connector
        if connector is None or not getattr(connector, "connected", False):
            return
        try:
            self._cdp_ui_baseline = connector.dump_visible_ui()
        except Exception as e:
            self._cdp_ui_baseline = None
            log_cb(f"⚠️ Базовый DOM-снимок не снят ({type(e).__name__}: {e}) — "
                   f"дальнейшие дампы меню покажут все видимые элементы без вычитания")

    @staticmethod
    def _cdp_item_key(item):
        """Ключ сравнения по содержимому одного элемента DOM-дампа
        (без координат — те сравниваются отдельно, см.
        CDP_ITEM_POSITION_TOLERANCE_PX и _cdp_item_in_baseline)."""
        return (item.get("tag"), item.get("id"), item.get("cls"), item.get("text"))

    @classmethod
    def _cdp_item_in_baseline(cls, item, baseline_by_key):
        """True, если item — тот же элемент, что уже был в базовом снимке:
        совпадает и содержимое (_cdp_item_key), и положение на экране (с
        допуском CDP_ITEM_POSITION_TOLERANCE_PX)."""
        candidates = baseline_by_key.get(cls._cdp_item_key(item))
        if not candidates:
            return False
        ix, iy = item.get("x", 0), item.get("y", 0)
        tol = cls.CDP_ITEM_POSITION_TOLERANCE_PX
        for b in candidates:
            if abs(b.get("x", 0) - ix) <= tol and abs(b.get("y", 0) - iy) <= tol:
                return True
        return False

    def _cdp_dump_ui(self, label, log_cb=None, once_key=None, charge_pace=False):
        """Пишет в лог видимые кнопки и пункты меню, как их видит DOM.

        Диагностика для слепых мест автоматизации: контекстное меню и модалки
        обходятся стрелками вслепую (`down` N раз + Enter), и стоит меню
        обзавестись лишним пунктом, как нажимается не то. По win32gui эти
        подписи недоступны в принципе (Qt+CEF не заводит дочерних HWND), а
        через CDP — видны.

        Если ранее вызывался _capture_cdp_ui_baseline (self._cdp_ui_baseline
        не None) — печатает только элементы, которых не было в базовом
        снимке: постоянно смонтированные части интерфейса (тулбар, его
        overflow-меню) иначе перепечатывались бы на каждый вызов и маскируют
        собой то единственное, что реально интересно — что появилось именно
        из-за этого right-click/модалки (см. issue #9). Без базового снимка
        печатает всё видимое, как раньше.

        Печатает не чаще одного раза на ключ за сессию приложения: дамп нужен,
        чтобы один раз увидеть структуру меню, а не чтобы залить лог на каждом
        прогоне теста (тесты гоняются по 3 прогона, а меню между ними не
        меняется).

        Вызывать вне окна замера либо с charge_pace=True — round-trip по
        websocket иначе попадёт в результат.

        Args:
            label: Человекочитаемая пометка, в какой момент снят дамп.
            log_cb: Функция логирования; по умолчанию self.add_test_log.
            once_key: Ключ дедупликации; по умолчанию сам label.
            charge_pace: Отнести собственную длительность в _paced_total, чтобы
                вычесть её из замера. Ставить только там, где Р7 в этот момент
                гарантированно простаивает (раскрытое меню, открытая модалка) —
                иначе вычтем время, которое Р7 работал.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        connector = self._webdriver_connector
        if connector is None or not getattr(connector, "connected", False):
            return
        key = once_key or label
        seen = getattr(self, "_cdp_dump_seen", None)
        if seen is None:
            seen = self._cdp_dump_seen = set()
        if key in seen:
            return
        seen.add(key)
        _t0 = time.perf_counter()
        try:
            items = connector.dump_visible_ui()
        except Exception:
            items = None
        if charge_pace:
            self._paced_total += time.perf_counter() - _t0
        if items is None:
            return
        if not items:
            return

        baseline = getattr(self, "_cdp_ui_baseline", None)
        total_visible = len(items)
        if baseline is not None:
            baseline_by_key = {}
            for it in baseline:
                baseline_by_key.setdefault(self._cdp_item_key(it), []).append(it)
            items = [it for it in items if not self._cdp_item_in_baseline(it, baseline_by_key)]
            if not items:
                log_cb(f"   🔬 DOM-дамп ({label}): новых элементов нет "
                       f"(все {total_visible} видимых уже были в базовом снимке до операций)")
                return
            log_cb(f"   🔬 DOM-дамп ({label}): новых элементов {len(items)} "
                   f"из {total_visible} видимых (базовый снимок вычтен)")
        else:
            log_cb(f"   🔬 DOM-дамп ({label}): видимых элементов {total_visible}")
        for it in items[:25]:
            try:
                # x/y/depth (frame) — координаты элемента на экране и глубина
                # вложенности iframe, где он найден. Нужны, чтобы отличить
                # реальный раскрытый попап (координаты рядом с курсором) от
                # статичного элемента где-то в стороне DOM (см. issue #9:
                # дамп раз за разом находил один и тот же список независимо
                # от контекста — координаты покажут, действительно ли это
                # один и тот же неподвижный узел).
                log_cb(f"      • {it.get('text','')!r} "
                       f"<{it.get('tag','')} id={it.get('id','')!r} class={it.get('cls','')!r}> "
                       f"@({it.get('x','?')},{it.get('y','?')}) frame={it.get('depth','?')}")
            except Exception:  # строка диагностического дампа — без неё дамп лишь короче
                pass

    def _cdp_dismiss_save_dialog(self):
        """Пробует нажать «Не сохранять» в HTML-модалке выхода через CDP.

        Тонкий адаптер над R7WebDriverConnector.dismiss_save_dialog(): гасит
        любые исключения и приводит ответ к тексту нажатой кнопки. Молча
        возвращает None, если CDP в этом запуске недоступен (Р7 стартован без
        debug-флага, нет requests/websocket-client и т.п.) — тогда закрытие
        идёт обычным путём, как и до появления коннектора.

        Returns:
            str | None: текст нажатой кнопки, либо None.
        """
        connector = self._webdriver_connector
        if connector is None:
            return None
        try:
            res = connector.dismiss_save_dialog()
        except Exception:
            return None
        if isinstance(res, dict) and res.get("clicked"):
            return res.get("text") or "не сохранять"
        return None


    def _close_update_dialog_if_exists(self, log_cb=None, search_timeout=5):
        """Looks for the R7-Office update dialog and closes it if found.

        Scans visible top-level windows for update-related title keywords AND
        owned by a Р7-Офис process (GetWindowThreadProcessId against
        _get_r7_processes()) — a title match alone used to be enough, which let
        this close "Центр обновления Windows", browser tabs containing "update"
        in the title, or a foreign installer window. Enumerates child windows to
        find a dismiss button and clicks it via Win32 messages (no pyautogui, no
        focus dependency). Falls back to WM_CLOSE + VK_ESCAPE if no button is
        matched. Logs nothing if no dialog is present.

        Args:
            log_cb: Callable for log output. Defaults to self.add_test_log.
            search_timeout: Seconds to poll for the dialog window (use a small
                value such as 1 when called from a monitor loop).

        Returns:
            bool: True if a dialog was found and dismissed.
        """
        if log_cb is None:
            log_cb = self.add_test_log

        if not env.WIN32_OK:
            return False

        import win32gui
        import win32con
        import win32process

        # Только составные фразы, специфичные для диалога обновления Р7-Офис.
        # Раньше список заканчивался голыми "обновление"/"update"/"доступна" —
        # под них подходило почти любое системное окно с таким словом в заголовке.
        UPDATE_TITLES = (
            'обновление программного обеспечения',
            'доступна новая версия',
            'р7-офис обновление',
            'update available',
            'software update',
            'новая версия доступна',
        )
        # Checked in order — first match wins
        DISMISS_PRIORITY = (
            'напомнить позже',
            'пропустить эту версию',
            'не сейчас',
            'remind me later',
            'skip this version',
            'not now',
            'later',
            'skip',
        )

        # ── Search for the dialog (up to search_timeout seconds) ─────────────
        # Сканируем минимум один раз, даже при search_timeout=0. Прежний цикл
        # с проверкой условия на входе при нулевом таймауте не выполнялся ни
        # разу, а при значении по умолчанию (5) сжигал все 5 секунд каждый раз,
        # когда диалога не было, — и эти секунды попадали в замер открытия файла.
        # Владелец окна обязан быть процессом Р7-Офис (не x2t — конвертер не
        # показывает диалогов). Пусто здесь значит, что Р7 сейчас не запущен —
        # в этом случае заголовок, каким бы он ни был, точно не наш диалог.
        r7_pids = {
            p.pid for p in self._get_r7_processes(log_cb=lambda _m: None)
            if "x2t" not in (p.name() or "").lower()
        } if env.PSUTIL_OK else set()

        def _owned_by_r7(hwnd):
            if not r7_pids:
                return False
            try:
                _, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return False
            return owner_pid in r7_pids

        found = []
        deadline = time.perf_counter() + search_timeout

        def _enum(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd).lower()
                if any(s in t for s in UPDATE_TITLES) and _owned_by_r7(hwnd):
                    found.append(hwnd)

        while True:
            win32gui.EnumWindows(_enum, None)
            if found or time.perf_counter() >= deadline:
                break
            time.sleep(0.5)

        if not found:
            return False

        hwnd = found[0]
        actual_title = win32gui.GetWindowText(hwnd)
        log_cb(f"⚠️ Обнаружено окно обновления: {actual_title}")

        clicked, _ = self._click_priority_button(hwnd, DISMISS_PRIORITY, log_cb=log_cb)

        # ── Fallback when no button matched ──────────────────────────────────
        if not clicked:
            # Try WM_CLOSE first (clean dialog dismissal)
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:  # окно уже закрылось — ниже проверка видимости и Esc
                pass
            time.sleep(0.2)

            # If still visible — send VK_ESCAPE via message (no pyautogui)
            if win32gui.IsWindowVisible(hwnd):
                try:
                    # lParam for key-down: repeat=1, scan=0x01, other bits=0
                    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN,
                                         win32con.VK_ESCAPE, 0x00010001)
                    time.sleep(0.05)
                    win32gui.PostMessage(hwnd, win32con.WM_KEYUP,
                                         win32con.VK_ESCAPE, 0xC0010001)
                except Exception:  # диалог исчез между проверкой и Esc — закрывать нечего
                    pass

        time.sleep(0.3)
        log_cb("✅ Окно обновления закрыто")
        return True
