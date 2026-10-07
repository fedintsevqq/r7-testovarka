"""Бисект по сборкам (r7/bisect.py): алгоритм на синтетических замерах —
без установок, без Р7. Замер — функция (сборка, повторов) → длительности."""
import json
import math
import random
from pathlib import Path

import pytest

from r7 import bisect as bs
from r7.bisect import (LIKE_BAD, LIKE_GOOD, NOT_DETERMINED, ROLE_BAD, ROLE_GOOD, ROLE_PROBE,
                       SKIPPED, STATUS_ERROR, STATUS_FOUND, STATUS_NO_CHANGE, STATUS_RANGE,
                       STATUS_SPEEDUP, STATUS_STOPPED, BisectError, Build)

THR = 5.0


def _builds(n, start=100):
    return [Build(f"R7-Office-2026.3.2.{start + i}.exe", (2026, 3, 2, start + i)) for i in range(n)]


class Synthetic:
    """Операция 1 с на сборках до step и 1 × (1 + effect) с начиная с step.
    noise — относительный разброс повтора (равномерный ±noise). special —
    {индекс сборки: функция (повтор) → время} для «странных» сборок.
    fail — индексы, где замер не удался (None), boom — где он бросает."""

    def __init__(self, builds, step, effect=0.20, noise=0.005, special=None, fail=(),
                 boom=(), seed="s"):
        self.builds, self.step, self.effect, self.noise = builds, step, effect, noise
        self.special, self.fail, self.boom, self.seed = special or {}, set(fail), set(boom), seed
        self.calls = []

    def __call__(self, build, n):
        idx = self.builds.index(build)
        self.calls.append((build.label, n))
        if idx in self.fail:
            return None
        if idx in self.boom:
            raise RuntimeError("установщик вернул 1603")
        rnd = random.Random(f"{self.seed}-{build.name}-{len(self.calls)}")
        if idx in self.special:
            return [self.special[idx](rnd) for _ in range(n)]
        level = 1.0 + (self.effect if idx >= self.step else 0.0)
        return [level * (1 + rnd.uniform(-self.noise, self.noise)) for _ in range(n)]


def _run(builds, measure, good=0, bad=-1, **kw):
    return bs.run_bisect(builds, builds[good], builds[bad], measure, kw.pop("thr", THR), **kw)


# ── поиск ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [2, 3, 4, 5, 8, 9, 16, 17])
def test_finds_step_at_every_position(n):
    """Каждая позиция ступеньки в отрезке любой длины находится точно."""
    builds = _builds(n)
    for step in range(1, n):
        res = _run(builds, Synthetic(builds, step))
        assert res.status == STATUS_FOUND, (n, step, res.message)
        assert res.first_bad == builds[step] and res.last_good == builds[step - 1]


@pytest.mark.parametrize("n", [3, 9, 17, 33])
def test_probe_count_is_logarithmic(n):
    builds = _builds(n)
    for step in range(1, n):
        res = _run(builds, Synthetic(builds, step))
        probes = [p for p in res.probes if p.role == ROLE_PROBE]
        assert len(probes) <= math.ceil(math.log2(n - 1)), (n, step)
        assert res.total_sessions == len(res.probes)          # без доборов — по заходу


def test_endpoints_are_measured_first_good_then_bad():
    builds = _builds(9)
    m = Synthetic(builds, 4)
    res = _run(builds, m)
    assert m.calls[0] == (builds[0].label, 7) and m.calls[1] == (builds[-1].label, 7)
    assert [p.role for p in res.probes[:2]] == [ROLE_GOOD, ROLE_BAD]
    assert res.probes[0].verdict == LIKE_GOOD and res.probes[1].verdict == LIKE_BAD
    # первая проба — середина отрезка
    assert m.calls[2][0] == builds[4].label


def test_noisy_stand_still_finds_step():
    """Разброс 3 % при сдвиге 25 % и пороге 5 %: шум не путает классы."""
    builds = _builds(12)
    for seed in ("a", "b", "c"):
        res = _run(builds, Synthetic(builds, 7, effect=0.25, noise=0.03, seed=seed))
        assert res.status == STATUS_FOUND and res.first_bad == builds[7], seed


def test_reverse_direction_when_bad_is_older():
    """База новее «плохой»: поиск идёт к старым сборкам (так ищут ускорение)."""
    builds = _builds(9)
    # старые медленные: ступенька «быстро» начинается со сборки 5
    special = {i: (lambda r: 1.2 * (1 + r.uniform(-0.005, 0.005))) for i in range(5)}
    m = Synthetic(builds, step=99, special=special)
    res = bs.run_bisect(builds, builds[-1], builds[0], m, THR)
    assert res.status == STATUS_FOUND
    assert res.first_bad == builds[4] and res.last_good == builds[5]
    assert res.builds[0] == builds[-1] and res.builds[-1] == builds[0]


def test_builds_are_sorted_by_version_numbers_not_input_order():
    builds = _builds(9)
    shuffled = builds[::-1]
    res = bs.run_bisect(shuffled, builds[0], builds[-1], Synthetic(builds, 3), THR)
    assert res.first_bad == builds[3]


def test_deterministic():
    builds = _builds(10)
    a = _run(builds, Synthetic(builds, 6, noise=0.02))
    b = _run(builds, Synthetic(builds, 6, noise=0.02))
    assert a.to_dict() == b.to_dict()


# ── не определено, пропуски ──────────────────────────────────────────────

def _midway(r):
    """Ровно посередине между базой и плохой при пороге больше половины
    сдвига: ни туда, ни сюда."""
    return 1.10 * (1 + r.uniform(-0.002, 0.002))


def test_undetermined_build_gets_more_runs_then_is_skipped():
    builds = _builds(9)
    m = Synthetic(builds, step=6, special={4: _midway})
    res = _run(builds, m, thr=12.0, max_runs=21)
    p4 = next(p for p in res.probes if p.build == builds[4])
    assert p4.verdict == SKIPPED and p4.requested == 21 and p4.sessions == 3
    assert [n for lbl, n in m.calls if lbl == builds[4].label] == [7, 7, 7]
    assert "не определено" in p4.note
    # бисект не встал: соседняя сборка проверена, ступенька найдена
    assert res.status == STATUS_FOUND and res.first_bad == builds[6]


def test_max_runs_caps_the_last_top_up():
    builds = _builds(5)
    m = Synthetic(builds, step=3, special={2: _midway})
    res = _run(builds, m, thr=12.0, max_runs=10)
    p2 = next(p for p in res.probes if p.build == builds[2])
    assert p2.requested == 10 and [n for lbl, n in m.calls if lbl == builds[2].label] == [7, 3]


def test_skipped_builds_blocking_answer_give_a_range():
    """Пропущенная сборка стоит прямо перед плохой: ответ — отрезок."""
    builds = _builds(6)
    m = Synthetic(builds, step=4, fail=(3,))
    res = _run(builds, m)
    assert res.status == STATUS_RANGE
    assert res.suspects == (builds[3], builds[4]) and res.last_good == builds[2]
    assert res.first_bad is None
    assert "одна из 2" in res.message


def test_failed_measurement_marks_skip_and_bisect_continues():
    builds = _builds(9)
    m = Synthetic(builds, step=2, fail=(4,), boom=(3,))
    res = _run(builds, m)
    by = {p.build: p for p in res.probes}
    assert by[builds[4]].verdict == SKIPPED and "не удался" in by[builds[4]].note
    assert by[builds[3]].verdict == SKIPPED and "1603" in by[builds[3]].note
    assert res.status == STATUS_FOUND and res.first_bad == builds[2]


def test_skip_picks_nearest_to_middle_then_closer_to_base():
    assert bs._pick(0, 8, set()) == 4
    assert bs._pick(0, 8, {4}) == 3
    assert bs._pick(0, 8, {3, 4}) == 5
    assert bs._pick(0, 1, set()) is None
    assert bs._pick(0, 3, {1, 2}) is None


def test_all_inner_builds_skipped():
    builds = _builds(5)
    res = _run(builds, Synthetic(builds, step=2, fail=(1, 2, 3)))
    assert res.status == STATUS_RANGE and res.suspects == tuple(builds[1:])


# ── крайние сборки ───────────────────────────────────────────────────────

def test_endpoints_that_do_not_differ_stop_with_message():
    builds = _builds(9)
    m = Synthetic(builds, step=99)
    res = _run(builds, m)
    assert res.status == STATUS_NO_CHANGE and "не различаются" in res.message
    assert len(m.calls) == 2 and len(res.probes) == 2       # пробы не тратились
    assert res.endpoints["decision"] == "эквивалентно"


def test_endpoints_undetermined_get_more_runs_then_stop():
    builds = _builds(5)
    """Сдвиг ровно на пороге: интервал его пересекает, добор не помогает."""
    m = Synthetic(builds, step=4, effect=0.05, noise=0.002)
    res = _run(builds, m, max_runs=14)
    assert res.status == STATUS_NO_CHANGE and "не различить и на 14" in res.message
    assert res.probes[0].requested == 14 and res.probes[1].requested == 14
    assert res.probes[0].sessions == 2 and len(res.probes) == 2
    assert [n for _lbl, n in m.calls] == [7, 7, 7, 7]


def test_speedup_between_endpoints_suggests_swapping():
    builds = _builds(5)
    res = _run(builds, Synthetic(builds, step=2, effect=-0.3))
    assert res.status == STATUS_SPEEDUP and "--good" in res.message


def test_endpoint_failure_is_an_error():
    builds = _builds(5)
    res = _run(builds, Synthetic(builds, step=2, fail=(4,)))
    assert res.status == STATUS_ERROR and builds[4].label in res.message
    res = _run(builds, Synthetic(builds, step=2, fail=(0,)))
    assert res.status == STATUS_ERROR and len(res.probes) == 1


def test_same_build_or_too_few_runs_is_refused():
    builds = _builds(3)
    with pytest.raises(BisectError):
        bs.run_bisect(builds, builds[1], builds[1], Synthetic(builds, 1), THR)
    with pytest.raises(BisectError):
        bs.run_bisect(builds, builds[0], builds[2], Synthetic(builds, 1), THR, runs=3)
    with pytest.raises(BisectError):
        bs.run_bisect(builds[:2], builds[0], builds[2], Synthetic(builds, 1), THR)


def test_stop_before_probe():
    builds = _builds(9)
    m = Synthetic(builds, step=5)
    res = _run(builds, m, should_stop=lambda: len(m.calls) >= 3)
    assert res.status == STATUS_STOPPED and len(m.calls) == 3
    assert res.suspects                                     # отрезок, где искать дальше


def test_stop_before_endpoints():
    builds = _builds(4)
    res = _run(builds, Synthetic(builds, 2), should_stop=lambda: True)
    assert res.status == STATUS_STOPPED


# ── класс пробы ──────────────────────────────────────────────────────────

GOOD = [1.00, 1.01, 0.99, 1.00, 1.02, 0.98, 1.00]
BAD = [1.30, 1.31, 1.29, 1.30, 1.32, 1.28, 1.30]


def test_classify_good_bad_and_intermediate():
    assert bs.classify(GOOD, BAD, [x * 1.001 for x in GOOD], THR)[0] == LIKE_GOOD
    assert bs.classify(GOOD, BAD, [x * 0.999 for x in BAD], THR)[0] == LIKE_BAD
    verdict, vg, vb, note = bs.classify(GOOD, BAD, [x * 1.15 for x in GOOD], THR)
    assert verdict == LIKE_BAD and "промежуточная" in note
    assert vg["decision"] == "РЕГРЕССИЯ" and vb["decision"] == "УСКОРЕНИЕ"


def test_classify_equivalent_to_both_is_undetermined():
    near_bad = [1.04, 1.05, 1.03, 1.04, 1.06, 1.02, 1.04]
    verdict, _vg, _vb, note = bs.classify(GOOD, near_bad, [1.02] * 7, 10.0)
    assert verdict == NOT_DETERMINED and "обеим" in note


def test_classify_too_few_runs_is_undetermined():
    assert bs.classify(GOOD, BAD, [1.0, 1.0], THR)[0] == NOT_DETERMINED


# ── сборки ───────────────────────────────────────────────────────────────

def test_parse_version_and_order():
    assert bs.parse_version("R7-Office-2026.10.1-x64") == (2026, 10, 1)
    assert bs.parse_version("setup") is None
    builds, unversioned = bs.builds_from_files(
        [Path("D/r7-2026.10.1.exe"), Path("D/r7-2026.9.5.exe"), Path("D/setup.exe"),
         Path("D/r7-2026.9.12.msi")])
    assert [b.label for b in builds] == ["2026.9.5", "2026.9.12", "2026.10.1"]
    assert unversioned == ["setup.exe"]


def test_resolve_build_by_name_path_and_version():
    builds, _ = bs.builds_from_files([Path("D/r7-2026.3.2.100.exe"), Path("D/r7-2026.3.3.7.exe"),
                                      Path("D/r7-2026.3.3.9.exe")])
    assert bs.resolve_build(builds, "r7-2026.3.2.100.exe") == builds[0]
    assert bs.resolve_build(builds, r"C:\x\Distributives\r7-2026.3.3.7.exe") == builds[1]
    assert bs.resolve_build(builds, "r7-2026.3.3.9") == builds[2]
    assert bs.resolve_build(builds, "v2026.3.2") == builds[0]          # начало номера
    assert bs.resolve_build(builds, "2026.3.3.9") == builds[2]
    with pytest.raises(BisectError, match="нескольким"):
        bs.resolve_build(builds, "2026.3.3")
    with pytest.raises(BisectError, match="нет среди"):
        bs.resolve_build(builds, "2025.1")
    with pytest.raises(BisectError):
        bs.resolve_build(builds, "")
    with pytest.raises(BisectError, match="не найдена"):
        bs.resolve_build(builds, "latest")


def test_find_build_for_installed_version():
    builds, _ = bs.builds_from_files([Path("r7-2026.3.2.exe"), Path("r7-2026.3.3.10.exe"),
                                      Path("r7-2026.3.3.11.exe")])
    assert bs.find_build_for_installed(builds, "2026.3.2.3229") == builds[0]   # имя без сборки
    assert bs.find_build_for_installed(builds, "2026.3.3.11") == builds[2]
    assert bs.find_build_for_installed(builds, "2026.3.3") is None              # двусмысленно
    assert bs.find_build_for_installed(builds, "2027.1") is None
    assert bs.find_build_for_installed(builds, None) is None


def test_max_probes_estimate():
    assert [bs._max_probes(n) for n in (2, 3, 4, 5, 9, 17)] == [0, 1, 2, 2, 3, 4]


# ── итог ─────────────────────────────────────────────────────────────────

def test_result_dict_is_json_and_summary_text():
    builds = _builds(6)
    res = _run(builds, Synthetic(builds, 3), op="Ctrl+A", threshold_source="по умолчанию")
    d = json.loads(json.dumps(res.to_dict(), ensure_ascii=False))
    assert d["status"] == STATUS_FOUND and d["first_bad"]["label"] == builds[3].label
    assert d["total_runs"] == res.total_runs == 7 * len(res.probes)
    assert d["op"] == "Ctrl+A" and len(d["builds"]) == 6
    text = bs.format_result(res)
    assert "Ctrl+A" in text and builds[3].label in text and "порог 5 %" in text
    assert LIKE_BAD in text and LIKE_GOOD in text


def test_logs_progress():
    builds = _builds(5)
    lines = []
    _run(builds, Synthetic(builds, 2), log=lines.append)
    assert lines[0].startswith("🔎 Бисект: 5 сборок") and lines[-1].startswith("✅")
