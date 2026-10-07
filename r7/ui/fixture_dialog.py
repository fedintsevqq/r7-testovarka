"""Окно «Генерация тестового файла»: размеры, имя, создание, выбор и тест.

Прежде это был метод compare_file_sizes на 251 строку. Здесь — класс
FixtureDialog: поля окна — атрибуты, форма, блокировка кнопок и три
действия — методы. Проверки ввода — r7.batch_config, сам тест своего
файла — R7Testovarka._worker_run_test (через _start_run, CUSTOM).
"""
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from r7.batch_config import auto_fixture_name, fixture_file_name, validate_fixture_dims
from r7.run_state import CUSTOM
from r7.ui.base import COLORS

PAD = {"padx": 16, "pady": 5}


class FixtureDialog:
    """Модальное окно генератора; show() ждёт его закрытия."""

    def __init__(self, app):
        self.app = app
        last = app._load_last_params()
        # Авто-имя при смене размеров; сбрасывается при ручном редактировании
        self.auto_name = True
        self.ext_path = None       # полный путь из filedialog

        dlg = self.dlg = tk.Toplevel(app.root)
        dlg.transient(app.root)
        dlg.configure(bg=COLORS["bg"])
        dlg.title("Генерация тестового файла")
        dlg.resizable(False, False)
        dlg.grab_set()

        self._build_form(last)
        self._build_buttons()
        dlg.columnconfigure(1, weight=1)
        self.rows_entry.focus_set()

    def show(self):
        self.dlg.wait_window()

    # ── форма ─────────────────────────────────────────────────────────────
    def _entry_row(self, row, label, value, hint):
        ttk.Label(self.dlg, text=label).grid(row=row, column=0, sticky=tk.W, **PAD)
        var = tk.StringVar(value=value)
        entry = ttk.Entry(self.dlg, textvariable=var, width=14)
        entry.grid(row=row, column=1, sticky=tk.W, **PAD)
        ttk.Label(self.dlg, text=hint, foreground=COLORS["text_secondary"]).grid(
            row=row, column=2, sticky=tk.W, padx=(0, 16))
        return var, entry

    def _build_form(self, last):
        dlg = self.dlg
        self.rows_var, self.rows_entry = self._entry_row(
            0, "Количество строк:", str(last.get("rows", 50000)), "(1 000 – 1 000 000)")
        self.cols_var, self.cols_entry = self._entry_row(
            1, "Количество столбцов:", str(last.get("cols", 50)), "(1 – 100)")
        ttk.Separator(dlg, orient=tk.HORIZONTAL).grid(
            row=2, column=0, columnspan=3, sticky=tk.EW, padx=16, pady=8)

        self.overwrite_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(dlg, text="Перезаписать если существует",
                        variable=self.overwrite_var).grid(
            row=3, column=0, columnspan=3, sticky=tk.W, padx=16, pady=2)

        ttk.Label(dlg, text="Имя файла:").grid(row=4, column=0, sticky=tk.W, **PAD)
        self.filename_var = tk.StringVar(value=last.get("filename", "test_data_50000x50.xlsx"))
        self.filename_entry = ttk.Entry(dlg, textvariable=self.filename_var, width=36)
        self.filename_entry.grid(row=4, column=1, columnspan=2, sticky=tk.EW,
                                 padx=(0, 16), pady=5)

        self.rows_var.trace_add("write", self._on_dim_change)
        self.cols_var.trace_add("write", self._on_dim_change)
        self.filename_var.trace_add("write", self._on_filename_edit)
        ttk.Separator(dlg, orient=tk.HORIZONTAL).grid(
            row=5, column=0, columnspan=3, sticky=tk.EW, padx=16, pady=8)

    def _on_dim_change(self, *_):
        if self.auto_name:
            try:
                self.filename_var.set(auto_fixture_name(self.rows_var.get(), self.cols_var.get()))
                self.ext_path = None
            except ValueError:  # в поле не число (ещё вводят) — имя не трогаем
                pass

    def _on_filename_edit(self, *_):
        try:
            expected = auto_fixture_name(self.rows_var.get(), self.cols_var.get())
        except ValueError:
            expected = ""
        self.auto_name = (self.filename_var.get() == expected)
        self.ext_path = None

    def _build_buttons(self):
        dlg = self.dlg
        bf = ttk.Frame(dlg)
        bf.grid(row=6, column=0, columnspan=3, sticky=tk.EW, padx=16)
        bf.columnconfigure(0, weight=1)
        bf.columnconfigure(1, weight=1)

        btn_create = ttk.Button(bf, text="1. Создать файл", command=self.on_create)
        btn_choose = ttk.Button(bf, text="2. Выбрать файл", command=self.on_choose)
        btn_test   = ttk.Button(bf, text="3. Протестировать", command=self.on_test)
        btn_cancel = ttk.Button(bf, text="4. Отмена", command=dlg.destroy)
        btn_create.grid(row=0, column=0, sticky=tk.EW, padx=(0, 3), pady=(0, 5))
        btn_choose.grid(row=0, column=1, sticky=tk.EW, padx=(3, 0), pady=(0, 5))
        btn_test  .grid(row=1, column=0, sticky=tk.EW, padx=(0, 3))
        btn_cancel.grid(row=1, column=1, sticky=tk.EW, padx=(3, 0))
        self.action_btns = [btn_create, btn_test]

        self.status_var = tk.StringVar(value="Статус: Готов")
        self.status_lbl = ttk.Label(dlg, textvariable=self.status_var, anchor=tk.W,
                                    foreground=COLORS["text_secondary"])
        self.status_lbl.grid(row=7, column=0, columnspan=3, sticky=tk.EW,
                             padx=16, pady=(10, 14))

    # ── статус и блокировка (зовутся и из фоновых потоков) ────────────────
    def _later(self, fn):
        """fn — в главном потоке; окно уже закрыто — ничего не делать."""
        def _do():
            try:
                fn()
            except tk.TclError:  # окно закрыто — показывать негде
                pass
        try:
            self.dlg.after(0, _do)
        except tk.TclError:  # окно закрыто — показывать негде
            pass

    def set_status(self, text, color=COLORS["text_secondary"]):
        def _do():
            self.status_var.set(f"Статус: {text}")
            self.status_lbl.config(foreground=color)
        self._later(_do)

    def _set_buttons(self, state):
        def _do():
            for b in self.action_btns:
                b.config(state=state)
        self._later(_do)

    def lock(self):
        self._set_buttons("disabled")

    def unlock(self):
        self._set_buttons("normal")

    # ── проверки ──────────────────────────────────────────────────────────
    def _validate_dims(self):
        r, c, err = validate_fixture_dims(self.rows_var.get(), self.cols_var.get())
        if err:
            field, text = err
            messagebox.showwarning("Ошибка", text, parent=self.dlg)
            (self.rows_entry if field == "rows" else self.cols_entry).focus_set()
            return None, None
        return r, c

    def _resolve_path(self):
        if self.ext_path:
            return Path(self.ext_path)
        fname = self.filename_var.get().strip()
        if not fname:
            return None
        if not fname.endswith(".xlsx"):
            fname += ".xlsx"
        return self.app.test_files_folder / fname

    # ── кнопка 1: только создать файл ─────────────────────────────────────
    def on_create(self):
        app = self.app
        r, c = self._validate_dims()
        if r is None:
            return
        if self.ext_path:
            messagebox.showwarning(
                "Внимание",
                "Файл выбран через диалог — кнопка «Создать файл» работает\n"
                "только с именем в поле «Имя файла».\n"
                "Введите имя файла вручную или очистите поле.",
                parent=self.dlg)
            return
        fname, err = fixture_file_name(self.filename_var.get())
        if err:
            messagebox.showwarning("Ошибка", err, parent=self.dlg)
            self.filename_entry.focus_set()
            return
        if fname != self.filename_var.get().strip():
            self.filename_var.set(fname)
        file_path = app.test_files_folder / fname
        if file_path.exists() and not self.overwrite_var.get():
            self.set_status(f"⚠️ Файл уже существует: {fname}", "#e67e22")
            app.add_test_log(f"⚠️ Файл уже существует: {file_path}")
            return
        app._save_last_params(r, c, fname)
        self.lock()
        self.set_status("⏳ Создание файла...", "#2980b9")

        def _worker():
            try:
                app._generate_custom_test_file(r, c, file_path)
                app.add_test_log(f"📊 Создан тестовый файл: {fname} ({r} строк, {c} столбцов)")
                self.set_status(f"✅ Файл создан: {fname}", "#27ae60")
            except Exception as e:
                app.add_test_log(f"❌ Ошибка создания файла: {e}")
                self.set_status(f"❌ Ошибка: {e}", "#e74c3c")
            finally:
                self.unlock()

        threading.Thread(target=_worker, daemon=True).start()

    # ── кнопка 2: выбрать любой xlsx ──────────────────────────────────────
    def on_choose(self):
        path = filedialog.askopenfilename(
            parent=self.dlg,
            title="Выбрать xlsx-файл для тестирования",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")])
        if path:
            # set() зовёт _on_filename_edit, а тот сбрасывает ext_path, —
            # поэтому путь запоминаем после. Прежде порядок был обратный, путь
            # терялся сразу, и «Создать файл» не предупреждал, что файл выбран
            # через диалог (тест шёл верно: полный путь брался из поля).
            self.filename_var.set(path)
            self.ext_path = path
            self.auto_name = False
            self.app.add_test_log(f"📁 Выбран файл: {path}")
            self.set_status(f"📁 Выбран файл: {Path(path).name}", "#2980b9")

    # ── кнопка 3: только тестирование ─────────────────────────────────────
    def on_test(self):
        app = self.app
        file_path = self._resolve_path()
        if not file_path:
            self.set_status("❌ Укажите имя или путь к файлу", "#e74c3c")
            return
        if not file_path.exists():
            msg = f"❌ Файл не найден: {file_path.name}"
            self.set_status(msg, "#e74c3c")
            app.add_test_log(msg)
            return
        try:
            r, c = int(self.rows_var.get()), int(self.cols_var.get())
        except ValueError:
            r, c = 0, 0
        app._save_last_params(r, c, file_path.name)

        def _prepare_ui():
            self.lock()
            self.set_status("⏳ Тестирование...", "#2980b9")

        def _done(success):
            self.set_status("✅ Тест завершён" if success else "❌ Тест завершён с ошибкой",
                            "#27ae60" if success else "#e74c3c")
            self.unlock()

        # Отказ, если идёт прогон вкладки или Batch: клавиши заняты.
        app._start_run(CUSTOM, lambda: app._worker_run_test(file_path, r, c, _done),
                       before=_prepare_ui, parent=self.dlg)
