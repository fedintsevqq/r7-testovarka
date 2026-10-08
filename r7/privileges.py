"""Права администратора — одно место для проверки и для подмены в тестах.

Права нужны только установке и удалению версий (msiexec) и сбросу
файлового кэша ОС перед холодным стартом (_purge_os_file_cache). Прогоны
без них идут, см. docs/first-run.md. Код зовёт privileges.is_admin(), а не
ctypes напрямую: тесты подменяют эту функцию, и подмена видна всем. Сам
вызов Win32 — r7.windows.is_user_an_admin (граница Windows-кода).
"""
import functools

from r7 import windows


@functools.lru_cache(maxsize=None)
def is_admin() -> bool:
    """True — процесс запущен с правами администратора. Результат кэшируется
    (права за время работы не меняются); любая ошибка — False."""
    try:
        return bool(windows.is_user_an_admin())
    except Exception:  # не Windows или нет shell32 — считаем, что прав нет
        return False
