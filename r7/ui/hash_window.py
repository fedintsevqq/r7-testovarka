"""Окно «Хеш-суммы дистрибутивов»: таблица, эталоны, копирование, CSV.

Прежде это был метод _show_hash_results на 298 строк. Здесь — класс
HashResultsWindow: таблица, диалог эталона, удаление эталона, контекстное
меню и CSV — методы. Чтение и запись эталонов, статус — r7/hashes.py.
"""
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from r7 import hashes
from r7.ui.base import COLORS

COLUMNS = (("name", "Имя файла", 260, tk.W, True),
           ("size", "Размер (МБ)", 90, tk.CENTER, False),
           ("md5", "MD5", 245, tk.W, False),
           ("sha256", "SHA256", 370, tk.W, False),
           ("status", "Статус", 130, tk.CENTER, False))
# Двойной клик копирует MD5 / SHA256: столбец Treeview → (подпись, индекс значения)
HASH_COLS = {"#3": ("MD5", 2), "#4": ("SHA256", 3)}
NO_VALUE = ("ОШИБКА", "—", "")     # копировать нечего


def row_values(r):
    return (r["name"], r["size"], r["md5"], r["sha256"], r["status"])


class HashResultsWindow:
    """results — список словарей name, size, md5, sha256, status, tag;
    при сохранении и удалении эталона словари меняются на месте."""

    def __init__(self, app, results):
        self.app, self.results = app, results
        self.hashes_path = app.distributives_folder / "hashes.json"
        win = self.win = tk.Toplevel(app.root)
        win.transient(app.root)
        win.configure(bg=COLORS["bg"])
        win.title("Хеш-суммы дистрибутивов")
        win.geometry("1120x480")
        win.resizable(True, True)
        self._build_tree()
        self._build_context_menu()
        self.tree.bind("<Double-1>", self.on_double_click)
        self._build_bottom_bar()

    # ── таблица ───────────────────────────────────────────────────────────
    def _build_tree(self):
        win = self.win
        tree = self.tree = ttk.Treeview(win, columns=[c[0] for c in COLUMNS],
                                        show="headings", selectmode="browse")
        for key, title, width, anchor, stretch in COLUMNS:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor=anchor, stretch=stretch)
        tree.tag_configure("ok",     background="#2E4A3A", foreground=COLORS["text"])
        tree.tag_configure("no_ref", background="#4A4326", foreground=COLORS["text"])
        tree.tag_configure("fail",   background="#4A2E2E", foreground=COLORS["text"])

        sb_y = ttk.Scrollbar(win, orient=tk.VERTICAL,   command=tree.yview)
        sb_x = ttk.Scrollbar(win, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")
        win.rowconfigure(0, weight=1)
        win.columnconfigure(0, weight=1)

        self.id_to_result = {}     # строка таблицы → словарь результата
        for r in self.results:
            iid = tree.insert("", tk.END, values=row_values(r), tags=(r["tag"],))
            self.id_to_result[iid] = r

    def _refresh_row(self, iid, row_data, ref):
        """Статус строки — заново по эталонам ref, без пересчёта хешей."""
        row_data["status"], row_data["tag"] = hashes.status_against(row_data, ref)
        self.tree.item(iid, values=row_values(row_data), tags=(row_data["tag"],))

    def selected(self):
        """(iid, row_data) выбранной строки, или предупреждение и (None, None)."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Нет выбора", "Выберите файл в таблице.", parent=self.win)
            return None, None
        return sel[0], self.id_to_result[sel[0]]

    def _copy(self, value, label):
        if value in NO_VALUE:
            return
        self.win.clipboard_clear()
        self.win.clipboard_append(value)
        messagebox.showinfo("Скопировано", f"{label} скопирован в буфер обмена:\n{value}",
                            parent=self.win)

    # ── эталон: правка ────────────────────────────────────────────────────
    def edit_selected(self):
        iid, row_data = self.selected()
        if iid:
            self.open_edit_dialog(iid, row_data)

    def open_edit_dialog(self, iid, row_data):
        """Диалог эталонных хешей одного файла."""
        win = self.win
        if row_data["md5"] in ("ОШИБКА", "—"):
            messagebox.showwarning("Недоступно",
                                   "Нельзя добавить эталон для файла с ошибкой чтения.",
                                   parent=win)
            return
        ref, err = hashes.load_reference(self.hashes_path)
        if err:
            # Битый hashes.json: сохранение затёрло бы все прочие эталоны.
            messagebox.showerror("Эталоны недоступны", err, parent=win)
            return
        current = ref.get(row_data["name"], {})

        dlg = tk.Toplevel(win)
        dlg.transient(win)
        dlg.title(f"Редактирование эталона: {row_data['name']}")
        dlg.geometry("520x185")
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(dlg, text="MD5 (32 hex-символа):").grid(
            row=0, column=0, sticky=tk.W, padx=12, pady=(16, 5))
        md5_var = tk.StringVar(value=current.get("md5", row_data["md5"]))
        md5_entry = ttk.Entry(dlg, textvariable=md5_var, width=46, font=("Consolas", 10))
        md5_entry.grid(row=0, column=1, padx=(0, 12), pady=(16, 5), sticky=tk.EW)

        ttk.Label(dlg, text="SHA256 (64 hex-символа):").grid(
            row=1, column=0, sticky=tk.W, padx=12, pady=5)
        sha256_var = tk.StringVar(value=current.get("sha256", row_data["sha256"]))
        ttk.Entry(dlg, textvariable=sha256_var, width=46, font=("Consolas", 10)).grid(
            row=1, column=1, padx=(0, 12), pady=5, sticky=tk.EW)
        dlg.columnconfigure(1, weight=1)

        def on_save():
            self._save_reference(dlg, iid, row_data, md5_var.get(), sha256_var.get())

        btn_row = ttk.Frame(dlg)
        btn_row.grid(row=2, column=0, columnspan=2, pady=14)
        ttk.Button(btn_row, text="Сохранить", command=on_save).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_row, text="Отмена",    command=dlg.destroy).pack(side=tk.LEFT)
        md5_entry.focus_set()
        dlg.bind("<Return>", lambda _: on_save())
        dlg.bind("<Escape>", lambda _: dlg.destroy())

    def _save_reference(self, dlg, iid, row_data, md5_text, sha256_text):
        md5_clean, err = hashes.validate_hex(md5_text, hashes.MD5_LEN, "MD5")
        if err:
            messagebox.showerror("Ошибка ввода", err, parent=dlg)
            return
        sha256_clean, err = hashes.validate_hex(sha256_text, hashes.SHA256_LEN, "SHA256")
        if err:
            messagebox.showerror("Ошибка ввода", err, parent=dlg)
            return
        try:
            err = hashes.set_reference(self.hashes_path, row_data["name"], md5_clean, sha256_clean)
        except Exception as e:
            err = f"Не удалось сохранить hashes.json:\n{e}"
        if err:
            messagebox.showerror("Ошибка записи", err, parent=dlg)
            return
        ref, _ = hashes.load_reference(self.hashes_path)
        self._refresh_row(iid, row_data, ref)
        self.app.add_test_log(f"✏️ Добавлен эталон для {row_data['name']}")
        messagebox.showinfo("Готово", "Эталон сохранён", parent=dlg)
        dlg.destroy()

    # ── эталон: удаление ──────────────────────────────────────────────────
    def delete_selected(self):
        iid, row_data = self.selected()
        if iid:
            self.delete_reference(iid, row_data)

    def delete_reference(self, iid, row_data):
        """Убирает эталон файла из hashes.json (после подтверждения)."""
        win = self.win
        ref, err = hashes.load_reference(self.hashes_path)
        if err:
            messagebox.showerror("Эталоны недоступны", err, parent=win)
            return
        if row_data["name"] not in ref:
            messagebox.showinfo("Нет эталона", f"Для файла «{row_data['name']}» эталон не задан.",
                                parent=win)
            return
        if not messagebox.askyesno("Подтверждение", f"Удалить эталон для:\n{row_data['name']}?",
                                   parent=win):
            return
        try:
            _deleted, err = hashes.delete_reference(self.hashes_path, row_data["name"])
        except Exception as e:
            err = f"Не удалось сохранить hashes.json:\n{e}"
        if err:
            messagebox.showerror("Ошибка записи", err, parent=win)
            return
        ref, _ = hashes.load_reference(self.hashes_path)
        self._refresh_row(iid, row_data, ref)
        self.app.add_test_log(f"🗑️ Удалён эталон для {row_data['name']}")
        messagebox.showinfo("Готово", "Эталон удалён", parent=win)

    # ── контекстное меню и двойной клик ───────────────────────────────────
    def _build_context_menu(self):
        menu = self.ctx_menu = tk.Menu(self.win, tearoff=0)
        menu.add_command(label="✏️ Добавить/редактировать эталон", command=self.edit_selected)
        menu.add_command(label="🗑️ Удалить эталон",                command=self.delete_selected)
        menu.add_separator()
        menu.add_command(label="Скопировать MD5",    command=lambda: self.copy_selected(2, "MD5"))
        menu.add_command(label="Скопировать SHA256", command=lambda: self.copy_selected(3, "SHA256"))
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.win.bind("<Button-1>", lambda e: menu.unpost())

    def show_context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.ctx_menu.post(event.x_root, event.y_root)

    def copy_selected(self, col_idx, label):
        iid, _row = self.selected()
        if iid:
            self._copy(self.tree.item(iid, "values")[col_idx], label)

    def on_double_click(self, event):
        col = self.tree.identify_column(event.x)
        iid = self.tree.identify_row(event.y)
        if not iid or col not in HASH_COLS:
            return
        label, idx = HASH_COLS[col]
        self._copy(self.tree.item(iid, "values")[idx], label)

    # ── нижняя панель и CSV ───────────────────────────────────────────────
    def _build_bottom_bar(self):
        bottom = ttk.Frame(self.win)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=6, padx=8)
        ttk.Label(bottom, text="ПКМ или двойной клик по MD5/SHA256 — дополнительные действия",
                  foreground=COLORS["text_secondary"]).pack(side=tk.LEFT)
        ttk.Button(bottom, text="💾 Сохранить отчёт (CSV)", command=self.save_csv).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="🗑️ Удалить эталон",        command=self.delete_selected).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="✏️ Добавить/редактировать эталон", command=self.edit_selected).pack(side=tk.RIGHT, padx=4)

    def save_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self.win,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"hash_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not path:
            return
        try:
            hashes.write_csv(path, self.results)
            messagebox.showinfo("Сохранено", f"Отчёт сохранён:\n{path}", parent=self.win)
            self.app.add_test_log(f"💾 Отчёт хешей сохранён: {path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}", parent=self.win)
