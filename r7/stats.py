"""Статистика замеров: критерий Манна-Уитни, вердикт сравнения версий,
детектор утечки памяти по ряду замеров ресурсов.

Сравнение версий (этап 3 плана, docs/statistics.md): сдвиг Ходжеса-Лемана,
bootstrap-интервал отношения медиан, точный перестановочный p при малых n,
поправка Бенджамини-Хохберга на число операций, порог по шуму стенда и
минимальный обнаружимый эффект.

Чистые функции без зависимостей от Р7, окон и Tk; numpy и scipy не нужны.
"""
import itertools
import math
import random
import statistics


def _linear_slope(points):
    """Наклон прямой методом наименьших квадратов по точкам (x, y).

    Не тянет numpy ради одной формулы — двухпроходная сумма по спискам
    длиной в десятки-сотни точек (частота семплера — раз в секунду, soak
    на несколько часов даёт тысячи, не миллионы) не оправдывает вес
    зависимости.

    Args:
        points: Непустая последовательность (x, y) — минимум 2 точки.

    Returns:
        float | None: Наклон (y на единицу x), либо None, если точек
        меньше 2 или все x совпадают (вертикальная выборка — наклон не
        определён, а не бесконечность).
    """
    pts = list(points)
    n = len(pts)
    if n < 2:
        return None
    mean_x = sum(x for x, _ in pts) / n
    mean_y = sum(y for _, y in pts) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in pts)
    den = sum((x - mean_x) ** 2 for x, _ in pts)
    if den == 0:
        return None
    return num / den


# Первая калибровка (не подтверждена многочасовым живым прогоном — см.
# отчёт по нагрузочному тестированию, раздел про soak-тесты, Stage 3).
# 5 МБ/час — заведомо выше шума однократных сборок мусора (JS-heap
# пилообразно колеблется на десятки МБ между циклами GC; линейная регрессия
# по достаточному числу точек эти колебания усредняет, но порог всё равно
# должен быть больше типичной амплитуды одного цикла, а не любого дрейфа).
LEAK_SLOPE_MB_PER_HOUR = 5.0


LEAK_MIN_SAMPLES = 30  # меньше — наклон недостоверен (шум одной точки GC)


# ПРОВЕРЕНО НА ЖИВОМ Р7 (25.08.2026): CDP-метрика "Documents" (доступна как
# doc_count в замерах ResourceSampler) — это ЧИСЛО ВНУТРЕННИХ DOM-ДОКУМЕНТОВ
# CEF-рендерера (фреймы, служебные контексты), а не «сколько файлов открыл
# пользователь». На живом прогоне с ОДНИМ открытым файлом и без единого
# действия пользователя она сама уехала 228 → 232 за 4 секунды простоя —
# то есть небольшой дрейф этой метрики нормален и не означает, что
# пользователь открыл второй документ. Поэтому «стабильность» ниже — это
# допуск на дрейф (доля от начального значения), а не точное равенство.
DOC_COUNT_STABLE_TOLERANCE_FRAC = 0.10  # первая калибровка по одному
                                        # короткому живому замеру (дрейф
                                        # 4/228 ≈ 1.8%), с запасом на более
                                        # длинный soak — требует уточнения
                                        # по итогам многочасового прогона
                                        # (Stage 3)


def detect_leak(samples, key="heap_mb",
                threshold_mb_per_hour=LEAK_SLOPE_MB_PER_HOUR,
                min_samples=LEAK_MIN_SAMPLES,
                doc_stable_tolerance_frac=DOC_COUNT_STABLE_TOLERANCE_FRAC,
                warmup_frac=0.2):
    """Оценивает наличие утечки по ряду замеров ResourceSampler.

    Критерий: наклон линейной регрессии выше threshold_mb_per_hour ПРИ
    стабильном числе документов (doc_count не выходил за пределы допуска
    doc_stable_tolerance_frac от своего начального значения). Без второго
    условия рост heap из-за открытия новых документов читался бы как
    утечка — это не утечка, а ожидаемый рост данных. Допуск, а не точное
    равенство — см. DOC_COUNT_STABLE_TOLERANCE_FRAC.

    Args:
        samples: Список dict вида {"t": unix-время, key: значение в МБ,
            "doc_count": внутренний счётчик DOM-документов CEF | None}.
            Формат — тот же, что отдаёт ResourceSampler.snapshot().
        key: Какое поле замера анализировать — "heap_mb" (JS-куча
            рендерера, основной сигнал утечки внутри Р7) или "rss_mb"
            (RSS процесса — грубее, аллокатор маскирует освобождённую
            память, но не требует CDP).
        threshold_mb_per_hour: Порог наклона.
        min_samples: Минимум точек с непустым key, иначе наклон недостоверен.
        doc_stable_tolerance_frac: Допустимый дрейф doc_count относительно
            первого замера, доля (0.10 = ±10%).
        warmup_frac: Доля начала ряда (по времени), которая отбрасывается как
            переходный процесс — RAM после открытия документа ещё оседает
            (сборка мусора, освобождение буферов загрузки), и наклон по
            всему ряду отражал это оседание, а не утечку (аудит 29.09.2026,
            пункт 16: −10011 МБ/ч в реальном отчёте). Применяется, только
            если после обрезки остаётся не меньше min_samples точек.
            0 — прежнее поведение.

    Returns:
        dict: {"leak": bool | None, "slope_mb_per_hour": float | None,
        "n_samples": int, "verdict": str}. leak=None означает «не удалось
        оценить» (мало точек или вырожденная выборка) — это НЕ «утечки нет»,
        вызывающий код должен различать эти два случая.
    """
    points = [(s["t"], s[key]) for s in samples if s.get(key) is not None]
    if points and warmup_frac > 0:
        t_first, t_last = points[0][0], points[-1][0]
        cut = t_first + (t_last - t_first) * warmup_frac
        trimmed = [pt for pt in points if pt[0] >= cut]
        # Обрезаем, только если точек хватает и после: иначе короткий ряд
        # лишился бы вердикта целиком, а не только переходного участка.
        if len(trimmed) >= min_samples:
            points = trimmed
            samples = [s for s in samples if s.get("t") is not None and s["t"] >= cut]
    if len(points) < min_samples:
        return {"leak": None, "slope_mb_per_hour": None, "n_samples": len(points),
                "verdict": f"недостаточно замеров для оценки ({len(points)} < {min_samples})"}

    t0 = points[0][0]
    hours_series = [((t - t0) / 3600.0, v) for t, v in points]
    slope = _linear_slope(hours_series)
    if slope is None:
        return {"leak": None, "slope_mb_per_hour": None, "n_samples": len(points),
                "verdict": "наклон не определён (все замеры на одном времени)"}

    doc_counts = [s["doc_count"] for s in samples if s.get("doc_count") is not None]
    if doc_counts:
        baseline = doc_counts[0]
        tolerance = max(1.0, abs(baseline) * doc_stable_tolerance_frac)
        stable_docs = (max(doc_counts) - min(doc_counts)) <= tolerance
    else:
        stable_docs = True  # doc_count не собирался (нет CDP) — не блокируем вердикт
    over_threshold = slope > threshold_mb_per_hour
    slope_r = round(slope, 3)

    if over_threshold and stable_docs:
        verdict = (f"ЕСТЬ УТЕЧКА: {slope_r:.2f} МБ/час при стабильном "
                  f"числе документов ({len(points)} замеров)")
        leak = True
    elif over_threshold and not stable_docs:
        verdict = (f"наклон {slope_r:.2f} МБ/час выше порога, но число "
                  f"документов менялось за период выборки — не утечка, "
                  f"а рост данных")
        leak = False
    else:
        verdict = f"утечки не обнаружено (наклон {slope_r:.2f} МБ/час)"
        leak = False

    return {"leak": leak, "slope_mb_per_hour": slope_r, "n_samples": len(points),
            "verdict": verdict}


def _normal_cdf(z):
    """Функция распределения стандартного нормального закона Φ(z).

    math.erf — часть стандартной библиотеки с Python 3.2, точное (не
    приближённое) вычисление; отдельной зависимости не требует.
    """
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _mann_whitney_u(x, y):
    """Критерий Манна-Уитни (Уилкоксона для двух независимых выборок) —
    ручная реализация без scipy: нормальное приближение с поправкой на
    связи (ties) и непрерывность.

    ПОЧЕМУ БЕЗ SCIPY: scipy.stats.mannwhitneyu с версии 1.7 по умолчанию
    считает ТОЧНЫЙ p-value для маленьких выборок без связей (см.
    документацию scipy — проверено context7 при разработке этой функции).
    Здесь выборки как раз маленькие (5-10 прогонов на версию) — точный
    расчёт был бы честнее нормального приближения. Но добавлять scipy как
    зависимость означает тянуть за собой numpy — десятки МБ веса для
    десктопного инструмента, который сейчас распространяется как компактный
    portable-архив (см. requirements.txt — там только лёгкие пакеты). При
    N=5..10 и одном сравнении на пару версий разница между точным расчётом
    и нормальным приближением с поправкой на непрерывность для практических
    выводов ("регрессия"/"без изменений") пренебрежимо мала — реализация
    ниже проверена в тестах против брутфорс-перебора всех перестановок
    (единственного надёжного способа получить точный p-value без scipy) на
    малых N и даёт близкий результат.

    Args:
        x, y: Две независимые выборки чисел (settle_ms/api_ms одной
            операции на двух версиях Р7). Порядок важен для знака
            результата (используется compare_runs при интерпретации), но
            не для самого p-value — тест двусторонний.

    Returns:
        tuple[float, float]: (U1, p_two_sided). U1 — статистика для x
        относительно y. p_two_sided — 1.0 в вырожденном случае (обе выборки
        совпадают полностью, разброса нет — заведомо «нет оснований для
        вывода о различии»), не 0.0/NaN.
    """
    n1, n2 = len(x), len(y)
    n = n1 + n2
    combined = sorted([(v, 0) for v in x] + [(v, 1) for v in y], key=lambda p: p[0])

    # Ранги 1..n, усреднённые внутри групп связей (одинаковых значений).
    ranks = [0.0] * n
    tie_sizes = []
    i = 0
    while i < n:
        j = i
        while j < n and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0  # ранги i+1..j (1-based), их среднее
        for k in range(i, j):
            ranks[k] = avg_rank
        tie_sizes.append(j - i)
        i = j

    rank_sum_x = sum(r for r, (_, grp) in zip(ranks, combined) if grp == 0)
    u1 = rank_sum_x - n1 * (n1 + 1) / 2.0

    mean_u = n1 * n2 / 2.0
    tie_correction = sum(t ** 3 - t for t in tie_sizes)
    # n*(n-1) == 0 невозможно при n1,n2 >= 1 (n >= 2) — знаменатель безопасен.
    var_u = (n1 * n2 / 12.0) * ((n + 1) - tie_correction / (n * (n - 1)))
    if var_u <= 0:
        return u1, 1.0  # все значения совпадают — различать нечего

    sigma_u = math.sqrt(var_u)
    diff = u1 - mean_u
    continuity = 0.5 if diff > 0 else (-0.5 if diff < 0 else 0.0)
    z = (diff - continuity) / sigma_u
    p = 2.0 * (1.0 - _normal_cdf(abs(z)))
    return u1, min(1.0, max(0.0, p))



# ── Описательные оценки: робастный CV, сдвиг Ходжеса-Лемана ─────────────

MAD_TO_SIGMA = 1.4826  # MAD × 1.4826 — оценка σ нормального распределения


def robust_cv_pct(values):
    """Робастный коэффициент вариации, %: MAD × 1.4826 / медиана × 100.

    MAD вместо стандартного отклонения — один выброс (сборка мусора, диск)
    не раздувает шум стенда. None — меньше двух значений или медиана ≤ 0.
    """
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    med = statistics.median(vals)
    if med <= 0:
        return None
    mad = statistics.median(abs(v - med) for v in vals)
    return mad * MAD_TO_SIGMA / med * 100.0


def hodges_lehmann_shift(base_times, new_times):
    """Сдвиг Ходжеса-Лемана: медиана всех попарных разностей new − base.

    Оценка сдвига, согласованная с критерием Манна-Уитни: устойчива к
    выбросам и точнее разности медиан на малых выборках. None — одна из
    выборок пустая.
    """
    if not base_times or not new_times:
        return None
    return statistics.median(n - b for b in base_times for n in new_times)


# ── Точный перестановочный p (Манн-Уитни) ───────────────────────────────

EXACT_P_MAX_N = 8  # n ≤ 8 на каждую сторону — C(16, 8) = 12870 перестановок,
                   # перебор занимает миллисекунды


def _midranks(values):
    """Ранги 1..n со средним рангом внутри связей, в порядке values."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        for k in range(i, j):
            ranks[order[k]] = (i + 1 + j) / 2.0
        i = j
    return ranks


def exact_mann_whitney_p(x, y):
    """Точный двусторонний p критерия Манна-Уитни полным перебором.

    Перебирает все C(n1+n2, n1) способов отдать n1 рангов (средних внутри
    связей) первой выборке и считает долю разметок, у которых U отстоит от
    среднего n1·n2/2 не меньше наблюдаемого. Без нормального приближения:
    на 5–8 повторах оно ошибается заметнее всего.

    Returns:
        float | None: p ∈ (0, 1]; None — выборка пустая или больше
        EXACT_P_MAX_N (перебор слишком долгий, нужна аппроксимация).
    """
    n1, n2 = len(x), len(y)
    if not n1 or not n2 or n1 > EXACT_P_MAX_N or n2 > EXACT_P_MAX_N:
        return None
    ranks = _midranks(list(x) + list(y))
    offset = n1 * (n1 + 1) / 2.0
    mean_u = n1 * n2 / 2.0
    observed = abs(sum(ranks[:n1]) - offset - mean_u)
    eps = 1e-9   # средние ранги дают полуцелые суммы — сравнение с допуском
    total = extreme = 0
    for combo in itertools.combinations(ranks, n1):
        total += 1
        if abs(sum(combo) - offset - mean_u) >= observed - eps:
            extreme += 1
    return extreme / total


# ── Bootstrap-интервал отношения медиан ──────────────────────────────────

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261007   # фиксированное зерно: один и тот же отчёт даёт
                            # один и тот же интервал при каждой сборке страницы
CI_LEVEL = 0.95


def _median_sorted(vals):
    vals = sorted(vals)
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _percentile(sorted_vals, q):
    """Перцентиль с линейной интерполяцией (как numpy по умолчанию)."""
    pos = (len(sorted_vals) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def bootstrap_ratio_ci(base_times, new_times, n_boot=BOOTSTRAP_RESAMPLES,
                       level=CI_LEVEL, seed=BOOTSTRAP_SEED):
    """Перцентильный bootstrap-интервал изменения медианы, %.

    Каждая выборка пересобирается с возвращением независимо, n_boot раз
    считается median(new*) / median(base*) − 1. Генератор — свой
    random.Random(seed), глобальное состояние random не трогается.

    Returns:
        tuple[float, float] | None: (нижняя, верхняя) граница в процентах;
        None — выборка пустая или все пересборки дали нулевую базу.
    """
    if not base_times or not new_times:
        return None
    rng = random.Random(seed)
    base, new = list(base_times), list(new_times)
    nb, nn = len(base), len(new)
    ratios = []
    for _ in range(n_boot):
        mb = _median_sorted(rng.choices(base, k=nb))
        mn = _median_sorted(rng.choices(new, k=nn))
        if mb > 0:
            ratios.append((mn / mb - 1.0) * 100.0)
    if not ratios:
        return None
    ratios.sort()
    tail = (1.0 - level) / 2.0
    return _percentile(ratios, tail), _percentile(ratios, 1.0 - tail)


# ── Поправка на множественные сравнения ──────────────────────────────────

def benjamini_hochberg(p_values):
    """Скорректированные p по Бенджамини-Хохбергу (контроль доли ложных
    открытий, FDR), в исходном порядке.

    p_(i) · m / i, затем накопленный минимум с конца и обрезка до 1 —
    так скорректированные p монотонны по рангу. None в списке пропускается
    и не входит в m (операции без вердикта не увеличивают семью).
    """
    indexed = [(p, i) for i, p in enumerate(p_values) if p is not None]
    m = len(indexed)
    out = [None] * len(p_values)
    if not m:
        return out
    indexed.sort()
    running = 1.0
    for rank in range(m, 0, -1):
        p, i = indexed[rank - 1]
        running = min(running, p * m / rank)
        out[i] = min(1.0, running)
    return out


# ── Минимальный обнаружимый эффект ───────────────────────────────────────

MEDIAN_SE_FACTOR = 1.2533   # √(π/2): ошибка медианы против ошибки среднего
Z_ALPHA_TWO_SIDED = 1.96    # α = 0,05, двусторонний
Z_POWER = 0.8416            # мощность 80 %


def min_detectable_effect_pct(cv_pct, n_base, n_new, threshold_pct=0.0):
    """Какой сдвиг медианы вердикт поймает с вероятностью 80 %, %.

    Ошибка разности медиан в процентах ≈ 1,2533 · CV · √(1/n1 + 1/n2).
    Вердикт требует, чтобы весь 95 %-интервал ушёл за порог, поэтому
    MDE = порог + (1,96 + 0,84) · ошибка. Нормальное приближение: на 5–8
    повторах цифра ориентир, а не гарантия.

    Returns:
        float | None: MDE в процентах; None — CV неизвестен или повторов нет.
    """
    if cv_pct is None or not n_base or not n_new:
        return None
    se = MEDIAN_SE_FACTOR * cv_pct * math.sqrt(1.0 / n_base + 1.0 / n_new)
    return (threshold_pct or 0.0) + (Z_ALPHA_TWO_SIDED + Z_POWER) * se


# ── Вердикт сравнения ────────────────────────────────────────────────────

MIN_RUNS_FOR_COMPARISON = 5  # минимум прогонов на КАЖДУЮ версию — меньше
                             # критерий Манна-Уитни статистически ненадёжен
COMPARISON_MIN_EFFECT_PCT = 10.0  # порог без профиля шума стенда: значимый,
                                  # но <10% сдвиг — шум даже на починенной
                                  # метрике (см. отчёт по нагрузочному
                                  # тестированию про разброс api_ms)
COMPARISON_ALPHA = 0.05

REGRESSION, SPEEDUP, NO_CHANGE = "РЕГРЕССИЯ", "УСКОРЕНИЕ", "без изменений"
EQUIVALENT, UNDETERMINED = "эквивалентно", "не определено"
INTERVAL_REGRESSION, INTERVAL_SPEEDUP = "регрессия", "ускорение"


def interval_verdict(ci_low, ci_high, threshold_pct):
    """Вердикт по интервалу против порога ±threshold_pct.

    «регрессия» — весь интервал выше +порога, «ускорение» — весь ниже
    −порога, «эквивалентно» — весь внутри ±порога, иначе «не определено»
    (интервал пересекает порог: повторов не хватило, чтобы решить).
    """
    if ci_low is None or ci_high is None or threshold_pct is None:
        return UNDETERMINED
    if ci_low > threshold_pct:
        return INTERVAL_REGRESSION
    if ci_high < -threshold_pct:
        return INTERVAL_SPEEDUP
    if -threshold_pct <= ci_low and ci_high <= threshold_pct:
        return EQUIVALENT
    return UNDETERMINED


def decide(ci_low, ci_high, threshold_pct, p_value, alpha=COMPARISON_ALPHA):
    """Итоговое решение: РЕГРЕССИЯ / УСКОРЕНИЕ / эквивалентно / не определено.

    Сдвиг за порог объявляется, только если интервал целиком за порогом И
    p (скорректированный, если сравнений несколько) меньше alpha. Интервал
    за порогом при большом p — «не определено»: на 5–8 повторах bootstrap
    бывает уже, чем есть на деле, критерий страхует от этого.
    """
    iv = interval_verdict(ci_low, ci_high, threshold_pct)
    significant = p_value is not None and p_value < alpha
    if iv == INTERVAL_REGRESSION:
        return REGRESSION if significant else UNDETERMINED
    if iv == INTERVAL_SPEEDUP:
        return SPEEDUP if significant else UNDETERMINED
    return iv


def _legacy_verdict(decision):
    """Старое поле verdict: эквивалентно и не определено → «без изменений»."""
    return decision if decision in (REGRESSION, SPEEDUP) else NO_CHANGE


_EMPTY_CI_KEYS = {"decision": None, "interval_verdict": None, "threshold_pct": None,
                  "ci_low_pct": None, "ci_high_pct": None, "ci_level": CI_LEVEL,
                  "hl_shift": None, "hl_shift_pct": None, "p_raw": None,
                  "p_exact": False, "p_adjusted": None, "family_size": None,
                  "cv_pct": None, "mde_pct": None}


def compare_runs(base_times, new_times,
                 min_effect_pct=COMPARISON_MIN_EFFECT_PCT,
                 alpha=COMPARISON_ALPHA,
                 min_runs=MIN_RUNS_FOR_COMPARISON,
                 threshold_pct=None,
                 noise_cv_pct=None,
                 n_boot=BOOTSTRAP_RESAMPLES,
                 seed=BOOTSTRAP_SEED):
    """Сравнивает длительности одной операции на двух версиях Р7 и выносит
    вердикт: регрессия, ускорение или без изменений.

    Два режима:
      * threshold_pct=None (по умолчанию, прежнее поведение) — регрессия или
        ускорение объявляются при p Манна-Уитни (нормальное приближение) <
        alpha И |сдвиг медиан| > min_effect_pct. Так работают пакет улик,
        сценарии и все старые вызовы.
      * threshold_pct задан (порог теста из профиля шума или по умолчанию) —
        вердикт по 95 %-интервалу против ±threshold_pct и p (точный при
        n ≤ 8), см. decide. Поле verdict тогда — РЕГРЕССИЯ / УСКОРЕНИЕ /
        «без изменений», а decision различает «эквивалентно» и «не определено».
    Поправка на число операций — adjust_family поверх результатов.

    Args:
        base_times, new_times: Списки длительностей одной операции — старая
            и новая версия Р7 соответственно. Порядок значим для знака
            effect_pct и для того, что считается "регрессией" (новая версия
            медленнее) против "ускорения" (новая версия быстрее).
        min_effect_pct: Порог практической значимости прежнего режима, %.
        alpha: Порог статистической значимости.
        min_runs: Минимум прогонов на каждую версию.
        threshold_pct: Порог теста для вердикта по интервалу, %.
        noise_cv_pct: CV теста из профиля шума — для MDE; None — по самим
            выборкам (больший из двух робастных CV).
        n_boot, seed: Число пересборок и зерно bootstrap.

    Returns:
        dict: прежние ключи {"verdict", "median_base", "median_new",
        "effect_pct", "p_value" (нормальное приближение), "n_base", "n_new"}
        и новые: decision, interval_verdict, threshold_pct, ci_low_pct,
        ci_high_pct, ci_level, hl_shift (с), hl_shift_pct, p_raw (точный
        при n ≤ 8, иначе приближение), p_exact (bool), p_adjusted и
        family_size (заполняет adjust_family), cv_pct, mde_pct.
        verdict «недостаточно прогонов» / «нет данных» (медиана базы ≤ 0) —
        новые ключи None.
    """
    n_base, n_new = len(base_times), len(new_times)
    if n_base < min_runs or n_new < min_runs:
        return {"verdict": "недостаточно прогонов", "median_base": None,
                "median_new": None, "effect_pct": None, "p_value": None,
                "n_base": n_base, "n_new": n_new, **_EMPTY_CI_KEYS}

    median_base = statistics.median(base_times)
    median_new = statistics.median(new_times)
    if median_base <= 0:
        # Нулевая база — не «без изменений», а сравнивать не с чем.
        return {"verdict": "нет данных", "median_base": median_base,
                "median_new": median_new, "effect_pct": None, "p_value": None,
                "n_base": n_base, "n_new": n_new, **_EMPTY_CI_KEYS}
    effect_pct = ((median_new - median_base) / median_base) * 100.0

    _, p_value = _mann_whitney_u(base_times, new_times)
    p_exact = exact_mann_whitney_p(base_times, new_times)
    p_raw = p_exact if p_exact is not None else p_value

    ci = bootstrap_ratio_ci(base_times, new_times, n_boot=n_boot, seed=seed)
    ci_low, ci_high = (None, None) if ci is None else ci
    hl = hodges_lehmann_shift(base_times, new_times)
    if noise_cv_pct is not None:
        cv = noise_cv_pct
    else:
        cvs = [c for c in (robust_cv_pct(base_times), robust_cv_pct(new_times)) if c is not None]
        cv = max(cvs) if cvs else None
    legacy_mode = threshold_pct is None
    thr = min_effect_pct if legacy_mode else threshold_pct
    decision = decide(ci_low, ci_high, thr, p_raw, alpha)

    if legacy_mode:
        significant = p_value < alpha
        if significant and effect_pct > min_effect_pct:
            verdict = REGRESSION
        elif significant and effect_pct < -min_effect_pct:
            verdict = SPEEDUP
        else:
            verdict = NO_CHANGE
    else:
        verdict = _legacy_verdict(decision)

    mde = min_detectable_effect_pct(cv, n_base, n_new, thr)
    return {"verdict": verdict, "median_base": median_base, "median_new": median_new,
            "effect_pct": round(effect_pct, 1), "p_value": round(p_value, 4),
            "n_base": n_base, "n_new": n_new,
            "decision": decision,
            "interval_verdict": interval_verdict(ci_low, ci_high, thr),
            "threshold_pct": None if legacy_mode else threshold_pct,
            # Границы без округления: adjust_family пересчитывает вердикт по
            # ним же, округление до 0,1 могло бы перевернуть его у порога.
            "ci_low_pct": ci_low, "ci_high_pct": ci_high,
            "ci_level": CI_LEVEL,
            "hl_shift": hl, "hl_shift_pct": round(hl / median_base * 100.0, 1),
            "p_raw": p_raw, "p_exact": p_exact is not None,
            "p_adjusted": None, "family_size": None,
            "cv_pct": None if cv is None else round(cv, 2),
            "mde_pct": None if mde is None else round(mde, 1)}


def adjust_family(results, alpha=COMPARISON_ALPHA):
    """Поправка Бенджамини-Хохберга на семью сравнений и вердикт по ней.

    Семья — все результаты с p_raw (одна пара отчётов: ~17 операций).
    Возвращает новый dict тех же ключей с копиями результатов: p_adjusted и
    family_size заполнены; у результатов с threshold_pct decision и verdict
    пересчитаны по скорректированному p. Прежний режим (threshold_pct None)
    вердикт не меняет — только добавляет p_adjusted.

    Args:
        results: {ключ: результат compare_runs}.
    """
    keys = [k for k, r in results.items() if r.get("p_raw") is not None]
    adjusted = benjamini_hochberg([results[k]["p_raw"] for k in keys])
    by_key = dict(zip(keys, adjusted))
    out = {}
    for k, r in results.items():
        r = dict(r)
        if k in by_key:
            r["p_adjusted"] = by_key[k]
            r["family_size"] = len(keys)
            if r.get("threshold_pct") is not None:
                r["decision"] = decide(r["ci_low_pct"], r["ci_high_pct"],
                                       r["threshold_pct"], by_key[k], alpha)
                r["verdict"] = _legacy_verdict(r["decision"])
        out[k] = r
    return out
