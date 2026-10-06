"""Решения окна «Сравнить версии» без Tk (этап 4 плана, 07.10.2026):
какие отчёты есть, что из них выбрано, годится ли выбор для сравнения.

Окно только показывает список и отказы. Разбор файла отчёта был двумя
копиями — при поиске и в «Добавить файл»; теперь один read_report_meta.
"""
import json
from pathlib import Path

REPORT_GLOB = "performance_full_*.json"


def fmt_report_ts(ts_raw):
    """'20261006_224450' → '06.10.2026 22:44'; иное — как есть."""
    ts_raw = ts_raw or ""
    if len(ts_raw) >= 13:
        return f"{ts_raw[6:8]}.{ts_raw[4:6]}.{ts_raw[:4]} {ts_raw[9:11]}:{ts_raw[11:13]}"
    return ts_raw


def read_report_meta(path, custom_names=None):
    """Метаданные одного отчёта для списка. Исключение — файл не читается
    (вызывающий решает: пропустить при поиске или показать ошибку).

    Returns:
        dict: path, key, version, ts, data, display_name (подпись версии,
        переименованная пользователем, иначе версия из отчёта).
    """
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("не отчёт performance_full: верхний уровень не объект")
    version = data.get("version") or path.stem
    key = str(path)
    return {"path": path, "key": key, "version": version,
            "ts": fmt_report_ts(data.get("timestamp", "")), "data": data,
            "display_name": (custom_names or {}).get(key, version)}


def scan_reports(folder, custom_names=None):
    """Отчёты в папке, новые сверху. Нечитаемый файл остаётся в списке с
    версией по имени файла и data=None — пользователь увидит его, а ошибку
    получит, только если выберет."""
    files = sorted(Path(folder).glob(REPORT_GLOB), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    result = []
    for jf in files:
        try:
            result.append(read_report_meta(jf, custom_names))
        except Exception:
            key = str(jf)
            result.append({"path": jf, "key": key, "version": jf.stem, "ts": "", "data": None,
                           "display_name": (custom_names or {}).get(key, jf.stem)})
    return result


def validate_comparison(selected_keys, base_key, max_files):
    """Отказ (заголовок, текст) или None, если выбор годится для сравнения."""
    if len(selected_keys) < 2:
        return ("Мало файлов", "Выберите минимум 2 файла.")
    if len(selected_keys) > max_files:
        return ("Много файлов", f"Выберите не более {max_files} файлов.")
    if not base_key:
        return ("Базовая версия", "Выберите базовую версию.")
    if base_key not in selected_keys:
        return ("Базовая версия", "Базовая версия должна быть среди выбранных файлов.")
    return None


def build_datasets(selected_keys, meta_by_key):
    """Наборы для страницы сравнения в порядке выбора. Не прочитанный при
    поиске файл читается сейчас; не читается — исключение с именем файла.

    Returns:
        list[dict]: {"path", "version" (подпись), "data"}.
    """
    datasets = []
    for k in selected_keys:
        m = meta_by_key[k]
        data = m.get("data")
        if data is None:
            try:
                data = read_report_meta(m["path"])["data"]
            except Exception as e:
                raise ValueError(f"Не удалось загрузить {Path(m['path']).name}:\n{e}") from e
        datasets.append({"path": str(m["path"]),
                         "version": m.get("display_name", m["version"]), "data": data})
    return datasets
