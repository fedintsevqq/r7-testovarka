"""Второй заход UIA-диагностики: точечный дамп только ComboBox/Button/Edit
(без огромного поддерева списка файлов, которое забило терминал в первом
заходе — tests/manual_saveas_uia_probe.py), пишет в файл, не в stdout.

ЗАПУСК (закройте Р7-Офис перед стартом):
    .venv/Scripts/python.exe tests/manual_saveas_uia_probe2.py
"""
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import subprocess           # noqa: E402
import r7_Testovarka as r7mod  # noqa: E402
import pyautogui            # noqa: E402
import win32gui             # noqa: E402

from pywinauto import Application  # noqa: E402

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

DEFAULT_FILE = "test_50k.xlsx"
OUT_FILE = BASE_DIR / "tests" / "_uia_dump.txt"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def make_app():
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app._paced_total = 0.0
    app._pending_modal_confirm = False
    app._pending_cdp_verify = None
    app._op_via_cdp = False
    app._cdp_api_ms = 0.0
    app._op_start_grace = None
    app._op_max_wait = None
    app._webdriver_connector = None
    app._current_webdriver_port = None
    app._r7_pids = None
    app._x2t_logged_pids = set()
    app._cached_r7_path = None
    app._cached_cpu_count = None
    app.add_test_log = log
    return app


def find_hwnd_factory(stem):
    def _find():
        found = [None]

        def _cb(h, _):
            try:
                title = win32gui.GetWindowText(h)
            except Exception:
                return
            if stem in title or "Р7-Офис" in title or "R7-Office" in title:
                found[0] = h

        win32gui.EnumWindows(_cb, None)
        return found[0]

    return _find


def find_dialog_hwnd(substrings):
    needles = [s.lower() for s in substrings]
    found = [None]

    def _cb(h, _):
        if found[0] is not None:
            return
        if not win32gui.IsWindowVisible(h):
            return
        t = win32gui.GetWindowText(h).lower()
        if any(n in t for n in needles):
            found[0] = h

    win32gui.EnumWindows(_cb, None)
    return found[0]


def wait_dialog(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = find_dialog_hwnd(("сохранить как", "save as"))
        if h:
            return h
        time.sleep(0.1)
    return None


def refocus(find_hwnd):
    hwnd = find_hwnd()
    if not hwnd:
        return False
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        cx, cy = (left + right) // 2, top + 40
        pyautogui.click(cx, cy)
    except Exception as e:
        log(f"   ⚠️ refocus: клик не удался: {e}")
    time.sleep(0.3)
    return True


def main(argv):
    raw = argv[1] if len(argv) > 1 else DEFAULT_FILE
    test_file = Path(raw)
    if not test_file.is_absolute():
        test_file = BASE_DIR / raw
    if not test_file.exists():
        log(f"❌ Файл не найден: {test_file}")
        return 2

    app = make_app()
    r7_path = app._find_r7_path()
    if not r7_path:
        log("❌ Р7-Офис не найден")
        return 2

    procs = app._get_r7_processes(log_cb=log)
    if procs:
        pids = ", ".join(str(p.pid) for p in procs)
        log(f"⚠️ Р7-Офис уже запущен (PID: {pids}). Закройте его и повторите.")
        return 2

    log(f"Р7-Офис: {r7_path}")
    log(f"Файл: {test_file}")

    find_hwnd = find_hwnd_factory(test_file.stem[:12])
    subprocess.Popen([r7_path, str(test_file)])

    deadline = time.time() + 60
    hwnd = None
    while time.time() < deadline:
        hwnd = find_hwnd()
        if hwnd:
            break
        time.sleep(0.3)
    if not hwnd:
        log("❌ Окно Р7 не появилось за 60 сек")
        app._terminate_r7_processes(log_cb=log)
        return 2
    log(f"Окно найдено: {hwnd}")

    lines = []

    try:
        ready = app._wait_until_r7_ready(find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Файл открыт ({'данные загружены' if ready else 'таймаут ожидания'})")

        refocus(find_hwnd)
        time.sleep(0.3)

        log("⏳ Ctrl+Shift+S...")
        pyautogui.hotkey('ctrl', 'shift', 's')
        dlg_hwnd = wait_dialog(5.0)
        if not dlg_hwnd:
            log("❌ Диалог не появился")
        else:
            app_uia = Application(backend="uia").connect(handle=dlg_hwnd)
            dlg = app_uia.window(handle=dlg_hwnd)
            dlg.wait("exists", timeout=5)

            for ctrl_type in ("ComboBox", "Button", "Edit"):
                lines.append(f"=== {ctrl_type} ===")
                try:
                    elems = dlg.descendants(control_type=ctrl_type)
                except Exception as e:
                    lines.append(f"  <err descendants: {e}>")
                    elems = []
                for i, el in enumerate(elems):
                    try:
                        info = el.element_info
                        name = info.name
                        auto_id = info.automation_id
                        rect = info.rectangle
                        texts = None
                        if ctrl_type == "ComboBox":
                            try:
                                texts = el.texts()
                            except Exception as e:
                                texts = f"<err {e}>"
                        lines.append(f"  [{i}] name={name!r} auto_id={auto_id!r} rect={rect} texts={texts}")
                    except Exception as e:
                        lines.append(f"  [{i}] <err {e}>")

            OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
            log(f"✅ Дамп записан в {OUT_FILE} ({len(lines)} строк)")

            pyautogui.press('escape')
            time.sleep(0.3)

    finally:
        log("🔚 Закрытие Р7-Офис (без сохранения)...")
        try:
            h = find_hwnd()
            if h:
                app._close_r7_gracefully(h, log_cb=log, timeout=15)
        except Exception as e:
            log(f"⚠️ Закрытие не удалось ({type(e).__name__}: {e}) — завершаю процессы")
            app._terminate_r7_processes(log_cb=log)
        app._cleanup_x2t_temp_pdfs(log_cb=log)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
