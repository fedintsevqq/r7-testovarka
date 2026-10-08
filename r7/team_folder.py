"""Общая папка отчётов команды (этап 2 плана, п. 3): копия каждого полного
JSON (и HTML) в подпапку своей машины, чтение чужих отчётов для трендов и
сравнения.

Папка задаётся ключом team_reports_folder в r7_settings.json (сетевой диск
или облачная папка). Подпапка машины — «<hostname>-<fingerprint_hash>»
(r7.fingerprint.machine_dir_name): отчёты одного ПК лежат вместе, а по
хэшу видно, тот ли это стенд. Недоступная папка — одна строка в журнал,
прогон от неё не зависит.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

from r7 import settings

REPORT_GLOB = "performance_full_*.json"

PathLike = str | os.PathLike[str]


def configured_folder() -> Path | None:
    """Path общей папки из настроек или None, если не задана."""
    raw = settings.get("team_reports_folder")
    if not raw or not str(raw).strip():
        return None
    return Path(str(raw).strip().strip('"'))


def is_reachable(folder: PathLike | None) -> bool:
    """Папка существует и это каталог (сетевой диск может быть отключён)."""
    if not folder:
        return False
    try:
        return Path(folder).is_dir()
    except OSError:
        return False


def copy_reports(folder: PathLike | None, machine_dir: str, paths: Iterable[PathLike | None],
                 log_cb: Callable[[str], object] | None = None) -> Path | None:
    """Копирует файлы в folder/machine_dir. Возвращает папку назначения или
    None; отказ (нет папки, нет прав, диск отвалился) только пишется в журнал."""
    log_cb = log_cb or (lambda msg: None)
    if not is_reachable(folder):
        log_cb(f"⚠️ Общая папка команды недоступна: {folder} — отчёт остался только локально")
        return None
    dest = Path(folder or "") / machine_dir
    try:
        dest.mkdir(parents=True, exist_ok=True)
        copied = []
        for p in paths:
            if p is None:
                continue
            p = Path(p)
            if not p.is_file():
                continue
            shutil.copy2(p, dest / p.name)
            copied.append(p.name)
    except Exception as e:  # сетевой диск, права, место — прогон не ронять
        log_cb(f"⚠️ Отчёт не скопирован в общую папку {dest} ({type(e).__name__}: {e})")
        return None
    if copied:
        log_cb(f"📤 Скопировано в общую папку команды: {', '.join(copied)} → {dest}")
    return dest


def team_report_files(folder: PathLike | None) -> list[tuple[str, Path]]:
    """[(machine, Path)] для всех performance_full_*.json в общей папке по
    времени изменения. machine — имя первой подпапки под folder; файл в
    корне общей папки получает machine «team»."""
    if not is_reachable(folder):
        return []
    root = Path(folder or "")
    found: list[tuple[float, str, Path]] = []
    try:
        for fp in root.rglob(REPORT_GLOB):
            try:
                rel = fp.relative_to(root)
                machine = rel.parts[0] if len(rel.parts) > 1 else "team"
                found.append((fp.stat().st_mtime, machine, fp))
            except OSError:  # файл исчез между обходом и stat — пропускаем
                continue
    except OSError:
        return []
    found.sort(key=lambda t: t[0])
    return [(machine, fp) for _m, machine, fp in found]
