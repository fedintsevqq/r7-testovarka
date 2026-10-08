"""Всплывающая подсказка у виджета: появляется через полсекунды после
наведения мыши или сразу при фокусе с клавиатуры, исчезает при уходе.

Нужна кнопкам, у которых вместо подписи только значок (переключатель темы,
«изменить» и «удалить» в окне сравнения): без неё назначение кнопки не
понять, а с клавиатуры — не узнать вовсе.
"""
import tkinter as tk

DELAY_MS = 500
GAP_PX = 6


def place_tip(widget_x, widget_y, widget_w, widget_h, tip_w, tip_h,
              screen_x, screen_y, screen_w, screen_h):
    """Где поставить подсказку: под виджетом по центру, а если снизу не
    помещается (кнопки в нижнем ряду окна) — над ним; по горизонтали — в пределах
    экрана. Раньше подсказка всегда шла вниз и у нижнего ряда уезжала в угол."""
    x = widget_x + widget_w // 2 - tip_w // 2
    x = max(screen_x, min(x, screen_x + screen_w - tip_w))
    below = widget_y + widget_h + GAP_PX
    if below + tip_h <= screen_y + screen_h:
        return x, below
    above = widget_y - tip_h - GAP_PX
    return x, max(screen_y, above)


class Tooltip:
    def __init__(self, widget, text, colors):
        self.widget, self.text, self.colors = widget, text, colors
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<FocusIn>", self.show, add="+")
        widget.bind("<FocusOut>", self.hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        try:
            self._job = self.widget.after(DELAY_MS, self.show)
        except tk.TclError:  # виджет уже уничтожен — подсказывать некому
            self._job = None

    def _cancel(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:  # отложенный показ уже выполнился
                pass
            self._job = None

    def show(self, _event=None):
        self._cancel()
        if self.tip is not None:
            return
        try:
            tip = self.tip = tk.Toplevel(self.widget)
            # Скрытым до конца: иначе окно сначала возникало в позиции по умолчанию,
            # а geometry() потом применялась со сдвигом — подсказка висела «где-то
            # вверху» вместо места у кнопки (09.10.2026). Размер и позиция — одним
            # вызовом, показ — после них.
            tip.withdraw()
            tip.wm_overrideredirect(True)
            tk.Label(tip, text=self.text, bg=self.colors["bg_card"], fg=self.colors["text"],
                     bd=1, relief="solid", padx=8, pady=4,
                     font=("Segoe UI", 9)).pack()
            tip.update_idletasks()
            w = self.widget
            tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
            x, y = place_tip(w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(),
                             w.winfo_height(), tw, th,
                             w.winfo_vrootx(), w.winfo_vrooty(),
                             w.winfo_vrootwidth() or w.winfo_screenwidth(),
                             w.winfo_vrootheight() or w.winfo_screenheight())
            tip.geometry(f"{tw}x{th}+{x}+{y}")
            tip.attributes("-topmost", True)
            tip.deiconify()
        except tk.TclError:  # окно закрыли в момент показа
            self.tip = None

    def hide(self, _event=None):
        self._cancel()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:  # подсказка уже закрыта вместе с окном
                pass
            self.tip = None
