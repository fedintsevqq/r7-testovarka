"""Текст GitHub Release из CHANGELOG (r7/release_notes.py)."""
from pathlib import Path

from r7 import release_notes as rn

CHANGELOG = """# Изменения

## [Unreleased]

## [1.1.0] — 2026-10-08

PR #106–#139, 07–08.10.2026.

### Добавлено

- Первый пункт, перенесённый
  на вторую строку и
  на третью (#1).
  - вложенный пункт
    с переносом.
- Второй пункт (#2).

Абзац текста
в две строки.

| a | b |
|---|---|

```bat
python -m r7
  --long
```

## [1.0.0] — 2026-10-07

Старое.
"""


def test_section_stops_at_next_version():
    sec = rn.changelog_section(CHANGELOG, "1.1.0")
    assert sec is not None and "Старое." not in "\n".join(sec)
    assert rn.changelog_section(CHANGELOG, "9.9.9") is None
    assert rn.changelog_section(CHANGELOG, "Unreleased") is None    # пустой раздел


def test_unwrap_joins_items_and_paragraphs_keeps_blocks():
    out = rn.unwrap(rn.changelog_section(CHANGELOG, "1.1.0"))
    assert "- Первый пункт, перенесённый на вторую строку и на третью (#1)." in out
    assert "  - вложенный пункт с переносом." in out
    assert "Абзац текста в две строки." in out
    assert "| a | b |" in out and "|---|---|" in out
    assert "  --long" in out                  # внутри блока кода не склеивается
    assert "### Добавлено" in out


def test_release_notes_has_install_link_and_footer():
    notes = rn.release_notes(CHANGELOG, "1.1.0", "o/r")
    assert notes.startswith("Скачайте `R7-Testovarka-1.1.0-win64.zip`")
    assert "https://github.com/o/r/blob/v1.1.0/docs/rollout/README-first-run.md" in notes
    assert "sha256sum -c" in notes and "blob/v1.1.0/CHANGELOG.md" in notes
    assert rn.release_notes(CHANGELOG, "2.0.0", "o/r") is None


def test_real_changelog_has_current_version():
    from r7.version import __version__
    text = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    assert rn.release_notes(text, __version__, "o/r") is not None


def test_cli_missing_section_exits_1(tmp_path, capsys):
    p = tmp_path / "CHANGELOG.md"
    p.write_text(CHANGELOG, encoding="utf-8")
    assert rn.main(["3.0.0", "--repo", "o/r", "--changelog", str(p)]) == 1


def test_cli_writes_utf8_file(tmp_path):
    p = tmp_path / "CHANGELOG.md"
    p.write_text(CHANGELOG, encoding="utf-8")
    out = tmp_path / "notes.md"
    assert rn.main(["1.1.0", "--repo", "o/r", "--changelog", str(p), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("Скачайте")
