"""Тип установщика и ключи тихой установки (r7.installers) и их применение в
install_version: /quiet годится только msiexec, Inno Setup и NSIS — свои ключи,
неизвестный .exe ставится без тихих ключей с предупреждением."""
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
from r7 import installers


def _fake(tmp_path, name, body):
    p = tmp_path / name
    p.write_bytes(body)
    return p


@pytest.mark.parametrize("name, body, kind", [
    ("r7.msi", b"\xd0\xcf\x11\xe0 anything", "msi"),
    ("R7.MSI", b"", "msi"),
    ("r7-inno.exe", b"MZ" + b"\0" * 500 + b"Inno Setup Setup Data (6.2.0)" + b"\0" * 100, "inno"),
    ("r7-nsis.exe", b"MZ" + b"\0" * 64 + b"Nullsoft Install System v3.08", "nsis"),
    ("r7-plain.exe", b"MZ" + b"\0" * 4096, "unknown"),
    ("readme.txt", b"Inno Setup", "unknown"),
])
def test_detect_installer_kind(tmp_path, name, body, kind):
    assert installers.detect_installer_kind(_fake(tmp_path, name, body)) == kind


def test_marker_across_chunk_boundary_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(installers, "_CHUNK", 1024)
    body = b"MZ" + b"\0" * (1024 - 2 - 4) + b"Inno Setup" + b"\0" * 64
    assert installers.detect_installer_kind(_fake(tmp_path, "r7.exe", body)) == "inno"


def test_marker_beyond_scan_limit_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(installers, "INSTALLER_SCAN_LIMIT", 2048)
    body = b"MZ" + b"\0" * 4096 + b"Inno Setup"
    assert installers.detect_installer_kind(_fake(tmp_path, "r7.exe", body)) == "unknown"


def test_missing_file_is_unknown(tmp_path):
    assert installers.detect_installer_kind(tmp_path / "нет.exe") == "unknown"


@pytest.mark.parametrize("kind, args", [
    ("msi", ["/quiet", "/norestart"]),
    ("inno", ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]),
    ("nsis", ["/S"]),
    ("unknown", []),
    ("что-то", []),
])
def test_silent_args(kind, args):
    assert installers.silent_args(kind) == args


def test_silent_args_returns_copy():
    installers.silent_args("inno").append("/X")
    assert installers.silent_args("inno") == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]


# ── install_version выбирает ключи по типу ───────────────────────────────

class _Proc:
    returncode = 0

    def __init__(self):
        self.timeouts = []

    def wait(self, timeout=None):
        self.timeouts.append(timeout)

    def kill(self):
        pass


@pytest.fixture
def inst(bare_r7, monkeypatch):
    env = {"popen": [], "log": [], "proc": _Proc()}
    bare_r7.status_var = Mock()
    bare_r7.add_test_log = env["log"].append
    bare_r7.detect_current_version = Mock()
    monkeypatch.setattr(r7mod.subprocess, "Popen",
                        lambda cmd, shell=False: env["popen"].append(cmd) or env["proc"])
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: None)
    return env


def test_install_inno_exe_uses_verysilent(bare_r7, inst, tmp_path):
    dist = _fake(tmp_path, "r7-office.exe", b"MZ" + b"\0" * 100 + b"Inno Setup" + b"\0" * 100)
    assert bare_r7.install_version(dist) is True
    assert inst["popen"][0] == [str(dist), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
    assert inst["proc"].timeouts == [bare_r7._INSTALL_QUIET_TIMEOUT_SEC]
    assert inst["log"] == []


def test_install_nsis_exe_uses_s(bare_r7, inst, tmp_path):
    dist = _fake(tmp_path, "r7-office.exe", b"MZ" + b"Nullsoft" + b"\0" * 100)
    assert bare_r7.install_version(dist) is True
    assert inst["popen"][0] == [str(dist), "/S"]


def test_install_unknown_exe_warns_and_waits_for_wizard(bare_r7, inst, tmp_path):
    dist = _fake(tmp_path, "r7-office.exe", b"MZ" + b"\0" * 100)
    assert bare_r7.install_version(dist) is True
    assert inst["popen"][0] == [str(dist)]                      # без /quiet
    assert inst["proc"].timeouts == [bare_r7._INSTALL_INTERACTIVE_TIMEOUT_SEC]
    assert any("не распознан" in m for m in inst["log"])


def test_install_msi_quiet_has_no_duplicate_norestart(bare_r7, inst, tmp_path):
    dist = _fake(tmp_path, "r7-office.msi", b"")
    assert bare_r7.install_version(dist) is True
    assert inst["popen"][0] == ["msiexec", "/i", str(dist), "/norestart", "/quiet"]


def test_install_interactive_adds_no_silent_args(bare_r7, inst, tmp_path):
    dist = _fake(tmp_path, "r7-office.exe", b"MZ" + b"Inno Setup")
    assert bare_r7.install_version(dist, quiet=False) is True
    assert inst["popen"][0] == [str(dist)]
    assert inst["proc"].timeouts == [bare_r7._INSTALL_INTERACTIVE_TIMEOUT_SEC]
