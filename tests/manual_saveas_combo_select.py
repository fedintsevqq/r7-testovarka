"""Разовая живая проверка: переключение комбобокса «Тип файла» в диалоге
«Сохранить как» через CB_SETCURSEL + CBN_SELCHANGE вместо надежды на
автоопределение формата по расширению в имени файла (которое, как
подтвердил tests/manual_saveas_dialog_dump.py, НЕ работает для ods/csv/xltx —
диалог остаётся на XLSX).

ЗАЧЕМ. Дамп показал, что диалог «Сохранить как» — стандартный Win32
common dialog (class '#32770'), а не HTML-модалка CEF. Комбобокс «Тип
файла» (10 пунктов) и поле имени (Edit id=1001) — обычные контролы,
доступные через SendMessage напрямую по hwnd, БЕЗ необходимости в
фокусе окна и синтетических клавишах pyautogui для самого выбора формата
(в отличие от глобального Ctrl+Shift+S, открывающего диалог).

ЧТО ДЕЛАЕТ:
  1. Открывает Р7 с тестовым файлом.
  2. Ctrl+Shift+S -> ждёт диалог.
  3. Для каждого формата (ods, csv, xltx) по очереди:
     - переоткрывает диалог (кроме первого раза);
     - CB_SETCURSEL на нужный индекс + WM_COMMAND/CBN_SELCHANGE родителю;
     - WM_SETTEXT в Edit 1001 — базовое имя БЕЗ расширения (пусть диалог
       сам допишет актуальное для выбранного типа);
     - BM_CLICK по кнопке «Сохранить» (id=1);
     - ждёт, что диалог исчезнет, и проверяет: какой файл реально
       появился в целевой папке (по паттерну basename.*).
  4. Печатает: сработало переключение типа или нет, для каждого формата.

НЕ ПРАВИТ save_as_format() — только проверяет гипотезу перед тем, как
переносить логику в r7_Testovarka.py.

ЗАПУСК (закройте Р7-Офис перед стартом):
    .venv/Scripts/python.exe tests/manual_saveas_combo_select.py
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
import win32api             # noqa: E402

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

DEFAULT_FILE = "test_50k.xlsx"
OUT_DIR = Path(r"E:\R7Manager\tests\_saveas_probe_out")

# Индексы подтверждены живым дампом 25.08.2026 (tests/manual_saveas_dialog_dump.py)
COMBO_INDEX = {"xlsx": 0, "xltx": 1, "ods": 2, "xltm": 3, "ots": 4,
               "csv": 5, "pdf": 6}


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
    """Возвращает (edit_hwnd, type_combo_hwnd) внутри диалога.

    Тип-комбобокс отличается от адресной строки ComboBox тем, что у него
    >1 пунктов (ComboBoxEx32/адресный combo пуст либо служебный)."""
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


def set_combo_selection(combo_hwnd, index):
    win32gui.SendMessage(combo_hwnd, win32con.CB_SETCURSEL, index, 0)
    parent = win32gui.GetParent(combo_hwnd)
    ctrl_id = win32gui.GetDlgCtrlID(combo_hwnd)
    wparam = win32api.MAKELONG(ctrl_id, win32con.CBN_SELCHANGE)
    win32gui.SendMessage(parent, win32con.WM_COMMAND, wparam, combo_hwnd)


def set_edit_text(edit_hwnd, text):
    win32gui.SendMessage(edit_hwnd, win32con.WM_SETTEXT, 0, text)


def click_save(dlg_hwnd):
    btn = win32gui.GetDlgItem(dlg_hwnd, 1)
    if not btn:
        return False
    win32gui.SendMessage(btn, win32con.BM_CLICK, 0, 0)
    return True


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


def probe_format(ext, find_hwnd, first=False):
    log(f"--- Формат .{ext} ---")
    if not first:
        refocus(find_hwnd)
        time.sleep(0.3)
        log("⏳ Ctrl+Shift+S...")
        pyautogui.hotkey('ctrl', 'shift', 's')
    dlg_hwnd = wait_dialog(5.0)
    if not dlg_hwnd:
        log(f"❌ Диалог не появился для .{ext}")
        return None

    edit_hwnd, combo_hwnd = find_controls(dlg_hwnd)
    if not edit_hwnd or not combo_hwnd:
        log(f"❌ Не найдены контролы: edit={edit_hwnd} combo={combo_hwnd}")
        pyautogui.press('escape')
        return None

    idx = COMBO_INDEX[ext]
    log(f"   Выставляю комбобокс на индекс {idx} ({ext})...")
    set_combo_selection(combo_hwnd, idx)
    time.sleep(0.3)

    cur = win32gui.SendMessage(combo_hwnd, win32con.CB_GETCURSEL, 0, 0)
    log(f"   Текущий индекс после SETCURSEL: {cur}")

    basename = f"probe_{ext}_{int(time.time())}"
    log(f"   Ввожу имя файла (без расширения): {basename}")
    set_edit_text(edit_hwnd, basename)
    time.sleep(0.2)

    edit_text_after = win32gui.GetWindowText(edit_hwnd)
    log(f"   Текст поля имени после SETTEXT: {edit_text_after!r}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log("   Жму «Сохранить»...")
    click_save(dlg_hwnd)

    # Диалог может остаться, если фокус/путь не туда — ждём исчезновения окна
    deadline = time.time() + 5.0
    gone = False
    while time.time() < deadline:
        if not win32gui.IsWindow(dlg_hwnd) or not win32gui.IsWindowVisible(dlg_hwnd):
            gone = True
            break
        time.sleep(0.1)
    log(f"   Диалог закрылся: {gone}")

    time.sleep(1.0)
    matches = list(BASE_DIR.glob(f"{basename}.*"))
    if matches:
        for m in matches:
            log(f"   ✅ Найден файл: {m.name} ({m.stat().st_size} байт)")
    else:
        log(f"   ⚠️ Файл {basename}.* НЕ найден в {BASE_DIR}")
    return matches


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

    results = {}
    try:
        ready = app._wait_until_r7_ready(find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Файл открыт ({'данные загружены' if ready else 'таймаут ожидания'})")

        refocus(find_hwnd)
        time.sleep(0.3)
        pyautogui.hotkey('ctrl', 'shift', 's')

        for i, ext in enumerate(("ods", "csv", "xltx")):
            results[ext] = probe_format(ext, find_hwnd, first=(i == 0))

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

    log("=" * 60)
    log("ИТОГ:")
    ok = True
    for ext, matches in results.items():
        if matches:
            log(f"  .{ext}: OK -> {[m.name for m in matches]}")
        else:
            log(f"  .{ext}: ПРОВАЛ — файл не создан")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
