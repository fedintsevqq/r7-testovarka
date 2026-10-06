"""Batch-режим: диалог выбора дистрибутивов и тестов, запуск фонового
прогона по версиям (установка → прогон → удаление).

Прогон одной версии — r7/runs.py (_batch_run_single_version).
BatchUiMixin — методы, которые R7Testovarka получает наследованием.
"""
import ctypes
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from r7 import config, env, readiness
from r7.run_state import BATCH, missing_packages
from r7.env import pyperclip
from r7.ui.base import COLORS


class BatchUiMixin:
    """Диалог и запуск Batch — часть R7Testovarka (через наследование)."""

    def run_batch_mode(self):
        """Entry point for Batch mode — validates prerequisites then shows config dialog."""
        refusal = self.run_state.refusal(BATCH)
        if refusal:
            messagebox.showwarning(*refusal)
            return
        if not ctypes.windll.shell32.IsUserAnAdmin():
            messagebox.showerror(
                "Ошибка прав",
                "Batch-режим требует прав администратора.\n"
                "Перезапустите программу от имени администратора."
            )
            return
        _warn = readiness._missing_cdp_warning()
        if _warn and not messagebox.askyesno("Нет доступа к интерфейсу Р7", _warn):
            return
        missing = missing_packages(env.PYAUTOGUI_OK, bool(pyperclip), env.EXCEL_OK, env.WIN32_OK)
        if missing:
            messagebox.showerror("Ошибка",
                                 f"Отсутствуют библиотеки: {', '.join(missing)}\n"
                                 "Установите: pip install " + " ".join(missing))
            return
        files = (list(self.distributives_folder.glob("*.msi")) +
                 list(self.distributives_folder.glob("*.exe")))
        files.sort(key=lambda f: self._extract_version(f.stem) or f.name)
        if not files:
            messagebox.showwarning("Нет дистрибутивов",
                                   "В папке Distributives не найдено .msi/.exe файлов.")
            return
        self._show_batch_config_dialog(files)

    def _show_batch_config_dialog(self, files):
        """Shows batch configuration dialog: version checkboxes, test file, options."""
        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.configure(bg=COLORS["bg"])
        dlg.title("Batch-режим")
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(dlg, text=f"Найдено дистрибутивов: {len(files)}",
                  font=("Arial", 10, "bold")).pack(pady=(14, 4), padx=16, anchor=tk.W)

        # ── Список версий ─────────────────────────────────────────────────────
        # Прокручиваемый список вместо обычного pack() — при resizable(False, False)
        # и десятке+ дистрибутивов список раньше выталкивал кнопки «Запустить»/
        # «Отмена» за нижнюю границу экрана без какой-либо возможности прокрутки.
        ver_frame = ttk.LabelFrame(dlg, text="Выберите версии для тестирования", padding="8")
        ver_frame.pack(fill=tk.BOTH, padx=16, pady=4)

        MAX_LIST_HEIGHT = 220
        ver_canvas = tk.Canvas(ver_frame, borderwidth=0, highlightthickness=0,
                               bg=COLORS["bg"])
        ver_vsb = ttk.Scrollbar(ver_frame, orient=tk.VERTICAL, command=ver_canvas.yview)
        ver_canvas.configure(yscrollcommand=ver_vsb.set)
        ver_inner = ttk.Frame(ver_canvas)
        ver_inner_id = ver_canvas.create_window((0, 0), window=ver_inner, anchor="nw")

        def _ver_on_inner_cfg(_e):
            ver_canvas.configure(scrollregion=ver_canvas.bbox("all"))
        def _ver_on_canvas_cfg(e):
            ver_canvas.itemconfig(ver_inner_id, width=e.width)
        ver_inner.bind("<Configure>", _ver_on_inner_cfg)
        ver_canvas.bind("<Configure>", _ver_on_canvas_cfg)

        ver_vars = {}
        for f in files:
            var = tk.BooleanVar(value=True)
            ver_vars[f] = var
            ttk.Checkbutton(ver_inner, text=f.name, variable=var).pack(anchor=tk.W, pady=1)
        # Колесо — на каждой строке списка. Прежняя схема (bind_all на <Enter>
        # холста, unbind_all на <Leave>) отключала прокрутку, как только курсор
        # заходил на строку: для Tk это уход с холста на дочерний виджет.
        self._bind_wheel(ver_canvas, ver_canvas, ver_inner)

        dlg.update_idletasks()
        content_h = min(MAX_LIST_HEIGHT, max(ver_inner.winfo_reqheight(), 24))
        ver_canvas.configure(height=content_h)
        ver_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ver_vsb.pack(side=tk.RIGHT, fill=tk.Y)

        mini = ttk.Frame(dlg)
        mini.pack(fill=tk.X, padx=16, pady=(0, 4))
        ttk.Button(mini, text="☑ Все", width=7,
                   command=lambda: [v.set(True) for v in ver_vars.values()]).pack(side=tk.LEFT)
        ttk.Button(mini, text="☐ Снять", width=7,
                   command=lambda: [v.set(False) for v in ver_vars.values()]).pack(
                       side=tk.LEFT, padx=3)

        ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        # ── Тестовый файл ─────────────────────────────────────────────────────
        file_frame = ttk.LabelFrame(dlg, text="Тестовый файл", padding="8")
        file_frame.pack(fill=tk.X, padx=16, pady=4)

        test_file_var = tk.StringVar()
        for sd in [self.test_files_folder, config.BASE_DIR, Path.home() / "Downloads", Path.home() / "Загрузки"]:
            if not sd.exists():
                continue
            for pat in ["файл-для-теста-Р7-офис-50К*.xlsx", "*50К*.xlsx"]:
                for found in sd.glob(pat):
                    if found.name.startswith("~$"):
                        continue
                    test_file_var.set(str(found))
                    break
            if test_file_var.get():
                break

        file_row = ttk.Frame(file_frame)
        file_row.pack(fill=tk.X)
        ttk.Label(file_row, text="Файл:").pack(side=tk.LEFT)
        ttk.Entry(file_row, textvariable=test_file_var, width=38).pack(
            side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        def browse_test_file():
            path = filedialog.askopenfilename(
                parent=dlg, title="Выберите тестовый файл",
                filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")])
            if path:
                test_file_var.set(path)

        ttk.Button(file_row, text="Обзор", command=browse_test_file).pack(side=tk.LEFT)

        ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        # ── Опции ─────────────────────────────────────────────────────────────
        opt_frame = ttk.LabelFrame(dlg, text="Параметры", padding="8")
        opt_frame.pack(fill=tk.X, padx=16, pady=4)

        stop_on_error_var = tk.BooleanVar(value=True)
        cleanup_var       = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="Останавливаться при первой ошибке",
                        variable=stop_on_error_var).pack(anchor=tk.W)
        ttk.Checkbutton(opt_frame, text="Удалять временные файлы кеша после каждого теста",
                        variable=cleanup_var).pack(anchor=tk.W, pady=(4, 0))

        # ── Кнопки ────────────────────────────────────────────────────────────
        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(pady=12, padx=16, fill=tk.X)

        def on_start():
            selected = [f for f, v in ver_vars.items() if v.get()]
            if not selected:
                messagebox.showwarning("Нет выбора",
                                       "Выберите хотя бы одну версию.", parent=dlg)
                return
            tf = test_file_var.get().strip()
            if not tf or not Path(tf).exists():
                messagebox.showwarning("Файл не найден",
                                       "Укажите существующий тестовый файл.", parent=dlg)
                return
            dlg.destroy()
            self._start_batch_run(selected, Path(tf),
                                  stop_on_error_var.get(), cleanup_var.get())

        ttk.Button(btn_frame, text="▶ Запустить", command=on_start).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Отмена", command=dlg.destroy).pack(side=tk.LEFT)

        dlg.update_idletasks()
        dlg.minsize(460, dlg.winfo_reqheight())

    def _start_batch_run(self, versions, test_file, stop_on_error, cleanup):
        """Захватывает состояние прогона и открывает окно Batch. Сбой до
        запуска потока освобождает состояние — иначе приложение считало бы
        Batch идущим до перезапуска."""
        refusal = self.run_state.try_start(BATCH)
        if refusal:                       # пока шёл диалог, начался другой прогон
            messagebox.showwarning(*refusal)
            return
        try:
            self._open_batch_progress(versions, test_file, stop_on_error, cleanup)
        except Exception:
            self.run_state.finish(BATCH)
            raise

    def _open_batch_progress(self, versions, test_file, stop_on_error, cleanup):
        """Creates the progress window and launches the batch worker thread."""
        prog = tk.Toplevel(self.root)
        prog.transient(self.root)
        prog.configure(bg=COLORS["bg"])
        prog.title("Batch-режим: выполнение")
        prog.geometry("680x540")
        prog.resizable(True, True)

        # ── Шапка прогресса ───────────────────────────────────────────────────
        top = ttk.Frame(prog, padding="10")
        top.pack(fill=tk.X)

        lbl_current = ttk.Label(top, text="Подготовка...", font=("Arial", 10, "bold"))
        lbl_current.pack(anchor=tk.W)

        progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(top, variable=progress_var,
                        maximum=len(versions), mode="determinate").pack(
                            fill=tk.X, pady=(4, 0))

        # ── Список версий с иконками ──────────────────────────────────────────
        ver_list_frame = ttk.LabelFrame(prog, text="Версии", padding="6")
        ver_list_frame.pack(fill=tk.X, padx=10, pady=4)

        ver_labels = {}
        for f in versions:
            var = tk.StringVar(value=f"⏳ {f.name}")
            ttk.Label(ver_list_frame, textvariable=var, anchor=tk.W).pack(anchor=tk.W, pady=1)
            ver_labels[f] = var

        # ── Лог ───────────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(prog, text="Лог", padding="4")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        log_text = tk.Text(log_frame, font=("Consolas", 9), wrap=tk.WORD,
                          bg=COLORS["log_bg"], fg=COLORS["text"],
                          insertbackground=COLORS["text"],
                          borderwidth=0, highlightthickness=0)
        log_scroll = ttk.Scrollbar(log_frame, command=log_text.yview)
        log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Управление ────────────────────────────────────────────────────────
        ctrl = ttk.Frame(prog, padding="6")
        ctrl.pack(fill=tk.X)

        stop_event  = threading.Event()
        pause_event = threading.Event()
        paused = [False]

        def toggle_pause():
            if paused[0]:
                paused[0] = False
                pause_event.clear()
                btn_pause.config(text="⏸ Пауза")
            else:
                paused[0] = True
                pause_event.set()
                btn_pause.config(text="▶ Продолжить")

        def request_stop():
            stop_event.set()
            pause_event.clear()
            btn_stop.config(state=tk.DISABLED)
            _log("⏹ Запрошена остановка...")

        btn_pause = ttk.Button(ctrl, text="⏸ Пауза", command=toggle_pause)
        btn_stop  = ttk.Button(ctrl, text="⏹ Остановить", command=request_stop)
        btn_pause.pack(side=tk.LEFT, padx=5)
        btn_stop.pack(side=tk.LEFT, padx=5)

        # ── UI-callback-и ──────────────────────────────────────────────────────
        def _log(msg):
            def _do():
                try:
                    ts = datetime.now().strftime("%H:%M:%S")
                    log_text.insert(tk.END, f"[{ts}] {msg}\n")
                    log_text.see(tk.END)
                    self.add_test_log(msg)
                except tk.TclError:
                    pass
            try:
                prog.after(0, _do)
            except tk.TclError:
                pass

        def _set_current(text):
            try:
                prog.after(0, lambda: lbl_current.config(text=text))
            except tk.TclError:
                pass

        def _set_ver_status(f, text):
            def _do():
                try:
                    if f in ver_labels:
                        ver_labels[f].set(text)
                except tk.TclError:
                    pass
            try:
                prog.after(0, _do)
            except tk.TclError:
                pass

        def _set_progress(n):
            try:
                prog.after(0, lambda: progress_var.set(n))
            except tk.TclError:
                pass

        def _on_done(batch_results, errors):
            def _do():
                try:
                    btn_pause.config(state=tk.DISABLED)
                    btn_stop.config(state=tk.DISABLED)
                    ok = sum(1 for r in batch_results if r.get("success"))
                    _log(f"✅ Batch-режим завершён. Успешно: {ok}, Ошибок: {errors}")
                    self.status_var.set(f"Batch завершён: {ok}/{len(batch_results)} успешно")
                    self.detect_current_version()
                    if batch_results:
                        html = self._generate_batch_summary_html(batch_results)
                        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
                        out_path = self.reports_folder / f"batch_summary_{ts_now}.html"
                        try:
                            out_path.write_text(html, encoding="utf-8")
                            _log(f"📊 Сводный отчёт: {out_path.name}")
                            webbrowser.open(str(out_path))
                        except Exception as e:
                            _log(f"⚠️ Ошибка сохранения отчёта: {e}")
                except tk.TclError:
                    pass
            try:
                prog.after(0, _do)
            except tk.TclError:
                pass

        self._set_busy_indicator(True, "Идёт Batch-режим")

        def _batch_thread():
            try:
                self._batch_worker(versions, test_file, stop_on_error, cleanup,
                                   _log, _set_current, _set_ver_status, _set_progress,
                                   _on_done, stop_event, pause_event)
            finally:
                self.run_state.finish(BATCH)
                self.root.after(0, lambda: self._set_busy_indicator(False))

        threading.Thread(target=_batch_thread, daemon=True).start()
