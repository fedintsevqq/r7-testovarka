"""Точки смены уровня на ряде замеров (этап 3 плана, п. 4).

Тренд «первый прогон против последнего» не говорит, с какой сборки начался
сдвиг, а один шумный прогон выглядит как регрессия. Здесь — круговая
бинарная сегментация (CBS, Olshen и др., 2004) с ранговой CUSUM-статистикой
и перестановочной значимостью: ищем отрезок ряда, уровень которого сильнее
всего отличается от остального (частный случай — разрез на «до» и «после»),
проверяем перестановками, что такого отрезка не нашлось бы на перемешанном
ряде, и повторяем на получившихся кусках. Отрезок, а не только разрез,
нужен для «ступеньки туда и обратно» (сборка замедлила, следующая починила):
один разрез такую пару не видит — средние до и после почти равны.

Почему ранги, а не сами секунды: один выброс (медленный диск стенда даёт
открытие 14 с вместо 9) не должен тянуть статистику — ранги его гасят.
Почему перестановки, а не формула: точек в тренде десятки, распределение
максимума статистики на таких длинах аналитически честно не оценить.
Перестановки останавливаются досрочно (Besag и Clifford, 1991), как только
стало ясно, что p не опустится ниже alpha: на ровном ряде это десятки
перестановок вместо сотен.

Чистые функции без numpy; random.Random с зерном — ответ воспроизводим.
"""
import re
import random
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MIN_SEGMENT = 3            # точек в сегменте минимум: на двух медиана — среднее, не уровень
PERMUTATIONS = 499         # перестановок на шаг; p не меньше 1/500
ALPHA = 0.05               # порог значимости шага
MIN_SHIFT_PCT = 5.0        # меньше — не сдвиг, а шум (тот же порог, что «нейтрально» в трендах)
DEFAULT_SEED = 20261007


def _ranks(values: Sequence[float]) -> list[float]:
    """Ранги 1..n, у равных значений — средний ранг."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _best_segment(seq: Sequence[float],
                  min_size: int) -> tuple[float, int | None, int | None]:
    """(статистика, i, j) отрезка seq[i:j], сильнее всего отличного от остального.

    Статистика: |среднее внутри − среднее снаружи| × √(m(n−m)/n), m = j − i —
    взвешенная CUSUM, короткие отрезки не выигрывают только за счёт малой
    выборки. i == 0 или j == n — обычный разрез на «до» и «после». Каждый из
    будущих кусков (до i, отрезок, после j) не короче min_size.
    """
    n = len(seq)
    prefix = [0.0]
    for v in seq:
        prefix.append(prefix[-1] + v)
    total = prefix[-1]
    best_i: int | None = None
    best_j: int | None = None
    best = -1.0
    starts = [0] + list(range(min_size, n - min_size + 1))
    for i in starts:
        for j in range(i + min_size, n + 1):
            if j != n and n - j < min_size:
                continue
            m = j - i
            if m == n or n - m < min_size:
                continue
            inside = prefix[j] - prefix[i]
            stat = abs(inside / m - (total - inside) / (n - m)) * (m * (n - m) / n) ** 0.5
            if stat > best:
                best, best_i, best_j = stat, i, j
    return best, best_i, best_j


def _segment_pvalue(seq: Sequence[float], observed: float, min_size: int, permutations: int,
                    alpha: float, rng: random.Random) -> float:
    """Перестановочное p максимума статистики (с поправкой +1). Досрочная
    остановка: как только совпадений столько, что p заведомо ≥ alpha."""
    work = list(seq)
    limit = alpha * (permutations + 1)
    hits = 0
    for done in range(1, permutations + 1):
        rng.shuffle(work)
        stat, _i, _j = _best_segment(work, min_size)
        if stat >= observed - 1e-12:
            hits += 1
            if hits + 1 > limit:
                return (hits + 1) / (done + 1)
    return (hits + 1) / (permutations + 1)


def _l1_cost(seg: Sequence[float]) -> float:
    """Сумма отклонений от медианы — разброс сегмента, устойчивый к выбросу."""
    if not seg:
        return 0.0
    med = statistics.median(seg)
    return sum(abs(v - med) for v in seg)


def _refine(vals: Sequence[float], bounds: Iterable[int], min_size: int) -> list[int]:
    """Уточняет каждую границу по самим значениям, а не по рангам.

    Ранги находят сдвиг, но положение границы по ним смещается: крайняя
    точка «нижнего» уровня, оказавшаяся самой высокой из нижних, по рангу
    близка к «верхним» и тянет границу на себя. Здесь граница двигается в
    пределах соседних сегментов (каждый не короче min_size) туда, где
    сумма отклонений от медиан двух сегментов минимальна.
    """
    bounds = list(bounds)
    for k in range(len(bounds)):
        a = bounds[k - 1] if k > 0 else 0
        c = bounds[k + 1] if k + 1 < len(bounds) else len(vals)
        candidates = range(a + min_size, c - min_size + 1)
        if not candidates:
            continue
        bounds[k] = min(candidates, key=lambda b: (_l1_cost(vals[a:b]) + _l1_cost(vals[b:c]),
                                                   abs(b - bounds[k])))
    return bounds


def _pct(before: float, after: float) -> float | None:
    return (after - before) / before * 100.0 if before > 0 else None


def detect(values: Iterable[float], min_size: int = MIN_SEGMENT, alpha: float = ALPHA,
           permutations: int = PERMUTATIONS, min_shift_pct: float = MIN_SHIFT_PCT,
           seed: int = DEFAULT_SEED) -> list[dict[str, Any]]:
    """Точки смены уровня на ряде.

    Args:
        values: ряд чисел по времени (медианы прогонов, секунды).
        min_size: минимум точек в каждом сегменте (по умолчанию 3).
        alpha: порог перестановочного p для шага сегментации.
        permutations: перестановок на шаг.
        min_shift_pct: минимальный сдвиг медианы соседних сегментов, %;
            значимый, но мелкий сдвиг на длинном ряде — не повод для отметки.
        seed: зерно генератора (одно и то же — один и тот же ответ).

    Returns:
        list[dict]: по возрастанию index — первая точка нового уровня;
        direction («up» — стало медленнее, «down» — быстрее), pct (сдвиг
        медианы к предыдущему сегменту, %), before/after (медианы соседних
        сегментов), p_value шага, который нашёл границу. Пустой список —
        сдвигов нет или ряд короче 2 × min_size.
    """
    vals = [float(v) for v in values]
    rng = random.Random(seed)
    cuts: dict[int, float] = {}

    def _split(lo: int, hi: int) -> None:
        if hi - lo < 2 * min_size:
            return
        seq = _ranks(vals[lo:hi])
        stat, i, j = _best_segment(seq, min_size)
        if i is None or j is None or stat <= 0:
            return
        p = _segment_pvalue(seq, stat, min_size, permutations, alpha, rng)
        if p >= alpha:
            return
        # Границу уточняем сразу, до рекурсии: иначе чужая точка, попавшая
        # в кусок, сама даёт «значимый» подотрезок внутри него.
        local = sorted({i, j} - {0, hi - lo})
        bounds = [lo + b for b in _refine(vals[lo:hi], local, min_size)]
        for b in bounds:
            cuts[b] = round(p, 4)
        pieces = [lo] + bounds + [hi]
        for a, b in zip(pieces, pieces[1:]):
            _split(a, b)

    _split(0, len(vals))
    bounds = _refine(vals, sorted(cuts), min_size)
    cuts = {b: p for b, p in zip(bounds, (cuts[k] for k in sorted(cuts)))}
    # Границы, между которыми уровень почти не изменился (значимо, но мельче
    # min_shift_pct), убираются по одной, начиная с самой мелкой: соседние
    # сегменты сливаются и медианы пересчитываются.
    bounds = sorted(cuts)
    while bounds:
        edges = [0] + bounds + [len(vals)]
        shifts = []
        for k, b in enumerate(bounds):
            before = statistics.median(vals[edges[k]:b])
            after = statistics.median(vals[b:edges[k + 2]])
            pct = _pct(before, after)
            shifts.append((abs(pct) if pct is not None else 0.0, k))
        smallest, k = min(shifts)
        if smallest >= min_shift_pct:
            break
        bounds.pop(k)
    edges = [0] + bounds + [len(vals)]
    found = []
    for k, b in enumerate(bounds):
        before = statistics.median(vals[edges[k]:b])
        after = statistics.median(vals[b:edges[k + 2]])
        # None (база ≤ 0) сюда не доходит: цикл выше считает его нулевым сдвигом
        # и убирает границу.
        pct = round(_pct(before, after) or 0.0, 1)
        found.append({"index": b, "direction": "up" if pct > 0 else "down", "pct": pct,
                      "before": before, "after": after, "p_value": cuts[b]})
    return found


_VERSION_NUMBER = re.compile(r"\d+(?:\.\d+){2,3}")


def format_shift(cp: Mapping[str, Any], version: Any) -> str:
    """Подпись отметки: «сдвиг с 2026.3.2.3229, +8 %». Из полного имени
    продукта («Р7-Офис. Профессиональный … 2026.3.2.3229 (x64)») берётся
    номер версии: подпись стоит на графике и должна быть короткой."""
    m = _VERSION_NUMBER.search(str(version or ""))
    return f"сдвиг с {m.group(0) if m else version}, {round(cp['pct']):+d} %"
