"""Решения диалога Batch без Tk (r7.batch_config, этап 4 плана)."""
from pathlib import Path

from r7.batch_config import (BatchConfig, find_test_file, list_distributives,
                             validate_batch_config)


def _extract_version(stem):
    import re
    m = re.search(r"(\d+\.\d+(?:\.\d+)*)", stem)
    return f"v{m.group(1)}" if m else None


# ── validate_batch_config ────────────────────────────────────────────────

def test_no_versions_selected_is_refused(tmp_path):
    f = tmp_path / "a.xlsx"
    f.write_bytes(b"x")
    cfg, refusal = validate_batch_config([], str(f))
    assert cfg is None and refusal[0] == "Нет выбора"


def test_missing_or_empty_file_is_refused(tmp_path):
    for tf in ("", "   ", None, str(tmp_path / "нет.xlsx"), str(tmp_path)):
        cfg, refusal = validate_batch_config([Path("r7.msi")], tf)
        assert cfg is None and refusal[0] == "Файл не найден", tf


def test_valid_selection_builds_config(tmp_path):
    f = tmp_path / "a.xlsx"
    f.write_bytes(b"x")
    cfg, refusal = validate_batch_config([Path("1.msi"), Path("2.msi")], f"  {f}  ",
                                         stop_on_error=False, cleanup=1)
    assert refusal is None
    assert cfg == BatchConfig((Path("1.msi"), Path("2.msi")), f, False, True)


# ── list_distributives ───────────────────────────────────────────────────

def test_distributives_sorted_by_numeric_version(tmp_path):
    """'v2026.10.1' — после 'v2026.9', а не раньше (строковое сравнение)."""
    for name in ("R7-2026.10.1.msi", "R7-2026.9.3.exe", "R7-2026.3.2.msi",
                 "без-версии.msi", "readme.txt"):
        (tmp_path / name).write_bytes(b"")
    names = [f.name for f in list_distributives(tmp_path, _extract_version)]
    assert names == ["R7-2026.3.2.msi", "R7-2026.9.3.exe", "R7-2026.10.1.msi", "без-версии.msi"]


def test_missing_distributives_folder_is_empty(tmp_path):
    assert list_distributives(tmp_path / "нет", _extract_version) == []


# ── find_test_file ───────────────────────────────────────────────────────

def test_fixture_found_in_first_folder_that_has_it(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (b / "файл-для-теста-Р7-офис-50К.xlsx").write_bytes(b"x")
    (a / "копия-50К.xlsx").write_bytes(b"x")
    found, locks = find_test_file([tmp_path / "нет", a, b])
    assert found == a / "копия-50К.xlsx" and locks == []


def test_lock_files_are_skipped_and_reported(tmp_path):
    lock = tmp_path / "~$файл-для-теста-Р7-офис-50К.xlsx"
    lock.write_bytes(b"")
    found, locks = find_test_file([tmp_path])
    assert found is None and locks == [lock]
    real = tmp_path / "файл-для-теста-Р7-офис-50К.xlsx"
    real.write_bytes(b"x")
    found, locks = find_test_file([tmp_path])
    assert found == real and locks == [lock]


def test_nfd_name_found_by_fallback_pattern(tmp_path):
    """«й» в имени рабочей фикстуры хранится в NFD — литеральный шаблон его
    не находит, запасной «*50К*.xlsx» — находит."""
    import unicodedata
    nfd = unicodedata.normalize("NFD", "файл-для-теста-Р7-офис-50К.xlsx")
    (tmp_path / nfd).write_bytes(b"x")
    found, _ = find_test_file([tmp_path])
    assert found is not None and found.name == nfd
