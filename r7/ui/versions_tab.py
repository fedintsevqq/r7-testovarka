"""Вкладка «Версии»: установленная версия Р7, дистрибутивы, установка и
удаление (успех msiexec — код 0 или 3010), проверка хэшей дистрибутивов.

Удаление идёт только через проверенную команду msiexec (r7.versions.
validate_uninstall_command), папка установки стирается только та, что в
записи реестра (remove_install_dir). Ключи тихой установки — по типу
дистрибутива (r7.installers).

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

from r7 import distributives, hashes, privileges, windows
from r7.installers import detect_installer_kind, silent_args
from r7.run_state import INSTALL
from r7.versions import install_dir_has_r7_exe, is_inno_uninstaller, remove_install_dir
from r7.ui.base import COLORS
from r7.ui.hash_window import HashResultsWindow


class VersionsTabMixin:
    """Вкладка «Версии» — часть R7Testovarka (через наследование)."""

    # Подсказка у кнопки «Установить» и текст под таблицей без прав
    # администратора: msiexec без них версию не поставит.
    NO_ADMIN_INSTALL_HINT = "Нужны права администратора"
    NO_ADMIN_INSTALL_TEXT = ("Установка версий недоступна: нужны права администратора. "
                             "Прогоны замеров без прав работают.")

    def _enable_install_button(self):
        """Включает «Установить», если есть права; без прав кнопка остаётся
        выключенной, а подпись под таблицей объясняет почему."""
        if privileges.is_admin():
            self.btn_install.config(state=tk.NORMAL)
            return True
        self.btn_install.config(state=tk.DISABLED)
        self.lbl_file_info.config(text=self.NO_ADMIN_INSTALL_TEXT)
        return False

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
                self.lbl_current.config(text=self._short_version_text(info),
                                        style="VersionOk.TLabel")
            else:
                self.lbl_current.config(text="Не установлена", style="VersionWarn.TLabel")

        if threading.current_thread() is threading.main_thread():
            _update_label()
        else:
            self.root.after(0, _update_label)

    def distributive_dirs(self):
        """Папки с дистрибутивами: Distributives/ и `distributives_dirs` из настроек."""
        return distributives.all_dirs(self.distributives_folder)

    def refresh_distributives(self):
        """Перечитывает все папки с дистрибутивами и перестраивает таблицу."""
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.distributives = []
        dirs = self.distributive_dirs()
        files = distributives.list_files(dirs)
        if not files:
            self.btn_install.config(state=tk.DISABLED)
            self._set_status("Дистрибутивы не найдены" + (f" (папок: {len(dirs)})" if len(dirs) > 1 else ""))
            return
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for f in files:
            ver = self._extract_version(f.stem) or "—"
            size_mb = round(f.stat().st_size / (1024 * 1024), 1)
            folder = "Distributives" if f.parent == self.distributives_folder else str(f.parent)
            self.distributives.append({"path": f, "name": f.name})
            self.tree.insert("", tk.END, iid=str(len(self.distributives) - 1),
                              values=(f.name, ver, size_mb, folder))
        self._set_status(f"Дистрибутивов: {len(files)}"
                         + (f" в {len(dirs)} папках" if len(dirs) > 1 else " в папке"))

    def _in_thread(self, work, done):
        """work() — в фоновом потоке, done(результат) — в главном. Окно при этом
        не замирает: Tk продолжает перерисовываться."""
        box = {}

        def run():
            try:
                box["value"] = work()
            except Exception as e:  # диалог или копирование упали — в журнал, не в тишину
                box["error"] = e

        t = threading.Thread(target=run, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                self.root.after(100, poll)
            elif "error" in box:
                self.add_test_log(f"❌ {type(box['error']).__name__}: {box['error']}")
                self._set_status(f"⚠️ {box['error']}")
            else:
                done(box.get("value"))
        self.root.after(100, poll)

    def _owner_hwnd(self):
        try:
            return int(self.root.wm_frame(), 16)
        except (tk.TclError, ValueError):
            return None

    def add_distributives_dir(self):
        """«Добавить папку»: папка запоминается в настройках, файлы не копируются.

        Выбор папки — системный диалог в отдельном потоке (windows.browse_for_folder):
        диалог Tk после импорта pywinauto не открывался, и приложение висело.
        """
        owner = self._owner_hwnd()
        self._set_status("Выберите папку с дистрибутивами Р7...")

        def done(folder):
            if not folder:
                self.refresh_distributives()
                return
            if distributives.add_dir(folder):
                self.add_test_log(f"📁 Папка дистрибутивов добавлена: {folder}")
            self.refresh_distributives()

        self._in_thread(lambda: windows.browse_for_folder("Папка с дистрибутивами Р7", owner), done)

    def search_distributives(self):
        """«Поискать в Загрузках»: «Загрузки» и «Рабочий стол», два уровня вглубь,
        в фоновом потоке; найденные папки предлагаются к добавлению. Во время
        прогона кнопка не работает: поиск читает диск."""
        busy = self.run_state.try_start(INSTALL)
        if busy is not None:
            messagebox.showwarning(*busy)
            return
        roots = distributives.default_search_roots()
        self._set_status("Поиск дистрибутивов в «Загрузках» и на «Рабочем столе»...")

        def worker():
            try:
                return distributives.find_installers(roots)
            finally:
                self.run_state.finish(INSTALL)

        def done(found):
            known = set(self.distributive_dirs())
            new = [p for p in (found or []) if p not in known]
            if not new:
                self._set_status("Поиск: новых папок с дистрибутивами не найдено")
                messagebox.showinfo("Поиск дистрибутивов",
                                    "В «Загрузках» и на «Рабочем столе» новых папок с "
                                    "дистрибутивами Р7 нет.\nИскались файлы r7-office*.exe/.msi.")
                return
            text = "\n".join(str(p) for p in new)
            if messagebox.askyesno("Поиск дистрибутивов",
                                   f"Найдены папки с дистрибутивами Р7:\n\n{text}\n\n"
                                   f"Добавить их в список? Файлы не копируются."):
                for p in new:
                    distributives.add_dir(p)
                self.add_test_log(f"📁 Добавлено папок дистрибутивов: {len(new)}")
            self.refresh_distributives()

        result = {}

        def run():
            result["found"] = worker()

        def finish():
            done(result.get("found"))

        t = threading.Thread(target=run, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                self.root.after(200, poll)
            else:
                finish()
        self.root.after(200, poll)

    def on_select_distributive(self, event):
        """Handles Treeview selection — enables Install button and shows file size."""
        sel = self.tree.selection()
        if sel and self.distributives:
            idx = int(sel[0])
            self.selected_distributive = self.distributives[idx]
            mb = self.selected_distributive["path"].stat().st_size / (1024 * 1024)
            self.lbl_file_info.config(text=f"{self.selected_distributive['name']} ({mb:.1f} МБ)")
            self._enable_install_button()
        else:
            self.btn_install.config(state=tk.DISABLED)

    # msiexec.exe возвращает 3010 при успешном завершении, если требуется
    # перезагрузка — это тоже успех, а не ошибка.
    _MSIEXEC_SUCCESS_CODES = (0, 3010)

    def uninstall_current_version(self):
        """Silently uninstalls the currently detected R7-Office version.

        Команда — только проверенный msiexec /X{GUID} из записи реестра
        (_build_uninstall_command); всё остальное отклоняется до запуска.
        После успеха удаляется папка InstallLocation той же записи — с
        предохранителями remove_install_dir, а не жёсткие пути в Program Files.

        Returns:
            bool: True если удаление подтверждено (код возврата 0/3010, либо
            версия изначально не была установлена). False при отклонённой
            команде, таймауте или ненулевом коде возврата — в этих случаях
            каталог программы НЕ удаляется, чтобы не рассинхронизировать
            файлы с реестром.
        """
        info = self.current_version_info
        if not info:
            return True
        self._set_status("Удаление...")
        try:
            cmd = self._build_uninstall_command(info)
        except ValueError as e:
            self._set_status(f"⚠️ {e}")
            self.add_test_log(f"⚠️ Удаление не запущено: {e}")
            return False
        if info.get("registry_hive") == "HKCU":
            self.add_test_log("ℹ️ Запись об установке — в HKCU (установка для пользователя); "
                              "удаление идёт через msiexec по коду продукта")
        location = info.get("install_location")
        had_exe = install_dir_has_r7_exe(location)
        try:
            # shell=False: аргументы уже разобраны и проверены, а без обёртки
            # cmd.exe proc.kill() ниже завершает реальный msiexec.
            proc = subprocess.Popen(cmd, shell=False)
        except OSError as e:
            self._set_status(f"⚠️ Не удалось запустить удаление: {e}")
            return False
        inno = is_inno_uninstaller(cmd[0])
        timeout_sec = self._UNINSTALL_INNO_TIMEOUT_SEC if inno else 60
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            proc.kill()
            self._set_status(f"⚠️ Удаление не завершилось за {timeout_sec} сек, "
                             f"процесс завершён принудительно")
            return False

        if proc.returncode not in self._MSIEXEC_SUCCESS_CODES:
            self._set_status(f"⚠️ Удаление завершилось с кодом {proc.returncode}")
            return False
        if inno and not self._wait_inno_uninstaller_gone(cmd[0]):
            self._set_status(f"⚠️ Деинсталлятор Inno не закончил работу за "
                             f"{self._UNINSTALL_INNO_TIMEOUT_SEC} сек")
            return False

        time.sleep(3)
        remove_install_dir(location, self.add_test_log, had_exe=had_exe)
        return True

    # Деинсталлятор Inno Setup (unins000.exe) копирует себя во временную папку
    # и удаляет программу оттуда; сам unins000.exe стирается последним. Конец
    # удаления — исчезновение этого файла, ждём его не дольше этого.
    _UNINSTALL_INNO_TIMEOUT_SEC = 300
    _UNINSTALL_INNO_POLL_SEC = 1.0

    def _wait_inno_uninstaller_gone(self, uninstaller):
        deadline = time.monotonic() + self._UNINSTALL_INNO_TIMEOUT_SEC
        while os.path.exists(uninstaller):
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._UNINSTALL_INNO_POLL_SEC)
        return True

    # Тихая установка не требует участия пользователя — 5 минут с запасом.
    # Интерактивная показывает мастер установки, который пользователь
    # проходит вручную, поэтому таймаут увеличен, чтобы не убить процесс
    # посреди диалогов (EULA, выбор папки и т.д.).
    _INSTALL_QUIET_TIMEOUT_SEC = 300
    _INSTALL_INTERACTIVE_TIMEOUT_SEC = 1800

    def install_version(self, path, quiet=True):
        """Installs an R7-Office distributive.

        Ключи тихой установки зависят от установщика (r7.installers): msi —
        /quiet /norestart, Inno Setup — /VERYSILENT…, NSIS — /S. Если тип
        .exe не распознан, тихих ключей нет: покажется мастер, и ожидание
        идёт по интерактивному таймауту — об этом пишется в журнал.

        Args:
            path: Path object pointing to the .msi or .exe installer.
            quiet: If True (default), installs silently when the installer
                kind is known. If False, the installer shows its normal UI.

        Returns:
            bool: True on success (return code 0 or 3010), False if the
            process timed out or exited with any other code.
        """
        # Подпись — перед установкой любого файла, откуда бы он ни был: ставим
        # от администратора только то, что подписано Р7 (r7.distributives).
        ok, why = distributives.check_installer(path, windows.authenticode_signature)
        if not ok:
            self._set_status(f"⚠️ {path.name}: не установлен — {why}")
            self.add_test_log(f"⚠️ Установка {path.name} отклонена: {why}")
            return False
        self.add_test_log(f"🔏 {path.name}: {why}")
        self._set_status(f"Установка {path.name}...")
        kind = detect_installer_kind(path)
        if kind == "msi":
            cmd = ["msiexec", "/i", str(path), "/norestart"]
        else:
            cmd = [str(path)]
        if quiet:
            args = silent_args(kind)
            if not args:
                self.add_test_log(
                    f"⚠️ {path.name}: тип установщика не распознан — тихая установка "
                    f"невозможна, откроется мастер установки, пройдите его вручную")
                quiet = False
            cmd += [a for a in args if a not in cmd]
        timeout_sec = (self._INSTALL_QUIET_TIMEOUT_SEC if quiet
                       else self._INSTALL_INTERACTIVE_TIMEOUT_SEC)
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
        if not privileges.is_admin():
            # Кнопка без прав выключена; это страховка для Enter и двойного
            # щелчка по строке таблицы.
            messagebox.showerror("Ошибка прав",
                                 "Установка версий недоступна без прав администратора: "
                                 "msiexec не поставит и не удалит версию. Перезапустите "
                                 "программу от имени администратора.")
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
            self._enable_install_button()

        # Установка и прогоны взаимно исключены (RunState, INSTALL): удаление
        # Р7 посреди замера уронило бы прогон.
        self._start_run(INSTALL, worker, on_done=done,
                        before=lambda: self.btn_install.config(state=tk.DISABLED))

    def add_distributive(self):
        """«Добавить файл»: выбрать дистрибутивы (.msi/.exe) и скопировать в
        Distributives. Копирование — в фоне: 430–630 МБ с медленного диска на
        главном потоке замораживали окно на десятки секунд."""
        files = filedialog.askopenfilenames(
            title="Дистрибутивы Р7", filetypes=[("Установщики Р7", "*.msi *.exe")])
        if not files:
            return
        self.distributives_folder.mkdir(parents=True, exist_ok=True)
        self._set_status(f"Копирую в Distributives: {len(files)} файл(ов)...")

        def work():
            copied = []
            for f in files:
                dst = self.distributives_folder / Path(f).name
                if Path(f).resolve() != dst.resolve():
                    shutil.copy2(f, dst)
                copied.append(dst.name)
            return copied

        def done(copied):
            self.add_test_log(f"📥 Скопировано в Distributives: {', '.join(copied or [])}")
            self.refresh_distributives()

        self._in_thread(work, done)

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
