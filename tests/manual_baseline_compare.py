"""Сверка двух эталонных прогонов (docs/plan-to-8.md, этап 0).

Тот же вердикт, что на странице «Сравнить версии» (compare_runs по
действительным повторам), но в консоль и без браузера. Рефакторинг
прошёл, если ни одна операция не дала «РЕГРЕССИЯ»/«УСКОРЕНИЕ», а
медианы разошлись не больше порога практической значимости
COMPARISON_MIN_EFFECT_PCT (10 %) — того же, что у вердикта.

MAD для этого не годится: он мерит разброс повторов внутри одного
прогона, а медиана между двумя прогонами на стенде гуляет на 1–3 %
(эталоны 06.10.2026) — «вне MAD» выходила половина операций без всякой
правки кода.

    .venv/Scripts/python.exe tests/manual_baseline_compare.py before after [perf|batch]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import r7_reports  # noqa: E402
import r7_Testovarka as r7mod  # noqa: E402


def _load(label, kind):
    path = ROOT / "Reports" / "baseline" / label / f"{kind}.json"
    return {r["name"]: r for r in json.loads(path.read_text(encoding="utf-8"))["results"]}


def compare(base_label, new_label, kind):
    base, new = _load(base_label, kind), _load(new_label, kind)
    bad = 0
    print(f"\n{kind}: {base_label} → {new_label}")
    print(f"{'операция':44} {'до':>8} {'после':>8} {'Δ%':>7} {'MAD':>6}  вердикт")
    for name, b in base.items():
        n = new.get(name)
        bt, nt = r7_reports.comparable_time(b), r7_reports.comparable_time(n)
        if n is None or bt is None or nt is None:
            print(f"{name[:44]:44} {'—':>8} {'—':>8}  нет данных ({(n or b).get('error')})")
            continue
        b_runs, n_runs = r7_reports.valid_runs(b), r7_reports.valid_runs(n)
        verdict = r7mod.compare_runs(b_runs, n_runs)["verdict"]
        mad = max(b.get("mad") or 0, n.get("mad") or 0)
        pct = (nt - bt) / bt * 100
        shifted = (verdict in ("РЕГРЕССИЯ", "УСКОРЕНИЕ")
                   or abs(pct) > r7mod.COMPARISON_MIN_EFFECT_PCT)
        flag = "  <<" if shifted else ""
        bad += bool(flag)
        print(f"{name[:44]:44} {bt:8.3f} {nt:8.3f} {pct:+7.1f} {mad:6.3f}  {verdict}{flag}")
    missing = set(base) ^ set(new)
    if missing:
        print(f"операции только в одном прогоне: {sorted(missing)}")
    return bad


def main(argv):
    base_label, new_label = argv[1], argv[2]
    kinds = [argv[3]] if len(argv) > 3 else ["perf", "batch"]
    bad = sum(compare(base_label, new_label, k) for k in kinds
              if (ROOT / "Reports" / "baseline" / new_label / f"{k}.json").exists())
    print(f"\nзначимых сдвигов: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
