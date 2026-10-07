"""Окно мастера первого запуска: список проверок стенда (r7/firstrun.py) со
значками ✓ / ! / ✗, «Повторить проверку», «Продолжить» и флажок «Больше не
показывать» (first_run_done в r7_settings.json).

Показывается из r7_Testovarka.__main__ после сборки R7Testovarka, пока
главное окно ещё скрыто. Главное окно открывается в любом случае: провал
проверки только показан пользователю, «Продолжить» при нём выключена, но
окно закрывается крестиком.
"""
import tkinter as tk
from tkinter import ttk

from r7 import firstrun, settings
from r7.ui.base import COLORS

TITLE = "Первый запуск: проверка стенда"
STATUS_MARK = {firstrun.OK: "✓", firstrun.WARN: "!", firstrun.FAIL: "✗"}
STATUS_STYLE = {firstrun.OK: "StatusOk.TLabel", firstrun.WARN: "StatusErr.TLabel",
                firstrun.FAIL: "StatusFail.TLabel"}
WRAP_PX = 560


class FirstRunDialog:
    """Окно со списком проверок. checks — готовый список (тесты), иначе
    run_checks() выполняется при открытии и по кнопке «Повторить проверку»."""

    def __init__(self, app, checks=None, run_checks=None):
        self.app = app
        self.root = app.root
        self._run_checks = run_checks or (lambda: firstrun.run_checks(app))
        dlg = self.dlg = tk.Toplevel(self.root)
        # transient к скрытому (withdrawn) корню прячет и сам диалог — Tk
        # повторяет за хозяином его состояние. Главное окно на этот момент
        # ещё не показано, поэтому transient — только если корень виден.
        if self.root.state() != "withdrawn":
            dlg.transient(self.root)
        dlg.title(TITLE)
        dlg.configure(bg=COLORS["bg"])
        dlg.resizable(False, False)
        dlg.protocol("WM_DELETE_WINDOW", self.close)

        # Нижняя панель упаковывается первой (side=BOTTOM): при нехватке
        # высоты сжимается список, а кнопки видны всегда.
        bottom = ttk.Frame(dlg, padding=(16, 8, 16, 14))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.dont_show = tk.BooleanVar(value=False)
        self.chk_dont_show = ttk.Checkbutton(bottom, text="Больше не показывать",
                                             variable=self.dont_show)
        self.chk_dont_show.pack(side=tk.LEFT)
        self.btn_continue = ttk.Button(bottom, text="Продолжить", style="Accent.TButton",
                                       command=self.close)
        self.btn_continue.pack(side=tk.RIGHT)
        self.btn_retry = ttk.Button(bottom, text="Повторить проверку", command=self.refresh)
        self.btn_retry.pack(side=tk.RIGHT, padx=(0, 8))

        head = ttk.Frame(dlg, padding=(16, 14, 16, 4))
        head.pack(fill=tk.X)
        ttk.Label(head, text="Проверка стенда перед первым прогоном",
                  style="Title.TLabel").pack(anchor=tk.W)
        self.lbl_summary = ttk.Label(head, text="", style="Secondary.TLabel",
                                     wraplength=WRAP_PX)
        self.lbl_summary.pack(anchor=tk.W, pady=(2, 0))

        self.rows = ttk.Frame(dlg, padding=(16, 6, 16, 6))
        self.rows.pack(fill=tk.BOTH, expand=True)

        self.checks = list(checks) if checks is not None else self._run_checks()
        self._render()

    def refresh(self):
        """«Повторить проверку»: заново выполнить проверки и перерисовать."""
        self.checks = list(self._run_checks())
        self._render()

    def _render(self):
        for child in self.rows.winfo_children():
            child.destroy()
        self.row_widgets = []
        for i, check in enumerate(self.checks):
            mark = ttk.Label(self.rows, text=STATUS_MARK.get(check.status, "?"),
                             style=STATUS_STYLE.get(check.status, "TLabel"),
                             font=("Segoe UI Semibold", 12), width=2)
            mark.grid(row=i, column=0, sticky=tk.N, pady=(4, 0))
            cell = ttk.Frame(self.rows)
            cell.grid(row=i, column=1, sticky=tk.W, pady=(4, 0))
            ttk.Label(cell, text=check.name, font=("Segoe UI Semibold", 10)).pack(anchor=tk.W)
            ttk.Label(cell, text=check.detail, style="Secondary.TLabel",
                      wraplength=WRAP_PX).pack(anchor=tk.W)
            if check.fix and check.status != firstrun.OK:
                ttk.Label(cell, text=f"Что сделать: {check.fix}", wraplength=WRAP_PX).pack(
                    anchor=tk.W)
            self.row_widgets.append((mark, cell))

        failed = firstrun.has_failures(self.checks)
        n_warn = sum(1 for c in self.checks if c.status == firstrun.WARN)
        if failed:
            text = ("Есть проблемы, без которых прогон не пойдёт (✗). Исправьте их и "
                    "нажмите «Повторить проверку». Окно можно закрыть крестиком.")
        elif n_warn:
            text = (f"Прогон пойдёт, но с оговорками ({n_warn}): они попадут в "
                    f"«Условия прогона» отчёта.")
        else:
            text = "Всё на месте: закройте Р7-Офис и запускайте тесты."
        self.lbl_summary.config(text=text)
        self.btn_continue.config(state=tk.DISABLED if failed else tk.NORMAL)

    def close(self):
        """«Продолжить» или крестик: при отмеченном флажке мастер больше не
        показывается (first_run_done в r7_settings.json)."""
        if self.dont_show.get():
            settings.save_settings({**settings.load_settings(), "first_run_done": True})
        try:
            self.dlg.destroy()
        except tk.TclError:  # окно уже закрыто
            pass

    def _place(self):
        """По центру рабочей области экрана: главное окно ещё скрыто, и
        центрировать относительно него не по чему."""
        self.dlg.update_idletasks()
        w, h = self.dlg.winfo_reqwidth(), self.dlg.winfo_reqheight()
        area = self.app._work_area() or (0, 0, self.dlg.winfo_screenwidth(),
                                         self.dlg.winfo_screenheight())
        ax, ay, aw, ah = area
        x = ax + max(0, (aw - w) // 2)
        y = ay + max(0, (ah - h) // 3)
        self.dlg.geometry(f"+{x}+{y}")


def show_first_run_dialog(app, checks=None, run_checks=None, wait=True):
    """Показывает мастер и (wait=True) ждёт его закрытия — модально.
    Возвращает окно: тесты открывают его с wait=False и нажимают кнопки."""
    dialog = FirstRunDialog(app, checks=checks, run_checks=run_checks)
    dialog._place()
    dialog.dlg.lift()
    try:
        dialog.dlg.focus_force()
    except tk.TclError:  # окно без фокуса — не страшно
        pass
    if wait:
        dialog.dlg.grab_set()
        app.root.wait_window(dialog.dlg)
    return dialog
