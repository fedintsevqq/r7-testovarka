"""Наборы тестов как данные (r7/suites.py): чтение TOML, проверка имён и
повторов, бюджеты, выбор тестов для интерфейса, поставляемые suites/*.toml."""
import json
from pathlib import Path

import pytest

import r7_Testovarka as r7mod
from r7 import config, suites
from r7.config import DEFAULT_TEST_RUNS, RUNS_MAX, RUNS_MIN
from r7.stats import COMPARISON_MIN_EFFECT_PCT
from r7.suites import Suite, SuiteError, list_suites, load_suite, parse_suite, suite_to_selection

ROOT = Path(__file__).resolve().parent.parent
NAMES = r7mod.R7Testovarka.TEST_DEFINITIONS
OPEN, CTRL_A, CTRL_V = NAMES[0], NAMES[1], NAMES[3]

GOOD = f'''
[suite]
name = "smoke"
description = "дым"

[tests]
"{OPEN}" = 3
"{CTRL_A}" = 5

[budgets]
"{OPEN}" = 12.0

[compare]
min_effect_pct = 15
'''


def _write(tmp_path, text, name="s.toml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_load_valid_suite(tmp_path):
    s = load_suite(_write(tmp_path, GOOD), NAMES)
    assert s.name == "smoke" and s.description == "дым"
    assert s.tests == {OPEN: 3, CTRL_A: 5} and s.total_runs == 8
    assert s.budgets == {OPEN: 12.0} and s.min_effect_pct == 15.0
    assert s.path == tmp_path / "s.toml"


def test_optional_sections_default(tmp_path):
    s = load_suite(_write(tmp_path, f'[suite]\nname="x"\n[tests]\n"{CTRL_A}" = 5\n'), NAMES)
    assert s.budgets == {} and s.min_effect_pct == COMPARISON_MIN_EFFECT_PCT
    assert s.description == ""


def test_name_defaults_to_file_stem(tmp_path):
    s = load_suite(_write(tmp_path, f'[tests]\n"{CTRL_A}" = 5\n', "nightly.toml"), NAMES)
    assert s.name == "nightly"


def test_unknown_test_lists_valid_names(tmp_path):
    path = _write(tmp_path, '[suite]\nname="x"\n[tests]\n"Ctrl+A" = 5\n')
    with pytest.raises(SuiteError) as e:
        load_suite(path, NAMES)
    msg = str(e.value)
    assert "'Ctrl+A'" in msg and "s.toml" in msg
    assert all(n in msg for n in NAMES)                 # список допустимых — в ошибке


@pytest.mark.parametrize("value, expected", [(0, RUNS_MIN), (-3, RUNS_MIN), (99, RUNS_MAX), (7, 7)])
def test_runs_clamped_like_ui_field(value, expected):
    s = parse_suite({"suite": {"name": "x"}, "tests": {CTRL_A: value}}, NAMES)
    assert s.tests[CTRL_A] == expected


@pytest.mark.parametrize("value", ["7", 7.5, True, None])
def test_runs_must_be_integer(value):
    with pytest.raises(SuiteError, match="целым числом"):
        parse_suite({"suite": {"name": "x"}, "tests": {CTRL_A: value}}, NAMES)


def test_missing_file(tmp_path):
    with pytest.raises(SuiteError, match="не найден"):
        load_suite(tmp_path / "нет.toml", NAMES)


def test_broken_toml(tmp_path):
    with pytest.raises(SuiteError, match="ошибка в TOML"):
        load_suite(_write(tmp_path, "[suite\nname = "), NAMES)


@pytest.mark.parametrize("data", [{"suite": {"name": "x"}}, {"suite": {"name": "x"}, "tests": {}}])
def test_empty_tests_rejected(data):
    with pytest.raises(SuiteError, match="нет ни одного теста"):
        parse_suite(data, NAMES)


def test_name_required_without_file():
    with pytest.raises(SuiteError, match="нет name"):
        parse_suite({}, NAMES)


def test_budget_for_test_outside_suite():
    with pytest.raises(SuiteError, match="нет в \\[tests\\]"):
        parse_suite({"suite": {"name": "x"}, "tests": {CTRL_A: 5}, "budgets": {OPEN: 10}}, NAMES)


@pytest.mark.parametrize("value", [0, -1, "10", True])
def test_budget_must_be_positive_number(value):
    with pytest.raises(SuiteError, match="больше нуля"):
        parse_suite({"suite": {"name": "x"}, "tests": {CTRL_A: 5}, "budgets": {CTRL_A: value}}, NAMES)


def test_unknown_section_is_a_typo():
    with pytest.raises(SuiteError, match=r"неизвестный раздел \[test\]"):
        parse_suite({"suite": {"name": "x"}, "test": {CTRL_A: 5}}, NAMES)


@pytest.mark.parametrize("value", [-5, "10", True])
def test_min_effect_pct_validated(value):
    with pytest.raises(SuiteError, match="min_effect_pct"):
        parse_suite({"suite": {"name": "x"}, "tests": {CTRL_A: 5},
                     "compare": {"min_effect_pct": value}}, NAMES)


def test_suite_to_selection_matches_gui_format(tmp_path, monkeypatch):
    s = Suite("x", "", {OPEN: 3, CTRL_V: 5})
    sel = suite_to_selection(s, NAMES)
    assert list(sel) == list(NAMES)
    assert sel[OPEN] == {"enabled": True, "runs": 3}
    assert sel[CTRL_A] == {"enabled": False, "runs": DEFAULT_TEST_RUNS}
    # Интерфейс читает ровно эту структуру из selected_tests.json.
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / "selected_tests.json").write_text(json.dumps(sel, ensure_ascii=False), encoding="utf-8")
    inst = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    assert inst._load_test_selection() == sel


def test_suite_to_selection_without_all_names_keeps_suite_order():
    s = Suite("x", "", {CTRL_V: 5, OPEN: 3})
    assert list(suite_to_selection(s)) == [CTRL_V, OPEN]


def test_list_suites_sorted_and_missing_dir(tmp_path):
    for n in ("b.toml", "a.toml", "c.txt"):
        (tmp_path / n).write_text("", encoding="utf-8")
    assert [p.name for p in list_suites(tmp_path)] == ["a.toml", "b.toml"]
    assert list_suites(tmp_path / "нет") == []


def test_suites_dir_follows_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    assert suites.suites_dir() == tmp_path / "suites"


# ── Поставляемые наборы ───────────────────────────────────────────────────

def test_shipped_suites_load_against_test_definitions():
    files = {p.stem: p for p in list_suites(ROOT / "suites")}
    assert {"smoke", "release", "export"} <= set(files)
    loaded = {n: load_suite(p, NAMES) for n, p in files.items()}
    assert all(s.description for s in loaded.values())
    assert list(loaded["release"].tests) == list(NAMES)          # все 17, в порядке определения
    assert loaded["smoke"].tests == {OPEN: 3, CTRL_A: 5, CTRL_V: 5, "Функция ВПР (50K строк)": 5}
    assert set(loaded["export"].tests) == r7mod.R7Testovarka.EXPORT_TESTS
    assert set(loaded["export"].tests.values()) == {3}


def test_release_suite_uses_default_repeats():
    s = load_suite(ROOT / "suites" / "release.toml", NAMES)
    cls = r7mod.R7Testovarka
    assert s.tests[OPEN] == cls.DEFAULT_OPEN_RUNS
    assert all(s.tests[n] == cls.DEFAULT_FORMAT_TEST_RUNS for n in cls.EXPORT_TESTS)
    assert all(s.tests[n] == DEFAULT_TEST_RUNS for n in NAMES
               if n != OPEN and n not in cls.EXPORT_TESTS)
