"""Ожидание штатного закрытия Р7 после WM_CLOSE — по шагам (plan-to-10, шаг 2).

Прежде это был цикл внутри _close_r7_gracefully на 185 строк. Здесь он —
класс CloseWait: состояние (нажата ли кнопка, сколько было попыток CDP) —
атрибуты, оба пути к диалогу «Сохранить изменения?» — методы. Порядок,
интервалы и тексты журнала не менялись (docs/closing-and-dialogs.md).

Правило 8 CLAUDE.md: в диалоге жать только «Не сохранять» по тексту —
кнопка по умолчанию «Сохранить» перезапишет эталонный файл.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from r7 import windows

LogCb = Callable[[str], object]

# Диалог сохранения — не диалог обновления: узнаём его не по тексту
# заголовка (тот отличается между версиями и локалями), а по тому,
# что это НОВОЕ top-level окно того же процесса, появившееся уже
# после WM_CLOSE.
SAVE_DIALOG_BUTTONS = ('не сохранять', "don't save", 'нет', 'no')

CLOSE_POLL_SEC = 0.2    # шаг цикла ожидания закрытия
# Тот же диалог не жмём чаще: окно после «Нет» живёт ещё доли секунды. Но Qt
# может показать вопрос следующего документа в том же hwnd — тогда жмём снова.
CLOSE_RECLICK_SEC = 1.0


class CloseWaitHost(Protocol):
    """Что CloseWait берёт у приложения (R7Testovarka: DialogsMixin, WindowsMixin)
    — интервал опроса CDP, коннектор и два метода. Нужен только mypy: так
    проверяется сам CloseWait, не весь класс приложения."""

    CLOSE_CDP_RETRY_SEC: float
    _webdriver_connector: Any

    def _click_priority_button(self, hwnd: int, keyword_priority: tuple[str, ...],
                               log_cb: LogCb | None = None) -> tuple[bool, str | None]: ...

    def _cdp_dismiss_save_dialog(self) -> str | None: ...


def owner_pid_of(hwnd: int) -> int | None:
    """PID процесса окна или None (окно уже закрылось)."""
    pid: int
    try:
        _, pid = windows.window_thread_process_id(hwnd)
        return pid
    except Exception:
        return None


def sibling_windows(hwnd: int, owner_pid: int) -> list[int]:
    """Видимые top-level окна того же процесса, кроме самого hwnd."""
    wins: list[int] = []

    def _enum(h: int, _: object) -> None:
        if h == hwnd or not windows.is_window_visible(h):
            return
        try:
            _, pid = windows.window_thread_process_id(h)
        except Exception:
            return
        if pid == owner_pid:
            wins.append(h)
    windows.enum_windows(_enum, None)
    return wins


class CloseWait:
    """Ждёт, пока окно hwnd исчезнет, по пути закрывая «Сохранить изменения?».
    run() → True, если окно закрылось за timeout; иначе False (завершать
    процессы — дело вызывающего)."""

    def __init__(self, app: CloseWaitHost, hwnd: int, owner_pid: int | None,
                 log_cb: LogCb, timeout: float, close_started: float) -> None:
        self.app, self.hwnd, self.owner_pid, self.log_cb = app, hwnd, owner_pid, log_cb
        self.timeout, self.close_started = timeout, close_started
        self.dismissed = False
        # hwnd диалога → когда нажали. Документов в окне может быть несколько,
        # и на каждый Р7 спрашивает «Сохранить изменения?» отдельно.
        self.clicked_at: dict[int, float] = {}
        self.diag_dumped = False
        self.cdp_tries = 0
        self.last_cdp_try = 0.0
        self.cdp_clicked = False   # только чтобы не повторять строку в логе

    def _log_candidate(self, w: int) -> None:
        """Сам факт «окно-диалог есть, но кнопку в нём не нашли» ниже не
        логируется: _click_priority_button печатает дамп только когда дочерние
        окна ЕСТЬ, а у диалога Qt их нет вовсе (Qt рисует кнопки сам, не
        заводя HWND). Поэтому заголовок и класс окна пишем здесь — именно они
        отличают Qt-диалог от HTML-модалки, у которой окна нет совсем."""
        try:
            self.log_cb(f"   Окно-кандидат на диалог сохранения: "
                        f"hwnd={w} class={windows.window_class(w)!r} "
                        f"title={windows.window_text(w)!r}")
        # окно закрылось до записи в журнал — это лишь диагностика
        except Exception:
            pass

    def _win32_dialog(self) -> None:
        """Путь 1 — отдельное окно-диалог того же процесса. Работает, только
        если сборка Р7 рисует его классическими Win32-виджетами."""
        if not self.owner_pid:
            return
        now = time.perf_counter()
        for w in sibling_windows(self.hwnd, self.owner_pid):
            if now - self.clicked_at.get(w, -CLOSE_RECLICK_SEC) < CLOSE_RECLICK_SEC:
                continue
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
                self.clicked_at[w] = now
                break

    def _cdp_dialog(self) -> None:
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

    def summary(self) -> str:
        """Строка журнала: что пробовали перед принудительным завершением."""
        return (f"   (Win32-кнопка: {'нажата' if self.dismissed else 'не найдена'}; "
                f"CDP: коннектор {'есть' if self.app._webdriver_connector else 'нет'}, "
                f"попыток {self.cdp_tries}, клик {'был' if self.cdp_clicked else 'не прошёл'})")

    def run(self) -> bool:
        deadline = time.perf_counter() + self.timeout
        while time.perf_counter() < deadline:
            if not windows.is_window(self.hwnd):
                self.log_cb(f"🔚 Р7-Офис закрыт штатно за "
                            f"{time.perf_counter() - self.close_started:.1f} сек")
                return True
            # Win32-путь — на каждом шаге: за первым диалогом может прийти
            # вопрос о следующем документе. CDP — пока Win32 не сработал ни разу.
            self._win32_dialog()
            self._cdp_dialog()
            time.sleep(CLOSE_POLL_SEC)
        return False
