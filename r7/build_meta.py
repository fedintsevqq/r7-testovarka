"""Метаданные сборки Р7 для отчёта: номер сборки, DesktopEditors.exe (дата,
размер, sha256), дистрибутив, ссылка на changelog (этап 2 плана, п. 1).

Без них регрессию не привязать к коду: строка «version» из реестра не
говорит, тот ли это exe, что лежит в папке, и из какого дистрибутива он
встал. Это метаданные, а не схема замера: цифры прогона от них не зависят,
поэтому MEASURE_SCHEMA_VERSION не поднимается (правило 11 CLAUDE.md), а
читатели отчётов переваривают файлы без ключа `build`.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

# (путь, mtime_ns, размер) → sha256. Exe весит ~50 МБ, хэш считается один
# раз за запуск программы и только вне измеряемых окон (см. вызовы).
_SHA_CACHE: dict[tuple[str, int, int], str] = {}

_READ_CHUNK = 1 << 20


def build_number(version: object) -> str | None:
    """Последний числовой компонент версии: «2026.3.2.3229» → «3229».

    Returns:
        str | None: None — в строке нет чисел или она пуста.
    """
    nums = re.findall(r"\d+", str(version or ""))
    return nums[-1] if nums else None


def exe_sha256(path: str | os.PathLike[str]) -> str | None:
    """sha256 файла с кэшем по пути, mtime и размеру.

    Returns:
        str | None: None — файла нет или он не читается (занят установщиком,
        нет прав). Никогда не бросает: это диагностическое поле отчёта.
    """
    try:
        p = Path(path)
        st = p.stat()
    except (OSError, TypeError, ValueError):
        return None
    key = (str(p), st.st_mtime_ns, st.st_size)
    cached = _SHA_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for block in iter(lambda: f.read(_READ_CHUNK), b""):
                digest.update(block)
    except OSError:
        return None
    _SHA_CACHE[key] = digest.hexdigest()
    return _SHA_CACHE[key]


def changelog_url(template: str | None, version: str | None,
                  build: str | None = None) -> str | None:
    """Ссылка на changelog по шаблону из настроек: «https://…/{version}»;
    поддерживаются поля {version} и {build}. Кривой шаблон — None, не ошибка."""
    if not template or not version:
        return None
    try:
        return str(template).format(version=version, build=build or "")
    except (KeyError, IndexError, ValueError):
        return None


def build_metadata(version_info: Mapping[str, Any] | None,
                   exe_path: str | os.PathLike[str] | None,
                   installer_file: str | None = None,
                   changelog_template: str | None = None) -> dict[str, Any]:
    """Объект `build` полного JSON-отчёта.

    Args:
        version_info: запись реестра (_read_current_version_from_registry)
            или None — тогда product/version/build_number пустые.
        exe_path: путь к DesktopEditors.exe или None; несуществующий файл
            даёт exe_mtime/exe_size/exe_sha256 = None, путь остаётся.
        installer_file: имя дистрибутива, если версию ставил сам инструмент
            в этой сессии (Batch), иначе None.
        changelog_template: шаблон ссылки из r7_settings.json.

    Returns:
        dict: product, version, build_number, installer_file, exe_path,
        exe_mtime (ISO), exe_size, exe_sha256, changelog_url.
    """
    info = version_info or {}
    version = (info.get("version") or "").strip() or None
    number = build_number(version)
    meta: dict[str, Any] = {
        "product": (info.get("name") or "").strip() or None,
        "version": version,
        "build_number": number,
        "installer_file": installer_file or None,
        "exe_path": str(exe_path) if exe_path else None,
        "exe_mtime": None,
        "exe_size": None,
        "exe_sha256": None,
        "changelog_url": changelog_url(changelog_template, version, number),
    }
    if exe_path:
        try:
            st = Path(exe_path).stat()
        except (OSError, TypeError, ValueError):
            return meta
        meta["exe_mtime"] = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
        meta["exe_size"] = st.st_size
        meta["exe_sha256"] = exe_sha256(exe_path)
    return meta


def build_summary(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Короткая форма для HTML из отчёта любой версии (ключа `build` может не
    быть — старые JSON): build_number, sha_short (12 символов), exe_date
    («30.09.2026»), installer_file, changelog_url. Все поля могут быть None."""
    b = (data or {}).get("build")
    b = b if isinstance(b, dict) else {}
    sha = b.get("exe_sha256")
    mtime = b.get("exe_mtime") or ""
    exe_date = None
    if len(mtime) >= 10:
        exe_date = f"{mtime[8:10]}.{mtime[5:7]}.{mtime[:4]}"
    return {
        "build_number": b.get("build_number"),
        "sha_short": sha[:12] if isinstance(sha, str) and sha else None,
        "exe_date": exe_date,
        "installer_file": b.get("installer_file"),
        "changelog_url": b.get("changelog_url"),
    }
