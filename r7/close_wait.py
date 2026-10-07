"""Ожидание штатного закрытия Р7 после WM_CLOSE — по шагам (plan-to-10, шаг 2).

Прежде это был цикл внутри _close_r7_gracefully на 185 строк. Здесь он —
класс CloseWait: состояние (нажата ли кнопка, сколько было попыток CDP) —
атрибуты, оба пути к диалогу «Сохранить изменения?» — методы. Порядок,
интервалы и тексты журнала не менялись (docs/closing-and-dialogs.md).

Правило 8 CLAUDE.md: в диалоге жать только «Не сохранять» по тексту —
кнопка по умолчанию «Сохранить» перезапишет эталонный файл.
"""
import time

# Диалог сохранения — не диалог обновления: узнаём его не по тексту
# заголовка (тот отличается между версиями и локалями), а по тому,
# что это НОВОЕ top-level окно того же процесса, появившееся уже
# после WM_CLOSE.
SAVE_DIALOG_BUTTONS = ('не сохранять', "don't save", 'нет', 'no')

CLOSE_POLL_SEC = 0.2    # шаг цикла ожидания закрытия


def _win32():
    """win32gui/win32process — в момент вызова, как и прежде внутри
    _close_r7_gracefully: тесты подменяют их через sys.modules."""
    import win32gui
    import win32process
    return win32gui, win32process


def owner_pid_of(hwnd):
    """PID процесса окна или None (окно уже закрылось)."""
    _gui, win32process = _win32()
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None


def sibling_windows(hwnd, owner_pid):
    """Видимые top-level окна того же процесса, кроме самого hwnd."""
    win32gui, win32process = _win32()
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


class CloseWait:
    """Ждёт, пока окно hwnd исчезнет, по пути закрывая «Сохранить изменения?».
    run() → True, если окно закрылось за timeout; иначе False (завершать
    процессы — дело вызывающего)."""

    def __init__(self, app, hwnd, owner_pid, log_cb, timeout, close_started):
        self.app, self.hwnd, self.owner_pid, self.log_cb = app, hwnd, owner_pid, log_cb
        self.timeout, self.close_started = timeout, close_started
        self.dismissed = False
        self.diag_dumped = False
        self.cdp_tries = 0
        self.last_cdp_try = 0.0
        self.cdp_clicked = False   # только чтобы не повторять строку в логе

    def _log_candidate(self, w):
        """Сам факт «окно-диалог есть, но кнопку в нём не нашли» ниже не
        логируется: _click_priority_button печатает дамп только когда дочерние
        окна ЕСТЬ, а у диалога Qt их нет вовсе (Qt рисует кнопки сам, не
        заводя HWND). Поэтому заголовок и класс окна пишем здесь — именно они
        отличают Qt-диалог от HTML-модалки, у которой окна нет совсем."""
        win32gui, _proc = _win32()
        try:
            self.log_cb(f"   Окно-кандидат на диалог сохранения: "
                        f"hwnd={w} class={win32gui.GetClassName(w)!r} "
                        f"title={win32gui.GetWindowText(w)!r}")
        # окно закрылось до записи в журнал — это лишь диагностика
        except Exception:
            pass

    def _win32_dialog(self):
        """Путь 1 — отдельное окно-диалог того же процесса. Работает, только
        если сборка Р7 рисует его классическими Win32-виджетами."""
        if not self.owner_pid:
            return
        for w in sibling_windows(self.hwnd, self.owner_pid):
            if not self.diag_dumped:
                self._log_candidate(w)
            clicked, text = self.app._click_priority_button(
                w, SAVE_DIALOG_BUTTONS,
                # Раньше сюда передавался глушитель `lambda _m: None`,
                # и дамп дочерних окон — единственная диагностика,
                # объясняющая, почему кнопка не нашлась, — молча
                # выбрасывался. Пишем его, но один раз за закрытие,
                # чтобы не залить лог на каждой итерации цикла.
                log_cb=(self.log_cb if not self.diag_dumped else (lambda _m: None)))
            # Флаг взводим только когда окно реально осмотрели.
            # Если сейчас siblings пусты, а диалог появится на
            # следующей итерации — его диагностику терять нельзя.
            self.diag_dumped = True
            if clicked:
                self.log_cb(f"   Диалог сохранения закрыт кнопкой «{text}»")
                self.dismissed = True
                break

    def _cdp_dialog(self):
        """Путь 2 — модалка внутри окна редактора (HTML в CEF). Отдельного
        окна ОС у неё нет, поэтому путь 1 её не находит вообще: hwnd остаётся
        жив, siblings пусты, и до этой правки цикл просто крутился весь
        timeout и уходил в kill — ровно тот симптом, с которого начали
        («не закрылся за 10 сек»). Опрашиваем не чаще CLOSE_CDP_RETRY_SEC:
        каждый вызов — round-trip по websocket, а при оборванном соединении
        ещё и строка в логе; на шаге цикла в 0.2 с это залило бы лог
        полусотней сообщений."""
        if self.dismissed or (time.perf_counter() - self.last_cdp_try) < self.app.CLOSE_CDP_RETRY_SEC:
            return
        self.last_cdp_try = time.perf_counter()
        self.cdp_tries += 1
        res = self.app._cdp_dismiss_save_dialog()
        if res and not self.cdp_clicked:
            # Намеренно НЕ ставим dismissed=True: JS сообщает «клик
            # прошёл», а не «модалка закрылась». Если попали не по той
            # кнопке (например, по видимому элементу в фоновом
            # документе), латч навсегда отключил бы и Win32-путь, и
            # повторные попытки — и закрытие гарантированно свелось бы
            # к kill. Признак успеха тут ровно один: окно исчезло, его
            # проверяет IsWindow в начале цикла.
            self.cdp_clicked = True
            self.log_cb(f"   Нажата кнопка модалки сохранения через CDP: «{res}»")

    def summary(self):
        """Строка журнала: что пробовали перед принудительным завершением."""
        return (f"   (Win32-кнопка: {'нажата' if self.dismissed else 'не найдена'}; "
                f"CDP: коннектор {'есть' if self.app._webdriver_connector else 'нет'}, "
                f"попыток {self.cdp_tries}, клик {'был' if self.cdp_clicked else 'не прошёл'})")

    def run(self):
        win32gui, _proc = _win32()
        deadline = time.perf_counter() + self.timeout
        while time.perf_counter() < deadline:
            if not win32gui.IsWindow(self.hwnd):
                self.log_cb(f"🔚 Р7-Офис закрыт штатно за "
                            f"{time.perf_counter() - self.close_started:.1f} сек")
                return True
            if not self.dismissed:
                self._win32_dialog()
                self._cdp_dialog()
            time.sleep(CLOSE_POLL_SEC)
        return False
