"""Плагины тестов: операции из plugins/*.py рядом с программой
(docs/plan-to-20.md, этап 5, пункт 3; контракт — docs/plugins.md).

Новый тест без правки ядра: файл в папке plugins/ объявляет

    def register(ops):          # ops — r7_ops.SpreadsheetOps текущего прогона
        return [(имя, функция с .prepare), ...]

по тому же контракту, что SpreadsheetOps.tests(): одна операция в замере,
подготовка .prepare вне замера (обязательна — правило 3), необязательная
уборка .cleanup после всех повторов, вид .kind — "edit" (по умолчанию) или
"export". Плагин пользуется публичным API ops (cdp_call, prepare_work_sheet,
hotkey, pace …), а не приватными методами приложения.

БЕЗОПАСНОСТЬ. Плагин — обычный код Python: он выполняется с правами
инструмента, а инструмент обычно запущен от администратора. Класть в
plugins/ только файлы из доверенного источника (репозиторий команды).
Выключить плагины: "plugins_enabled": false в r7_settings.json или ключ
--no-plugins командной строки.

Сломанный плагин не роняет запуск: ошибка импорта, нет register, register
бросил, неверная запись — предупреждение в журнал, остальное работает.
Модули импортируются один раз за процесс (кэш по папке); register зовётся
на каждый SpreadsheetOps, чтобы функции были привязаны к его ops.

Модуль не импортирует tkinter и ничего не берёт из r7_Testovarka;
r7_ops — лениво (он сам импортирует этот модуль).
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from r7 import config, logfile, settings

LogCb = Callable[[str], object]

PLUGINS_SUBDIR = "plugins"
MAX_NAME_LEN = 80
KIND_EDIT, KIND_EXPORT = "edit", "export"
KINDS = (KIND_EDIT, KIND_EXPORT)
PLUGIN_MARK = "плагин"        # пометка в списке тестов вкладки

_MODULE_PREFIX = "r7_plugin_"
_cache: dict[str, list[LoadedPlugin]] = {}   # папка → плагины
_process = {"cli_disabled": False}   # --no-plugins на весь процесс


@dataclass(frozen=True)
class LoadedPlugin:
    """Импортированный файл плагина."""
    file: str                 # имя файла: example_bold_column.py
    module: ModuleType


@dataclass(frozen=True)
class PluginTest:
    """Проверенный тест плагина."""
    name: str
    fn: object                # функция без аргументов с .prepare
    file: str
    kind: str = KIND_EDIT


def plugins_dir() -> Path:
    """Папка плагинов рядом с программой (в сборке — рядом с exe):
    читается при вызове, чтобы тесты могли подменить config.BASE_DIR."""
    return config.BASE_DIR / PLUGINS_SUBDIR


def disable_for_process() -> None:
    """--no-plugins: плагины выключены до конца процесса, что бы ни было
    в r7_settings.json."""
    _process["cli_disabled"] = True


def plugins_enabled() -> bool:
    """Включены ли плагины: --no-plugins сильнее настройки plugins_enabled."""
    if _process["cli_disabled"]:
        return False
    return bool(settings.get("plugins_enabled"))


def reset_cache() -> None:
    """Забыть импортированные модули (тесты и смена папки)."""
    for plugins in _cache.values():
        for p in plugins:
            sys.modules.pop(p.module.__name__, None)
    _cache.clear()


def _warn(log_cb: LogCb | None, msg: str) -> None:
    """Предупреждение о плагине — в файловый журнал и в журнал прогона."""
    logfile.get_logger().warning("%s", msg)
    if log_cb is not None:
        try:
            log_cb(f"⚠️ {msg}")
        except Exception:  # журнал окна закрыт — файловый журнал уже записан
            pass


def _module_name(path: Path) -> str:
    """Уникальное имя модуля: два plugins/ (тесты, разные BASE_DIR) и файлы
    с одинаковой основой не затирают друг друга в sys.modules."""
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
    stem = re.sub(r"\W", "_", path.stem)
    return f"{_MODULE_PREFIX}{stem}_{digest}"


def _import_plugin(path: Path) -> ModuleType:
    """Импортирует один файл в изоляции. Бросает всё, что бросил импорт."""
    name = _module_name(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"не удалось создать спецификацию модуля для {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # dataclasses и pickle ищут модуль здесь
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def load_plugins(folder: str | Path | None = None,
                 log_cb: LogCb | None = None) -> list[LoadedPlugin]:
    """Импортированные плагины папки (по имени файла), с кэшем на процесс.

    Берутся только *.py (не *.py.txt, не подпапки, не файлы на «_»).
    Плагины выключены или папки нет — пустой список. Файл с ошибкой импорта
    или без функции register пропускается с предупреждением.
    """
    if not plugins_enabled():
        return []
    folder = Path(folder) if folder else plugins_dir()
    key = str(folder.resolve()) if folder.exists() else str(folder)
    if key in _cache:
        return _cache[key]
    loaded: list[LoadedPlugin] = []
    files = sorted(folder.glob("*.py")) if folder.is_dir() else []
    for path in files:
        if path.name.startswith("_") or not path.is_file():
            continue
        try:
            module = _import_plugin(path)
        except (Exception, SystemExit) as e:   # sys.exit() в плагине тоже не роняет запуск
            logfile.get_logger().exception("плагин %s не загружен", path.name)
            _warn(log_cb, f"Плагин {path.name} не загружен: {type(e).__name__}: {e}")
            continue
        if not callable(getattr(module, "register", None)):
            _warn(log_cb, f"Плагин {path.name} пропущен: нет функции register(ops)")
            sys.modules.pop(module.__name__, None)
            continue
        loaded.append(LoadedPlugin(path.name, module))
    if loaded:
        logfile.get_logger().warning(
            "загружены плагины тестов (выполняются с правами инструмента): %s",
            ", ".join(p.file for p in loaded))
    _cache[key] = loaded
    return loaded


def collect_tests(ops: Any, builtin_names: Iterable[str], folder: str | Path | None = None,
                  log_cb: LogCb | None = None) -> list[PluginTest]:
    """Тесты всех плагинов для этого ops, проверенные по контракту.

    Args:
        ops: r7_ops.SpreadsheetOps — передаётся в register каждого плагина.
        builtin_names: имена встроенных тестов: совпадение — тест пропущен.
        folder: папка плагинов (по умолчанию plugins_dir()).
        log_cb: журнал прогона для предупреждений.

    Returns:
        list[PluginTest]: в порядке файлов и записей внутри файла. У каждой
        функции выставлены .plugin (имя файла), .kind и .mutates.
    """
    taken = set(builtin_names)
    out: list[PluginTest] = []
    for plugin in load_plugins(folder, log_cb):
        try:
            entries = plugin.module.register(ops)
        except Exception as e:
            logfile.get_logger().exception("register() плагина %s упал", plugin.file)
            _warn(log_cb, f"Плагин {plugin.file}: register(ops) упал — "
                          f"{type(e).__name__}: {e}")
            continue
        if not isinstance(entries, (list, tuple)):
            _warn(log_cb, f"Плагин {plugin.file}: register(ops) должен вернуть список "
                          f"(имя, функция), а вернул {type(entries).__name__}")
            continue
        for entry in entries:
            test = _validate_entry(plugin.file, entry, taken, log_cb)
            if test is not None:
                taken.add(test.name)
                out.append(test)
    return out


def _validate_entry(file: str, entry: Any, taken: set[str],
                    log_cb: LogCb | None) -> PluginTest | None:
    """PluginTest из записи (имя, функция) или None с предупреждением."""
    if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
        _warn(log_cb, f"Плагин {file}: запись {entry!r} — не пара (имя, функция), пропущена")
        return None
    name, fn = entry
    problem = _entry_problem(name, fn, taken)
    if problem:
        _warn(log_cb, f"Плагин {file}: тест {name!r} пропущен — {problem}")
        return None
    kind = getattr(fn, "kind", KIND_EDIT)
    try:
        fn.plugin = file
        fn.kind = kind
        if getattr(fn, "mutates", None) is None:
            # Экспорт документ не меняет; правка — должна (предохранитель
            # «история правок не сдвинулась» в _measure_one_run).
            fn.mutates = kind == KIND_EDIT
    except (AttributeError, TypeError) as e:
        _warn(log_cb, f"Плагин {file}: тесту {name!r} не задать атрибуты ({e}) — "
                      f"оберните операцию в lambda или def")
        return None
    return PluginTest(name=name, fn=fn, file=file, kind=kind)


def _entry_problem(name: Any, fn: Any, taken: set[str]) -> str | None:
    """Почему запись не годится, или None."""
    if not isinstance(name, str) or not name.strip():
        return "имя — непустая строка"
    if name != name.strip():
        return "пробелы в начале или конце имени"
    if len(name) > MAX_NAME_LEN:
        return f"имя длиннее {MAX_NAME_LEN} символов"
    if name in taken:
        return "имя уже занято встроенным тестом или другим плагином"
    if not callable(fn):
        return "вторым элементом должна быть функция без аргументов"
    if not callable(getattr(fn, "prepare", None)):
        return ("нет подготовки .prepare: лист и выделение задаются вне замера "
                "(CLAUDE.md, правило 3)")
    cleanup = getattr(fn, "cleanup", None)
    if cleanup is not None and not callable(cleanup):
        return ".cleanup задан, но это не функция"
    kind = getattr(fn, "kind", KIND_EDIT)
    if kind not in KINDS:
        return f".kind = {kind!r}, допустимо {', '.join(KINDS)}"
    mutates = getattr(fn, "mutates", None)
    if mutates is not None and not isinstance(mutates, bool):
        return f".mutates — True, False или не задан, а не {mutates!r}"
    return None


class PluginsMixin:
    """Действующий список тестов (встроенные + плагины) — часть R7Testovarka.

    TEST_DEFINITIONS остаётся списком встроенных тестов; всё, что выбирает
    тест по имени (вкладка, selected_tests.json, наборы, CLI, сценарии),
    берёт effective_test_definitions().
    """
    # Задаются в R7Testovarka; здесь только типы (значений нет — MRO не задет).
    TEST_DEFINITIONS: list[str]
    EXPORT_TESTS: set[str]

    def plugin_tests(self) -> list[PluginTest]:
        """[PluginTest] — один раз на экземпляр: register зовётся на
        SpreadsheetOps без окна и файла, только ради имён и видов."""
        cached: list[PluginTest] | None = getattr(self, "_plugin_tests_cache", None)
        if cached is None:
            from r7_ops import SpreadsheetOps   # лениво: r7_ops импортирует этот модуль
            ops = SpreadsheetOps(self, find_hwnd=lambda: None,
                                 log_cb=getattr(self, "add_test_log", None), test_file=None)
            cached = self._plugin_tests_cache = ops.plugin_tests()
        return cached

    def effective_test_definitions(self) -> list[str]:
        """Встроенные тесты (TEST_DEFINITIONS) и после них тесты плагинов."""
        return list(self.TEST_DEFINITIONS) + [t.name for t in self.plugin_tests()]

    def _plugin_test(self, name: str) -> PluginTest | None:
        """PluginTest по имени или None (встроенный или неизвестный тест)."""
        return next((t for t in self.plugin_tests() if t.name == name), None)

    def _is_export_test(self, name: str) -> bool:
        """Тест экспорта: встроенный из EXPORT_TESTS или плагин с kind="export"."""
        if name in self.EXPORT_TESTS:
            return True
        test = self._plugin_test(name)
        return test is not None and test.kind == KIND_EXPORT
