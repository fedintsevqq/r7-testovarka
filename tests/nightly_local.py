"""Ночной прогон без раннера (docs/plan-to-8.md, этап 5): прогон вкладки на
рабочей фикстуре, отчёт в Reports/nightly/, сравнение с медианой последних
пяти сравнимых ночей (r7.nightly.baseline_reports).

    .venv/Scripts/python.exe tests/nightly_local.py            # все тесты, кроме ODS
    .venv/Scripts/python.exe tests/nightly_local.py --quick    # 5 тестов, ~4 мин
    .venv/Scripts/python.exe tests/nightly_local.py --compare-only
    .venv/Scripts/python.exe tests/nightly_local.py --aa --quick   # A/A: профиль шума
    .venv/Scripts/python.exe tests/nightly_local.py --aa-reports A.json B.json

Код выхода: 0 — регрессий нет, 2 — есть регрессия (при той же схеме
замера), 1 — прогон не удался. Сводка — Reports/nightly/nightly_last.txt.

A/A (--aa): та же версия меряется дважды подряд, из повторов обоих прогонов
пишется профиль шума стенда Reports/noise_profile.json (r7/noise.py,
docs/statistics.md); пороги сравнения берутся из него. Код выхода 2 —
сравнение A с A нашло изменение, стенд шумит. Сводка — aa_last.txt.
Р7-Офис должен быть закрыт; клавиатуру и мышь во время прогона не трогать.
Запускать из Планировщика заданий в сессии пользователя (не службой):
инструмент жмёт клавиши и читает окна.
"""
import argparse
import shutil
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

NIGHTLY_DIR = ROOT / "Reports" / "nightly"
NOISE_DIR = ROOT / "Reports"            # там же, где отчёты приложения
QUICK_TESTS = ("Повторное открытие файла", "Выделение всех ячеек (Ctrl+A)",
               "Вставка большого массива (Ctrl+V)", "Функция ВПР (50K строк)",
               "Сохранение в XLTX (конвертация x2t)")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_tab(quick):
    """Прогон вкладки «Производительность»; путь к новому отчёту или None."""
    import tkinter as tk
    import r7_Testovarka as r7mod
    root = tk.Tk()
    root.withdraw()
    app = r7mod.R7Testovarka(root)
    app.add_test_log = log
    app._show_post_test_dialog = lambda *a, **k: None
    if app._get_r7_processes(log_cb=log):
        log("Р7 уже запущен — закройте его")
        root.destroy()
        return None
    names = [n for n in app.TEST_DEFINITIONS if "ODS" not in n]   # ODS: x2t падает (DE-8304)
    enabled = {n for n in names if not quick or n in QUICK_TESTS}
    runs = {n: app._default_test_entry(n)["runs"] for n in app.TEST_DEFINITIONS}
    before = set(app.reports_folder.glob("performance_full_*.json"))
    err = []

    def worker():
        try:
            app._spreadsheet_worker(enabled, runs, threading.Event())
        except Exception as e:
            import traceback
            traceback.print_exc()
            err.append(e)
        finally:
            root.after(0, root.quit)

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()
    root.destroy()
    new = sorted(set(app.reports_folder.glob("performance_full_*.json")) - before,
                 key=lambda p: p.stat().st_mtime)
    return None if err or not new else new[-1]


def run_aa(args, nightly_dir):
    """A/A: два прогона одной версии (или два готовых отчёта) → профиль шума.

    Отчёты прогонов кладутся в <dir>/aa/, а не рядом с ночными: их цепочку
    сравнения «с прошлой ночью» A/A-пара не должна сбивать.
    """
    from r7 import noise
    from r7.nightly import aa_check, format_comparison, load_report
    if args.aa_reports:
        path_a, path_b = (Path(x) for x in args.aa_reports)
    else:
        aa_dir = nightly_dir / "aa"
        aa_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d_%H%M')
        paths = []
        for i in (1, 2):
            log(f"A/A: прогон {i} из 2")
            src = run_tab(args.quick)
            if src is None:
                log("прогон не удался — отчёта нет")
                return 1
            dst = aa_dir / f"aa_{stamp}_{i}.json"
            shutil.copy2(src, dst)
            paths.append(dst)
        path_a, path_b = paths
    try:
        entry, cmp = aa_check(load_report(path_a), load_report(path_b))
    except (OSError, ValueError) as e:   # NoiseProfileError — тоже ValueError
        log(f"профиль шума не собран: {e}")
        return 1
    doc = noise.merge_profile(noise.read_profile_doc(args.noise_dir), entry)
    saved = noise.save_profile_doc(args.noise_dir, doc)
    text = "\n\n".join([noise.format_profile(entry),
                         format_comparison(cmp, path_a.name, path_b.name)])
    (nightly_dir / "aa_last.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    log(f"профиль шума: {saved}")
    if cmp["regressions"] or cmp["speedups"]:
        log("⚠️ A/A нашёл изменение между прогонами одной версии — стенд шумит, "
            "пороги ненадёжны")
        return 2
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="5 тестов вместо всех")
    ap.add_argument("--dir", type=Path, default=NIGHTLY_DIR,
                    help="папка ночных отчётов (на раннере — вне рабочей копии: "
                         "checkout чистит неотслеживаемые файлы)")
    ap.add_argument("--compare-only", action="store_true",
                    help="не запускать Р7, сравнить последний ночной отчёт с базой")
    ap.add_argument("--baseline-k", type=int, default=5,
                    help="сколько прошлых сравнимых ночей брать в базу (медиана), по умолчанию 5")
    ap.add_argument("--aa", action="store_true",
                    help="A/A: дважды прогнать одну версию и записать профиль шума стенда")
    ap.add_argument("--aa-reports", nargs=2, metavar=("A", "B"),
                    help="профиль шума из двух готовых отчётов одной версии, без Р7")
    ap.add_argument("--noise-dir", type=Path, default=NOISE_DIR,
                    help="папка с noise_profile.json (по умолчанию Reports/)")
    args = ap.parse_args(argv)
    nightly_dir = args.dir

    from r7 import noise
    from r7.nightly import (baseline_label, baseline_reports, compare_reports,
                            compare_with_baseline, format_comparison, is_alarm,
                            load_report, previous_report)
    nightly_dir.mkdir(parents=True, exist_ok=True)
    if args.aa or args.aa_reports:
        return run_aa(args, nightly_dir)
    mode = "quick" if args.quick else "full"
    if args.compare_only:
        reports = sorted(nightly_dir.glob("nightly_*.json"))
        if len(reports) < 2:
            log("для сравнения нужно хотя бы два ночных отчёта")
            return 0
        cur = reports[-1]
    else:
        t0 = time.time()
        src = run_tab(args.quick)
        if src is None:
            log("прогон не удался — отчёта нет")
            return 1
        cur = nightly_dir / f"nightly_{time.strftime('%Y%m%d_%H%M')}_{mode}.json"
        shutil.copy2(src, cur)
        log(f"отчёт: {cur.name} ({(time.time() - t0) / 60:.1f} мин)")

    # База — медиана последних K сравнимых ночей того же режима (быстрый и
    # полный между собой не сравниваются: разный набор тестов).
    base = baseline_reports(nightly_dir, cur, k=args.baseline_k)
    prev = None
    if not base:
        # Сравнимых нет (другой стенд или схема) — сравниваем с прошлой ночью
        # того же режима, чтобы сводка назвала причину; флаг не ставится.
        prev = previous_report(nightly_dir, cur)
        while prev is not None and prev.stem.rsplit("_", 1)[-1] != cur.stem.rsplit("_", 1)[-1]:
            prev = previous_report(nightly_dir, prev)
    if base:
        cur_data = load_report(cur)
        cmp = compare_with_baseline([load_report(p) for p in base], cur_data,
                                    noise_profile=noise.noise_for_report(args.noise_dir, cur_data))
        text = format_comparison(cmp, baseline_label(base), cur.name)
        alarm = is_alarm(cmp)
    elif prev is None:
        text = f"{cur.name}: первый ночной отчёт этого режима — сравнивать не с чем"
        alarm = False
    else:
        cur_data = load_report(cur)
        cmp = compare_reports(load_report(prev), cur_data,
                              noise_profile=noise.noise_for_report(args.noise_dir, cur_data))
        text = format_comparison(cmp, prev.name, cur.name)
        alarm = is_alarm(cmp)
    (nightly_dir / "nightly_last.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    if alarm:
        log("⚠️ РЕГРЕССИЯ относительно прошлой ночи")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
