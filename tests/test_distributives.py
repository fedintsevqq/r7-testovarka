"""Папки дистрибутивов, поиск по кнопке и доверие по подписи (r7/distributives.py)."""
import json

import pytest

from r7 import config, distributives as d, settings


@pytest.fixture
def base(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    return tmp_path


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_extra_dirs_skips_garbage_and_duplicates(base):
    (base / settings.SETTINGS_FILE).write_text(json.dumps(
        {"distributives_dirs": ["D:\\one", "", 5, "D:\\one", " D:\\two "]}), encoding="utf-8")
    assert [p.name for p in d.extra_dirs()] == ["one", "two"]
    (base / settings.SETTINGS_FILE).write_text(json.dumps({"distributives_dirs": "D:\\x"}),
                                               encoding="utf-8")
    assert d.extra_dirs() == []


def test_all_dirs_primary_first_without_repeats(base):
    primary = base / "Distributives"
    (base / settings.SETTINGS_FILE).write_text(json.dumps(
        {"distributives_dirs": [str(primary), str(base / "team")]}), encoding="utf-8")
    assert d.all_dirs(primary) == [primary, base / "team"]


def test_add_dir_persists_once(base):
    assert d.add_dir(base / "team") is True
    assert d.add_dir(base / "team") is False
    assert settings.load_settings()["distributives_dirs"] == [str(base / "team")]


def test_list_files_merges_dirs_and_skips_missing(base):
    a = _touch(base / "a" / "r7-office_2026.3.2.3229_x64.exe")
    b = _touch(base / "b" / "r7-office_2026.3.1.3296_x64.msi")
    _touch(base / "b" / "readme.txt")
    files = d.list_files([base / "a", base / "b", base / "нет", base / "a"])
    assert sorted(f.name for f in files) == sorted([a.name, b.name])


def test_find_installers_limited_depth_and_names(base):
    _touch(base / "Downloads" / "r7-office_2026.3.2.3229_x64.exe")
    _touch(base / "Downloads" / "deep" / "deeper" / "r7-office_2026.1.1.1849_x64.msi")
    _touch(base / "Downloads" / "deep" / "deeper" / "too" / "r7-office_2025.4.1.1604_x64.exe")
    _touch(base / "Downloads" / "other" / "setup.exe")
    found = d.find_installers([base / "Downloads"])
    assert found == [base / "Downloads", base / "Downloads" / "deep" / "deeper"] or \
        sorted(found) == sorted([base / "Downloads", base / "Downloads" / "deep" / "deeper"])
    assert d.find_installers([base / "Downloads"], should_stop=lambda: True) == []
    assert d.find_installers([base / "нет"]) == []


@pytest.mark.parametrize("status, subject, ok, why", [
    ("Valid", "E=x@r7-office.ru, CN=AO R7, O=AO R7", True, "AO R7"),
    ("Valid", "CN=Someone Else", False, "подписан не Р7"),
    ("NotSigned", None, False, "не подписан"),
    ("HashMismatch", "CN=AO R7", False, "HashMismatch"),
    ("", None, False, "не прочиталась"),
])
def test_trust_verdict(status, subject, ok, why):
    got_ok, got_why = d.trust_verdict(status, subject)
    assert got_ok is ok and why in got_why


def test_check_installer_missing_file_and_failing_probe(base):
    assert d.check_installer(base / "нет.exe", lambda p: ("Valid", "AO R7")) == (False, "файла нет")
    f = _touch(base / "r7-office_2026.3.2.3229_x64.exe")

    def boom(p):
        raise TimeoutError("60 с")
    ok, why = d.check_installer(f, boom)
    assert ok is False and "TimeoutError" in why
    assert d.check_installer(f, lambda p: ("Valid", "CN=AO R7"))[0] is True


@pytest.mark.parametrize("widget, tip, expect", [
    # (x, y, w, h) виджета; (w, h) подсказки; экран 1000 × 800
    ((400, 100, 100, 30), (160, 24), (370, 136)),      # место снизу есть — под кнопкой
    ((400, 760, 100, 30), (160, 24), (370, 730)),      # нижний ряд — над кнопкой
    ((950, 100, 40, 30), (160, 24), (840, 136)),       # у правого края — внутрь экрана
    ((5, 100, 20, 30), (160, 24), (0, 136)),           # у левого края
    ((400, 5, 100, 30), (160, 900), (370, 0)),         # не влезает нигде — не выше экрана
])
def test_place_tip_stays_on_screen(widget, tip, expect):
    """Подсказка у нижнего ряда кнопок раньше уезжала в угол экрана (09.10.2026)."""
    from r7.ui.tooltip import place_tip
    assert place_tip(*widget, *tip, 0, 0, 1000, 800) == expect


def test_list_distributives_accepts_many_dirs(base):
    from r7.batch_config import list_distributives
    _touch(base / "a" / "r7-office_2026.3.2.3229_x64.exe")
    _touch(base / "b" / "r7-office_2026.3.1.3296_x64.exe")
    names = [f.name for f in list_distributives([base / "a", base / "b"], lambda s: s.split("_")[1])]
    assert names == ["r7-office_2026.3.1.3296_x64.exe", "r7-office_2026.3.2.3229_x64.exe"]
    assert len(list_distributives(base / "a", lambda s: None)) == 1
