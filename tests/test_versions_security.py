"""Безопасность управления версиями: команда удаления из реестра проверяется
до запуска (только msiexec из System32 с /X{GUID}), папка установки
удаляется только та, что в записи реестра, и только с предохранителями.
Живой Р7, реестр и msiexec не нужны."""
import pytest

import r7_Testovarka as r7mod
from r7 import versions as r7versions

GUID = "{0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9}"
SYSROOT = r"C:\Windows"
MSIEXEC = r"C:\Windows\System32\msiexec.exe"


def _validate(cmd):
    return r7versions.validate_uninstall_command(cmd, system_root=SYSROOT)


# ── validate_uninstall_command: что проходит ─────────────────────────────

@pytest.mark.parametrize("cmd, expected", [
    (f"MsiExec.exe /X{GUID}", [MSIEXEC, f"/X{GUID}", "/quiet", "/norestart"]),
    (f"MsiExec.exe /I{GUID}", [MSIEXEC, f"/X{GUID}", "/quiet", "/norestart"]),
    (f"msiexec /x{GUID.lower()}", [MSIEXEC, f"/X{GUID}", "/quiet", "/norestart"]),
    (f"MsiExec.exe /X{GUID} /qn", [MSIEXEC, f"/X{GUID}", "/qn", "/norestart"]),
    (f"MsiExec.exe /X{GUID} /quiet /norestart", [MSIEXEC, f"/X{GUID}", "/quiet", "/norestart"]),
    (f"MsiExec.exe /X {GUID}", [MSIEXEC, f"/X{GUID}", "/quiet", "/norestart"]),
    (f'"C:\\Windows\\System32\\msiexec.exe" /X{GUID}',
     [MSIEXEC, f"/X{GUID}", "/quiet", "/norestart"]),
    (f"C:\\WINDOWS\\SysWOW64\\MsiExec.exe /X{GUID}",
     [MSIEXEC, f"/X{GUID}", "/quiet", "/norestart"]),
    (f"%SystemRoot%\\System32\\msiexec.exe /X{GUID}",
     [MSIEXEC, f"/X{GUID}", "/quiet", "/norestart"]),
])
def test_valid_msiexec_commands_pass(monkeypatch, cmd, expected):
    monkeypatch.setenv("SystemRoot", SYSROOT)
    assert _validate(cmd) == expected


# ── validate_uninstall_command: что отклоняется ──────────────────────────

@pytest.mark.parametrize("cmd, reason", [
    (r"C:\Users\x\evil.exe", "msiexec"),
    (r'"C:\Users\x\evil.exe" /X' + GUID, "msiexec"),
    (r"C:\Program Files\R7\unins000.exe /SILENT", "msiexec"),
    (f"C:\\Tools\\msiexec.exe /X{GUID}", "System32"),
    (f"C:\\Users\\x\\msiexec.exe /X{GUID}", "System32"),
    (f"MsiExec.exe /X{GUID} && C:\\evil.exe", "лишний"),
    (f"MsiExec.exe /X{GUID} | more", "лишний"),
    (f"MsiExec.exe /X{GUID} /l*v C:\\log.txt", "лишний"),
    (f"MsiExec.exe /X{GUID} /X{GUID}", "больше одного"),
    ("MsiExec.exe /X{0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9;calc}", "не GUID"),
    ("MsiExec.exe /X{0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9}}", "не GUID"),
    ("MsiExec.exe /X{0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F}", "не GUID"),
    ("MsiExec.exe /X{0A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F9} /Xextra", "больше одного"),
    ("MsiExec.exe /Xextra", "не GUID"),
    ("MsiExec.exe /quiet", "нет ключа"),
    ("MsiExec.exe", "нет ключа"),
    ("", "пуста"),
    (None, "пуста"),
])
def test_suspicious_commands_are_rejected(cmd, reason):
    with pytest.raises(ValueError, match=reason) as exc:
        _validate(cmd)
    assert "отклонена" in str(exc.value)


# ── registry_hive в записи реестра ───────────────────────────────────────

class _FakeWinreg:
    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0

    def __init__(self, entries):
        self.entries = entries

    def OpenKey(self, parent, sub, *args):
        if isinstance(parent, tuple):
            return ("entry", parent[1][int(sub)])
        items = self.entries.get((parent, sub))
        if items is None:
            raise OSError("нет ключа")
        return ("root", items)

    def QueryInfoKey(self, key):
        return (len(key[1]), 0, 0)

    def EnumKey(self, key, i):
        return str(i)

    def QueryValueEx(self, key, name):
        try:
            return (key[1][name], 1)
        except KeyError:
            raise OSError(name)

    def CloseKey(self, key):
        pass


UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
ENTRY = {"DisplayName": "Р7-Офис. Профессиональный (десктопная версия)",
         "DisplayVersion": "2026.3.2.3229", "UninstallString": f"MsiExec.exe /I{GUID}"}


@pytest.mark.parametrize("hive", ["HKLM", "HKCU"])
def test_registry_reader_records_hive(monkeypatch, hive):
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    monkeypatch.setattr(r7versions, "winreg", _FakeWinreg({(hive, UNINSTALL): [ENTRY]}))
    monkeypatch.setattr(r7mod.R7Testovarka, "_UNINSTALL_REGISTRY_ROOTS",
                        (("HKLM", UNINSTALL), ("HKCU", UNINSTALL)))
    info = app._read_current_version_from_registry()
    assert info["registry_hive"] == hive


# ── remove_install_dir ───────────────────────────────────────────────────

@pytest.fixture
def rm(monkeypatch):
    removed = []
    monkeypatch.setattr(r7versions.shutil, "rmtree",
                        lambda p, ignore_errors=False: removed.append(str(p)))
    return removed


def _r7_dir(tmp_path, name="Editors", exe="DesktopEditors.exe"):
    d = tmp_path / "Program Files" / "R7-Office" / name
    d.mkdir(parents=True)
    if exe:
        (d / exe).write_bytes(b"")
    return d


def test_remove_install_dir_deletes_registry_folder(tmp_path, rm, log):
    d = _r7_dir(tmp_path)
    assert r7versions.remove_install_dir(str(d), log) is True
    assert rm == [str(d)]
    assert any("удалена" in m for m in log.messages)


def test_remove_install_dir_accepts_quotes_and_trailing_slash(tmp_path, rm, log):
    d = _r7_dir(tmp_path, name="Editors-2026.3.2")
    assert r7versions.remove_install_dir(f'"{d}\\"', log) is True
    assert rm == [str(d)]


def test_remove_install_dir_uses_editors_exe_in_subfolder(tmp_path, rm, log):
    d = _r7_dir(tmp_path, exe=None)
    (d / "editors").mkdir()
    (d / "editors" / "editors.exe").write_bytes(b"")
    assert r7versions.remove_install_dir(str(d), log) is True


def test_remove_install_dir_deletes_empty_folder_after_msiexec(tmp_path, rm, log):
    d = _r7_dir(tmp_path, exe=None)
    assert r7versions.remove_install_dir(str(d), log) is True
    assert rm == [str(d)]


def test_remove_install_dir_trusts_exe_seen_before_msiexec(tmp_path, rm, log):
    """exe был до msiexec (had_exe), после остались только хвосты — папку убираем."""
    d = _r7_dir(tmp_path, exe=None)
    (d / "cache.bin").write_bytes(b"x")
    assert r7versions.remove_install_dir(str(d), log, had_exe=True) is True


def test_remove_install_dir_refuses_folder_without_exe(tmp_path, rm, log):
    d = _r7_dir(tmp_path, exe=None)
    (d / "something.txt").write_text("x")
    assert r7versions.remove_install_dir(str(d), log) is False
    assert rm == [] and any("нет exe" in m for m in log.messages)


@pytest.mark.parametrize("location", [r"C:\\", "C:\\Program Files", "C:\\Program Files (x86)"])
def test_remove_install_dir_refuses_roots(location, rm, log):
    assert r7versions.remove_install_dir(location, log) is False
    assert rm == [] and any("не удаляю" in m for m in log.messages)


def test_remove_install_dir_refuses_foreign_name(tmp_path, rm, log):
    d = tmp_path / "Program Files" / "Chrome"
    d.mkdir(parents=True)
    (d / "DesktopEditors.exe").write_bytes(b"")
    assert r7versions.remove_install_dir(str(d), log) is False
    assert rm == [] and any("не папка Р7" in m for m in log.messages)


def test_remove_install_dir_refuses_user_profile(monkeypatch, tmp_path, rm, log):
    home = tmp_path / "Users" / "r7user"
    home.mkdir(parents=True)
    monkeypatch.setattr(r7versions.Path, "home", classmethod(lambda cls: home))
    assert r7versions.remove_install_dir(str(home), log) is False
    assert r7versions.remove_install_dir(str(home.parent), log) is False
    assert rm == []


@pytest.mark.parametrize("location", [None, ""])
def test_remove_install_dir_skips_missing_location(location, rm, log):
    assert r7versions.remove_install_dir(location, log) is False
    assert rm == [] and any("InstallLocation" in m for m in log.messages)


def test_remove_install_dir_skips_absent_folder(tmp_path, rm, log):
    assert r7versions.remove_install_dir(str(tmp_path / "R7-Office" / "Editors"), log) is False
    assert rm == [] and any("отсутствует" in m for m in log.messages)


def test_remove_install_dir_reports_oserror(tmp_path, monkeypatch, log):
    d = _r7_dir(tmp_path)

    def boom(p, ignore_errors=False):
        raise OSError("занята")
    monkeypatch.setattr(r7versions.shutil, "rmtree", boom)
    assert r7versions.remove_install_dir(str(d), log) is False
    assert any("не полностью" in m and "занята" in m for m in log.messages)


def test_install_dir_has_r7_exe(tmp_path):
    assert r7versions.install_dir_has_r7_exe(str(_r7_dir(tmp_path))) is True
    assert r7versions.install_dir_has_r7_exe(str(_r7_dir(tmp_path, "Editors2", exe=None))) is False
    assert r7versions.install_dir_has_r7_exe(None) is False
    assert r7versions.install_dir_has_r7_exe(str(tmp_path / "нет")) is False
