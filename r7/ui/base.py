"""Оформление и геометрия окон: тема Sun Valley (sv-ttk) в тёмном и светлом
вариантах, шрифты, значки, размер и положение главного окна и диалогов,
колесо мыши.

ttk-виджеты рисует тема sv-ttk (вид Windows 11). Виджеты Tk вне ttk (журнал,
холсты, меню, фон окон) красятся из COLORS — палитры текущей темы; при смене
темы _recolor_tk_widgets перекрашивает уже созданные. Нет пакета sv-ttk —
прежняя тёмная тема на «clam». Toplevel создаются с .transient(root); колесо —
_bind_wheel на виджетах, не bind_all. UiBaseMixin — методы, которые
R7Testovarka получает наследованием.
"""
import ctypes
import json
import tkinter as tk
from tkinter import ttk

from r7 import config
from r7.ui.icons import IconSet

try:
    import sv_ttk
    SV_TTK_OK = True
except ImportError:
    sv_ttk = None
    SV_TTK_OK = False

# Палитры под темы sv-ttk: фон и текст — как у самой темы, статусные цвета —
# из системы Fluent (Windows 11), чтобы журнал и метки читались на обоих фонах.
PALETTES = {
    "dark": {
        "bg":             "#1c1c1c",  # фон окна — как у sv-ttk dark
        "bg_card":        "#272727",  # фон панелей (список тестов)
        "accent":         "#57c8ff",  # акцент: заголовки групп, выделение
        "accent_hover":   "#2f60d8",
        "text":           "#fafafa",
        "text_secondary": "#a0a0a0",
        "border":         "#3a3a3a",
        "border_hover":   "#4a4a4a",
        "log_bg":         "#141414",
        "success":        "#6ccb5f",
        "warn":           "#fce100",
        "error":          "#ff99a4",
        "result":         "#99ebff",  # итоги операций в журнале
        "row_ok":         "#2e4a3a",  # строки таблицы хешей: совпало,
        "row_warn":       "#4a4326",  # нет эталона,
        "row_fail":       "#4a2e2e",  # не совпало
    },
    "light": {
        "bg":             "#fafafa",  # фон окна — как у sv-ttk light
        "bg_card":        "#ffffff",
        "accent":         "#005fb8",
        "accent_hover":   "#2f60d8",
        "text":           "#1c1c1c",
        "text_secondary": "#5c5c5c",
        "border":         "#d6d6d6",
        "border_hover":   "#c4c4c4",
        "log_bg":         "#ffffff",
        "success":        "#0f7b0f",
        "warn":           "#9d5d00",
        "error":          "#c42b1c",
        "result":         "#005fb8",
        "row_ok":         "#dff6dd",
        "row_warn":       "#fff4ce",
        "row_fail":       "#fde7e9",
    },
}
DEFAULT_THEME = "dark"

# Текущая палитра. Модули интерфейса берут цвета отсюда при создании
# виджетов; смена темы обновляет словарь на месте (ссылки на него у
# модулей остаются верными) и перекрашивает уже созданное.
COLORS = dict(PALETTES[DEFAULT_THEME])

FONT_UI = ("Segoe UI", 10)
FONT_LOG = ("Consolas", 10)

UI_SETTINGS_FILE = "ui_settings.json"


def load_ui_settings():
    """Настройки интерфейса (тема); битый или отсутствующий файл — по умолчанию."""
    try:
        with open(config.BASE_DIR / UI_SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # файла нет или он битый — настройки по умолчанию
        return {}


def save_ui_settings(data):
    try:
        with open(config.BASE_DIR / UI_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:  # папка только для чтения — тема просто не запомнится
        pass


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
        except Exception:  # нет API рабочей области — вызывающий берёт размер экрана
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
        """Тема при старте: сохранённая в ui_settings.json, иначе тёмная."""
        theme = load_ui_settings().get("theme", DEFAULT_THEME)
        self._apply_theme(theme if theme in PALETTES else DEFAULT_THEME)

    def _apply_theme(self, theme):
        """Включает тему theme ("dark"/"light"): sv-ttk, палитра, свои стили."""
        COLORS.clear()
        COLORS.update(PALETTES[theme])
        self._theme = theme
        if getattr(self, "icons", None) is None:
            self.icons = IconSet(self.root)
        self.root.configure(bg=COLORS["bg"])
        style = ttk.Style(self.root)
        if SV_TTK_OK:
            if not getattr(self, "_theme_event_bound", False):
                self.root.bind("<<ThemeChanged>>", self._on_theme_changed, add="+")
                self._theme_event_bound = True
            sv_ttk.set_theme(theme, self.root)
        else:
            style.theme_use("clam")
            self._configure_clam(style)
        # sv-ttk на <<ThemeChanged>> зовёт tk_setPalette: тот пишет цвет текста
        # в базу опций Tk, и ttk-надписи брали его вместо цвета своего стиля
        # (заголовки групп и вторичный текст становились белыми). Более точный
        # шаблон с приоритетом выше отдаёт цвет обратно стилям.
        for cls in ("TLabel", "TButton", "TCheckbutton", "TRadiobutton"):
            self.root.option_add(f"*{cls}.foreground", "")
        self._configure_custom_styles(style)

    def _on_theme_changed(self, _event=None):
        """После tk_setPalette (sv-ttk, <<ThemeChanged>>): вернуть свои цвета
        виджетам Tk и снять цвет текста, который он вписал ttk-надписям."""
        def _fix():
            self._recolor_tk_widgets(self.root)
            self._clear_ttk_foreground(self.root)
        try:
            self.root.after_idle(_fix)
        except tk.TclError:  # окно закрывается — перекрашивать нечего
            pass

    def _clear_ttk_foreground(self, widget):
        """Цвет текста ttk-надписей — из стиля. Виджеты с намеренно своим
        цветом (статус генератора) помечены _r7_fg и не трогаются."""
        try:
            if (widget.winfo_class() in ("TLabel", "TButton", "TCheckbutton", "TRadiobutton")
                    and not getattr(widget, "_r7_fg", False)):
                widget.configure(foreground="")
            children = widget.winfo_children()
        except tk.TclError:  # виджет уже уничтожен
            return
        for child in children:
            self._clear_ttk_foreground(child)

    def _toggle_theme(self):
        """Кнопка в шапке: тёмная ⇄ светлая, выбор запоминается."""
        theme = "light" if getattr(self, "_theme", DEFAULT_THEME) == "dark" else "dark"
        self._apply_theme(theme)
        self._recolor_tk_widgets(self.root)
        self._refresh_icons()
        save_ui_settings({**load_ui_settings(), "theme": theme})

    def _configure_custom_styles(self, style):
        """Стили поверх темы: заголовки, вторичный текст, панели, статусы.
        Фоны кнопок, флажков и полей не трогаем — их рисует тема картинками."""
        c = COLORS
        style.configure(".", font=FONT_UI)
        style.configure("Secondary.TLabel", foreground=c["text_secondary"])
        style.configure("Header.TLabel", foreground=c["text"], font=("Segoe UI Semibold", 15))
        style.configure("Version.TLabel", foreground=c["text"], font=("Segoe UI Semibold", 11))
        style.configure("VersionOk.TLabel", foreground=c["success"], font=("Segoe UI Semibold", 11))
        style.configure("VersionWarn.TLabel", foreground=c["warn"], font=("Segoe UI Semibold", 11))
        style.configure("Title.TLabel", foreground=c["text"], font=("Segoe UI Semibold", 12))
        style.configure("StatusOk.TLabel", foreground=c["success"])
        style.configure("StatusErr.TLabel", foreground=c["warn"])
        # Панель списка тестов — чуть светлее фона окна.
        style.configure("Panel.TFrame", background=c["bg_card"])
        style.configure("Panel.TLabel", background=c["bg_card"], foreground=c["text"])
        style.configure("PanelDim.TLabel", background=c["bg_card"], foreground=c["text_secondary"])
        style.configure("Group.TLabel", background=c["bg_card"], foreground=c["accent"],
                        font=("Segoe UI Semibold", 9))
        style.configure("Panel.TCheckbutton", background=c["bg_card"])
        style.map("Panel.TCheckbutton", background=[("active", c["bg_card"])])
        # Прежние имена стилей — ими пользуются окна Batch, сравнения, отчёта.
        style.configure("Card.TFrame", background=c["bg_card"])
        style.configure("Card.TLabel", background=c["bg_card"], foreground=c["text"])
        style.configure("Card.TCheckbutton", background=c["bg_card"])
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 11), padding=(16, 7))
        style.configure("Small.TButton", padding=(8, 2), font=("Segoe UI", 9))
        style.configure("Step.TButton", padding=(2, 0), width=2, font=("Segoe UI", 10))
        style.configure("Runs.TEntry", padding=(2, 1))
        style.configure("Treeview", rowheight=28)
        self.root.option_add("*TCombobox*Listbox.background", c["bg_card"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent_hover"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    def _recolor_tk_widgets(self, widget):
        """Перекрашивает виджеты Tk вне ttk (их не трогает тема) в COLORS."""
        c = COLORS
        try:
            if isinstance(widget, (tk.Tk, tk.Toplevel)):
                widget.configure(bg=c["bg"])
            elif isinstance(widget, tk.Text):
                widget.configure(bg=c["log_bg"], fg=c["text"], insertbackground=c["text"])
                self._configure_log_tags(widget)
            elif isinstance(widget, tk.Canvas):
                panel = getattr(widget, "_r7_panel", False)
                widget.configure(bg=c["bg_card"] if panel else c["bg"])
            elif isinstance(widget, tk.Menu):
                widget.configure(bg=c["bg_card"], fg=c["text"],
                                 activebackground=c["accent_hover"], activeforeground="#ffffff")
            elif isinstance(widget, tk.Frame):
                widget.configure(bg=c["border"])          # линии-разделители
        except tk.TclError:  # виджет уже уничтожен — перекрашивать нечего
            return
        for child in widget.winfo_children():
            self._recolor_tk_widgets(child)

    def _configure_log_tags(self, text):
        """Цвета журнала: время приглушено, ошибки, предупреждения, успех и
        итоги операций — своими цветами, остальное — обычным текстом."""
        c = COLORS
        text.tag_configure("TIME", foreground=c["text_secondary"])
        text.tag_configure("INFO", foreground=c["text"])
        text.tag_configure("OK", foreground=c["success"])
        text.tag_configure("RESULT", foreground=c["result"])
        text.tag_configure("WARN", foreground=c["warn"])
        text.tag_configure("ERROR", foreground=c["error"])
        text.tag_configure("HINT", foreground=c["text_secondary"], font=FONT_UI, spacing1=2)

    def _icon_button(self, parent, text, icon, command=None, style=None, **kw):
        """ttk.Button со значком слева; значок перекрашивается при смене темы."""
        btn = ttk.Button(parent, text=text, command=command, style=style or "TButton",
                         compound=tk.LEFT, **kw)
        btn._r7_icon = icon
        self._set_button_icon(btn)
        self.__dict__.setdefault("_icon_widgets", []).append(btn)
        return btn

    def _set_button_icon(self, btn):
        # Акцентная кнопка sv-ttk: в тёмной теме светло-голубая с тёмным
        # текстом, в светлой — синяя с белым. Значок — в цвет её текста.
        accent = str(btn.cget("style")) == "Accent.TButton"
        if accent:
            color = "#1c1c1c" if getattr(self, "_theme", DEFAULT_THEME) == "dark" else "#ffffff"
        else:
            color = COLORS["text"]
        icons = getattr(self, "icons", None)
        img = icons.get(btn._r7_icon, color) if icons else None
        dim = icons.get(btn._r7_icon, COLORS["text_secondary"]) if icons else None
        try:
            # Отдельная картинка для недоступной кнопки: ttk сам подменит её
            # при смене состояния, перерисовывать вручную не нужно.
            btn.configure(image=(img, "disabled", dim) if img and dim else "")
        except tk.TclError:  # кнопка уже уничтожена
            pass

    def _refresh_icons(self):
        for btn in list(self.__dict__.get("_icon_widgets", [])):
            self._set_button_icon(btn)
        refresh_tabs = getattr(self, "_refresh_tab_icons", None)
        if refresh_tabs:
            refresh_tabs()

    def _configure_clam(self, style):
        """Запасная тема без sv-ttk: прежняя тёмная на «clam».

        Нативные темы Windows ('vista'/'winnative') рисуют кнопки средствами
        ОС и игнорируют цвета ttk.Style; 'clam' — свой рендерер Tk.
        """
        c = COLORS
        style.configure(".", background=c["bg"], foreground=c["text"], font=FONT_UI)
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("TLabelframe", background=c["bg"], foreground=c["text"],
                        bordercolor=c["border"])
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["text_secondary"])
        style.configure("TButton", background=c["border"], foreground=c["text"],
                        bordercolor=c["border"], focusthickness=0, padding=6)
        style.map("TButton", background=[("active", c["border_hover"]),
                                         ("pressed", c["border_hover"])])
        style.configure("Accent.TButton", background=c["accent_hover"], foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", c["accent"]),
                                                ("pressed", c["accent"])])
        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"])
        style.map("TCheckbutton", background=[("active", c["bg"])])
        style.configure("TNotebook", background=c["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=c["bg"], foreground=c["text_secondary"],
                        padding=(14, 8), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", c["bg"])],
                  foreground=[("selected", c["accent"])])
        style.configure("TScrollbar", background=c["border"], troughcolor=c["bg"],
                        bordercolor=c["bg"], arrowcolor=c["text_secondary"])
        style.configure("Treeview", background=c["bg_card"], fieldbackground=c["bg_card"],
                        foreground=c["text"], bordercolor=c["border"])
        style.configure("Treeview.Heading", background=c["border"], foreground=c["text"],
                        relief="flat")
        style.map("Treeview", background=[("selected", c["accent_hover"])],
                  foreground=[("selected", "#ffffff")])
        style.configure("Horizontal.TProgressbar", background=c["accent_hover"],
                        troughcolor=c["bg_card"], bordercolor=c["bg"])
        for name in ("TEntry", "TCombobox"):
            style.configure(name, fieldbackground=c["bg_card"], foreground=c["text"],
                            insertcolor=c["text"], bordercolor=c["border"],
                            arrowcolor=c["text"], background=c["border"])
        style.configure("TPanedwindow", background=c["bg"])

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
        except tk.TclError:  # диалог закрыт до показа — центрировать нечего
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
