"""Диагностика (без сохранения): переключается ли комбобокс «Тип файла» сам,
если печатать расширение ПОСИМВОЛЬНО (pyautogui.write), а не вставлять
целиком через буфер обмена (как делает нынешний save_as_format()).

Ничего не сохраняет — только печатает имя, читает состояние комбобокса
(CB_GETCURSEL, read-only — в отличие от CB_SETCURSEL это безопасно и
надёжно даже для DirectUI-диалогов) и жмёт Escape.

ЗАПУСК (закройте Р7-Офис перед стартом):
    .venv/Scripts/python.exe tests/manual_saveas_type_probe.py
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
import win32con             # noqa: E402

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

DEFAULT_FILE = "test_50k.xlsx"

COMBO_LABEL_BY_INDEX = {
    0: "xlsx", 1: "xltx", 2: "ods", 3: "xltm", 4: "ots",
    5: "csv", 6: "pdf", 7: "pdf/A", 8: "png", 9: "jpg",
}


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


def enum_children(parent_hwnd):
    children = []

    def _cb(h, _):
        children.append(h)

    win32gui.EnumChildWindows(parent_hwnd, _cb, None)
    return children


def find_controls(dlg_hwnd):
    edit_hwnd = None
    combo_hwnd = None
    for h in enum_children(dlg_hwnd):
        try:
            cls = win32gui.GetClassName(h)
        except Exception:
            continue
        if cls == "Edit" and win32gui.GetDlgCtrlID(h) == 1001:
            edit_hwnd = h
        elif cls == "ComboBox":
            count = win32gui.SendMessage(h, win32con.CB_GETCOUNT, 0, 0)
            if count and count > 1:
                combo_hwnd = h
    return edit_hwnd, combo_hwnd


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


def probe_typed_extension(ext, find_hwnd, first=False):
    log(f"--- Печатаю имя с расширением .{ext} (посимвольно) ---")
    if not first:
        refocus(find_hwnd)
        time.sleep(0.3)
        log("⏳ Ctrl+Shift+S...")
        pyautogui.hotkey('ctrl', 'shift', 's')
    dlg_hwnd = wait_dialog(5.0)
    if not dlg_hwnd:
        log(f"❌ Диалог не появился для .{ext}")
        return

    edit_hwnd, combo_hwnd = find_controls(dlg_hwnd)
    if not edit_hwnd or not combo_hwnd:
        log(f"❌ Не найдены контролы: edit={edit_hwnd} combo={combo_hwnd}")
        pyautogui.press('escape')
        return

    before_idx = win32gui.SendMessage(combo_hwnd, win32con.CB_GETCURSEL, 0, 0)
    log(f"   Комбобокс ДО ввода: индекс={before_idx} ({COMBO_LABEL_BY_INDEX.get(before_idx, '?')})")

    # Кликаем прямо по полю имени (реальный клик, не SendMessage) — гарантирует фокус
    left, top, right, bottom = win32gui.GetWindowRect(edit_hwnd)
    cx, cy = (left + right) // 2, (top + bottom) // 2
    pyautogui.click(cx, cy)
    time.sleep(0.1)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.1)

    basename = f"probe_{ext}_{int(time.time())}.{ext}"
    log(f"   Печатаю посимвольно: {basename}")
    pyautogui.write(basename, interval=0.03)
    time.sleep(0.4)

    edit_text = win32gui.GetWindowText(edit_hwnd)
    after_idx = win32gui.SendMessage(combo_hwnd, win32con.CB_GETCURSEL, 0, 0)
    log(f"   Поле имени теперь: {edit_text!r}")
    log(f"   Комбобокс ПОСЛЕ ввода: индекс={after_idx} ({COMBO_LABEL_BY_INDEX.get(after_idx, '?')})")

    if after_idx != before_idx:
        log(f"   ✅ АВТОПЕРЕКЛЮЧЕНИЕ СРАБОТАЛО для .{ext}!")
    else:
        log(f"   ❌ Комбобокс НЕ переключился для .{ext} (остался {COMBO_LABEL_BY_INDEX.get(after_idx, '?')})")

    log("   Escape (не сохраняю)...")
    pyautogui.press('escape')
    time.sleep(0.3)


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

    try:
        ready = app._wait_until_r7_ready(find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Файл открыт ({'данные загружены' if ready else 'таймаут ожидания'})")

        refocus(find_hwnd)
        time.sleep(0.3)
        pyautogui.hotkey('ctrl', 'shift', 's')

        for i, ext in enumerate(("ods", "csv", "xltx", "pdf")):
            probe_typed_extension(ext, find_hwnd, first=(i == 0))

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
