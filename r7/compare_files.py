"""Решения окна «Сравнить версии» без Tk (этап 4 плана, 07.10.2026):
какие отчёты есть, что из них выбрано, годится ли выбор для сравнения.

Окно только показывает список и отказы. Разбор файла отчёта был двумя
копиями — при поиске и в «Добавить файл»; теперь один read_report_meta.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from r7 import team_folder

REPORT_GLOB = "performance_full_*.json"

StrPath = str | os.PathLike[str]


def fmt_report_ts(ts_raw: str | None) -> str:
    """'20261006_224450' → '06.10.2026 22:44'; иное — как есть."""
    ts_raw = ts_raw or ""
    if len(ts_raw) >= 13:
        return f"{ts_raw[6:8]}.{ts_raw[4:6]}.{ts_raw[:4]} {ts_raw[9:11]}:{ts_raw[11:13]}"
    return ts_raw


def read_report_meta(path: StrPath, custom_names: Mapping[str, str] | None = None,
                     machine: str | None = None) -> dict[str, Any]:
    """Метаданные одного отчёта для списка. Исключение — файл не читается
    (вызывающий решает: пропустить при поиске или показать ошибку).

    Args:
        machine: имя подпапки машины в общей папке команды; у локального
            отчёта None. Входит в подпись по умолчанию («2026.3.2 · PC-7-ab12»),
            чтобы в списке и на странице сравнения было видно, чей это прогон.

    Returns:
        dict: path, key, version, ts, data, machine, display_name (подпись
        версии, переименованная пользователем, иначе версия из отчёта).
    """
    report_path = Path(path)
    with open(report_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("не отчёт performance_full: верхний уровень не объект")
    version = data.get("version") or report_path.stem
    key = str(report_path)
    default_name = f"{version} · {machine}" if machine else version
    return {"path": report_path, "key": key, "version": version,
            "ts": fmt_report_ts(data.get("timestamp", "")), "data": data,
            "machine": machine,
            "display_name": (custom_names or {}).get(key, default_name)}


def _unreadable_meta(jf: Path, custom_names: Mapping[str, str] | None,
                     machine: str | None = None) -> dict[str, Any]:
    key = str(jf)
    return {"path": jf, "key": key, "version": jf.stem, "ts": "", "data": None,
            "machine": machine, "display_name": (custom_names or {}).get(key, jf.stem)}


def scan_reports(folder: StrPath, custom_names: Mapping[str, str] | None = None,
                 team: StrPath | Literal[False] | None = None) -> list[dict[str, Any]]:
    """Отчёты в папке, новые сверху. Нечитаемый файл остаётся в списке с
    версией по имени файла и data=None — пользователь увидит его, а ошибку
    получит, только если выберет.

    Args:
        team: общая папка команды; None — из настроек (team_reports_folder),
            False — не читать. Её отчёты идут с меткой machine (подпапка);
            файл с тем же именем, что локальный, второй раз не берётся.
    """
    items: list[tuple[float, str | None, Path]] = [
        (p.stat().st_mtime, None, p) for p in Path(folder).glob(REPORT_GLOB)]
    seen = {p.name for _m, _machine, p in items}
    if team is None:
        team = team_folder.configured_folder()
    if team:
        for machine, p in team_folder.team_report_files(team):
            if p.name in seen:
                continue
            seen.add(p.name)
            try:
                items.append((p.stat().st_mtime, machine, p))
            except OSError:
                continue
    items.sort(key=lambda t: t[0], reverse=True)
    result: list[dict[str, Any]] = []
    for _mtime, owner, jf in items:
        try:
            result.append(read_report_meta(jf, custom_names, owner))
        except Exception:
            result.append(_unreadable_meta(jf, custom_names, owner))
    return result


def validate_comparison(selected_keys: Sequence[str], base_key: str | None,
                        max_files: int) -> tuple[str, str] | None:
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


def build_datasets(selected_keys: Iterable[str],
                   meta_by_key: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Наборы для страницы сравнения в порядке выбора. Не прочитанный при
    поиске файл читается сейчас; не читается — исключение с именем файла.

    Returns:
        list[dict]: {"path", "version" (подпись), "data"}.
    """
    datasets: list[dict[str, Any]] = []
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
