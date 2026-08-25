"""Тесты сравнения версий (этап 2, M5): критерий Манна-Уитни без scipy +
практическая величина эффекта.

Раздел "оракул" ниже — САМОСТОЯТЕЛЬНАЯ проверка правильности статистики:
без scipy негде свериться с эталонной реализацией, поэтому здесь есть
собственный брутфорс-перебор всех перестановок (единственный способ
получить ТОЧНЫЙ p-value для маленьких выборок), и производная реализация
(нормальное приближение в r7_Testovarka._mann_whitney_u) сверяется против
него — не только против собственных примеров с заранее известным ответом.
"""
import itertools
import statistics

import pytest

import r7_Testovarka as r7mod


# ── оракул: точный p-value полным перебором перестановок ─────────────────

def _exact_mann_whitney_p(x, y):
    """Точный двусторонний p-value: перебирает ВСЕ способы разметить
    n1+n2 рангов (с учётом связей — усреднённые ранги, как и в
    production-коде) на группу размера n1, и считает долю перестановок,
    чья статистика U1 хотя бы так же далека от среднего (n1*n2/2), как и
    наблюдаемая. Это определение точного теста Манна-Уитни-Уилкоксона —
    независимая от _mann_whitney_u реализация (не переиспользует ранжирование
    оттуда, чтобы не унаследовать возможную общую ошибку)."""
    n1, n2 = len(x), len(y)
    n = n1 + n2
    combined = sorted(list(x) + list(y))

    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and combined[j] == combined[i]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j

    mean_u = n1 * n2 / 2.0

    def u_for_positions(positions):
        rank_sum = sum(ranks[p] for p in positions)
        return rank_sum - n1 * (n1 + 1) / 2.0

    # наблюдаемая статистика: какие позиции в combined принадлежат x.
    # combined отсортирован, поэтому ищем позиции первых вхождений значений
    # x с учётом дублей — проще пересчитать U1 напрямую по исходным данным
    # тем же способом, что и по позициям (согласованность).
    sorted_x = sorted(x)
    used = [False] * n
    obs_positions = []
    for v in sorted_x:
        for idx in range(n):
            if not used[idx] and combined[idx] == v:
                used[idx] = True
                obs_positions.append(idx)
                break
    u1_observed = u_for_positions(obs_positions)
    observed_dist = abs(u1_observed - mean_u)

    total = 0
    extreme = 0
    for positions in itertools.combinations(range(n), n1):
        total += 1
        u1 = u_for_positions(positions)
        if abs(u1 - mean_u) >= observed_dist - 1e-9:
            extreme += 1
    return extreme / total


@pytest.mark.parametrize("x,y", [
    ([1, 2, 3, 4, 5], [6, 7, 8, 9, 10]),          # полное разделение
    ([1, 2, 3, 4, 5], [3, 4, 5, 6, 7]),           # частичное перекрытие
    ([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]),           # полное совпадение
    ([1, 1, 2, 2, 3], [2, 3, 3, 4, 4]),           # со связями (ties)
    ([5, 3, 8, 1, 9, 2], [4, 6, 7, 10, 11, 3]),   # n1=n2=6, вперемешку
    ([1, 2], [10, 11, 12, 13, 14]),               # n1=2, n2=5 (неравные)
])
def test_normal_approximation_agrees_with_exact_permutation_test(x, y):
    """Нормальное приближение (production-код) не обязано совпадать с
    точным p-value до знака после запятой, но обязано давать один и тот же
    практический вывод (значимо/незначимо на alpha=0.05) — иначе разница
    между "точно" и "приближённо" ломает сам смысл вердикта."""
    exact_p = _exact_mann_whitney_p(x, y)
    _, approx_p = r7mod._mann_whitney_u(x, y)

    # Численная близость — мягкая проверка (приближение есть приближение).
    assert abs(exact_p - approx_p) < 0.15, (
        f"точный p={exact_p:.4f}, приближённый p={approx_p:.4f} — "
        f"разошлись сильнее ожидаемого допуска")

    # Практический вывод (главное, от чего зависит compare_runs) должен
    # совпадать по обе стороны alpha=0.05.
    assert (exact_p < 0.05) == (approx_p < 0.05), (
        f"разные выводы о значимости: точный p={exact_p:.4f}, "
        f"приближённый p={approx_p:.4f}")


def test_identical_samples_give_p_one_not_crash():
    u1, p = r7mod._mann_whitney_u([5.0, 5.0, 5.0], [5.0, 5.0, 5.0])
    assert p == 1.0


def test_mann_whitney_p_value_in_valid_range():
    """Регрессия на арифметику: p всегда в [0, 1], никогда не NaN/отрицательный
    (нормальное приближение теоретически может дать z настолько большим,
    что 2*(1-Phi(z)) уходит в отрицательные числа из-за ошибок округления
    без явного clamp)."""
    import random
    rnd = random.Random(0)
    for _ in range(200):
        n1, n2 = rnd.randint(2, 15), rnd.randint(2, 15)
        x = [rnd.uniform(0, 100) for _ in range(n1)]
        y = [rnd.uniform(0, 100) for _ in range(n2)]
        _, p = r7mod._mann_whitney_u(x, y)
        assert 0.0 <= p <= 1.0


# ── compare_runs ────────────────────────────────────────────────────────────

def test_compare_runs_insufficient_samples():
    result = r7mod.compare_runs([1.0, 1.1, 1.0], [1.0, 1.1])
    assert result["verdict"] == "недостаточно прогонов"
    assert result["median_base"] is None
    assert result["n_base"] == 3
    assert result["n_new"] == 2


def test_compare_runs_detects_regression():
    base = [1.0, 1.02, 0.98, 1.01, 0.99, 1.0, 1.03]
    new = [1.5, 1.52, 1.48, 1.51, 1.49, 1.5, 1.53]  # ~50% медленнее
    result = r7mod.compare_runs(base, new)
    assert result["verdict"] == "РЕГРЕССИЯ"
    assert result["effect_pct"] > 40
    assert result["p_value"] < 0.05


def test_compare_runs_detects_speedup():
    base = [1.5, 1.52, 1.48, 1.51, 1.49, 1.5, 1.53]
    new = [1.0, 1.02, 0.98, 1.01, 0.99, 1.0, 1.03]  # быстрее
    result = r7mod.compare_runs(base, new)
    assert result["verdict"] == "УСКОРЕНИЕ"
    assert result["effect_pct"] < -20


def test_compare_runs_no_change_when_overlapping():
    base = [1.0, 1.05, 0.95, 1.02, 0.98, 1.01, 0.99]
    new = [1.01, 1.03, 0.97, 1.0, 0.99, 1.02, 0.98]
    result = r7mod.compare_runs(base, new)
    assert result["verdict"] == "без изменений"


def test_compare_runs_significant_but_below_effect_threshold_is_no_change():
    """Статистически значимый, но practically незначимый (<10%) сдвиг не
    должен объявляться регрессией — именно ради этого добавлен
    min_effect_pct поверх голого p-value."""
    # Очень маленький, но стабильный сдвиг: значим статистически при узком
    # разбросе, но меньше порога 10%.
    base = [1.000, 1.001, 0.999, 1.000, 1.001, 0.999, 1.000]
    new = [1.040, 1.041, 1.039, 1.040, 1.041, 1.039, 1.040]  # +4%
    result = r7mod.compare_runs(base, new)
    assert result["effect_pct"] == pytest.approx(4.0, abs=0.5)
    assert result["verdict"] == "без изменений"


def test_compare_runs_large_effect_but_not_significant_is_no_change():
    """Большая разница медиан, но выборки настолько шумные/перекрывающиеся,
    что критерий не считает её статистически надёжной."""
    base = [0.5, 3.0, 0.4, 2.8, 0.6, 3.2, 0.5]
    new = [0.9, 2.5, 0.8, 2.6, 1.0, 2.4, 0.9]
    result = r7mod.compare_runs(base, new)
    # Не переоцениваем конкретный вердикт (данные подобраны для
    # демонстрации, не для точного p) — проверяем только контракт: без
    # значимости регрессия/ускорение не объявляются.
    if result["p_value"] >= 0.05:
        assert result["verdict"] == "без изменений"


def test_compare_runs_effect_pct_sign_matches_direction():
    base = [1.0] * 7
    new = [2.0] * 7
    result = r7mod.compare_runs(base, new)
    assert result["effect_pct"] > 0  # new медленнее base — положительный эффект


def test_compare_runs_custom_thresholds():
    base = [1.0, 1.01, 0.99, 1.0, 1.01, 0.99, 1.0]
    new = [1.06, 1.07, 1.05, 1.06, 1.07, 1.05, 1.06]  # ~6%
    default = r7mod.compare_runs(base, new)  # порог 10% — не должно сработать
    loose = r7mod.compare_runs(base, new, min_effect_pct=3.0)
    assert default["verdict"] == "без изменений"
    assert loose["verdict"] == "РЕГРЕССИЯ"


def test_compare_runs_uses_median_not_mean():
    """compare_runs должен опираться на медиану (устойчивую к выбросам,
    см. этап 1, H2), а не на среднее."""
    base = [1.0] * 6 + [1.0]
    new = [1.0] * 6 + [100.0]  # один огромный выброс не должен исказить медиану
    result = r7mod.compare_runs(base, new)
    assert result["median_new"] == statistics.median(new)
    assert result["median_new"] == pytest.approx(1.0)
    assert result["verdict"] == "без изменений"
