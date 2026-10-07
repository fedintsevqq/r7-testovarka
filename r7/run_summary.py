"""Итоги прогона по записям операций: пики и средние RAM/CPU, вердикт утечки.

Было двумя копиями — в прогоне вкладки и в Batch (этап «до 10»: фазы
прогона тестируются без Р7). Ключи словаря — те же, что в разделе
summary JSON-отчёта (schema 9), их читают сравнение, тренды и HTML.
"""
from r7.stats import detect_leak


def _vals(results, key):
    return [r[key] for r in results if isinstance(r, dict) and r.get(key) is not None]


def resource_summary(results):
    """Сводка ресурсов по операциям прогона.

    Returns:
        dict: peak_ram_mb, avg_ram_mb, min_ram_mb, peak_cpu_pct,
        peak_cpu_normalized_pct, avg_cpu_normalized_pct (None — данных нет)
        и ram_vals / cpu_vals — ряды для HTML-отчёта.
    """
    ram, cpu, cpu_norm = _vals(results, "ram"), _vals(results, "cpu"), _vals(results, "cpu_normalized")
    return {
        "peak_ram_mb": max(ram) if ram else None,
        "avg_ram_mb": round(sum(ram) / len(ram), 1) if ram else None,
        "min_ram_mb": min(ram) if ram else None,
        "peak_cpu_pct": max(cpu) if cpu else None,
        "peak_cpu_normalized_pct": max(cpu_norm) if cpu_norm else None,
        "avg_cpu_normalized_pct": round(sum(cpu_norm) / len(cpu_norm), 1) if cpu_norm else None,
        "ram_vals": ram,
        "cpu_vals": cpu,
    }


def report_summary(summary, leak_detection=None):
    """Раздел summary для JSON-отчёта: сводка без рядов (+ вердикт утечки)."""
    out = {k: v for k, v in summary.items() if k not in ("ram_vals", "cpu_vals")}
    if leak_detection is not None:
        out["leak_detection"] = leak_detection
    return out


def run_leak_verdict(samples):
    """Вердикт утечки для прогона операций.

    В прогоне объём данных меняют сами операции (вставка массива, новые
    листы, откаты между повторами), и наклон RAM утечку не показывает:
    прежний вердикт «утечки не обнаружено (наклон −10011 МБ/ч)» вводил в
    заблуждение (аудит 29.09.2026, пункт 16). Наклон остаётся для справки,
    вердикт — только у soak-теста, где документ не меняется.
    """
    verdict = detect_leak(samples)
    if verdict.get("slope_mb_per_hour") is None:
        return verdict                      # мало замеров — короткий прогон, это ожидаемо
    return dict(verdict, leak=None, applicable=False,
                verdict=(f"не оценивается: операции прогона меняют объём данных (наклон "
                         f"{verdict['slope_mb_per_hour']:.1f} МБ/ч для справки); "
                         f"утечки ищет soak-тест"))
