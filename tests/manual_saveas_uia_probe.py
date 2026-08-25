"""UIA-диагностика диалога «Сохранить как» через pywinauto (backend='uia').

ЗАЧЕМ. tests/manual_saveas_dialog_dump.py и tests/manual_saveas_combo_select.py
подтвердили: диалог «Сохранить как» — это современный IFileDialog с
DirectUI-прослойкой (DUIViewWndClassName и т.п.), где сырые Win32-сообщения
(CB_SETCURSEL, WM_SETTEXT) к контролам класса 'ComboBox'/'Edit' либо не
долетают до реальной логики, либо это вообще не те окна, что реально что-то
показывают: даже посимвольная печать реальными клавишами (pyautogui.write)
в найденный Edit id=1001 не отразилась в GetWindowText. Нужен API, который
понимает UI Automation дерево этого диалога, а не голый win32gui.

ЧТО ДЕЛАЕТ:
  1. Открывает Р7 с тестовым файлом, Ctrl+Shift+S -> ждёт диалог (hwnd уже
     умеем находить через win32gui, как в предыдущих скриптах).
  2. pywinauto.Application(backend='uia').connect(handle=dlg_hwnd) — цепляется
     к уже открытому окну.
  3. Печатает дерево UIA-контролов диалога (control_type, texts, auto_id) —
     чтобы увидеть, как реально называются поле имени и комбобокс типа.
  4. Пытается: combo.select(<label с "ods">), edit.set_edit_text(basename),
     клик по кнопке "Сохранить".
  5. Проверяет, какой файл реально появился на диске.

НЕ ТРОГАЕТ save_as_format() — только проверка перед переносом логики.

ЗАПУСК (закройте Р7-Офис перед стартом):
    .venv/Scripts/python.exe tests/manual_saveas_uia_probe.py
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
from pywinauto.findwindows import ElementNotFoundError  # noqa: E402

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


def dump_uia_tree(dlg_wrapper, max_depth=4):
    log("UIA-дерево диалога:")

    def _walk(elem, depth):
        if depth > max_depth:
            return
        try:
            info = elem.element_info
            ctype = info.control_type
            name = info.name
            auto_id = info.automation_id
            rect = info.rectangle
        except Exception as e:
            log(f"{'  '*depth}<err {e}>")
            return
        log(f"{'  '*depth}[{ctype}] name={name!r} auto_id={auto_id!r} rect={rect}")
        try:
            children = elem.children()
        except Exception:
            children = []
        for c in children:
            _walk(c, depth + 1)

    _walk(dlg_wrapper, 0)


def probe(find_hwnd):
    log("⏳ Ctrl+Shift+S...")
    pyautogui.hotkey('ctrl', 'shift', 's')
    dlg_hwnd = wait_dialog(5.0)
    if not dlg_hwnd:
        log("❌ Диалог не появился")
        return False

    app_uia = Application(backend="uia").connect(handle=dlg_hwnd)
    dlg = app_uia.window(handle=dlg_hwnd)
    dlg.wait("exists", timeout=5)

    dump_uia_tree(dlg, max_depth=5)

    # Ищем комбобокс и поле имени по control_type (без привязки к точному
    # тексту метки — она может отличаться от ожиданий).
    try:
        combos = dlg.descendants(control_type="ComboBox")
    except Exception as e:
        log(f"❌ Не удалось получить список ComboBox: {e}")
        combos = []
    try:
        edits = dlg.descendants(control_type="Edit")
    except Exception as e:
        log(f"❌ Не удалось получить список Edit: {e}")
        edits = []

    log(f"Найдено ComboBox: {len(combos)}, Edit: {len(edits)}")
    for i, c in enumerate(combos):
        try:
            log(f"  Combo[{i}]: name={c.element_info.name!r} auto_id={c.element_info.automation_id!r}")
        except Exception as e:
            log(f"  Combo[{i}]: <err {e}>")
    for i, e_ in enumerate(edits):
        try:
            log(f"  Edit[{i}]: name={e_.element_info.name!r} auto_id={e_.element_info.automation_id!r} value={e_.get_value() if hasattr(e_, 'get_value') else '?'!r}")
        except Exception as e:
            log(f"  Edit[{i}]: <err {e}>")

    # Пробуем найти комбобокс с >1 пунктом (тип файла) и Edit с типом "имя файла"
    type_combo = None
    for c in combos:
        try:
            texts = c.texts()
        except Exception:
            texts = []
        if len(texts) > 1 or (c.element_info.automation_id or "").lower() in ("filetypescombo", "1136"):
            type_combo = c
            break
    if type_combo is None and combos:
        type_combo = combos[0]

    name_edit = None
    for e_ in edits:
        auto_id = (e_.element_info.automation_id or "").lower()
        if auto_id in ("1148", "filenameedit") or "имя" in (e_.element_info.name or "").lower():
            name_edit = e_
            break
    if name_edit is None and edits:
        name_edit = edits[0]

    if type_combo is None or name_edit is None:
        log(f"❌ Не удалось идентифицировать комбобокс/поле: combo={type_combo} edit={name_edit}")
        pyautogui.press('escape')
        return False

    log("Пытаюсь выбрать пункт с 'OpenDocument' в комбобоксе типа...")
    try:
        items = type_combo.texts()
        log(f"   Пункты комбобокса (UIA): {items}")
    except Exception as e:
        log(f"   ⚠️ Не удалось прочитать items: {e}")

    try:
        type_combo.select("Электронная таблица OpenDocument (*.ods)")
        log("   ✅ select() по точному тексту сработал")
    except Exception as e:
        log(f"   ⚠️ select() по тексту не сработал: {e}")
        try:
            type_combo.select(2)  # индекс ods по прошлому дампу win32
            log("   ✅ select(2) по индексу сработал")
        except Exception as e2:
            log(f"   ❌ select(2) тоже не сработал: {e2}")

    time.sleep(0.3)
    basename = f"probe_uia_{int(time.time())}"
    log(f"Ввожу имя файла: {basename}")
    try:
        name_edit.set_edit_text(basename)
        log(f"   ✅ set_edit_text сработал, value={name_edit.get_value() if hasattr(name_edit, 'get_value') else '?'}")
    except Exception as e:
        log(f"   ⚠️ set_edit_text не сработал: {e}")

    time.sleep(0.3)

    log("Ищу кнопку 'Сохранить'...")
    try:
        save_btn = dlg.child_window(title_re=".*охранить.*", control_type="Button")
        save_btn.click_input()
        log("   ✅ Клик по кнопке 'Сохранить'")
    except Exception as e:
        log(f"   ❌ Не удалось кликнуть Сохранить: {e}")
        pyautogui.press('escape')
        return False

    deadline = time.time() + 5.0
    gone = False
    while time.time() < deadline:
        if not win32gui.IsWindow(dlg_hwnd) or not win32gui.IsWindowVisible(dlg_hwnd):
            gone = True
            break
        time.sleep(0.1)
    log(f"Диалог закрылся: {gone}")

    time.sleep(1.0)
    matches = list(BASE_DIR.glob(f"{basename}.*"))
    if matches:
        for m in matches:
            log(f"   ✅ Найден файл: {m.name} ({m.stat().st_size} байт)")
        return True
    else:
        log(f"   ⚠️ Файл {basename}.* НЕ найден в {BASE_DIR}")
        return False


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

    ok = False
    try:
        ready = app._wait_until_r7_ready(find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Файл открыт ({'данные загружены' if ready else 'таймаут ожидания'})")

        refocus(find_hwnd)
        time.sleep(0.3)

        ok = probe(find_hwnd)

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

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
