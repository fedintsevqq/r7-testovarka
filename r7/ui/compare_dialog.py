"""Окно «Сравнение версий»: список отчётов, база для Δ%, построение страницы.

Прежде это был один метод compare_versions на 313 строк с замыканиями над
общими словарями. Здесь — класс CompareDialog: состояние окна — атрибуты,
строка списка, панель кнопок, выбор базы, переименование и удаление записи
— методы. Чтение отчётов и проверки — r7.compare_files, страница —
r7_reports (через _generate_comparison_html приложения). «Пакет улик» —
r7.evidence: zip для тикета из двух отмеченных отчётов.
"""
import os
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from r7 import evidence
from r7.compare_files import build_datasets, read_report_meta, scan_reports, validate_comparison
from r7.config import SERIES_COLORS
from r7.ui.base import COLORS

# Не больше цветов палитры: девятая версия на графике получила бы
# повтор цвета и слилась бы с первой.
MAX_FILES = len(SERIES_COLORS)
CHART_COLORS = list(SERIES_COLORS)


def row_label(meta):
    """Подпись строки и пункта базы: имя (или версия) и метка времени."""
    name = meta.get("display_name", meta["version"])
    ts = meta.get("ts", "")
    return f"{name}  •  {ts}" if ts else name


class CompareDialog:
    """Окно выбора 2–10 отчётов. app — R7Testovarka (корень, папка
    отчётов, настройки сравнения, журнал и генерация страницы)."""

    def __init__(self, app, initial_meta, settings):
        self.app = app
        self.custom_names = settings.get("custom_names", {})
        self.last_selected = set(settings.get("last_selected_files", []))
        self.last_base = settings.get("last_base_version", "")
        self.file_meta_by_key = {}   # key -> meta dict
        self.sel_vars = {}           # key -> BooleanVar
        self.combo_keys = []         # ключи в порядке значений списка базы

        dlg = self.dlg = tk.Toplevel(app.root)
        dlg.transient(app.root)
        dlg.configure(bg=COLORS["bg"])
        dlg.title("Сравнение версий")
        dlg.resizable(True, True)
        dlg.minsize(580, 400)
        try:
            self._build(initial_meta)
        except Exception:
            try:
                dlg.destroy()
            except Exception:  # окно уже закрыто — ошибку покажет вызывающий
                pass
            raise

    def _build(self, initial_meta):
        dlg = self.dlg
        dlg.grab_set()
        ttk.Label(dlg, text=f"Выберите от 2 до {MAX_FILES} прогонов для сравнения",
                  style="Title.TLabel").pack(pady=(12, 4), padx=14, anchor=tk.W)

        self._build_list()
        for i, m in enumerate(initial_meta[:MAX_FILES]):
            self.file_meta_by_key[m["key"]] = m
            self._build_row(m, i)
        self._build_toolbar()
        ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=14, pady=8)
        self._build_base_selector()
        self.refresh_base_combo()
        self._build_actions()
        dlg.protocol("WM_DELETE_WINDOW", self.close)

        dlg.update_idletasks()
        row_h = max(len(self.file_meta_by_key) * 34 + 20, 80)
        self.list_canvas.configure(height=min(row_h, 220))
        w = max(680, dlg.winfo_reqwidth())
        h = min(700, max(440, dlg.winfo_reqheight()))
        self.app._center_dialog(dlg, w, h)

    # ── список ────────────────────────────────────────────────────────────
    def _build_list(self):
        list_outer = ttk.LabelFrame(self.dlg, text="Доступные результаты", padding="4")
        list_outer.pack(fill=tk.BOTH, expand=True, padx=14, pady=4)

        canvas = self.list_canvas = tk.Canvas(list_outer, highlightthickness=0)
        vsb = ttk.Scrollbar(list_outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = self.inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(inner_id, width=e.width))
        # Колесо — на холсте и на каждой строке (_bind_wheel), не bind_all:
        # unbind_all при закрытии окна отключал прокрутку всему приложению.
        self.app._bind_wheel(canvas, canvas, inner)

    def _build_row(self, meta, idx):
        key = meta["key"]
        var = tk.BooleanVar(value=(key in self.last_selected))
        self.sel_vars[key] = var
        # «Пакет улик» доступен только при двух отмеченных отчётах.
        var.trace_add("write", lambda *_: self.refresh_evidence_button())
        color = CHART_COLORS[idx % len(CHART_COLORS)]

        rf = ttk.Frame(self.inner)
        rf.pack(fill=tk.X, pady=1, padx=2)
        ttk.Checkbutton(rf, variable=var).pack(side=tk.LEFT)

        dot = tk.Canvas(rf, width=14, height=14, highlightthickness=0, bg=self.dlg.cget("bg"))
        dot.create_oval(2, 2, 12, 12, fill=color, outline="")
        dot.pack(side=tk.LEFT, padx=(2, 4))

        lbl = ttk.Label(rf, text=row_label(meta), anchor=tk.W)
        # Упаковывается ПОСЛЕ кнопок (ниже): упаковщик раздаёт место по
        # порядку, и длинное имя раньше вытесняло кнопки за край строки.
        self.app._icon_button(rf, "", "edit", style="Small.TButton", tooltip="Переименовать",
                              command=lambda: self.rename(meta, lbl)).pack(side=tk.RIGHT, padx=1)
        self.app._icon_button(rf, "", "clear", style="Small.TButton", tooltip="Убрать из списка",
                              command=lambda: self.delete(meta, rf)).pack(side=tk.RIGHT, padx=1)
        lbl.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)

        ctx = tk.Menu(self.dlg, tearoff=0)

        def show_ctx(e):
            try:
                ctx.delete(0, tk.END)
                ctx.add_command(label="✏️ Переименовать", command=lambda: self.rename(meta, lbl))
                ctx.add_command(label="🗑️ Удалить из списка", command=lambda: self.delete(meta, rf))
                ctx.add_separator()
                ctx.add_command(label="📌 Сделать базовой", command=lambda: self.set_base(key))
                ctx.tk_popup(e.x_root, e.y_root)
            finally:
                ctx.grab_release()
        rf.bind("<Button-3>", show_ctx)
        lbl.bind("<Button-3>", show_ctx)
        self.app._bind_wheel(rf, self.list_canvas, self.inner)

    def rename(self, meta, label):
        new_name = simpledialog.askstring(
            "Переименовать", "Новое название:",
            initialvalue=meta.get("display_name", meta["version"]), parent=self.dlg)
        if new_name and new_name.strip():
            meta["display_name"] = new_name.strip()
            self.custom_names[meta["key"]] = new_name.strip()
            label.config(text=row_label(meta))
            self.refresh_base_combo()

    def delete(self, meta, row_frame):
        self.file_meta_by_key.pop(meta["key"], None)
        self.sel_vars.pop(meta["key"], None)
        self.custom_names.pop(meta["key"], None)
        row_frame.destroy()
        self.refresh_base_combo()
        self.refresh_evidence_button()

    # ── панель: добавить файл, обновить список ────────────────────────────
    def _build_toolbar(self):
        toolbar = ttk.Frame(self.dlg)
        toolbar.pack(fill=tk.X, padx=14, pady=(4, 0))
        self.app._icon_button(toolbar, "Добавить файл", "add",
                              command=self.add_file).pack(side=tk.LEFT, padx=(0, 6))
        self.app._icon_button(toolbar, "Обновить список", "refresh",
                              command=self.refresh_list).pack(side=tk.LEFT)

    def _append(self, meta):
        idx = len(self.file_meta_by_key)
        self.file_meta_by_key[meta["key"]] = meta
        self._build_row(meta, idx)

    def add_file(self):
        if len(self.file_meta_by_key) >= MAX_FILES:
            messagebox.showwarning("Лимит", f"Максимум {MAX_FILES} файлов.", parent=self.dlg)
            return
        path_str = filedialog.askopenfilename(
            parent=self.dlg,
            title="Выбрать JSON-файл результатов",
            filetypes=[("JSON файлы", "*.json"), ("Все файлы", "*.*")],
            initialdir=str(self.app.reports_folder))
        if not path_str:
            return
        if str(Path(path_str)) in self.file_meta_by_key:
            messagebox.showinfo("Уже добавлен", "Этот файл уже есть в списке.", parent=self.dlg)
            return
        try:
            meta = read_report_meta(path_str, self.custom_names)
        except Exception as ex:
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл:\n{ex}", parent=self.dlg)
            return
        self._append(meta)
        self.refresh_base_combo()

    def refresh_list(self):
        added = 0
        for m in scan_reports(self.app.reports_folder, self.custom_names):
            if m["key"] not in self.file_meta_by_key:
                if len(self.file_meta_by_key) >= MAX_FILES:
                    break
                self._append(m)
                added += 1
        if added:
            self.refresh_base_combo()
            messagebox.showinfo("Обновлено", f"Добавлено новых файлов: {added}", parent=self.dlg)
        else:
            messagebox.showinfo("Нет изменений", "Новых файлов не найдено.", parent=self.dlg)

    # ── база для Δ% ───────────────────────────────────────────────────────
    def _build_base_selector(self):
        base_frame = ttk.LabelFrame(self.dlg, text="Базовая версия (для расчёта Δ%)", padding="6")
        base_frame.pack(fill=tk.X, padx=14, pady=4)
        base_var = tk.StringVar()
        self.base_combo = ttk.Combobox(base_frame, textvariable=base_var,
                                       state="readonly", width=60)
        # Ссылка на переменную — у виджета: compare_versions возвращается
        # сразу, и локальная StringVar собиралась сборщиком мусора, а Tk
        # очищал поле. Базовая версия, сохранённая с прошлого раза, не
        # показывалась, и «Сравнить» отвечал «Выберите базовую версию».
        self.base_combo.textvar_ref = base_var
        self.base_combo.pack(fill=tk.X, padx=4, pady=2)

    def _current_base_key(self):
        cidx = self.base_combo.current()
        return self.combo_keys[cidx] if 0 <= cidx < len(self.combo_keys) else None

    def refresh_base_combo(self):
        prev_key = self._current_base_key() or ""
        keys = list(self.file_meta_by_key.keys())
        self.combo_keys[:] = keys
        self.base_combo["values"] = [row_label(self.file_meta_by_key[k]) for k in keys]
        if prev_key and prev_key in keys:
            self.base_combo.current(keys.index(prev_key))
        elif self.last_base and self.last_base in keys:
            self.base_combo.current(keys.index(self.last_base))
        elif keys:
            self.base_combo.current(0)

    def set_base(self, key):
        if key in self.combo_keys:
            self.base_combo.current(self.combo_keys.index(key))

    # ── сравнить / отмена ─────────────────────────────────────────────────
    def _build_actions(self):
        btn_frame = ttk.Frame(self.dlg)
        btn_frame.pack(pady=10, padx=14, fill=tk.X)
        self.app._icon_button(btn_frame, "Сравнить", "compare", style="Accent.TButton",
                              command=self.compare).pack(side=tk.LEFT, padx=(0, 6))
        self.btn_evidence = self.app._icon_button(
            btn_frame, "Пакет улик", "save", command=self.build_evidence,
            tooltip=("Zip для тикета: оба JSON, страница сравнения, окружение, хвост "
                     "журнала и готовый текст тикета. Нужны ровно два отмеченных отчёта."))
        self.btn_evidence.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Отмена", command=self.close).pack(side=tk.LEFT)
        self.refresh_evidence_button()

    def _selected_keys(self):
        return [k for k, v in self.sel_vars.items() if v.get()]

    def refresh_evidence_button(self):
        """«Пакет улик» — только при двух отмеченных отчётах и не во время сборки."""
        btn = getattr(self, "btn_evidence", None)
        if btn is None:
            return                                  # панель кнопок ещё не собрана
        enabled = len(self._selected_keys()) == 2 and not getattr(self, "_evidence_busy", False)
        try:
            btn.config(state=tk.NORMAL if enabled else tk.DISABLED)
        except tk.TclError:                         # окно уже закрыто
            pass

    def close(self):
        self.dlg.destroy()

    # ── пакет улик ───────────────────────────────────────────────────────
    def build_evidence(self):
        """Собирает evidence_<время>.zip из двух отмеченных отчётов в
        Reports/evidence (r7.evidence). База — выбранная в списке базы, если
        она среди отмеченных, иначе первый отмеченный. Сборка идёт в фоновом
        потоке (чтение двух JSON по мегабайтам и рендер страницы); это не
        прогон Р7, поэтому RunState не захватывается."""
        app = self.app
        keys = self._selected_keys()
        if len(keys) != 2:
            messagebox.showwarning("Пакет улик", "Отметьте ровно два отчёта: базу и проверяемую сборку.",
                                   parent=self.dlg)
            return
        base_key = self._current_base_key()
        if base_key not in keys:
            base_key = keys[0]
        cur_key = next(k for k in keys if k != base_key)
        base_meta, cur_meta = self.file_meta_by_key[base_key], self.file_meta_by_key[cur_key]
        labels = (row_label(base_meta).split("  •  ")[0], row_label(cur_meta).split("  •  ")[0])
        out_dir = Path(app.reports_folder) / evidence.EVIDENCE_DIR_NAME
        self._evidence_busy = True
        self.refresh_evidence_button()

        def _done(path=None, error=None):
            self._evidence_busy = False
            self.refresh_evidence_button()
            if error is not None:
                app.add_test_log(f"❌ Пакет улик не собран: {error}")
                messagebox.showerror("Пакет улик", f"Не удалось собрать пакет улик:\n{error}",
                                     parent=self._parent_for_box())
                return
            app.add_test_log(f"📦 Пакет улик собран: {path}")
            messagebox.showinfo("Пакет улик",
                                f"Пакет собран:\n{path}\n\nВнутри — ticket.md с готовым текстом "
                                f"тикета, оба JSON, страница сравнения, окружение и хвост журнала.",
                                parent=self._parent_for_box())
            try:
                os.startfile(str(path.parent))
            except OSError as e:                    # нет проводника (сервер, CI) — путь уже показан
                app.add_test_log(f"⚠️ Папка пакета улик не открылась: {e}")

        def _work():
            try:
                path = evidence.build_evidence_pack(
                    base_meta["path"], cur_meta["path"], out_dir,
                    render_html=app._generate_comparison_html, labels=labels)
            except Exception as e:                  # любой сбой — в окно и журнал, не в stderr потока
                error = f"{type(e).__name__}: {e}"  # имя e живёт только внутри except
                app._ui_call(lambda: _done(error=error))
                return
            app._ui_call(lambda: _done(path=path))

        threading.Thread(target=_work, daemon=True).start()

    def _parent_for_box(self):
        """Окно для сообщения: диалог, пока он жив, иначе главное окно."""
        try:
            if self.dlg.winfo_exists():
                return self.dlg
        except tk.TclError:
            pass
        return self.app.root

    def compare(self):
        app = self.app
        selected_keys = [k for k, v in self.sel_vars.items() if v.get()]
        base_key = self._current_base_key()
        refusal = validate_comparison(selected_keys, base_key, MAX_FILES)
        if refusal:
            messagebox.showwarning(*refusal, parent=self.dlg)
            return
        try:
            datasets = build_datasets(selected_keys, self.file_meta_by_key)
        except ValueError as ex:
            messagebox.showerror("Ошибка", str(ex), parent=self.dlg)
            return

        app._save_comparison_settings({
            "custom_names": self.custom_names,
            "last_selected_files": selected_keys,
            "last_base_version": base_key,
        })
        self.close()
        html = app._generate_comparison_html(
            datasets, str(self.file_meta_by_key[base_key]["path"]))
        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = app.reports_folder / f"comparison_{ts_now}.html"
        try:
            out_path.write_text(html, encoding="utf-8")
            app.add_test_log(f"📊 Отчёт сравнения сохранён: {out_path.name}")
            webbrowser.open(str(out_path))
        except Exception as ex:
            messagebox.showerror("Ошибка", f"Не удалось сохранить отчёт:\n{ex}")
