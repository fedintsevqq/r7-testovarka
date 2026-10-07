"""Общая папка команды (r7.team_folder): копия отчёта в подпапку машины,
чтение чужих отчётов в тренды и список сравнения, предупреждения о разных
стендах на страницах."""
import json

import pytest

import r7_reports
from r7 import config as r7config
from r7 import fingerprint, team_folder
from r7.compare_files import scan_reports


@pytest.fixture
def base_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    return tmp_path


def _settings(base_dir, **kw):
    (base_dir / "r7_settings.json").write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")


def _fp(**over):
    base = dict(cpu_model="i7", cpu_logical=8, ram_gb=16, os_name="Win10", dpi_scale_pct=100,
                r7_data_drive="C:", reports_drive="C:", power_plan="High")
    return fingerprint.collect(**{**base, **over})


def _report(folder, name, version, fp=None, ts="20261007_120000", results=()):
    env = None
    if fp is not None:
        env = {"fingerprint": fp, "fingerprint_hash": fingerprint.fingerprint_hash(fp)}
    data = {"timestamp": ts, "version": version, "measure_schema": 9,
            "results": list(results), "system": {"environment": env}}
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


# ── копия ─────────────────────────────────────────────────────────────────

def test_copy_reports_into_machine_subfolder(tmp_path, log):
    team = tmp_path / "team"
    team.mkdir()
    j = tmp_path / "performance_full_1.json"
    h = tmp_path / "Performance_Report_1.html"
    j.write_text("{}", encoding="utf-8")
    h.write_text("<html>", encoding="utf-8")
    dest = team_folder.copy_reports(team, "pc7-abc", (j, h, None), log)
    assert dest == team / "pc7-abc"
    assert (dest / j.name).read_text(encoding="utf-8") == "{}" and (dest / h.name).exists()
    assert any("Скопировано в общую папку" in m for m in log.messages)


def test_copy_reports_unreachable_folder_only_logs(tmp_path, log):
    assert team_folder.copy_reports(tmp_path / "нет", "pc", (tmp_path / "x.json",), log) is None
    assert any("недоступна" in m for m in log.messages)


def test_copy_reports_failure_is_logged_not_raised(tmp_path, log, monkeypatch):
    team = tmp_path / "team"
    team.mkdir()
    j = tmp_path / "performance_full_1.json"
    j.write_text("{}", encoding="utf-8")

    def boom(*a, **k):
        raise PermissionError("сетевой диск только для чтения")
    monkeypatch.setattr(team_folder.shutil, "copy2", boom)
    assert team_folder.copy_reports(team, "pc", (j,), log) is None
    assert any("не скопирован" in m and "PermissionError" in m for m in log.messages)


def test_write_run_reports_copies_when_folder_set(bare_r7, base_dir, tmp_path, log, monkeypatch):
    team = tmp_path / "team"
    team.mkdir()
    _settings(base_dir, team_reports_folder=str(team))
    bare_r7.reports_folder = tmp_path / "Reports"
    bare_r7.current_version_info = {"name": "Р7", "version": "2026.3.2.3229"}
    bare_r7._run_environment = {"fingerprint_hash": "abc123abc123", "warnings": []}
    bare_r7._cached_cpu_count = 4
    bare_r7._cached_r7_path = None
    monkeypatch.setattr(fingerprint, "hostname", lambda: "PC-7")
    res = {"ram_vals": [], "cpu_vals": [], "peak_ram_mb": 1.0, "avg_ram_mb": 1.0,
           "min_ram_mb": 1.0, "peak_cpu_pct": 1.0, "peak_cpu_normalized_pct": 1.0}
    ts, html_path = bare_r7._write_run_reports([], tmp_path / "f.xlsx", 8.0, res, None, log)
    dest = team / "PC-7-abc123abc123"
    assert (dest / f"performance_full_{ts}.json").exists()
    assert (dest / html_path.name).exists()


def test_write_run_reports_skips_copy_when_folder_unset(bare_r7, base_dir, tmp_path, log):
    bare_r7.reports_folder = tmp_path / "Reports"
    bare_r7.current_version_info = None
    bare_r7._run_environment = None
    bare_r7._cached_cpu_count = 4
    bare_r7._cached_r7_path = None
    res = {"ram_vals": [], "cpu_vals": [], "peak_ram_mb": 1.0, "avg_ram_mb": 1.0,
           "min_ram_mb": 1.0, "peak_cpu_pct": 1.0, "peak_cpu_normalized_pct": 1.0}
    bare_r7._write_run_reports([], tmp_path / "f.xlsx", 8.0, res, None, log)
    assert not any("общую папку" in m for m in log.messages)
    assert bare_r7._copy_report_to_team(tmp_path / "x.json", None, log) is None


def test_batch_json_is_copied_too(bare_r7, base_dir, tmp_path, log, monkeypatch):
    team = tmp_path / "team"
    team.mkdir()
    _settings(base_dir, team_reports_folder=str(team))
    bare_r7.reports_folder = tmp_path / "Reports"
    bare_r7.reports_folder.mkdir()
    bare_r7.current_version_info = None
    bare_r7._run_environment = {"fingerprint_hash": "ff00ff00ff00"}
    bare_r7._cached_cpu_count = 4
    bare_r7._cached_r7_path = None
    monkeypatch.setattr(fingerprint, "hostname", lambda: "pc")
    res = {"peak_ram_mb": 1.0, "avg_ram_mb": 1.0, "min_ram_mb": 1.0, "peak_cpu_pct": 1.0,
           "peak_cpu_normalized_pct": 1.0}
    path = bare_r7._batch_save_json("2026.3.2", tmp_path / "f.xlsx", [], res, log)
    assert (team / "pc-ff00ff00ff00" / path.name).exists()


# ── чтение ────────────────────────────────────────────────────────────────

def test_team_report_files_tagged_by_subfolder_and_sorted(tmp_path):
    team = tmp_path / "team"
    a = _report(team / "pc1-aaa", "performance_full_1.json", "v1")
    b = _report(team / "pc2-bbb" / "nested", "performance_full_2.json", "v2")
    c = _report(team, "performance_full_3.json", "v3")
    import os
    for i, p in enumerate((a, b, c)):
        os.utime(p, (1000 + i, 1000 + i))
    files = team_folder.team_report_files(team)
    assert [(m, p.name) for m, p in files] == [
        ("pc1-aaa", "performance_full_1.json"), ("pc2-bbb", "performance_full_2.json"),
        ("team", "performance_full_3.json")]
    assert team_folder.team_report_files(tmp_path / "нет") == []
    assert team_folder.team_report_files(None) == []


def test_load_trends_runs_merges_team_folder_and_dedupes(bare_r7, base_dir, tmp_path):
    team = tmp_path / "team"
    local = tmp_path / "Reports"
    _settings(base_dir, team_reports_folder=str(team))
    fp_a, fp_b = _fp(), _fp(cpu_model="Ryzen")
    _report(local, "performance_full_1.json", "v1", fp_a, results=[{"name": "A", "time": 1.0}])
    _report(team / "me-x", "performance_full_1.json", "v1", fp_a)          # своя же копия
    _report(team / "pc2-y", "performance_full_2.json", "v1", fp_b, ts="20261007_130000",
            results=[{"name": "A", "time": 2.0}])
    bare_r7.reports_folder = local
    runs = bare_r7._load_trends_runs()
    assert [(r["machine"], r["path"].name) for r in runs] == [
        (None, "performance_full_1.json"), ("pc2-y", "performance_full_2.json")]
    assert runs[0]["fingerprint"] == fingerprint.fingerprint_hash(fp_a)
    assert runs[1]["fingerprint_fields"] == fp_b
    model = r7_reports.trends_model(runs)
    assert model["multi_machine"] and "процессор" in model["fingerprint_warning"]
    assert model["machines"] == ["эта папка", "pc2-y"]
    out = bare_r7._generate_trends_html(runs)
    assert 'id="machineFilter"' in out and "Отчёты с разных машин" in out
    assert 'data-machine="pc2-y"' in out


def test_load_trends_runs_without_team_folder_keeps_old_shape(bare_r7, base_dir, tmp_path):
    local = tmp_path / "Reports"
    _report(local, "performance_full_1.json", "v1", results=[{"name": "A", "time": 1.0}])
    bare_r7.reports_folder = local
    runs = bare_r7._load_trends_runs()
    assert runs[0]["machine"] is None and runs[0]["fingerprint"] is None
    model = r7_reports.trends_model(runs)
    assert model["fingerprint_warning"] is None and not model["multi_machine"]
    out = bare_r7._generate_trends_html(runs + runs)
    assert 'id="machineFilter"' not in out


def test_scan_reports_includes_team_folder_with_machine_label(base_dir, tmp_path):
    team = tmp_path / "team"
    local = tmp_path / "Reports"
    local.mkdir()
    _settings(base_dir, team_reports_folder=str(team))
    _report(local, "performance_full_1.json", "v1")
    _report(team / "me", "performance_full_1.json", "v1")
    _report(team / "pc2-y", "performance_full_2.json", "v2")
    metas = scan_reports(local)
    by_name = {m["path"].name: m for m in metas}
    assert len(metas) == 2
    assert by_name["performance_full_1.json"]["machine"] is None
    assert by_name["performance_full_2.json"]["machine"] == "pc2-y"
    assert by_name["performance_full_2.json"]["display_name"] == "v2 · pc2-y"
    assert [m["path"].name for m in scan_reports(local, team=False)] == ["performance_full_1.json"]


# ── предупреждение на странице сравнения ─────────────────────────────────

def _ds(path, version, fp):
    data = {"results": [{"name": "A", "time": 1.0, "runs": [1.0] * 6, "run_statuses": ["ok"] * 6}],
            "measure_schema": 9, "system": {"environment": {"fingerprint": fp} if fp else None}}
    return {"path": path, "version": version, "data": data}


def test_comparison_warns_only_when_fingerprints_differ(bare_r7):
    same = bare_r7._generate_comparison_html([_ds("a", "v1", _fp()), _ds("b", "v2", _fp())], "a")
    assert "Разные стенды" not in same
    old = bare_r7._generate_comparison_html([_ds("a", "v1", _fp()), _ds("b", "v2", None)], "a")
    assert "Разные стенды" not in old                       # старый отчёт — не чужая машина
    other = bare_r7._generate_comparison_html(
        [_ds("a", "v1", _fp()), _ds("b", "v2", _fp(ram_gb=32, dpi_scale_pct=150))], "a")
    assert "Разные стенды" in other and "RAM, масштаб экрана" in other
    assert other.index("Разные стенды") < other.index('id="timeChart"')
