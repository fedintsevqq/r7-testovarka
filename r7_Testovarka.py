# -*- coding: utf-8 -*-
"""
R7-Testovarka Light – управление версиями + стресс-тест таблиц
"""

import os
import sys
import subprocess
import winreg
import time
import threading
import shutil
import re
import json
import hashlib
import csv
import ctypes
import platform
import random
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import webbrowser

def get_base_dir() -> Path:
    """Возвращает базовую директорию приложения (портативный режим).

    В режиме PyInstaller (.exe, sys.frozen == True) — папка рядом с .exe.
    В режиме Python-скрипта — папка, содержащая .py файл.
    Использование sys.executable вместо sys.argv[0] надёжнее при запуске
    через ярлык или другой лаунчер.
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller: sys.executable указывает на собранный .exe
        return Path(sys.executable).parent
    # Обычный запуск: берём папку скрипта, а не текущую рабочую директорию
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()

# Библиотеки для автоматизации
try:
    import pyautogui
    PYAUTOGUI_OK = True
    pyautogui.FAILSAFE = False
except ImportError:
    PYAUTOGUI_OK = False
    print("⚠️ Установите pyautogui: pip install pyautogui")

try:
    import pyperclip
except ImportError:
    pyperclip = None
    print("⚠️ Установите pyperclip: pip install pyperclip")

try:
    from openpyxl import Workbook
    EXCEL_OK = True
except ImportError:
    EXCEL_OK = False
    print("⚠️ Установите openpyxl: pip install openpyxl")

try:
    import win32gui
    import win32con
    WIN32_OK = True
except ImportError:
    WIN32_OK = False
    print("⚠️ Установите pywin32: pip install pywin32")

try:
    import psutil
    PSUTIL_OK = True
except ImportError:
    PSUTIL_OK = False
    print("⚠️ Установите psutil: pip install psutil")



class R7Testovarka:
    TEST_DEFINITIONS = [
        "Выделение всех ячеек (Ctrl+A)",
        "Копирование всех ячеек (Ctrl+C)",
        "Вставка большого массива (Ctrl+V)",
        "Добавление нового листа",
        "Добавление столбца (горячие клавиши)",
        "Добавление столбца (меню Вставка)",
        "Вставка 1 ячейки (горячие клавиши)",
        "Вставка 5 ячеек (горячие клавиши)",
        "Вставка 1 ячейки (ПКМ)",
        "Вставка 5 ячеек (ПКМ)",
        "Функция ВПР (50K строк)",
        "Удаление столбца (Del)",
    ]

    def __init__(self, root):
        """Initializes the main application window and state.

        Args:
            root: The tkinter root window.
        """
        self.root = root
        self.root.title("R7-Testovarka Light")
        self.root.geometry("800x600")
        self.root.resizable(True, True)

        self.distributives_folder = BASE_DIR / "Distributives"
        self.distributives_folder.mkdir(exist_ok=True)

        self.test_files_folder = BASE_DIR / "TestFiles"
        self.test_files_folder.mkdir(exist_ok=True)

        self.reports_folder = BASE_DIR / "Reports"
        self.reports_folder.mkdir(exist_ok=True)

        self.current_version_info = None
        self.distributives = []
        self.selected_distributive = None
        self._cached_r7_path = None
        self.test_vars = {}   # populated by _build_perf_tab

        self.setup_ui()
        self.refresh_distributives()
        self.detect_current_version()

    # ---------------------- UI ----------------------
    def setup_ui(self):
        """Builds the main UI layout with notebook tabs and status bar."""
        main = ttk.Frame(self.root, padding="10")
        main.pack(fill=tk.BOTH, expand=True)

        info = ttk.LabelFrame(main, text="Текущая версия", padding="5")
        info.pack(fill=tk.X, pady=(0, 10))
        self.lbl_current = ttk.Label(info, text="Не определена", foreground="red", font=("Arial", 10))
        self.lbl_current.pack(anchor=tk.W)

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_versions = ttk.Frame(self.notebook)
        self.tab_perf = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_versions, text="📦 Версии")
        self.notebook.add(self.tab_perf, text="⚡ Производительность")

        self._build_versions_tab()
        self._build_perf_tab()

        self.status_var = tk.StringVar(value="Готов")
        status = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2))
        status.pack(side=tk.BOTTOM, fill=tk.X)

    def _build_versions_tab(self):
        """Builds the distributives list and install controls."""
        ttk.Label(self.tab_versions, text="Дистрибутивы:", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(0, 5))
        frame = ttk.Frame(self.tab_versions)
        frame.pack(fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(frame, yscrollcommand=scroll.set, font=("Consolas", 10))
        self.listbox.pack(fill=tk.BOTH, expand=True)
        scroll.config(command=self.listbox.yview)

        self.lbl_file_info = ttk.Label(self.tab_versions, text="", foreground="gray")
        self.lbl_file_info.pack(anchor=tk.W, pady=5)

        btn_frame = ttk.Frame(self.tab_versions)
        btn_frame.pack(fill=tk.X, pady=10)
        self.btn_install = ttk.Button(btn_frame, text="📥 Установить", command=self.install_selected, state=tk.DISABLED)
        self.btn_install.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🔄 Обновить", command=self.refresh_distributives).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📁 Добавить", command=self.add_distributive).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📂 Открыть папку", command=self.open_distributives_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🔐 Проверить хеш-суммы", command=self.check_hashes).pack(side=tk.LEFT, padx=5)

        self.listbox.bind('<<ListboxSelect>>', self.on_select_distributive)

    def _build_perf_tab(self):
        """Builds the performance tab: log on the left, scrollable test checkboxes on the right."""
        # ── Top area: log + test-selection panel ─────────────────────────────
        top = ttk.Frame(self.tab_perf)
        top.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        # Log widget
        log_frame = ttk.Frame(top)
        log_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.test_log = tk.Text(log_frame, font=("Consolas", 9))
        scroll_log = ttk.Scrollbar(log_frame, command=self.test_log.yview)
        self.test_log.configure(yscrollcommand=scroll_log.set)
        scroll_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.test_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Test-selection panel (right, fixed width, scrollable inside)
        sel_outer = ttk.LabelFrame(top, text="Выберите тесты", padding="4")
        sel_outer.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))

        cv = tk.Canvas(sel_outer, width=230, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(sel_outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        cv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = ttk.Frame(cv)
        cv_win = cv.create_window((0, 0), window=inner, anchor="nw")

        saved = self._load_test_selection()
        self.test_vars = {}
        for name in self.TEST_DEFINITIONS:
            var = tk.BooleanVar(value=saved.get(name, True))
            tk.Checkbutton(inner, text=name, variable=var,
                           wraplength=210, anchor=tk.W, justify=tk.LEFT
                           ).pack(anchor=tk.W, pady=1, padx=2)
            self.test_vars[name] = var

        def _on_inner_configure(event):
            cv.configure(scrollregion=cv.bbox("all"))
        def _on_canvas_configure(event):
            cv.itemconfig(cv_win, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        cv.bind("<Configure>", _on_canvas_configure)

        # Select-all / deselect-all
        mini = ttk.Frame(sel_outer)
        mini.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(mini, text="☑ Все", width=7,
                   command=lambda: [v.set(True) for v in self.test_vars.values()]).pack(side=tk.LEFT)
        ttk.Button(mini, text="☐ Снять", width=7,
                   command=lambda: [v.set(False) for v in self.test_vars.values()]).pack(side=tk.LEFT, padx=3)

        # ── Bottom action buttons ─────────────────────────────────────────────
        btn_frame = ttk.Frame(self.tab_perf)
        btn_frame.pack(fill=tk.X, pady=4)
        ttk.Button(btn_frame, text="🧪 Запустить выбранные тесты",
                   command=self.run_spreadsheet_test).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📊 Сравнить размеры файлов",
                   command=self.compare_file_sizes).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📊 Сравнить версии",
                   command=self.compare_versions).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🚀 Batch-режим (все версии)",
                   command=self.run_batch_mode).pack(side=tk.LEFT, padx=5)

    # ---------------------- Управление версиями ----------------------
    def detect_current_version(self):
        """Reads Windows registry to detect the currently installed R7-Office version."""
        try:
            paths = [
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
            ]
            for reg_path in paths:
                try:
                    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_READ)
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        sub = winreg.EnumKey(key, i)
                        subkey = winreg.OpenKey(key, sub)
                        try:
                            name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                            if "Р7-Офис" in name or "R7-Office" in name:
                                ver = winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                                self.lbl_current.config(text=f"{name} ({ver})", foreground="green")
                                self.current_version_info = {
                                    "name": name,
                                    "version": ver,
                                    "uninstall_string": winreg.QueryValueEx(subkey, "UninstallString")[0]
                                }
                                return
                        except:
                            pass
                        finally:
                            winreg.CloseKey(subkey)
                    winreg.CloseKey(key)
                except:
                    pass
            self.lbl_current.config(text="Не установлена", foreground="orange")
            self.current_version_info = None
        except:
            self.current_version_info = None

    def refresh_distributives(self):
        """Rescans the Distributives folder and refreshes the listbox."""
        self.listbox.delete(0, tk.END)
        self.distributives = []
        files = list(self.distributives_folder.glob("*.msi")) + list(self.distributives_folder.glob("*.exe"))
        if not files:
            self.listbox.insert(tk.END, "--- нет дистрибутивов ---")
            self.btn_install.config(state=tk.DISABLED)
            return
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for f in files:
            ver = self._extract_version(f.stem)
            display = f"{f.name} [{ver}]" if ver else f.name
            self.distributives.append({"path": f, "name": f.name})
            self.listbox.insert(tk.END, display)
        self.status_var.set(f"Найдено: {len(files)}")

    def _extract_version(self, filename):
        """Extracts a version string like v2026.1.3 from a filename.

        Args:
            filename: The installer filename stem (without extension).

        Returns:
            str: Version string like 'v2026.1.3', or None if not found.
        """
        match = re.search(r'(\d+\.\d+(?:\.\d+)*)', filename)
        return f"v{match.group(1)}" if match else None

    def on_select_distributive(self, event):
        """Handles listbox selection — enables Install button and shows file size."""
        sel = self.listbox.curselection()
        if sel and self.distributives:
            self.selected_distributive = self.distributives[sel[0]]
            self.btn_install.config(state=tk.NORMAL)
            mb = self.selected_distributive["path"].stat().st_size / (1024 * 1024)
            self.lbl_file_info.config(text=f"{self.selected_distributive['name']} ({mb:.1f} МБ)")
        else:
            self.btn_install.config(state=tk.DISABLED)

    def uninstall_current_version(self):
        """Silently uninstalls the currently detected R7-Office version.

        Returns:
            bool: Always True — errors are logged but do not halt the install flow.
        """
        if not self.current_version_info:
            return True
        try:
            self.status_var.set("Удаление...")
            cmd = self.current_version_info["uninstall_string"] + " /quiet /norestart"
            proc = subprocess.Popen(cmd, shell=True)
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                self.status_var.set("⚠️ Удаление не завершилось за 60 сек, процесс завершён принудительно")
            time.sleep(3)
            for p in [r"C:\Program Files\R7-Office", r"C:\Program Files (x86)\R7-Office"]:
                if os.path.exists(p):
                    shutil.rmtree(p, ignore_errors=True)
            return True
        except:
            return True

    def install_version(self, path):
        """Installs an R7-Office distributive silently.

        Args:
            path: Path object pointing to the .msi or .exe installer.

        Returns:
            bool: True on success, False if the process timed out.
        """
        self.status_var.set(f"Установка {path.name}...")
        if path.suffix == ".msi":
            cmd = ["msiexec", "/i", str(path), "/quiet", "/norestart"]
        else:
            cmd = [str(path), "/quiet"]
        proc = subprocess.Popen(cmd, shell=True)
        try:
            proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.status_var.set("⚠️ Установка не завершилась за 5 минут, процесс завершён принудительно")
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

        def worker():
            self.uninstall_current_version()
            self.install_version(self.selected_distributive["path"])
            self.root.after(0, lambda: messagebox.showinfo("Готово", "Установка завершена"))
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

    # ---------------------- Настройки тестов ----------------------
    def _load_test_selection(self):
        """Loads saved test-selection state from selected_tests.json.

        Returns:
            dict: Mapping test_name → bool. Missing keys default to True.
        """
        path = BASE_DIR / "selected_tests.json"
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_test_selection(self):
        """Persists the current checkbox state to selected_tests.json."""
        path = BASE_DIR / "selected_tests.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({name: var.get() for name, var in self.test_vars.items()},
                          f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.add_test_log(f"⚠️ Не удалось сохранить настройки тестов: {e}")

    # ---------------------- Лог ----------------------
    def add_test_log(self, msg):
        """Appends a timestamped message to the performance test log widget.

        Args:
            msg: The text to append.
        """
        try:
            self.test_log.insert(tk.END, f"[{datetime.now():%H:%M:%S}] {msg}\n")
            self.test_log.see(tk.END)
            self.root.update()
        except:
            print(msg)

    # ---------------------- Стресс-тест таблиц ----------------------
    def run_spreadsheet_test(self):
        """Entry point for the stress test — validates prerequisites then launches worker thread."""
        if not ctypes.windll.shell32.IsUserAnAdmin():
            messagebox.showerror(
                "Ошибка прав",
                "Стресс-тест требует запуска от имени администратора.\n"
                "Перезапустите программу с правами администратора."
            )
            return
        if not self.current_version_info:
            messagebox.showwarning("Нет версии", "Р7-Офис не установлен или не определён.")
            return
        if not PYAUTOGUI_OK or not pyperclip or not EXCEL_OK or not WIN32_OK:
            missing = []
            if not PYAUTOGUI_OK: missing.append("pyautogui")
            if not pyperclip: missing.append("pyperclip")
            if not EXCEL_OK: missing.append("openpyxl")
            if not WIN32_OK: missing.append("pywin32")
            messagebox.showerror("Ошибка",
                                 f"Отсутствуют библиотеки:\n{', '.join(missing)}\n"
                                 f"Установите: pip install " + " ".join(missing))
            return
        enabled = ({n for n, v in self.test_vars.items() if v.get()}
                   if self.test_vars else set(self.TEST_DEFINITIONS))
        if not enabled:
            messagebox.showwarning("Нет тестов", "Выберите хотя бы один тест для выполнения.")
            return
        self._save_test_selection()
        threading.Thread(target=self._spreadsheet_worker, args=(enabled,), daemon=True).start()

    def _spreadsheet_worker(self, enabled_tests=None):
        """Runs selected spreadsheet performance tests sequentially and saves reports.

        Args:
            enabled_tests: Set of test-name strings to execute. None → all tests.
        """
        if enabled_tests is None:
            enabled_tests = set(self.TEST_DEFINITIONS)
        self.add_test_log("\n🚀 ЗАПУСК СТРЕСС-ТЕСТА ТАБЛИЦ")
        REPORT_FILE = self.reports_folder / "Performance_Report.xlsx"
        HTML_REPORT_PATH = REPORT_FILE.with_suffix(".html")

        # ----- 1. Поиск тестового файла -----
        def find_test_file():
            """Searches known directories for the 50K-row test spreadsheet.

            Returns:
                Path: Path to the found file, or None.
            """
            patterns = ["файл-для-теста-Р7-офис-50К*.xlsx", "файл-для-теста-Р7-офис-50К*.xls", "*50К*.xlsx"]
            search_dirs = [self.test_files_folder, BASE_DIR, Path.home() / "Downloads", Path.home() / "Загрузки", Path.cwd()]
            for sd in search_dirs:
                if not sd.exists():
                    continue
                for pat in patterns:
                    for f in sd.glob(pat):
                        return f
            return None

        test_file = find_test_file()
        if not test_file:
            self.add_test_log("❌ Тестовый файл не найден.")
            return
        self.add_test_log(f"✅ Найден файл: {test_file}")

        # ----- 2. Вспомогательные функции для окон -----
        def find_r7_window():
            """Returns the hwnd of the first visible R7-Office window, or None."""
            import win32gui
            wins = []
            def enum_cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if "Р7-Офис" in title or test_file.stem in title:
                        wins.append(hwnd)
            win32gui.EnumWindows(enum_cb, wins)
            return wins[0] if wins else None

        def wait_for_window(title_part, timeout=60):
            """Polls for a visible window containing title_part, sets it foreground when found.

            Args:
                title_part: Substring to search for in window titles.
                timeout: Maximum seconds to wait.

            Returns:
                bool: True if window found, False on timeout.
            """
            import win32gui
            start = time.time()
            while time.time() - start < timeout:
                wins = []
                def enum_cb(hwnd, _):
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        if title_part.lower() in title.lower():
                            wins.append(hwnd)
                win32gui.EnumWindows(enum_cb, wins)
                if wins:
                    win32gui.SetForegroundWindow(wins[0])
                    return True
                time.sleep(0.5)
            return False

        def maximize_window():
            """Maximizes the R7-Office window if found.

            Returns:
                bool: True if window was found and maximized.
            """
            import win32gui, win32con
            hwnd = find_r7_window()
            if hwnd:
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                time.sleep(0.5)
                return True
            return False

        def focus_window():
            """Brings the R7-Office window to the foreground.

            Returns:
                bool: True if window was found and focused.
            """
            import win32gui
            hwnd = find_r7_window()
            if hwnd:
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                time.sleep(0.3)
                return True
            return False

        def close_update_dialog():
            return self._close_update_dialog_if_exists()

        def safe_hotkey(*keys):
            """Sends a keyboard shortcut without a built-in post-action delay."""
            pyautogui.hotkey(*keys, interval=0.1)

        def safe_press(key, presses=1):
            """Presses a key one or more times with a short inter-press delay.

            Args:
                key: Key name understood by pyautogui.
                presses: Number of times to press the key.
            """
            for _ in range(presses):
                pyautogui.press(key)
                time.sleep(0.1)

        def post_action_delay(seconds=0.5):
            """Waits after an operation completes — called outside measure() timing window."""
            time.sleep(seconds)

        def wait_for_data_ready(timeout=120):
            """Waits until the spreadsheet is fully loaded by timing Ctrl+End navigation.

            Two exit conditions are checked on every probe:
              1. Primary  — probe_time >= LOAD_THRESHOLD (machine is slow enough to see the diff).
              2. Fallback — probe_time has been stable (within STABLE_DELTA) for STABLE_SECS
                            consecutive seconds (machine is fast; threshold never fires).

            Raises:
                RuntimeError: If focus_window() fails MAX_FOCUS_FAILS times in a row,
                              indicating the R7-Office window has disappeared.

            Args:
                timeout: Maximum seconds to wait; on expiry a warning is logged and
                         the function returns False so the test can still run.

            Returns:
                bool: True if data confirmed ready, False on timeout.
            """
            LOAD_THRESHOLD  = 5.0   # Ctrl+End slower than this → data present
            BASE_WAIT       = 3     # initial pause before probing starts
            MAX_FOCUS_FAILS = 3     # consecutive focus failures → window is gone
            STABLE_SECS     = 10   # probe stable this many seconds → declare loaded
            STABLE_DELTA    = 0.05  # max spread in probe_time to be called "stable"

            self.add_test_log(
                f"⏳ Ожидание загрузки данных "
                f"(базовая задержка {BASE_WAIT} сек, порог {LOAD_THRESHOLD} сек)..."
            )
            time.sleep(BASE_WAIT)

            deadline          = time.time() + max(0, timeout - BASE_WAIT)
            focus_fail_streak = 0
            probe_history     = []   # rolling window of last STABLE_SECS probes

            while time.time() < deadline:
                elapsed_so_far = int(time.time() - open_start)

                # ── Liveness check ────────────────────────────────────────────
                if not focus_window():
                    focus_fail_streak += 1
                    self.add_test_log(
                        f"   ⚠️ Окно Р7-Офис недоступно. Проверьте, не упало ли приложение. "
                        f"(попытка {focus_fail_streak}/{MAX_FOCUS_FAILS})"
                    )
                    if focus_fail_streak >= MAX_FOCUS_FAILS:
                        raise RuntimeError(
                            "❌ Окно Р7-Офис потеряно, возможно, приложение зависло"
                        )
                    time.sleep(1)
                    continue
                focus_fail_streak = 0

                # ── Probe ─────────────────────────────────────────────────────
                t0 = time.time()
                try:
                    pyautogui.hotkey('ctrl', 'End', interval=0.05)
                except Exception as e:
                    self.add_test_log(f"   ⚠️ Ошибка зонда Ctrl+End: {e}")
                    time.sleep(1)
                    continue
                probe_time = time.time() - t0

                # Rolling history for stability check (last STABLE_SECS values)
                probe_history.append(probe_time)
                if len(probe_history) > STABLE_SECS:
                    probe_history.pop(0)

                self.add_test_log(
                    f"   ⏳ Ожидание загрузки данных... ({elapsed_so_far} сек, "
                    f"Ctrl+End = {probe_time:.3f} сек)"
                )

                # ── Exit condition 1: probe exceeded threshold ─────────────────
                if probe_time >= LOAD_THRESHOLD:
                    self.add_test_log(
                        f"   📊 Таблица загружена, навигация заняла {probe_time:.2f} сек"
                    )
                    pyautogui.hotkey('ctrl', 'Home', interval=0.05)
                    return True

                # ── Exit condition 2: probe stable for STABLE_SECS seconds ─────
                if (len(probe_history) == STABLE_SECS and
                        max(probe_history) - min(probe_history) <= STABLE_DELTA):
                    self.add_test_log(
                        f"   📊 Время Ctrl+End стабилизировалось на "
                        f"{probe_time:.3f} сек в течение {STABLE_SECS} сек "
                        f"— считаем загрузку завершённой"
                    )
                    pyautogui.hotkey('ctrl', 'Home', interval=0.05)
                    return True

                time.sleep(1)

            # ── Timeout ───────────────────────────────────────────────────────
            self.add_test_log(
                f"⚠️ Таймаут {timeout} сек: данные могут быть не загружены полностью, "
                f"продолжаем тест"
            )
            # Final liveness check — raise if the window vanished during the wait
            if not focus_window():
                raise RuntimeError(
                    "❌ Окно Р7-Офис недоступно после таймаута — тест прерван"
                )
            return False

        # ----- 3. Запуск Р7 и замер времени открытия -----
        r7_path = self._find_r7_path()
        if not r7_path:
            self.add_test_log("❌ Р7-Офис не найден.")
            return

        self.add_test_log(f"🔄 Запуск Р7-Офис с файлом: {test_file.name}")
        open_start = time.time()
        subprocess.Popen([r7_path, str(test_file)], shell=True)

        if not wait_for_window(test_file.stem, timeout=60) and not wait_for_window("Р7-Офис", timeout=10):
            self.add_test_log("❌ Окно Р7 не появилось.")
            return

        maximize_window()
        focus_window()
        time.sleep(1)
        close_update_dialog()
        time.sleep(0.5)

        # Фоновый мониторинг окна обновления на весь период теста
        _upd_stop = threading.Event()
        threading.Thread(
            target=self._monitor_update_dialog,
            args=(_upd_stop,),
            daemon=True,
        ).start()
        self.add_test_log("🔍 Запущен мониторинг окна обновления (проверка каждые 2 сек)")

        try:
            wait_for_data_ready(timeout=120)
        except RuntimeError as e:
            _upd_stop.set()
            self.add_test_log(str(e))
            return
        open_elapsed = time.time() - open_start
        self.add_test_log(f"✅ Файл открыт за {open_elapsed:.2f} сек (данные загружены)")

        focus_window()

        # ----- 3.5 Мониторинг ресурсов ------------------------------------------------
        self._r7_pids = None  # сбросить кэш перед новым поиском
        self._x2t_logged_pids = set()  # сбросить дедуп x2t перед новым тестом
        r7_procs = self._get_r7_processes()
        if PSUTIL_OK and r7_procs:
            try:
                _init_ram = round(
                    sum(p.memory_info().rss for p in r7_procs) / (1024 * 1024), 1
                )
                pids_str = ", ".join(str(p.pid) for p in r7_procs)
                self.add_test_log(
                    f"🔍 Поиск процесса Р7: найдено {len(r7_procs)} процессов "
                    f"(PID: {pids_str}), суммарная RAM = {_init_ram:.1f} МБ"
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self.add_test_log(f"🔍 Найдено {len(r7_procs)} процессов Р7, RAM недоступна")
        else:
            self.add_test_log(
                "⚠️ Процесс Р7 не найден — замеры RAM/CPU будут недоступны"
                if PSUTIL_OK else
                "⚠️ psutil не установлен — замеры RAM/CPU недоступны"
            )

        # ----- 4. Тесты ----------------------------------------------------------------
        sample0 = self._sample_r7_resources(r7_procs)
        results = [{
            "name": "Открытие файла", "time": open_elapsed, "error": None,
            "ram":            sample0["ram_mb"]       if sample0 else None,
            "cpu":            sample0["cpu_raw_pct"]   if sample0 else None,
            "cpu_normalized": sample0["cpu_norm_pct"]  if sample0 else None,
            "threads":        sample0["threads"]       if sample0 else None,
            "uptime_sec":     sample0["uptime_sec"]    if sample0 else None,
        }]

        def measure(name, func):
            """Times a single UI operation, samples resources, and logs the result.

            Skips the operation if name is not in enabled_tests.

            Args:
                name: Human-readable label; must match an entry in TEST_DEFINITIONS.
                func: Callable that performs the operation.

            Returns:
                dict with keys name/time/error/ram/cpu/cpu_normalized/threads/uptime_sec,
                or None if test is skipped.
            """
            nonlocal r7_procs
            if name not in enabled_tests:
                return None
            self.add_test_log(f"⏳ {name}...")
            try:
                focus_window()
            except Exception as e:
                self.add_test_log(f"   ⚠️ Не удалось установить фокус: {e}")
            start = time.time()
            error = None
            try:
                func()
            except Exception as e:
                error = str(e)
            elapsed = time.time() - start
            post_action_delay()
            self._r7_pids = None
            r7_procs = self._get_r7_processes()
            sample = self._sample_r7_resources(r7_procs)
            self._log_resources(sample)
            self.add_test_log(f"   ✅ {name}: {elapsed:.2f} сек" + (f" (ошибка: {error})" if error else ""))
            return {
                "name": name, "time": elapsed, "error": error,
                "ram":            sample["ram_mb"]      if sample else None,
                "cpu":            sample["cpu_raw_pct"]  if sample else None,
                "cpu_normalized": sample["cpu_norm_pct"] if sample else None,
                "threads":        sample["threads"]      if sample else None,
                "uptime_sec":     sample["uptime_sec"]    if sample else None,
            }

        def run_test(name, func):
            r = measure(name, func)
            if r is not None:
                results.append(r)

        def copy_paste_hotkey(cell_count, paste_offset):
            safe_hotkey('ctrl', 'home')
            for _ in range(cell_count - 1):
                pyautogui.hotkey('shift', 'right')
            safe_hotkey('ctrl', 'c')
            pyautogui.press('right', presses=paste_offset)
            safe_hotkey('ctrl', 'v')

        def copy_paste_context(cell_count, paste_offset):
            safe_hotkey('ctrl', 'home')
            for _ in range(cell_count - 1):
                pyautogui.hotkey('shift', 'right')
            pyautogui.click(button='right')
            safe_press('down', 2)
            safe_press('enter')
            pyautogui.press('right', presses=paste_offset)
            pyautogui.click(button='right')
            safe_press('down', 3)
            safe_press('enter')

        def add_column(method='hotkey'):
            safe_hotkey('ctrl', 'pageup')
            time.sleep(0.1)
            pyautogui.press('right')
            if method == 'hotkey':
                safe_hotkey('ctrl', 'shift', '=')
            else:
                safe_hotkey('alt', 'i')
                safe_press('c')

        def paste_big():
            safe_hotkey('shift', 'f11')
            safe_hotkey('ctrl', 'v')

        def vlookup():
            safe_hotkey('ctrl', 'pagedown')
            time.sleep(0.1)
            safe_hotkey('ctrl', 'home')
            pyperclip.copy('=VLOOKUP(A2;Лист1!A:B;2;FALSE)')
            safe_hotkey('ctrl', 'v')
            safe_press('enter')
            safe_hotkey('ctrl', 'shift', 'down')
            safe_hotkey('ctrl', 'd')
            time.sleep(0.5)

        def del_column():
            safe_hotkey('ctrl', 'home')
            pyautogui.press('right')
            pyautogui.press('delete')

        run_test("Выделение всех ячеек (Ctrl+A)",          lambda: safe_hotkey('ctrl', 'a'))
        run_test("Копирование всех ячеек (Ctrl+C)",         lambda: safe_hotkey('ctrl', 'c'))
        run_test("Вставка большого массива (Ctrl+V)",        paste_big)
        run_test("Добавление нового листа",                  lambda: safe_hotkey('shift', 'f11'))
        run_test("Добавление столбца (горячие клавиши)",     lambda: add_column('hotkey'))
        run_test("Добавление столбца (меню Вставка)",        lambda: add_column('menu'))
        run_test("Вставка 1 ячейки (горячие клавиши)",       lambda: copy_paste_hotkey(1, 10))
        run_test("Вставка 5 ячеек (горячие клавиши)",        lambda: copy_paste_hotkey(5, 15))
        run_test("Вставка 1 ячейки (ПКМ)",                   lambda: copy_paste_context(1, 10))
        run_test("Вставка 5 ячеек (ПКМ)",                    lambda: copy_paste_context(5, 15))
        run_test("Функция ВПР (50K строк)",                  vlookup)
        run_test("Удаление столбца (Del)",                   del_column)

        # ----- 5. Статистика ресурсов --------------------------------------------------
        ram_vals      = [r["ram"] for r in results if r.get("ram") is not None]
        cpu_vals      = [r["cpu"] for r in results if r.get("cpu") is not None]
        cpu_norm_vals = [r["cpu_normalized"] for r in results if r.get("cpu_normalized") is not None]
        peak_ram = max(ram_vals) if ram_vals else None
        avg_ram  = round(sum(ram_vals) / len(ram_vals), 1) if ram_vals else None
        min_ram  = min(ram_vals) if ram_vals else None
        peak_cpu = max(cpu_vals) if cpu_vals else None
        peak_cpu_norm = max(cpu_norm_vals) if cpu_norm_vals else None
        avg_cpu_norm  = round(sum(cpu_norm_vals) / len(cpu_norm_vals), 1) if cpu_norm_vals else None
        if peak_ram is not None:
            self.add_test_log(
                f"📊 Пик RAM: {peak_ram:.1f} МБ  Средн: {avg_ram:.1f} МБ  Мин: {min_ram:.1f} МБ")
        if peak_cpu is not None:
            self.add_test_log(
                f"📊 Пик CPU: {peak_cpu:.1f}% (сырое)  {peak_cpu_norm:.1f}% (норм., "
                f"{psutil.cpu_count() if PSUTIL_OK else '?'} ядер)")

        # ----- 6. Сохранение отчётов ---------------------------------------------------
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)

            # Excel
            from openpyxl import Workbook as WB
            wb = WB()
            ws = wb.active
            ws.title = "Результаты"
            ws.append(["Операция", "Время (сек)", "RAM (МБ)", "CPU (%)", "Ошибка"])
            for r in results:
                ws.append([r["name"], round(r["time"], 2),
                           r.get("ram") or "", r.get("cpu") or "",
                           r.get("error") or ""])
            wb.save(str(REPORT_FILE))
            self.add_test_log(f"📊 Excel-отчёт сохранён: {REPORT_FILE}")

            # JSON (полные данные для последующего сравнения версий)
            json_path = self.reports_folder / f"performance_full_{ts}.json"
            sys_mem_gb = round(psutil.virtual_memory().total / (1024**3), 1) if PSUTIL_OK else None
            full_data = {
                "timestamp": ts,
                "version": self.current_version_info.get("name") if self.current_version_info else None,
                "test_file": str(test_file),
                "system": {
                    "os": platform.platform(),
                    "ram_total_gb": sys_mem_gb,
                    "cpu_model": platform.processor() or None,
                },
                "summary": {
                    "peak_ram_mb": peak_ram,
                    "avg_ram_mb": avg_ram,
                    "min_ram_mb": min_ram,
                    "peak_cpu_pct": peak_cpu,
                    "peak_cpu_normalized_pct": peak_cpu_norm,
                    "avg_cpu_normalized_pct": avg_cpu_norm,
                },
                "results": results,
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(full_data, f, indent=2, ensure_ascii=False)
            self.add_test_log(f"📄 JSON-данные сохранены: {json_path.name}")

            # HTML
            version_str = (self.current_version_info.get("name")
                           if self.current_version_info else None)
            html_content = self._generate_html_report(
                results, test_file, open_elapsed, version_str,
                ram_vals, cpu_vals, peak_ram, avg_ram, min_ram, peak_cpu,
            )
            with open(HTML_REPORT_PATH, "w", encoding="utf-8") as f:
                f.write(html_content)
            self.add_test_log(f"📄 HTML-отчёт сохранён: {HTML_REPORT_PATH}")

        except Exception as e:
            self.add_test_log(f"⚠️ Ошибка сохранения отчётов: {e}")

        # ----- 7. Закрытие -------------------------------------------------------------
        _upd_stop.set()
        self.add_test_log("🔍 Мониторинг окна обновления остановлен")
        self.add_test_log("🔚 Закрытие Р7-Офис...")
        safe_hotkey('alt', 'f4')
        time.sleep(1)
        safe_press('right')
        safe_press('enter')
        self.add_test_log("🏁 Тест завершён.")

        # ----- 8. Диалог после теста ---------------------------------------------------
        self.root.after(0, lambda: self._show_post_test_dialog(HTML_REPORT_PATH, ts))

    # ---------------------- Вспомогательные методы (ресурсы, отчёты) ------

    def _get_r7_processes(self, log_cb=None):
        """Returns list of psutil.Process objects for all R7-Office related processes.

        Searches by name substrings: editors_helper, desktopeditors, r7, р7 (Cyrillic),
        x2t (внутренний конвертер документов Р7-Офис — отдельный процесс, который
        может давать заметный вклад в общую RAM/CPU при открытии/сохранении файлов).
        If self._r7_pids is set (from a previous call), tries direct PID lookup first.

        Args:
            log_cb: Callback for the x2t detection line; defaults to self.add_test_log.
        """
        if log_cb is None:
            log_cb = self.add_test_log

        if not PSUTIL_OK:
            return []

        if not hasattr(self, "_x2t_logged_pids"):
            self._x2t_logged_pids = set()

        # Fast path: try previously discovered PIDs directly
        if getattr(self, "_r7_pids", None):
            procs = []
            for pid in self._r7_pids:
                try:
                    p = psutil.Process(pid)
                    p.name()  # raises NoSuchProcess if dead
                    procs.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if procs:
                return procs

        # Full scan with expanded name list
        search_substrings = ("editors_helper", "desktopeditors", "r7", "р7", "x2t")
        found = []
        try:
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if any(s in name for s in search_substrings):
                        found.append(proc)
                        if "x2t" in name:
                            pid = proc.info.get("pid")
                            if pid not in self._x2t_logged_pids:
                                log_cb(
                                    f"🔧 Обнаружен процесс конвертации x2t: "
                                    f"PID={pid}, имя={proc.info.get('name')}"
                                )
                                self._x2t_logged_pids.add(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

        # Cache PIDs for subsequent fast-path calls
        self._r7_pids = [p.pid for p in found]
        return found

    def _sample_r7_resources(self, procs):
        """Снимает агрегированные метрики RAM/CPU/потоков/аптайма по списку процессов Р7.

        CPU нормализуется делением на psutil.cpu_count(): «сырое» значение psutil
        может превышать 100% на многоядерных системах (сумма по всем ядрам), а
        «норм.» приводит его к шкале 0–100%, как в диспетчере задач Windows.

        Args:
            procs: список psutil.Process, обычно результат _get_r7_processes().

        Returns:
            dict | None: {"ram_mb", "cpu_raw_pct", "cpu_norm_pct", "threads",
            "uptime_sec"}, или None если psutil недоступен или ни один процесс не жив.
        """
        if not (PSUTIL_OK and procs):
            return None

        total_ram_mb  = 0.0
        max_cpu_raw   = 0.0
        total_threads = 0
        oldest_create = None
        alive = 0
        now = time.time()

        for p in procs:
            # Память: основной метрик; процесс считается "живым" если RAM читается успешно
            try:
                total_ram_mb += p.memory_info().rss / (1024 * 1024)
                alive += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            # CPU: каждый вызов независим от других
            try:
                max_cpu_raw = max(max_cpu_raw, p.cpu_percent(interval=0.1))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            # Потоки: каждый вызов независим
            try:
                total_threads += p.num_threads()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            # Время создания: каждый вызов независим
            try:
                create_ts = p.create_time()
                if oldest_create is None or create_ts < oldest_create:
                    oldest_create = create_ts
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if alive == 0:
            return None

        cpu_count = psutil.cpu_count() or 1
        return {
            "ram_mb":       round(total_ram_mb, 1),
            "cpu_raw_pct":  round(max_cpu_raw, 1),
            "cpu_norm_pct": round(max_cpu_raw / cpu_count, 1),
            "threads":      total_threads,
            "uptime_sec":   round(now - oldest_create, 1) if oldest_create is not None else None,
        }

    def _log_resources(self, sample, log_cb=None):
        """Форматированный вывод одного замера ресурсов с цветовой индикацией CPU.

        Индикатор считается по нормализованному CPU (0–100%, все ядра):
        🟢 < 50% — обычная нагрузка, 🟡 50–79.9% — средняя, 🔴 ≥ 80% — высокая.

        Args:
            sample: dict из _sample_r7_resources(), либо None (тогда ничего не пишет).
            log_cb: функция логирования; по умолчанию self.add_test_log.
        """
        if sample is None:
            return
        if log_cb is None:
            log_cb = self.add_test_log

        cpu_norm = sample.get("cpu_norm_pct")
        if cpu_norm is None:
            icon = "⚪"
        elif cpu_norm < 50:
            icon = "🟢"
        elif cpu_norm < 80:
            icon = "🟡"
        else:
            icon = "🔴"

        uptime = sample.get("uptime_sec")
        uptime_str = f"{uptime:.0f} сек" if uptime is not None else "—"

        log_cb(
            f"   📊 RAM: {sample['ram_mb']:.1f} МБ  "
            f"CPU: {sample['cpu_raw_pct']:.1f}% (норм. {cpu_norm if cpu_norm is not None else '—'}%) {icon}  "
            f"Потоки: {sample['threads']}  Аптайм: {uptime_str}"
        )

    def _generate_html_report(self, results, test_file, open_elapsed,
                              version_str, ram_vals, cpu_vals,
                              peak_ram, avg_ram, min_ram, peak_cpu):
        """Builds and returns the full HTML performance report as a string."""
        ts_display = datetime.now().strftime("%d.%m.%Y %H:%M")
        os_info = platform.platform()
        try:
            cpu_info = platform.processor() or "N/A"
        except Exception:
            cpu_info = "N/A"
        sys_mem_gb = (round(psutil.virtual_memory().total / (1024 ** 3), 1)
                      if PSUTIL_OK else "N/A")
        file_size_mb = (round(test_file.stat().st_size / (1024 * 1024), 2)
                        if test_file.exists() else "N/A")
        cpu_count_display = psutil.cpu_count() if PSUTIL_OK else "N/A"

        # CPU-показатели psutil не нормализованы по числу ядер (могут быть >100%);
        # норм. значение делит их на cpu_count(), получая шкалу 0–100% как в Task Manager.
        cpu_norm_vals = [r.get("cpu_normalized") for r in results if r.get("cpu_normalized") is not None]
        peak_cpu_norm = max(cpu_norm_vals) if cpu_norm_vals else None

        # Chart data
        labels_json   = json.dumps([r["name"] for r in results], ensure_ascii=False)
        times_json    = json.dumps([round(r["time"], 3) for r in results])
        ram_json      = json.dumps([r.get("ram") for r in results])
        cpu_json      = json.dumps([r.get("cpu") for r in results])
        cpu_norm_json = json.dumps([r.get("cpu_normalized") for r in results])

        # Stats cards
        def stat_card(title, value, unit="", warn=False):
            color = "#e74c3c" if warn else "#2980b9"
            val_str = f"{value:.1f}" if isinstance(value, float) else (str(value) if value is not None else "—")
            return (f'<div class="card" style="border-left:4px solid {color}">'
                    f'<div class="card-title">{title}</div>'
                    f'<div class="card-value" style="color:{color}">{val_str}{unit}</div></div>')

        # Порог предупреждения считаем по нормализованному CPU — «сырое» значение
        # может законно превышать 100% на многоядерной системе и не годится для warn.
        cpu_warn = peak_cpu_norm is not None and peak_cpu_norm > 80
        cards_html = (stat_card("Пик RAM", peak_ram, " МБ") +
                      stat_card("Средн. RAM", avg_ram, " МБ") +
                      stat_card("Мин. RAM", min_ram, " МБ") +
                      stat_card("Пик CPU (сырое)", peak_cpu, "%") +
                      stat_card("Пик CPU (норм.)", peak_cpu_norm, "%", warn=cpu_warn))

        # Results table rows
        rows_html = ""
        for r in results:
            err_class = "row-error" if r.get("error") else ""
            ram_cell = f"{r['ram']:.1f}" if r.get("ram") is not None else "—"
            cpu_cell = f"{r['cpu']:.1f}" if r.get("cpu") is not None else "—"
            cpu_norm_cell = (f"{r['cpu_normalized']:.1f}"
                              if r.get("cpu_normalized") is not None else "—")
            err_cell = r.get("error") or ""
            rows_html += (f"<tr class='{err_class}'>"
                          f"<td>{r['name']}</td>"
                          f"<td>{r['time']:.3f}</td>"
                          f"<td>{ram_cell}</td>"
                          f"<td>{cpu_cell}</td>"
                          f"<td>{cpu_norm_cell}</td>"
                          f"<td>{err_cell}</td></tr>\n")

        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>R7-Office Performance Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body{{font-family:Arial,sans-serif;margin:0;padding:20px;background:#f5f6fa;color:#333}}
  h1{{color:#2c3e50}}
  .info-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px;margin-bottom:20px}}
  .info-item{{background:#fff;padding:8px 12px;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
  .info-label{{font-size:.75em;color:#888;text-transform:uppercase}}
  .info-value{{font-weight:bold;margin-top:2px}}
  .cards{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:24px}}
  .card{{background:#fff;padding:14px 18px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.12);min-width:150px}}
  .card-title{{font-size:.8em;color:#888}}
  .card-value{{font-size:1.6em;font-weight:bold;margin-top:4px}}
  .cpu-note{{font-size:.85em;color:#555;margin:-6px 0 20px;padding:8px 12px;
    background:#eef3fb;border-radius:6px}}
  .charts{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:28px}}
  .chart-box{{background:#fff;padding:16px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.12)}}
  @media(max-width:700px){{.charts{{grid-template-columns:1fr}}}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.12)}}
  th{{background:#2c3e50;color:#fff;padding:10px 12px;text-align:left;font-size:.85em}}
  td{{padding:8px 12px;border-bottom:1px solid #eee;font-size:.9em}}
  tr:last-child td{{border-bottom:none}}
  tr.row-error td{{background:#fdecea;color:#c0392b}}
  tr:not(.row-error):hover td{{background:#f0f4ff}}
  .pdf-btn{{position:fixed;top:16px;right:16px;padding:8px 18px;background:#2c3e50;
    color:#fff;border:none;border-radius:6px;font-size:.9em;cursor:pointer;
    box-shadow:0 2px 6px rgba(0,0,0,.25);z-index:1000}}
  .pdf-btn:hover{{background:#34495e}}
  @media print{{
    .pdf-btn{{display:none}}
    body{{background:#fff}}
    canvas,.chart-box,table{{page-break-inside:avoid}}
    h1,h2,h3{{page-break-after:avoid}}
  }}
</style>
</head>
<body>
<button class="pdf-btn" onclick="window.print()">📄 Сохранить как PDF</button>
<h1>Отчёт о производительности R7-Office</h1>

<div class="info-grid">
  <div class="info-item"><div class="info-label">Версия R7-Office</div><div class="info-value">{version_str or "—"}</div></div>
  <div class="info-item"><div class="info-label">Дата и время</div><div class="info-value">{ts_display}</div></div>
  <div class="info-item"><div class="info-label">Тестовый файл</div><div class="info-value">{test_file.name}</div></div>
  <div class="info-item"><div class="info-label">Размер файла</div><div class="info-value">{file_size_mb} МБ</div></div>
  <div class="info-item"><div class="info-label">ОС</div><div class="info-value">{os_info}</div></div>
  <div class="info-item"><div class="info-label">Процессор</div><div class="info-value">{cpu_info}</div></div>
  <div class="info-item"><div class="info-label">RAM (всего)</div><div class="info-value">{sys_mem_gb} ГБ</div></div>
  <div class="info-item"><div class="info-label">Время открытия файла</div><div class="info-value">{open_elapsed:.2f} сек</div></div>
</div>

<div class="cards">{cards_html}</div>
<p class="cpu-note">ℹ️ CPU показан относительно всех ядер (0–100%). «Сырое» значение — как
в диспетчере задач Windows на вкладке «Подробности» (может превышать 100% на многоядерных
системах), «норм.» — то же значение, делённое на количество логических ядер
({cpu_count_display}).</p>

<div class="charts">
  <div class="chart-box"><canvas id="timeChart"></canvas></div>
  <div class="chart-box"><canvas id="ramChart"></canvas></div>
  <div class="chart-box"><canvas id="cpuChart"></canvas></div>
</div>

<table>
<thead><tr><th>Операция</th><th>Время (сек)</th><th>RAM (МБ)</th><th>CPU (%)</th><th>CPU норм. (%)</th><th>Ошибка</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>

<script>
const labels   = {labels_json};
const times    = {times_json};
const rams     = {ram_json};
const cpus     = {cpu_json};
const cpusNorm = {cpu_norm_json};
const defOpts = (title) => ({{
  responsive: true,
  plugins: {{legend:{{display:false}}, title:{{display:true, text:title}}}},
  scales: {{y:{{beginAtZero:true}}}}
}});
new Chart(document.getElementById('timeChart'), {{
  type:'bar', data:{{labels, datasets:[{{label:'сек',data:times,backgroundColor:'#3498db'}}]}},
  options: defOpts('Время выполнения (сек)')
}});
new Chart(document.getElementById('ramChart'), {{
  type:'line', data:{{labels, datasets:[{{label:'МБ',data:rams,borderColor:'#27ae60',backgroundColor:'rgba(39,174,96,.15)',fill:true,tension:.3}}]}},
  options: defOpts('Потребление RAM (МБ)')
}});
new Chart(document.getElementById('cpuChart'), {{
  type:'line',
  data:{{labels, datasets:[
    {{label:'CPU сырое (%)', data:cpus, borderColor:'#e67e22', backgroundColor:'rgba(230,126,34,.12)', fill:false, tension:.3}},
    {{label:'CPU норм. (%)', data:cpusNorm, borderColor:'#8e44ad', backgroundColor:'rgba(142,68,173,.15)', fill:true, tension:.3}}
  ]}},
  options: {{
    responsive: true,
    plugins: {{legend:{{display:true}}, title:{{display:true, text:'Нагрузка на CPU (%)'}}}},
    scales: {{y:{{beginAtZero:true}}}}
  }}
}});
</script>
</body>
</html>"""
        return html

    # ---------------------- Диалог после теста ----------------------

    def _show_post_test_dialog(self, html_path, ts):
        """Shows dialog after test completion: open report, new test, or exit."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Тест завершён")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.focus_set()

        ttk.Label(dlg, text="Тест завершён!", font=("Arial", 12, "bold")).pack(
            pady=(24, 6), padx=40)
        ttk.Label(dlg, text="Что делать дальше?", foreground="#555").pack(pady=(0, 20))

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(pady=(0, 24), padx=40)

        def show_report():
            webbrowser.open(str(html_path))
            dlg.destroy()
            if messagebox.askyesno("Сохранить копию", "Сохранить копию HTML-отчёта?"):
                save_path = filedialog.asksaveasfilename(
                    defaultextension=".html",
                    filetypes=[("HTML files", "*.html"), ("All files", "*.*")],
                    initialfile=f"Performance_Report_{ts}.html"
                )
                if save_path:
                    shutil.copy(str(html_path), save_path)
                    self.add_test_log(f"📎 Копия отчёта сохранена: {save_path}")

        def new_test():
            dlg.destroy()
            self._reset_test_state()

        def exit_app():
            dlg.destroy()
            self.root.quit()

        ttk.Button(btn_frame, text="📊 Показать отчёт", command=show_report, width=20
                   ).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🔄 Новый тест", command=new_test, width=14
                   ).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="❌ Выход", command=exit_app, width=10
                   ).pack(side=tk.LEFT, padx=5)

        dlg.update_idletasks()
        w = dlg.winfo_reqwidth()
        h = dlg.winfo_reqheight()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")

    def _reset_test_state(self):
        """Clears the test log and resets the status bar for a new run."""
        self.test_log.delete("1.0", tk.END)
        self.status_var.set("Готов")
        self.add_test_log("🔄 Готов к новому тесту.")

    # ---------------------- Сравнение версий ----------------------

    def _load_comparison_settings(self):
        path = BASE_DIR / "last_comparison_settings.json"
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"custom_names": {}, "last_selected_files": [], "last_base_version": ""}

    def _save_comparison_settings(self, settings):
        path = BASE_DIR / "last_comparison_settings.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def compare_versions(self):
        """Opens dialog to select 2-10 performance JSON files and builds a comparison report."""
        MAX_FILES = 10
        COLORS = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6',
                  '#1abc9c', '#e67e22', '#c0392b', '#16a085', '#f1c40f']

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
        dlg = tk.Toplevel(self.root)
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
            color = COLORS[idx % len(COLORS)]

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
            lbl.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)

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
        w = max(600, dlg.winfo_reqwidth())
        h = min(700, max(440, dlg.winfo_reqheight()))
        sx = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        sy = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{sx}+{sy}")

    def _generate_comparison_html(self, datasets, base_path_str):
        """Builds comparison HTML for 2-10 performance datasets.

        Args:
            datasets: list of dicts {path: str, version: str, data: dict}
            base_path_str: path string of the dataset used as baseline
        """
        COLORS = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6',
                  '#1abc9c', '#e67e22', '#c0392b', '#16a085', '#f1c40f']
        ALPHA  = ["rgba(52,152,219,.15)", "rgba(231,76,60,.15)", "rgba(46,204,113,.15)",
                  "rgba(243,156,18,.15)", "rgba(155,89,182,.15)", "rgba(26,188,156,.15)",
                  "rgba(230,126,34,.15)", "rgba(192,57,43,.15)", "rgba(22,160,133,.15)",
                  "rgba(241,196,15,.15)"]

        # Collect operation names in the order they first appear
        seen, op_names = set(), []
        for ds in datasets:
            for r in ds["data"].get("results", []):
                if r["name"] not in seen:
                    op_names.append(r["name"])
                    seen.add(r["name"])

        for ds in datasets:
            ds["lookup"] = {r["name"]: r for r in ds["data"].get("results", [])}

        base_ds = next(ds for ds in datasets if ds["path"] == base_path_str)
        legend_pos = "right" if len(datasets) >= 6 else "top"

        # Chart datasets
        time_ds, ram_ds, cpu_ds = [], [], []
        for i, ds in enumerate(datasets):
            is_base = ds["path"] == base_path_str
            lbl = ds["version"] + (" (база)" if is_base else "")
            lk  = ds["lookup"]
            time_ds.append({
                "label": lbl,
                "data": [round(lk[op]["time"], 3) if op in lk else None for op in op_names],
                "backgroundColor": COLORS[i % len(COLORS)],
                "borderRadius": 3,
            })
            ram_ds.append({
                "label": lbl,
                "data": [lk[op].get("ram") if op in lk else None for op in op_names],
                "borderColor": COLORS[i % len(COLORS)],
                "backgroundColor": ALPHA[i % len(ALPHA)],
                "tension": 0.3, "fill": True,
            })
            cpu_ds.append({
                "label": lbl,
                "data": [lk[op].get("cpu") if op in lk else None for op in op_names],
                "borderColor": COLORS[i % len(COLORS)],
                "backgroundColor": ALPHA[i % len(ALPHA)],
                "tension": 0.3, "fill": False,
            })

        labels_json  = json.dumps(op_names, ensure_ascii=False)
        time_ds_json = json.dumps(time_ds,  ensure_ascii=False)
        ram_ds_json  = json.dumps(ram_ds,   ensure_ascii=False)
        cpu_ds_json  = json.dumps(cpu_ds,   ensure_ascii=False)

        # Table header (two rows)
        th1 = "<tr><th rowspan='2'>Операция</th>"
        th2 = "<tr>"
        for i, ds in enumerate(datasets):
            is_base = ds["path"] == base_path_str
            suffix  = " <em>(база)</em>" if is_base else ""
            color   = COLORS[i % len(COLORS)]
            th1 += f'<th colspan="2" style="background:{color}">{ds["version"]}{suffix}</th>'
            th2 += (f'<th style="background:{color}">Время (сек)</th>'
                    f'<th style="background:{color}">Δ%</th>')
        th1 += "</tr>"
        th2 += "</tr>"

        # Table rows
        def delta_td(t, base_t, is_base):
            if is_base or base_t is None:
                return "<td class='delta-base'>—</td>"
            if t is None:
                return "<td>—</td>"
            pct = (t - base_t) / base_t * 100
            if abs(pct) <= 5:
                return f"<td class='delta-same'>{'+'if pct>0 else ''}{pct:.1f}%</td>"
            if pct < 0:
                return f"<td class='delta-better'>{pct:.1f}%</td>"
            return f"<td class='delta-worse'>+{pct:.1f}%</td>"

        table_rows = ""
        for op in op_names:
            base_r = base_ds["lookup"].get(op)
            base_t = base_r["time"] if base_r else None
            row = f"<tr><td>{op}</td>"
            for ds in datasets:
                r = ds["lookup"].get(op)
                t = r["time"] if r else None
                row += f"<td>{'—' if t is None else f'{t:.3f}'}</td>"
                row += delta_td(t, base_t, ds["path"] == base_path_str)
            row += "</tr>"
            table_rows += row + "\n"

        # System info cards
        sys_cards = ""
        for i, ds in enumerate(datasets):
            color    = COLORS[i % len(COLORS)]
            is_base  = ds["path"] == base_path_str
            sys_info = ds["data"].get("system", {})
            summ     = ds["data"].get("summary", {})
            ts_raw   = ds["data"].get("timestamp", "")
            base_lbl = " <strong>(база)</strong>" if is_base else ""
            pr  = summ.get("peak_ram_mb")
            ar  = summ.get("avg_ram_mb")
            pc  = summ.get("peak_cpu_pct")
            sys_cards += (
                f'<div class="sys-card" style="border-left:4px solid {color}">'
                f'<div class="sys-title" style="color:{color}">'
                f'{ds["version"]}{base_lbl}</div>'
                f'<div class="sys-row"><span class="sys-lbl">ОС</span>'
                f'<span>{sys_info.get("os","—")}</span></div>'
                f'<div class="sys-row"><span class="sys-lbl">RAM</span>'
                f'<span>{sys_info.get("ram_total_gb","—")} ГБ</span></div>'
                f'<div class="sys-row"><span class="sys-lbl">Пик RAM</span>'
                f'<span>{"—" if pr is None else f"{pr:.1f} МБ"}</span></div>'
                f'<div class="sys-row"><span class="sys-lbl">Средн. RAM</span>'
                f'<span>{"—" if ar is None else f"{ar:.1f} МБ"}</span></div>'
                f'<div class="sys-row"><span class="sys-lbl">Пик CPU</span>'
                f'<span>{"—" if pc is None else f"{pc:.1f}%"}</span></div>'
                f'<div class="sys-row"><span class="sys-lbl">Дата теста</span>'
                f'<span>{ts_raw}</span></div>'
                f'</div>'
            )

        # Legend
        legend_items = ""
        for i, ds in enumerate(datasets):
            is_base = ds["path"] == base_path_str
            suffix  = " (база)" if is_base else ""
            legend_items += (
                f'<div class="legend-item">'
                f'<span class="legend-dot" style="background:{COLORS[i%len(COLORS)]}"></span>'
                f'<span>{ds["version"]}{suffix}</span></div>\n'
            )

        base_version = base_ds["version"]
        ts_display   = datetime.now().strftime("%d.%m.%Y %H:%M")

        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Сравнение версий R7-Office</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body{{font-family:Arial,sans-serif;margin:0;padding:20px;background:#f5f6fa;color:#333}}
  h1{{color:#2c3e50;margin-bottom:2px}}
  h2{{color:#2c3e50;margin-top:28px;margin-bottom:10px}}
  .subtitle{{color:#888;font-size:.9em;margin-bottom:14px}}
  .legend{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px}}
  .legend-item{{display:flex;align-items:center;gap:6px;font-size:.88em}}
  .legend-dot{{width:13px;height:13px;border-radius:50%;flex-shrink:0}}
  .sys-cards{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:8px}}
  .sys-card{{background:#fff;padding:14px 16px;border-radius:8px;
    box-shadow:0 1px 4px rgba(0,0,0,.12);min-width:200px;flex:1}}
  .sys-title{{font-weight:bold;font-size:.9em;margin-bottom:8px}}
  .sys-row{{display:flex;gap:8px;font-size:.82em;margin-bottom:3px;color:#444}}
  .sys-lbl{{color:#888;min-width:90px}}
  .charts{{display:grid;grid-template-columns:1fr;gap:18px;margin-bottom:28px}}
  .chart-box{{background:#fff;padding:16px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.12)}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;
    overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.12);margin-bottom:16px}}
  th{{color:#fff;padding:8px 10px;text-align:center;font-size:.8em}}
  th:first-child{{background:#2c3e50;text-align:left}}
  td{{padding:7px 10px;border-bottom:1px solid #eee;font-size:.87em;text-align:center}}
  td:first-child{{text-align:left;font-weight:500}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#f0f4ff}}
  .delta-better{{color:#27ae60;font-weight:bold}}
  .delta-worse{{color:#e74c3c;font-weight:bold}}
  .delta-same{{color:#e67e22}}
  .delta-base{{color:#aaa}}
  .legend-note{{font-size:.82em;color:#555;margin-bottom:10px;padding:8px 12px;
    background:#fff;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
  .pdf-btn{{position:fixed;top:16px;right:16px;padding:8px 18px;background:#2c3e50;
    color:#fff;border:none;border-radius:6px;font-size:.9em;cursor:pointer;
    box-shadow:0 2px 6px rgba(0,0,0,.25);z-index:1000}}
  .pdf-btn:hover{{background:#34495e}}
  @media print{{
    .pdf-btn{{display:none}}
    body{{background:#fff}}
    canvas,.chart-box,table{{page-break-inside:avoid}}
    h1,h2,h3{{page-break-after:avoid}}
  }}
</style>
</head>
<body>
<button class="pdf-btn" onclick="window.print()">📄 Сохранить как PDF</button>
<h1>Сравнение версий R7-Office</h1>
<div class="subtitle">Сформировано: {ts_display} &nbsp;|&nbsp; Базовая версия: <strong>{base_version}</strong></div>
<div class="legend">
{legend_items}</div>

<h2>Сведения о системе</h2>
<div class="sys-cards">{sys_cards}</div>

<h2>Графики производительности</h2>
<div class="charts">
  <div class="chart-box"><canvas id="timeChart"></canvas></div>
  <div class="chart-box"><canvas id="ramChart"></canvas></div>
  <div class="chart-box"><canvas id="cpuChart"></canvas></div>
</div>

<h2>Детальное сравнение</h2>
<div class="legend-note">
  <span class="delta-better">Зелёный</span> — быстрее базовой версии &nbsp;&nbsp;
  <span class="delta-worse">Красный</span> — медленнее &nbsp;&nbsp;
  <span class="delta-same">Оранжевый</span> — разница ≤ 5%
</div>
<table>
<thead>{th1}{th2}</thead>
<tbody>
{table_rows}</tbody>
</table>

<script>
const labels = {labels_json};
new Chart(document.getElementById('timeChart'), {{
  type:'bar', data:{{labels, datasets:{time_ds_json}}},
  options:{{responsive:true,
    plugins:{{title:{{display:true,text:'Время выполнения операций (сек)'}},legend:{{position:'{legend_pos}'}}}},
    scales:{{y:{{beginAtZero:true}}}}}}
}});
new Chart(document.getElementById('ramChart'), {{
  type:'line', data:{{labels, datasets:{ram_ds_json}}},
  options:{{responsive:true,
    plugins:{{title:{{display:true,text:'Потребление RAM (МБ)'}},legend:{{position:'{legend_pos}'}}}},
    scales:{{y:{{beginAtZero:false}}}}}}
}});
new Chart(document.getElementById('cpuChart'), {{
  type:'line', data:{{labels, datasets:{cpu_ds_json}}},
  options:{{responsive:true,
    plugins:{{title:{{display:true,text:'Нагрузка на CPU (%)'}},legend:{{position:'{legend_pos}'}}}},
    scales:{{y:{{beginAtZero:true}}}}}}
}});
</script>
</body>
</html>"""

    # ---------------------- Batch-режим ----------------------

    def run_batch_mode(self):
        """Entry point for Batch mode — validates prerequisites then shows config dialog."""
        if not ctypes.windll.shell32.IsUserAnAdmin():
            messagebox.showerror(
                "Ошибка прав",
                "Batch-режим требует прав администратора.\n"
                "Перезапустите программу от имени администратора."
            )
            return
        if not PYAUTOGUI_OK or not pyperclip or not EXCEL_OK or not WIN32_OK:
            missing = []
            if not PYAUTOGUI_OK: missing.append("pyautogui")
            if not pyperclip:    missing.append("pyperclip")
            if not EXCEL_OK:     missing.append("openpyxl")
            if not WIN32_OK:     missing.append("pywin32")
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
        dlg.title("Batch-режим")
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(dlg, text=f"Найдено дистрибутивов: {len(files)}",
                  font=("Arial", 10, "bold")).pack(pady=(14, 4), padx=16, anchor=tk.W)

        # ── Список версий ─────────────────────────────────────────────────────
        ver_frame = ttk.LabelFrame(dlg, text="Выберите версии для тестирования", padding="8")
        ver_frame.pack(fill=tk.BOTH, padx=16, pady=4)

        ver_vars = {}
        for f in files:
            var = tk.BooleanVar(value=True)
            ver_vars[f] = var
            ttk.Checkbutton(ver_frame, text=f.name, variable=var).pack(anchor=tk.W, pady=1)

        mini = ttk.Frame(ver_frame)
        mini.pack(fill=tk.X, pady=(4, 0))
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
        for sd in [self.test_files_folder, BASE_DIR, Path.home() / "Downloads", Path.home() / "Загрузки"]:
            if not sd.exists():
                continue
            for pat in ["файл-для-теста-Р7-офис-50К*.xlsx", "*50К*.xlsx"]:
                for found in sd.glob(pat):
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
        """Creates the progress window and launches the batch worker thread."""
        prog = tk.Toplevel(self.root)
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

        log_text = tk.Text(log_frame, font=("Consolas", 9), wrap=tk.WORD)
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

        threading.Thread(
            target=self._batch_worker,
            args=(versions, test_file, stop_on_error, cleanup,
                  _log, _set_current, _set_ver_status, _set_progress,
                  _on_done, stop_event, pause_event),
            daemon=True
        ).start()

    def _batch_worker(self, versions, test_file, stop_on_error, cleanup,
                      log_cb, current_cb, ver_status_cb, progress_cb,
                      done_cb, stop_event, pause_event):
        """Batch worker thread: install each version, run tests, collect results."""
        batch_results = []
        errors = 0
        log_cb(f"🚀 Запуск Batch-режима: найдено {len(versions)} версий")

        for idx, dist_file in enumerate(versions):
            if stop_event.is_set():
                log_cb("⏹ Остановлено пользователем.")
                break

            ver_name = self._extract_version(dist_file.stem) or dist_file.stem
            current_cb(f"Текущая версия: {ver_name} ({idx + 1} из {len(versions)})")
            ver_status_cb(dist_file, f"🔄 {dist_file.name}: выполняется...")
            log_cb(f"--- Версия {idx + 1}/{len(versions)}: {dist_file.name} ---")

            result = {
                "file":            dist_file.name,
                "version":         ver_name,
                "success":         False,
                "error":           None,
                "open_elapsed":    None,
                "vlookup_elapsed": None,
                "peak_ram":        None,
                "avg_ram":         None,
                "peak_cpu":        None,
            }

            try:
                log_cb("🗑️ Удаление текущей версии...")
                self.uninstall_current_version()
                time.sleep(2)

                log_cb(f"📥 Установка {dist_file.name}...")
                if not self.install_version(dist_file):
                    raise RuntimeError("Установка не завершилась за 5 минут")
                self.detect_current_version()
                ver_display = (self.current_version_info.get("name")
                               if self.current_version_info else ver_name)
                log_cb(f"✅ Установлена: {ver_display}")

                if pause_event.is_set():
                    log_cb("⏸ Пауза...")
                    pause_event.wait()
                    log_cb("▶ Продолжение...")
                if stop_event.is_set():
                    break

                if cleanup:
                    cleared = self._clear_r7_cache()
                    if cleared:
                        log_cb(f"🧹 Очищено {cleared} объектов кеша")

                test_result = self._batch_run_single_version(
                    test_file, ver_display, log_cb, stop_event, pause_event)

                if test_result:
                    result.update(test_result)
                    result["success"] = True
                    ot  = result.get("open_elapsed") or 0
                    vt  = result.get("vlookup_elapsed")
                    vts = f", ВПР {vt:.2f} сек" if vt else ""
                    log_cb(f"✅ {ver_name}: открытие {ot:.2f} сек{vts}")
                    ver_status_cb(dist_file,
                                  f"✅ {dist_file.name}: {ot:.1f} сек"
                                  + (f" / ВПР {vt:.1f} сек" if vt else ""))
                else:
                    raise RuntimeError("Тест не вернул результатов")

            except Exception as e:
                errors += 1
                result["error"] = str(e)
                log_cb(f"❌ {ver_name}: ошибка — {e}")
                ver_status_cb(dist_file, f"❌ {dist_file.name}: ошибка")
                if stop_on_error:
                    log_cb("🛑 Остановка (включена опция 'стоп при ошибке')")
                    batch_results.append(result)
                    break

            batch_results.append(result)
            progress_cb(idx + 1)

            if pause_event.is_set():
                log_cb("⏸ Пауза между версиями...")
                pause_event.wait()
                log_cb("▶ Продолжение...")

        done_cb(batch_results, errors)

    def _batch_run_single_version(self, test_file, version_label, log_cb,
                                  stop_event, pause_event):
        """Runs the full 12-operation stress test for the currently installed version.

        Returns a result dict with timing/resource data, or None on critical failure.
        """
        r7_path = self._find_r7_path()
        if not r7_path:
            log_cb("❌ Р7-Офис не найден.")
            return None

        # ── Оконные вспомогательные функции ──────────────────────────────────
        if WIN32_OK:
            import win32gui as _wg
            import win32con as _wc

        def _find_hwnd():
            if not WIN32_OK:
                return None
            wins = []
            def _cb(h, _):
                if _wg.IsWindowVisible(h):
                    t = _wg.GetWindowText(h)
                    if "Р7-Офис" in t or test_file.stem[:12] in t:
                        wins.append(h)
            _wg.EnumWindows(_cb, wins)
            return wins[0] if wins else None

        def _focus():
            hwnd = _find_hwnd()
            if hwnd:
                try:
                    _wg.SetForegroundWindow(hwnd)
                    time.sleep(0.2)
                    return True
                except Exception:
                    pass
            return not WIN32_OK

        def _maximize():
            hwnd = _find_hwnd()
            if hwnd and WIN32_OK:
                _wg.ShowWindow(hwnd, _wc.SW_MAXIMIZE)
                time.sleep(0.5)

        def _close_update_dlg():
            self._close_update_dialog_if_exists(log_cb=log_cb)

        def _hk(*keys):
            pyautogui.hotkey(*keys, interval=0.1)

        def _pr(key, n=1):
            for _ in range(n):
                pyautogui.press(key)
                time.sleep(0.1)

        # ── Открытие Р7-Офис ──────────────────────────────────────────────────
        log_cb(f"▶ Запуск Р7-Офис: {test_file.name}")
        open_start = time.time()
        subprocess.Popen([r7_path, str(test_file)], shell=True)

        deadline = time.time() + 60
        while time.time() < deadline:
            if _find_hwnd():
                break
            time.sleep(0.5)
        else:
            log_cb("❌ Окно Р7-Офис не появилось.")
            return None

        _maximize()
        _focus()
        time.sleep(1)
        _close_update_dlg()
        time.sleep(0.5)

        # Фоновый мониторинг окна обновления на весь период теста
        _upd_stop = threading.Event()
        threading.Thread(
            target=self._monitor_update_dialog,
            args=(_upd_stop,),
            kwargs={"log_cb": log_cb},
            daemon=True,
        ).start()
        log_cb("🔍 Запущен мониторинг окна обновления (проверка каждые 2 сек)")

        data_ready   = self._wait_data_ready_for_test(open_start, _find_hwnd(), timeout=120)
        open_elapsed = time.time() - open_start
        log_cb(f"✅ Файл открыт за {open_elapsed:.2f} сек")
        _focus()

        # ── Мониторинг ресурсов ───────────────────────────────────────────────
        self._r7_pids = None
        self._x2t_logged_pids = set()  # сбросить дедуп x2t перед новым тестом
        r7_procs = self._get_r7_processes(log_cb=log_cb)

        sample0 = self._sample_r7_resources(r7_procs)
        results = [{
            "name": "Открытие файла", "time": open_elapsed, "error": None,
            "ram":            sample0["ram_mb"]       if sample0 else None,
            "cpu":            sample0["cpu_raw_pct"]   if sample0 else None,
            "cpu_normalized": sample0["cpu_norm_pct"]  if sample0 else None,
            "threads":        sample0["threads"]       if sample0 else None,
            "uptime_sec":     sample0["uptime_sec"]    if sample0 else None,
        }]

        def measure(name, func):
            nonlocal r7_procs
            if stop_event.is_set():
                return
            if pause_event.is_set():
                log_cb("⏸ Пауза...")
                pause_event.wait()
                log_cb("▶ Продолжение...")
            log_cb(f"⏳ {name}...")
            _focus()
            t0  = time.time()
            err = None
            try:
                func()
            except Exception as e:
                err = str(e)
            elapsed = time.time() - t0
            time.sleep(0.5)
            self._r7_pids = None
            r7_procs = self._get_r7_processes(log_cb=log_cb)
            sample = self._sample_r7_resources(r7_procs)
            self._log_resources(sample, log_cb=log_cb)
            log_cb(f"   ✅ {name}: {elapsed:.2f} сек" + (f" (ошибка: {err})" if err else ""))
            results.append({
                "name": name, "time": elapsed, "error": err,
                "ram":            sample["ram_mb"]      if sample else None,
                "cpu":            sample["cpu_raw_pct"]  if sample else None,
                "cpu_normalized": sample["cpu_norm_pct"] if sample else None,
                "threads":        sample["threads"]      if sample else None,
                "uptime_sec":     sample["uptime_sec"]    if sample else None,
            })

        # ── Тест-функции (зеркало _spreadsheet_worker) ────────────────────────
        def paste_big():
            _hk('shift', 'f11')
            _hk('ctrl', 'v')

        def add_col_hk():
            _hk('ctrl', 'pageup')
            time.sleep(0.1)
            pyautogui.press('right')
            _hk('ctrl', 'shift', '=')

        def add_col_menu():
            _hk('ctrl', 'pageup')
            time.sleep(0.1)
            pyautogui.press('right')
            _hk('alt', 'i')
            _pr('c')

        def paste_hk(cell_count, paste_offset):
            _hk('ctrl', 'home')
            for _ in range(cell_count - 1):
                pyautogui.hotkey('shift', 'right')
            _hk('ctrl', 'c')
            pyautogui.press('right', presses=paste_offset)
            _hk('ctrl', 'v')

        def paste_pkm(cell_count, paste_offset):
            _hk('ctrl', 'home')
            for _ in range(cell_count - 1):
                pyautogui.hotkey('shift', 'right')
            pyautogui.click(button='right')
            _pr('down', 2)
            _pr('enter')
            pyautogui.press('right', presses=paste_offset)
            pyautogui.click(button='right')
            _pr('down', 3)
            _pr('enter')

        def vlookup():
            _hk('ctrl', 'pagedown')
            time.sleep(0.1)
            _hk('ctrl', 'home')
            pyperclip.copy('=VLOOKUP(A2;Лист1!A:B;2;FALSE)')
            _hk('ctrl', 'v')
            _pr('enter')
            _hk('ctrl', 'shift', 'down')
            _hk('ctrl', 'd')
            time.sleep(0.5)

        def del_col():
            _hk('ctrl', 'home')
            pyautogui.press('right')
            pyautogui.press('delete')

        # ── Выполнение тестов ─────────────────────────────────────────────────
        measure("Выделение всех ячеек (Ctrl+A)",      lambda: _hk('ctrl', 'a'))
        measure("Копирование всех ячеек (Ctrl+C)",     lambda: _hk('ctrl', 'c'))
        measure("Вставка большого массива (Ctrl+V)",    paste_big)
        measure("Добавление нового листа",              lambda: _hk('shift', 'f11'))
        measure("Добавление столбца (горячие клавиши)", add_col_hk)
        measure("Добавление столбца (меню Вставка)",    add_col_menu)
        measure("Вставка 1 ячейки (горячие клавиши)",   lambda: paste_hk(1, 10))
        measure("Вставка 5 ячеек (горячие клавиши)",    lambda: paste_hk(5, 15))
        measure("Вставка 1 ячейки (ПКМ)",               lambda: paste_pkm(1, 10))
        measure("Вставка 5 ячеек (ПКМ)",                lambda: paste_pkm(5, 15))
        measure("Функция ВПР (50K строк)",              vlookup)
        measure("Удаление столбца (Del)",               del_col)

        # ── Статистика ────────────────────────────────────────────────────────
        ram_vals      = [r["ram"] for r in results if r.get("ram") is not None]
        cpu_vals      = [r["cpu"] for r in results if r.get("cpu") is not None]
        cpu_norm_vals = [r["cpu_normalized"] for r in results if r.get("cpu_normalized") is not None]
        peak_ram = max(ram_vals) if ram_vals else None
        avg_ram  = round(sum(ram_vals) / len(ram_vals), 1) if ram_vals else None
        peak_cpu = max(cpu_vals) if cpu_vals else None
        peak_cpu_norm = max(cpu_norm_vals) if cpu_norm_vals else None
        avg_cpu_norm  = round(sum(cpu_norm_vals) / len(cpu_norm_vals), 1) if cpu_norm_vals else None

        # ── Закрытие Р7-Офис ──────────────────────────────────────────────────
        _upd_stop.set()
        log_cb("🔍 Мониторинг окна обновления остановлен")
        log_cb("🔚 Закрытие Р7-Офис...")
        try:
            _hk('alt', 'f4')
            time.sleep(1)
            _pr('right')
            _pr('enter')
        except Exception:
            pass

        # ── Сохранение JSON ───────────────────────────────────────────────────
        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = self.reports_folder / f"performance_full_{ts_now}.json"
        sys_mem_gb = (round(psutil.virtual_memory().total / (1024 ** 3), 1)
                      if PSUTIL_OK else None)
        try:
            with open(json_path, "w", encoding="utf-8") as jf:
                json.dump({
                    "timestamp": ts_now,
                    "version":   version_label,
                    "test_file": str(test_file),
                    "system": {
                        "os": platform.platform(),
                        "ram_total_gb": sys_mem_gb,
                        "cpu_model": platform.processor() or None,
                    },
                    "summary": {
                        "peak_ram_mb": peak_ram, "avg_ram_mb": avg_ram,
                        "min_ram_mb":  min(ram_vals) if ram_vals else None,
                        "peak_cpu_pct": peak_cpu,
                        "peak_cpu_normalized_pct": peak_cpu_norm,
                        "avg_cpu_normalized_pct": avg_cpu_norm,
                    },
                    "results": results,
                }, jf, indent=2, ensure_ascii=False)
            log_cb(f"📄 JSON сохранён: {json_path.name}")
        except Exception as e:
            log_cb(f"⚠️ Ошибка сохранения JSON: {e}")

        vpr_r = next((r for r in results if r["name"] == "Функция ВПР (50K строк)"), None)
        return {
            "open_elapsed":     open_elapsed,
            "vlookup_elapsed":  vpr_r["time"] if vpr_r else None,
            "peak_ram":         peak_ram,
            "avg_ram":          avg_ram,
            "peak_cpu":         peak_cpu,
            "peak_cpu_normalized": peak_cpu_norm,
            "results":          results,
            "json_path":        str(json_path),
        }

    def _generate_batch_summary_html(self, batch_results):
        """Builds summary HTML report for all batch results."""
        ts_display = datetime.now().strftime("%d.%m.%Y %H:%M")
        n          = len(batch_results)
        versions   = [r["version"] for r in batch_results]

        open_times = [r.get("open_elapsed")    for r in batch_results]
        vpr_times  = [r.get("vlookup_elapsed") for r in batch_results]
        peak_rams  = [r.get("peak_ram")        for r in batch_results]
        peak_cpus  = [r.get("peak_cpu")        for r in batch_results]

        def _idx_best(vals):
            valid = [(v, i) for i, v in enumerate(vals) if v is not None]
            return min(valid, key=lambda x: x[0])[1] if valid else -1

        def _idx_worst(vals):
            valid = [(v, i) for i, v in enumerate(vals) if v is not None]
            return max(valid, key=lambda x: x[0])[1] if valid else -1

        bi_open, wi_open = _idx_best(open_times), _idx_worst(open_times)
        bi_vpr,  wi_vpr  = _idx_best(vpr_times),  _idx_worst(vpr_times)
        bi_ram,  wi_ram  = _idx_best(peak_rams),   _idx_worst(peak_rams)

        def _cell(val, i, bi, wi, fmt=".2f"):
            if val is None:
                return "<td>—</td>"
            style = (' style="background:#d4edda;font-weight:bold;color:#155724"' if i == bi
                     else ' style="background:#f8d7da;color:#721c24"' if i == wi else "")
            return f"<td{style}>{val:{fmt}}</td>"

        rows_html = ""
        for i, r in enumerate(batch_results):
            status  = "✅ OK" if r.get("success") else f"❌ {str(r.get('error',''))[:50]}"
            pc      = r.get("peak_cpu")
            pc_cell = f"<td>{'—' if pc is None else f'{pc:.0f}'}</td>"
            rows_html += (
                f"<tr><td>{r['version']}</td>"
                + _cell(r.get("open_elapsed"),    i, bi_open, wi_open, ".2f")
                + _cell(r.get("vlookup_elapsed"), i, bi_vpr,  wi_vpr,  ".2f")
                + _cell(r.get("peak_ram"),        i, bi_ram,  wi_ram,  ".0f")
                + pc_cell
                + f"<td>{status}</td></tr>\n"
            )

        labels_json = json.dumps(versions, ensure_ascii=False)
        open_json   = json.dumps(open_times)
        vpr_json    = json.dumps(vpr_times)
        ram_json    = json.dumps(peak_rams)

        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Batch-отчёт R7-Office</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body{{font-family:Arial,sans-serif;margin:0;padding:20px;background:#f5f6fa;color:#333}}
  h1{{color:#2c3e50;margin-bottom:2px}}
  h2{{color:#2c3e50;margin-top:28px;margin-bottom:10px}}
  .subtitle{{color:#888;font-size:.9em;margin-bottom:16px}}
  .legend-note{{font-size:.82em;color:#555;margin-bottom:12px;padding:8px 12px;
    background:#fff;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
  .charts{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:28px}}
  @media(max-width:700px){{.charts{{grid-template-columns:1fr}}}}
  .chart-box{{background:#fff;padding:16px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.12)}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;
    overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.12)}}
  th{{background:#2c3e50;color:#fff;padding:9px 12px;text-align:left;font-size:.85em}}
  td{{padding:8px 12px;border-bottom:1px solid #eee;font-size:.88em;text-align:center}}
  td:first-child{{text-align:left;font-weight:500}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#f8f9ff}}
  .pdf-btn{{position:fixed;top:16px;right:16px;padding:8px 18px;background:#2c3e50;
    color:#fff;border:none;border-radius:6px;font-size:.9em;cursor:pointer;
    box-shadow:0 2px 6px rgba(0,0,0,.25);z-index:1000}}
  .pdf-btn:hover{{background:#34495e}}
  @media print{{
    .pdf-btn{{display:none}}
    body{{background:#fff}}
    canvas,.chart-box,table{{page-break-inside:avoid}}
    h1,h2,h3{{page-break-after:avoid}}
  }}
</style>
</head>
<body>
<button class="pdf-btn" onclick="window.print()">📄 Сохранить как PDF</button>
<h1>Batch-отчёт R7-Office</h1>
<div class="subtitle">Сформировано: {ts_display} &nbsp;|&nbsp; Протестировано версий: {n}</div>
<div class="legend-note">
  <span style="background:#d4edda;color:#155724;font-weight:bold;padding:2px 6px;border-radius:3px">Зелёный</span>
  — лучший результат в столбце &nbsp;&nbsp;
  <span style="background:#f8d7da;color:#721c24;padding:2px 6px;border-radius:3px">Красный</span>
  — худший
</div>

<h2>Сводная таблица</h2>
<table>
<thead><tr>
  <th>Версия</th><th>Открытие (сек)</th><th>ВПР (сек)</th>
  <th>Пик RAM (МБ)</th><th>Пик CPU (%)</th><th>Статус</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>

<h2>Графики</h2>
<div class="charts">
  <div class="chart-box"><canvas id="openChart"></canvas></div>
  <div class="chart-box"><canvas id="vprChart"></canvas></div>
  <div class="chart-box"><canvas id="ramChart"></canvas></div>
</div>

<script>
const labels = {labels_json};
const defOpts = t => ({{
  responsive:true,
  plugins:{{legend:{{display:false}},title:{{display:true,text:t}}}},
  scales:{{y:{{beginAtZero:false}}}}
}});
new Chart(document.getElementById('openChart'),{{type:'bar',
  data:{{labels,datasets:[{{label:'сек',data:{open_json},backgroundColor:'#3498db',borderRadius:4}}]}},
  options:defOpts('Открытие файла (сек)')}});
new Chart(document.getElementById('vprChart'),{{type:'bar',
  data:{{labels,datasets:[{{label:'сек',data:{vpr_json},backgroundColor:'#27ae60',borderRadius:4}}]}},
  options:defOpts('Функция ВПР (сек)')}});
new Chart(document.getElementById('ramChart'),{{type:'bar',
  data:{{labels,datasets:[{{label:'МБ',data:{ram_json},backgroundColor:'#e67e22',borderRadius:4}}]}},
  options:defOpts('Пик RAM (МБ)')}});
</script>
</body>
</html>"""

    # --- Хранилище последних параметров тестового файла ---
    _LAST_PARAMS_FILE = "last_test_params.json"

    def _load_last_params(self):
        """Returns dict with last used rows/cols/filename, or defaults."""
        path = BASE_DIR / self._LAST_PARAMS_FILE
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"rows": 50000, "cols": 50, "filename": "test_data_50000x50.xlsx"}

    def _save_last_params(self, rows, cols, filename):
        """Persists rows/cols/filename to last_test_params.json."""
        path = BASE_DIR / self._LAST_PARAMS_FILE
        try:
            path.write_text(
                json.dumps({"rows": rows, "cols": cols, "filename": filename},
                           indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception:
            pass

    def compare_file_sizes(self):
        """Opens the test-file generation dialog with 4 separate action buttons."""
        last = self._load_last_params()

        dlg = tk.Toplevel(self.root)
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
        ttk.Label(dlg, text="(1 000 – 1 000 000)", foreground="#888").grid(
            row=0, column=2, sticky=tk.W, padx=(0, 16))

        # ── Столбцы ─────────────────────────────────────────────────────────
        ttk.Label(dlg, text="Количество столбцов:").grid(
            row=1, column=0, sticky=tk.W, **PAD)
        cols_var = tk.StringVar(value=str(last.get("cols", 50)))
        cols_entry = ttk.Entry(dlg, textvariable=cols_var, width=14)
        cols_entry.grid(row=1, column=1, sticky=tk.W, **PAD)
        ttk.Label(dlg, text="(1 – 100)", foreground="#888").grid(
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
                               foreground="#555")
        status_lbl.grid(row=7, column=0, columnspan=3, sticky=tk.EW,
                        padx=16, pady=(10, 14))

        # ── Вспомогательные функции ──────────────────────────────────────────
        _action_btns = [btn_create, btn_test]

        def _set_status(text, color="#555"):
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
            _lock()
            _set_status("⏳ Тестирование...", "#2980b9")

            def _done(success):
                _set_status(
                    "✅ Тест завершён" if success else "❌ Тест завершён с ошибкой",
                    "#27ae60" if success else "#e74c3c")
                _unlock()

            threading.Thread(
                target=self._worker_run_test,
                args=(file_path, r, c, _done),
                daemon=True
            ).start()

        btn_create.config(command=on_create)
        btn_choose.config(command=on_choose)
        btn_test  .config(command=on_test)

        dlg.columnconfigure(1, weight=1)
        rows_entry.focus_set()
        dlg.wait_window()

    def _worker_run_test(self, file_path, rows, cols, done_cb):
        """Worker: kills stale R7 instances, clears cache, opens file, runs VPR, shows report."""
        success = False
        try:
            # ----- 1. Завершаем старые процессы Р7 ---------------------------------------
            killed = self._kill_r7_processes_for_test()
            if killed:
                self.add_test_log(f"🔄 Завершено {killed} процессов Р7-Офис")
                time.sleep(2)
            else:
                self.add_test_log("ℹ️ Активных процессов Р7-Офис не найдено")

            # ----- 2. Очистка кеша -------------------------------------------------------
            cleared = self._clear_r7_cache()
            if cleared:
                self.add_test_log(f"🧹 Очищено {cleared} временных объектов Р7 из %TEMP%")

            # ----- 3. Реальное количество строк ------------------------------------------
            real_rows = self._get_xlsx_row_count(file_path)
            if real_rows is not None:
                self.add_test_log(f"📊 Реальное количество строк в файле: {real_rows:,}")
            else:
                real_rows = rows

            # ----- 4. Поиск пути к Р7-Офис -----------------------------------------------
            r7_path = self._find_r7_path()
            if not r7_path:
                self.add_test_log("❌ Р7-Офис не найден.")
                return

            # ----- 5. Запуск и ожидание окна --------------------------------------------
            self.add_test_log(f"⏳ Запуск теста на файле {file_path.name}")
            open_start = time.time()
            subprocess.Popen([r7_path, str(file_path)], shell=True)

            def _find_hwnd():
                found = [None]
                if WIN32_OK:
                    stem = file_path.stem[:12]
                    def _cb(h, _):
                        t = win32gui.GetWindowText(h)
                        if stem in t or "Р7-Офис" in t:
                            found[0] = h
                    win32gui.EnumWindows(_cb, None)
                return found[0]

            deadline = time.time() + 60
            hwnd = None
            while time.time() < deadline:
                hwnd = _find_hwnd()
                if hwnd:
                    break
                time.sleep(0.5)

            if not hwnd:
                self.add_test_log("⚠️ Окно Р7 не найдено, продолжаем без фокуса")

            # ----- 6. Фокус и разворот ---------------------------------------------------
            if WIN32_OK and hwnd:
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.5)
                except Exception:
                    pass

            # ----- 7. Динамическое ожидание загрузки -------------------------------------
            data_ready   = self._wait_data_ready_for_test(open_start, hwnd)
            open_elapsed = time.time() - open_start
            self.add_test_log(
                f"✅ Файл открыт за {open_elapsed:.2f} сек "
                f"({'данные загружены' if data_ready else 'таймаут — возможна частичная загрузка'})"
            )

            # ----- 8. ВПР-бенчмарк на всех строках --------------------------------------
            vlookup_elapsed = None
            vlookup_error   = None
            vlookup_rows    = 0

            if PYAUTOGUI_OK and pyperclip:
                try:
                    # Переходим в C2: первая свободная колонка после ID и Name
                    pyautogui.hotkey('ctrl', 'Home')
                    time.sleep(0.2)
                    pyautogui.press('right')   # → B1
                    pyautogui.press('right')   # → C1
                    pyautogui.press('down')    # → C2

                    # Вставляем формулу ВПР в C2
                    pyperclip.copy('=VLOOKUP(A2,A:B,2,FALSE)')
                    pyautogui.hotkey('ctrl', 'v')
                    time.sleep(0.1)

                    vstart = time.time()
                    pyautogui.press('enter')

                    # Возвращаемся в C2 и заполняем формулой весь столбец
                    pyautogui.hotkey('ctrl', 'Home')
                    pyautogui.press('right')
                    pyautogui.press('right')
                    pyautogui.press('down')                # C2
                    pyautogui.hotkey('ctrl', 'shift', 'down')  # выделяем до конца данных
                    pyautogui.hotkey('ctrl', 'd')              # заполняем вниз
                    time.sleep(0.5)

                    vlookup_elapsed = round(time.time() - vstart, 3)
                    vlookup_rows    = real_rows
                    self.add_test_log(
                        f"✅ ВПР по {real_rows:,} строкам завершён за {vlookup_elapsed:.2f} сек"
                    )
                except Exception as e:
                    vlookup_error = str(e)
                    self.add_test_log(f"⚠️ Ошибка ВПР: {e}")
            else:
                self.add_test_log("⚠️ pyautogui/pyperclip недоступны — ВПР пропущен")

            # ----- 9. Закрытие Р7 --------------------------------------------------------
            try:
                pyautogui.hotkey('alt', 'f4')
                time.sleep(1)
                pyautogui.press('right')
                pyautogui.press('enter')
            except Exception:
                pass

            # ----- 10. Отчёт -------------------------------------------------------------
            file_size_mb = (round(file_path.stat().st_size / (1024 ** 2), 2)
                            if file_path.exists() else None)
            self._show_custom_test_report({
                "filename":        file_path.name,
                "rows":            rows,
                "cols":            cols,
                "real_rows":       real_rows,
                "vlookup_rows":    vlookup_rows,
                "file_size_mb":    file_size_mb,
                "open_elapsed":    round(open_elapsed, 3),
                "vlookup_elapsed": vlookup_elapsed,
                "vlookup_error":   vlookup_error,
                "cache_cleared":   cleared > 0,
                "data_ready":      data_ready,
                "timestamp":       datetime.now().strftime("%d.%m.%Y %H:%M"),
            })
            success = True
        except Exception as e:
            self.add_test_log(f"❌ Ошибка тестирования: {e}")
        finally:
            done_cb(success)

    def _kill_r7_processes_for_test(self):
        """Kills all R7-Office processes. Returns count killed."""
        if not PSUTIL_OK:
            return 0
        search = ("editors_helper", "desktopeditors", "r7officemain", "r7office")
        killed = 0
        try:
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if any(s in name for s in search):
                        proc.kill()
                        killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
        return killed

    def _clear_r7_cache(self):
        """Removes R7-Office temp items from %%TEMP%%. Returns count removed."""
        cleared = 0
        temp_dir = Path(os.environ.get("TEMP", ""))
        if not temp_dir.exists():
            return 0
        for pat in ("r7*", "R7*", "editors*"):
            for item in temp_dir.glob(pat):
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                    cleared += 1
                except Exception:
                    pass
        return cleared

    def _get_xlsx_row_count(self, path):
        """Returns data row count (excluding header row) via openpyxl read-only, or None."""
        if not EXCEL_OK:
            return None
        try:
            from openpyxl import load_workbook as _lw
            wb = _lw(str(path), read_only=True, data_only=True)
            count = max(0, (wb.active.max_row or 1) - 1)
            wb.close()
            return count
        except Exception as e:
            self.add_test_log(f"⚠️ Не удалось прочитать количество строк: {e}")
            return None

    def _wait_data_ready_for_test(self, open_start, hwnd, timeout=120):
        """Waits for R7-Office to finish loading via Ctrl+End probe timing.

        Uses LOAD_THRESHOLD=5.0 s and an 8-second stability window.
        Works as a method (no outer closures), mirrors wait_for_data_ready logic.

        Returns:
            True if data confirmed loaded, False on timeout or window loss.
        """
        LOAD_THRESHOLD  = 5.0
        BASE_WAIT       = 3
        STABLE_SECS     = 8
        STABLE_DELTA    = 0.05
        MAX_FOCUS_FAILS = 3

        self.add_test_log(
            f"⏳ Ожидание загрузки данных "
            f"(порог {LOAD_THRESHOLD} сек, стабилизация за {STABLE_SECS} сек)..."
        )
        time.sleep(BASE_WAIT)

        def _focus():
            if WIN32_OK and hwnd:
                try:
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.1)
                    return True
                except Exception:
                    return False
            return True

        deadline    = time.time() + max(0, timeout - BASE_WAIT)
        fail_streak = 0
        probe_hist  = []

        while time.time() < deadline:
            elapsed = int(time.time() - open_start)

            if not _focus():
                fail_streak += 1
                self.add_test_log(
                    f"   ⚠️ Окно недоступно (попытка {fail_streak}/{MAX_FOCUS_FAILS})")
                if fail_streak >= MAX_FOCUS_FAILS:
                    self.add_test_log("⚠️ Окно Р7 потеряно — продолжаем тест")
                    return False
                time.sleep(1)
                continue
            fail_streak = 0

            if not PYAUTOGUI_OK:
                time.sleep(2)
                break

            t0 = time.time()
            pyautogui.hotkey('ctrl', 'End', interval=0.05)
            probe = time.time() - t0

            probe_hist.append(probe)
            if len(probe_hist) > STABLE_SECS:
                probe_hist.pop(0)

            self.add_test_log(
                f"   ⏳ Ожидание загрузки... ({elapsed} сек, Ctrl+End = {probe:.3f} сек)"
            )

            if probe >= LOAD_THRESHOLD:
                self.add_test_log(
                    f"   ✅ Данные загружены: навигация {probe:.2f} сек > порога {LOAD_THRESHOLD} сек"
                )
                pyautogui.hotkey('ctrl', 'Home', interval=0.05)
                return True

            if (len(probe_hist) == STABLE_SECS and
                    max(probe_hist) - min(probe_hist) <= STABLE_DELTA):
                self.add_test_log(
                    f"   ✅ Навигация стабилизировалась ({probe:.3f} сек) — данные загружены"
                )
                pyautogui.hotkey('ctrl', 'Home', interval=0.05)
                return True

            time.sleep(1)

        self.add_test_log(f"⚠️ Таймаут ожидания ({timeout} сек) — продолжаем тест")
        return False

    def _show_custom_test_report(self, result):
        """Builds and opens an HTML report for a single custom-file benchmark."""
        fname        = result["filename"]
        cols         = result.get("cols") or 0
        real_rows    = result.get("real_rows") or result.get("rows") or 0
        vlookup_rows = result.get("vlookup_rows") or 0
        size_mb      = f"{result['file_size_mb']:.2f}" if result.get("file_size_mb") else "—"
        open_t       = f"{result['open_elapsed']:.3f}"
        vlook_t      = (f"{result['vlookup_elapsed']:.3f}"
                        if result.get("vlookup_elapsed") is not None else "—")
        vlook_err    = result.get("vlookup_error") or ""
        ts           = result["timestamp"]
        cache_ok     = result.get("cache_cleared", False)
        data_ready   = result.get("data_ready", None)

        bar_data   = json.dumps([
            result["open_elapsed"],
            result["vlookup_elapsed"] if result.get("vlookup_elapsed") is not None else 0,
        ])
        bar_labels = json.dumps(
            ["Открытие файла", f"ВПР ({vlookup_rows:,} строк)".replace(",", " ")])

        # Баннер предупреждения если данные могут быть не загружены
        warn_html = ""
        if data_ready is False:
            warn_html = (
                '<div class="warn-banner">⚠️ Данные могут быть загружены не полностью '
                '(сработал таймаут ожидания). Результаты могут быть занижены.</div>'
            )

        # Бейдж очистки кеша
        cache_badge = (
            '<span class="badge badge-ok">🧹 Кеш очищен перед тестом</span>'
            if cache_ok else
            '<span class="badge badge-warn">⚠️ psutil недоступен — кеш не очищался</span>'
        )

        # Статус загрузки данных
        if data_ready is True:
            load_status = '<td class="ok">✅ Данные загружены</td>'
        elif data_ready is False:
            load_status = '<td class="err">⏰ Таймаут</td>'
        else:
            load_status = '<td>—</td>'

        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Тест: {fname}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body{{font-family:Arial,sans-serif;padding:24px;background:#f5f6fa;color:#333}}
  h1{{color:#2c3e50;margin-bottom:4px}}
  .subtitle{{color:#888;font-size:.9em;margin-bottom:14px}}
  .warn-banner{{background:#fff3cd;border:1px solid #ffc107;border-radius:6px;
    padding:10px 16px;margin-bottom:14px;color:#856404;font-size:.9em}}
  .badge{{display:inline-block;padding:4px 12px;border-radius:12px;
    font-size:.8em;font-weight:bold;margin-bottom:16px}}
  .badge-ok{{background:#d4edda;color:#155724}}
  .badge-warn{{background:#fff3cd;color:#856404}}
  .info-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
    gap:10px;margin-bottom:20px}}
  .info-item{{background:#fff;padding:10px 14px;border-radius:8px;
    box-shadow:0 1px 3px rgba(0,0,0,.1)}}
  .info-label{{font-size:.75em;color:#888;text-transform:uppercase}}
  .info-value{{font-weight:bold;font-size:1.05em;margin-top:3px}}
  .chart-box{{background:#fff;padding:20px;border-radius:8px;
    box-shadow:0 1px 4px rgba(0,0,0,.12);max-width:560px;margin-bottom:24px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;
    box-shadow:0 1px 4px rgba(0,0,0,.12);overflow:hidden}}
  th{{background:#2c3e50;color:#fff;padding:10px 14px;text-align:left;font-size:.85em}}
  td{{padding:9px 14px;border-bottom:1px solid #eee;font-size:.9em}}
  .ok{{color:#27ae60;font-weight:bold}} .err{{color:#e74c3c;font-weight:bold}}
</style>
</head>
<body>
<h1>Тест производительности: {fname}</h1>
<div class="subtitle">{ts}</div>
{warn_html}
{cache_badge}

<div class="info-grid">
  <div class="info-item">
    <div class="info-label">Файл</div>
    <div class="info-value" style="font-size:.9em;word-break:break-all">{fname}</div>
  </div>
  <div class="info-item">
    <div class="info-label">Строк в файле</div>
    <div class="info-value">{real_rows:,}</div>
  </div>
  <div class="info-item">
    <div class="info-label">Столбцов</div>
    <div class="info-value">{cols}</div>
  </div>
  <div class="info-item">
    <div class="info-label">Размер файла</div>
    <div class="info-value">{size_mb} МБ</div>
  </div>
  <div class="info-item">
    <div class="info-label">Открытие файла</div>
    <div class="info-value">{open_t} сек</div>
  </div>
  <div class="info-item">
    <div class="info-label">ВПР ({vlookup_rows:,} строк)</div>
    <div class="info-value {'err' if vlook_err else 'ok'}">{vlook_t} {'⚠ ' + vlook_err if vlook_err else 'сек'}</div>
  </div>
</div>

<div class="chart-box">
  <canvas id="barChart"></canvas>
</div>

<table>
<thead>
  <tr><th>Операция</th><th>Строк</th><th>Время (сек)</th><th>Статус</th></tr>
</thead>
<tbody>
<tr>
  <td>Открытие файла</td>
  <td>{real_rows:,}</td>
  <td>{open_t}</td>
  {load_status}
</tr>
<tr>
  <td>ВПР (VLOOKUP)</td>
  <td>{vlookup_rows:,}</td>
  <td>{vlook_t}</td>
  <td class="{'err' if vlook_err else 'ok'}">{'⚠️ ' + vlook_err if vlook_err else '✅ OK'}</td>
</tr>
</tbody>
</table>

<script>
new Chart(document.getElementById('barChart'), {{
  type: 'bar',
  data: {{
    labels: {bar_labels},
    datasets: [{{
      label: 'Время (сек)',
      data: {bar_data},
      backgroundColor: ['#3498db', '#27ae60'],
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{display: false}},
      title: {{display: true, text: 'Время выполнения операций (сек)'}}
    }},
    scales: {{y: {{beginAtZero: true}}}}
  }}
}});
</script>
</body>
</html>"""

        ts_file  = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.reports_folder / f"custom_test_{ts_file}.html"
        try:
            out_path.write_text(html, encoding="utf-8")
            self.add_test_log(f"📊 Отчёт готов: {out_path.name}")
            webbrowser.open(str(out_path))
        except Exception as e:
            self.add_test_log(f"⚠️ Ошибка записи отчёта: {e}")

    def _generate_custom_test_file(self, rows, cols, path):
        """Creates an xlsx file with rows×cols of test data using openpyxl."""
        if not EXCEL_OK:
            raise RuntimeError("openpyxl не установлен")
        wb = Workbook()
        ws = wb.active
        ws.title = "Лист1"
        # Заголовки
        header = ["ID", "Name"] + [f"Col{i}" for i in range(3, cols + 1)]
        ws.append(header)
        # Данные
        for i in range(1, rows + 1):
            row = [i, f"Item_{i:05d}"]
            for c in range(3, cols + 1):
                row.append(i * c)
            ws.append(row)
        wb.save(str(path))

    # ---------------------- Хеш-суммы дистрибутивов ----------------------
    def check_hashes(self):
        """Entry point for hash verification — creates progress window then spawns worker thread."""
        files = (list(self.distributives_folder.glob("*.msi")) +
                 list(self.distributives_folder.glob("*.exe")))
        if not files:
            messagebox.showwarning("Нет файлов", "В папке Distributives нет файлов для проверки.")
            return

        prog_win = tk.Toplevel(self.root)
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
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(8192)
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

        tree.tag_configure("ok",     background="#d4edda")
        tree.tag_configure("no_ref", background="#fff3cd")
        tree.tag_configure("fail",   background="#f8d7da")

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
                         foreground="gray")
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

    # ---------------------- Поиск пути Р7 ----------------------
    def _monitor_update_dialog(self, stop_event, log_cb=None, interval=2):
        """Background thread: checks for the update dialog every `interval` seconds.

        Args:
            stop_event: threading.Event — set it to stop the loop.
            log_cb: Callable for log output. Defaults to self.add_test_log.
            interval: Seconds between checks.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        while not stop_event.is_set():
            self._close_update_dialog_if_exists(log_cb=log_cb, search_timeout=1)
            stop_event.wait(timeout=interval)

    def _close_update_dialog_if_exists(self, log_cb=None, search_timeout=5):
        """Looks for the R7-Office update dialog and closes it if found.

        Scans visible top-level windows for update-related title keywords,
        enumerates child windows to find a dismiss button and clicks it via
        Win32 messages (no pyautogui, no focus dependency).
        Falls back to WM_CLOSE + VK_ESCAPE if no button is matched.
        Logs nothing if no dialog is present.

        Args:
            log_cb: Callable for log output. Defaults to self.add_test_log.
            search_timeout: Seconds to poll for the dialog window (use a small
                value such as 1 when called from a monitor loop).

        Returns:
            bool: True if a dialog was found and dismissed.
        """
        if log_cb is None:
            log_cb = self.add_test_log

        if not WIN32_OK:
            return False

        import win32gui
        import win32con

        UPDATE_TITLES = (
            'обновление программного обеспечения',
            'доступна новая версия',
            'р7-офис обновление',
            'update available',
            'software update',
            'доступна',
            'новая версия',
            'обновление',
            'new version',
            'update',
        )
        # Checked in order — first match wins
        DISMISS_PRIORITY = (
            'напомнить позже',
            'пропустить эту версию',
            'не сейчас',
            'remind me later',
            'skip this version',
            'not now',
            'later',
            'skip',
        )

        # ── Search for the dialog (up to search_timeout seconds) ─────────────
        found = []
        deadline = time.time() + search_timeout
        while time.time() < deadline and not found:
            def _enum(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    t = win32gui.GetWindowText(hwnd).lower()
                    if any(s in t for s in UPDATE_TITLES):
                        found.append(hwnd)
            win32gui.EnumWindows(_enum, None)
            if not found:
                time.sleep(0.5)

        if not found:
            return False

        hwnd = found[0]
        actual_title = win32gui.GetWindowText(hwnd)
        log_cb(f"⚠️ Обнаружено окно обновления: {actual_title}")

        # ── Collect all child windows ─────────────────────────────────────────
        children = []
        def _collect(h, _):
            try:
                children.append((h, win32gui.GetWindowText(h), win32gui.GetClassName(h)))
            except Exception:
                pass

        try:
            win32gui.EnumChildWindows(hwnd, _collect, None)
        except Exception:
            pass

        # ── Find and click dismiss button ─────────────────────────────────────
        clicked = False
        for rank, keyword in enumerate(DISMISS_PRIORITY):
            for h, text, cls in children:
                if keyword in text.lower():
                    log_cb(f"   Найдена кнопка: «{text}», закрываю...")
                    # Strategy 1: BM_CLICK (Win32 BUTTON class)
                    try:
                        win32gui.SendMessage(h, win32con.BM_CLICK, 0, 0)
                        clicked = True
                    except Exception:
                        pass
                    # Strategy 2: WM_LBUTTONDOWN + WM_LBUTTONUP on button hwnd
                    if not clicked:
                        try:
                            win32gui.PostMessage(h, win32con.WM_LBUTTONDOWN,
                                                 win32con.MK_LBUTTON, 0)
                            time.sleep(0.05)
                            win32gui.PostMessage(h, win32con.WM_LBUTTONUP, 0, 0)
                            clicked = True
                        except Exception:
                            pass
                    break
            if clicked:
                break

        # ── Fallback when no button matched ──────────────────────────────────
        if not clicked:
            log_cb("   ⚠️ Кнопки для закрытия не найдены. Дочерние окна для диагностики:")
            for h, text, cls in children:
                if text or cls:
                    log_cb(f"      hwnd={h}  class={cls!r}  text={text!r}")

            # Try WM_CLOSE first (clean dialog dismissal)
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
            time.sleep(0.2)

            # If still visible — send VK_ESCAPE via message (no pyautogui)
            if win32gui.IsWindowVisible(hwnd):
                try:
                    # lParam for key-down: repeat=1, scan=0x01, other bits=0
                    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN,
                                         win32con.VK_ESCAPE, 0x00010001)
                    time.sleep(0.05)
                    win32gui.PostMessage(hwnd, win32con.WM_KEYUP,
                                         win32con.VK_ESCAPE, 0xC0010001)
                except Exception:
                    pass

        time.sleep(0.3)
        log_cb("✅ Окно обновления закрыто")
        return Truecl

    def _find_r7_path(self):
        """Locates the R7-Office desktop executable, caching the result.

        Returns:
            str: Absolute path to DesktopEditors.exe, or None if not found.
        """
        if self._cached_r7_path:
            return self._cached_r7_path
        possible_paths = [
            r"C:\Program Files\R7-Office\Editors\DesktopEditors\DesktopEditors.exe",
            r"C:\Program Files\R7-Office\Editors\DesktopEditors\R7.exe",
            r"C:\Program Files (x86)\R7-Office\Editors\DesktopEditors\DesktopEditors.exe",
            r"C:\Program Files\Р7-Офис\Editors\DesktopEditors\DesktopEditors.exe",
        ]
        for path in possible_paths:
            if Path(path).exists():
                self._cached_r7_path = path
                return path
        for search_dir in [r"C:\Program Files", r"C:\Program Files (x86)"]:
            if Path(search_dir).exists():
                for exe_path in Path(search_dir).rglob("DesktopEditors.exe"):
                    self._cached_r7_path = str(exe_path)
                    return str(exe_path)
        return None


if __name__ == "__main__":
    if not ctypes.windll.shell32.IsUserAnAdmin():
        result = messagebox.askyesno("Права администратора", "Запустить от имени администратора?")
        if result:
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
            sys.exit()
    root = tk.Tk()
    app = R7Testovarka(root)
    root.mainloop()

