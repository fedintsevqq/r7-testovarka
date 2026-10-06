"""Эталонный прогон для рефакторинга (docs/plan-to-8.md, этап 0).

Гоняет настоящие воркеры на рабочей фикстуре и складывает результаты в
Reports/baseline/<метка>/: вкладку «Производительность» (все тесты, кроме
ODS — x2t падает, DE-8304, — повторы по умолчанию) и Batch на текущей
установленной версии (без установки и удаления). После рефакторинга тот же
скрипт с другой меткой, затем сравнение через «Сравнить версии» или
tests/manual_baseline_compare.py: вердикт «без изменений» по всем
операциям.

    .venv/Scripts/python.exe tests/manual_baseline_run.py before
    .venv/Scripts/python.exe tests/manual_baseline_run.py after [perf|batch]

Р7-Офис должен быть закрыт; не трогать клавиатуру и мышь во время прогона.
"""
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import tkinter as tk  # noqa: E402
import r7_Testovarka as r7mod  # noqa: E402


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _new_jsons(app, before):
    return sorted(set(app.reports_folder.glob("performance_full_*.json")) - before,
                  key=lambda p: p.stat().st_mtime)


def run_perf(app, root, out_dir):
    enabled = {n for n in app.TEST_DEFINITIONS if "ODS" not in n}
    runs = {n: app._default_test_entry(n)["runs"] for n in app.TEST_DEFINITIONS}
    log(f"Вкладка: тестов {len(enabled)}, повторы {sorted(set(runs.values()))}")
    before = set(app.reports_folder.glob("performance_full_*.json"))
    stop = threading.Event()
    err = []

    def worker():
        try:
            app._spreadsheet_worker(enabled, runs, stop)
        except Exception as e:
            import traceback
            traceback.print_exc()
            err.append(e)
        finally:
            root.after(0, root.quit)

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()
    for p in _new_jsons(app, before):
        shutil.copy2(p, out_dir / "perf.json")
        log(f"Вкладка: {p.name} → {out_dir / 'perf.json'}")
    return not err


def run_batch(app, root, out_dir):
    fixture = app.find_test_file() if hasattr(app, "find_test_file") else None
    if fixture is None:
        fixture = next((ROOT / "TestFiles").glob("*50К*.xlsx"))
    before = set(app.reports_folder.glob("performance_full_*.json"))
    stop, pause = threading.Event(), threading.Event()
    box = {}

    def worker():
        try:
            box["res"] = app._batch_run_single_version(Path(fixture), "baseline", log, stop, pause)
        except Exception as e:
            import traceback
            traceback.print_exc()
            box["err"] = e
        finally:
            root.after(0, root.quit)

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()
    for p in _new_jsons(app, before):
        shutil.copy2(p, out_dir / "batch.json")
        log(f"Batch: {p.name} → {out_dir / 'batch.json'}")
    return "err" not in box


def main(argv):
    label = argv[1] if len(argv) > 1 else "before"
    which = argv[2] if len(argv) > 2 else "both"
    out_dir = ROOT / "Reports" / "baseline" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    root = tk.Tk()
    root.withdraw()
    app = r7mod.R7Testovarka(root)
    app.add_test_log = log
    app._show_post_test_dialog = lambda *a, **k: None
    if app._get_r7_processes(log_cb=log):
        log("Р7 уже запущен — закройте его")
        return 2
    log(f"Эталон «{label}» на {sha}, Р7 {app.current_version_info.get('version')}, "
        f"exe {app._find_r7_path()}")
    ok = True
    t0 = time.time()
    if which in ("both", "perf"):
        ok = run_perf(app, root, out_dir) and ok
    if which in ("both", "batch"):
        ok = run_batch(app, root, out_dir) and ok
    (out_dir / "meta.json").write_text(json.dumps(
        {"label": label, "git": sha, "r7": app.current_version_info.get("version"),
         "minutes": round((time.time() - t0) / 60, 1)}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    log(f"Готово за {(time.time() - t0) / 60:.1f} мин, Р7 осталось: "
        f"{[p.pid for p in app._get_r7_processes(log_cb=lambda m: None, fresh=True)]}")
    try:
        root.destroy()
    except Exception:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
