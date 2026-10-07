"""Всплывающая подсказка у виджета: появляется через полсекунды после
наведения мыши или сразу при фокусе с клавиатуры, исчезает при уходе.

Нужна кнопкам, у которых вместо подписи только значок (переключатель темы,
«изменить» и «удалить» в окне сравнения): без неё назначение кнопки не
понять, а с клавиатуры — не узнать вовсе.
"""
import tkinter as tk

DELAY_MS = 500


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
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            tip = self.tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(tip, text=self.text, bg=self.colors["bg_card"], fg=self.colors["text"],
                     bd=1, relief="solid", padx=8, pady=4,
                     font=("Segoe UI", 9)).pack()
            tip.update_idletasks()
            tip.geometry(f"+{max(0, x - tip.winfo_width() // 2)}+{y}")
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
