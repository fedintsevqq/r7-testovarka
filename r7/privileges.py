"""Права администратора — одно место для проверки и для подмены в тестах.

Права нужны только установке и удалению версий (msiexec) и сбросу
файлового кэша ОС перед холодным стартом (_purge_os_file_cache). Прогоны
без них идут, см. docs/first-run.md. Код зовёт privileges.is_admin(), а не
ctypes напрямую: тесты подменяют эту функцию, и подмена видна всем.
"""
import ctypes
import functools


@functools.lru_cache(maxsize=None)
def is_admin():
    """True — процесс запущен с правами администратора. Результат кэшируется
    (права за время работы не меняются); любая ошибка — False."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # не Windows или нет shell32 — считаем, что прав нет
        return False
