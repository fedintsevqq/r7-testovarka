"""Главное окно: вкладки «Версии» и «Производительность», список тестов
с числом повторов, журнал прогона и индикатор занятости.

add_test_log вызывается сотнями раз из фоновых потоков и использует
update_idletasks(), не update(). MainWindowMixin — методы, которые
R7Testovarka получает наследованием.
"""
from r7.run_state import PERF
import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from r7.config import DEFAULT_TEST_RUNS, RUNS_MAX, RUNS_MIN
from r7.ui.base import COLORS, FONT_LOG, FONT_UI


class MainWindowMixin:
    """Главное окно и журнал — часть R7Testovarka (через наследование)."""

    def setup_ui(self):
        """Builds the main UI layout with notebook tabs and status bar."""
        self._apply_dark_theme()
        self.root.bind_class("Toplevel", "<Map>", self._on_toplevel_map, add="+")

        # Строка статуса упаковывается ПЕРВОЙ и снизу: упаковщик раздаёт место
        # в порядке упаковки, и при низком окне последний виджет обрезается
        # первым. Раньше это была именно она.
        self.status_var = tk.StringVar(value="Готов")
        status = ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W, padding=(10, 3),
                           style="Secondary.TLabel")
        status.pack(side=tk.BOTTOM, fill=tk.X)

        main = ttk.Frame(self.root, padding=(10, 8, 10, 4))
        main.pack(fill=tk.BOTH, expand=True)

        # ── Шапка в одну строку: название, установленная версия, состояние ───
        # Раньше версия занимала отдельную карточку шрифтом 16 — ~70 px высоты,
        # которых на ноутбуке не хватало самой вкладке.
        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(header, text="⚡ R7 Testovarka", style="Header.TLabel").pack(side=tk.LEFT)
        self.lbl_status_dot = ttk.Label(header, text="●  Готов", style="StatusOk.TLabel")
        self.lbl_status_dot.pack(side=tk.RIGHT)
        ver_box = ttk.Frame(header)
        ver_box.pack(side=tk.LEFT, padx=(24, 12), fill=tk.X, expand=True)
        ttk.Label(ver_box, text="Установлен:", style="Secondary.TLabel").pack(side=tk.LEFT)
        self.lbl_current = ttk.Label(ver_box, text="определяется…", style="Version.TLabel")
        self.lbl_current.pack(side=tk.LEFT, padx=(6, 0))
        # «Тень» под шапкой: одна тёмная линия — ttk.Style не умеет рисовать
        # настоящую размытую тень, это ближайшее достижимое приближение.
        shadow = tk.Frame(main, height=1, bg=COLORS["border"])
        shadow.pack(fill=tk.X, pady=(0, 6))

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_versions = ttk.Frame(self.notebook)
        self.tab_perf = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_versions, text="📦 Версии")
        self.notebook.add(self.tab_perf, text="⚡ Производительность")

        self._build_versions_tab()
        self._build_perf_tab()

    def _build_versions_tab(self):
        """Builds the distributives table and install controls.

        Кнопки и подсказка упакованы снизу ДО таблицы: при низком окне
        сжимается таблица (у неё своя прокрутка), а не панель кнопок.
        """
        tab = self.tab_versions
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 4))
        self.btn_install = ttk.Button(btn_frame, text="📥 Установить", style="Accent.TButton",
                                      command=self.install_selected, state=tk.DISABLED)
        self.btn_install.pack(side=tk.LEFT, padx=(0, 8))
        self.quiet_install_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btn_frame, text="Тихая установка",
                        variable=self.quiet_install_var).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Button(btn_frame, text="🔐 Проверить хеш-суммы",
                   command=self.check_hashes).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_frame, text="📂 Открыть папку",
                   command=self.open_distributives_folder).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_frame, text="📁 Добавить",
                   command=self.add_distributive).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_frame, text="🔄 Обновить",
                   command=self.refresh_distributives).pack(side=tk.RIGHT, padx=(6, 0))

        self.lbl_file_info = ttk.Label(
            tab, text="Выберите дистрибутив в таблице, чтобы установить его.",
            style="Secondary.TLabel")
        self.lbl_file_info.pack(side=tk.BOTTOM, anchor=tk.W, pady=(4, 0))

        ttk.Label(tab, text="Дистрибутивы (папка Distributives)", style="Secondary.TLabel").pack(
            anchor=tk.W, pady=(6, 4))
        frame = ttk.Frame(tab, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(
            frame, columns=("name", "version", "size"), show="headings",
            selectmode="browse", yscrollcommand=scroll.set, height=6)
        self.tree.heading("name", text="Имя")
        self.tree.heading("version", text="Версия")
        self.tree.heading("size", text="Размер (МБ)")
        # Растягивается только имя: версия и размер короткие, и раньше
        # таблица разносила их на полэкрана от имени.
        self.tree.column("name", width=360, anchor=tk.W, stretch=True)
        self.tree.column("version", width=150, anchor=tk.CENTER, stretch=False)
        self.tree.column("size", width=110, anchor=tk.E, stretch=False)
        self.tree.pack(fill=tk.BOTH, expand=True)
        scroll.config(command=self.tree.yview)

        self.tree.bind('<<TreeviewSelect>>', self.on_select_distributive)
        # Двойной щелчок и Enter по строке — то же, что кнопка «Установить».
        self.tree.bind('<Double-1>', lambda _e: self._install_if_selected())
        self.tree.bind('<Return>', lambda _e: self._install_if_selected())

    def _install_if_selected(self):
        """Запускает установку, только если строка выбрана и кнопка доступна."""
        if self.tree.selection() and str(self.btn_install.cget("state")) != tk.DISABLED:
            self.install_selected()

    # ---------------------- Вкладка «Производительность» ----------------------
    LOG_HINT = ("Здесь появится ход прогона.\n\n"
                "1. Отметьте тесты в списке слева. Щелчок по названию тоже "
                "включает и выключает тест.\n"
                "2. Задайте число повторов кнопками «−» и «+» или введите его "
                f"с клавиатуры ({RUNS_MIN}–{RUNS_MAX}).\n"
                "3. Нажмите «Запустить выбранные тесты».\n\n"
                "Выбор тестов и число повторов сохраняются сами.")

    def _make_runs_control(self, parent, runs_var):
        """Поле числа повторов: «−» [N] «+».

        Вместо ttk.Spinbox: у него стрелки по 8 px, в которые трудно
        попасть, и он принимал любой текст. Здесь в поле можно ввести только
        цифры, значение прижимается к RUNS_MIN..RUNS_MAX при уходе фокуса или
        Enter, стрелки ↑/↓ в поле меняют его на 1.

        Returns:
            ttk.Frame: контейнер; у него есть метод commit() — применить то,
            что введено, но ещё не подтверждено.
        """
        box = ttk.Frame(parent)
        text = tk.StringVar(value=str(runs_var.get()))

        def commit(*_):
            value = self._clamp_runs(text.get(), runs_var.get())
            if value != runs_var.get():
                runs_var.set(value)
            text.set(str(value))

        def step(delta):
            commit()
            runs_var.set(max(RUNS_MIN, min(RUNS_MAX, runs_var.get() + delta)))
            return "break"

        runs_var.trace_add("write", lambda *_: text.set(str(runs_var.get())))
        only_digits = (box.register(lambda p: p == "" or (p.isdigit() and len(p) <= 2)), "%P")
        ttk.Button(box, text="−", width=2, style="Step.TButton", takefocus=False,
                   command=lambda: step(-1)).pack(side=tk.LEFT)
        entry = ttk.Entry(box, textvariable=text, width=3, justify=tk.CENTER,
                          validate="key", validatecommand=only_digits, style="Runs.TEntry")
        entry.pack(side=tk.LEFT, padx=2)
        ttk.Button(box, text="+", width=2, style="Step.TButton", takefocus=False,
                   command=lambda: step(1)).pack(side=tk.LEFT)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Return>", commit)
        entry.bind("<Up>", lambda _e: step(1))
        entry.bind("<Down>", lambda _e: step(-1))
        box.commit = commit
        return box

    def _build_test_list(self, parent):
        """Панель выбора тестов: список по группам с прокруткой.

        Returns:
            ttk.Frame: панель для Panedwindow.
        """
        panel = ttk.Frame(parent, padding=(0, 0, 8, 0))
        saved = self._load_test_selection()
        self.test_vars = {}
        self.test_runs = {}
        self._runs_controls = []

        head = ttk.Frame(panel)
        head.pack(fill=tk.X)
        ttk.Label(head, text="Тесты", style="Version.TLabel").pack(side=tk.LEFT)
        ttk.Button(head, text="Снять все", style="Small.TButton",
                   command=lambda: self._set_all_tests(False)).pack(side=tk.RIGHT)
        ttk.Button(head, text="Отметить все", style="Small.TButton",
                   command=lambda: self._set_all_tests(True)).pack(side=tk.RIGHT, padx=(0, 4))

        bulk = ttk.Frame(panel)
        bulk.pack(fill=tk.X, pady=(6, 6))
        ttk.Label(bulk, text="Повторов у отмеченных:", style="Secondary.TLabel").pack(side=tk.LEFT)
        self._bulk_runs = tk.IntVar(value=DEFAULT_TEST_RUNS)
        self._bulk_runs_control = self._make_runs_control(bulk, self._bulk_runs)
        self._bulk_runs_control.pack(side=tk.LEFT, padx=6)
        ttk.Button(bulk, text="Применить", style="Small.TButton",
                   command=self._apply_bulk_runs).pack(side=tk.LEFT)

        self.lbl_tests_summary = ttk.Label(panel, text="", style="Secondary.TLabel")
        self.lbl_tests_summary.pack(side=tk.BOTTOM, anchor=tk.W, pady=(6, 0))

        area = ttk.Frame(panel, style="Card.TFrame")
        area.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(area, bg=COLORS["bg_card"], highlightthickness=0, borderwidth=0,
                           width=10, height=160, yscrollincrement=24)
        vsb = ttk.Scrollbar(area, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = ttk.Frame(canvas, style="Card.TFrame", padding=(6, 4, 6, 6))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.columnconfigure(1, weight=1)

        ttk.Label(inner, text="Повторы", style="Secondary.TLabel",
                  background=COLORS["bg_card"]).grid(row=0, column=2, sticky=tk.E, pady=(0, 2))
        row = 1
        self._building_test_list = True
        for title, names in self._test_groups():
            grp = ttk.Label(inner, text=title, style="Group.TLabel",
                            background=COLORS["bg_card"], cursor="hand2")
            grp.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=(10 if row > 1 else 0, 2))
            # Щелчок по заголовку группы — включить всю группу, а если она уже
            # вся включена — выключить.
            grp.bind("<Button-1>", lambda _e, ns=names: self._toggle_group(ns))
            row += 1
            for name in names:
                entry = saved.get(name) or self._default_test_entry(name)
                default = self._default_test_entry(name)
                var = tk.BooleanVar(value=bool(entry.get("enabled", default["enabled"])))
                runs_var = tk.IntVar(value=self._clamp_runs(entry.get("runs"), default["runs"]))
                ttk.Checkbutton(inner, variable=var, style="Card.TCheckbutton",
                                takefocus=False).grid(row=row, column=0, sticky=tk.W, pady=1)
                lbl = ttk.Label(inner, text=name, style="Card.TLabel", cursor="hand2")
                lbl.grid(row=row, column=1, sticky=tk.W, padx=(2, 12))
                lbl.bind("<Button-1>", lambda _e, v=var: v.set(not v.get()))
                ctl = self._make_runs_control(inner, runs_var)
                ctl.grid(row=row, column=2, sticky=tk.E, pady=1)
                self._runs_controls.append(ctl)

                def _refresh(*_a, v=var, label=lbl):
                    label.configure(foreground=COLORS["text"] if v.get()
                                    else COLORS["text_secondary"])
                    self._on_test_selection_changed()
                var.trace_add("write", _refresh)
                runs_var.trace_add("write", lambda *_a: self._on_test_selection_changed())
                _refresh()
                self.test_vars[name] = var
                self.test_runs[name] = runs_var
                row += 1
        self._building_test_list = False

        def _on_inner_configure(_event):
            # Холст по ширине содержимого: панель просит ровно столько места,
            # сколько занимают строки, остальное отдаётся логу.
            canvas.configure(scrollregion=canvas.bbox("all"), width=inner.winfo_reqwidth())
        inner.bind("<Configure>", _on_inner_configure)
        self._bind_wheel(area, canvas, inner)
        self._update_tests_summary()
        return panel

    def _set_all_tests(self, enabled):
        for var in self.test_vars.values():
            var.set(enabled)

    def _toggle_group(self, names):
        enable = not all(self.test_vars[n].get() for n in names)
        for n in names:
            self.test_vars[n].set(enable)

    def _commit_runs_inputs(self):
        """Применяет недоподтверждённый ввод во всех полях повторов."""
        for ctl in getattr(self, "_runs_controls", []):
            ctl.commit()

    def _apply_bulk_runs(self):
        """«Применить»: число повторов из общего поля — всем отмеченным тестам."""
        self._bulk_runs_control.commit()
        value = self._bulk_runs.get()
        for name, var in self.test_vars.items():
            if var.get():
                self.test_runs[name].set(value)

    def _selection_summary(self):
        """(отмечено, всего, сумма повторов, отмечен ли экспорт)."""
        chosen = [n for n, v in self.test_vars.items() if v.get()]
        runs = 0
        for n in chosen:
            try:
                runs += int(self.test_runs[n].get())
            except (tk.TclError, ValueError):
                pass
        return (len(chosen), len(self.test_vars), runs,
                any(n in self.EXPORT_TESTS for n in chosen))

    def _update_tests_summary(self):
        chosen, total, runs, export = self._selection_summary()
        text = f"Отмечено {chosen} из {total} · всего повторов: {runs}"
        if export:
            text += " · экспорт идёт долго"
        try:
            self.lbl_tests_summary.config(text=text)
            self.btn_run_perf.config(
                state=tk.NORMAL if chosen and not self._perf_running else tk.DISABLED)
        except (AttributeError, tk.TclError):
            pass  # виджеты ещё не созданы — первая сводка при сборке панели

    def _on_test_selection_changed(self):
        """Сводка сразу, сохранение в selected_tests.json — с задержкой.

        Раньше выбор сохранялся только при нажатии «Запустить», и закрытое
        без запуска окно теряло всё, что пользователь отметил.
        """
        self._update_tests_summary()
        if getattr(self, "_building_test_list", False):
            return  # начальные значения при сборке — сохранять нечего
        pending = getattr(self, "_save_selection_job", None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except tk.TclError:
                pass
        self._save_selection_job = self.root.after(800, self._save_test_selection)

    def _set_busy_indicator(self, busy, text=None):
        """Индикатор в правом верхнем углу: «● Готов» / «● Идёт прогон»."""
        try:
            self.lbl_status_dot.config(
                text=f"●  {text or ('Идёт прогон' if busy else 'Готов')}",
                style="StatusErr.TLabel" if busy else "StatusOk.TLabel")
        except (AttributeError, tk.TclError):
            pass

    def _clear_test_log(self):
        self.test_log.delete("1.0", tk.END)
        self._log_hint_shown = False

    def _build_perf_tab(self):
        """Builds the performance tab: test list | log, run bar, tools.

        Панели кнопок упакованы снизу ДО содержимого: при низком окне
        сжимаются список тестов и лог (у обоих своя прокрутка), а кнопка
        «Запустить» остаётся видна всегда.
        """
        tab = self.tab_perf

        # ── Нижняя панель: запуск, прогресс, инструменты ──────────────────────
        tools = ttk.Frame(tab)
        tools.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 2))
        ttk.Label(tools, text="Инструменты:", style="Secondary.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        for caption, command in (("🚀 Batch-режим (все версии)", self.run_batch_mode),
                                 ("📊 Сравнить версии", self.compare_versions),
                                 ("📈 Тренды", self.show_trends),
                                 ("📄 Тестовые файлы", self.compare_file_sizes)):
            ttk.Button(tools, text=caption, command=command).pack(side=tk.LEFT, padx=(0, 6))

        run_row = ttk.Frame(tab)
        run_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        self.btn_run_perf = ttk.Button(
            run_row, text="▶ Запустить выбранные тесты", style="Accent.TButton",
            command=self.run_spreadsheet_test)
        self.btn_run_perf.pack(side=tk.LEFT)
        self.btn_stop_perf = ttk.Button(
            run_row, text="⏹ Остановить", command=self._request_stop_perf_test,
            state=tk.DISABLED)
        self.btn_stop_perf.pack(side=tk.LEFT, padx=(8, 12))
        self.progress_var = tk.DoubleVar(value=0)
        self.lbl_progress = ttk.Label(run_row, text="", width=5, anchor=tk.E,
                                      style="Secondary.TLabel")
        self.lbl_progress.pack(side=tk.RIGHT)
        ttk.Progressbar(run_row, variable=self.progress_var, maximum=100,
                        mode="determinate").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.progress_var.trace_add(
            "write", lambda *_: self.lbl_progress.config(
                text=f"{self.progress_var.get():.0f}%" if self.progress_var.get() else ""))

        # ── Список тестов | лог — с перетаскиваемой границей ─────────────────
        paned = ttk.Panedwindow(tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        paned.add(self._build_test_list(paned), weight=0)

        log_panel = ttk.Frame(paned, padding=(8, 0, 0, 0))
        paned.add(log_panel, weight=1)
        log_head = ttk.Frame(log_panel)
        log_head.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(log_head, text="Лог прогона", style="Version.TLabel").pack(side=tk.LEFT)
        ttk.Button(log_head, text="📂 Папка отчётов", style="Small.TButton",
                   command=lambda: os.startfile(str(self.reports_folder))).pack(side=tk.RIGHT)
        ttk.Button(log_head, text="Очистить", style="Small.TButton",
                   command=self._clear_test_log).pack(side=tk.RIGHT, padx=(0, 4))

        log_frame = ttk.Frame(log_panel)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.test_log = tk.Text(log_frame, font=FONT_LOG, bg=COLORS["log_bg"],
                                fg=COLORS["text"], insertbackground=COLORS["text"],
                                borderwidth=0, highlightthickness=0, wrap=tk.WORD,
                                width=40, height=8, padx=8, pady=6)
        self.test_log.tag_configure("INFO", foreground=COLORS["success"])
        self.test_log.tag_configure("WARN", foreground=COLORS["warn"])
        self.test_log.tag_configure("ERROR", foreground=COLORS["error"])
        self.test_log.tag_configure("HINT", foreground=COLORS["text_secondary"],
                                    font=FONT_UI, spacing1=2)
        scroll_log = ttk.Scrollbar(log_frame, command=self.test_log.yview)
        self.test_log.configure(yscrollcommand=scroll_log.set)
        scroll_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.test_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Лог — только для чтения с клавиатуры: копировать можно (Ctrl+C,
        # Ctrl+A), печатать в него — нет. Программа пишет в него через insert.
        self.test_log.bind("<Key>", lambda e: None if e.state & 0x4 else "break")
        self.test_log.insert("1.0", self.LOG_HINT, "HINT")
        self._log_hint_shown = True
        self._update_tests_summary()

    # ---------------------- Лог ----------------------
    def add_test_log(self, msg):
        """Appends a timestamped, severity-colored message to the performance log.

        Severity is inferred from the leading emoji already used consistently
        throughout the codebase (❌/⚠️ for errors/warnings, everything else
        default) — no call site elsewhere in the file needs to change.

        Args:
            msg: The text to append.
        """
        try:
            if getattr(self, "_log_hint_shown", False):
                # Первое настоящее сообщение убирает подсказку «как запустить».
                self.test_log.delete("1.0", tk.END)
                self._log_hint_shown = False
            if msg.startswith("❌"):
                tag = "ERROR"
            elif msg.startswith("⚠️"):
                tag = "WARN"
            else:
                tag = "INFO"
            line = f"[{datetime.now():%H:%M:%S}] {msg}\n"
            self.test_log.insert(tk.END, line, tag)
            self.test_log.see(tk.END)
            # update_idletasks (не update!): перерисовывает накопившиеся
            # изменения без обработки очереди событий Tk. add_test_log
            # вызывается сотнями раз за прогон из фоновых потоков — update()
            # заходил бы в главный цикл Tk и обрабатывал там события, включая
            # нажатия кнопок, реентерабельно посреди стека фонового потока.
            self.root.update_idletasks()
        except Exception:
            print(msg)

    def _set_perf_progress(self, done, total):
        """Updates the Performance tab's progress bar (0-100%). Safe to call
        even if the widget doesn't exist yet or the app is in another mode.
        Marshals the actual Tk update onto the main thread via root.after,
        since this is called from the worker thread during a test run."""
        try:
            pct = 100 * done / total if total else 0
            self.root.after(0, lambda: self.progress_var.set(pct))
        except Exception:
            pass

    # ---------------------- Стресс-тест таблиц ----------------------
    def _reset_perf_buttons(self):
        """Возвращает кнопки вкладки «Производительность» в состояние покоя.

        Вызывается из главного потока (через root.after) в finally-обёртке
        рабочего потока — при любом исходе: нормальном завершении,
        досрочной остановке или исключении.
        """
        self.run_state.finish(PERF)
        self._set_busy_indicator(False)
        try:
            self.btn_run_perf.config(state=tk.NORMAL)
            self.btn_stop_perf.config(state=tk.DISABLED)
        except Exception:
            pass
        self._update_tests_summary()  # «Запустить» недоступна, если ничего не отмечено

    def _request_stop_perf_test(self):
        """Обработчик кнопки «⏹ Остановить»: просит рабочий поток прерваться
        между операциями. Р7-Офис закрывается штатно, отчёт по уже
        выполненным операциям всё равно сохраняется."""
        self.perf_stop_event.set()
        self.btn_stop_perf.config(state=tk.DISABLED)
        self.add_test_log("⏹ Запрошена остановка теста...")
