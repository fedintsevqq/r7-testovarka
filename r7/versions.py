"""Установленная версия Р7: реестр, путь к exe, команда удаления, кэши.

Версия отчёта и exe берутся из одной записи реестра (InstallLocation);
запасные пути принимают только exe с той же ProductVersion. Установка и
удаление с прогрессом в окне пока остаются в R7Testovarka (интерфейс,
этап 4). VersionsMixin — методы, которые R7Testovarka получает наследованием.
"""
import os
import re
import shlex
import shutil
import winreg
from pathlib import Path

from r7 import env, logfile, privileges, settings
from r7.env import win32api


# GUID продукта Windows Installer — ровно 8-4-4-4-12 шестнадцатеричных цифр
# в фигурных скобках. Любой другой символ внутри — не GUID, а попытка
# протащить в командную строку что-то своё.
_MSI_PRODUCT_GUID_RE = re.compile(
    r"^\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}$", re.I)
# Ключи msiexec, которые разрешены в команде удаления помимо /X{GUID}.
_MSIEXEC_ALLOWED_FLAGS = ("/quiet", "/qn", "/norestart")
_MSIEXEC_QUIET_FLAGS = ("/quiet", "/qn")


def _msiexec_paths(system_root=None):
    """Нормализованные пути к настоящему msiexec.exe (System32 и SysWOW64)."""
    root = system_root or os.environ.get("SystemRoot") or r"C:\Windows"
    return tuple(os.path.normcase(os.path.join(root, sub, "msiexec.exe"))
                 for sub in ("System32", "SysWOW64"))


def validate_uninstall_command(cmd, system_root=None):
    """Проверяет команду удаления из реестра и собирает аргументы для Popen.

    Запись Uninstall в реестре пишет установщик, а ветку HKCU может
    переписать любая программа без прав. Инструмент же работает от
    администратора, поэтому выполнять оттуда что попало нельзя. Принимается
    только Windows Installer: msiexec из %SystemRoot%\\System32 (или SysWOW64)
    с одним ключом /X{GUID} (или /I{GUID} — он переписывается в /X, иначе
    с /quiet получился бы тихий ремонт вместо удаления) и, по желанию,
    /quiet, /qn, /norestart. Всё остальное — ValueError с объяснением.

    Args:
        cmd: строка UninstallString / QuietUninstallString из реестра.
        system_root: подмена %SystemRoot% для тестов.

    Returns:
        list[str]: [полный путь к msiexec.exe, "/X{GUID}", "/quiet", "/norestart"].

    Raises:
        ValueError: команда не прошла проверку (в тексте — почему).
    """
    if not cmd or not str(cmd).strip():
        raise ValueError("Команда удаления из реестра отклонена: она пуста")
    try:
        tokens = shlex.split(str(cmd), posix=False)
    except ValueError as e:
        raise ValueError(f"Команда удаления из реестра отклонена: не разобрать кавычки ({e})")
    if not tokens:
        raise ValueError("Команда удаления из реестра отклонена: она пуста")

    exe = tokens[0].strip('"')
    exe_name = os.path.basename(exe).lower()
    if exe_name not in ("msiexec", "msiexec.exe"):
        raise ValueError(
            f"Команда удаления из реестра отклонена: ожидался msiexec, а записан «{exe}». "
            f"Инструмент работает от администратора и не запускает произвольные программы "
            f"из реестра")
    if os.path.dirname(exe):
        full = os.path.normcase(os.path.abspath(os.path.expandvars(exe)))
        if not full.lower().endswith(".exe"):
            full += ".exe"
        if full not in _msiexec_paths(system_root):
            raise ValueError(
                f"Команда удаления из реестра отклонена: msiexec ожидается в "
                f"%SystemRoot%\\System32, а записан «{exe}»")
    msiexec = os.path.join(system_root or os.environ.get("SystemRoot") or r"C:\Windows",
                           "System32", "msiexec.exe")

    guid = None
    flags = []
    rest = [t.strip('"') for t in tokens[1:]]
    i = 0
    while i < len(rest):
        tok = rest[i]
        low = tok.lower()
        if low in ("/x", "/i") and i + 1 < len(rest):
            tok, low = tok + rest[i + 1], low + rest[i + 1].lower()
            i += 1
        if low.startswith(("/x", "/i")) and len(low) > 2:
            if guid is not None:
                raise ValueError(
                    "Команда удаления из реестра отклонена: в ней больше одного продукта")
            candidate = tok[2:]
            if not _MSI_PRODUCT_GUID_RE.match(candidate):
                raise ValueError(
                    f"Команда удаления из реестра отклонена: «{candidate}» — не GUID продукта")
            guid = candidate.upper()
        elif low in _MSIEXEC_ALLOWED_FLAGS:
            flags.append(low)
        else:
            raise ValueError(
                f"Команда удаления из реестра отклонена: лишний аргумент «{tok}». "
                f"Разрешены только /X{{GUID}}, /quiet, /qn, /norestart")
        i += 1
    if guid is None:
        raise ValueError(
            "Команда удаления из реестра отклонена: нет ключа /X{GUID} с кодом продукта")

    result = [msiexec, f"/X{guid}"]
    if not any(f in flags for f in _MSIEXEC_QUIET_FLAGS):
        flags.append("/quiet")
    if "/norestart" not in flags:
        flags.append("/norestart")
    return result + flags


def _dir_has_r7_exe(path, depth=2):
    """Есть ли DesktopEditors.exe / editors.exe в папке или её подпапках (до depth)."""
    names = ("desktopeditors.exe", "editors.exe")
    stack = [(path, 0)]
    while stack:
        folder, level = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if entry.is_file(follow_symlinks=False) and entry.name.lower() in names:
                return True
            if entry.is_dir(follow_symlinks=False) and level < depth:
                stack.append((entry.path, level + 1))
    return False


def install_dir_has_r7_exe(location):
    """True, если в папке установки из реестра лежит exe Р7 (проверка до удаления)."""
    if not location:
        return False
    path = Path(os.path.expandvars(str(location).strip().strip('"')))
    return path.is_dir() and _dir_has_r7_exe(path)


def remove_install_dir(location, log_cb, had_exe=False):
    """Удаляет папку установки Р7 из записи реестра — и только её.

    Раньше после msiexec стирались захардкоженные C:\\Program Files\\R7-Office
    и (x86): при установке на другой диск они были ни при чём, а при кривой
    записи в реестре можно было снести чужое. Теперь путь — только
    InstallLocation той же записи, что дала версию, и с предохранителями:
    папка существует; у пути не меньше трёх частей (не корень диска и не
    Program Files целиком); имя папки содержит «r7» или «editors»; это не
    профиль пользователя и не его родитель; внутри до удаления был exe Р7
    (had_exe / install_dir_has_r7_exe) либо папка уже пуста после msiexec.

    Args:
        location: InstallLocation из реестра (может быть None).
        log_cb: куда писать, что удалено и что пропущено.
        had_exe: результат install_dir_has_r7_exe до запуска msiexec.

    Returns:
        bool: True — папка удалена.
    """
    if not location:
        log_cb("ℹ️ В записи реестра нет InstallLocation — папку установки не трогаю")
        return False
    path = Path(os.path.expandvars(str(location).strip().strip('"')))
    if not path.is_dir():
        log_cb(f"ℹ️ Папка установки {path} уже отсутствует")
        return False
    path = Path(os.path.abspath(path))
    if len(path.parts) < 3:
        log_cb(f"⚠️ Папку {path} не удаляю: слишком близко к корню диска")
        return False
    name = path.name.lower()
    if "r7" not in name and "editors" not in name:
        log_cb(f"⚠️ Папку {path} не удаляю: по имени это не папка Р7")
        return False
    try:
        home = Path(os.path.abspath(Path.home()))
        if home == path or path in home.parents:
            log_cb(f"⚠️ Папку {path} не удаляю: это профиль пользователя")
            return False
    except (RuntimeError, OSError):  # профиль не определить — остальные проверки остаются
        pass
    try:
        is_empty = not any(os.scandir(path))
    except OSError as e:
        log_cb(f"⚠️ Папку {path} не прочитать: {e}")
        return False
    if not had_exe and not is_empty and not _dir_has_r7_exe(path):
        log_cb(f"⚠️ Папку {path} не удаляю: в ней нет exe Р7, а пустой она не стала")
        return False
    try:
        shutil.rmtree(path, ignore_errors=False)
    except OSError as e:
        log_cb(f"⚠️ Папка {path} удалена не полностью: {e}")
        return False
    log_cb(f"🗑️ Папка установки {path} удалена")
    return True


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
            "quiet_uninstall_string", "install_location", "registry_hive"}
            для первой найденной записи Р7-Офис, либо None, если ничего не
            найдено. install_location — None, если в записи его нет;
            registry_hive — "HKLM" или "HKCU": откуда запись (HKCU пишется
            без прав, см. validate_uninstall_command).
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
                                "registry_hive": ("HKCU" if root == winreg.HKEY_CURRENT_USER
                                                  else "HKLM"),
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
        """Собирает проверенную команду тихого удаления из данных реестра.

        UninstallString для MSI-пакетов обычно выглядит как
        "MsiExec.exe /I{GUID}" — это задокументированное поведение Windows
        Installer: /I означает «установить/переустановить», и с флагом
        /quiet, добавленным поверх, получается тихий РЕМОНТ установки, а не
        удаление. Настоящая тихая деинсталляция требует /X. Windows отдельно
        хранит QuietUninstallString с уже верным /X{GUID} — используем её,
        если она есть; иначе чиним /I на /X сами.

        Команда проходит validate_uninstall_command: инструмент работает от
        администратора, а запись Uninstall (особенно в HKCU) может написать
        кто угодно, поэтому выполняется только msiexec из System32 с
        /X{GUID}. Записи из HKCU это касается так же: они годятся для
        определения версии, но удаление по ним идёт лишь через msiexec.

        Args:
            info: self.current_version_info.

        Returns:
            list[str]: аргументы для subprocess.Popen(..., shell=False).

        Raises:
            ValueError: команда из реестра не прошла проверку.
        """
        cmd = info.get("quiet_uninstall_string") or info.get("uninstall_string")
        try:
            return validate_uninstall_command(cmd)
        except ValueError as e:
            if info.get("registry_hive") == "HKCU":
                raise ValueError(f"{e}. Запись взята из HKCU — её мог создать кто угодно")
            raise

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
        if not privileges.is_admin():
            # Без прав NtSetSystemInformation откажет в любом случае — не
            # пробуем, одна строка в журнал и метка в «Условия прогона»
            # отчёта (_note_os_cache_not_purged): открытие могло быть тёплым.
            log_cb("⚠️ кэш ОС не сброшен: нет прав администратора")
            self._note_os_cache_not_purged()
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

        Явный путь из r7_settings.json (ключ r7_path) стоит выше реестра:
        на ПК коллеги Р7 может быть установлен без записи в реестре или не
        той версией, что в записи. Где искали, запоминается в
        self._r7_path_searched и пишется в журнал, если ничего не нашлось:
        прежнее «Р7-Офис не найден» не давало подсказки.

        Returns:
            str: Absolute path to DesktopEditors.exe, or None if not found.
        """
        log = logfile.get_logger()
        searched = self._r7_path_searched = []

        custom = settings.get("r7_path")
        if custom:
            exe = Path(os.path.expandvars(str(custom).strip().strip('"')))
            if exe.is_file():
                log.info("путь к Р7 из r7_settings.json: %s", exe)
                self._cached_r7_path = str(exe)
                return str(exe)
            searched.append(f"r7_settings.json: {exe} (файла нет)")
            log.warning("r7_settings.json: путь к Р7 не существует: %s — ищем в реестре", exe)

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
            searched.append(f"реестр (InstallLocation): {location}")
        else:
            searched.append("реестр: записи Р7-Офис с InstallLocation нет")
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
                log.info("путь к Р7 — запасной (реестр без папки): %s", path)
                self._cached_r7_path = path
                return path
        searched.extend(possible_paths)
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
        searched.extend(f"{d}Program Files*\\R7-Office | Р7-Офис (обход)" for d in drives)
        found = [p for p in found if self._exe_matches_version(p, want)]
        if found:
            best = max(found, key=lambda p: p.stat().st_mtime)
            log.info("путь к Р7 — обход Program Files: %s", best)
            self._cached_r7_path = str(best)
            return str(best)
        log.warning("Р7-Офис не найден%s. Искали: %s. Путь можно задать в "
                    "r7_settings.json (ключ r7_path)",
                    f" (версия из реестра {want})" if want else "", "; ".join(searched))
        return None
