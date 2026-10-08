"""Метаданные сборки Р7 в отчёте (r7.build_meta, этап 2 плана, п. 1)."""
import hashlib
import json

import pytest

import r7_reports
import r7_Testovarka as r7mod
from r7 import build_meta
from r7 import config as r7config


@pytest.fixture
def base_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    return tmp_path


def _exe(folder, content=b"MZ" + b"x" * 1000):
    exe = folder / "DesktopEditors.exe"
    exe.write_bytes(content)
    return exe


@pytest.mark.parametrize("version, number", [
    ("2026.3.2.3229", "3229"), ("2026.2.2.2923-1", "1"), ("v2026.1.3", "3"),
    ("", None), (None, None), ("без чисел", None),
])
def test_build_number_is_last_numeric_component(version, number):
    assert build_meta.build_number(version) == number


def test_metadata_from_registry_and_exe(tmp_path):
    exe = _exe(tmp_path)
    meta = build_meta.build_metadata(
        {"name": "Р7-Офис. Профессиональный", "version": "2026.3.2.3229"}, exe,
        installer_file="r7-office-2026.3.2.msi",
        changelog_template="https://r7/changelog/{version}#{build}")
    assert meta["product"] == "Р7-Офис. Профессиональный"
    assert meta["version"] == "2026.3.2.3229" and meta["build_number"] == "3229"
    assert meta["installer_file"] == "r7-office-2026.3.2.msi"
    assert meta["exe_path"] == str(exe) and meta["exe_size"] == 1002
    assert meta["exe_sha256"] == hashlib.sha256(exe.read_bytes()).hexdigest()
    assert len(meta["exe_mtime"]) == 19 and meta["exe_mtime"][4] == "-"
    assert meta["changelog_url"] == "https://r7/changelog/2026.3.2.3229#3229"


def test_metadata_tolerates_missing_exe_and_registry(tmp_path):
    meta = build_meta.build_metadata(None, tmp_path / "нет.exe")
    assert meta["product"] is None and meta["version"] is None and meta["build_number"] is None
    assert meta["exe_path"].endswith("нет.exe")
    assert meta["exe_mtime"] is None and meta["exe_size"] is None and meta["exe_sha256"] is None
    assert meta["installer_file"] is None and meta["changelog_url"] is None
    assert build_meta.build_metadata({"version": "1.2"}, None)["exe_path"] is None


def test_changelog_template_errors_give_none():
    assert build_meta.changelog_url("https://x/{nope}", "1.2") is None
    assert build_meta.changelog_url("https://x/{version}", None) is None
    assert build_meta.changelog_url(None, "1.2") is None


def test_sha256_cached_by_path_mtime_and_size(tmp_path, monkeypatch):
    exe = _exe(tmp_path)
    calls = []
    real_open = open

    def counting_open(*a, **k):
        calls.append(a[0])
        return real_open(*a, **k)
    monkeypatch.setattr("builtins.open", counting_open)
    first = build_meta.exe_sha256(exe)
    second = build_meta.exe_sha256(exe)
    assert first == second and len(calls) == 1          # второй раз — из кэша
    exe.write_bytes(b"MZ" + b"y" * 2000)                # размер сменился → пересчёт
    assert build_meta.exe_sha256(exe) != first and len(calls) == 2
    assert build_meta.exe_sha256(tmp_path / "нет.exe") is None
    assert build_meta.exe_sha256(None) is None


def test_build_summary_tolerates_old_report():
    assert build_meta.build_summary({}) == {"build_number": None, "sha_short": None,
                                            "exe_date": None, "installer_file": None,
                                            "changelog_url": None}
    s = build_meta.build_summary({"build": {"build_number": "3229", "exe_sha256": "a" * 64,
                                            "exe_mtime": "2026-09-30T12:00:00"}})
    assert s["sha_short"] == "a" * 12 and s["exe_date"] == "30.09.2026"


# ── в отчёте ───────────────────────────────────────────────────────────────

def test_full_report_carries_build_without_schema_bump(bare_r7, base_dir, tmp_path):
    exe = _exe(tmp_path)
    bare_r7._applied_r7_window_size = None
    bare_r7._run_environment = None
    bare_r7._cached_cpu_count = 4
    bare_r7._cached_r7_path = str(exe)
    bare_r7.current_version_info = {"name": "Р7-Офис", "version": "2026.3.2.3229"}
    bare_r7._session_installer = ("r7-2026.3.2.msi", "2026.3.2.3229")
    rep = bare_r7._build_full_report("20261007_120000", "Р7-Офис 2026.3.2.3229", "f.xlsx", [], {})
    assert rep["measure_schema"] == r7config.MEASURE_SCHEMA_VERSION == 11
    assert rep["build"]["build_number"] == "3229" and rep["build"]["exe_sha256"]
    assert rep["build"]["installer_file"] == "r7-2026.3.2.msi"
    json.dumps(rep)                                       # сериализуемо


def test_installer_file_only_for_the_version_batch_installed(bare_r7, base_dir):
    bare_r7._applied_r7_window_size = None
    bare_r7._run_environment = None
    bare_r7._cached_cpu_count = 4
    bare_r7.current_version_info = {"name": "Р7-Офис", "version": "2026.3.3.4000"}
    bare_r7._session_installer = ("r7-2026.3.2.msi", "2026.3.2.3229")   # поставили другую
    assert bare_r7._build_metadata()["installer_file"] is None
    bare_r7._session_installer = None
    assert bare_r7._build_metadata()["installer_file"] is None


def test_bare_instance_without_attributes_gives_empty_build(base_dir):
    inst = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    b = inst._build_metadata()
    assert b["version"] is None and b["exe_path"] is None and b["exe_sha256"] is None


def test_run_report_shows_build_rows(tmp_path):
    env = {"calibration": {"cpu_ms": 312.4, "disk_mb_s": 410.0}, "fingerprint_hash": "abc123def456"}
    model = r7_reports.run_report_model(
        [], tmp_path / "f.xlsx", 8.0, "2026.3.2", system={"environment": env},
        build={"build_number": "3229", "exe_sha256": "f" * 64, "exe_mtime": "2026-09-30T12:00:00",
               "changelog_url": "https://r7/c/2026.3.2"})
    meta = dict(model["meta"])
    assert meta["Сборка Р7"] == "3229"
    assert meta["DesktopEditors.exe"] == "sha256 ffffffffffff, от 30.09.2026"
    assert meta["Индекс стенда"] == "CPU 312 мс, диск 410 МБ/с"
    assert meta["Отпечаток машины"] == "abc123def456"
    assert meta["Changelog"] == "https://r7/c/2026.3.2"
    out = r7_reports.render("run.html", **model)
    assert "ffffffffffff" in out and "3229" in out


def test_run_report_without_build_shows_dashes(tmp_path):
    meta = dict(r7_reports.run_report_model([], tmp_path / "f.xlsx", 8.0, "2026.3.2")["meta"])
    assert meta["Сборка Р7"] == "—" and meta["DesktopEditors.exe"] == "—"
    assert meta["Индекс стенда"] == "—" and meta["Отпечаток машины"] == "—"
    assert "Changelog" not in meta


def test_comparison_shows_build_number_per_column():
    ds = [{"path": "a", "version": "2026.3.1",
           "data": {"results": [], "build": {"build_number": "3100", "exe_sha256": "a" * 64}}},
          {"path": "b", "version": "2026.3.2", "data": {"results": []}}]
    model = r7_reports.comparison_model(ds, "a", lambda a, b: {"verdict": "без изменений"}, 5)
    assert [v["build"] for v in model["versions"]] == ["3100", None]
    out = r7_reports.render("comparison.html", **model)
    assert "сборка 3100" in out and "aaaaaaaaaaaa" in out
