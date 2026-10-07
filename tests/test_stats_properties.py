"""Свойства статистики (r7/stats.py, r7/changepoint.py) на случайных данных.

Hypothesis перебирает выборки, которые руками не придумать: связи, одинаковые
значения, перекошенные размеры. Проверяются не конкретные числа, а законы,
которые обязаны выполняться при любых данных: симметрия Манна-Уитни,
p ∈ [0, 1], свойства поправки Бенджамини-Хохберга, инвариантность вердикта
к масштабу и т. п.

Чтобы равенства были точными, а не «примерно»:
- длительности — целые миллисекунды во float: сложение и сравнение таких
  чисел точное, сдвиг на целое не создаёт ложных связей;
- масштаб — степень двойки: умножение на 2^k не меняет мантиссу, поэтому
  медианы, отношения и bootstrap-интервал совпадают до бита.

Число примеров умеренное, база примеров отключена (`database=None`): набор
должен идти секунды и не оставлять папку `.hypothesis` в рабочей копии.
"""
import math
import statistics

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from r7 import changepoint, stats
from r7.measure import MeasureMixin

FAST = settings(max_examples=60, deadline=None, database=None)
SLOW = settings(max_examples=20, deadline=None, database=None)

# Длительность операции в миллисекундах (целое число во float).
ms = st.integers(min_value=1, max_value=200_000).map(float)
# Выборка повторов одной версии: как в отчётах, 5–8 повторов.
sample = st.lists(ms, min_size=5, max_size=8)
small_sample = st.lists(ms, min_size=1, max_size=6)
pow2 = st.integers(min_value=-6, max_value=6).map(lambda k: 2.0 ** k)
shift = st.integers(min_value=-500, max_value=500).map(float)
p_list = st.lists(st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0)),
                  max_size=20)

N_BOOT = 200   # меньше пересборок, чем в отчёте: свойства от их числа не зависят


# ── Манн-Уитни ───────────────────────────────────────────────────────────

@FAST
@given(small_sample, small_sample)
def test_mann_whitney_swap_same_p_complementary_u(x, y):
    u_xy, p_xy = stats._mann_whitney_u(x, y)
    u_yx, p_yx = stats._mann_whitney_u(y, x)
    assert 0.0 <= p_xy <= 1.0
    assert p_xy == p_yx
    # U1 + U2 = n1·n2: перестановка выборок — тот же сдвиг с обратным знаком.
    assert u_xy + u_yx == len(x) * len(y)


@FAST
@given(small_sample, small_sample)
def test_exact_p_in_unit_interval_and_symmetric(x, y):
    p = stats.exact_mann_whitney_p(x, y)
    assert 0.0 < p <= 1.0
    assert math.isclose(p, stats.exact_mann_whitney_p(y, x), rel_tol=1e-12)


@FAST
@given(small_sample)
def test_exact_p_of_identical_samples_is_one(x):
    assert stats.exact_mann_whitney_p(x, list(x)) == 1.0


@FAST
@given(small_sample, small_sample, shift, pow2)
def test_rank_tests_invariant_to_common_shift_and_scale(x, y, s, c):
    assume(min(x + y) + s > 0)
    xs, ys = [v + s for v in x], [v + s for v in y]
    assert stats._mann_whitney_u(xs, ys) == stats._mann_whitney_u(x, y)
    assert stats.exact_mann_whitney_p(xs, ys) == stats.exact_mann_whitney_p(x, y)
    xc, yc = [v * c for v in x], [v * c for v in y]
    assert stats.exact_mann_whitney_p(xc, yc) == stats.exact_mann_whitney_p(x, y)


def test_exact_p_none_outside_its_range():
    assert stats.exact_mann_whitney_p([], [1.0]) is None
    assert stats.exact_mann_whitney_p([1.0] * (stats.EXACT_P_MAX_N + 1), [1.0]) is None


# ── Сдвиг Ходжеса-Лемана ─────────────────────────────────────────────────

@FAST
@given(small_sample, small_sample, shift)
def test_hodges_lehmann_antisymmetric_and_shift_equivariant(x, y, s):
    hl = stats.hodges_lehmann_shift(x, y)
    assert stats.hodges_lehmann_shift(y, x) == -hl
    assert stats.hodges_lehmann_shift(x, [v + s for v in y]) == hl + s
    assert stats.hodges_lehmann_shift([v + s for v in x], [v + s for v in y]) == hl


@FAST
@given(small_sample, small_sample)
def test_separated_samples_give_shift_of_right_sign(x, y):
    # Всё новое медленнее всего старого — сдвиг положительный, и наоборот.
    slow = [v + max(x) for v in y]
    assert stats.hodges_lehmann_shift(x, slow) > 0
    assert stats.hodges_lehmann_shift(slow, x) < 0


# ── Медиана, MAD, робастный CV ───────────────────────────────────────────

@FAST
@given(small_sample, shift, pow2)
def test_mad_properties(x, s, c):
    mad = MeasureMixin._mad(x)
    assert 0.0 <= mad <= max(x) - min(x)
    assert MeasureMixin._mad([v + s for v in x]) == mad
    assert MeasureMixin._mad([v * c for v in x]) == mad * c
    assert min(x) <= statistics.median(x) <= max(x)
    assert stats._median_sorted(x) == statistics.median(x)


@FAST
@given(ms, st.integers(min_value=1, max_value=10))
def test_mad_and_cv_of_constant_sample_are_zero(v, n):
    assert MeasureMixin._mad([v] * n) == 0.0
    if n >= 2:
        assert stats.robust_cv_pct([v] * n) == 0.0


@FAST
@given(st.lists(ms, min_size=2, max_size=10), pow2)
def test_robust_cv_nonnegative_and_scale_free(x, c):
    cv = stats.robust_cv_pct(x)
    assert cv >= 0.0
    assert stats.robust_cv_pct([v * c for v in x]) == cv


@FAST
@given(st.lists(ms, min_size=1, max_size=30), st.floats(min_value=0.0, max_value=1.0))
def test_percentile_within_range_and_monotone(x, q):
    vals = sorted(x)
    p = stats._percentile(vals, q)
    assert vals[0] <= p <= vals[-1]
    assert stats._percentile(vals, 0.0) == vals[0]
    assert stats._percentile(vals, 1.0) == vals[-1]


# ── Поправка Бенджамини-Хохберга ─────────────────────────────────────────

@FAST
@given(p_list)
def test_benjamini_hochberg_bounds_and_monotonicity(ps):
    adj = stats.benjamini_hochberg(ps)
    assert len(adj) == len(ps)
    pairs = [(p, a) for p, a in zip(ps, adj)]
    for p, a in pairs:
        assert (p is None) == (a is None)      # None не входит в семью и остаётся None
        if p is not None:
            assert p <= a <= 1.0
    known = sorted((p, a) for p, a in pairs if p is not None)
    # Порядок сырых p сохраняется: меньший сырой — не больший скорректированный.
    for (_p1, a1), (_p2, a2) in zip(known, known[1:]):
        assert a1 <= a2


@FAST
@given(st.floats(min_value=0.0, max_value=1.0))
def test_benjamini_hochberg_single_p_unchanged(p):
    assert stats.benjamini_hochberg([p]) == [p]


@FAST
@given(p_list, st.randoms(use_true_random=False))
def test_benjamini_hochberg_independent_of_order(ps, rnd):
    order = list(range(len(ps)))
    rnd.shuffle(order)
    shuffled = [ps[i] for i in order]
    adj, adj_sh = stats.benjamini_hochberg(ps), stats.benjamini_hochberg(shuffled)
    assert [adj[i] for i in order] == adj_sh


# ── Интервал и решение ───────────────────────────────────────────────────

ci_bound = st.floats(min_value=-200.0, max_value=200.0, allow_nan=False)
threshold = st.floats(min_value=0.0, max_value=50.0, allow_nan=False)
p_or_none = st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0))

MIRROR = {stats.REGRESSION: stats.SPEEDUP, stats.SPEEDUP: stats.REGRESSION,
          stats.LIKELY_REGRESSION: stats.LIKELY_SPEEDUP,
          stats.LIKELY_SPEEDUP: stats.LIKELY_REGRESSION,
          stats.INTERVAL_REGRESSION: stats.INTERVAL_SPEEDUP,
          stats.INTERVAL_SPEEDUP: stats.INTERVAL_REGRESSION}


@FAST
@given(ci_bound, ci_bound, threshold, p_or_none, p_or_none)
def test_decision_mirrors_when_interval_is_mirrored(a, b, t, p, p_raw):
    lo, hi = min(a, b), max(a, b)
    d = stats.decide(lo, hi, t, p, p_raw=p_raw)
    d_mirror = stats.decide(-hi, -lo, t, p, p_raw=p_raw)
    assert d_mirror == MIRROR.get(d, d)
    iv = stats.interval_verdict(lo, hi, t)
    assert stats.interval_verdict(-hi, -lo, t) == MIRROR.get(iv, iv)


@FAST
@given(ci_bound, ci_bound, threshold, st.floats(min_value=0.0, max_value=1.0), pow2)
def test_decision_invariant_to_scaling_interval_and_threshold(a, b, t, p, c):
    lo, hi = min(a, b), max(a, b)
    assert stats.decide(lo * c, hi * c, t * c, p) == stats.decide(lo, hi, t, p)


@FAST
@given(ci_bound, ci_bound, threshold, st.floats(min_value=stats.COMPARISON_ALPHA, max_value=1.0))
def test_no_alarm_without_significance(a, b, t, p):
    lo, hi = min(a, b), max(a, b)
    assert stats.decide(lo, hi, t, p) not in (stats.REGRESSION, stats.SPEEDUP)
    assert stats.decide(lo, hi, t, None) not in (stats.REGRESSION, stats.SPEEDUP)


# ── Bootstrap-интервал ───────────────────────────────────────────────────

@FAST
@given(sample, sample)
def test_bootstrap_ci_ordered_and_reproducible(x, y):
    ci = stats.bootstrap_ratio_ci(x, y, n_boot=N_BOOT)
    assert ci[0] <= ci[1]
    assert stats.bootstrap_ratio_ci(x, y, n_boot=N_BOOT) == ci


@FAST
@given(ms, ms, st.integers(1, 8), st.integers(1, 8))
def test_bootstrap_ci_of_constant_samples_is_a_point(b, n, nb, nn):
    lo, hi = stats.bootstrap_ratio_ci([b] * nb, [n] * nn, n_boot=N_BOOT)
    assert lo == hi == (n / b - 1.0) * 100.0


# ── Сравнение версий целиком ─────────────────────────────────────────────

COMPARE_KEYS = ("verdict", "decision", "interval_verdict", "effect_pct", "p_value",
                "p_raw", "p_exact", "ci_low_pct", "ci_high_pct", "hl_shift_pct",
                "cv_pct", "mde_pct")


@FAST
@given(sample, sample, pow2, st.one_of(st.none(), threshold))
def test_compare_runs_invariant_to_units(x, y, c, thr):
    # Секунды или миллисекунды — вердикт один и тот же.
    r = stats.compare_runs(x, y, threshold_pct=thr, n_boot=N_BOOT)
    rc = stats.compare_runs([v * c for v in x], [v * c for v in y],
                            threshold_pct=thr, n_boot=N_BOOT)
    assert {k: rc[k] for k in COMPARE_KEYS} == {k: r[k] for k in COMPARE_KEYS}
    assert rc["hl_shift"] == r["hl_shift"] * c


@FAST
@given(sample, sample, st.one_of(st.none(), threshold))
def test_compare_runs_p_in_unit_interval_and_swap_keeps_p(x, y, thr):
    r = stats.compare_runs(x, y, threshold_pct=thr, n_boot=N_BOOT)
    r_sw = stats.compare_runs(y, x, threshold_pct=thr, n_boot=N_BOOT)
    for key in ("p_value", "p_raw"):
        assert 0.0 <= r[key] <= 1.0
        assert math.isclose(r[key], r_sw[key], rel_tol=1e-12)
    assert r_sw["hl_shift"] == -r["hl_shift"]


@FAST
@given(sample, threshold)
def test_identical_runs_are_never_a_regression(x, thr):
    r = stats.compare_runs(x, list(x), threshold_pct=thr, n_boot=N_BOOT)
    assert r["verdict"] == stats.NO_CHANGE
    assert r["effect_pct"] == 0.0 and r["p_raw"] == 1.0


@FAST
@given(st.lists(st.tuples(sample, sample), min_size=1, max_size=6))
def test_adjust_family_keeps_inputs_and_bounds(pairs):
    raw = {f"op{i}": stats.compare_runs(x, y, threshold_pct=5.0, n_boot=N_BOOT)
           for i, (x, y) in enumerate(pairs)}
    before = {k: dict(v) for k, v in raw.items()}
    out = stats.adjust_family(raw)
    assert raw == before                         # вход не меняется
    for k, r in out.items():
        assert r["p_raw"] <= r["p_adjusted"] <= 1.0
        assert r["family_size"] == len(pairs)


# ── Минимальный обнаружимый эффект ───────────────────────────────────────

@FAST
@given(st.floats(min_value=0.0, max_value=50.0), st.integers(1, 30), st.integers(1, 30),
       threshold)
def test_mde_at_least_threshold_and_falls_with_more_runs(cv, n1, n2, thr):
    mde = stats.min_detectable_effect_pct(cv, n1, n2, thr)
    assert mde >= thr
    assert stats.min_detectable_effect_pct(cv, n1 + 1, n2 + 1, thr) <= mde


# ── Детектор утечки ──────────────────────────────────────────────────────

@FAST
@given(st.floats(min_value=10.0, max_value=10_000.0),
       st.integers(stats.LEAK_MIN_SAMPLES, 120),
       st.one_of(st.none(), st.integers(1, 300)))
def test_detect_leak_constant_series_has_no_leak(level, n, docs):
    samples = [{"t": 1_000_000.0 + i, "heap_mb": level, "doc_count": docs} for i in range(n)]
    res = stats.detect_leak(samples)
    assert res["leak"] is False
    assert abs(res["slope_mb_per_hour"]) < 1e-6


@FAST
@given(st.integers(0, stats.LEAK_MIN_SAMPLES - 1))
def test_detect_leak_short_series_is_undetermined(n):
    samples = [{"t": float(i), "heap_mb": 100.0 + i} for i in range(n)]
    assert stats.detect_leak(samples)["leak"] is None


@FAST
@given(st.floats(min_value=stats.LEAK_SLOPE_MB_PER_HOUR * 2, max_value=500.0),
       st.integers(stats.LEAK_MIN_SAMPLES * 2, 120))
def test_detect_leak_finds_steady_growth(mb_per_hour, n):
    samples = [{"t": i * 60.0, "heap_mb": 500.0 + mb_per_hour * i / 60.0, "doc_count": 5}
               for i in range(n)]
    res = stats.detect_leak(samples)
    assert res["leak"] is True
    assert math.isclose(res["slope_mb_per_hour"], mb_per_hour, rel_tol=1e-3)


# ── Точки смены уровня ───────────────────────────────────────────────────

@SLOW
@given(ms, st.integers(0, 30))
def test_changepoint_constant_series_has_no_shift(v, n):
    assert changepoint.detect([v] * n) == []


@SLOW
@given(st.lists(ms, min_size=0, max_size=2 * changepoint.MIN_SEGMENT - 1))
def test_changepoint_short_series_is_not_split(vals):
    assert changepoint.detect(vals) == []


@SLOW
@given(st.lists(ms, min_size=6, max_size=18), pow2)
def test_changepoint_invariant_to_units(vals, c):
    found = changepoint.detect(vals)
    scaled = changepoint.detect([v * c for v in vals])
    assert [(f["index"], f["direction"], f["pct"], f["p_value"]) for f in scaled] == \
           [(f["index"], f["direction"], f["pct"], f["p_value"]) for f in found]
    for f in found:
        assert changepoint.MIN_SEGMENT <= f["index"] <= len(vals) - changepoint.MIN_SEGMENT
        assert 0.0 < f["p_value"] < changepoint.ALPHA
