"""_find_r7_path берёт exe из той же записи реестра, что и версию в отчёте.

Стенд 06.10.2026: установлены 2026.3.1 (Editors\\) и 2026.3.2
(Editors-2026.3.2\\). Шапка и отчёты показывали 2026.3.2 — первую запись
реестра, — а запускалась 2026.3.1, у её exe дата изменения новее.
Реестр и диски подменяются, живой Р7 не нужен.
"""
import pytest

import r7_Testovarka as r7mod
from r7 import versions as r7versions  # noqa: E402


# ── Фейковый winreg: ровно те функции, что зовёт _read_current_version_from_registry

class _FakeWinreg:
    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 0

    def __init__(self, entries):
        # entries: {root_path: [{"DisplayName": ..., ...}, ...]}
        self.entries = entries

    def OpenKey(self, parent, sub, *args):
        if isinstance(parent, tuple):            # подключ записи
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


def _entry(version, location=None):
    e = {"DisplayName": "Р7-Офис. Профессиональный (десктопная версия)",
         "DisplayVersion": version, "UninstallString": "unins000.exe"}
    if location is not None:
        e["InstallLocation"] = location
    return e


@pytest.fixture
def app():
    inst = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    inst._cached_r7_path = None
    return inst


def _install(root, name):
    exe = root / name / "DesktopEditors.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


def _registry(monkeypatch, app, info):
    monkeypatch.setattr(app, "_read_current_version_from_registry", lambda: info)


# ── _find_r7_path ─────────────────────────────────────────────────────────

def test_exe_comes_from_registry_install_location(app, tmp_path, monkeypatch):
    old = _install(tmp_path, "Editors")                  # 2026.3.1, exe «свежее»
    new = _install(tmp_path, "Editors-2026.3.2")
    app._cached_r7_path = str(old)
    _registry(monkeypatch, app, {"version": "2026.3.2.3229",
                                 "install_location": str(new.parent) + "\\"})

    assert app._find_r7_path() == str(new)


def test_registry_reread_after_batch_reinstall(app, tmp_path, monkeypatch):
    """Batch ставит версии по очереди — кэш не должен вести в папку прежней."""
    a = _install(tmp_path, "Editors-2026.3.1")
    b = _install(tmp_path, "Editors-2026.3.2")
    info = {"install_location": str(a.parent)}
    monkeypatch.setattr(app, "_read_current_version_from_registry", lambda: info)
    assert app._find_r7_path() == str(a)

    info["install_location"] = str(b.parent)
    assert app._find_r7_path() == str(b)


def test_nested_layout_under_install_location(app, tmp_path, monkeypatch):
    exe = tmp_path / "Editors" / "DesktopEditors" / "DesktopEditors.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    _registry(monkeypatch, app, {"install_location": str(tmp_path / "Editors")})
    assert app._find_r7_path() == str(exe)


def test_missing_exe_in_location_falls_back_to_cache(app, tmp_path, monkeypatch):
    cached = _install(tmp_path, "Editors")
    app._cached_r7_path = str(cached)
    _registry(monkeypatch, app, {"install_location": str(tmp_path / "удалена")})
    assert app._find_r7_path() == str(cached)


def test_stale_cache_is_not_returned(app, tmp_path, monkeypatch):
    """Кэш указывает на удалённую установку, в реестре пусто, на дисках
    ничего — None, а не путь к несуществующему exe."""
    app._cached_r7_path = str(tmp_path / "удалена" / "DesktopEditors.exe")
    _registry(monkeypatch, app, None)
    monkeypatch.setattr(r7mod.Path, "exists", lambda self: False)   # ни типовых путей,
    monkeypatch.setattr(r7versions.os.path, "exists", lambda p: False)   # ни дисков для обхода
    assert app._find_r7_path() is None


@pytest.mark.parametrize("raw", ['"{loc}"', ' {loc} ', r'%R7_TEST_ROOT%\Editors-2026.3.2'])
def test_install_location_quotes_spaces_and_env_vars(app, tmp_path, monkeypatch, raw):
    exe = _install(tmp_path, "Editors-2026.3.2")
    monkeypatch.setenv("R7_TEST_ROOT", str(tmp_path))
    _registry(monkeypatch, app, {"version": "2026.3.2.3229",
                                 "install_location": raw.format(loc=exe.parent)})
    assert app._find_r7_path() == str(exe)


def test_registry_hit_updates_cache(app, tmp_path, monkeypatch):
    exe = _install(tmp_path, "Editors-2026.3.2")
    _registry(monkeypatch, app, {"install_location": str(exe.parent)})
    app._find_r7_path()
    assert app._cached_r7_path == str(exe)     # запасной путь, если реестр потом пуст


def test_cache_of_other_version_is_rejected(app, tmp_path, monkeypatch):
    """В записи нет папки, а кэш ведёт в 2026.3.1 — его не запускать."""
    old = _install(tmp_path, "Editors")
    app._cached_r7_path = str(old)
    _registry(monkeypatch, app, {"version": "2026.3.2.3229", "install_location": None})
    monkeypatch.setattr(r7mod.R7Testovarka, "_exe_version",
                        staticmethod(lambda p: "2026.3.1.3296-2"))
    monkeypatch.setattr(r7mod.Path, "exists", lambda self: str(self) == str(old))
    monkeypatch.setattr(r7versions.os.path, "exists", lambda p: False)
    assert app._find_r7_path() is None


@pytest.mark.parametrize("actual, wanted, ok", [
    ("2026.3.2.3229-1", "2026.3.2.3229", True),     # ProductVersion с суффиксом сборки
    ("2026.3.1.3296-2", "2026.3.2.3229", False),
    ("2026.3.2.3228", "2026.3.2.3229", False),      # FileVersion стенда — не путать
    (None, "2026.3.2.3229", True),                  # ресурсы не читаются — не мешать
    ("2026.3.1.3296-2", None, True),                # версия в реестре неизвестна
])
def test_exe_matches_version(app, monkeypatch, actual, wanted, ok):
    monkeypatch.setattr(r7mod.R7Testovarka, "_exe_version", staticmethod(lambda p: actual))
    assert app._exe_matches_version("x.exe", wanted) is ok


# ── _read_current_version_from_registry: install_location ─────────────────

def test_registry_reader_returns_install_location(app, monkeypatch):
    fake = _FakeWinreg({("HKLM", UNINSTALL): [
        {"DisplayName": "Chrome", "DisplayVersion": "1"},
        _entry("2026.3.2.3229", r"E:\Program Files\R7-Office\Editors-2026.3.2" + "\\"),
        _entry("2026.3.1.3296", r"E:\Program Files\R7-Office\Editors" + "\\"),
    ]})
    monkeypatch.setattr(r7versions, "winreg", fake)
    monkeypatch.setattr(r7mod.R7Testovarka, "_UNINSTALL_REGISTRY_ROOTS", (("HKLM", UNINSTALL),))

    info = app._read_current_version_from_registry()

    # Та же запись, что даёт версию, — и папка её же.
    assert info["version"] == "2026.3.2.3229"
    assert info["install_location"].endswith("Editors-2026.3.2\\")


@pytest.mark.parametrize("value", ["", None])
def test_registry_reader_empty_or_missing_install_location(app, monkeypatch, value):
    entry = _entry("2026.3.2.3229", value) if value is not None else _entry("2026.3.2.3229")
    fake = _FakeWinreg({("HKLM", UNINSTALL): [entry]})
    monkeypatch.setattr(r7versions, "winreg", fake)
    monkeypatch.setattr(r7mod.R7Testovarka, "_UNINSTALL_REGISTRY_ROOTS", (("HKLM", UNINSTALL),))
    assert app._read_current_version_from_registry()["install_location"] is None


def test_registry_reader_without_install_location(app, monkeypatch):
    fake = _FakeWinreg({("HKLM", UNINSTALL): [_entry("2026.3.2.3229")]})
    monkeypatch.setattr(r7versions, "winreg", fake)
    monkeypatch.setattr(r7mod.R7Testovarka, "_UNINSTALL_REGISTRY_ROOTS", (("HKLM", UNINSTALL),))

    info = app._read_current_version_from_registry()

    assert info["version"] == "2026.3.2.3229"
    assert info["install_location"] is None
