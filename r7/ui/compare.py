"""Сравнение версий, тренды и тест своего файла: выбор отчётов, окна с
результатами, открытие HTML-страниц.

Статистика — r7.stats.compare_runs, вид страниц — r7_reports.py.
CompareMixin — методы, которые R7Testovarka получает наследованием.
"""
import json
import re
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import r7_reports
from r7.run_state import CUSTOM
from r7.config import SERIES_COLORS
from r7.ui.base import COLORS


class CompareMixin:
    """Сравнение версий, тренды, тест своего файла — часть R7Testovarka."""

    def compare_versions(self):
        """Opens dialog to select 2-10 performance JSON files and builds a comparison report."""
        # Не больше цветов палитры: девятая версия на графике получила бы
        # повтор цвета и слилась бы с первой.
        MAX_FILES = len(SERIES_COLORS)
        CHART_COLORS = list(SERIES_COLORS)

        settings = self._load_comparison_settings()
        custom_names = settings.get("custom_names", {})
        last_selected = set(settings.get("last_selected_files", []))
        last_base = settings.get("last_base_version", "")

        def scan_files():
            json_files = sorted(
                self.reports_folder.glob("performance_full_*.json"),
                key=lambda fp: fp.stat().st_mtime, reverse=True
            )
            result = []
            for jf in json_files:
                key = str(jf)
                try:
                    with open(jf, encoding="utf-8") as fh:
                        jdata = json.load(fh)
                    version = jdata.get("version") or jf.stem
                    ts_raw = jdata.get("timestamp", "")
                    ts_disp = (f"{ts_raw[6:8]}.{ts_raw[4:6]}.{ts_raw[:4]} "
                               f"{ts_raw[9:11]}:{ts_raw[11:13]}"
                               if len(ts_raw) >= 13 else ts_raw)
                except Exception:
                    jdata = None
                    version = jf.stem
                    ts_disp = ""
                result.append({
                    "path": jf, "key": key, "version": version,
                    "ts": ts_disp, "data": jdata,
                    "display_name": custom_names.get(key, version),
                })
            return result

        initial_meta = scan_files()
        if len(initial_meta) < 2:
            self.add_test_log(
                f"⚠️ Сравнение версий: найдено {len(initial_meta)} файлов "
                f"performance_full_*.json (нужно минимум 2)")
            messagebox.showwarning(
                "Недостаточно данных",
                "Для сравнения нужно минимум 2 файла performance_full_*.json.\n"
                "Запустите тесты для нескольких версий R7-Office."
            )
            return

        # Mutable state shared by all closures
        file_meta_by_key = {}   # key -> meta dict
        sel_vars = {}           # key -> BooleanVar
        combo_keys_ref = []     # ordered list of keys matching combo values

        # ── Dialog ──────────────────────────────────────────────────────────
        try:
            dlg = tk.Toplevel(self.root)
            dlg.transient(self.root)
            dlg.configure(bg=COLORS["bg"])
            dlg.title("Сравнение версий")
            dlg.resizable(True, True)
            dlg.minsize(580, 400)
            dlg.grab_set()

            ttk.Label(dlg, text="Выберите 2–10 файлов для сравнения:",
                      font=("Arial", 10, "bold")).pack(pady=(12, 4), padx=14, anchor=tk.W)

            # ── Scrollable list ──────────────────────────────────────────────────
            list_outer = ttk.LabelFrame(dlg, text="Доступные результаты", padding="4")
            list_outer.pack(fill=tk.BOTH, expand=True, padx=14, pady=4)

            list_canvas = tk.Canvas(list_outer, highlightthickness=0)
            vsb = ttk.Scrollbar(list_outer, orient=tk.VERTICAL, command=list_canvas.yview)
            list_canvas.configure(yscrollcommand=vsb.set)
            vsb.pack(side=tk.RIGHT, fill=tk.Y)
            list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            inner = ttk.Frame(list_canvas)
            inner_id = list_canvas.create_window((0, 0), window=inner, anchor="nw")

            def _on_inner_cfg(e):
                list_canvas.configure(scrollregion=list_canvas.bbox("all"))
            inner.bind("<Configure>", _on_inner_cfg)

            def _on_canvas_cfg(e):
                list_canvas.itemconfig(inner_id, width=e.width)
            list_canvas.bind("<Configure>", _on_canvas_cfg)

            def _on_mwheel(e):
                list_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            list_canvas.bind_all("<MouseWheel>", _on_mwheel)

            # ── Row builder ─────────────────────────────────────────────────────
            def build_row(meta, idx):
                key = meta["key"]
                var = tk.BooleanVar(value=(key in last_selected))
                sel_vars[key] = var
                color = CHART_COLORS[idx % len(CHART_COLORS)]

                rf = ttk.Frame(inner)
                rf.pack(fill=tk.X, pady=1, padx=2)

                ttk.Checkbutton(rf, variable=var).pack(side=tk.LEFT)

                dot = tk.Canvas(rf, width=14, height=14, highlightthickness=0,
                                bg=dlg.cget("bg"))
                dot.create_oval(2, 2, 12, 12, fill=color, outline="")
                dot.pack(side=tk.LEFT, padx=(2, 4))

                ts_val = meta.get("ts", "")
                name_txt = meta.get("display_name", meta["version"])
                lbl_txt = f"{name_txt}  •  {ts_val}" if ts_val else name_txt
                lbl = ttk.Label(rf, text=lbl_txt, anchor=tk.W)
                # Упаковывается ПОСЛЕ кнопок (ниже): упаковщик раздаёт место по
                # порядку, и длинное имя раньше вытесняло кнопки за край строки.

                def make_rename(m, lb):
                    def do_rename():
                        new_name = simpledialog.askstring(
                            "Переименовать", "Новое название:",
                            initialvalue=m.get("display_name", m["version"]),
                            parent=dlg
                        )
                        if new_name and new_name.strip():
                            m["display_name"] = new_name.strip()
                            custom_names[m["key"]] = new_name.strip()
                            ts = m.get("ts", "")
                            lb.config(text=f"{new_name.strip()}  •  {ts}" if ts
                                      else new_name.strip())
                            refresh_base_combo()
                    return do_rename

                def make_delete(m, row_frame):
                    def do_delete():
                        file_meta_by_key.pop(m["key"], None)
                        sel_vars.pop(m["key"], None)
                        custom_names.pop(m["key"], None)
                        row_frame.destroy()
                        refresh_base_combo()
                    return do_delete

                btn_ren = ttk.Button(rf, text="✏️", width=3,
                                     command=make_rename(meta, lbl))
                btn_ren.pack(side=tk.RIGHT, padx=1)
                btn_del = ttk.Button(rf, text="🗑️", width=3,
                                     command=make_delete(meta, rf))
                btn_del.pack(side=tk.RIGHT, padx=1)
                lbl.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)

                ctx = tk.Menu(dlg, tearoff=0)

                def make_ctx_handler(m, lb, row_frame):
                    def show(e):
                        try:
                            ctx.delete(0, tk.END)
                            ctx.add_command(label="✏️ Переименовать",
                                            command=make_rename(m, lb))
                            ctx.add_command(label="🗑️ Удалить из списка",
                                            command=make_delete(m, row_frame))
                            ctx.add_separator()
                            ctx.add_command(label="📌 Сделать базовой",
                                            command=lambda: _set_base_by_key(m["key"]))
                            ctx.tk_popup(e.x_root, e.y_root)
                        finally:
                            ctx.grab_release()
                    return show

                show_ctx = make_ctx_handler(meta, lbl, rf)
                rf.bind("<Button-3>", show_ctx)
                lbl.bind("<Button-3>", show_ctx)

            # ── Populate initial rows ────────────────────────────────────────────
            for i, m in enumerate(initial_meta[:MAX_FILES]):
                file_meta_by_key[m["key"]] = m
                build_row(m, i)

            # ── Toolbar ─────────────────────────────────────────────────────────
            toolbar = ttk.Frame(dlg)
            toolbar.pack(fill=tk.X, padx=14, pady=(4, 0))

            def add_file():
                if len(file_meta_by_key) >= MAX_FILES:
                    messagebox.showwarning("Лимит",
                                           f"Максимум {MAX_FILES} файлов.", parent=dlg)
                    return
                path_str = filedialog.askopenfilename(
                    parent=dlg,
                    title="Выбрать JSON-файл результатов",
                    filetypes=[("JSON файлы", "*.json"), ("Все файлы", "*.*")],
                    initialdir=str(self.reports_folder)
                )
                if not path_str:
                    return
                from pathlib import Path as _Path
                jf = _Path(path_str)
                key = str(jf)
                if key in file_meta_by_key:
                    messagebox.showinfo("Уже добавлен",
                                        "Этот файл уже есть в списке.", parent=dlg)
                    return
                try:
                    with open(jf, encoding="utf-8") as fh:
                        jdata = json.load(fh)
                    version = jdata.get("version") or jf.stem
                    ts_raw = jdata.get("timestamp", "")
                    ts_disp = (f"{ts_raw[6:8]}.{ts_raw[4:6]}.{ts_raw[:4]} "
                               f"{ts_raw[9:11]}:{ts_raw[11:13]}"
                               if len(ts_raw) >= 13 else ts_raw)
                except Exception as ex:
                    messagebox.showerror("Ошибка",
                                         f"Не удалось прочитать файл:\n{ex}", parent=dlg)
                    return
                meta = {
                    "path": jf, "key": key, "version": version,
                    "ts": ts_disp, "data": jdata,
                    "display_name": custom_names.get(key, version),
                }
                idx = len(file_meta_by_key)
                file_meta_by_key[key] = meta
                build_row(meta, idx)
                refresh_base_combo()

            def refresh_list():
                new_meta = scan_files()
                added = 0
                for m in new_meta:
                    if m["key"] not in file_meta_by_key:
                        if len(file_meta_by_key) >= MAX_FILES:
                            break
                        idx = len(file_meta_by_key)
                        file_meta_by_key[m["key"]] = m
                        build_row(m, idx)
                        added += 1
                if added:
                    refresh_base_combo()
                    messagebox.showinfo("Обновлено",
                                        f"Добавлено новых файлов: {added}", parent=dlg)
                else:
                    messagebox.showinfo("Нет изменений",
                                        "Новых файлов не найдено.", parent=dlg)

            ttk.Button(toolbar, text="➕ Добавить файл",
                       command=add_file).pack(side=tk.LEFT, padx=(0, 6))
            ttk.Button(toolbar, text="🔄 Обновить список",
                       command=refresh_list).pack(side=tk.LEFT)

            ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=14, pady=8)

            # ── Base version selector ────────────────────────────────────────────
            base_frame = ttk.LabelFrame(dlg, text="Базовая версия (для расчёта Δ%)", padding="6")
            base_frame.pack(fill=tk.X, padx=14, pady=4)

            base_var = tk.StringVar()
            base_combo = ttk.Combobox(base_frame, textvariable=base_var,
                                      state="readonly", width=60)
            base_combo.pack(fill=tk.X, padx=4, pady=2)

            def refresh_base_combo():
                prev_key = (combo_keys_ref[base_combo.current()]
                            if combo_keys_ref and 0 <= base_combo.current() < len(combo_keys_ref)
                            else "")
                keys = list(file_meta_by_key.keys())
                combo_keys_ref.clear()
                combo_keys_ref.extend(keys)
                values = []
                for k in keys:
                    m = file_meta_by_key[k]
                    nm = m.get("display_name", m["version"])
                    ts = m.get("ts", "")
                    values.append(f"{nm}  •  {ts}" if ts else nm)
                base_combo["values"] = values
                if prev_key and prev_key in keys:
                    base_combo.current(keys.index(prev_key))
                elif last_base and last_base in keys:
                    base_combo.current(keys.index(last_base))
                elif keys:
                    base_combo.current(0)

            def _set_base_by_key(key):
                if key in combo_keys_ref:
                    base_combo.current(combo_keys_ref.index(key))

            refresh_base_combo()

            # ── Action buttons ───────────────────────────────────────────────────
            btn_frame = ttk.Frame(dlg)
            btn_frame.pack(pady=10, padx=14, fill=tk.X)

            def _cleanup():
                list_canvas.unbind_all("<MouseWheel>")
                dlg.destroy()

            def do_compare():
                selected_keys = [k for k, v in sel_vars.items() if v.get()]
                if len(selected_keys) < 2:
                    messagebox.showwarning("Мало файлов",
                                           "Выберите минимум 2 файла.", parent=dlg)
                    return
                if len(selected_keys) > MAX_FILES:
                    messagebox.showwarning("Много файлов",
                                           f"Выберите не более {MAX_FILES} файлов.", parent=dlg)
                    return
                cidx = base_combo.current()
                if cidx < 0 or cidx >= len(combo_keys_ref):
                    messagebox.showwarning("Базовая версия",
                                           "Выберите базовую версию.", parent=dlg)
                    return
                base_key = combo_keys_ref[cidx]
                if base_key not in selected_keys:
                    messagebox.showwarning(
                        "Базовая версия",
                        "Базовая версия должна быть среди выбранных файлов.", parent=dlg)
                    return

                datasets = []
                for k in selected_keys:
                    m = file_meta_by_key[k]
                    jdata = m.get("data")
                    if jdata is None:
                        try:
                            with open(m["path"], encoding="utf-8") as fh:
                                jdata = json.load(fh)
                        except Exception as ex:
                            messagebox.showerror(
                                "Ошибка",
                                f"Не удалось загрузить {m['path'].name}:\n{ex}",
                                parent=dlg)
                            return
                    datasets.append({
                        "path": str(m["path"]),
                        "version": m.get("display_name", m["version"]),
                        "data": jdata,
                    })

                self._save_comparison_settings({
                    "custom_names": custom_names,
                    "last_selected_files": selected_keys,
                    "last_base_version": base_key,
                })
                _cleanup()
                html = self._generate_comparison_html(
                    datasets, str(file_meta_by_key[base_key]["path"]))
                ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_path = self.reports_folder / f"comparison_{ts_now}.html"
                try:
                    out_path.write_text(html, encoding="utf-8")
                    self.add_test_log(f"📊 Отчёт сравнения сохранён: {out_path.name}")
                    webbrowser.open(str(out_path))
                except Exception as ex:
                    messagebox.showerror("Ошибка", f"Не удалось сохранить отчёт:\n{ex}")

            ttk.Button(btn_frame, text="📊 Сравнить",
                       command=do_compare).pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_frame, text="Отмена",
                       command=_cleanup).pack(side=tk.LEFT)

            dlg.protocol("WM_DELETE_WINDOW", _cleanup)

            dlg.update_idletasks()
            row_h = max(len(file_meta_by_key) * 34 + 20, 80)
            list_canvas.configure(height=min(row_h, 220))
            w = max(680, dlg.winfo_reqwidth())
            h = min(700, max(440, dlg.winfo_reqheight()))
            self._center_dialog(dlg, w, h)
        except Exception as ex:
            self.add_test_log(f"❌ Ошибка при построении окна сравнения версий: {ex}")
            try:
                list_canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass
            try:
                dlg.destroy()
            except Exception:
                pass
            messagebox.showerror("Ошибка", f"Не удалось открыть окно сравнения версий:\n{ex}")

    def show_trends(self):
        """Строит и открывает в браузере страницу трендов по всем
        накопленным performance_full_*.json. Точка входа из UI (кнопка
        «📈 Тренды» рядом с «Сравнить версии»)."""
        runs = self._load_trends_runs()
        if len(runs) < 2:
            messagebox.showinfo(
                "Недостаточно данных",
                f"Найдено {len(runs)} файлов performance_full_*.json "
                f"(нужно минимум 2 для тренда).\nЗапустите тесты несколько раз.")
            return
        html_content = self._generate_trends_html(runs)
        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.reports_folder / f"trends_{ts_now}.html"
        try:
            out_path.write_text(html_content, encoding="utf-8")
            self.add_test_log(f"📈 Страница трендов: {out_path.name}")
            webbrowser.open(str(out_path))
        except Exception as e:
            self.add_test_log(f"⚠️ Ошибка сохранения страницы трендов: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить страницу трендов:\n{e}")

    def compare_file_sizes(self):
        """Opens the test-file generation dialog with 4 separate action buttons."""
        last = self._load_last_params()

        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.configure(bg=COLORS["bg"])
        dlg.title("Генерация тестового файла")
        dlg.resizable(False, False)
        dlg.grab_set()

        PAD = {"padx": 16, "pady": 5}

        # ── Строки ──────────────────────────────────────────────────────────
        ttk.Label(dlg, text="Количество строк:").grid(
            row=0, column=0, sticky=tk.W, **PAD)
        rows_var = tk.StringVar(value=str(last.get("rows", 50000)))
        rows_entry = ttk.Entry(dlg, textvariable=rows_var, width=14)
        rows_entry.grid(row=0, column=1, sticky=tk.W, **PAD)
        ttk.Label(dlg, text="(1 000 – 1 000 000)", foreground=COLORS["text_secondary"]).grid(
            row=0, column=2, sticky=tk.W, padx=(0, 16))

        # ── Столбцы ─────────────────────────────────────────────────────────
        ttk.Label(dlg, text="Количество столбцов:").grid(
            row=1, column=0, sticky=tk.W, **PAD)
        cols_var = tk.StringVar(value=str(last.get("cols", 50)))
        cols_entry = ttk.Entry(dlg, textvariable=cols_var, width=14)
        cols_entry.grid(row=1, column=1, sticky=tk.W, **PAD)
        ttk.Label(dlg, text="(1 – 100)", foreground=COLORS["text_secondary"]).grid(
            row=1, column=2, sticky=tk.W, padx=(0, 16))

        ttk.Separator(dlg, orient=tk.HORIZONTAL).grid(
            row=2, column=0, columnspan=3, sticky=tk.EW, padx=16, pady=8)

        # ── Перезаписать ─────────────────────────────────────────────────────
        overwrite_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(dlg, text="Перезаписать если существует",
                        variable=overwrite_var).grid(
            row=3, column=0, columnspan=3, sticky=tk.W, padx=16, pady=2)

        # ── Имя файла ────────────────────────────────────────────────────────
        ttk.Label(dlg, text="Имя файла:").grid(row=4, column=0, sticky=tk.W, **PAD)
        filename_var = tk.StringVar(value=last.get("filename", "test_data_50000x50.xlsx"))
        filename_entry = ttk.Entry(dlg, textvariable=filename_var, width=36)
        filename_entry.grid(row=4, column=1, columnspan=2, sticky=tk.EW,
                            padx=(0, 16), pady=5)

        # Авто-имя при смене размеров; сбрасывается при ручном редактировании
        _auto_name = [True]
        _ext_path  = [None]   # полный путь из filedialog

        def _on_dim_change(*_):
            if _auto_name[0]:
                try:
                    filename_var.set(
                        f"test_data_{int(rows_var.get())}x{int(cols_var.get())}.xlsx")
                    _ext_path[0] = None
                except ValueError:
                    pass

        def _on_filename_edit(*_):
            try:
                expected = (
                    f"test_data_{int(rows_var.get())}x{int(cols_var.get())}.xlsx")
            except ValueError:
                expected = ""
            _auto_name[0] = (filename_var.get() == expected)
            _ext_path[0]  = None

        rows_var.trace_add("write", _on_dim_change)
        cols_var.trace_add("write", _on_dim_change)
        filename_var.trace_add("write", _on_filename_edit)

        ttk.Separator(dlg, orient=tk.HORIZONTAL).grid(
            row=5, column=0, columnspan=3, sticky=tk.EW, padx=16, pady=8)

        # ── Кнопки 2×2 ───────────────────────────────────────────────────────
        bf = ttk.Frame(dlg)
        bf.grid(row=6, column=0, columnspan=3, sticky=tk.EW, padx=16)
        bf.columnconfigure(0, weight=1)
        bf.columnconfigure(1, weight=1)

        btn_create = ttk.Button(bf, text="1. Создать файл")
        btn_choose = ttk.Button(bf, text="2. Выбрать файл")
        btn_test   = ttk.Button(bf, text="3. Протестировать")
        btn_cancel = ttk.Button(bf, text="4. Отмена", command=dlg.destroy)

        btn_create.grid(row=0, column=0, sticky=tk.EW, padx=(0, 3), pady=(0, 5))
        btn_choose.grid(row=0, column=1, sticky=tk.EW, padx=(3, 0), pady=(0, 5))
        btn_test  .grid(row=1, column=0, sticky=tk.EW, padx=(0, 3))
        btn_cancel.grid(row=1, column=1, sticky=tk.EW, padx=(3, 0))

        # ── Статус ───────────────────────────────────────────────────────────
        status_var = tk.StringVar(value="Статус: Готов")
        status_lbl = ttk.Label(dlg, textvariable=status_var, anchor=tk.W,
                               foreground=COLORS["text_secondary"])
        status_lbl.grid(row=7, column=0, columnspan=3, sticky=tk.EW,
                        padx=16, pady=(10, 14))

        # ── Вспомогательные функции ──────────────────────────────────────────
        _action_btns = [btn_create, btn_test]

        def _set_status(text, color=COLORS["text_secondary"]):
            def _do():
                try:
                    status_var.set(f"Статус: {text}")
                    status_lbl.config(foreground=color)
                except tk.TclError:
                    pass
            try:
                dlg.after(0, _do)
            except tk.TclError:
                pass

        def _lock():
            def _do():
                try:
                    for b in _action_btns:
                        b.config(state="disabled")
                except tk.TclError:
                    pass
            try:
                dlg.after(0, _do)
            except tk.TclError:
                pass

        def _unlock():
            def _do():
                try:
                    for b in _action_btns:
                        b.config(state="normal")
                except tk.TclError:
                    pass
            try:
                dlg.after(0, _do)
            except tk.TclError:
                pass

        def _validate_dims():
            try:
                r = int(rows_var.get())
                assert 1_000 <= r <= 1_000_000
            except (ValueError, AssertionError):
                messagebox.showwarning(
                    "Ошибка", "Строки: от 1 000 до 1 000 000.", parent=dlg)
                rows_entry.focus_set()
                return None, None
            try:
                c = int(cols_var.get())
                assert 1 <= c <= 100
            except (ValueError, AssertionError):
                messagebox.showwarning(
                    "Ошибка", "Столбцы: от 1 до 100.", parent=dlg)
                cols_entry.focus_set()
                return None, None
            return r, c

        def _resolve_path():
            if _ext_path[0]:
                return Path(_ext_path[0])
            fname = filename_var.get().strip()
            if not fname:
                return None
            if not fname.endswith(".xlsx"):
                fname += ".xlsx"
            return self.test_files_folder / fname

        # ── Кнопка 1: только создать файл ────────────────────────────────────
        def on_create():
            r, c = _validate_dims()
            if r is None:
                return
            if _ext_path[0]:
                messagebox.showwarning(
                    "Внимание",
                    "Файл выбран через диалог — кнопка «Создать файл» работает\n"
                    "только с именем в поле «Имя файла».\n"
                    "Введите имя файла вручную или очистите поле.",
                    parent=dlg)
                return
            fname = filename_var.get().strip()
            if not fname or not re.fullmatch(r"[A-Za-z0-9_.]+", fname):
                messagebox.showwarning(
                    "Ошибка",
                    "Имя файла: только латиница, цифры, '_' и '.'.",
                    parent=dlg)
                filename_entry.focus_set()
                return
            if not fname.endswith(".xlsx"):
                fname += ".xlsx"
                filename_var.set(fname)
            file_path = self.test_files_folder / fname
            if file_path.exists() and not overwrite_var.get():
                _set_status(f"⚠️ Файл уже существует: {fname}", "#e67e22")
                self.add_test_log(f"⚠️ Файл уже существует: {file_path}")
                return
            self._save_last_params(r, c, fname)
            _lock()
            _set_status("⏳ Создание файла...", "#2980b9")

            def _worker():
                try:
                    self._generate_custom_test_file(r, c, file_path)
                    self.add_test_log(
                        f"📊 Создан тестовый файл: {fname} ({r} строк, {c} столбцов)")
                    _set_status(f"✅ Файл создан: {fname}", "#27ae60")
                except Exception as e:
                    self.add_test_log(f"❌ Ошибка создания файла: {e}")
                    _set_status(f"❌ Ошибка: {e}", "#e74c3c")
                finally:
                    _unlock()

            threading.Thread(target=_worker, daemon=True).start()

        # ── Кнопка 2: выбрать любой xlsx ─────────────────────────────────────
        def on_choose():
            path = filedialog.askopenfilename(
                parent=dlg,
                title="Выбрать xlsx-файл для тестирования",
                filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")]
            )
            if path:
                _ext_path[0]  = path
                _auto_name[0] = False
                filename_var.set(path)
                self.add_test_log(f"📁 Выбран файл: {path}")
                _set_status(f"📁 Выбран файл: {Path(path).name}", "#2980b9")

        # ── Кнопка 3: только тестирование ────────────────────────────────────
        def on_test():
            file_path = _resolve_path()
            if not file_path:
                _set_status("❌ Укажите имя или путь к файлу", "#e74c3c")
                return
            if not file_path.exists():
                msg = f"❌ Файл не найден: {file_path.name}"
                _set_status(msg, "#e74c3c")
                self.add_test_log(msg)
                return
            try:
                r, c = int(rows_var.get()), int(cols_var.get())
            except ValueError:
                r, c = 0, 0
            self._save_last_params(r, c, file_path.name)
            def _prepare_ui():
                _lock()
                _set_status("⏳ Тестирование...", "#2980b9")

            def _done(success):
                _set_status(
                    "✅ Тест завершён" if success else "❌ Тест завершён с ошибкой",
                    "#27ae60" if success else "#e74c3c")
                _unlock()

            # Отказ, если идёт прогон вкладки или Batch: клавиши заняты.
            self._start_run(CUSTOM, lambda: self._worker_run_test(file_path, r, c, _done),
                            before=_prepare_ui, parent=dlg)

        btn_create.config(command=on_create)
        btn_choose.config(command=on_choose)
        btn_test  .config(command=on_test)

        dlg.columnconfigure(1, weight=1)
        rows_entry.focus_set()
        dlg.wait_window()

    def _show_custom_test_report(self, result):
        """Отчёт по своему файлу (templates/reports/custom.html): строит,
        сохраняет и открывает в браузере."""
        html_content = r7_reports.render("custom.html", **r7_reports.custom_model(result))
        ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.reports_folder / f"custom_test_{ts_file}.html"
        try:
            out_path.write_text(html_content, encoding="utf-8")
            self.add_test_log(f"📊 Отчёт готов: {out_path.name}")
            webbrowser.open(str(out_path))
        except Exception as e:
            self.add_test_log(f"⚠️ Ошибка записи отчёта: {e}")
