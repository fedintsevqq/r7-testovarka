"""Установленная версия Р7: реестр, путь к exe, команда удаления, кэши.

Версия отчёта и exe берутся из одной записи реестра (InstallLocation);
запасные пути принимают только exe с той же ProductVersion. Установка и
удаление с прогрессом в окне пока остаются в R7Testovarka (интерфейс,
этап 4). VersionsMixin — методы, которые R7Testovarka получает наследованием.
"""
import os
import re
import shutil
import winreg
from pathlib import Path

from r7 import env
from r7.env import win32api



def version_label(info):
    """Подпись версии для отчётов: название из реестра и номер сборки.

    DisplayName у Р7 до 2026.2 содержал номер («… 2026.2.2.2923 (x64)»), у
    2026.3 — нет («Р7-Офис. Профессиональный (десктопная версия)»), и все
    отчёты новых сборок были подписаны одинаково: в сравнении и трендах
    версии не различались (07.10.2026, 53 отчёта). Номер дописывается, только
    если его в названии нет — подписи старых сборок не меняются.

    Args:
        info: результат _read_current_version_from_registry (name, version) или None.
    """
    if not info:
        return None
    name = (info.get("name") or "").strip()
    ver = (info.get("version") or "").strip()
    if not ver or ver in name:
        return name or ver or None
    return f"{name} {ver}" if name else ver

class VersionsMixin:
    """Реестр, путь к exe Р7, команда удаления, кэши — часть R7Testovarka."""

    # ---------------------- Управление версиями ----------------------
    # Реестр читается и для HKLM (машинные установки, 64- и 32-битная ветка),
    # и для HKCU (установка "только для текущего пользователя") — раньше
    # HKCU не проверялся вовсе, и такие установки Р7-Офис не находились.
    _UNINSTALL_REGISTRY_ROOTS = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )

    def _read_current_version_from_registry(self):
        """Чистое чтение реестра — без обращения к виджетам Tk.

        winreg — обычный Python-модуль, потокобезопасен как любой другой;
        в отличие от него, Tk-виджеты (self.lbl_current) не гарантированно
        безопасны для изменения не из главного потока. Разделение на «прочитать»
        (эта функция, любой поток) и «показать» (detect_current_version,
        только главный поток) нужно ровно поэтому — метод вызывается и из
        главного потока (при старте), и из фоновых (_batch_worker,
        install_selected.worker).

        Returns:
            dict | None: {"name", "version", "uninstall_string",
            "quiet_uninstall_string", "install_location"} для первой
            найденной записи Р7-Офис, либо None, если ничего не найдено.
            install_location — None, если в записи его нет.
        """
        for root, reg_path in self._UNINSTALL_REGISTRY_ROOTS:
            try:
                key = winreg.OpenKey(root, reg_path, 0, winreg.KEY_READ)
            except OSError:
                continue
            try:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub = winreg.EnumKey(key, i)
                    except OSError:
                        continue
                    try:
                        subkey = winreg.OpenKey(key, sub)
                    except OSError:
                        continue
                    try:
                        name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                        if "Р7-Офис" in name or "R7-Office" in name:
                            ver = winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                            info = {
                                "name": name,
                                "version": ver,
                                "uninstall_string": winreg.QueryValueEx(subkey, "UninstallString")[0],
                            }
                            try:
                                info["quiet_uninstall_string"] = \
                                    winreg.QueryValueEx(subkey, "QuietUninstallString")[0]
                            except OSError:
                                info["quiet_uninstall_string"] = None
                            # Папка установки той же записи — по ней
                            # _find_r7_path запускает ровно эту версию.
                            try:
                                info["install_location"] = winreg.QueryValueEx(
                                    subkey, "InstallLocation")[0] or None
                            except OSError:
                                info["install_location"] = None
                            return info
                    except OSError:  # запись реестра без нужных полей — смотрим следующую
                        pass
                    finally:
                        winreg.CloseKey(subkey)
            finally:
                winreg.CloseKey(key)
        return None

    def _build_uninstall_command(self, info):
        """Строит команду тихого удаления из данных реестра.

        UninstallString для MSI-пакетов обычно выглядит как
        "MsiExec.exe /I{GUID}" — это задокументированное поведение Windows
        Installer: /I означает «установить/переустановить», и с флагом
        /quiet, добавленным поверх, получается тихий РЕМОНТ установки, а не
        удаление. Настоящая тихая деинсталляция требует /X. Windows отдельно
        хранит QuietUninstallString с уже верным /X{GUID} — используем её,
        если она есть; иначе чиним /I на /X сами.

        Args:
            info: self.current_version_info.

        Returns:
            str: Готовая командная строка для subprocess.Popen(..., shell=False).
        """
        quiet_str = info.get("quiet_uninstall_string")
        if quiet_str:
            return quiet_str
        cmd = info["uninstall_string"]
        if "msiexec" in cmd.lower():
            fixed = re.sub(r'(?i)/I(\{[0-9A-Fa-f-]+\})', r'/X\1', cmd)
            cmd = fixed
        return cmd + " /quiet /norestart"

    def _purge_os_file_cache(self, log_cb=None):
        """Сбрасывает standby-список памяти Windows (файловый кэш ОС).

        NtSetSystemInformation(SystemMemoryListInformation=80,
        MemoryPurgeStandbyList=4) — тот же вызов, что у RAMMap «Empty Standby
        List». Нужна привилегия SeProfileSingleProcessPrivilege, она есть у
        администратора, но по умолчанию выключена — включаем.

        Returns:
            bool: True — кэш сброшен.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if not self.PURGE_OS_FILE_CACHE or os.name != "nt":
            return False
        try:
            import ctypes
            from ctypes import wintypes

            advapi = ctypes.WinDLL("advapi32", use_last_error=True)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            ntdll = ctypes.WinDLL("ntdll")

            class LUID(ctypes.Structure):
                _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

            class TOKEN_PRIVILEGES(ctypes.Structure):
                _fields_ = [("PrivilegeCount", wintypes.DWORD),
                            ("Luid", LUID), ("Attributes", wintypes.DWORD)]

            TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY = 0x20, 0x8
            token = wintypes.HANDLE()
            # Без argtypes ctypes передаёт дескриптор как int32, а псевдо-
            # дескриптор текущего процесса (-1 в 64 битах) туда не влезает.
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                ctypes.POINTER(wintypes.HANDLE)]
            advapi.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                                     ctypes.POINTER(LUID)]
            advapi.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL,
                                                     ctypes.POINTER(TOKEN_PRIVILEGES),
                                                     wintypes.DWORD, ctypes.c_void_p,
                                                     ctypes.c_void_p]
            ntdll.NtSetSystemInformation.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                                     wintypes.ULONG]
            ntdll.NtSetSystemInformation.restype = ctypes.c_long
            if not advapi.OpenProcessToken(kernel.GetCurrentProcess(),
                                           TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                           ctypes.byref(token)):
                raise OSError(ctypes.get_last_error(), "OpenProcessToken")
            try:
                luid = LUID()
                if not advapi.LookupPrivilegeValueW(None, "SeProfileSingleProcessPrivilege",
                                                    ctypes.byref(luid)):
                    raise OSError(ctypes.get_last_error(), "LookupPrivilegeValue")
                tp = TOKEN_PRIVILEGES(1, luid, 0x2)   # SE_PRIVILEGE_ENABLED
                ctypes.set_last_error(0)
                advapi.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
                if ctypes.get_last_error() != 0:        # ERROR_NOT_ALL_ASSIGNED и т.п.
                    raise OSError(ctypes.get_last_error(), "AdjustTokenPrivileges")
            finally:
                kernel.CloseHandle(token)

            cmd = ctypes.c_int(4)   # MemoryPurgeStandbyList
            status = ntdll.NtSetSystemInformation(80, ctypes.byref(cmd), ctypes.sizeof(cmd))
            if status != 0:
                raise OSError(status & 0xFFFFFFFF, "NtSetSystemInformation")
            log_cb("🧊 Файловый кэш ОС сброшен (настоящий холодный старт)")
            return True
        except Exception as e:
            log_cb(f"⚠️ Файловый кэш ОС не сброшен ({e}) — холодный старт будет "
                   f"тёплым по кэшу ОС, первый запуск стоит отбросить")
            return False

    def _kill_r7_processes_for_test(self):
        """Завершает все процессы Р7-Офис перед «тестом своего файла».

        Раньше здесь была своя маска по подстроке ("editors_helper",
        "desktopeditors", ...), и под неё не попадал главный процесс
        editors.exe — тот самый, что перезапускает убитые editors_helper.exe
        (QA-аудит 29.09.2026, G-03; тот же класс бага уже чинили в
        _R7_PROCESS_NAMES). Старый Р7 переживал «убийство», новый файл уходил
        в его окно, и холодный старт измерялся как тёплый. Теперь — общий
        поиск процессов Р7 (_matches_r7_process) и _terminate_r7_processes,
        который завершает родителя первым.

        Returns:
            int: сколько процессов Р7 было найдено для завершения.
        """
        if not env.PSUTIL_OK:
            return 0
        silent = lambda _m: None  # noqa: E731
        self._r7_pids = None
        found = len(self._get_r7_processes(log_cb=silent, fresh=True))
        if found:
            self._r7_pids = None
            self._terminate_r7_processes(log_cb=silent)
        return found

    def _clear_r7_cache(self):
        """Removes R7-Office temp items from %%TEMP%%. Returns count removed."""
        cleared = 0
        temp_dir = Path(os.environ.get("TEMP", ""))
        if not temp_dir.exists():
            return 0
        # "R7*" не нужен отдельно от "r7*": glob на Windows регистронезависим,
        # так что оба паттерна и так матчат одни и те же файлы — второй
        # проход просто не находит ничего (первый уже всё удалил).
        for pat in ("r7*", "editors*"):
            for item in temp_dir.glob(pat):
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                    cleared += 1
                except Exception:  # файл занят Р7 или уже удалён — чистка по возможности
                    pass
        return cleared

    @staticmethod
    def _exe_version(path):
        """ProductVersion из ресурсов файла («2026.3.2.3229-1»), None — не прочитать.

        Не FileVersion: на стенде 06.10.2026 у 2026.3.2 он «…3228», а в
        реестре и в ProductVersion — «…3229».
        """
        if not env.WIN32_OK:
            return None
        # Translation у DesktopEditors.exe указывает на 041904e3, а строки
        # лежат под 040904e4 — поэтому после ключей из Translation перебор
        # типовых.
        keys = []
        try:
            keys = [f"{lang:04x}{cp:04x}" for lang, cp in
                    win32api.GetFileVersionInfo(str(path), "\\VarFileInfo\\Translation")]
        except Exception:  # нет Translation — ниже перебор типовых кодовых страниц
            pass
        for key in keys + ["040904e4", "040904b0", "041904e3", "041904b0"]:
            try:
                value = win32api.GetFileVersionInfo(
                    str(path), f"\\StringFileInfo\\{key}\\ProductVersion")
            except Exception:
                continue
            if value:
                return value
        return None

    def _exe_matches_version(self, exe, version):
        """True, если exe той же версии, что запись реестра.

        Неизвестная версия (нет записи в реестре или не читаются ресурсы
        файла) проверку не проваливает — иначе Р7 без записи в реестре не
        нашёлся бы вовсе. Сравниваются первые четыре числовых компонента:
        DisplayVersion «2026.3.2.3229», ProductVersion бывает «…-1».
        """
        if not version:
            return True
        actual = self._exe_version(exe)
        if actual is None:
            return True
        def _parts(v):
            return re.findall(r"\d+", v)[:4]
        return _parts(actual) == _parts(version)

    def _find_r7_path(self):
        """Locates the R7-Office desktop executable, caching the result.

        Сначала — папка установки из той же записи реестра, что даёт версию
        в шапке и в отчётах (_read_current_version_from_registry). Раньше
        путь искался отдельно, и при двух установленных версиях (стенд
        06.10.2026: 2026.3.1 в папке Editors, 2026.3.2 — в Editors-2026.3.2)
        запускалась 2026.3.1 — у её exe дата новее, — а отчёт подписывался
        2026.3.2. Реестр читается при каждом вызове: Batch ставит версии по
        очереди, и закэшированный путь вёл бы в папку прежней.

        Если в записи нет папки (или exe в ней нет), запасные пути — кэш,
        типовые каталоги, обход Program Files — принимают только exe той же
        версии, что в реестре (_exe_matches_version). Не нашлось такого —
        None: «Р7 не найден» честнее, чем замер другой версии под чужой
        подписью.

        Returns:
            str: Absolute path to DesktopEditors.exe, or None if not found.
        """
        reg = self._read_current_version_from_registry() or {}
        want = reg.get("version")

        def _fits(exe):
            # Запасные пути не должны молча запускать другую версию: если
            # версия из реестра известна, exe принимается только с той же.
            return exe.exists() and self._exe_matches_version(exe, want)

        location = reg.get("install_location")
        if location:
            # InstallLocation бывает в кавычках и с %VAR% (REG_EXPAND_SZ).
            location = os.path.expandvars(location.strip().strip('"'))
            for exe in (Path(location) / "DesktopEditors.exe",
                        Path(location) / "DesktopEditors" / "DesktopEditors.exe"):
                if exe.exists():
                    self._cached_r7_path = str(exe)
                    return str(exe)
        if self._cached_r7_path and _fits(Path(self._cached_r7_path)):
            return self._cached_r7_path
        # Реальная раскладка установки: ...\R7-Office\Editors\DesktopEditors.exe
        # Вложенной папки DesktopEditors\ не существует — прежний список путей
        # промахивался всеми четырьмя вариантами, и поиск каждый раз уходил в
        # запасной rglob по всему Program Files. Тот отрабатывал (0.4 сек на
        # тестовой машине), но только потому, что каталог R7-Office попадается
        # обходу рано; при другом порядке имён или установке в Program Files
        # (x86) это полный обход дерева.
        possible_paths = [
            r"C:\Program Files\R7-Office\Editors\DesktopEditors.exe",
            r"C:\Program Files (x86)\R7-Office\Editors\DesktopEditors.exe",
            r"C:\Program Files\Р7-Офис\Editors\DesktopEditors.exe",
            r"C:\Program Files (x86)\Р7-Офис\Editors\DesktopEditors.exe",
            # Раскладки других сборок — оставлены как запасные варианты.
            r"C:\Program Files\R7-Office\Editors\DesktopEditors\DesktopEditors.exe",
            r"C:\Program Files (x86)\R7-Office\Editors\DesktopEditors\DesktopEditors.exe",
        ]
        for path in possible_paths:
            if _fits(Path(path)):
                self._cached_r7_path = path
                return path
        # Запасной поиск: каталоги Р7 в Program Files на ЛЮБОМ диске. Раньше
        # смотрели только C:, и установка вида
        # E:\Program Files\R7-Office\Editors-2026.3.2\DesktopEditors.exe
        # не находилась вовсе (аудит
        # 29.09.2026). Из нескольких найденных берём самую свежую по mtime.
        # Пустые каталоги после удаления (Editors\editors без exe) rglob
        # просто пропускает.
        found = []
        drives = [f"{d}:\\" for d in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{d}:\\")]
        for drive in drives:
            for pf in ("Program Files", "Program Files (x86)"):
                for brand in ("R7-Office", "Р7-Офис"):
                    base = Path(drive) / pf / brand
                    if not base.exists():
                        continue
                    try:
                        found.extend(base.rglob("DesktopEditors.exe"))
                    except OSError:  # папка недоступна — ищем в остальных
                        pass
        found = [p for p in found if self._exe_matches_version(p, want)]
        if found:
            best = max(found, key=lambda p: p.stat().st_mtime)
            self._cached_r7_path = str(best)
            return str(best)
        return None
