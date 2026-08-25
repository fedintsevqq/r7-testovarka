"""Разовый дамп контролов диалога «Сохранить как» через win32gui.

ЗАЧЕМ. save_as_format() для ods/csv/xltx сохраняет обычную XLSX-копию
(подтверждено живым прогоном 25.08.2026 — см. CLAUDE.md, раздел L2) — диалог
«Сохранить как» открывается, но комбобокс «Тип файла» остаётся на XLSX, и
вставка расширения в поле имени файла его не переключает. Чтобы починить
экспорт, нужно увидеть реальные дочерние контролы диалога (класс, control
id, текст, rect) — в первую очередь combobox «Тип файла» и его пункты, а
также убедиться, что это вообще стандартный Win32-диалог (ComboBox/Edit —
enumerable), а не что-то, что win32gui не видит (как кнопки в HTML-модалках
CEF в остальных частях этого приложения).

НЕ ПРАВИТ ничего в save_as_format — только смотрит и логирует. Диалог
закрывается через Escape (безопасно — как и Cancel, ничего не портит,
это тот же случай, что и BLOCKING_DIALOG_TITLES в r7_Testovarka.py).

ПОЧЕМУ НЕ pytest-тест: имя не начинается с test_, открывает реальное окно
и шлёт реальные клавиши — как manual_cdp_smoke.py / manual_saveas_smoke.py.

ЗАПУСК (закройте Р7-Офис перед стартом):
    .venv/Scripts/python.exe tests/manual_saveas_dialog_dump.py
    .venv/Scripts/python.exe tests/manual_saveas_dialog_dump.py путь/к/файлу.xlsx

ВНИМАНИЕ: шлёт реальные Ctrl+Shift+S через pyautogui. Не трогайте
клавиатуру/мышь, пока скрипт работает (~30 сек).
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
    """Возвращает hwnd первого видимого top-level окна, чей заголовок
    содержит одну из подстрок (без учёта регистра) — не просто bool, как
    R7Testovarka._win_title_contains, а сам дескриптор, нужный для дампа."""
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


def refocus(find_hwnd):
    """См. одноимённую функцию в manual_saveas_smoke.py — без реального
    клика окно не получает фокус, т.к. сам процесс запущен из фонового
    Bash-инструмента без истории ввода (foreground lock)."""
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


def dump_combobox_items(h):
    """CB_GETLBTEXT нужен отдельный буфер ctypes — win32gui.SendMessage не
    маршалит строки для этого сообщения."""
    import ctypes
    items = []
    try:
        count = win32gui.SendMessage(h, win32con.CB_GETCOUNT, 0, 0)
    except Exception:
        return items
    for i in range(count):
        try:
            length = win32gui.SendMessage(h, win32con.CB_GETLBTEXTLEN, i, 0)
            if length < 0:
                continue
            buf = ctypes.create_unicode_buffer(length + 1)
            win32gui.SendMessage(h, win32con.CB_GETLBTEXT, i, buf)
            items.append(buf.value)
        except Exception as e:
            items.append(f"<err {e}>")
    return items


def enum_children(parent_hwnd):
    children = []

    def _cb(h, _):
        children.append(h)

    win32gui.EnumChildWindows(parent_hwnd, _cb, None)
    return children


def main(argv):
    raw = argv[1] if len(argv) > 1 else DEFAULT_FILE
    test_file = Path(raw)
    if not test_file.is_absolute():
        test_file = BASE_DIR / raw
    if not test_file.exists() and test_file.parent.exists():
        import unicodedata
        target = unicodedata.normalize("NFC", test_file.name)
        for candidate in test_file.parent.iterdir():
            if unicodedata.normalize("NFC", candidate.name) == target:
                test_file = candidate
                break
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

        log("⏳ Ctrl+Shift+S...")
        pyautogui.hotkey('ctrl', 'shift', 's')
        dlg_hwnd = None
        deadline = time.time() + 5.0
        while time.time() < deadline:
            dlg_hwnd = find_dialog_hwnd(("сохранить как", "save as"))
            if dlg_hwnd:
                break
            time.sleep(0.1)

        if not dlg_hwnd:
            log("❌ Диалог «Сохранить как» не найден через EnumWindows")
            log("   Пробую меню Файл...")
            pyautogui.hotkey('alt', 'f')
            time.sleep(0.3)
            for _ in range(3):
                pyautogui.press('down')
                time.sleep(0.2)
            pyautogui.press('enter')
            deadline = time.time() + 5.0
            while time.time() < deadline:
                dlg_hwnd = find_dialog_hwnd(("сохранить как", "save as"))
                if dlg_hwnd:
                    break
                time.sleep(0.1)

        if not dlg_hwnd:
            log("❌ Диалог так и не появился ни одним путём — дампить нечего")
            log("   Видимые окна сейчас:")
            app._dump_visible_window_titles(log, limit=30)
        else:
            title = win32gui.GetWindowText(dlg_hwnd)
            cls = win32gui.GetClassName(dlg_hwnd)
            rect = win32gui.GetWindowRect(dlg_hwnd)
            log(f"✅ Диалог найден: hwnd={dlg_hwnd} class={cls!r} title={title!r} rect={rect}")

            children = enum_children(dlg_hwnd)
            log(f"Дочерних контролов: {len(children)}")
            log("-" * 90)
            log(f"{'hwnd':>10} {'class':<22} {'id':>6} {'vis':>4}  text")
            log("-" * 90)
            combo_hwnds = []
            for h in children:
                try:
                    ccls = win32gui.GetClassName(h)
                except Exception:
                    ccls = "?"
                try:
                    ctext = win32gui.GetWindowText(h)
                except Exception:
                    ctext = ""
                try:
                    cid = win32gui.GetDlgCtrlID(h)
                except Exception:
                    cid = -1
                try:
                    vis = win32gui.IsWindowVisible(h)
                except Exception:
                    vis = "?"
                log(f"{h:>10} {ccls:<22} {cid:>6} {str(vis):>4}  {ctext!r}")
                if ccls == "ComboBox":
                    combo_hwnds.append(h)

            log("-" * 90)
            if not combo_hwnds:
                log("⚠️ Ни одного ComboBox среди дочерних контролов не найдено")
                log("   (значит диалог не стандартный Win32 common-dialog, либо")
                log("   «Тип файла» реализован другим классом контрола)")
            for ch in combo_hwnds:
                items = dump_combobox_items(ch)
                cur = win32gui.SendMessage(ch, win32con.CB_GETCURSEL, 0, 0)
                log(f"ComboBox hwnd={ch}: {len(items)} пунктов, текущий индекс={cur}")
                for idx, it in enumerate(items):
                    marker = " <-- ТЕКУЩИЙ" if idx == cur else ""
                    log(f"      [{idx}] {it!r}{marker}")

            log("Закрываю диалог (Escape)...")
            pyautogui.press('escape')
            time.sleep(0.5)

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
