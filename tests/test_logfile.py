"""Файловый журнал (r7/logfile.py): файл с ротацией, уровни по значку,
перехват исключений, недоступная папка, повторная настройка."""
import logging
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import r7.logfile as logfile


@pytest.fixture(autouse=True)
def _clean_logging():
    """Каждый тест настраивает журнал в своей папке и снимает его после."""
    logfile.shutdown_logging()
    yield
    logfile.shutdown_logging()


@pytest.fixture
def log_dir(tmp_path):
    logfile.setup_logging(tmp_path)
    return tmp_path / "Reports" / "logs"


def _read(path):
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- файл

def test_setup_creates_rotating_file(tmp_path, log_dir):
    path = log_dir / "r7-testovarka.log"
    assert path.is_file()
    assert logfile.get_log_path() == path
    handler = logfile._state["handler"]
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 5
    assert handler.encoding == "utf-8"


def test_record_format_one_line(log_dir):
    logfile.get_logger().info("привет")
    line = _read(log_dir / "r7-testovarka.log").splitlines()[-1]
    date, level, thread, msg = [p.strip() for p in line.split(" | ")]
    assert len(date) == len("2026-10-07 12:34:56.789") and date[10] == " "
    assert (level, thread, msg) == ("INFO", "MainThread", "привет")


def test_session_header_logged(log_dir, tmp_path):
    text = _read(log_dir / "r7-testovarka.log")
    assert "R7-Testovarka: запуск" in text
    assert f"BASE_DIR={tmp_path}" in text
    assert "Python " in text and "скрипт" in text


def test_tool_version_unknown_without_git(tmp_path):
    assert logfile._tool_version(tmp_path, frozen=True) == "unknown"
    assert logfile._tool_version(tmp_path, frozen=False) == "unknown"   # не репозиторий


def test_tool_version_survives_missing_git(tmp_path, monkeypatch):
    def _no_git(*a, **kw):
        raise FileNotFoundError("git")
    monkeypatch.setattr(logfile.subprocess, "run", _no_git)
    assert logfile._tool_version(tmp_path, frozen=False) == "unknown"


def test_setup_twice_does_not_duplicate_handlers(tmp_path, log_dir):
    before = list(logfile.get_logger().handlers)
    logfile.setup_logging(tmp_path)
    logfile.setup_logging(tmp_path / "другая")
    assert logfile.get_logger().handlers == before
    logfile.get_logger().info("один раз")
    assert _read(log_dir / "r7-testovarka.log").count("один раз") == 1


def test_crash_log_enabled_in_append_mode(log_dir):
    import faulthandler
    assert faulthandler.is_enabled()
    crash = logfile._state["crash_file"]
    assert crash.name == str(log_dir / "crash.log") and crash.mode == "a"


# ---------------------------------------------------------------- уровни

@pytest.mark.parametrize("msg, level", [
    ("❌ сбой", logging.ERROR), ("  ⚠️ внимание", logging.WARNING),
    ("✅ ок", logging.INFO), ("📊 медиана", logging.INFO), ("просто текст", logging.INFO),
])
def test_level_by_prefix(msg, level):
    assert logfile.level_for_message(msg) == level


# ---------------------------------------------------------------- папка недоступна

def test_unwritable_dir_falls_back_to_null_handler(tmp_path, capsys):
    base = tmp_path / "файл-вместо-папки"
    base.write_text("x")
    logfile._fallback_warned = False
    logger = logfile.setup_logging(base)                 # не падает
    logger.info("в никуда")
    assert logfile.get_log_path() is None
    assert isinstance(logfile._state["handler"], logging.NullHandler)
    err = capsys.readouterr().err
    assert "Файловый журнал недоступен" in err
    # Второе предупреждение не печатается.
    logfile.shutdown_logging()
    logfile.setup_logging(base)
    assert "Файловый журнал недоступен" not in capsys.readouterr().err


def test_permission_error_on_open_falls_back(tmp_path, monkeypatch):
    def _denied(*a, **kw):
        raise PermissionError("read-only")
    monkeypatch.setattr(logfile, "RotatingFileHandler", _denied)
    logfile.setup_logging(tmp_path)
    assert isinstance(logfile._state["handler"], logging.NullHandler)


# ---------------------------------------------------------------- excepthook

def _raise_for_tb():
    try:
        raise ValueError("boom")
    except ValueError:
        return sys.exc_info()


def test_sys_excepthook_logs_traceback_and_prints(log_dir, capsys):
    exc = _raise_for_tb()
    sys.excepthook(*exc)
    text = _read(log_dir / "r7-testovarka.log")
    assert "| ERROR |" in text and "Необработанное исключение в главном потоке" in text
    assert "ValueError: boom" in text and "Traceback" in text
    assert "ValueError: boom" in capsys.readouterr().err


def test_threading_excepthook_logs_and_chains(tmp_path, monkeypatch):
    prev = Mock()
    monkeypatch.setattr(threading, "excepthook", prev)
    logfile.setup_logging(tmp_path)
    exc_type, exc_value, tb = _raise_for_tb()
    args = SimpleNamespace(exc_type=exc_type, exc_value=exc_value, exc_traceback=tb,
                           thread=SimpleNamespace(name="worker-7"))
    threading.excepthook(args)
    text = _read(tmp_path / "Reports" / "logs" / "r7-testovarka.log")
    assert "в потоке worker-7" in text and "ValueError: boom" in text
    prev.assert_called_once_with(args)


def test_shutdown_restores_hooks(tmp_path):
    sys_hook, thread_hook = sys.excepthook, threading.excepthook
    logfile.setup_logging(tmp_path)
    assert sys.excepthook is not sys_hook
    logfile.shutdown_logging()
    assert sys.excepthook is sys_hook and threading.excepthook is thread_hook
    assert logfile.get_logger().handlers == [h for h in logfile.get_logger().handlers
                                             if isinstance(h, logging.NullHandler)]
