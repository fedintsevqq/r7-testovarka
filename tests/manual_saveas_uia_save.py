"""Живая проверка полного цикла: открыть диалог «Сохранить как», через UIA
переключить «Тип файла» на ODS (auto_id='FileTypeControlHost'), ввести имя
и нажать «Сохранить» (auto_id='1') — и убедиться, что на диске появляется
настоящий .ods, а не .xlsx с двойным расширением.

auto_id контролов подтверждены живым дампом 26.08.2026
(tests/manual_saveas_uia_probe2.py, tests/_uia_dump.txt):
  FileNameControlHost / auto_id=1001 — поле имени файла
  FileTypeControlHost                — комбобокс типа файла
  auto_id='1' name='Сохранить'       — кнопка сохранения
  auto_id='2' name='Отмена'          — кнопка отмены

ЗАПУСК (закройте Р7-Офис перед стартом):
    .venv/Scripts/python.exe tests/manual_saveas_uia_save.py
    .venv/Scripts/python.exe tests/manual_saveas_uia_save.py ods
    .venv/Scripts/python.exe tests/manual_saveas_uia_save.py csv
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
import win32con              # noqa: E402

from pywinauto import Application  # noqa: E402

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

DEFAULT_FILE = "test_50k.xlsx"

FORMAT_LABELS = {
    "xlsx": "(*.xlsx)",
    "xltx": "(*.xltx)",
    "ods": "(*.ods)",
    "xltm": "(*.xltm)",
    "ots": "(*.ots)",
    "csv": "(*.csv)",
    "pdf": "(*.pdf)",
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


def save_as(ext, find_hwnd, out_dir):
    log(f"⏳ Ctrl+Shift+S...")
    pyautogui.hotkey('ctrl', 'shift', 's')
    dlg_hwnd = wait_dialog(5.0)
    if not dlg_hwnd:
        log("❌ Диалог не появился")
        return None

    app_uia = Application(backend="uia").connect(handle=dlg_hwnd)
    dlg = app_uia.window(handle=dlg_hwnd)
    dlg.wait("exists", timeout=5)

    type_combo = dlg.child_window(auto_id="FileTypeControlHost", control_type="ComboBox")
    name_edit = dlg.child_window(auto_id="1001", control_type="Edit")
    save_btn = dlg.child_window(auto_id="1", control_type="Button")

    label_needle = FORMAT_LABELS[ext]
    log(f"Разворачиваю комбобокс типа файла, ищу пункт с {label_needle!r}...")
    try:
        type_combo.expand()
    except Exception as e:
        log(f"   ⚠️ expand() не сработал: {e}")

    time.sleep(0.3)
    target_item = None
    try:
        # dlg.descendants() отдаёт и пункты выпадающего списка, и список
        # файлов текущей папки (там же может быть .claude и т.п.) — поэтому
        # матчим строго по литералу "(*.ext)", который есть только у пунктов
        # формата, и берём ПЕРВОЕ совпадение (иначе "(*.pdf)" зацепит и
        # обычный pdf, и pdf/A — нужен именно первый, plain-вариант).
        items = dlg.descendants(control_type="ListItem")
        log(f"   Пунктов ListItem в дереве диалога: {len(items)}")
        for it in items:
            nm = it.element_info.name or ""
            if label_needle.lower() in nm.lower():
                log(f"     -> совпадение: {nm!r}")
                target_item = it
                break
    except Exception as e:
        log(f"   ⚠️ Не удалось получить ListItem: {e}")

    if target_item is None:
        log(f"❌ Пункт для .{ext} не найден в развёрнутом списке")
        pyautogui.press('escape')
        pyautogui.press('escape')
        return None

    log(f"   Кликаю по пункту: {target_item.element_info.name!r}")
    target_item.click_input()
    time.sleep(0.3)

    cur_texts = type_combo.texts()
    log(f"   Комбобокс после выбора: {cur_texts}")

    basename = f"probe_{ext}_{int(time.time())}"
    log(f"Ввожу имя файла: {basename}")
    try:
        name_edit.click_input()
        time.sleep(0.15)
        name_edit.type_keys("^a", pause=0.02)
        name_edit.type_keys(basename, with_spaces=True, pause=0.02)
    except Exception as e:
        log(f"   ⚠️ Ввод имени через type_keys не сработал: {e}")

    time.sleep(0.3)
    try:
        val = name_edit.get_value()
    except Exception:
        val = "?"
    log(f"   Значение поля имени сейчас: {val!r}")

    log("Жму «Сохранить»...")
    save_btn.click_input()

    deadline = time.time() + 8.0
    gone = False
    while time.time() < deadline:
        if not win32gui.IsWindow(dlg_hwnd) or not win32gui.IsWindowVisible(dlg_hwnd):
            gone = True
            break
        time.sleep(0.1)
    log(f"Диалог «Сохранить как» закрылся: {gone}")

    # Некоторые форматы (CSV — точно, возможно и другие) после «Сохранить»
    # показывают ВТОРОЙ диалог — предупреждение о потере функций формата
    # (многолистовость/форматирование), с кнопками OK/Отмена. Найден живым
    # прогоном 26.08.2026: без обработки этого диалога сохранение зависает
    # незавершённым, файл не появляется.
    time.sleep(0.5)
    confirm_hwnd = None
    deadline2 = time.time() + 3.0
    while time.time() < deadline2:
        h = find_dialog_hwnd(("р7-офис", "r7-office"))
        if h and h != dlg_hwnd:
            confirm_hwnd = h
            break
        time.sleep(0.2)

    if confirm_hwnd:
        log(f"⚠️ Обнаружен второй диалог (предупреждение формата): hwnd={confirm_hwnd}")
        # Кнопка вложена глубже прямых детей (под DirectUIHWND) — нужен
        # рекурсивный обход, FindWindowEx (только прямые дети) её не видит.
        ok_btn = [None]

        def _find_ok(h, _):
            if ok_btn[0] is not None:
                return
            try:
                if win32gui.GetClassName(h) == "Button" and win32gui.GetWindowText(h) == "OK":
                    ok_btn[0] = h
            except Exception:
                pass

        win32gui.EnumChildWindows(confirm_hwnd, _find_ok, None)
        if ok_btn[0]:
            log(f"   Жму OK (hwnd={ok_btn[0]}) — сохранить в выбранном формате...")
            win32gui.SendMessage(ok_btn[0], win32con.BM_CLICK, 0, 0)
        else:
            log("   ⚠️ Кнопка OK не найдена в диалоге-предупреждении")
        time.sleep(1.0)

    time.sleep(1.5)
    matches = list(out_dir.glob(f"{basename}.*"))
    for m in matches:
        log(f"   ✅ Найден файл: {m.name} ({m.stat().st_size} байт)")
    if not matches:
        log(f"   ⚠️ Файл {basename}.* НЕ найден в {out_dir}")
    return matches


def main(argv):
    ext = argv[1] if len(argv) > 1 else "ods"
    raw = argv[2] if len(argv) > 2 else DEFAULT_FILE
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
    log(f"Формат для проверки: .{ext}")

    cleared = app._clear_r7_cache()
    log(f"🧹 Очищен кэш Р7 в %TEMP% ({cleared} объектов) — избегаем диалога восстановления")

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
    log(f"Окно найдено: {hwnd} title={win32gui.GetWindowText(hwnd)!r}")

    matches = None
    try:
        ready = app._wait_until_r7_ready(find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Файл открыт ({'данные загружены' if ready else 'таймаут ожидания'})")

        refocus(find_hwnd)
        time.sleep(0.3)

        matches = save_as(ext, find_hwnd, BASE_DIR)

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

    return 0 if matches else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
