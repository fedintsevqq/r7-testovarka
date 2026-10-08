"""Главное окно: вкладки «Версии», «Производительность» и «Сценарии»
(r7/ui/scenarios_tab.py), список тестов с числом повторов, журнал прогона и
индикатор занятости.

add_test_log вызывается сотнями раз из фоновых потоков: сообщения идут
через очередь, виджет пишет только главный поток. MainWindowMixin — методы, которые
R7Testovarka получает наследованием.
"""
import os
import queue
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk

from r7 import logfile, privileges, update_check
from r7.config import DEFAULT_TEST_RUNS, RUNS_MAX, RUNS_MIN
from r7.editors import EDITOR_LABELS
from r7.plugins import PLUGIN_MARK
from r7.run_state import PERF
from r7.ui.base import COLORS, FONT_LOG


class MainWindowMixin:
    """Главное окно и журнал — часть R7Testovarka (через наследование)."""

    def setup_ui(self):
        """Builds the main UI layout with notebook tabs and status bar."""
        self._apply_dark_theme()
        self._install_tk_error_handler()
        self.root.bind_class("Toplevel", "<Map>", self._on_toplevel_map, add="+")

        # Строка статуса упаковывается ПЕРВОЙ и снизу: упаковщик раздаёт место
        # в порядке упаковки, и при низком окне последний виджет обрезается
        # первым. Раньше это была именно она.
        self.status_var = tk.StringVar(value="Готов")
        status = ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W, padding=(14, 4),
                           style="Secondary.TLabel")
        status.pack(side=tk.BOTTOM, fill=tk.X)

        main = ttk.Frame(self.root, padding=(14, 10, 14, 4))
        main.pack(fill=tk.BOTH, expand=True)

        # ── Шапка в одну строку: название, установленная версия, состояние ───
        # Раньше версия занимала отдельную карточку шрифтом 16 — ~70 px высоты,
        # которых на ноутбуке не хватало самой вкладке.
        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(header, text="R7 Testovarka", style="Header.TLabel").pack(side=tk.LEFT)
        theme_btn = self._icon_button(header, "", "theme", command=self._toggle_theme,
                                      style="Small.TButton",
                                      tooltip="Светлая или тёмная тема")
        theme_btn.pack(side=tk.RIGHT, padx=(12, 0))
        self.btn_theme = theme_btn
        self.lbl_status_dot = ttk.Label(header, text="●  Готов", style="StatusOk.TLabel")
        self.lbl_status_dot.pack(side=tk.RIGHT)
        # Ссылка на новый релиз; создаётся скрытой, показывает _show_update_link.
        self.lbl_update = ttk.Label(header, text="", style="StatusBusy.TLabel", cursor="hand2")
        ver_box = ttk.Frame(header)
        ver_box.pack(side=tk.LEFT, padx=(24, 12), fill=tk.X, expand=True)
        ttk.Label(ver_box, text="Установлен:", style="Secondary.TLabel").pack(side=tk.LEFT)
        self.lbl_current = ttk.Label(ver_box, text="определяется…", style="Version.TLabel")
        self.lbl_current.pack(side=tk.LEFT, padx=(6, 0))
        # «Тень» под шапкой: одна тёмная линия — ttk.Style не умеет рисовать
        # настоящую размытую тень, это ближайшее достижимое приближение.
        shadow = tk.Frame(main, height=1, bg=COLORS["border"])
        shadow.pack(fill=tk.X, pady=(0, 8))

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_versions = ttk.Frame(self.notebook)
        self.tab_perf = ttk.Frame(self.notebook)
        self.tab_scenarios = ttk.Frame(self.notebook, padding=(0, 0, 0, 0))

        self.notebook.add(self.tab_versions, text=" Версии ", compound=tk.LEFT)
        self.notebook.add(self.tab_perf, text=" Производительность ", compound=tk.LEFT)
        self.notebook.add(self.tab_scenarios, text=" Сценарии ", compound=tk.LEFT)
        self._refresh_tab_icons()

        self._build_versions_tab()
        self._build_perf_tab()
        self._build_scenarios_tab()

    def _refresh_tab_icons(self):
        """Значки вкладок — в цвет текста текущей темы."""
        for tab, icon in ((self.tab_versions, "versions"), (self.tab_perf, "perf"),
                          (self.tab_scenarios, "scenarios")):
            self.notebook.tab(tab, image=self.icons.get(icon, COLORS["text"]) or "")

    def _build_versions_tab(self):
        """Builds the distributives table and install controls.

        Кнопки и подсказка упакованы снизу ДО таблицы: при низком окне
        сжимается таблица (у неё своя прокрутка), а не панель кнопок.
        """
        tab = self.tab_versions
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 4))
        # Без прав администратора кнопка остаётся выключенной (включает её
        # только _enable_install_button), а подсказка объясняет почему.
        self.btn_install = self._icon_button(
            btn_frame, "Установить", "install", command=self.install_selected,
            style="Accent.TButton", state=tk.DISABLED,
            tooltip=None if privileges.is_admin() else self.NO_ADMIN_INSTALL_HINT)
        self.btn_install.pack(side=tk.LEFT, padx=(0, 8))
        self.quiet_install_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btn_frame, text="Тихая установка",
                        variable=self.quiet_install_var).pack(side=tk.LEFT, padx=(0, 16))
        # Шесть кнопок в один ряд при 1220 px: «Обновить» — только значок с
        # подсказкой, иначе подписи обрезались.
        for caption, icon, command, tip in (
                ("Хеш-суммы", "hashes", self.check_hashes, "Проверить хеш-суммы дистрибутивов"),
                ("Открыть папку", "folder", self.open_distributives_folder, None),
                ("Поискать в Загрузках", "search", self.search_distributives,
                 "Найти r7-office*.exe/.msi в «Загрузках» и на «Рабочем столе»"),
                ("Добавить папку", "folder", self.add_distributives_dir,
                 "Папка с дистрибутивами, в том числе сетевая; файлы не копируются"),
                ("Добавить", "add", self.add_distributive, "Скопировать файл в Distributives"),
                ("", "refresh", self.refresh_distributives, "Обновить список")):
            self._icon_button(btn_frame, caption, icon, command=command, tooltip=tip).pack(
                side=tk.RIGHT, padx=(6, 0))

        self.lbl_file_info = ttk.Label(
            tab, text=("Выберите дистрибутив в таблице, чтобы установить его."
                       if privileges.is_admin() else self.NO_ADMIN_INSTALL_TEXT),
            style="Secondary.TLabel")
        self.lbl_file_info.pack(side=tk.BOTTOM, anchor=tk.W, pady=(4, 0))

        ttk.Label(tab, text="Дистрибутивы", style="Title.TLabel").pack(anchor=tk.W, pady=(8, 0))
        ttk.Label(tab, text="Двойной щелчок по строке ставит версию; другие папки — «Добавить папку»",
                  style="Secondary.TLabel").pack(anchor=tk.W, pady=(0, 6))
        frame = ttk.Frame(tab)
        frame.pack(fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(
            frame, columns=("name", "version", "size", "folder"), show="headings",
            selectmode="browse", yscrollcommand=scroll.set, height=6)
        self.tree.heading("name", text="Имя")
        self.tree.heading("version", text="Версия")
        self.tree.heading("size", text="Размер (МБ)")
        self.tree.heading("folder", text="Папка")
        # Растягивается только имя: версия и размер короткие, и раньше
        # таблица разносила их на полэкрана от имени.
        self.tree.column("name", width=360, anchor=tk.W, stretch=True)
        self.tree.column("version", width=150, anchor=tk.CENTER, stretch=False)
        self.tree.column("size", width=110, anchor=tk.E, stretch=False)
        self.tree.column("folder", width=220, anchor=tk.W, stretch=False)
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
                "1. Выберите редактор над списком: таблица, документ или "
                "презентация. У каждого свои тесты.\n"
                "2. Отметьте тесты в списке слева. Щелчок по названию тоже "
                "включает и выключает тест.\n"
                "3. Задайте число повторов кнопками «−» и «+» или введите его "
                f"с клавиатуры ({RUNS_MIN}–{RUNS_MAX}).\n"
                "4. Нажмите «Запустить выбранные тесты».\n\n"
                "Выбор тестов и число повторов сохраняются сами, у каждого "
                "редактора свои.")

    def _make_runs_control(self, parent, runs_var, panel=False):
        """Поле числа повторов: «−» [N] «+».

        Вместо ttk.Spinbox: у него стрелки по 8 px, в которые трудно
        попасть, и он принимал любой текст. Здесь в поле можно ввести только
        цифры, значение прижимается к RUNS_MIN..RUNS_MAX при уходе фокуса или
        Enter, стрелки ↑/↓ в поле меняют его на 1.

        Returns:
            ttk.Frame: контейнер; у него есть метод commit() — применить то,
            что введено, но ещё не подтверждено.
        """
        box = ttk.Frame(parent, style="Panel.TFrame" if panel else "TFrame")
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
        self.test_vars = {}
        self.test_runs = {}
        self._runs_controls = []
        # Редактор — тот, что был выбран в прошлый раз (selected_tests.json).
        self._perf_editor = self._saved_perf_editor()

        head = ttk.Frame(panel)
        head.pack(fill=tk.X)
        ttk.Label(head, text="Тесты", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Button(head, text="Снять все", style="Small.TButton",
                   command=lambda: self._set_all_tests(False)).pack(side=tk.RIGHT)
        ttk.Button(head, text="Отметить все", style="Small.TButton",
                   command=lambda: self._set_all_tests(True)).pack(side=tk.RIGHT, padx=(0, 4))

        # Переключатель редактора: обычные радиокнопки с точкой. Сегменты
        # Toolbutton в тёмной теме sv-ttk выбранный не подсвечивали — все три
        # выглядели одинаково (живая проверка 08.10.2026). Смена пересобирает
        # список тестов.
        selector = ttk.Frame(panel)
        selector.pack(fill=tk.X, pady=(6, 0))
        self.perf_editor_var = tk.StringVar(value=self._perf_editor)
        self._editor_radios = []
        for editor, caption in EDITOR_LABELS.items():
            rb = ttk.Radiobutton(selector, text=caption, value=editor,
                                 variable=self.perf_editor_var,
                                 command=self._on_perf_editor_selected)
            rb.pack(side=tk.LEFT, padx=(0, 12))
            self._editor_radios.append(rb)
        self.lbl_fixture_hint = ttk.Label(panel, text="", style="Secondary.TLabel",
                                          wraplength=360, justify=tk.LEFT)
        self.lbl_fixture_hint.pack(fill=tk.X, anchor=tk.W, pady=(4, 0))

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

        area = ttk.Frame(panel, style="Panel.TFrame")
        area.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(area, bg=COLORS["bg_card"], highlightthickness=0, borderwidth=0,
                           width=10, height=160, yscrollincrement=28)
        canvas._r7_panel = True          # фон панели при смене темы
        vsb = ttk.Scrollbar(area, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = ttk.Frame(canvas, style="Panel.TFrame", padding=(10, 8, 10, 10))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.columnconfigure(1, weight=1)
        self._tests_canvas, self._tests_inner = canvas, inner

        def _on_inner_configure(_event):
            # Холст по ширине содержимого: панель просит ровно столько места,
            # сколько занимают строки, остальное отдаётся логу.
            canvas.configure(scrollregion=canvas.bbox("all"), width=inner.winfo_reqwidth())
        inner.bind("<Configure>", _on_inner_configure)
        self._bind_wheel(area, canvas, inner)
        self._fill_test_rows()
        return panel

    def _fill_test_rows(self):
        """Строки списка тестов редактора self._perf_editor: группы, флажки,
        повторы. Зовётся при сборке панели и при смене редактора — прежние
        строки удаляются, выбор берётся из selected_tests.json."""
        canvas, inner = self._tests_canvas, self._tests_inner
        for child in inner.winfo_children():
            child.destroy()
        saved = self._load_test_selection()
        self.test_vars = {}
        self.test_runs = {}
        self._runs_controls = []

        ttk.Label(inner, text="Повторы", style="PanelDim.TLabel").grid(
            row=0, column=2, sticky=tk.E, pady=(0, 2))
        row = 1
        self._building_test_list = True
        for title, names in self._test_groups():
            grp = ttk.Label(inner, text=title, style="Group.TLabel", cursor="hand2")
            grp.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=(12 if row > 1 else 0, 4))
            # Щелчок по заголовку группы — включить всю группу, а если она уже
            # вся включена — выключить.
            grp.bind("<Button-1>", lambda _e, ns=names: self._toggle_group(ns))
            row += 1
            for name in names:
                entry = saved.get(name) or self._default_test_entry(name)
                default = self._default_test_entry(name)
                var = tk.BooleanVar(value=bool(entry.get("enabled", default["enabled"])))
                runs_var = tk.IntVar(value=self._clamp_runs(entry.get("runs"), default["runs"]))
                # Флажок доступен с клавиатуры: Tab — к следующему тесту,
                # пробел — включить или выключить, список прокручивается к фокусу.
                cb = ttk.Checkbutton(inner, variable=var, style="Panel.TCheckbutton")
                cb.grid(row=row, column=0, sticky=tk.W, pady=2)
                cb.bind("<FocusIn>", lambda _e, w=cb: self._scroll_into_view(canvas, inner, w),
                        add="+")
                # Тест из plugins/*.py помечен: его код не из поставки.
                plugin = self._plugin_test(name)
                text = f"{name} · {PLUGIN_MARK}" if plugin else name
                lbl = ttk.Label(inner, text=text, style="Panel.TLabel", cursor="hand2")
                lbl.grid(row=row, column=1, sticky=tk.W, padx=(2, 12))
                lbl.bind("<Button-1>", lambda _e, v=var: v.set(not v.get()))
                ctl = self._make_runs_control(inner, runs_var, panel=True)
                ctl.grid(row=row, column=2, sticky=tk.E, pady=2)
                self._runs_controls.append(ctl)

                def _refresh(*_a, v=var, label=lbl):
                    # Стиль, а не цвет: при смене темы приглушённые строки
                    # перекрашиваются вместе со стилем.
                    label.configure(style="Panel.TLabel" if v.get() else "PanelDim.TLabel")
                    self._on_test_selection_changed()
                var.trace_add("write", _refresh)
                runs_var.trace_add("write", lambda *_a: self._on_test_selection_changed())
                _refresh()
                self.test_vars[name] = var
                self.test_runs[name] = runs_var
                row += 1
        self._building_test_list = False
        # Колесо — на новых строках: _bind_wheel вешает обработчик только на
        # виджеты, которые уже есть (холст и рамка получили его при сборке).
        for child in inner.winfo_children():
            self._bind_wheel(child, canvas, inner)
        canvas.yview_moveto(0)
        self._update_tests_summary()
        self._update_fixture_hint()

    @staticmethod
    def _scroll_into_view(canvas, content, widget):
        """Прокручивает список тестов так, чтобы виджет в фокусе был виден."""
        try:
            total = max(1, content.winfo_height())
            top = widget.winfo_y() + (widget.master.winfo_y() if widget.master is not content else 0)
            bottom = top + widget.winfo_height()
            view_top, view_bottom = (f * total for f in canvas.yview())
            if top < view_top:
                canvas.yview_moveto(max(0.0, (top - 8) / total))
            elif bottom > view_bottom:
                canvas.yview_moveto(min(1.0, (bottom + 8 - canvas.winfo_height()) / total))
        except tk.TclError:  # список уже закрыт — прокручивать нечего
            pass

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
            except (tk.TclError, ValueError):  # в поле повторов не число — в сумму не входит
                pass
        return (len(chosen), len(self.test_vars), runs,
                any(self._tab_is_export(n) for n in chosen))

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
            except tk.TclError:  # отложенное сохранение уже выполнилось
                pass
        self._save_selection_job = self.root.after(800, self._save_test_selection)

    def _start_update_check(self):
        """Проверка новой версии в GitHub Releases — daemon-поток, чтобы
        запуск не ждал сети; результат — в шапку через _ui_call."""
        def _worker():
            info = update_check.check_for_update()
            if info:
                self._ui_call(lambda: self._show_update_link(info))
        threading.Thread(target=_worker, daemon=True).start()

    def _show_update_link(self, info):
        try:
            self.lbl_update.config(text=f"Доступна версия {info['latest']}")
            self.lbl_update.bind("<Button-1>", lambda _e: webbrowser.open(info["url"]))
            self.lbl_update.pack(side=tk.RIGHT, padx=(0, 12))
        except tk.TclError:  # окно закрыто раньше, чем ответил GitHub
            pass

    def _set_busy_indicator(self, busy, text=None):
        """Индикатор в правом верхнем углу: «● Готов» / «● Идёт прогон»."""
        try:
            self.lbl_status_dot.config(
                text=f"●  {text or ('Идёт прогон' if busy else 'Готов')}",
                style="StatusBusy.TLabel" if busy else "StatusOk.TLabel")
        except (AttributeError, tk.TclError):  # индикатор ещё не создан или окно закрыто
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
        ttk.Label(tools, text="Инструменты", style="Secondary.TLabel").pack(side=tk.LEFT, padx=(0, 10))
        for caption, icon, command in (("Batch-режим", "batch", self.run_batch_mode),
                                       ("Сравнить версии", "compare", self.compare_versions),
                                       ("Тренды", "trends", self.show_trends),
                                       ("Тестовые файлы", "files", self.compare_file_sizes)):
            self._icon_button(tools, caption, icon, command=command).pack(side=tk.LEFT, padx=(0, 6))

        run_row = ttk.Frame(tab)
        run_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        self.btn_run_perf = self._icon_button(
            run_row, "Запустить выбранные тесты", "play", style="Accent.TButton",
            command=self.run_spreadsheet_test)
        self.btn_run_perf.pack(side=tk.LEFT)
        self.btn_stop_perf = self._icon_button(
            run_row, "Остановить", "stop", command=self._request_stop_perf_test,
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
        ttk.Label(log_head, text="Журнал прогона", style="Title.TLabel").pack(side=tk.LEFT)
        self._icon_button(log_head, "Папка отчётов", "folder", style="Small.TButton",
                          command=lambda: os.startfile(str(self.reports_folder))).pack(side=tk.RIGHT)
        self._icon_button(log_head, "Очистить", "clear", style="Small.TButton",
                          command=self._clear_test_log).pack(side=tk.RIGHT, padx=(0, 4))

        log_frame = ttk.Frame(log_panel)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.test_log = tk.Text(log_frame, font=FONT_LOG, bg=COLORS["log_bg"],
                                fg=COLORS["text"], insertbackground=COLORS["text"],
                                borderwidth=0, highlightthickness=0, wrap=tk.WORD,
                                width=40, height=8, padx=12, pady=10, spacing1=1, spacing3=1)
        self._configure_log_tags(self.test_log)
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
    # Сколько сообщений журнала выводить за один проход главного потока и как
    # часто проверять очередь: прогон пишет сотни строк, окно не должно залипать.
    LOG_DRAIN_BATCH = 200
    LOG_DRAIN_MS = 50

    def add_test_log(self, msg):
        """Добавляет строку в журнал прогона — из любого потока.

        Прежде фоновые потоки писали прямо в виджет (insert и
        update_idletasks из чужого потока), а Tk не потокобезопасен: правило
        «виджеты — только из главного потока» нарушал сам журнал. Теперь
        фоновый поток кладёт сообщение с меткой времени в очередь, а главный
        выводит его (_drain_test_log, раз в LOG_DRAIN_MS). Из главного потока
        — сразу, но сначала то, что уже ждёт в очереди: порядок строк
        сохраняется.

        Уровень — по первому символу, как и прежде: ❌ ошибка, ⚠️ предупреждение.

        В файл (r7.logfile, Reports/logs/r7-testovarka.log) строка уходит
        сразу, из вызывающего потока: logging потокобезопасен, а ждать
        главного потока нельзя — после сбоя окна он может и не прийти.
        """
        stamp = datetime.now()
        logfile.get_logger().log(logfile.level_for_message(msg), msg)
        if threading.current_thread() is threading.main_thread():
            self._drain_test_log(reschedule=False)
            self._write_test_log(stamp, msg)
        else:
            self._log_queue().put((stamp, msg))

    def _log_queue(self):
        q = self.__dict__.get("_test_log_queue")
        if q is None:
            q = self.__dict__["_test_log_queue"] = queue.SimpleQueue()
        return q

    def _drain_test_log(self, reschedule=True):
        """Выводит накопившиеся сообщения фоновых потоков (главный поток)."""
        q = self._log_queue()
        for _ in range(self.LOG_DRAIN_BATCH):
            try:
                stamp, msg = q.get_nowait()
            except queue.Empty:
                break
            self._write_test_log(stamp, msg)
        if reschedule:
            try:
                self.root.after(self.LOG_DRAIN_MS, self._drain_test_log)
            except Exception:
                pass                       # окно закрыто — журнал больше не нужен

    def _write_test_log(self, stamp, msg):
        try:
            if getattr(self, "_log_hint_shown", False):
                # Первое настоящее сообщение убирает подсказку «как запустить».
                self.test_log.delete("1.0", tk.END)
                self._log_hint_shown = False
            self.test_log.insert(tk.END, f"{stamp:%H:%M:%S}  ", "TIME",
                                 f"{msg}\n", self._log_tag(msg))
            self.test_log.see(tk.END)
        except Exception:
            print(msg)

    # Цвет строки журнала — по первому значку сообщения.
    LOG_TAG_BY_PREFIX = (("❌", "ERROR"), ("⚠", "WARN"), ("✅", "OK"), ("🏁", "OK"),
                         ("📊", "RESULT"), ("⏱", "RESULT"))

    @classmethod
    def _log_tag(cls, msg):
        text = msg.lstrip()
        for prefix, tag in cls.LOG_TAG_BY_PREFIX:
            if text.startswith(prefix):
                return tag
        return "INFO"

    # ---------------------- Ошибки внутри обработчиков Tk ----------------------
    # Исключение в обработчике кнопки, события или after Tk не роняет
    # программу, а печатает traceback в stderr — у pythonw его нет, и сбой
    # пропадал без следа. Теперь он идёт в файловый журнал и показывается
    # окном, но не чаще раза в TK_ERROR_BOX_INTERVAL_SEC: сбой в after-цикле
    # повторяется 20 раз в секунду, и окна сыпались бы без остановки.
    TK_ERROR_BOX_INTERVAL_SEC = 5.0
    TK_ERROR_BOX_TEXT = ("Внутренняя ошибка интерфейса — подробности в "
                         "Reports/logs/r7-testovarka.log")

    def _install_tk_error_handler(self):
        self.root.report_callback_exception = self._on_tk_callback_error

    def _on_tk_callback_error(self, exc_type, exc_value, tb):
        """Замена tk.Tk.report_callback_exception: в журнал — всегда,
        в stderr — как прежде, окно — не чаще раза в 5 с."""
        logfile.get_logger().error("Ошибка в обработчике Tk",
                                   exc_info=(exc_type, exc_value, tb))
        try:
            traceback.print_exception(exc_type, exc_value, tb)
        except Exception:
            pass                               # нет stderr (pythonw) — журнал уже есть
        now = time.monotonic()
        last = self.__dict__.get("_tk_error_box_at")
        if last is not None and now - last < self.TK_ERROR_BOX_INTERVAL_SEC:
            return
        self._tk_error_box_at = now
        try:
            messagebox.showerror("Внутренняя ошибка интерфейса", self.TK_ERROR_BOX_TEXT,
                                 parent=self.root)
        except Exception:
            pass                               # окно уже разрушено — сообщать некому

    def _start_run(self, kind, target, before=None, on_done=None, parent=None):
        """Один цикл для всех фоновых прогонов: захват RunState → подготовка
        окна в главном потоке → поток → finally: освободить состояние и
        вызвать on_done в главном потоке. Любой исход потока (return,
        исключение) освобождает состояние; сбой до запуска потока — тоже.

        Args:
            kind: вид прогона (r7.run_state.PERF/BATCH/CUSTOM/INSTALL).
            target: работа фонового потока.
            before: подготовка в главном потоке после захвата (кнопки, окно).
            on_done: что сделать в главном потоке после потока.
            parent: окно для сообщения об отказе (модальный диалог).

        Returns:
            bool: прогон запущен.
        """
        refusal = self.run_state.try_start(kind)
        if refusal:
            if parent is not None:
                messagebox.showwarning(*refusal, parent=parent)
            else:
                messagebox.showwarning(*refusal)
            return False

        def _thread():
            try:
                target()
            finally:
                self.run_state.finish(kind)
                if on_done is not None:
                    self._ui_call(on_done)
        try:
            if before is not None:
                before()
            threading.Thread(target=_thread, daemon=True).start()
        except Exception:
            self.run_state.finish(kind)
            raise
        return True

    def _set_status(self, text):
        """Строка статуса из любого потока."""
        if threading.current_thread() is threading.main_thread():
            self.status_var.set(text)
        else:
            self._ui_call(lambda: self.status_var.set(text))

    def _ui_call(self, fn):
        """Выполнить fn в главном потоке (виджеты — только оттуда).

        root.after из фонового потока работает, пока идёт mainloop; окно
        закрыто — вызов теряется, но не молча: строка в консоль."""
        try:
            self.root.after(0, fn)
        except Exception as e:
            print(f"⚠️ интерфейс недоступен, действие пропущено: {e}")

    def _set_perf_progress(self, done, total):
        """Updates the Performance tab's progress bar (0-100%). Safe to call
        even if the widget doesn't exist yet or the app is in another mode.
        Marshals the actual Tk update onto the main thread via root.after,
        since this is called from the worker thread during a test run."""
        try:
            pct = 100 * done / total if total else 0
            self.root.after(0, lambda: self.progress_var.set(pct))
        except Exception:  # окно закрыто посреди прогона — шкалы уже нет
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
        except Exception:  # окно закрыто — кнопок уже нет
            pass
        self._set_editor_selector_state(True)
        self._update_fixture_hint()   # прогон мог создать фикстуру документа
        self._update_tests_summary()  # «Запустить» недоступна, если ничего не отмечено

    def _request_stop_perf_test(self):
        """Обработчик кнопки «⏹ Остановить»: просит рабочий поток прерваться
        между операциями. Р7-Офис закрывается штатно, отчёт по уже
        выполненным операциям всё равно сохраняется."""
        self.perf_stop_event.set()
        self.btn_stop_perf.config(state=tk.DISABLED)
        self.add_test_log("⏹ Запрошена остановка теста...")
