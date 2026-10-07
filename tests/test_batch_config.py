"""Решения диалога Batch без Tk (r7.batch_config, этап 4 плана)."""
from pathlib import Path

import pytest

from r7.batch_config import (FIXTURE_COLS, FIXTURE_NAME, FIXTURE_ROWS, LEGACY_FIXTURE_NAME,
                             TEST_FILE_PATTERNS, BatchConfig, auto_fixture_name,
                             find_test_file, list_distributives,
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


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_legacy_name_in_either_normalization_is_found(tmp_path, form):
    """«й» в имени прежней фикстуры на стенде хранится в NFD, на другом ПК
    то же имя наберут в NFC — литеральный шаблон совпадает только с одной
    формой, запасной «*50К*.xlsx» находит обе."""
    import unicodedata
    name = unicodedata.normalize(form, LEGACY_FIXTURE_NAME)
    (tmp_path / name).write_bytes(b"x")
    found, _ = find_test_file([tmp_path])
    assert found is not None and found.name == name


def test_nfc_and_nfd_legacy_names_are_distinct_files_and_one_is_found(tmp_path):
    import unicodedata
    names = {unicodedata.normalize(f, LEGACY_FIXTURE_NAME) for f in ("NFC", "NFD")}
    assert len(names) == 2
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    assert len(list(tmp_path.iterdir())) == 2      # NTFS хранит их как два файла
    found, _ = find_test_file([tmp_path])
    assert found is not None and found.name in names


def test_new_latin_name_preferred_over_legacy_in_same_folder(tmp_path):
    """Новое имя r7-test-50k.xlsx ищется первым; прежнее рядом — не мешает."""
    (tmp_path / LEGACY_FIXTURE_NAME).write_bytes(b"x")
    (tmp_path / "test_50k.xlsx").write_bytes(b"x")
    new = tmp_path / FIXTURE_NAME
    new.write_bytes(b"x")
    found, _ = find_test_file([tmp_path])
    assert found == new


def test_latin_50k_fallback_matches_without_legacy(tmp_path):
    (tmp_path / "copy_50k.xlsx").write_bytes(b"x")
    found, _ = find_test_file([tmp_path])
    assert found == tmp_path / "copy_50k.xlsx"


def test_auto_fixture_name_for_working_dims_is_canonical():
    """Генератор при размерах рабочей фикстуры даёт её каноническое имя,
    при других — прежний шаблон test_data_<строки>x<столбцы>."""
    assert auto_fixture_name(FIXTURE_ROWS, FIXTURE_COLS) == FIXTURE_NAME == "r7-test-50k.xlsx"
    assert auto_fixture_name("50000", "50") == FIXTURE_NAME
    assert auto_fixture_name(50000, 49) == "test_data_50000x49.xlsx"
    assert auto_fixture_name(10000, 50) == "test_data_10000x50.xlsx"
    assert TEST_FILE_PATTERNS[0] == "r7-test-50k*.xlsx"
