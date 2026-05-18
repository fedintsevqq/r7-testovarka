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
import tempfile
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import webbrowser
from tkinter import filedialog
import shutil

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


class R7Testovarka:
    def __init__(self, root):
        self.root = root
        self.root.title("R7-Testovarka Light")
        self.root.geometry("800x600")
        self.root.resizable(True, True)

        self.distributives_folder = Path(sys.argv[0]).parent / "Distributives"
        self.distributives_folder.mkdir(exist_ok=True)

        self.current_version_info = None
        self.distributives = []
        self.selected_distributive = None
        self._cached_r7_path = None

        self.setup_ui()
        self.refresh_distributives()
        self.detect_current_version()

    # ---------------------- UI ----------------------
    def setup_ui(self):
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

        self.listbox.bind('<<ListboxSelect>>', self.on_select_distributive)

    def _build_perf_tab(self):
        # Лог
        self.test_log = tk.Text(self.tab_perf, font=("Consolas", 9), height=20)
        scroll = ttk.Scrollbar(self.tab_perf, command=self.test_log.yview)
        self.test_log.configure(yscrollcommand=scroll.set)
        self.test_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        btn_frame = ttk.Frame(self.tab_perf)
        btn_frame.pack(fill=tk.X, pady=10)
        ttk.Button(btn_frame, text="🧪 Запустить стресс-тест таблиц", command=self.run_spreadsheet_test).pack(pady=5)

    # ---------------------- Управление версиями ----------------------
    def detect_current_version(self):
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
        match = re.search(r'(\d+\.\d+(?:\.\d+)*)', filename)
        return f"v{match.group(1)}" if match else None

    def on_select_distributive(self, event):
        sel = self.listbox.curselection()
        if sel and self.distributives:
            self.selected_distributive = self.distributives[sel[0]]
            self.btn_install.config(state=tk.NORMAL)
            mb = self.selected_distributive["path"].stat().st_size / (1024 * 1024)
            self.lbl_file_info.config(text=f"{self.selected_distributive['name']} ({mb:.1f} МБ)")
        else:
            self.btn_install.config(state=tk.DISABLED)

    def uninstall_current_version(self):
        if not self.current_version_info:
            return True
        try:
            self.status_var.set("Удаление...")
            cmd = self.current_version_info["uninstall_string"] + " /quiet /norestart"
            subprocess.Popen(cmd, shell=True).wait(timeout=60)
            time.sleep(3)
            for p in [r"C:\Program Files\R7-Office", r"C:\Program Files (x86)\R7-Office"]:
                if os.path.exists(p):
                    shutil.rmtree(p, ignore_errors=True)
            return True
        except:
            return True

    def install_version(self, path):
        self.status_var.set(f"Установка {path.name}...")
        if path.suffix == ".msi":
            cmd = ["msiexec", "/i", str(path), "/quiet", "/norestart"]
        else:
            cmd = [str(path), "/quiet"]
        subprocess.Popen(cmd, shell=True).wait(timeout=300)
        time.sleep(3)
        self.detect_current_version()
        return True

    def install_selected(self):
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
        files = filedialog.askopenfilenames(filetypes=[("Installer", "*.msi *.exe")])
        for f in files:
            dst = self.distributives_folder / Path(f).name
            shutil.copy2(f, dst)
        self.refresh_distributives()

    def open_distributives_folder(self):
        os.startfile(str(self.distributives_folder))

    # ---------------------- Лог ----------------------
    def add_test_log(self, msg):
        try:
            self.test_log.insert(tk.END, f"[{datetime.now():%H:%M:%S}] {msg}\n")
            self.test_log.see(tk.END)
            self.root.update()
        except:
            print(msg)

    # ---------------------- Стресс-тест таблиц ----------------------
    def run_spreadsheet_test(self):
        if not self.current_version_info:
            messagebox.showwarning("Нет версии", "Р7-Офис не установлен или не определён.")
            return
        if not PYAUTOGUI_OK or not pyperclip or not EXCEL_OK or not WIN32_OK:
            missing = []
            if not PYAUTOGUI_OK: missing.append("pyautogui")
            if not pyperclip: missing.append("pyperclip")
            if not EXCEL_OK: missing.append("openpyxl")
            if not WIN32_OK: missing.append("pywin32")
            messagebox.showerror("Ошибка", f"Отсутствуют библиотеки:\n{', '.join(missing)}\nУстановите: pip install " + " ".join(missing))
            return
        threading.Thread(target=self._spreadsheet_worker, daemon=True).start()



    def _spreadsheet_worker(self):
        self.add_test_log("\n🚀 ЗАПУСК СТРЕСС-ТЕСТА ТАБЛИЦ")
        REPORT_FILE = Path("E:/R7Manager/Performance_Report.xlsx")
        HTML_REPORT_PATH = REPORT_FILE.with_suffix(".html")

        # ----- 1. Поиск тестового файла -----
        def find_test_file():
            patterns = ["файл-для-теста-Р7-офис-50К*.xlsx", "файл-для-теста-Р7-офис-50К*.xls", "*50К*.xlsx"]
            search_dirs = [Path("E:/R7Manager"), Path.home() / "Downloads", Path.home() / "Загрузки", Path.cwd()]
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
        def wait_for_window(title_part, timeout=60):
            import win32gui
            start = time.time()
            while time.time() - start < timeout:
                def enum_cb(hwnd, wins):
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        if title_part.lower() in title.lower():
                            wins.append(hwnd)

                wins = []
                win32gui.EnumWindows(enum_cb, wins)
                if wins:
                    win32gui.SetForegroundWindow(wins[0])
                    return True
                time.sleep(0.5)
            return False

        def maximize_window():
            import win32gui, win32con
            def enum_cb(hwnd, wins):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if "Р7-Офис" in title or test_file.stem in title:
                        wins.append(hwnd)

            wins = []
            win32gui.EnumWindows(enum_cb, wins)
            if wins:
                win32gui.ShowWindow(wins[0], win32con.SW_MAXIMIZE)
                time.sleep(0.5)
                return True
            return False

        def focus_window():
            import win32gui
            def enum_cb(hwnd, wins):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if "Р7-Офис" in title or test_file.stem in title:
                        wins.append(hwnd)

            wins = []
            win32gui.EnumWindows(enum_cb, wins)
            if wins:
                win32gui.SetForegroundWindow(wins[0])
                time.sleep(0.3)
                return True
            return False

        def close_update_dialog():
            """Закрывает окно с предложением обновить Р7-Офис."""
            import win32gui
            def enum_cb(hwnd, wins):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if any(s in title.lower() for s in ['доступна', 'update', 'новая версия', 'обновление']):
                        wins.append(hwnd)

            wins = []
            win32gui.EnumWindows(enum_cb, wins)
            if wins:
                win32gui.SetForegroundWindow(wins[0])
                time.sleep(0.3)
                pyautogui.press('enter')  # обычно "Напомнить позже"
                # Можно также нажать Esc, если Enter не срабатывает
                # pyautogui.press('esc')
                self.add_test_log("⚠️ Закрыто окно обновления Р7-Офис.")
                return True
            return False

        def safe_hotkey(*keys):
            pyautogui.hotkey(*keys, interval=0.1)
            time.sleep(0.5)

        def safe_press(key, presses=1):
            for _ in range(presses):
                pyautogui.press(key)
                time.sleep(0.1)

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
        close_update_dialog()  # закрыть диалог обновления, если он есть
        time.sleep(0.5)

        DATA_LOAD_SECONDS = 0  # настройте под свою систему
        self.add_test_log(f"⏳ Ожидание загрузки данных ({DATA_LOAD_SECONDS} сек)...")
        time.sleep(DATA_LOAD_SECONDS)
        open_elapsed = time.time() - open_start
        self.add_test_log(f"✅ Файл открыт за {open_elapsed:.2f} сек")

        focus_window()
        pyautogui.click(x=800, y=400)
        time.sleep(1)

        # ----- 4. Тесты (13 операций) -----
        def measure(name, func):
            self.add_test_log(f"⏳ {name}...")
            focus_window()
            start = time.time()
            error = None
            try:
                func()
            except Exception as e:
                error = str(e)
            elapsed = time.time() - start
            self.add_test_log(f"   ✅ {name}: {elapsed:.2f} сек" + (f" (ошибка: {error})" if error else ""))
            return {"name": name, "time": elapsed, "error": error}

        results = [{"name": "Открытие файла", "time": open_elapsed, "error": None}]

        # 1. Выделение всех ячеек (Ctrl+A)
        results.append(measure("Выделение всех ячеек (Ctrl+A)", lambda: safe_hotkey('ctrl', 'a')))

        # 2. Копирование (Ctrl+C)
        results.append(measure("Копирование всех ячеек (Ctrl+C)", lambda: safe_hotkey('ctrl', 'c')))

        # 3. Вставка большого массива (новый лист + Ctrl+V)
        def paste_big():
            safe_hotkey('shift', 'f11')
            # без time.sleep(2) – пусть ОС сама переключится
            safe_hotkey('ctrl', 'v')
            # без time.sleep(5) – не ждём лишнего

        results.append(measure("Вставка большого массива (Ctrl+V)", paste_big))

        # 4. Добавление нового листа
        results.append(measure("Добавление нового листа", lambda: safe_hotkey('shift', 'f11')))

        # 5. Добавление столбца (горячие клавиши)
        def add_col_hotkey():
            safe_hotkey('ctrl', 'pageup')
            time.sleep(0.1)
            pyautogui.press('right')
            safe_hotkey('ctrl', 'shift', '=')

        results.append(measure("Добавление столбца (горячие клавиши)", add_col_hotkey))

        # 6. Добавление столбца (меню Вставка)
        def add_col_ui():
            safe_hotkey('ctrl', 'pageup')
            time.sleep(0.1)
            pyautogui.press('right')
            safe_hotkey('alt', 'i')
            safe_press('c')

        results.append(measure("Добавление столбца (меню Вставка)", add_col_ui))

        # 7. Копипаст 1 ячейки (хоткеи)
        def cp1_hotkey():
            safe_hotkey('ctrl', 'home')
            safe_hotkey('ctrl', 'c')
            pyautogui.press('right', presses=10)
            safe_hotkey('ctrl', 'v')

        results.append(measure("Вставка 1 ячейки (горячие клавиши)", cp1_hotkey))

        # 8. Копипаст 5 ячеек (хоткеи)
        def cp5_hotkey():
            safe_hotkey('ctrl', 'home')
            for _ in range(4):
                pyautogui.hotkey('shift', 'right')
            safe_hotkey('ctrl', 'c')
            pyautogui.press('right', presses=15)
            safe_hotkey('ctrl', 'v')

        results.append(measure("Вставка 5 ячеек (горячие клавиши)", cp5_hotkey))

        # 9. Копипаст 1 ячейки (ПКМ)
        def cp1_context():
            safe_hotkey('ctrl', 'home')
            pyautogui.click(button='right')
            safe_press('down', 2)
            safe_press('enter')
            pyautogui.press('right', presses=10)
            pyautogui.click(button='right')
            safe_press('down', 3)
            safe_press('enter')

        results.append(measure("Вставка 1 ячейки (ПКМ)", cp1_context))

        # 10. Копипаст 5 ячеек (ПКМ)
        def cp5_context():
            safe_hotkey('ctrl', 'home')
            for _ in range(4):
                pyautogui.hotkey('shift', 'right')
            pyautogui.click(button='right')
            safe_press('down', 2)
            safe_press('enter')
            pyautogui.press('right', presses=15)
            pyautogui.click(button='right')
            safe_press('down', 3)
            safe_press('enter')

        results.append(measure("Вставка 5 ячеек (ПКМ)", cp5_context))

        # 11. Функция ВПР
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

        results.append(measure("Функция ВПР (50K строк)", vlookup))

        # 12. Удаление столбца (Del)
        def del_column():
            safe_hotkey('ctrl', 'home')
            pyautogui.press('right')
            pyautogui.press('delete')

        results.append(measure("Удаление столбца (Del)", del_column))

        # ----- 5. Сохранение отчётов (Excel + HTML) -----
        try:
            # Excel
            REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "Результаты"
            ws.append(["Название операции", "Затраченное время (сек)", "Примечание об ошибке"])
            for r in results:
                ws.append([r["name"], round(r["time"], 2), r["error"] if r["error"] else ""])
            wb.save(str(REPORT_FILE))
            self.add_test_log(f"📊 Excel-отчёт сохранён: {REPORT_FILE}")

            # HTML
            def generate_html(results, test_file, open_time, version):
                rows = ""
                for r in results:
                    error_class = "error" if r["error"] else ""
                    rows += f"<tr class='{error_class}'>)<td>{r['name']}</td><td>{round(r['time'], 2)}</td><td>{r['error'] or ''}</td></tr>"
                return f"""<!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Отчёт о производительности Р7-Офис</title>
    <style>
    body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
    h1 {{ color: #2c3e50; }}
    .container {{ max-width: 1000px; margin: auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
    table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    th {{ background-color: #3498db; color: white; }}
    tr:nth-child(even) {{ background-color: #f9f9f9; }}
    tr.error {{ background-color: #ffe6e6; }}
    .info {{ margin-top: 20px; color: #7f8c8d; }}
    .badge {{ display: inline-block; background: #2ecc71; color: white; padding: 5px 10px; border-radius: 15px; font-size: 12px; }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    </head>
    <body>
    <div class="container">
    <h1>Отчёт о производительности Р7-Офис</h1>
    <p><strong>Дата теста:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    <p><strong>Версия Р7-Офис:</strong> {version if version else 'Неизвестно'}</p>
    <p><strong>Тестовый файл:</strong> {test_file}</p>
    <p><strong>Время открытия файла:</strong> {open_time:.2f} сек</p>
    <h2>Результаты операций</h2>
    <table><thead><tr><th>Операция</th><th>Время (сек)</th><th>Ошибка</th></tr></thead><tbody>{rows}</tbody></table>
    <h2>График времени выполнения</h2>
    <canvas id="timeChart" width="800" height="400"></canvas>
    <div class="info"><p class="badge">Тест завершён</p><p>Отчёт сгенерирован автоматически.</p></div>
    </div>
    <script>
    const labels = {[r["name"] for r in results]};
    const times = {[r["time"] for r in results]};
    const ctx = document.getElementById('timeChart').getContext('2d');
    new Chart(ctx, {{
        type: 'bar',
        data: {{
            labels: labels,
            datasets: [{{
                label: 'Время (сек)',
                data: times,
                backgroundColor: 'rgba(52, 152, 219, 0.6)',
                borderColor: 'rgba(52, 152, 219, 1)',
                borderWidth: 1
            }}]
        }},
        options: {{
            responsive: true,
            scales: {{
                y: {{ beginAtZero: true, title: {{ display: true, text: 'Секунды' }} }},
                x: {{ ticks: {{ autoSkip: false, rotation: 45, maxRotation: 90, minRotation: 45 }} }}
            }}
        }}
    }});
    </script>
    </body>
    </html>"""

            html_content = generate_html(results, test_file, open_elapsed,
                                         self.current_version_info['name'] if self.current_version_info else None)
            with open(HTML_REPORT_PATH, "w", encoding="utf-8") as f:
                f.write(html_content)
            self.add_test_log(f"📄 HTML-отчёт сохранён: {HTML_REPORT_PATH}")

            # Открыть в браузере
            webbrowser.open(str(HTML_REPORT_PATH))

            # Предложить сохранить копию
            if messagebox.askyesno("Скачать отчёт", "Хотите сохранить копию HTML-отчёта в другом месте?"):
                save_path = filedialog.asksaveasfilename(
                    defaultextension=".html",
                    filetypes=[("HTML files", "*.html"), ("All files", "*.*")],
                    initialfile=f"Performance_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                )
                if save_path:
                    shutil.copy(HTML_REPORT_PATH, save_path)
                    self.add_test_log(f"📎 Копия отчёта сохранена: {save_path}")

        except Exception as e:
            self.add_test_log(f"⚠️ Ошибка сохранения отчётов: {e}")

        # ----- 6. Закрытие -----
        self.add_test_log("🔚 Закрытие Р7-Офис...")
        safe_hotkey('alt', 'f4')
        time.sleep(1)
        safe_press('right')
        safe_press('enter')
        self.add_test_log("🏁 Тест завершён.")

    # ---------------------- Поиск пути Р7 ----------------------
    def _find_r7_path(self):
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
    import ctypes
    if not ctypes.windll.shell32.IsUserAnAdmin():
        result = messagebox.askyesno("Права администратора", "Запустить от имени администратора?")
        if result:
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
            sys.exit()
    root = tk.Tk()
    app = R7Testovarka(root)
    root.mainloop()
