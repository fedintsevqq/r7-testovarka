"""Вкладка «Версии»: установленная версия Р7, дистрибутивы, установка и
удаление (успех msiexec — код 0 или 3010), проверка хэшей дистрибутивов.

VersionsTabMixin — методы, которые R7Testovarka получает наследованием.
"""
import os
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from r7 import hashes
from r7.run_state import INSTALL
from r7.ui.base import COLORS
from r7.ui.hash_window import HashResultsWindow


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
            self._set_status("Дистрибутивы не найдены")
            return
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for f in files:
            ver = self._extract_version(f.stem) or "—"
            size_mb = round(f.stat().st_size / (1024 * 1024), 1)
            self.distributives.append({"path": f, "name": f.name})
            self.tree.insert("", tk.END, iid=str(len(self.distributives) - 1),
                              values=(f.name, ver, size_mb))
        self._set_status(f"Найдено: {len(files)}")

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
        self._set_status("Удаление...")
        cmd = self._build_uninstall_command(self.current_version_info)
        try:
            # shell=False: командная строка уже полностью собрана, а без
            # обёртки cmd.exe proc.kill() ниже завершает реальный процесс
            # деинсталлятора, а не промежуточный cmd.exe.
            proc = subprocess.Popen(cmd, shell=False)
        except OSError as e:
            self._set_status(f"⚠️ Не удалось запустить удаление: {e}")
            return False
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            self._set_status("⚠️ Удаление не завершилось за 60 сек, процесс завершён принудительно")
            return False

        if proc.returncode not in self._MSIEXEC_SUCCESS_CODES:
            self._set_status(f"⚠️ Удаление завершилось с кодом {proc.returncode}")
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
        self._set_status(f"Установка {path.name}...")
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
            self._set_status(f"⚠️ Не удалось запустить установку: {e}")
            return False
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            proc.kill()
            self._set_status(
                f"⚠️ Установка не завершилась за {timeout_sec // 60} мин, процесс завершён принудительно")
            return False
        if proc.returncode not in self._MSIEXEC_SUCCESS_CODES:
            self._set_status(f"⚠️ Установка завершилась с кодом {proc.returncode}")
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
        quiet = self.quiet_install_var.get()
        path = self.selected_distributive["path"]
        outcome = {}

        def worker():
            uninstalled = self.uninstall_current_version()
            outcome["uninstalled"] = uninstalled
            outcome["installed"] = bool(uninstalled) and self.install_version(path, quiet=quiet)

        def done():
            # Главный поток, при любом исходе потока (в т. ч. исключении):
            # прежде кнопка «Установить» оставалась выключенной навсегда.
            if outcome.get("installed"):
                messagebox.showinfo("Готово", "Установка завершена")
            elif not outcome.get("uninstalled"):
                messagebox.showerror(
                    "Ошибка", "Не удалось удалить текущую версию — установка отменена.\n"
                             "Подробности в строке статуса.")
            else:
                messagebox.showerror(
                    "Ошибка", "Установка не завершилась успешно.\n"
                             "Подробности в строке статуса.")
            self.refresh_distributives()
            self.detect_current_version()
            self.btn_install.config(state=tk.NORMAL)

        # Установка и прогоны взаимно исключены (RunState, INSTALL): удаление
        # Р7 посреди замера уронило бы прогон.
        self._start_run(INSTALL, worker, on_done=done,
                        before=lambda: self.btn_install.config(state=tk.DISABLED))

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
        reference, err = hashes.load_reference(self.distributives_folder / "hashes.json")
        if err:
            self.add_test_log(f"⚠️ {err}")

        def _update_progress(filename, idx):
            lbl_file.config(text=f"Обработка: {filename}")
            progressbar.config(value=idx)
            lbl_count.config(text=f"{idx + 1} / {len(files)}")

        results = []
        for i, path in enumerate(files):
            self.root.after(0, lambda fn=path.name, idx=i: _update_progress(fn, idx))
            row = hashes.hash_row(path, reference)
            results.append(row)
            if row["md5"] == "ОШИБКА":
                self.add_test_log(f"❌ {path.name}: ошибка чтения — {row['sha256']}")
            else:
                self.add_test_log(f"🔐 {path.name}: {row['status']}")

        _total, ok_count, fail_count = hashes.summary(results)
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
        Окно — r7.ui.hash_window.HashResultsWindow.

        Args:
            results: List of dicts with keys name, size, md5, sha256, status, tag.
                     Dicts are mutated in place when references are saved/deleted.
        """
        return HashResultsWindow(self, results)
