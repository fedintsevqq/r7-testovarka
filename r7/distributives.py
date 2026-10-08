"""Дистрибутивы Р7: откуда берутся, как ищутся и чему доверяем перед установкой.

Папки с дистрибутивами — `Distributives/` рядом с программой плюс папки из
настройки `distributives_dirs` (локальные или сетевая папка команды). Файлы
не копируются: установщик весит 430–630 МБ, программа запоминает папку.
По всему компьютеру ничего не ищется: обход дисков долгий и непредсказуемый,
а найденное ставится от администратора. Есть только поиск по кнопке — в
«Загрузках» и на «Рабочем столе», на два уровня вглубь (find_installers).

Перед установкой файл проверяется по цифровой подписи (r7.windows.
authenticode_signature): подпись должна быть действительна и принадлежать
издателю из TRUSTED_SIGNERS. Проверка идёт только перед установкой выбранного
файла, а не при показе списка: Windows читает весь файл, и 30 дистрибутивов
по 600 МБ при каждом обновлении списка забили бы файловый кэш — на слабом
стенде это портит следующий замер открытия. Все 33 дистрибутива на стенде
(2025.4–2026.3, .exe и .msi) подписаны «AO R7» (проверено 08.10.2026).
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from r7 import settings

DISTRIBUTIVE_PATTERNS = ("*.msi", "*.exe")
# Издатели, чьи установщики разрешено ставить: подстрока CN сертификата.
TRUSTED_SIGNERS = ("AO R7",)
# Поиск по кнопке: только эти папки и не глубже SEARCH_DEPTH подпапок.
SEARCH_DEPTH = 2
SEARCH_NAME_PREFIXES = ("r7-office", "r7office", "r7_office")

SignatureFn = Callable[[str | os.PathLike[str]], tuple[str, str | None]]


def extra_dirs(data: dict[str, object] | None = None) -> list[Path]:
    """Папки из настройки `distributives_dirs` (список строк; мусор пропускается)."""
    raw = (data if data is not None else settings.load_settings()).get("distributives_dirs")
    if not isinstance(raw, list):
        return []
    out: list[Path] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            p = Path(os.path.expandvars(item.strip()))
            if p not in out:
                out.append(p)
    return out


def all_dirs(primary: str | os.PathLike[str], data: dict[str, object] | None = None) -> list[Path]:
    """Папка Distributives плюс папки из настроек, без повторов."""
    dirs = [Path(primary)]
    for p in extra_dirs(data):
        if p not in dirs:
            dirs.append(p)
    return dirs


def add_dir(folder: str | os.PathLike[str]) -> bool:
    """Добавляет папку в `distributives_dirs` и сохраняет настройки.
    False — уже была или не записалось."""
    data = settings.load_settings()
    current = extra_dirs(data)
    p = Path(folder)
    if p in current:
        return False
    data["distributives_dirs"] = [str(x) for x in current] + [str(p)]
    return settings.save_settings(data)


def list_files(dirs: Iterable[str | os.PathLike[str]]) -> list[Path]:
    """Установщики (.msi/.exe) из всех папок; недоступная папка пропускается,
    один и тот же файл (по абсолютному пути) — один раз."""
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        folder = Path(d)
        try:
            if not folder.is_dir():
                continue
            files = [f for pat in DISTRIBUTIVE_PATTERNS for f in folder.glob(pat)]
        except OSError:
            continue
        for f in files:
            key = os.path.normcase(str(f.resolve())) if f.exists() else str(f)
            if key not in seen:
                seen.add(key)
                out.append(f)
    return out


def looks_like_installer(name: str) -> bool:
    low = name.lower()
    return low.endswith((".exe", ".msi")) and low.startswith(SEARCH_NAME_PREFIXES)


def default_search_roots() -> list[Path]:
    """«Загрузки» (обе локали) и «Рабочий стол» пользователя."""
    home = Path.home()
    roots = [home / "Downloads", home / "Загрузки", home / "Desktop", home / "Рабочий стол"]
    return [r for r in roots if r.is_dir()]


def find_installers(roots: Sequence[str | os.PathLike[str]] | None = None,
                    depth: int = SEARCH_DEPTH,
                    should_stop: Callable[[], bool] | None = None) -> list[Path]:
    """Папки, где лежат похожие на дистрибутив Р7 файлы (по имени).

    Обход — os.scandir, не глубже depth подпапок, без символических ссылок;
    ошибок доступа не бросает. should_stop — отмена из интерфейса.
    """
    found: list[Path] = []
    stack = [(Path(r), 0) for r in (roots if roots is not None else default_search_roots())]
    while stack:
        folder, level = stack.pop()
        if should_stop is not None and should_stop():
            break
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        hit = False
        for e in entries:
            try:
                if e.is_file(follow_symlinks=False) and looks_like_installer(e.name):
                    hit = True
                elif e.is_dir(follow_symlinks=False) and level < depth:
                    stack.append((Path(e.path), level + 1))
            except OSError:
                continue
        if hit and folder not in found:
            found.append(folder)
    return found


def trust_verdict(status: str, subject: str | None,
                  trusted: Sequence[str] = TRUSTED_SIGNERS) -> tuple[bool, str]:
    """(можно ли ставить, почему) по итогу authenticode_signature."""
    if status != "Valid":
        return False, (f"подпись {status or 'не прочиталась'}" if status != "NotSigned"
                       else "файл не подписан")
    subj = subject or ""
    if not any(t.lower() in subj.lower() for t in trusted):
        return False, f"подписан не Р7: {subj or 'издатель не прочитался'}"
    return True, f"подпись действительна, издатель {subj}"


def check_installer(path: str | os.PathLike[str], signature: SignatureFn) -> tuple[bool, str]:
    """Проверка файла перед установкой: существует и подписан доверенным издателем."""
    p = Path(path)
    if not p.is_file():
        return False, "файла нет"
    try:
        status, subject = signature(p)
    except Exception as e:  # PowerShell не запустился, таймаут — ставить нельзя
        return False, f"подпись не проверилась ({type(e).__name__}: {e})"
    return trust_verdict(status, subject)
