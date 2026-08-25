"""Полный прогон ВСЕХ тестов вкладки «Производительность» на реальном
рабочем фикстур-файле TestFiles/файл-для-теста-Р7-офис-50К.xlsx — через
настоящий `_spreadsheet_worker` (не переизобретённые куски, как в прежних
ручных пробниках), чтобы подтвердить, что все 16 операций (включая три
формата экспорта, зафиксированные UIA-фиксом 26.08.2026) реально
отрабатывают на продакшен-пути, а не только в изолированных скриптах.

Строит настоящий R7Testovarka(root) — тот же путь, что и обычный запуск
приложения (setup_ui, detect_current_version и т.д.), но с withdraw()'нутым
окном (не мешает, не кликается по ошибке) и БЕЗ root.mainloop() — вызывает
_spreadsheet_worker() синхронно в основном потоке текущего скрипта.
`root.after(...)`-колбэки внутри воркера (обновление статус-бара,
пост-тестовый диалог) просто никогда не срабатывают без цикла событий —
безвредно для headless-прогона, сам воркер их не ждёт.

По умолчанию 1 прогон на тест (функциональная проверка «работает/не
работает», не статистика — для медианы/MAD см. обычный DEFAULT_TEST_RUNS=7
через саму вкладку «Производительность»). Число прогонов передаётся первым
аргументом.

ЗАПУСК (закройте Р7-Офис перед стартом, права администратора желательны,
но не проверяются жёстко — сам _spreadsheet_worker их не требует):
    .venv/Scripts/python.exe tests/manual_full_suite_real_fixture.py
    .venv/Scripts/python.exe tests/manual_full_suite_real_fixture.py 3
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

import ctypes         # noqa: E402
import tkinter as tk  # noqa: E402
import r7_Testovarka as r7mod  # noqa: E402


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main(argv):
    runs = int(argv[1]) if len(argv) > 1 else 1

    is_admin = False
    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        pass
    if not is_admin:
        log("⚠️ Скрипт запущен не от администратора — _spreadsheet_worker "
            "сам по себе прав не требует, но продолжаю с предупреждением")

    fixture_dir = BASE_DIR / "TestFiles"
    # "*50К*.xlsx", не полное имя с "файл-для-...": на диске "й" в реальном
    # имени хранится в NFD-разложении ("и" + U+0306 combining breve), а не
    # предкомпонованным U+0439 — точный литерал с precomposed "й" (как в
    # исходном r7_Testovarka.py, первый из трёх паттернов find_test_file())
    # НЕ матчит на этом диске; сама find_test_file() не ломается только
    # потому, что перебирает три паттерна по очереди и берёт этот же
    # третий, без "й", как рабочий fallback.
    candidates = sorted(fixture_dir.glob("*50К*.xlsx"))
    candidates = [c for c in candidates if not c.name.startswith("~$")]
    if not candidates:
        log(f"❌ Фикстура не найдена в {fixture_dir}")
        return 2
    fixture = candidates[0]
    log(f"Фикстура: {fixture} ({fixture.stat().st_size / 1024 / 1024:.1f} МБ)")

    root = tk.Tk()
    root.withdraw()
    app = r7mod.R7Testovarka(root)
    app.add_test_log = log  # печать в консоль вместо (не отрисовываемого) Text-виджета

    # ЭКСПЕРИМЕНТ (26.08.2026): отключаем фоновый поток-монитор диалога
    # обновления — единственное, что реально работает в фоне ПОСТОЯННО
    # (каждые 2 сек win32-сканирование EnumWindows/GetWindowThreadProcessId)
    # в настоящем _spreadsheet_worker, но чего НЕТ в изолированных пробниках
    # (manual_saveas_uia_save.py и т.п.), которые Ctrl+Shift+S открывали
    # без единого сбоя. Проверяем гипотезу гонки потоков за win32 UI state.
    app._monitor_update_dialog = lambda stop_event, log_cb=None, interval=2: None

    procs = app._get_r7_processes(log_cb=log)
    if procs:
        pids = ", ".join(str(p.pid) for p in procs)
        log(f"⚠️ Р7-Офис уже запущен (PID: {pids}). Закройте его и повторите.")
        root.destroy()
        return 2

    only_formats = len(argv) > 2 and argv[2] == "formats_only"
    if only_formats:
        enabled = {n for n in app.TEST_DEFINITIONS if n in app.EXTRA_FORMAT_TESTS
                   or "PDF" in n}
        log("🧪 ЭКСПЕРИМЕНТ: только тесты форматов, без 12 CDP-тестов перед ними")
    else:
        enabled = set(app.TEST_DEFINITIONS)
    test_runs = {name: runs for name in app.TEST_DEFINITIONS}
    log(f"Тестов включено: {len(enabled)}, прогонов на тест: {runs}")

    stop_event = threading.Event()
    t0 = time.time()

    # НАЙДЕНО ЖИВЫМ ПРОГОНОМ 26.08.2026: вызов _spreadsheet_worker
    # СИНХРОННО в главном потоке (без root.mainloop()) 100%-но ронял
    # Ctrl+Shift+S — даже на файле, который в изолированных пробниках
    # (без Tk-окна вообще) открывал диалог без единого сбоя. Настоящий
    # run_spreadsheet_test() ВСЕГДА гоняет воркер в фоновом потоке, пока
    # root.mainloop() крутится в главном — «зависшее» (не качающее
    # сообщения) Tk-окно нашего процесса, похоже, сбивает маршрутизацию
    # системного ввода к дочернему окну Р7 на уровне Windows. Повторяем
    # эту же модель здесь, а не синхронный вызов.
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

    threading.Thread(target=_worker, daemon=True).start()
    root.mainloop()

    if _exc_holder:
        log(f"❌ _spreadsheet_worker упал с исключением: "
            f"{type(_exc_holder[0]).__name__}: {_exc_holder[0]}")
    total_elapsed = time.time() - t0
    log(f"Полный прогон занял {total_elapsed / 60:.1f} мин")

    try:
        root.destroy()
    except Exception:
        pass

    files = sorted(app.reports_folder.glob("performance_full_*.json"),
                    key=lambda p: p.stat().st_mtime)
    if not files:
        log("❌ Отчёт performance_full_*.json не создан — прогон не дошёл до сохранения")
        return 1
    latest = files[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    results = data.get("results", [])
    log(f"Отчёт: {latest.name} (test_file={data.get('test_file')})")

    log("=" * 70)
    log(f"{'Тест':<45} {'Статус':<10} {'Время'}")
    log("-" * 70)
    ok_count = 0
    fail_count = 0
    skip_count = 0
    missing = list(enabled)
    for r in results:
        name = r["name"]
        if name in missing:
            missing.remove(name)
        err = r.get("error")
        if err and err.startswith("SKIP:"):
            # Известное ограничение окружения (например, диалог «Сохранить
            # как» недоступен для синтетического ввода), не баг в тесте —
            # см. save_as_format(). Отдельно от FAIL, чтобы не путать со
            # сломанной функциональностью при беглом просмотре сводки.
            skip_count += 1
            log(f"{name:<45} ⏭ SKIP    {err[5:].strip()[:60]}")
        elif err:
            fail_count += 1
            log(f"{name:<45} ❌ FAIL    {err[:60]}")
        else:
            ok_count += 1
            below = " (below_floor)" if r.get("below_floor") else ""
            log(f"{name:<45} ✅ OK      {r.get('time', 0):.3f} сек{below}")
    for name in missing:
        log(f"{name:<45} ⚠️ НЕ ЗАПУЩЕН (не попал в results)")

    log("=" * 70)
    log(f"ИТОГ: {ok_count} OK, {fail_count} FAIL, {skip_count} SKIP, "
        f"{len(missing)} не запущено из {len(enabled)} тестов")

    return 0 if (fail_count == 0 and not missing) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
