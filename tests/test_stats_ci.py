"""Статистика сравнения этапа 3 (r7/stats.py, docs/statistics.md): сдвиг
Ходжеса-Лемана, точный перестановочный p, bootstrap-интервал, поправка
Бенджамини-Хохберга, MDE, вердикт по интервалу и семья сравнений.

Известные значения посчитаны вручную — в комментарии у каждого теста.
"""
import itertools
import math
import random
import statistics

import pytest

from r7 import stats


# ── робастный CV ──────────────────────────────────────────────────────────

def test_robust_cv_known_value():
    # медиана 3, |отклонения| 2,1,0,1,2 → MAD 1; 1 × 1,4826 / 3 × 100
    assert stats.robust_cv_pct([1, 2, 3, 4, 5]) == pytest.approx(49.42, abs=0.01)


def test_robust_cv_constant_is_zero_and_degenerate_is_none():
    assert stats.robust_cv_pct([2.0] * 6) == 0.0
    assert stats.robust_cv_pct([1.0]) is None
    assert stats.robust_cv_pct([0.0, 0.0, 0.0]) is None
    assert stats.robust_cv_pct([]) is None


def test_robust_cv_ignores_single_outlier():
    """Один выброс (сборка мусора) не раздувает шум, в отличие от σ."""
    vals = [1.00, 1.01, 0.99, 1.00, 1.01, 0.99, 3.0]
    assert stats.robust_cv_pct(vals) < 2.0
    assert statistics.stdev(vals) / statistics.median(vals) * 100 > 50


# ── Ходжес-Леман ──────────────────────────────────────────────────────────

def test_hodges_lehmann_known_values():
    # разности new−base: 1,2,3,0,1,2,−1,0,1 → медиана 1
    assert stats.hodges_lehmann_shift([1, 2, 3], [2, 3, 4]) == 1
    # разности 9 и 8 → 8,5
    assert stats.hodges_lehmann_shift([1, 2], [10]) == 8.5
    # сдвиг на константу точно восстанавливается
    base = [1.0, 1.3, 0.9, 1.1, 1.2]
    assert stats.hodges_lehmann_shift(base, [b + 0.25 for b in base]) == pytest.approx(0.25)


def test_hodges_lehmann_empty_is_none():
    assert stats.hodges_lehmann_shift([], [1.0]) is None


# ── точный перестановочный p ──────────────────────────────────────────────

def test_exact_p_complete_separation_known_values():
    # C(6,3)=20 разметок, крайние U=0 и U=9 → 2/20
    assert stats.exact_mann_whitney_p([1, 2, 3], [4, 5, 6]) == pytest.approx(0.1)
    # C(5,2)=10, крайние U=0 и U=6 → 2/10
    assert stats.exact_mann_whitney_p([1, 2], [3, 4, 5]) == pytest.approx(0.2)
    # 6 и 6: 2/C(12,6) = 2/924
    assert stats.exact_mann_whitney_p(list(range(6)), list(range(10, 16))) == \
        pytest.approx(2 / 924)


def test_exact_p_identical_samples_is_one():
    assert stats.exact_mann_whitney_p([1.0, 1.0, 1.0], [1.0, 1.0, 1.0]) == 1.0


def test_exact_p_too_large_or_empty_is_none():
    assert stats.exact_mann_whitney_p(list(range(9)), list(range(5))) is None
    assert stats.exact_mann_whitney_p([], [1.0]) is None


def _brute_force_p(x, y):
    """Независимый оракул: все перестановки объединённой выборки (не
    сочетания рангов), U по определению — число пар (a, b), a > b, плюс ½ за
    равенство."""
    def u(a, b):
        return sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in a for q in b)
    n1 = len(x)
    pooled = list(x) + list(y)
    mean = len(x) * len(y) / 2
    obs = abs(u(y, x) - mean)
    hits = total = 0
    for perm in itertools.permutations(pooled):
        total += 1
        if abs(u(perm[n1:], perm[:n1]) - mean) >= obs - 1e-9:
            hits += 1
    return hits / total


@pytest.mark.parametrize("seed", range(6))
def test_exact_p_matches_brute_force_with_ties(seed):
    rng = random.Random(seed)
    x = [rng.choice([1.0, 1.1, 1.2, 1.3]) for _ in range(3)]
    y = [rng.choice([1.1, 1.2, 1.3, 1.4]) for _ in range(4)]
    assert stats.exact_mann_whitney_p(x, y) == pytest.approx(_brute_force_p(x, y))


# ── bootstrap ─────────────────────────────────────────────────────────────

STEADY = [1.00, 1.01, 0.99, 1.02, 1.00, 0.98]
SLOW = [1.50, 1.52, 1.49, 1.51, 1.50, 1.48]


def test_bootstrap_is_deterministic_with_seed():
    a = stats.bootstrap_ratio_ci(STEADY, SLOW, seed=7)
    b = stats.bootstrap_ratio_ci(STEADY, SLOW, seed=7)
    assert a == b
    assert stats.bootstrap_ratio_ci(STEADY, SLOW) == stats.bootstrap_ratio_ci(STEADY, SLOW)


def test_bootstrap_does_not_touch_global_random():
    random.seed(123)
    expected = random.random()
    random.seed(123)
    stats.bootstrap_ratio_ci(STEADY, SLOW)
    assert random.random() == expected


def test_bootstrap_interval_contains_true_ratio():
    low, high = stats.bootstrap_ratio_ci(STEADY, SLOW)
    assert low < 50.0 < high
    assert 45 < low and high < 55


def test_bootstrap_constant_samples_give_point_interval():
    assert stats.bootstrap_ratio_ci([2.0] * 5, [3.0] * 5) == pytest.approx((50.0, 50.0))


def test_bootstrap_empty_or_zero_base_is_none():
    assert stats.bootstrap_ratio_ci([], [1.0]) is None
    assert stats.bootstrap_ratio_ci([0.0] * 5, [1.0] * 5) is None


def test_percentile_interpolates_like_numpy():
    # numpy.percentile([1,2,3,4], 25) = 1.75
    assert stats._percentile([1, 2, 3, 4], 0.25) == pytest.approx(1.75)


# ── Бенджамини-Хохберг ────────────────────────────────────────────────────

def test_bh_against_hand_computed_values():
    # m=4, по рангу: 0,01·4/1=0,04; 0,03·4/2=0,06; 0,04·4/3=0,0533; 0,5·4/4=0,5;
    # накопленный минимум с конца: 0,04; 0,0533; 0,0533; 0,5
    adj = stats.benjamini_hochberg([0.01, 0.04, 0.03, 0.5])
    assert adj == pytest.approx([0.04, 0.16 / 3, 0.16 / 3, 0.5])


def test_bh_skips_none_and_caps_at_one():
    adj = stats.benjamini_hochberg([None, 0.02, 0.9, None])
    assert adj[0] is None and adj[3] is None
    assert adj[1:3] == pytest.approx([0.04, 0.9])
    assert stats.benjamini_hochberg([0.8, 0.9]) == pytest.approx([0.9, 0.9])
    assert stats.benjamini_hochberg([]) == []
    assert stats.benjamini_hochberg([None]) == [None]


def test_bh_single_test_is_unchanged():
    assert stats.benjamini_hochberg([0.0123]) == [0.0123]


# ── MDE ───────────────────────────────────────────────────────────────────

def test_mde_formula():
    # порог + (1,96 + 0,8416) · 1,2533 · CV · √(1/n1 + 1/n2)
    expected = 6.0 + (1.96 + 0.8416) * 1.2533 * 2.0 * math.sqrt(2 / 7)
    assert stats.min_detectable_effect_pct(2.0, 7, 7, 6.0) == pytest.approx(expected)


def test_mde_shrinks_with_more_runs_and_needs_cv():
    assert stats.min_detectable_effect_pct(3.0, 10, 10) < stats.min_detectable_effect_pct(3.0, 5, 5)
    assert stats.min_detectable_effect_pct(None, 7, 7) is None
    assert stats.min_detectable_effect_pct(1.0, 0, 7) is None


# ── вердикт по интервалу ──────────────────────────────────────────────────

@pytest.mark.parametrize("low, high, expected", [
    (7.0, 18.0, "регрессия"),
    (-18.0, -7.0, "ускорение"),
    (-1.0, 2.0, "эквивалентно"),
    (3.0, 18.0, "не определено"),     # пересекает +порог
    (-8.0, 1.0, "не определено"),
    (None, None, "не определено"),
])
def test_interval_verdict(low, high, expected):
    assert stats.interval_verdict(low, high, 5.0) == expected


def test_decide_requires_significance_for_shift():
    assert stats.decide(7.0, 18.0, 5.0, 0.01) == "РЕГРЕССИЯ"
    assert stats.decide(7.0, 18.0, 5.0, 0.2) == "не определено"
    assert stats.decide(-18.0, -7.0, 5.0, 0.01) == "УСКОРЕНИЕ"
    assert stats.decide(-1.0, 1.0, 5.0, 0.9) == "эквивалентно"


# ── compare_runs: новые ключи и два режима ────────────────────────────────

NEW_KEYS = {"decision", "interval_verdict", "threshold_pct", "ci_low_pct", "ci_high_pct",
            "ci_level", "hl_shift", "hl_shift_pct", "p_raw", "p_exact", "p_adjusted",
            "family_size", "cv_pct", "mde_pct"}


def test_compare_runs_keeps_old_keys_and_adds_new():
    res = stats.compare_runs(STEADY, SLOW)
    assert {"verdict", "median_base", "median_new", "effect_pct", "p_value",
            "n_base", "n_new"} <= set(res)
    assert NEW_KEYS <= set(res)
    assert res["verdict"] == "РЕГРЕССИЯ" and res["threshold_pct"] is None
    assert res["p_exact"] and res["p_raw"] == pytest.approx(2 / 924)
    assert res["hl_shift"] == pytest.approx(0.5) and res["hl_shift_pct"] == 50.0
    assert res["ci_low_pct"] < 50 < res["ci_high_pct"]


def test_compare_runs_insufficient_has_new_keys_none():
    res = stats.compare_runs([1.0] * 3, [1.0] * 3)
    assert res["verdict"] == "недостаточно прогонов"
    assert NEW_KEYS <= set(res) and res["ci_low_pct"] is None and res["decision"] is None


def test_noise_threshold_catches_shift_the_default_misses():
    """Ctrl+V с разбросом 0,1 %: сдвиг 5 % по прежнему порогу 10 % не виден,
    с порогом шума 2 % — регрессия."""
    base = [2.000, 2.001, 1.999, 2.002, 2.000, 1.998, 2.001]
    new = [b * 1.05 for b in base]
    assert stats.compare_runs(base, new)["verdict"] == "без изменений"
    res = stats.compare_runs(base, new, threshold_pct=2.0)
    assert res["verdict"] == "РЕГРЕССИЯ" and res["decision"] == "РЕГРЕССИЯ"
    assert res["threshold_pct"] == 2.0


def test_equivalent_and_undetermined_both_map_to_no_change():
    base = [1.00, 1.01, 0.99, 1.02, 1.00, 0.98, 1.01]
    same = stats.compare_runs(base, list(base), threshold_pct=10.0)
    assert same["decision"] == "эквивалентно" and same["verdict"] == "без изменений"
    noisy_new = [1.00, 1.30, 0.80, 1.25, 0.95, 1.20, 0.85]
    unsure = stats.compare_runs(base, noisy_new, threshold_pct=5.0)
    assert unsure["decision"] == "не определено" and unsure["verdict"] == "без изменений"


def test_mde_uses_noise_cv_when_given():
    res = stats.compare_runs(STEADY, SLOW, threshold_pct=4.0, noise_cv_pct=1.0)
    expected = stats.min_detectable_effect_pct(1.0, 6, 6, 4.0)
    assert res["cv_pct"] == 1.0 and res["mde_pct"] == round(expected, 1)


# ── семья сравнений ───────────────────────────────────────────────────────

def test_adjust_family_turns_lone_weak_regression_likely():
    """5 и 5 повторов: точный p не меньше 2/252 ≈ 0,0079. Одна такая
    регрессия среди 17 операций после поправки даёт 0,135 — уже не РЕГРЕССИЯ,
    но сырой p прошёл: «вероятная регрессия», видна, но не тревога."""
    base = [1.00, 1.01, 0.99, 1.02, 0.98]
    slow = [1.50, 1.51, 1.49, 1.52, 1.48]
    results = {"op0": stats.compare_runs(base, slow, threshold_pct=10.0)}
    assert results["op0"]["verdict"] == "РЕГРЕССИЯ"
    for i in range(1, 17):
        results[f"op{i}"] = stats.compare_runs(base, list(base), threshold_pct=10.0)
    final = stats.adjust_family(results)
    assert final["op0"]["p_adjusted"] == pytest.approx(2 / 252 * 17)
    assert final["op0"]["family_size"] == 17
    assert final["op0"]["decision"] == "вероятная регрессия"
    assert final["op0"]["verdict"] == "без изменений"
    assert results["op0"]["verdict"] == "РЕГРЕССИЯ"      # исходные не тронуты


def test_adjust_family_keeps_strong_regression():
    base = [1.00, 1.01, 0.99, 1.02, 1.00, 0.98, 1.01]
    slow = [b * 1.5 for b in base]
    results = {"a": stats.compare_runs(base, slow, threshold_pct=10.0),
               "b": stats.compare_runs(base, list(base), threshold_pct=10.0)}
    final = stats.adjust_family(results)
    assert final["a"]["verdict"] == "РЕГРЕССИЯ" and final["a"]["p_adjusted"] < 0.05
    assert final["b"]["decision"] == "эквивалентно"


def test_adjust_family_legacy_mode_only_adds_p_adjusted():
    results = {"a": stats.compare_runs(STEADY, SLOW), "b": stats.compare_runs([1.0] * 2, [1.0] * 2)}
    final = stats.adjust_family(results)
    assert final["a"]["verdict"] == "РЕГРЕССИЯ" and final["a"]["p_adjusted"] is not None
    assert final["b"]["p_adjusted"] is None and final["b"]["verdict"] == "недостаточно прогонов"


def test_bootstrap_cost_is_reasonable():
    """17 операций × 2000 пересборок не должны тормозить сборку страницы."""
    import time
    t0 = time.perf_counter()
    for _ in range(17):
        stats.compare_runs(STEADY + [1.0, 1.01], SLOW + [1.5, 1.49], threshold_pct=5.0)
    assert time.perf_counter() - t0 < 5.0


def test_likely_regression_when_only_raw_p_passes():
    # Интервал за порогом, сырой p < 0.05, скорректированный нет: не тревога,
    # но и не «не определено» — одиночная регрессия должна быть видна.
    assert stats.decide(12.0, 20.0, 5.0, 0.13, p_raw=0.008) == stats.LIKELY_REGRESSION
    assert stats.decide(-20.0, -12.0, 5.0, 0.13, p_raw=0.008) == stats.LIKELY_SPEEDUP
    assert stats.decide(12.0, 20.0, 5.0, 0.13, p_raw=0.2) == stats.UNDETERMINED
    assert stats.decide(12.0, 20.0, 5.0, 0.01, p_raw=0.008) == stats.REGRESSION
    assert stats._legacy_verdict(stats.LIKELY_REGRESSION) == stats.NO_CHANGE


def test_family_of_17_keeps_single_regression_visible():
    import random as _r
    rng = _r.Random(5)
    base = {f"op{i}": [1.0 + rng.gauss(0, 0.003) for _ in range(5)] for i in range(17)}
    cur = {k: [x * (1.0 + rng.gauss(0, 0.003)) for x in v] for k, v in base.items()}
    cur["op0"] = [x * 1.3 for x in base["op0"]]
    res = {k: stats.compare_runs(base[k], cur[k], threshold_pct=5.0) for k in base}
    adj = stats.adjust_family(res)
    assert adj["op0"]["decision"] == stats.LIKELY_REGRESSION
    assert adj["op0"]["verdict"] == stats.NO_CHANGE
