"""Отпечаток машины (этап 2 плана, п. 2): какие поля стенда входят в хэш,
как он считается и как два отчёта сравниваются по нему.

Отчёты с разных ПК команды сравнивать напрямую нельзя — другой процессор
или диск с данными Р7 меняют цифры сильнее, чем сборка Р7. Отпечаток
кладётся в `system.environment` полного JSON (`fingerprint`,
`fingerprint_hash`); сравнение и тренды предупреждают, когда хэши разные,
так же, как про разные схемы замера. Сбор значений — в
ResourcesMixin._capture_environment; здесь — чистые функции без psutil и Tk.
"""
import hashlib
import json
import os
import platform
import re

# Порядок — порядок в предупреждениях; хэш считается по отсортированным ключам.
FIELDS = ("cpu_model", "cpu_logical", "ram_gb", "os", "dpi_scale_pct",
          "r7_data_drive", "reports_drive", "power_plan")

FIELD_TITLES = {
    "cpu_model": "процессор",
    "cpu_logical": "число логических ядер",
    "ram_gb": "RAM",
    "os": "ОС",
    "dpi_scale_pct": "масштаб экрана",
    "r7_data_drive": "диск с данными Р7",
    "reports_drive": "диск отчётов",
    "power_plan": "план питания",
}

HASH_LEN = 12


def drive_of(path):
    """Буква диска пути: «C:\\Users\\…» → «C:»; None — пути нет или без диска."""
    if not path:
        return None
    drive = os.path.splitdrive(str(path))[0]
    return drive.upper() or None


def collect(cpu_model=None, cpu_logical=None, ram_gb=None, os_name=None,
            dpi_scale_pct=None, r7_data_drive=None, reports_drive=None, power_plan=None):
    """Словарь отпечатка в фиксированном порядке полей. RAM округляется до
    гигабайта: 15.9 и 16.0 ГБ — один и тот же стенд."""
    return {
        "cpu_model": cpu_model or None,
        "cpu_logical": int(cpu_logical) if cpu_logical else None,
        "ram_gb": int(round(ram_gb)) if isinstance(ram_gb, (int, float)) else None,
        "os": os_name or None,
        "dpi_scale_pct": int(dpi_scale_pct) if dpi_scale_pct else None,
        "r7_data_drive": r7_data_drive or None,
        "reports_drive": reports_drive or None,
        "power_plan": power_plan or None,
    }


def fingerprint_hash(fp):
    """Первые 12 hex sha256 от канонического JSON словаря (ключи
    отсортированы, без пробелов) — одинаковые значения дают одинаковый хэш
    независимо от порядка сборки."""
    canonical = json.dumps(fp or {}, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:HASH_LEN]


def diff_fields(a, b):
    """Поля, которыми два отпечатка различаются, в порядке FIELDS (плюс
    неизвестные ключи в конце)."""
    a, b = a or {}, b or {}
    keys = list(FIELDS) + sorted((set(a) | set(b)) - set(FIELDS))
    return [k for k in keys if a.get(k) != b.get(k)]


def describe_fields(fields):
    """«процессор, RAM, диск с данными Р7» — для текста предупреждения."""
    return ", ".join(FIELD_TITLES.get(f, f) for f in fields)


def report_fingerprint(data):
    """(hash, dict) из полного JSON-отчёта; (None, None) — отчёт старой версии
    или без окружения. Хэш, если его нет в файле, пересчитывается из
    словаря — так старые и новые читатели сходятся на одном значении."""
    env = ((data or {}).get("system") or {}).get("environment") or {}
    if not isinstance(env, dict):
        return None, None
    fp = env.get("fingerprint")
    if not isinstance(fp, dict):
        return None, None
    h = env.get("fingerprint_hash") or fingerprint_hash(fp)
    return h, fp


MISMATCH_TEXT = ("Отчёты сняты на разных машинах — отличаются: {fields}. Разница во "
                 "времени может идти от стенда, а не от Р7; сравнивать можно только "
                 "направление изменений, не цифры.")


def mismatch_warning(fingerprints):
    """Текст предупреждения, если среди отпечатков есть хотя бы два разных
    хэша; None — все одинаковые или известен только один.

    Args:
        fingerprints: список (hash, dict); записи с hash None пропускаются
            (старые отчёты без отпечатка не считаются «другой машиной»).
    """
    known = [(h, fp or {}) for h, fp in fingerprints if h]
    hashes = {h for h, _fp in known}
    if len(hashes) <= 1:
        return None
    fields = []
    first = known[0][1]
    for _h, fp in known[1:]:
        for f in diff_fields(first, fp):
            if f not in fields:
                fields.append(f)
    text = describe_fields(fields) if fields else "состав отпечатка"
    return MISMATCH_TEXT.format(fields=text)


def hostname():
    """Имя ПК для подпапки в общей папке команды; «pc» — если не узнать."""
    try:
        return platform.node() or "pc"
    except Exception:
        return "pc"


def machine_dir_name(fp_hash, host=None):
    """Имя подпапки машины в общей папке: «<hostname>-<hash>», символы вне
    [A-Za-z0-9._-] заменены на «_»."""
    host = re.sub(r"[^A-Za-z0-9._-]+", "_", host or hostname()).strip("_") or "pc"
    return f"{host}-{fp_hash or 'nofp'}"
