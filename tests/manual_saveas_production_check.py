"""Проверка НОВЫХ методов класса R7Testovarka (_find_window_hwnd,
_uia_select_saveas_type, _dismiss_saveas_format_warning), добавленных для
фикса save_as_format() (этап 3/L2, 26.08.2026) — не переизобретает логику
отдельно, как предыдущие manual_saveas_uia_*.py, а дёргает ровно то, что
теперь реально вызывает продакшен-код в r7_Testovarka.py.

Воспроизводит тело save_as_format() построчно (без полного харнеса
_spreadsheet_worker — тому нужен настоящий self.root/status_var/etc.,
которых у этого разового скрипта нет).

ЗАПУСК (закройте Р7-Офис перед стартом):
    .venv/Scripts/python.exe tests/manual_saveas_production_check.py ods
    .venv/Scripts/python.exe tests/manual_saveas_production_check.py csv
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

import os                    # noqa: E402
import shutil                # noqa: E402
import subprocess           # noqa: E402
import r7_Testovarka as r7mod  # noqa: E402
import pyautogui            # noqa: E402
import win32gui              # noqa: E402

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
            if stem in title:
                found[0] = h

        win32gui.EnumWindows(_cb, None)
        return found[0]

    return _find


def clear_recovery_record(filename):
    """Удаляет осиротевшую запись восстановления для конкретного файла из
    %LOCALAPPDATA%/R7-Office/Editors/data/recover/<DE_xxxx>/ — иначе Р7 при
    следующем запуске показывает HTML-оверлей «Обнаружен файл блокировки,
    оставшийся после аварийного завершения работы» (см. run_crash_recovery.py),
    который не имеет отдельного HWND и перехватывает фокус/клавиатуру у
    всего окна документа, включая Ctrl+Shift+S.

    Найдено живым прогоном 26.08.2026: наши собственные force-kill в этой
    диагностической сессии создали именно такую запись для test_50k.xlsx и
    заблокировали все последующие попытки. Трогает ТОЛЬКО записи с этим
    именем файла — другие DE_* (чужие/более старые записи о реальных
    сбоях) не удаляются."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "R7-Office" / "Editors" / "data" / "recover"
    if not base.exists():
        return 0
    removed = 0
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        if (entry / filename).exists():
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed


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


def save_as_format_production(app, ext, main_hwnd=None):
    """Дословное тело save_as_format() из r7_Testovarka.py (_spreadsheet_worker),
    вызывающее ТЕ ЖЕ методы класса, что и настоящий прогон."""
    tmp_path = str(Path(__import__("os").environ.get("TEMP", ".")) /
                   f"temp_export_x2t_{int(time.time())}.{ext}")

    pyautogui.hotkey('ctrl', 'shift', 's')
    if not app._wait_for_window_title(("сохранить как", "save as"), timeout=5.0):
        log("   ⚠️ Первая попытка не открыла диалог, пробую ещё раз...")
        pyautogui.hotkey('ctrl', 'shift', 's')
        if not app._wait_for_window_title(("сохранить как", "save as"), timeout=5.0):
            raise RuntimeError("диалог «Сохранить как» не открылся")

    dlg_hwnd = app._find_window_hwnd("сохранить как", "save as")
    log(f"   dlg_hwnd={dlg_hwnd}")
    # _uia_select_saveas_type теперь набирает путь и жмёт «Сохранить» сама,
    # целиком через UI Automation (см. её docstring, 27.08.2026) — раньше
    # (до этой правки) это делал pyperclip+Ctrl+A/Ctrl+V+Enter здесь, что и
    # оказалось корнем бага «файл экспорта никогда не появляется»: Р7
    # игнорировал вставленный путь и сохранял под исходным именем документа.
    if dlg_hwnd is None or not app._uia_select_saveas_type(dlg_hwnd, ext, tmp_path, log_cb=log):
        raise RuntimeError(f"не удалось сохранить в .{ext} через UI Automation")

    app._dismiss_saveas_format_warning(dlg_hwnd, main_hwnd=main_hwnd, timeout=3.0, log_cb=log)

    ok = app._wait_for_export_file(tmp_path, log_cb=log)
    return ok, tmp_path


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

    cleared = app._clear_r7_cache()
    log(f"🧹 Очищен кэш Р7 в %TEMP% ({cleared} объектов)")
    rec_cleared = clear_recovery_record(test_file.name)
    log(f"🧹 Удалено записей восстановления для {test_file.name!r}: {rec_cleared}")
    lock_file = test_file.parent / f"~${test_file.name}"
    if lock_file.exists():
        lock_file.unlink()
        log(f"🧹 Удалён lock-файл {lock_file.name!r} (главная причина диалога "
            f"«Обнаружен файл блокировки» — не папка recover/, как думали раньше)")

    log(f"Р7-Офис: {r7_path}")
    log(f"Файл: {test_file}")
    log(f"Формат: .{ext}")

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

    ok = False
    tmp_path = None
    try:
        ready = app._wait_until_r7_ready(find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Файл открыт ({'данные загружены' if ready else 'таймаут ожидания'})")

        refocus(find_hwnd)
        time.sleep(0.3)

        ok, tmp_path = save_as_format_production(app, ext, main_hwnd=hwnd)

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
        app._close_webdriver_connector()

    if ok and tmp_path:
        p = Path(tmp_path)
        log(f"✅ ИТОГ: {p.name} ({p.stat().st_size} байт)")
        try:
            p.unlink()
        except Exception:
            pass
    else:
        log("❌ ИТОГ: экспорт не завершился (файл не появился)")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
