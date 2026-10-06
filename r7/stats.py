"""Статистика замеров: критерий Манна-Уитни, вердикт сравнения версий,
детектор утечки памяти по ряду замеров ресурсов.

Чистые функции без зависимостей от Р7, окон и Tk; numpy и scipy не нужны.
"""
import math
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


MIN_RUNS_FOR_COMPARISON = 5  # минимум прогонов на КАЖДУЮ версию — меньше
                             # критерий Манна-Уитни статистически ненадёжен
COMPARISON_MIN_EFFECT_PCT = 10.0  # практическая величина эффекта: значимый,
                                  # но <10% сдвиг — шум даже на починенной
                                  # метрике (см. отчёт по нагрузочному
                                  # тестированию про разброс api_ms)
COMPARISON_ALPHA = 0.05


def compare_runs(base_times, new_times,
                 min_effect_pct=COMPARISON_MIN_EFFECT_PCT,
                 alpha=COMPARISON_ALPHA,
                 min_runs=MIN_RUNS_FOR_COMPARISON):
    """Сравнивает длительности одной операции на двух версиях Р7 и выносит
    вердикт: регрессия, ускорение или без изменений.

    Регрессия/ускорение объявляются, только если ОБА условия выполнены:
      1. Статистическая значимость (критерий Манна-Уитни, p < alpha) —
         разница не объясняется случайным разбросом между прогонами.
      2. Практическая значимость (|относительная разница медиан| >
         min_effect_pct) — на достаточно большом N даже 2%-й сдвиг станет
         "статистически значимым", хотя для реального решения он не имеет
         веса (см. отчёт по нагрузочному тестированию, раздел про
         автодетект регрессий).

    Args:
        base_times, new_times: Списки длительностей одной операции — старая
            и новая версия Р7 соответственно. Порядок значим для знака
            effect_pct и для того, что считается "регрессией" (новая версия
            медленнее) против "ускорения" (новая версия быстрее).
        min_effect_pct: Порог практической значимости, %.
        alpha: Порог статистической значимости.
        min_runs: Минимум прогонов на каждую версию.

    Returns:
        dict: {"verdict": "РЕГРЕССИЯ" | "УСКОРЕНИЕ" | "без изменений" |
        "недостаточно прогонов" | "нет данных" (медиана базы ≤ 0),
        "median_base": float | None,
        "median_new": float | None, "effect_pct": float | None,
        "p_value": float | None, "n_base": int, "n_new": int}.
    """
    n_base, n_new = len(base_times), len(new_times)
    if n_base < min_runs or n_new < min_runs:
        return {"verdict": "недостаточно прогонов", "median_base": None,
                "median_new": None, "effect_pct": None, "p_value": None,
                "n_base": n_base, "n_new": n_new}

    median_base = statistics.median(base_times)
    median_new = statistics.median(new_times)
    if median_base <= 0:
        # Нулевая база — не «без изменений», а сравнивать не с чем.
        return {"verdict": "нет данных", "median_base": median_base,
                "median_new": median_new, "effect_pct": None, "p_value": None,
                "n_base": n_base, "n_new": n_new}
    effect_pct = ((median_new - median_base) / median_base) * 100.0

    _, p_value = _mann_whitney_u(base_times, new_times)

    significant = p_value < alpha
    if significant and effect_pct > min_effect_pct:
        verdict = "РЕГРЕССИЯ"
    elif significant and effect_pct < -min_effect_pct:
        verdict = "УСКОРЕНИЕ"
    else:
        verdict = "без изменений"

    return {"verdict": verdict, "median_base": median_base, "median_new": median_new,
            "effect_pct": round(effect_pct, 1), "p_value": round(p_value, 4),
            "n_base": n_base, "n_new": n_new}
