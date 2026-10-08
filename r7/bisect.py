"""Бисект по сборкам: двоичный поиск первой сборки с регрессией одной операции
(docs/plan-to-20.md, этап 3, п. 10; подробности — docs/cli.md).

Есть «хорошая» сборка (база) и «плохая» (операция на ней медленнее), между
ними по номеру версии лежат другие дистрибутивы. Бисект меряет крайние
сборки, проверяет, что они действительно различаются, и дальше делит
отрезок пополам, как `git bisect`: каждая проба — одна сборка, её повторы
сравниваются с повторами обеих крайних (r7.stats.compare_runs с порогом
теста). Итог — первая сборка, которая ведёт себя как плохая, или отрезок,
если решить мешают пропущенные сборки.

Модуль чистый: ни Tk, ни установки, ни Р7. Замер приходит снаружи функцией
`measure(сборка, повторов) -> длительности | None` (r7/bisect_runner.py
ставит сборку и прогоняет операцию; тесты подставляют синтетику), поэтому
алгоритм детерминирован и проверяется без стенда.

Классы пробы:
  * «как база» — проба эквивалентна базе или значимо быстрее плохой;
  * «как регрессия» — проба значимо медленнее базы или эквивалентна плохой.
    Если она медленнее базы и при этом быстрее плохой, сдвигов несколько:
    проба считается плохой (первый сдвиг за порог уже случился), в журнале
    пометка «промежуточная»;
  * «не определено» — ни то, ни другое (или признаки спорят). Тогда сборка
    получает ещё повторы, пока их не станет max_runs; не помогло — сборка
    пропускается, как `git bisect skip`, и бисект берёт соседнюю.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from r7.stats import (EQUIVALENT, MIN_RUNS_FOR_COMPARISON, REGRESSION, SPEEDUP, UNDETERMINED,
                      compare_runs)

LIKE_GOOD, LIKE_BAD, NOT_DETERMINED, SKIPPED = (
    "как база", "как регрессия", "не определено", "пропущена")
ROLE_GOOD, ROLE_BAD, ROLE_PROBE = "база", "плохая", "проба"

# Итог бисекта.
STATUS_FOUND = "found"            # первая плохая сборка найдена
STATUS_RANGE = "range"            # пропущенные сборки не дали сузить до одной
STATUS_NO_CHANGE = "no_change"    # крайние сборки не различаются (или не решить)
STATUS_SPEEDUP = "speedup"        # «плохая» на деле быстрее базы
STATUS_ERROR = "error"            # крайнюю сборку не удалось измерить
STATUS_STOPPED = "stopped"        # остановлено пользователем

DEFAULT_RUNS = 7                  # повторов на сборку за один заход (как у вкладки)
DEFAULT_MAX_RUNS = 21             # потолок повторов сборки, пока она «не определено»

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)*)")   # то же, что _extract_version

class BisectError(ValueError):
    """Бисект нельзя начать: сборка не найдена, неоднозначна, отрезок пуст."""


@dataclass(frozen=True)
class Build:
    """Сборка — один дистрибутив из папки Distributives."""
    name: str                                 # имя файла дистрибутива
    version: tuple[int, ...] | None           # номер версии числами (из имени файла)
    path: Path | None = None

    @property
    def label(self) -> str:
        """«2026.3.2.100» — номер версии; без номера — имя файла."""
        return ".".join(str(x) for x in self.version) if self.version else self.name


# Замер: (сборка, повторов) → длительности годных повторов или None (не удалось).
Measure = Callable[[Build, int], "Sequence[float] | None"]


@dataclass(frozen=True)
class Probe:
    """Замер одной сборки и её класс."""
    build: Build
    role: str                                 # ROLE_GOOD / ROLE_BAD / ROLE_PROBE
    runs: tuple[float, ...] = ()
    requested: int = 0                        # сколько повторов запрошено всего
    sessions: int = 0                         # сколько раз Р7 открывался на этой сборке
    verdict: str = NOT_DETERMINED
    vs_good: Mapping[str, Any] | None = None  # compare_runs(база, проба)
    vs_bad: Mapping[str, Any] | None = None   # compare_runs(плохая, проба)
    note: str | None = None


@dataclass(frozen=True)
class BisectResult:
    """Итог бисекта. probes — в порядке замеров (база, плохая, пробы)."""
    status: str
    message: str
    builds: tuple[Build, ...]                 # отрезок от базы до плохой, по порядку
    probes: tuple[Probe, ...] = ()
    first_bad: Build | None = None
    last_good: Build | None = None
    suspects: tuple[Build, ...] = ()          # отрезок, где первая плохая (STATUS_RANGE)
    endpoints: Mapping[str, Any] | None = None   # compare_runs(база, плохая)
    threshold_pct: float | None = None
    threshold_source: str | None = None
    op: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def total_runs(self) -> int:
        """Повторов операции всего (запрошено)."""
        return sum(p.requested for p in self.probes)

    @property
    def total_sessions(self) -> int:
        """Сколько раз Р7 открывался (каждый заход — установка, если сборка другая)."""
        return sum(p.sessions for p in self.probes)

    def to_dict(self) -> dict[str, Any]:
        """JSON-словарь для отчёта и страницы (r7_reports.bisect_model)."""
        return {
            "status": self.status, "message": self.message, "op": self.op,
            "threshold_pct": self.threshold_pct, "threshold_source": self.threshold_source,
            "builds": [build_dict(b) for b in self.builds],
            "probes": [probe_dict(p) for p in self.probes],
            "first_bad": build_dict(self.first_bad) if self.first_bad else None,
            "last_good": build_dict(self.last_good) if self.last_good else None,
            "suspects": [build_dict(b) for b in self.suspects],
            "endpoints": dict(self.endpoints) if self.endpoints else None,
            "total_runs": self.total_runs, "total_sessions": self.total_sessions,
            **dict(self.extra),
        }


def build_dict(b: Build) -> dict[str, Any]:
    return {"name": b.name, "label": b.label,
            "version": list(b.version) if b.version else None}


def probe_dict(p: Probe) -> dict[str, Any]:
    return {"build": build_dict(p.build), "role": p.role, "runs": list(p.runs),
            "requested": p.requested, "sessions": p.sessions, "verdict": p.verdict,
            "vs_good": dict(p.vs_good) if p.vs_good else None,
            "vs_bad": dict(p.vs_bad) if p.vs_bad else None, "note": p.note}


# ── сборки и их порядок ──────────────────────────────────────────────────

def parse_version(text: str) -> tuple[int, ...] | None:
    """«R7-Office-2026.3.2.100-x64» → (2026, 3, 2, 100); без номера — None.
    Регулярка та же, что у R7Testovarka._extract_version."""
    m = _VERSION_RE.search(text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def builds_from_files(files: Iterable[Path | str]) -> tuple[list[Build], list[str]]:
    """Сборки из файлов дистрибутивов, по номеру версии (числами, не строкой).

    Returns:
        (сборки по порядку, имена файлов без номера версии — бисект их не
        видит: без номера порядок сборок неизвестен).
    """
    builds, unversioned = [], []
    for f in files:
        p = Path(f)
        ver = parse_version(p.stem)
        if ver is None:
            unversioned.append(p.name)
        else:
            builds.append(Build(p.name, ver, p))
    return order_builds(builds), sorted(unversioned)


def order_builds(builds: Iterable[Build]) -> list[Build]:
    """По номеру версии, при равных — по имени файла (детерминированно)."""
    return sorted(builds, key=lambda b: (b.version is None, b.version or (), b.name))


def resolve_build(builds: Sequence[Build], spec: str) -> Build:
    """Сборка по тому, что ввёл пользователь: имя или путь дистрибутива,
    номер версии («2026.3.2», «v2026.3.2.100»). Номер может быть началом
    полного: «2026.3.2» найдёт «2026.3.2.100», если такая сборка одна.

    Raises:
        BisectError: не нашлась или подходит несколько.
    """
    spec = (spec or "").strip()
    if not spec:
        raise BisectError("сборка не указана")
    name = Path(spec).name
    by_name = [b for b in builds if name in (b.name, Path(b.name).stem)]
    if len(by_name) == 1:
        return by_name[0]
    ver = parse_version(spec)
    if ver is None:
        raise BisectError(f"сборка «{spec}» не найдена среди дистрибутивов")
    exact = [b for b in builds if b.version == ver]
    if len(exact) == 1:
        return exact[0]
    cands = exact or [b for b in builds if b.version and b.version[:len(ver)] == ver]
    if not cands:
        raise BisectError(f"сборки {'.'.join(map(str, ver))} нет среди дистрибутивов")
    if len(cands) > 1:
        raise BisectError(f"«{spec}» подходит к нескольким дистрибутивам: "
                          + ", ".join(b.name for b in cands) + " — укажите имя файла")
    return cands[0]


def find_build_for_installed(builds: Sequence[Build], installed: str | None) -> Build | None:
    """Дистрибутив установленной версии (номер из реестра, «2026.3.2.3229»).

    Точное совпадение номера, иначе единственная сборка, чей номер — начало
    установленного или наоборот (в имени файла бывает «2026.3.2» без
    номера сборки). Несколько таких или ни одной — None.
    """
    ver = parse_version(installed or "")
    if ver is None:
        return None
    exact = [b for b in builds if b.version == ver]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    near = [b for b in builds if b.version
            and (ver[:len(b.version)] == b.version or b.version[:len(ver)] == ver)]
    return near[0] if len(near) == 1 else None


def segment(builds: Sequence[Build], good: Build, bad: Build) -> list[Build]:
    """Отрезок от базы до плохой включительно, в сторону плохой.

    База обычно старше, но не обязательно: «плохая» старше базы — поиск
    идёт к старым сборкам (так ищут, с какой сборки пришло ускорение:
    база — новая быстрая, «плохая» — старая медленная).
    """
    ordered = order_builds(builds)
    try:
        i_good, i_bad = ordered.index(good), ordered.index(bad)
    except ValueError:
        raise BisectError("крайняя сборка не входит в список дистрибутивов") from None
    if i_good == i_bad:
        raise BisectError(f"база и плохая — одна и та же сборка ({good.label})")
    if i_good < i_bad:
        return ordered[i_good:i_bad + 1]
    return list(reversed(ordered[i_bad:i_good + 1]))


# ── класс пробы ──────────────────────────────────────────────────────────

def classify(good_runs: Sequence[float], bad_runs: Sequence[float],
             probe_runs: Sequence[float], threshold_pct: float
             ) -> tuple[str, dict[str, Any], dict[str, Any], str | None]:
    """Класс пробы по сравнению с обеими крайними сборками.

    Returns:
        (класс, compare_runs(база, проба), compare_runs(плохая, проба), пометка).
    """
    vs_good = compare_runs(list(good_runs), list(probe_runs), threshold_pct=threshold_pct)
    vs_bad = compare_runs(list(bad_runs), list(probe_runs), threshold_pct=threshold_pct)
    dg, db = vs_good.get("decision"), vs_bad.get("decision")
    good_ev = dg in (EQUIVALENT, SPEEDUP) or db == SPEEDUP
    bad_ev = dg == REGRESSION or db in (EQUIVALENT, REGRESSION)
    if bad_ev and not good_ev:
        return LIKE_BAD, vs_good, vs_bad, None
    if good_ev and not bad_ev:
        return LIKE_GOOD, vs_good, vs_bad, None
    if dg == REGRESSION and db == SPEEDUP:
        return (LIKE_BAD, vs_good, vs_bad,
                "промежуточная: медленнее базы, но быстрее плохой — сдвигов несколько")
    note = "эквивалентна обеим крайним — порог шире разницы между ними" if bad_ev else None
    return NOT_DETERMINED, vs_good, vs_bad, note


# ── поиск ────────────────────────────────────────────────────────────────

def _pick(lo: int, hi: int, skipped: set[int]) -> int | None:
    """Следующая проба: середина отрезка, а если она пропущена — ближайшая
    непропущенная (при равном расстоянии — ближе к базе). None — проверять
    нечего."""
    cands = [i for i in range(lo + 1, hi) if i not in skipped]
    if not cands:
        return None
    mid = (lo + hi) // 2
    return min(cands, key=lambda i: (abs(i - mid), i))


@dataclass
class _Search:
    """Изменяемое состояние одного бисекта (наружу не выходит)."""
    builds: list[Build]
    measure: Measure
    runs: int
    max_runs: int
    threshold_pct: float
    should_stop: Callable[[], bool]
    log: Callable[[str], None]
    probes: list[Probe] = field(default_factory=list)
    stopped: bool = False

    def take(self, build: Build, n: int) -> tuple[list[float] | None, str | None]:
        """Один заход замера. (повторы, None) или (None, причина)."""
        if self.should_stop():
            self.stopped = True
            return None, "остановлено"
        try:
            got = self.measure(build, n)
        except Exception as e:   # сбой одной сборки не роняет бисект — она пропускается
            return None, f"замер упал: {type(e).__name__}: {e}"
        if self.should_stop():
            self.stopped = True
        if got is None:
            return None, "замер не удался (установка, запуск Р7 или операция)"
        return [float(t) for t in got], None

    def endpoint(self, build: Build, role: str) -> Probe | None:
        runs, why = self.take(build, self.runs)
        if runs is None:
            self.probes.append(Probe(build, role, requested=self.runs, sessions=1,
                                     verdict=SKIPPED, note=why))
            return None
        probe = Probe(build, role, tuple(runs), self.runs, 1,
                      LIKE_GOOD if role == ROLE_GOOD else LIKE_BAD)
        self.probes.append(probe)
        return probe

    def top_up(self, good: Probe, bad: Probe) -> tuple[Probe, Probe, dict[str, Any]]:
        """Сравнение крайних; «не определено» — ещё заход обеим, пока не
        кончится max_runs."""
        cmp = compare_runs(list(good.runs), list(bad.runs), threshold_pct=self.threshold_pct)
        while cmp.get("decision") == UNDETERMINED and good.requested < self.max_runs:
            n = min(self.runs, self.max_runs - good.requested)
            self.log(f"ℹ️ Крайние сборки не различить на {len(good.runs)}/{len(bad.runs)} "
                     f"повторах — ещё {n} каждой")
            more_g, _ = self.take(good.build, n)
            more_b, _ = self.take(bad.build, n) if more_g is not None else (None, None)
            if more_g is None or more_b is None:
                break
            good = replace(good, runs=good.runs + tuple(more_g), requested=good.requested + n,
                           sessions=good.sessions + 1)
            bad = replace(bad, runs=bad.runs + tuple(more_b), requested=bad.requested + n,
                          sessions=bad.sessions + 1)
            self._swap(good)
            self._swap(bad)
            cmp = compare_runs(list(good.runs), list(bad.runs), threshold_pct=self.threshold_pct)
        # Сдвиг плохой к базе — в её строку таблицы. Класс «как регрессия» она
        # получает, только если и правда медленнее: живой бисект 08.10.2026
        # (на «плохой» ВПР быстрее на 12,8 %) показывал «как регрессия» и пустые Δ.
        decision = cmp.get("decision")
        if decision == REGRESSION:
            verdict = bad.verdict
        elif decision == UNDETERMINED:
            verdict = NOT_DETERMINED
        else:
            verdict = LIKE_GOOD
        bad = replace(bad, vs_good=cmp, verdict=verdict)
        self._swap(bad)
        return good, bad, cmp

    def _swap(self, probe: Probe) -> None:
        """Заменяет запись пробы той же сборки и роли на новую."""
        self.probes = [probe if (p.build, p.role) == (probe.build, probe.role) else p
                       for p in self.probes]

    def probe(self, build: Build, good: Probe, bad: Probe) -> Probe:
        """Проба сборки: заход, класс; «не определено» — добор до max_runs."""
        runs, why = self.take(build, self.runs)
        if runs is None:
            return Probe(build, ROLE_PROBE, requested=self.runs, sessions=1,
                         verdict=SKIPPED, note=why)
        requested, sessions = self.runs, 1
        verdict, vg, vb, note = classify(good.runs, bad.runs, runs, self.threshold_pct)
        while verdict == NOT_DETERMINED and requested < self.max_runs and not self.stopped:
            n = min(self.runs, self.max_runs - requested)
            self.log(f"ℹ️ {build.label}: не определено на {len(runs)} повторах — ещё {n}")
            more, why = self.take(build, n)
            if more is None:
                note = why
                break
            runs, requested, sessions = runs + more, requested + n, sessions + 1
            verdict, vg, vb, note = classify(good.runs, bad.runs, runs, self.threshold_pct)
        if verdict == NOT_DETERMINED:
            verdict = SKIPPED
            reason = f"не определено и после {requested} повторов"
            note = f"{reason}: {note}" if note else reason
        return Probe(build, ROLE_PROBE, tuple(runs), requested, sessions, verdict, vg, vb, note)


def run_bisect(builds: Sequence[Build], good: Build, bad: Build, measure: Measure,
               threshold_pct: float, runs: int = DEFAULT_RUNS,
               max_runs: int = DEFAULT_MAX_RUNS,
               should_stop: Callable[[], bool] | None = None,
               log: Callable[[str], None] | None = None,
               op: str | None = None, threshold_source: str | None = None) -> BisectResult:
    """Двоичный поиск первой сборки, на которой операция ведёт себя как на плохой.

    Args:
        builds: все сборки (порядок не важен — сортируются по номеру).
        good, bad: база и плохая (из builds).
        measure: (сборка, повторов) → длительности годных повторов или None.
        threshold_pct: порог теста, % (профиль шума или 10 %).
        runs: повторов за заход; max_runs — потолок повторов сборки.
        should_stop: True — остановить перед следующим заходом.

    Raises:
        BisectError: крайние сборки не годятся (одна и та же, не в списке).
    """
    if runs < MIN_RUNS_FOR_COMPARISON:
        raise BisectError(f"повторов на сборку нужно не меньше {MIN_RUNS_FOR_COMPARISON}")
    seq = segment(builds, good, bad)
    st = _Search(seq, measure, runs, max(runs, max_runs), threshold_pct,
                 should_stop or (lambda: False), log or (lambda _m: None))
    base: dict[str, Any] = {"builds": tuple(seq), "threshold_pct": threshold_pct,
            "threshold_source": threshold_source, "op": op}

    def done(status: str, message: str, **kw: Any) -> BisectResult:
        st.log(("✅ " if status == STATUS_FOUND else "ℹ️ ") + message)
        return BisectResult(status, message, probes=tuple(st.probes), **base, **kw)

    st.log(f"🔎 Бисект: {len(seq)} сборок от {good.label} до {bad.label}, "
           f"порог {threshold_pct:g} %, до {_max_probes(len(seq))} проб")
    g = st.endpoint(seq[0], ROLE_GOOD)
    b = st.endpoint(seq[-1], ROLE_BAD) if g is not None else None
    if st.stopped:
        return done(STATUS_STOPPED, "остановлено до сравнения крайних сборок")
    if g is None or b is None:
        failed = seq[0] if g is None else seq[-1]
        return done(STATUS_ERROR, f"крайнюю сборку {failed.label} измерить не удалось: "
                    f"{st.probes[-1].note}")
    g, b, cmp = st.top_up(g, b)
    if st.stopped:
        return done(STATUS_STOPPED, "остановлено до сравнения крайних сборок", endpoints=cmp)
    decision = cmp.get("decision")
    if decision == SPEEDUP:
        return done(STATUS_SPEEDUP,
                    f"на {bad.label} операция быстрее, чем на {good.label} "
                    f"({cmp.get('effect_pct'):+.1f} %) — искать нечего. Чтобы найти сборку "
                    f"с ускорением, поменяйте местами --good и --bad", endpoints=cmp)
    if decision != REGRESSION:
        why = ("разница внутри порога" if decision == EQUIVALENT
               else f"не различить и на {g.requested} повторах")
        return done(STATUS_NO_CHANGE,
                    f"крайние сборки не различаются ({why}, порог {threshold_pct:g} %) — "
                    f"регрессии между ними для этой операции нет", endpoints=cmp)
    st.log(f"📉 Крайние сборки различаются: {cmp.get('effect_pct'):+.1f} % "
           f"[{cmp.get('ci_low_pct') or 0:+.1f}; {cmp.get('ci_high_pct') or 0:+.1f}]")

    lo, hi = 0, len(seq) - 1
    skipped: set[int] = set()
    while (i := _pick(lo, hi, skipped)) is not None:
        if st.should_stop():
            st.stopped = True
            break
        st.log(f"🔎 Проба {seq[i].label} (между {seq[lo].label} и {seq[hi].label})")
        p = st.probe(seq[i], g, b)
        st.probes.append(p)
        if st.stopped and p.verdict == SKIPPED:
            break
        st.log(f"   {seq[i].label}: {p.verdict}" + (f" — {p.note}" if p.note else ""))
        if p.verdict == LIKE_GOOD:
            lo = i
        elif p.verdict == LIKE_BAD:
            hi = i
        else:
            skipped.add(i)
    if st.stopped:
        return done(STATUS_STOPPED, f"остановлено: первая плохая — между {seq[lo].label} и "
                    f"{seq[hi].label}", endpoints=cmp, last_good=seq[lo],
                    suspects=tuple(seq[lo + 1:hi + 1]))
    if hi == lo + 1:
        return done(STATUS_FOUND, f"регрессия появилась в сборке {seq[hi].label} "
                    f"(последняя как база — {seq[lo].label})", endpoints=cmp,
                    first_bad=seq[hi], last_good=seq[lo])
    suspects = tuple(seq[lo + 1:hi + 1])
    return done(STATUS_RANGE, f"первая плохая — одна из {len(suspects)} сборок: "
                + ", ".join(s.label for s in suspects)
                + " (промежуточные пропущены: замер не удался или не определено)",
                endpoints=cmp, last_good=seq[lo], suspects=suspects)


def _max_probes(n_builds: int) -> int:
    """Проб между крайними без пропусков: ⌈log2(n − 1)⌉."""
    inner, k = max(0, n_builds - 2), 0
    while (1 << k) < inner + 1:
        k += 1
    return k


def format_result(res: BisectResult) -> str:
    """Итог для консоли: вердикт и таблица проб."""
    lines = [f"Бисект{f' «{res.op}»' if res.op else ''}: {res.message}",
             f"{'сборка':24} {'роль':7} {'n':>3} {'медиана':>8} {'Δ к базе':>12} "
             f"{'Δ к плохой':>12}  класс"]
    for p in res.probes:
        med = _median(p.runs)
        lines.append(f"{p.build.label[:24]:24} {p.role:7} {len(p.runs):>3} "
                     f"{'—' if med is None else f'{med:.3f}':>8} {_delta(p.vs_good):>12} "
                     f"{_delta(p.vs_bad):>12}  {p.verdict}"
                     + (f" ({p.note})" if p.note else ""))
    lines.append(f"Повторов всего: {res.total_runs}, заходов Р7: {res.total_sessions}, "
                 f"порог {_thr(res.threshold_pct)} %"
                 + (f" ({res.threshold_source})" if res.threshold_source else ""))
    return "\n".join(lines)


def _median(runs: Sequence[float]) -> float | None:
    if not runs:
        return None
    s = sorted(runs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _delta(cmp: Mapping[str, Any] | None) -> str:
    eff = (cmp or {}).get("effect_pct")
    return "—" if eff is None else f"{eff:+.1f} %"


def _thr(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"
