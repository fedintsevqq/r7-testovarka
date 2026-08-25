"""Ручной прогон save_as_format (PDF/ODS/CSV/XLTX) на ЖИВОМ Р7-Офис.

ЗАЧЕМ. Живой прогон вкладки «Производительность» (25.08.2026, WEBDRIVER_OK=False)
поймал баг: когда диалог «Сохранить как» не открывался ни хоткеем Ctrl+Shift+S,
ни через меню Файл, save_as_format() всё равно слепо слала Ctrl+A → Ctrl+V →
Enter — в то окно, что было в фокусе на тот момент (не обязательно Р7; на
стенде параллельно были открыты Chrome, PowerShell, Steam и т.д.). Исправлено:
теперь при неоткрывшемся диалоге функция бросает RuntimeError и ничего не шлёт.
Этот скрипт — единственный способ проверить фикс на реальном Р7-Офис: у автора
правки нет инструмента для управления Tkinter-интерфейсом R7Testovarka
(чекбоксы/кнопка «Запустить»), поэтому тест-функция гоняется напрямую, в обход
всего UI, тем же способом, что и tests/manual_cdp_smoke.py для CDP-операций.

ЧТО ПРОВЕРЯЕТСЯ: все четыре формата (pdf/ods/csv/xltx) реально открывают
диалог «Сохранить как» и доводят экспорт до файла на диске — то есть основной
путь работает и фикс НЕ ломает штатный сценарий (сам abort-путь на баг
воспроизвести искусственно нечем — он зависит от того, что диалог Р7 не
появится, а провоцировать такое на живом стенде не из чего).

ПОЧЕМУ НЕ pytest-ТЕСТ. Имя файла намеренно не начинается с `test_`: pytest
его не собирает. Запуск открывает окно редактора и шлёт реальные клавиши —
такому не место в автоматическом прогоне.

ЗАПУСК (закройте Р7-Офис перед стартом — файл иначе уйдёт в уже открытое
окно, а скрипт его не дождётся):
    .venv/Scripts/python.exe tests/manual_saveas_smoke.py                # test_50k.xlsx
    .venv/Scripts/python.exe tests/manual_saveas_smoke.py test_10k.xlsx
    .venv/Scripts/python.exe tests/manual_saveas_smoke.py C:/путь/свой.xlsx

Код возврата 0 — все четыре формата экспортировались; 1 — хотя бы один нет.

ВНИМАНИЕ: скрипт шлёт реальные Ctrl+Shift+S/Ctrl+A/Ctrl+V/Enter через
pyautogui. Не трогайте клавиатуру/мышь и не переключайте окна, пока он
работает (около 1–2 минут на файл).
"""
import subprocess
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

import r7_Testovarka as r7mod  # noqa: E402
import pyautogui               # noqa: E402
import pyperclip                # noqa: E402

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

DEFAULT_FILE = "test_50k.xlsx"
FORMATS = ("pdf", "ods", "csv", "xltx")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def make_app():
    """«Голый» R7Testovarka без Tk — см. make_app() в manual_cdp_smoke.py."""
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
    import win32gui

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


def warn_if_r7_running(app):
    procs = app._get_r7_processes(log_cb=log)
    if procs:
        pids = ", ".join(str(p.pid) for p in procs)
        log(f"⚠️ Р7-Офис уже запущен (PID: {pids}). Закройте его и повторите — "
            f"иначе файл откроется в существующем окне.")
        return True
    return False


def save_as_format(app, ext):
    """Дословная копия save_as_format() из r7_Testovarka.py (обе точки —
    _spreadsheet_worker и _batch_run_single_version — идентичны после фикса).

    Возвращает True, если диалог открылся и экспорт дошёл до файла на диске;
    False — если диалог не появился (в этом случае, как и в исправленном
    коде, НИКАКИЕ клавиши дальше не шлются).
    """
    tmp_path = str(Path(__import__("os").environ.get("TEMP", ".")) /
                   f"temp_export_x2t_{int(time.time())}.{ext}")
    app._op_start_grace = app.OP_PDF_GRACE_SEC

    pyautogui.hotkey('ctrl', 'shift', 's')
    t_dlg = time.time()
    if not app._wait_for_window_title(("сохранить как", "save as"), timeout=3.0):
        app._paced_total += time.time() - t_dlg
        log("   ⚠️ Ctrl+Shift+S не открыл диалог, пробуем меню Файл")
        pyautogui.hotkey('alt', 'f')
        app._pace(0.3)
        for _ in range(3):
            pyautogui.press('down')
            app._pace(0.3)
        pyautogui.press('enter')
        if not app._wait_for_window_title(("сохранить как", "save as"), timeout=3.0):
            log("   ⚠️ Диалог «Сохранить как» не появился и через меню Файл")
            app._dump_visible_window_titles(log)
            log("   ❌ Отмена: без диалога Ctrl+A/Ctrl+V/Enter ушли бы в то "
                "окно, что сейчас в фокусе — клавиши НЕ шлю (это и есть фикс)")
            return False

    pyperclip.copy(tmp_path)
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.hotkey('ctrl', 'v')
    app._pace(0.3)
    pyautogui.press('enter')
    return app._wait_for_export_file(tmp_path, log_cb=log)


def refocus(find_hwnd):
    """Восстанавливает фокус ОС на окне Р7 перед каждой операцией.

    В production-коде (`focus_window()` внутри `_spreadsheet_worker`) это
    голый `SetForegroundWindow`, и там это надёжно работает, потому что сам
    Tkinter-процесс уже держит настоящий фокус ввода в момент запуска теста
    (пользователь только что кликнул кнопку «Запустить»). У ЭТОГО скрипта
    такой истории ввода нет — он стартует из фонового процесса (Bash-тула
    Claude Code), и голый `SetForegroundWindow` от процесса без фокуса
    Windows тихо игнорирует (foreground lock) — без исключения, диалог
    просто не появляется. Клик по клиентской области окна — не часть
    исправляемого бага, а компенсация ИМЕННО этого способа запуска скрипта:
    реальный (пусть и синтетический через SendInput) клик активирует окно
    как побочный эффект обработки WM_LBUTTONDOWN, в обход ограничения на
    SetForegroundWindow.
    """
    import win32gui
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
        log(f"   🔍 refocus: rect=({left},{top},{right},{bottom}) клик по ({cx},{cy})")
        pyautogui.click(cx, cy)
    except Exception as e:
        log(f"   ⚠️ refocus: клик не удался: {e}")
    time.sleep(0.3)
    try:
        fg = win32gui.GetForegroundWindow()
        fg_title = win32gui.GetWindowText(fg)
        log(f"   🔍 refocus: реальный foreground сейчас = {fg} ({fg_title!r}), целевой = {hwnd}")
    except Exception:
        pass
    return True


def run_format(app, find_hwnd, ext):
    app._paced_total = 0.0
    app._op_start_grace = None
    log(f"⏳ Сохранение в {ext.upper()}")
    refocus(find_hwnd)
    t0 = time.time()
    try:
        ok = save_as_format(app, ext)
    except Exception as e:
        log(f"   ❌ исключение: {type(e).__name__}: {e}")
        return False, 0.0
    done_ts, status = app._wait_operation_done(find_hwnd, log_cb=log)
    elapsed = (time.time() - t0 - app._paced_total if status == "timeout"
               else max(0.0, done_ts - t0 - app._paced_total))
    time.sleep(0.5)
    verdict = "✅ файл экспортирован" if ok else "❌ диалог не открылся"
    log(f"   ⏱ {ext.upper()}: {elapsed:.3f} сек [{status}] — {verdict}")
    return ok, elapsed


def main(argv):
    raw = argv[1] if len(argv) > 1 else DEFAULT_FILE
    test_file = Path(raw)
    if not test_file.is_absolute():
        test_file = BASE_DIR / raw
    if not test_file.exists() and test_file.parent.exists():
        # Аргументы shell (Bash-инструмент/Git Bash) и путь на диске могут
        # нести один и тот же кириллический символ в разной Unicode-форме
        # (NFC/NFD — например «й» одним кодпоинтом против «и»+U+0306).
        # Байтово они не совпадают, хотя выглядят одинаково, поэтому ищем
        # по нормализованному имени, а не полагаемся на точное совпадение.
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
    log(f"Р7-Офис: {r7_path}")
    log(f"Файл: {test_file} ({test_file.stat().st_size / 1024:.0f} КБ)")
    if warn_if_r7_running(app):
        return 2

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

    results = []
    try:
        ready = app._wait_until_r7_ready(find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Файл открыт ({'данные загружены' if ready else 'таймаут ожидания'})")
        try:
            import win32gui
            win32gui.SetForegroundWindow(find_hwnd())
        except Exception:
            pass
        time.sleep(0.5)

        for ext in FORMATS:
            ok, elapsed = run_format(app, find_hwnd, ext)
            results.append((ext, ok, elapsed))

        log("─" * 50)
        log("ИТОГ:")
        for ext, ok, elapsed in results:
            log(f"   {'✅' if ok else '❌'} {ext.upper():5s} {elapsed:7.3f} с")
        failed = [ext for ext, ok, _ in results if not ok]
        if failed:
            log(f"❌ Не прошли: {', '.join(failed)}")
        else:
            log("✅ Все четыре формата экспортированы")
    finally:
        log("🔚 Закрытие Р7-Офис (без сохранения)...")
        try:
            app._close_r7_gracefully(find_hwnd(), log_cb=log, timeout=15)
        except Exception as e:
            log(f"⚠️ Закрытие не удалось ({type(e).__name__}: {e}) — завершаю процессы")
            app._terminate_r7_processes(log_cb=log)
        app._cleanup_x2t_temp_pdfs(log_cb=log)

    return 1 if any(not ok for _, ok, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
