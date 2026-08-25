"""Финальное разделение причины хронического сбоя Ctrl+Shift+S в реальном
_spreadsheet_worker(): собирает R7Testovarka через __new__() (минуя
__init__/setup_ui/detect_current_version — как в РАБОЧИХ пробниках
manual_saveas_uia_save.py и т.п.), но вызывает НАСТОЯЩИЙ метод
_spreadsheet_worker(), а не переизобретённую логику. Если хоткей теперь
срабатывает — причина в setup_ui()/полном __init__ (реальные виджеты).
Если всё ещё нет — причина в чём-то внутри самого _spreadsheet_worker,
не связанном с виджетами.

ЗАПУСК (закройте Р7-Офис перед стартом):
    .venv/Scripts/python.exe tests/manual_full_suite_bypass_init.py formats_only
"""
import sys
import json
import time
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import tkinter as tk  # noqa: E402
import r7_Testovarka as r7mod  # noqa: E402


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def make_worker_capable_app(root):
    """Стаб как в manual_saveas_uia_save.py (__new__, без __init__), но
    расширенный атрибутами, которые реально читает _spreadsheet_worker
    (см. разбор grep self\\. внутри тела метода, 26.08.2026)."""
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app.root = root
    app.add_test_log = log
    app.status_var = tk.StringVar(root)
    app.test_files_folder = BASE_DIR / "TestFiles"
    app.reports_folder = BASE_DIR / "Reports"
    app.current_version_info = None
    app._applied_r7_window_size = None
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
    app._cdp_ui_baseline = None
    return app


def main(argv):
    runs = 1
    only_formats = len(argv) > 1 and argv[1] == "formats_only"

    app_probe = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app_probe._cached_r7_path = None
    app_probe._cached_cpu_count = None
    app_probe._x2t_logged_pids = set()
    r7_path = app_probe._find_r7_path()
    if not r7_path:
        log("❌ Р7-Офис не найден")
        return 2
    procs = app_probe._get_r7_processes(log_cb=log)
    if procs:
        pids = ", ".join(str(p.pid) for p in procs)
        log(f"⚠️ Р7-Офис уже запущен (PID: {pids}). Закройте его и повторите.")
        return 2

    root = tk.Tk()
    root.withdraw()
    app = make_worker_capable_app(root)

    if only_formats:
        enabled = {n for n in app.TEST_DEFINITIONS if n in app.EXTRA_FORMAT_TESTS
                   or "PDF" in n}
        log("🧪 Только тесты форматов (без 12 CDP-тестов перед ними)")
    else:
        enabled = set(app.TEST_DEFINITIONS)
    test_runs = {name: runs for name in app.TEST_DEFINITIONS}
    log(f"Тестов включено: {len(enabled)}, __new__ вместо полного __init__ (без setup_ui)")

    stop_event = threading.Event()
    _exc_holder = []

    def _worker():
        try:
            app._spreadsheet_worker(enabled, test_runs, stop_event)
        except Exception as e:
            _exc_holder.append(e)
            import traceback
            traceback.print_exc()
        finally:
            root.after(0, root.quit)

    t0 = time.time()
    threading.Thread(target=_worker, daemon=True).start()
    root.mainloop()

    if _exc_holder:
        log(f"❌ _spreadsheet_worker упал: {type(_exc_holder[0]).__name__}: {_exc_holder[0]}")
    log(f"Полный прогон занял {(time.time() - t0) / 60:.1f} мин")

    try:
        root.destroy()
    except Exception:
        pass

    files = sorted(app.reports_folder.glob("performance_full_*.json"),
                    key=lambda p: p.stat().st_mtime)
    if not files:
        log("❌ Отчёт не создан")
        return 1
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    results = data.get("results", [])

    ok_count = fail_count = 0
    for r in results:
        if r.get("error"):
            fail_count += 1
            log(f"❌ {r['name']}: {r['error']}")
        else:
            ok_count += 1
            log(f"✅ {r['name']}: {r.get('time', 0):.3f} сек")

    log(f"ИТОГ: {ok_count} OK, {fail_count} FAIL из {len(results)}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
