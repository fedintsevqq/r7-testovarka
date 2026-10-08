"""Текст GitHub Release из раздела CHANGELOG.md.

`gh release create --generate-notes` давал список всех PR с первого (у v1.1.0 —
140 строк, начиная с «Worktree resource metrics improvement»). Релиз должен
говорить, что нового в этой версии, — это уже написано в CHANGELOG. Строки там
перенесены вручную по ~78 символов, а в описании релиза GitHub превращает
одиночный перенос в разрыв строки, поэтому абзацы и пункты склеиваются.

Запуск в сборке: `python -m r7.release_notes 1.1.0 --repo owner/name --out notes.md`
(файл пишется в UTF-8 самим скриптом: перенаправление через pwsh перекодирует вывод).
Раздела нет — код выхода 1, сборка тогда берёт `--generate-notes`.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

_LIST_ITEM_RE = re.compile(r"^(\s*)([-*]|\d+\.)\s+")
_BLOCK_START = ("#", "|", "```", ">", "---")


def changelog_section(text: str, version: str) -> list[str] | None:
    """Строки раздела `## [version]` без заголовка, до следующего `## [`.

    Returns:
        list[str] | None: строки раздела; None — раздела нет или он пуст.
    """
    lines = text.splitlines()
    head = f"## [{version}]"
    for i, line in enumerate(lines):
        if line.startswith(head):
            body: list[str] = []
            for rest in lines[i + 1:]:
                if rest.startswith("## ["):
                    break
                body.append(rest)
            return body if "".join(body).strip() else None
    return None


def unwrap(lines: Iterable[str]) -> list[str]:
    """Склеивает строки, перенесённые внутри абзаца или пункта списка.

    Новая строка начинается с пустой строки, заголовка, таблицы, цитаты,
    блока кода и пункта списка (в том числе вложенного); всё остальное —
    продолжение предыдущей строки. Внутри блока кода ничего не трогается.
    """
    out: list[str] = []
    in_code = False
    for raw in lines:
        line = raw.rstrip()
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        starts_block = (in_code or not stripped or stripped.startswith(_BLOCK_START)
                        or _LIST_ITEM_RE.match(line) is not None)
        prev = out[-1] if out else ""
        prev_open = bool(prev.strip()) and not prev.lstrip().startswith(_BLOCK_START)
        if not starts_block and prev_open:
            out[-1] = f"{prev} {stripped}"
        else:
            out.append(line)
    return out


def release_notes(changelog: str, version: str, repo: str) -> str | None:
    """Текст релиза: как поставить, раздел CHANGELOG, где хеши. None — раздела нет."""
    section = changelog_section(changelog, version)
    if section is None:
        return None
    tag = f"v{version}"
    base = f"https://github.com/{repo}/blob/{tag}"
    head = [
        f"Скачайте `R7-Testovarka-{version}-win64.zip`, распакуйте в папку на локальном "
        f"диске и запустите `R7-Testovarka.exe` от имени администратора. Пошагово — "
        f"[первый запуск]({base}/docs/rollout/README-first-run.md).",
        "",
    ]
    tail = [
        "",
        "---",
        "",
        "Хеши — файлы `.sha256` рядом: `sha256sum -c <файл>.sha256` или "
        "`certutil -hashfile <файл> SHA256`. Все версии — "
        f"[CHANGELOG.md]({base}/CHANGELOG.md).",
    ]
    body = unwrap(section)
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(head + body + tail) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m r7.release_notes",
                                 description="Текст GitHub Release из раздела CHANGELOG.md")
    ap.add_argument("version", help="версия без «v», как в r7/version.py")
    ap.add_argument("--repo", required=True, help="owner/name репозитория на GitHub")
    ap.add_argument("--changelog", default="CHANGELOG.md", help="путь к CHANGELOG.md")
    ap.add_argument("--out", help="файл для текста (UTF-8); без него — в stdout")
    args = ap.parse_args(argv)
    text = Path(args.changelog).read_text(encoding="utf-8")
    notes = release_notes(text, args.version, args.repo)
    if notes is None:
        print(f"в {args.changelog} нет раздела ## [{args.version}]", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(notes, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
