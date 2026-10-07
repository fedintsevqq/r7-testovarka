"""Настройки на машину (r7/settings.py, r7_settings.json): умолчания, битый
файл, запись и чтение, приоритет r7_path над реестром в _find_r7_path,
папка отчётов из настроек."""
import json
import logging

import pytest

import r7_Testovarka as r7mod
from r7 import config as r7config
from r7 import logfile, settings


@pytest.fixture
def base_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    return tmp_path


# ── load / save / get ─────────────────────────────────────────────────────

def test_missing_file_gives_defaults(base_dir):
    assert settings.load_settings() == settings.DEFAULTS
    assert settings.get("r7_path") is None
    assert settings.get("first_run_done") is None          # ключа нет — None, не KeyError


def test_broken_file_gives_defaults_and_one_warning(base_dir, caplog):
    (base_dir / settings.SETTINGS_FILE).write_text("{битый json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=logfile.LOGGER_NAME):
        assert settings.load_settings() == settings.DEFAULTS
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "r7_settings.json" in warnings[0].getMessage()


def test_non_object_json_gives_defaults(base_dir, caplog):
    (base_dir / settings.SETTINGS_FILE).write_text("[1, 2]", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=logfile.LOGGER_NAME):
        assert settings.load_settings() == settings.DEFAULTS
    assert any("ожидался объект" in r.getMessage() for r in caplog.records)


def test_save_and_load_round_trip_keeps_unknown_keys(base_dir):
    assert settings.save_settings({"r7_path": r"E:\R7\DesktopEditors.exe",
                                   "default_runs": 5, "first_run_done": True}) is True
    loaded = settings.load_settings()
    assert loaded["r7_path"] == r"E:\R7\DesktopEditors.exe"
    assert loaded["default_runs"] == 5
    assert loaded["first_run_done"] is True
    assert loaded["reports_folder"] is None                 # умолчание подставлено
    assert settings.get("default_runs") == 5
    assert json.loads((base_dir / settings.SETTINGS_FILE).read_text(encoding="utf-8"))[
        "first_run_done"] is True


def test_save_failure_is_logged_not_raised(base_dir, monkeypatch, caplog):
    monkeypatch.setattr(r7config, "BASE_DIR", base_dir / "нет" / "такой" / "папки")
    with caplog.at_level(logging.WARNING, logger=logfile.LOGGER_NAME):
        assert settings.save_settings({"r7_path": None}) is False
    assert any("не записан" in r.getMessage() for r in caplog.records)


def test_get_falls_back_to_default_when_value_is_null(base_dir):
    settings.save_settings({"default_runs": None})
    assert settings.get("default_runs") is settings.DEFAULTS["default_runs"]


# ── _find_r7_path: r7_path из настроек выше реестра ───────────────────────

@pytest.fixture
def app():
    inst = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    inst._cached_r7_path = None
    return inst


def test_settings_path_wins_over_registry(app, base_dir, monkeypatch, caplog):
    exe = base_dir / "custom" / "DesktopEditors.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    settings.save_settings({"r7_path": str(exe)})
    registry_calls = []
    monkeypatch.setattr(app, "_read_current_version_from_registry",
                        lambda: registry_calls.append(1) or {"install_location": r"C:\другая"})
    with caplog.at_level(logging.INFO, logger=logfile.LOGGER_NAME):
        assert app._find_r7_path() == str(exe)
    assert registry_calls == []                              # реестр даже не читался
    assert any("из r7_settings.json" in r.getMessage() for r in caplog.records)


def test_missing_settings_path_falls_back_to_registry(app, base_dir, tmp_path, monkeypatch, caplog):
    settings.save_settings({"r7_path": str(base_dir / "нет" / "DesktopEditors.exe")})
    exe = tmp_path / "Editors" / "DesktopEditors.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(app, "_read_current_version_from_registry",
                        lambda: {"install_location": str(exe.parent)})
    with caplog.at_level(logging.WARNING, logger=logfile.LOGGER_NAME):
        assert app._find_r7_path() == str(exe)
    assert any("не существует" in r.getMessage() for r in caplog.records)


def test_not_found_logs_where_it_looked(app, base_dir, monkeypatch, caplog):
    monkeypatch.setattr(app, "_read_current_version_from_registry",
                        lambda: {"version": "2026.3.2", "install_location": r"C:\R7\удалена"})
    monkeypatch.setattr(r7mod.Path, "exists", lambda self: False)
    monkeypatch.setattr(r7mod.os.path, "exists", lambda p: False)
    with caplog.at_level(logging.WARNING, logger=logfile.LOGGER_NAME):
        assert app._find_r7_path() is None
    msg = next(r.getMessage() for r in caplog.records if "не найден" in r.getMessage())
    assert r"C:\R7\удалена" in msg                            # папка из реестра
    assert r"C:\Program Files\R7-Office\Editors\DesktopEditors.exe" in msg   # запасные пути
    assert "r7_path" in msg                                   # подсказка, как задать путь
    assert app._r7_path_searched and any("реестр" in s for s in app._r7_path_searched)


def test_fallback_path_use_is_logged(app, base_dir, monkeypatch, caplog):
    fallback = r"C:\Program Files\R7-Office\Editors\DesktopEditors.exe"
    monkeypatch.setattr(app, "_read_current_version_from_registry", lambda: {})
    monkeypatch.setattr(r7mod.Path, "exists", lambda self: str(self) == fallback)
    with caplog.at_level(logging.INFO, logger=logfile.LOGGER_NAME):
        assert app._find_r7_path() == fallback
    assert any("запасной" in r.getMessage() and fallback in r.getMessage()
               for r in caplog.records)


# ── default_runs: повторы тестов правки ───────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (None, r7config.DEFAULT_TEST_RUNS), ("не число", r7config.DEFAULT_TEST_RUNS),
    (5, 5), (0, r7config.RUNS_MIN), (99, r7config.RUNS_MAX),
])
def test_default_runs_from_settings_is_clamped(app, base_dir, value, expected):
    settings.save_settings({"default_runs": value})
    assert app._default_test_entry("Выделение всех ячеек (Ctrl+A)")["runs"] == expected
    assert app._default_test_entry(app.OPEN_TEST_NAME)["runs"] == app.DEFAULT_OPEN_RUNS


# ── Папка отчётов из настроек ─────────────────────────────────────────────

def test_reports_folder_from_settings(tmp_path):
    custom = tmp_path / "общая" / "Reports"
    assert r7mod.R7Testovarka._resolve_reports_folder(str(custom)) == custom
    assert custom.is_dir()                                   # создана


def test_reports_folder_default_when_unset_or_unusable(tmp_path):
    default = r7mod.BASE_DIR / "Reports"
    assert r7mod.R7Testovarka._resolve_reports_folder(None) == default
    blocker = tmp_path / "файл"
    blocker.write_text("x", encoding="utf-8")
    assert r7mod.R7Testovarka._resolve_reports_folder(str(blocker / "Reports")) == default
