"""Файловый журнал программы: Reports/logs/r7-testovarka.log.

Журнал прогона прежде жил только в виджете Tk: окно закрыли — ничего не
осталось, а исключение внутри обработчика Tk терялось совсем. Инструмент
ставят на много ПК, и разбирать чужие сбои без файла невозможно.

setup_logging(base_dir) — один раз на процесс (повторный вызов ничего не
дублирует): файл с ротацией 5 МБ × 5, перехват необработанных исключений
главного и фоновых потоков (в журнал и, как прежде, в stderr) и faulthandler в
отдельный Reports/logs/crash.log — след от жёсткого падения интерпретатора.
Папку не создать или файл не открыть — журнал молча идёт в NullHandler, одно
предупреждение в stderr.

Модуль не импортирует tkinter: его зовут и из r7_Testovarka до создания окна.
"""
import faulthandler
import logging
import platform
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "r7"
LOG_DIR = Path("Reports") / "logs"           # относительно base_dir
LOG_FILE_NAME = "r7-testovarka.log"
CRASH_FILE_NAME = "crash.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)s | %(threadName)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
GIT_TIMEOUT_SEC = 2.0

# Первый значок сообщения журнала прогона → уровень записи в файле.
# Те же префиксы, что красят строки виджета (MainWindowMixin.LOG_TAG_BY_PREFIX).
LEVEL_BY_PREFIX = (("❌", logging.ERROR), ("⚠", logging.WARNING))

_logger = logging.getLogger(LOGGER_NAME)
_logger.setLevel(logging.INFO)
# Без своего обработчика logging печатал бы WARNING+ через lastResort в
# stderr — в юнит-тестах, где setup_logging не звали, это лишний шум.
_logger.addHandler(logging.NullHandler())

# Состояние установки: обработчик, файл faulthandler, прежние перехватчики.
# Пустой словарь — журнал не настроен.
_state = {}
_fallback_warned = False


def get_logger():
    """Логгер программы: пишут в него и до setup_logging (тогда — в никуда)."""
    return _logger


def get_log_path():
    """Путь к файлу журнала, если он открыт, иначе None."""
    return _state.get("log_path")


def level_for_message(msg):
    """Уровень записи по первому значку сообщения: ❌ → ERROR, ⚠ → WARNING,
    остальное — INFO."""
    text = str(msg).lstrip()
    for prefix, level in LEVEL_BY_PREFIX:
        if text.startswith(prefix):
            return level
    return logging.INFO


def setup_logging(base_dir):
    """Включает файловый журнал в <base_dir>/Reports/logs. Повторный вызов
    ничего не меняет и возвращает тот же логгер."""
    if _state:
        return _logger
    base_dir = Path(base_dir)
    log_dir = base_dir / LOG_DIR
    handler, log_path = _open_file_handler(log_dir / LOG_FILE_NAME)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    _logger.addHandler(handler)
    _state.update(handler=handler, log_path=log_path)
    _install_excepthooks()
    _enable_faulthandler(log_dir / CRASH_FILE_NAME)
    _log_session_header(base_dir)
    return _logger


def shutdown_logging():
    """Снимает всё, что поставил setup_logging (для тестов и повторной
    настройки в другой папке)."""
    if not _state:
        return
    handler = _state["handler"]
    _logger.removeHandler(handler)
    handler.close()
    sys.excepthook = _state["prev_sys_hook"]
    threading.excepthook = _state["prev_thread_hook"]
    crash_file = _state.get("crash_file")
    if crash_file is not None:
        faulthandler.disable()
        crash_file.close()
        if _state.get("faulthandler_was_enabled"):
            faulthandler.enable()
    _state.clear()


def _open_file_handler(path):
    """Обработчик с ротацией, либо NullHandler, если папка недоступна
    (путь тогда None). Предупреждение печатается один раз на процесс."""
    global _fallback_warned
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES,
                                   backupCount=LOG_BACKUP_COUNT, encoding="utf-8"), path
    except OSError as e:
        if not _fallback_warned:
            _fallback_warned = True
            _print_stderr(f"⚠️ Файловый журнал недоступен ({path}): {e}. "
                          f"Сообщения остаются только в окне программы.")
        return logging.NullHandler(), None


def _print_stderr(text):
    try:
        if sys.stderr is not None:
            print(text, file=sys.stderr)
    except Exception:
        pass                                   # нет консоли (pythonw) — молчим


def _install_excepthooks():
    """Необработанное исключение — в журнал и дальше прежнему перехватчику
    (он печатает traceback в stderr, как и без нас)."""
    _state["prev_sys_hook"] = sys.excepthook
    _state["prev_thread_hook"] = threading.excepthook
    sys.excepthook = _sys_excepthook
    threading.excepthook = _threading_excepthook


def _sys_excepthook(exc_type, exc_value, tb):
    _logger.error("Необработанное исключение в главном потоке",
                  exc_info=(exc_type, exc_value, tb))
    prev = _state.get("prev_sys_hook") or sys.__excepthook__
    prev(exc_type, exc_value, tb)


def _threading_excepthook(args):
    thread_name = getattr(args.thread, "name", "?") if args.thread is not None else "?"
    _logger.error("Необработанное исключение в потоке %s", thread_name,
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    prev = _state.get("prev_thread_hook") or threading.__excepthook__
    prev(args)


def _enable_faulthandler(path):
    """Жёсткое падение (segfault, abort в pywin32/Tk) не доходит до
    excepthook; faulthandler допишет стек всех потоков в crash.log."""
    try:
        crash_file = open(path, "a", encoding="utf-8")
    except OSError as e:
        _logger.warning("crash.log не открыть (%s): %s", path, e)
        return
    _state["faulthandler_was_enabled"] = faulthandler.is_enabled()
    _state["crash_file"] = crash_file
    faulthandler.enable(file=crash_file, all_threads=True)


def _log_session_header(base_dir):
    frozen = bool(getattr(sys, "frozen", False))
    _logger.info("=== R7-Testovarka: запуск ===")
    _logger.info("версия: %s | Python %s | %s | %s | BASE_DIR=%s",
                 _tool_version(base_dir, frozen), platform.python_version(),
                 platform.platform(), "exe (PyInstaller)" if frozen else "скрипт", base_dir)
    if _state.get("log_path") is None:
        _logger.info("файл журнала недоступен, записи идут в NullHandler")


def _tool_version(base_dir, frozen):
    """Короткий SHA git, пока у инструмента нет своей версии; в .exe и без
    git — «unknown». Не дольше GIT_TIMEOUT_SEC: git на сетевом диске виснет."""
    if frozen:
        return "unknown"
    try:
        proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(base_dir),
                              capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else "unknown"
