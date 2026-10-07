"""Отпечаток машины (r7.fingerprint) и его сбор в _capture_environment."""
import json

import pytest

import r7_Testovarka as r7mod
from r7 import calibration, fingerprint

FP = dict(cpu_model="Intel i7", cpu_logical=8, ram_gb=15.9, os_name="Windows-10-10.0.19045",
          dpi_scale_pct=125, r7_data_drive="C:", reports_drive="E:", power_plan="Высокая")


def test_hash_is_deterministic_and_order_independent():
    a = fingerprint.collect(**FP)
    b = dict(reversed(list(fingerprint.collect(**FP).items())))
    assert fingerprint.fingerprint_hash(a) == fingerprint.fingerprint_hash(b)
    assert len(fingerprint.fingerprint_hash(a)) == 12
    assert a["ram_gb"] == 16                                   # округление до ГБ


@pytest.mark.parametrize("field, value", [
    ("cpu_model", "AMD Ryzen"), ("cpu_logical", 16), ("ram_gb", 32), ("os_name", "Windows-11"),
    ("dpi_scale_pct", 100), ("r7_data_drive", "D:"), ("reports_drive", "C:"), ("power_plan", "Сбаланс"),
])
def test_hash_changes_when_any_field_changes(field, value):
    base = fingerprint.collect(**FP)
    other = fingerprint.collect(**{**FP, field: value})
    assert fingerprint.fingerprint_hash(base) != fingerprint.fingerprint_hash(other)
    key = "os" if field == "os_name" else field
    assert fingerprint.diff_fields(base, other) == [key]


def test_same_hash_for_ram_within_rounding():
    a = fingerprint.collect(**{**FP, "ram_gb": 15.6})
    b = fingerprint.collect(**{**FP, "ram_gb": 16.2})
    assert fingerprint.fingerprint_hash(a) == fingerprint.fingerprint_hash(b)


def test_drive_of():
    assert fingerprint.drive_of(r"c:\Users\x\AppData\Local") == "C:"
    assert fingerprint.drive_of("/tmp/x") is None and fingerprint.drive_of(None) is None


def test_report_fingerprint_tolerates_old_reports():
    assert fingerprint.report_fingerprint({}) == (None, None)
    assert fingerprint.report_fingerprint({"system": {"environment": None}}) == (None, None)
    fp = fingerprint.collect(**FP)
    h, d = fingerprint.report_fingerprint({"system": {"environment": {"fingerprint": fp}}})
    assert h == fingerprint.fingerprint_hash(fp) and d == fp      # хэш восстановлен


def test_mismatch_warning_only_for_different_hashes():
    fp = fingerprint.collect(**FP)
    other = fingerprint.collect(**{**FP, "cpu_model": "AMD", "r7_data_drive": "D:"})
    h, h2 = fingerprint.fingerprint_hash(fp), fingerprint.fingerprint_hash(other)
    assert fingerprint.mismatch_warning([(h, fp), (h, fp)]) is None
    assert fingerprint.mismatch_warning([(h, fp), (None, None)]) is None   # старый отчёт — не чужой
    text = fingerprint.mismatch_warning([(h, fp), (h2, other), (None, None)])
    assert "разных машинах" in text and "процессор, диск с данными Р7" in text


def test_machine_dir_name_is_filesystem_safe():
    assert fingerprint.machine_dir_name("ab12cd34ef56", "PC Иванова/7") == "PC_7-ab12cd34ef56"
    assert fingerprint.machine_dir_name(None, "pc") == "pc-nofp"
    assert fingerprint.machine_dir_name("ab12", "") .startswith(fingerprint.hostname()[:1] or "p")


# ── сбор в _capture_environment ──────────────────────────────────────────

def test_capture_environment_adds_fingerprint_and_calibration(bare_r7, tmp_path, monkeypatch, log):
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    monkeypatch.setattr(r7mod.ctypes.windll.shcore, "GetScaleFactorForDevice", lambda idx: 150)
    monkeypatch.setattr(calibration, "cpu_index", lambda **k: 311.0)
    monkeypatch.setattr(calibration, "disk_index", lambda folder, **k: 400.0)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    bare_r7.reports_folder = tmp_path
    bare_r7._cached_cpu_count = 8
    bare_r7._cached_r7_path = None
    info = bare_r7._capture_environment(log_cb=log)
    fp = info["fingerprint"]
    assert fp["cpu_logical"] == 8 and fp["dpi_scale_pct"] == 150
    assert fp["r7_data_drive"] == "C:" and fp["reports_drive"] == tmp_path.drive.upper()
    assert fp["power_plan"] == info["power_plan"]
    assert info["fingerprint_hash"] == fingerprint.fingerprint_hash(fp)
    assert info["calibration"] == {"cpu_ms": 311.0, "disk_mb_s": 400.0}
    assert any("Калибровка стенда: CPU 311 мс, диск 400 МБ/с" in m for m in log.messages)
    json.dumps(info)
    # Прежние ключи на месте — читатели старых отчётов их ждут.
    assert {"system_cpu_pct", "top_processes", "ram_available_gb", "power_plan",
            "warnings", "disk_free_gb"} <= set(info)


def test_capture_environment_survives_failed_calibration(bare_r7, tmp_path, monkeypatch, log):
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    monkeypatch.setattr(calibration, "cpu_index", lambda **k: None)
    monkeypatch.setattr(calibration, "disk_index", lambda folder, **k: None)
    bare_r7.reports_folder = tmp_path
    bare_r7._cached_cpu_count = 8
    bare_r7._cached_r7_path = None
    info = bare_r7._capture_environment(log_cb=log)
    assert info["calibration"] == {"cpu_ms": None, "disk_mb_s": None}
    assert any("Калибровка стенда не удалась" in m for m in log.messages)
    assert info["fingerprint_hash"]
