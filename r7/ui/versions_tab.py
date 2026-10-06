"""Вкладка «Версии»: установленная версия Р7, дистрибутивы, установка и
удаление (успех msiexec — код 0 или 3010), проверка хэшей дистрибутивов.

VersionsTabMixin — методы, которые R7Testovarka получает наследованием.
"""
import csv
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from r7.ui.base import COLORS


class VersionsTabMixin:
    """Вкладка «Версии» — часть R7Testovarka (через наследование)."""

    def detect_current_version(self):
        """Reads Windows registry and updates the "Текущая версия" label.

        self.current_version_info обновляется синхронно в вызывающем потоке —
        это обычное присваивание Python, оно безопасно из любого потока и
        нужно немедленно там, где detect_current_version вызывается из
        фонового потока и код сразу же читает результат (например,
        _batch_worker). Обновление самого виджета — единственная часть,
        которую нельзя делать не из главного потока, — маршалится туда через
        root.after(), если вызов пришёл не из главного потока.
        """
        info = self._read_current_version_from_registry()
        self.current_version_info = info

        def _update_label():
            if info:
                self.lbl_current.config(
                    text=self._short_version_text(info), foreground=COLORS["success"])
            else:
                self.lbl_current.config(text="Не установлена", foreground=COLORS["warn"])

        if threading.current_thread() is threading.main_thread():
            _update_label()
        else:
            self.root.after(0, _update_label)

    def refresh_distributives(self):
        """Rescans the Distributives folder and refreshes the table."""
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.distributives = []
        files = list(self.distributives_folder.glob("*.msi")) + list(self.distributives_folder.glob("*.exe"))
        if not files:
            self.btn_install.config(state=tk.DISABLED)
            self.status_var.set("Дистрибутивы не найдены")
            return
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for f in files:
            ver = self._extract_version(f.stem) or "—"
            size_mb = round(f.stat().st_size / (1024 * 1024), 1)
            self.distributives.append({"path": f, "name": f.name})
            self.tree.insert("", tk.END, iid=str(len(self.distributives) - 1),
                              values=(f.name, ver, size_mb))
        self.status_var.set(f"Найдено: {len(files)}")

    def on_select_distributive(self, event):
        """Handles Treeview selection — enables Install button and shows file size."""
        sel = self.tree.selection()
        if sel and self.distributives:
            idx = int(sel[0])
            self.selected_distributive = self.distributives[idx]
            self.btn_install.config(state=tk.NORMAL)
            mb = self.selected_distributive["path"].stat().st_size / (1024 * 1024)
            self.lbl_file_info.config(text=f"{self.selected_distributive['name']} ({mb:.1f} МБ)")
        else:
            self.btn_install.config(state=tk.DISABLED)

    # msiexec.exe возвращает 3010 при успешном завершении, если требуется
    # перезагрузка — это тоже успех, а не ошибка.
    _MSIEXEC_SUCCESS_CODES = (0, 3010)

    def uninstall_current_version(self):
        """Silently uninstalls the currently detected R7-Office version.

        Returns:
            bool: True если удаление подтверждено (код возврата 0/3010, либо
            версия изначально не была установлена). False при таймауте или
            ненулевом коде возврата — в этом случае каталоги программы НЕ
            удаляются, чтобы не рассинхронизировать файлы с реестром.
        """
        if not self.current_version_info:
            return True
        self.status_var.set("Удаление...")
        cmd = self._build_uninstall_command(self.current_version_info)
        try:
            # shell=False: командная строка уже полностью собрана, а без
            # обёртки cmd.exe proc.kill() ниже завершает реальный процесс
            # деинсталлятора, а не промежуточный cmd.exe.
            proc = subprocess.Popen(cmd, shell=False)
        except OSError as e:
            self.status_var.set(f"⚠️ Не удалось запустить удаление: {e}")
            return False
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.status_var.set("⚠️ Удаление не завершилось за 60 сек, процесс завершён принудительно")
            return False

        if proc.returncode not in self._MSIEXEC_SUCCESS_CODES:
            self.status_var.set(f"⚠️ Удаление завершилось с кодом {proc.returncode}")
            return False

        time.sleep(3)
        for p in [r"C:\Program Files\R7-Office", r"C:\Program Files (x86)\R7-Office"]:
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)
        return True

    def install_version(self, path, quiet=True):
        """Installs an R7-Office distributive.

        Args:
            path: Path object pointing to the .msi or .exe installer.
            quiet: If True (default), adds /quiet and installs silently.
                If False, the installer shows its normal UI.

        Returns:
            bool: True on success (return code 0 or 3010), False if the
            process timed out or exited with any other code.
        """
        self.status_var.set(f"Установка {path.name}...")
        if path.suffix == ".msi":
            cmd = ["msiexec", "/i", str(path), "/norestart"]
        else:
            cmd = [str(path)]
        if quiet:
            cmd.append("/quiet")
        # Тихая установка не требует участия пользователя — 5 минут с запасом.
        # Интерактивная показывает мастер установки, который пользователь
        # проходит вручную, поэтому таймаут увеличен, чтобы не убить процесс
        # посреди диалогов (EULA, выбор папки и т.д.).
        timeout_sec = 300 if quiet else 1800
        # shell=False: список аргументов не требует обёртки cmd.exe, и без неё
        # proc.kill() по таймауту завершает реальный установщик, а не cmd.exe.
        try:
            proc = subprocess.Popen(cmd, shell=False)
        except OSError as e:
            self.status_var.set(f"⚠️ Не удалось запустить установку: {e}")
            return False
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.status_var.set(
                f"⚠️ Установка не завершилась за {timeout_sec // 60} мин, процесс завершён принудительно")
            return False
        if proc.returncode not in self._MSIEXEC_SUCCESS_CODES:
            self.status_var.set(f"⚠️ Установка завершилась с кодом {proc.returncode}")
            return False
        time.sleep(3)
        self.detect_current_version()
        return True

    def install_selected(self):
        """Confirms and launches uninstall + install in a background thread."""
        if not self.selected_distributive:
            return
        if self.current_version_info:
            if not messagebox.askyesno("Подтверждение",
                                       f"Удалить текущую и установить\n{self.selected_distributive['name']}?"):
                return
        self.btn_install.config(state=tk.DISABLED)
        quiet = self.quiet_install_var.get()

        def worker():
            uninstalled = self.uninstall_current_version()
            installed = False
            if uninstalled:
                installed = self.install_version(self.selected_distributive["path"], quiet=quiet)

            if installed:
                self.root.after(0, lambda: messagebox.showinfo("Готово", "Установка завершена"))
            elif not uninstalled:
                self.root.after(0, lambda: messagebox.showerror(
                    "Ошибка", "Не удалось удалить текущую версию — установка отменена.\n"
                             "Подробности в строке статуса."))
            else:
                self.root.after(0, lambda: messagebox.showerror(
                    "Ошибка", "Установка не завершилась успешно.\n"
                             "Подробности в строке статуса."))
            self.root.after(0, self.refresh_distributives)
            self.root.after(0, self.detect_current_version)
            self.root.after(0, lambda: self.btn_install.config(state=tk.NORMAL))
        threading.Thread(target=worker, daemon=True).start()

    def add_distributive(self):
        """Opens a file dialog to copy installers into the Distributives folder."""
        files = filedialog.askopenfilenames(filetypes=[("Installer", "*.msi *.exe")])
        for f in files:
            dst = self.distributives_folder / Path(f).name
            shutil.copy2(f, dst)
        self.refresh_distributives()

    def open_distributives_folder(self):
        """Opens the Distributives folder in Windows Explorer."""
        os.startfile(str(self.distributives_folder))

    # ---------------------- Хеш-суммы дистрибутивов ----------------------
    def check_hashes(self):
        """Entry point for hash verification — creates progress window then spawns worker thread."""
        files = (list(self.distributives_folder.glob("*.msi")) +
                 list(self.distributives_folder.glob("*.exe")))
        if not files:
            messagebox.showwarning("Нет файлов", "В папке Distributives нет файлов для проверки.")
            return

        prog_win = tk.Toplevel(self.root)
        prog_win.transient(self.root)
        prog_win.configure(bg=COLORS["bg"])
        prog_win.title("Вычисление хеш-сумм...")
        prog_win.geometry("440x120")
        prog_win.resizable(False, False)
        prog_win.grab_set()

        lbl_file = ttk.Label(prog_win, text="Подготовка...", wraplength=410, anchor=tk.W)
        lbl_file.pack(pady=(14, 4), padx=15, fill=tk.X)

        progressbar = ttk.Progressbar(prog_win, maximum=len(files), mode="determinate")
        progressbar.pack(fill=tk.X, padx=15)

        lbl_count = ttk.Label(prog_win, text=f"0 / {len(files)}")
        lbl_count.pack(pady=4)

        threading.Thread(
            target=self._hash_worker,
            args=(files, prog_win, progressbar, lbl_file, lbl_count),
            daemon=True,
        ).start()

    def _hash_worker(self, files, prog_win, progressbar, lbl_file, lbl_count):
        """Computes MD5/SHA256 for each file in a background thread, then shows results.

        Args:
            files: List of Path objects to hash.
            prog_win: Progress Toplevel window (destroyed when done).
            progressbar: ttk.Progressbar widget to update.
            lbl_file: Label showing the current filename.
            lbl_count: Label showing N / total progress.
        """
        hashes_json = self.distributives_folder / "hashes.json"
        reference = {}
        if hashes_json.exists():
            try:
                with open(hashes_json, encoding="utf-8") as f:
                    reference = json.load(f)
            except Exception as e:
                self.add_test_log(f"⚠️ Ошибка загрузки hashes.json: {e}")

        def _update_progress(filename, idx):
            lbl_file.config(text=f"Обработка: {filename}")
            progressbar.config(value=idx)
            lbl_count.config(text=f"{idx + 1} / {len(files)}")

        results = []
        for i, path in enumerate(files):
            self.root.after(0, lambda fn=path.name, idx=i: _update_progress(fn, idx))
            try:
                size_mb = path.stat().st_size / (1024 * 1024)
                md5h = hashlib.md5()
                sha256h = hashlib.sha256()
                # 1 МБ вместо прежних 8 КБ — дистрибутивы весят сотни МБ/ГБ,
                # и мелкий чанк умножает накладные расходы на системные вызовы.
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        md5h.update(chunk)
                        sha256h.update(chunk)
                md5_val = md5h.hexdigest()
                sha256_val = sha256h.hexdigest()

                ref = reference.get(path.name, {})
                if not ref:
                    status, tag = "⚠️ Нет эталона", "no_ref"
                elif (ref.get("md5", "").lower() == md5_val and
                      ref.get("sha256", "").lower() == sha256_val):
                    status, tag = "✅ Совпадает", "ok"
                else:
                    status, tag = "❌ Не совпадает", "fail"

                results.append({
                    "name": path.name,
                    "size": f"{size_mb:.2f}",
                    "md5": md5_val,
                    "sha256": sha256_val,
                    "status": status,
                    "tag": tag,
                })
                self.add_test_log(f"🔐 {path.name}: {status}")

            except Exception as e:
                results.append({
                    "name": path.name,
                    "size": "—",
                    "md5": "ОШИБКА",
                    "sha256": str(e),
                    "status": "❌ Ошибка чтения",
                    "tag": "fail",
                })
                self.add_test_log(f"❌ {path.name}: ошибка чтения — {e}")

        ok_count   = sum(1 for r in results if r["tag"] == "ok")
        fail_count = sum(1 for r in results if r["tag"] == "fail")
        self.add_test_log(
            f"🔐 Проверка завершена: {len(results)} файлов  "
            f"✅ {ok_count} совпадают  ❌ {fail_count} не совпадают"
        )
        self.root.after(0, prog_win.destroy)
        self.root.after(0, lambda: self._show_hash_results(results))

    def _show_hash_results(self, results):
        """Opens a Treeview window with hash results.

        Supports: copy-on-double-click, reference editing/deletion via button and
        context menu, status refresh without re-scanning, and CSV export.

        Args:
            results: List of dicts with keys name, size, md5, sha256, status, tag.
                     Dicts are mutated in place when references are saved/deleted.
        """
        hashes_path = self.distributives_folder / "hashes.json"

        win = tk.Toplevel(self.root)
        win.transient(self.root)
        win.configure(bg=COLORS["bg"])
        win.title("Хеш-суммы дистрибутивов")
        win.geometry("1120x480")
        win.resizable(True, True)

        # ── Treeview ──────────────────────────────────────────────────────────
        columns = ("name", "size", "md5", "sha256", "status")
        tree = ttk.Treeview(win, columns=columns, show="headings", selectmode="browse")

        tree.heading("name",   text="Имя файла")
        tree.heading("size",   text="Размер (МБ)")
        tree.heading("md5",    text="MD5")
        tree.heading("sha256", text="SHA256")
        tree.heading("status", text="Статус")

        tree.column("name",   width=260, anchor=tk.W,      stretch=True)
        tree.column("size",   width=90,  anchor=tk.CENTER, stretch=False)
        tree.column("md5",    width=245, anchor=tk.W,      stretch=False)
        tree.column("sha256", width=370, anchor=tk.W,      stretch=False)
        tree.column("status", width=130, anchor=tk.CENTER, stretch=False)

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

        # row_id → result dict, built while populating the tree
        id_to_result = {}
        for r in results:
            iid = tree.insert("", tk.END,
                              values=(r["name"], r["size"], r["md5"], r["sha256"], r["status"]),
                              tags=(r["tag"],))
            id_to_result[iid] = r

        # ── hashes.json helpers ───────────────────────────────────────────────
        def load_reference():
            """Returns current hashes.json content or an empty dict."""
            if hashes_path.exists():
                try:
                    with open(hashes_path, encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    return {}
            return {}

        def save_reference(ref):
            """Persists the reference dict to hashes.json (creates if absent).

            Args:
                ref: Dict mapping filename → {md5, sha256}.
            """
            hashes_path.parent.mkdir(parents=True, exist_ok=True)
            with open(hashes_path, "w", encoding="utf-8") as f:
                json.dump(ref, f, indent=2, ensure_ascii=False)

        def recompute_status(row_data, ref):
            """Returns (status_str, tag) for row_data against current reference.

            Files with read errors keep their error status regardless of the reference.

            Args:
                row_data: Result dict for one file.
                ref: Current hashes.json dict.

            Returns:
                Tuple[str, str]: Human-readable status and Treeview tag name.
            """
            if row_data["md5"] in ("ОШИБКА", "—"):
                return row_data["status"], row_data["tag"]
            entry = ref.get(row_data["name"], {})
            if not entry:
                return "⚠️ Нет эталона", "no_ref"
            if (entry.get("md5", "").lower() == row_data["md5"].lower() and
                    entry.get("sha256", "").lower() == row_data["sha256"].lower()):
                return "✅ Совпадает", "ok"
            return "❌ Не совпадает", "fail"

        def refresh_row(iid, row_data):
            """Redraws one Treeview row from the (already updated) row_data dict."""
            tree.item(iid, values=(
                row_data["name"], row_data["size"],
                row_data["md5"], row_data["sha256"], row_data["status"],
            ), tags=(row_data["tag"],))

        # ── Selection helper ──────────────────────────────────────────────────
        def get_selected():
            """Returns (iid, row_data) for the selected row, or warns and returns (None, None)."""
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Нет выбора", "Выберите файл в таблице.", parent=win)
                return None, None
            iid = sel[0]
            return iid, id_to_result[iid]

        # ── Edit dialog ───────────────────────────────────────────────────────
        def open_edit_dialog(iid, row_data):
            """Opens the reference-hash editing dialog for a single file.

            Args:
                iid: Treeview item id for the file.
                row_data: Mutable result dict for the file.
            """
            if row_data["md5"] in ("ОШИБКА", "—"):
                messagebox.showwarning(
                    "Недоступно",
                    "Нельзя добавить эталон для файла с ошибкой чтения.",
                    parent=win,
                )
                return

            ref = load_reference()
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
            sha256_entry = ttk.Entry(dlg, textvariable=sha256_var, width=46, font=("Consolas", 10))
            sha256_entry.grid(row=1, column=1, padx=(0, 12), pady=5, sticky=tk.EW)

            dlg.columnconfigure(1, weight=1)

            def validate_hex(value, expected_len, label):
                """Returns (cleaned_str, error_msg_or_None)."""
                s = value.strip().lower()
                if len(s) != expected_len:
                    return None, f"{label}: длина должна быть {expected_len} символов (введено {len(s)})"
                if not all(c in "0123456789abcdef" for c in s):
                    return None, f"{label}: допустимы только символы 0–9 и a–f"
                return s, None

            def on_save():
                md5_clean, err = validate_hex(md5_var.get(), 32, "MD5")
                if err:
                    messagebox.showerror("Ошибка ввода", err, parent=dlg)
                    return
                sha256_clean, err = validate_hex(sha256_var.get(), 64, "SHA256")
                if err:
                    messagebox.showerror("Ошибка ввода", err, parent=dlg)
                    return

                ref = load_reference()
                ref[row_data["name"]] = {"md5": md5_clean, "sha256": sha256_clean}
                try:
                    save_reference(ref)
                except Exception as e:
                    messagebox.showerror("Ошибка записи",
                                         f"Не удалось сохранить hashes.json:\n{e}", parent=dlg)
                    return

                new_status, new_tag = recompute_status(row_data, ref)
                row_data["status"] = new_status
                row_data["tag"]    = new_tag
                refresh_row(iid, row_data)

                self.add_test_log(f"✏️ Добавлен эталон для {row_data['name']}")
                messagebox.showinfo("Готово", "Эталон сохранён", parent=dlg)
                dlg.destroy()

            btn_row = ttk.Frame(dlg)
            btn_row.grid(row=2, column=0, columnspan=2, pady=14)
            ttk.Button(btn_row, text="Сохранить", command=on_save).pack(side=tk.LEFT, padx=10)
            ttk.Button(btn_row, text="Отмена",    command=dlg.destroy).pack(side=tk.LEFT)

            md5_entry.focus_set()
            dlg.bind("<Return>", lambda _: on_save())
            dlg.bind("<Escape>", lambda _: dlg.destroy())

        # ── Delete reference ──────────────────────────────────────────────────
        def delete_reference(iid, row_data):
            """Removes the reference entry for this file from hashes.json.

            Args:
                iid: Treeview item id.
                row_data: Mutable result dict for the file.
            """
            ref = load_reference()
            if row_data["name"] not in ref:
                messagebox.showinfo("Нет эталона",
                                    f"Для файла «{row_data['name']}» эталон не задан.",
                                    parent=win)
                return
            if not messagebox.askyesno("Подтверждение",
                                       f"Удалить эталон для:\n{row_data['name']}?",
                                       parent=win):
                return
            del ref[row_data["name"]]
            try:
                save_reference(ref)
            except Exception as e:
                messagebox.showerror("Ошибка записи",
                                     f"Не удалось сохранить hashes.json:\n{e}", parent=win)
                return

            new_status, new_tag = recompute_status(row_data, ref)
            row_data["status"] = new_status
            row_data["tag"]    = new_tag
            refresh_row(iid, row_data)

            self.add_test_log(f"🗑️ Удалён эталон для {row_data['name']}")
            messagebox.showinfo("Готово", "Эталон удалён", parent=win)

        # ── Context menu ──────────────────────────────────────────────────────
        ctx_menu = tk.Menu(win, tearoff=0)

        def show_context_menu(event):
            iid = tree.identify_row(event.y)
            if not iid:
                return
            tree.selection_set(iid)
            ctx_menu.post(event.x_root, event.y_root)

        def ctx_edit():
            iid, row_data = get_selected()
            if iid:
                open_edit_dialog(iid, row_data)

        def ctx_delete():
            iid, row_data = get_selected()
            if iid:
                delete_reference(iid, row_data)

        def ctx_copy(col_idx, label):
            iid, row_data = get_selected()
            if not iid:
                return
            value = tree.item(iid, "values")[col_idx]
            if value in ("ОШИБКА", "—", ""):
                return
            win.clipboard_clear()
            win.clipboard_append(value)
            messagebox.showinfo("Скопировано",
                                f"{label} скопирован в буфер обмена:\n{value}", parent=win)

        ctx_menu.add_command(label="✏️ Добавить/редактировать эталон", command=ctx_edit)
        ctx_menu.add_command(label="🗑️ Удалить эталон",                command=ctx_delete)
        ctx_menu.add_separator()
        ctx_menu.add_command(label="Скопировать MD5",    command=lambda: ctx_copy(2, "MD5"))
        ctx_menu.add_command(label="Скопировать SHA256", command=lambda: ctx_copy(3, "SHA256"))

        tree.bind("<Button-3>", show_context_menu)
        win.bind("<Button-1>", lambda e: ctx_menu.unpost())

        # ── Double-click copies MD5 / SHA256 ─────────────────────────────────
        HASH_COLS = {"#3": ("MD5", 2), "#4": ("SHA256", 3)}

        def on_double_click(event):
            col   = tree.identify_column(event.x)
            iid   = tree.identify_row(event.y)
            if not iid or col not in HASH_COLS:
                return
            label, idx = HASH_COLS[col]
            value = tree.item(iid, "values")[idx]
            if value in ("ОШИБКА", "—", ""):
                return
            win.clipboard_clear()
            win.clipboard_append(value)
            messagebox.showinfo("Скопировано",
                                f"{label} скопирован в буфер обмена:\n{value}", parent=win)

        tree.bind("<Double-1>", on_double_click)

        # ── Bottom bar ────────────────────────────────────────────────────────
        bottom = ttk.Frame(win)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=6, padx=8)

        hint = ttk.Label(bottom,
                         text="ПКМ или двойной клик по MD5/SHA256 — дополнительные действия",
                         foreground=COLORS["text_secondary"])
        hint.pack(side=tk.LEFT)

        def on_edit_btn():
            iid, row_data = get_selected()
            if iid:
                open_edit_dialog(iid, row_data)

        def on_delete_btn():
            iid, row_data = get_selected()
            if iid:
                delete_reference(iid, row_data)

        def save_csv():
            path = filedialog.asksaveasfilename(
                parent=win,
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=f"hash_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            )
            if not path:
                return
            try:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Имя файла", "Размер (МБ)", "MD5", "SHA256", "Статус"])
                    for r in results:
                        writer.writerow([r["name"], r["size"], r["md5"], r["sha256"], r["status"]])
                messagebox.showinfo("Сохранено", f"Отчёт сохранён:\n{path}", parent=win)
                self.add_test_log(f"💾 Отчёт хешей сохранён: {path}")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}", parent=win)

        ttk.Button(bottom, text="💾 Сохранить отчёт (CSV)", command=save_csv).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="🗑️ Удалить эталон",        command=on_delete_btn).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="✏️ Добавить/редактировать эталон", command=on_edit_btn).pack(side=tk.RIGHT, padx=4)
