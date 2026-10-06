"""Оформление и геометрия окон: тёмная тема, шрифты, размер и положение
главного окна и диалогов, колесо мыши.

Toplevel создаются с .transient(root); колесо — _bind_wheel на виджетах,
не bind_all. UiBaseMixin — методы, которые R7Testovarka получает
наследованием.
"""
import ctypes
import tkinter as tk
from tkinter import ttk


COLORS = {
    "bg":            "#1E1E2E",  # основной фон
    "bg_card":       "#2A2A3E",  # фон карточек/фреймов
    "accent":        "#6C63FF",  # акцент: кнопки, активные элементы, заголовки
    "accent_hover":  "#5750D9",  # затемнение акцента при наведении
    "text":          "#E0E0E0",  # основной текст
    "text_secondary":"#A0A0B0",  # вторичный текст
    "border":        "#3A3A5A",  # границы/разделители, фон обычных кнопок
    "border_hover":  "#4A4A6A",  # фон кнопок при наведении
    "log_bg":        "#1A1A2E",  # фон лога
    "success":       "#4CAF50",  # INFO / ✅
    "warn":          "#FF9800",  # WARN / ⚠️
    "error":         "#F44336",  # ERROR / ❌
}


FONT_UI  = ("Segoe UI", 10)


FONT_LOG = ("Consolas", 9)


class UiBaseMixin:
    """Тема, шрифты и геометрия окон — часть R7Testovarka (через наследование)."""

    # ---------------------- UI ----------------------
    # Желаемый размер окна при старте. Числа не на глаз: собранному UI нужно
    # winfo_reqwidth x winfo_reqheight = 1149x669 (вкладка «Производительность»
    # одна требует 1125 по ширине — там сетка карточек Canvas 380px и лог
    # стоят рядом). Прежние 800x600 обрезали её на 325px по ширине — отсюда и
    # «интерфейс обрезан». Ниже — требуемое плюс запас на будущие виджеты.
    DEFAULT_WIN_W = 1220

    DEFAULT_WIN_H = 780

    # Минимальный размер, при котором вся раскладка ещё работает: список
    # тестов и лог прокручиваются, а шапка и панель кнопок видны всегда.
    MIN_WIN_W = 820

    MIN_WIN_H = 560

    @staticmethod
    def _work_area():
        """Рабочая область основного монитора без панели задач: (x, y, w, h).

        winfo_screenheight() отдаёт весь экран, включая панель задач, — окно
        по его высоте уходило низом под панель. SPI_GETWORKAREA возвращает
        именно видимую область. None, если API недоступен.
        """
        try:
            from ctypes import wintypes
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return (rect.left, rect.top,
                        rect.right - rect.left, rect.bottom - rect.top)
        except Exception:
            pass
        return None

    @classmethod
    def _fit_window(cls, need_w, need_h, area):
        """Считает геометрию окна по нужному размеру и рабочей области.

        Чистая функция — проверяется тестами без Tk.

        Args:
            need_w, need_h: сколько просит собранный интерфейс (winfo_req*).
            area: (x, y, w, h) рабочей области экрана.

        Returns:
            tuple: (w, h, x, y, zoomed). zoomed=True — интерфейс не
            помещается даже в рабочую область, окно надо развернуть.
        """
        ax, ay, aw, ah = area
        # Поля под рамку окна и заголовок, их нет в winfo_req*.
        frame_w, frame_h = 16, 40
        want_w = max(cls.DEFAULT_WIN_W, need_w)
        want_h = max(cls.DEFAULT_WIN_H, need_h)
        if need_w + frame_w > aw or need_h + frame_h > ah:
            return aw - frame_w, ah - frame_h, ax, ay, True
        w = min(want_w, aw - frame_w)
        h = min(want_h, ah - frame_h)
        x = ax + max(0, (aw - w - frame_w) // 2)
        y = ay + max(0, (ah - h - frame_h) // 3)  # чуть выше центра — визуально ровнее
        return w, h, x, y, False

    def _apply_default_geometry(self):
        """Ставит стартовый размер окна так, чтобы весь интерфейс был виден.

        Вызывается ПОСЛЕ setup_ui: размер берётся из того, что интерфейсу
        реально нужно (winfo_reqwidth/height), а не из констант. На экране с
        масштабом 125–150% шрифты крупнее, интерфейс просит больше места, и
        прежние фиксированные 1220x780 обрезали низ вкладки вместе с кнопкой
        «Запустить». Окно вписывается в рабочую область (без панели задач);
        если интерфейс не помещается и в неё — окно разворачивается.
        """
        try:
            self.root.update_idletasks()
            area = self._work_area() or (0, 0, self.root.winfo_screenwidth(),
                                         self.root.winfo_screenheight() - 48)
            w, h, x, y, zoomed = self._fit_window(
                self.root.winfo_reqwidth(), self.root.winfo_reqheight(), area)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            if zoomed:
                self.root.state("zoomed")
        except Exception:
            # winfo_* теоретически может отказать до полной инициализации Tk —
            # окно без явной геометрии всё равно откроется, просто по умолчанию.
            self.root.geometry(f"{self.DEFAULT_WIN_W}x{self.DEFAULT_WIN_H}")

    def _apply_dark_theme(self):
        """Настраивает тёмную тему через ttk.Style.

        Тема 'clam' выбрана намеренно: нативные темы Windows ('vista'/
        'winnative') рисуют кнопки/вкладки/скроллбары средствами ОС и
        игнорируют цветовые переопределения ttk.Style для многих опций —
        подтверждено документацией Tk. 'clam' — собственный рендерер Tk,
        поддерживающий полную кастомизацию цвета для всех использованных
        здесь виджетов.
        """
        self.root.configure(bg=COLORS["bg"])
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"],
                         font=FONT_UI)
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["bg_card"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Card.TLabel", background=COLORS["bg_card"], foreground=COLORS["text"])
        style.configure("Secondary.TLabel", background=COLORS["bg"],
                         foreground=COLORS["text_secondary"])
        style.configure("Header.TLabel", background=COLORS["bg"],
                         foreground=COLORS["accent"], font=("Segoe UI", 16, "bold"))
        style.configure("StatusOk.TLabel", background=COLORS["bg"], foreground=COLORS["success"])
        style.configure("StatusErr.TLabel", background=COLORS["bg"], foreground=COLORS["error"])

        style.configure("TLabelframe", background=COLORS["bg"], foreground=COLORS["text"],
                         bordercolor=COLORS["border"])
        style.configure("TLabelframe.Label", background=COLORS["bg"], foreground=COLORS["text_secondary"])

        style.configure("TButton", background=COLORS["border"], foreground=COLORS["text"],
                         bordercolor=COLORS["border"], focusthickness=0, padding=6)
        style.map("TButton",
                  background=[("active", COLORS["border_hover"]), ("pressed", COLORS["border_hover"])])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="#FFFFFF",
                         font=("Segoe UI", 12, "bold"), padding=10)
        style.map("Accent.TButton",
                  background=[("active", COLORS["accent_hover"]), ("pressed", COLORS["accent_hover"])])

        style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["text"])
        style.map("TCheckbutton", background=[("active", COLORS["bg"])])
        style.configure("Card.TCheckbutton", background=COLORS["bg_card"], foreground=COLORS["text"])
        style.map("Card.TCheckbutton", background=[("active", COLORS["bg_card"])])

        style.configure("TSpinbox", fieldbackground=COLORS["bg_card"], background=COLORS["bg_card"],
                         foreground=COLORS["text"], arrowcolor=COLORS["text"])

        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=COLORS["bg"], foreground=COLORS["text_secondary"],
                         padding=(14, 8), borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", COLORS["bg"])],
                  foreground=[("selected", COLORS["accent"])])

        style.configure("TScrollbar", background=COLORS["border"], troughcolor=COLORS["bg"],
                         bordercolor=COLORS["bg"], arrowcolor=COLORS["text_secondary"])
        style.map("TScrollbar", background=[("active", COLORS["border_hover"])])

        style.configure("Treeview", background=COLORS["bg_card"], fieldbackground=COLORS["bg_card"],
                         foreground=COLORS["text"], bordercolor=COLORS["border"], rowheight=26)
        style.configure("Treeview.Heading", background=COLORS["border"], foreground=COLORS["text"],
                         relief="flat")
        style.map("Treeview",
                  background=[("selected", COLORS["accent"])],
                  foreground=[("selected", "#FFFFFF")])

        style.configure("Horizontal.TProgressbar", background=COLORS["accent"],
                         troughcolor=COLORS["bg_card"], bordercolor=COLORS["bg"])

        # Флажки: в clam отмеченный флажок — крестик на белом, на тёмном фоне
        # его почти не отличить от пустого. Отмеченный — заливка акцентом.
        for name, bg in (("TCheckbutton", COLORS["bg"]), ("Card.TCheckbutton", COLORS["bg_card"])):
            style.configure(name, indicatorbackground=COLORS["bg"],
                            indicatorforeground="#FFFFFF", indicatormargin=(2, 2, 6, 2),
                            upperbordercolor=COLORS["text_secondary"],
                            lowerbordercolor=COLORS["text_secondary"])
            style.map(name, background=[("active", bg)],
                      indicatorbackground=[("selected", COLORS["accent"]),
                                           ("active", COLORS["border"])])

        # Маленькие кнопки «−»/«+» у числа повторов и кнопки в заголовках панелей.
        style.configure("Small.TButton", padding=(6, 1), font=("Segoe UI", 9))
        style.configure("Step.TButton", padding=(0, 0), width=2,
                         font=("Segoe UI", 10, "bold"))
        style.map("Step.TButton", background=[("active", COLORS["accent"]),
                                              ("pressed", COLORS["accent_hover"])])
        style.configure("Runs.TEntry", fieldbackground=COLORS["bg"], foreground=COLORS["text"],
                         insertcolor=COLORS["text"], bordercolor=COLORS["border"],
                         padding=(2, 1))
        style.configure("Group.TLabel", background=COLORS["bg"], foreground=COLORS["accent"],
                         font=("Segoe UI", 9, "bold"))
        style.configure("Version.TLabel", background=COLORS["bg"], foreground=COLORS["text"],
                         font=("Segoe UI", 11, "bold"))
        # Поля ввода и выпадающие списки. Без этого clam рисовал их светлым
        # полем, а текст брал светлый из общего стиля «.» — число строк в
        # диалоге тестовых файлов и путь в Batch-режиме было почти не прочесть.
        for name in ("TEntry", "TCombobox"):
            style.configure(name, fieldbackground=COLORS["bg_card"], foreground=COLORS["text"],
                            insertcolor=COLORS["text"], bordercolor=COLORS["border"],
                            lightcolor=COLORS["bg_card"], darkcolor=COLORS["bg_card"],
                            selectbackground=COLORS["accent"], selectforeground="#FFFFFF",
                            arrowcolor=COLORS["text"], background=COLORS["border"])
            style.map(name,
                      fieldbackground=[("readonly", COLORS["bg_card"]),
                                       ("disabled", COLORS["bg"])],
                      foreground=[("disabled", COLORS["text_secondary"]),
                                  ("readonly", COLORS["text"])],
                      bordercolor=[("focus", COLORS["accent"])])
        self.root.option_add("*TCombobox*Listbox.background", COLORS["bg_card"])
        self.root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        style.configure("Runs.TEntry", fieldbackground=COLORS["bg"])
        style.configure("TPanedwindow", background=COLORS["bg"])
        style.configure("Sash", sashthickness=6, gripcount=0, background=COLORS["border"])

    def _center_dialog(self, dlg, w=None, h=None):
        """Ставит диалог по центру главного окна, не выходя за рабочую область.

        Без w/h меняется только положение: размер остаётся за диалогом, иначе
        окно, которое потом добавляет себе виджеты, обрезало бы их.
        """
        dlg.update_idletasks()
        size_given = bool(w and h)
        w = w or max(dlg.winfo_width(), dlg.winfo_reqwidth())
        h = h or max(dlg.winfo_height(), dlg.winfo_reqheight())
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3
        area = self._work_area()
        if area:
            ax, ay, aw, ah = area
            x = max(ax, min(x, ax + aw - w - 16))
            y = max(ay, min(y, ay + ah - h - 40))
        pos = f"+{max(0, x)}+{max(0, y)}"
        dlg.geometry(f"{w}x{h}{pos}" if size_given else pos)

    def _on_toplevel_map(self, event):
        """Первое появление диалога: если он открылся не над главным окном
        (Windows по умолчанию кладёт новые окна в левый верхний угол экрана),
        переносит его в центр главного окна. Диалоги, которые уже поставили
        себя сами (сравнение версий, simpledialog), не трогаются."""
        dlg = event.widget
        if not isinstance(dlg, tk.Toplevel) or getattr(dlg, "_placed_once", False):
            return
        dlg._placed_once = True
        try:
            cx = dlg.winfo_rootx() + dlg.winfo_width() // 2
            cy = dlg.winfo_rooty() + dlg.winfo_height() // 2
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            inside = (rx <= cx <= rx + self.root.winfo_width()
                      and ry <= cy <= ry + self.root.winfo_height())
            if not inside:
                self._center_dialog(dlg)
        except tk.TclError:
            pass

    def _bind_wheel(self, widget, canvas, content):
        """Прокрутка колёсиком над списком тестов — на каждом его виджете.

        Не bind_all: диалоги сравнения версий и Batch-режима при закрытии
        зовут unbind_all("<MouseWheel>") и отключили бы прокрутку и здесь.
        """
        def _on_wheel(event):
            if content.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        def _walk(w):
            w.bind("<MouseWheel>", _on_wheel, add="+")
            for child in w.winfo_children():
                _walk(child)
        _walk(widget)
