"""Тесты этапа 1 нагрузочного стенда: нормировка CPU на число ядер (M6),
медиана+MAD вместо среднего (H2), окружение прогона и версия схемы (M1).

Не тестируется здесь (нужен живой Р7 или Tk-окно): сама run_test_with_runs
(вложенная функция внутри _spreadsheet_worker) и живое поведение детекторов
простоя — для них есть tests/manual_cdp_smoke.py и ручная проверка вживую.
"""
import r7_Testovarka as r7mod


# ── модульные константы: версия схемы и число прогонов по умолчанию ──────

def test_default_test_runs_is_seven():
    """H2: N >= 7 для медианы/MAD на операциях короче разрешения детектора
    простоя. Диапазон UI (Spinbox 1..10) не менялся — 7 в него укладывается."""
    assert r7mod.DEFAULT_TEST_RUNS == 7


def test_measure_schema_version_is_two():
    assert r7mod.MEASURE_SCHEMA_VERSION == 2


def test_min_runs_for_stats():
    """Меньше 2 прогонов — отбрасывать первый (прогрев) уже нечем заменить."""
    assert r7mod.R7Testovarka.MIN_RUNS_FOR_STATS == 2


# ── _mad: Median Absolute Deviation ───────────────────────────────────────

def test_mad_of_identical_values_is_zero():
    assert r7mod.R7Testovarka._mad([1.0, 1.0, 1.0]) == 0.0


def test_mad_single_value_is_zero():
    assert r7mod.R7Testovarka._mad([2.5]) == 0.0


def test_mad_known_value_odd_length():
    # median([1,2,3,4,100]) = 3; отклонения |1-3|,|2-3|,|3-3|,|4-3|,|100-3| =
    # 2,1,0,1,97 -> median из них = 1
    assert r7mod.R7Testovarka._mad([1, 2, 3, 4, 100]) == 1


def test_mad_known_value_even_length():
    # median([1,2,3,4]) = 2.5; отклонения 1.5,0.5,0.5,1.5 -> median = 1.0
    assert r7mod.R7Testovarka._mad([1, 2, 3, 4]) == 1.0


def test_mad_is_deterministic_for_same_input():
    """_mad не принимает center отдельным параметром (упрощено после
    /simplify: единственный вызывающий код передавал не более 6 значений —
    пересчитать median() внутри дешевле, чем поддерживать вторую сигнатуру
    ради одного места). Медиана считается внутри всегда одинаково."""
    values = [1, 2, 3, 4, 100]
    assert r7mod.R7Testovarka._mad(values) == r7mod.R7Testovarka._mad(values)
    assert r7mod.R7Testovarka._mad(values) == 1


def test_mad_robust_to_single_outlier():
    """Ключевое свойство MAD, ради которого он и введён: один выброс почти
    не двигает MAD, в отличие от stdev/среднего."""
    stable = [1.0, 1.02, 0.98, 1.01, 0.99, 1.0, 1.03]
    with_outlier = stable[:-1] + [50.0]
    mad_stable = r7mod.R7Testovarka._mad(stable)
    mad_outlier = r7mod.R7Testovarka._mad(with_outlier)
    assert mad_outlier < mad_stable + 0.5  # выброс почти не сдвинул MAD
    avg_stable = sum(stable) / len(stable)
    avg_outlier = sum(with_outlier) / len(with_outlier)
    assert avg_outlier - avg_stable > 6  # а среднее — сдвинул сильно


# ── _cpu_count: кэш числа ядер ────────────────────────────────────────────

def test_cpu_count_caches_after_first_call(bare_r7, monkeypatch):
    calls = []

    def fake_cpu_count():
        calls.append(1)
        return 8

    monkeypatch.setattr(r7mod, "PSUTIL_OK", True)
    monkeypatch.setattr(r7mod.psutil, "cpu_count", fake_cpu_count)
    bare_r7._cached_cpu_count = None

    first = bare_r7._cpu_count()
    second = bare_r7._cpu_count()

    assert first == second == 8
    assert len(calls) == 1  # второй вызов взял значение из кэша


def test_cpu_count_falls_back_to_one_when_psutil_none(bare_r7, monkeypatch):
    """psutil.cpu_count() документированно может вернуть None (не смог
    определить число ядер) — деление на None иначе уронило бы вызывающий код."""
    monkeypatch.setattr(r7mod, "PSUTIL_OK", True)
    monkeypatch.setattr(r7mod.psutil, "cpu_count", lambda: None)
    bare_r7._cached_cpu_count = None

    assert bare_r7._cpu_count() == 1


def test_cpu_count_is_one_without_psutil(bare_r7, monkeypatch):
    monkeypatch.setattr(r7mod, "PSUTIL_OK", False)
    bare_r7._cached_cpu_count = None

    assert bare_r7._cpu_count() == 1


# ── _build_system_info: окружение прогона (M1) ────────────────────────────

def test_build_system_info_has_expected_keys(bare_r7, monkeypatch):
    monkeypatch.setattr(r7mod, "PSUTIL_OK", True)
    monkeypatch.setattr(r7mod.psutil, "cpu_count", lambda: 8)

    class _FakeVMem:
        total = 16 * 1024**3

    monkeypatch.setattr(r7mod.psutil, "virtual_memory", lambda: _FakeVMem())
    bare_r7._cached_cpu_count = None

    info = bare_r7._build_system_info()

    assert set(info) == {"os", "ram_total_gb", "cpu_model", "cpu_cores_logical"}
    assert info["cpu_cores_logical"] == 8
    assert info["ram_total_gb"] == 16.0


def test_build_system_info_ram_none_without_psutil(bare_r7, monkeypatch):
    monkeypatch.setattr(r7mod, "PSUTIL_OK", False)
    bare_r7._cached_cpu_count = None

    info = bare_r7._build_system_info()

    assert info["ram_total_gb"] is None
    assert info["cpu_cores_logical"] == 1


# ── _generate_comparison_html: предупреждение о смешанных measure_schema ──

def _dataset(path, version, schema=None):
    data = {"results": [], "system": {}, "summary": {}, "timestamp": ""}
    if schema is not None:
        data["measure_schema"] = schema
    return {"path": path, "version": version, "data": data}


def test_comparison_html_warns_on_mixed_schema_versions(bare_r7):
    """Altitude-находка: measure_schema писался в JSON, но ни один читатель
    не проверял его при сравнении версий — страница молча строила график по
    несопоставимым числам (среднее по сырым порогам CPU против медианы по
    нормированным). Баннер должен появиться, когда версии реально разные."""
    datasets = [_dataset("a.json", "2026.1", schema=1),
               _dataset("b.json", "2026.2", schema=2)]
    out = bare_r7._generate_comparison_html(datasets, "a.json")
    assert "смешаны файлы разных версий схемы замера" in out
    assert "1, 2" in out


def test_comparison_html_silent_when_schema_matches(bare_r7):
    datasets = [_dataset("a.json", "2026.1", schema=2),
               _dataset("b.json", "2026.2", schema=2)]
    out = bare_r7._generate_comparison_html(datasets, "a.json")
    assert "смешаны файлы разных версий" not in out


def test_comparison_html_missing_schema_field_treated_as_version_one(bare_r7):
    """Файл без measure_schema (сохранён до 25.08.2026) — версия 1 по
    умолчанию, а не None/крах."""
    datasets = [_dataset("a.json", "2026.1", schema=None),   # старый файл
               _dataset("b.json", "2026.2", schema=1)]        # явная v1
    out = bare_r7._generate_comparison_html(datasets, "a.json")
    assert "смешаны файлы разных версий" not in out   # обе де-факто v1


def test_comparison_html_missing_and_v2_triggers_warning(bare_r7):
    datasets = [_dataset("a.json", "2026.1", schema=None),   # старый файл -> v1
               _dataset("b.json", "2026.2", schema=2)]
    out = bare_r7._generate_comparison_html(datasets, "a.json")
    assert "смешаны файлы разных версий схемы замера" in out
