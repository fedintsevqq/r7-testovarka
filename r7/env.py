"""Необязательные зависимости и флаги их наличия — один источник для всего кода.

Пакеты ставятся из requirements.txt, но программа запускается и без
некоторых из них (двойной щелчок системным Python, урезанный стенд): тогда
соответствующий флаг *_OK ложен, а имя модуля — None. Код читает флаги как
`env.PSUTIL_OK`, а не копию в своём модуле: тесты подменяют их здесь, и
подмена видна всем модулям сразу.

Импортировать после перезапуска под .venv (r7_Testovarka.py, __main__):
иначе первый процесс печатал бы ложные «Установите …».
"""

try:
    import pyautogui
    PYAUTOGUI_OK = True
    # Аварийный выход: во время многоминутного автотеста клавиатура занята
    # программой, а мышь — нет. Инструмент нигде не двигает курсор сам
    # (только клики по текущей позиции — moveTo/dragTo в коде не используются),
    # поэтому включённый FAILSAFE ничего не ломает и не срабатывает случайно, а
    # даёт оператору физический способ прервать сценарий: увести мышь в угол
    # экрана поднимает pyautogui.FailSafeException.
    pyautogui.FAILSAFE = True
    # PAUSE по умолчанию 0.1 с и добавляется ПОСЛЕ КАЖДОГО вызова pyautogui.
    # Для замеров это чистый шум: хоткей из двух клавиш стоил 0.5 с сна
    # (4 события × interval + PAUSE) независимо от того, что делает Р7-Офис,
    # и именно эта константа, а не производительность Р7, определяла результат
    # 13 тестов. Всю действительно необходимую паузу задаём явно через
    # R7Testovarka._pace(), чтобы её можно было вычесть из замера.
    pyautogui.PAUSE = 0
except ImportError:
    pyautogui = None
    PYAUTOGUI_OK = False
    print("⚠️ Установите pyautogui: pip install pyautogui")

try:
    import pyperclip
except ImportError:
    pyperclip = None
    print("⚠️ Установите pyperclip: pip install pyperclip")

try:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, PatternFill
    EXCEL_OK = True
except ImportError:
    Workbook = WriteOnlyCell = Font = PatternFill = None
    EXCEL_OK = False
    print("⚠️ Установите openpyxl: pip install openpyxl")

try:
    import win32gui
    import win32con
    import win32api
    import win32process
    WIN32_OK = True
except ImportError:
    win32gui = win32con = win32api = win32process = None
    WIN32_OK = False
    print("⚠️ Установите pywin32: pip install pywin32")

try:
    import psutil
    PSUTIL_OK = True
except ImportError:
    psutil = None
    PSUTIL_OK = False
    print("⚠️ Установите psutil: pip install psutil")

# Счётчики производительности Windows (PDH) — для частоты CPU во время
# замера (r7/cpu_freq.py): psutil.cpu_freq() на Windows отдаёт номинальную
# частоту и троттлинг не видит. Часть pywin32; без него частота просто не
# снимается, подсказка не нужна — её уже печатает блок pywin32 выше.
try:
    import win32pdh
    PDH_OK = True
except ImportError:
    win32pdh = None
    PDH_OK = False


def pdh_open_counter(path):
    """Открывает PDH-запрос со счётчиком path (английское имя) и снимает
    первую базу: счётчик — отношение за интервал между опросами.

    Returns:
        (query, counter) или None — PDH нет. Счётчика нет — исключение.
    """
    pdh = win32pdh if PDH_OK else None
    if pdh is None:
        return None
    query = pdh.OpenQuery()
    counter = pdh.AddEnglishCounter(query, path)
    pdh.CollectQueryData(query)
    return query, counter


def pdh_read_double(query, counter):
    """Опрос запроса и значение счётчика как float; сбой — исключение."""
    pdh = win32pdh
    pdh.CollectQueryData(query)
    _type, value = pdh.GetFormattedCounterValue(counter, pdh.PDH_FMT_DOUBLE)
    return value


def pdh_close_query(query):
    """Закрывает PDH-запрос; без win32pdh — ничего. Сбой — исключение."""
    if win32pdh is not None:
        win32pdh.CloseQuery(query)

# UI Automation для комбобокса «Тип файла» в диалоге «Сохранить как»
# (save_as_format, этап 3/L2). ПОДТВЕРЖДЕНО ЖИВЫМ ПРОГОНОМ (26.08.2026,
# tests/manual_saveas_uia_save.py): этот диалог — современный IFileDialog
# с DirectUI-прослойкой, обычный win32gui.SendMessage (CB_SETCURSEL,
# WM_SETTEXT) до реальной логики комбобокса/поля имени не долетает —
# CB_SETCURSEL молча меняет внутренний индекс контрола, но диалог при
# нажатии «Сохранить» всё равно пишет файл с расширением, соответствующим
# СТАРОМУ выбору (по умолчанию — исходный формат документа, XLSX).
# Обязательная зависимость (см. requirements.txt) — без неё ни один формат
# кроме исходного XLSX-совместимого не переключается достоверно.
try:
    from pywinauto import Application as _UiaApplication
    PYWINAUTO_OK = True
except ImportError:
    _UiaApplication = None
    PYWINAUTO_OK = False
    print("⚠️ Установите pywinauto: pip install pywinauto comtypes")

# Опциональный CDP-триггер готовности редактора (кнопка «Жирный» в DOM —
# см. r7_webdriver_connector.py и коммит 7978206). Полностью необязателен:
# без пакетов requests/websocket-client (или самого модуля) программа
# работает как раньше, на win32gui/CPU-логике из _wait_until_r7_ready.
try:
    from r7_webdriver_connector import (
        R7WebDriverConnector,
        r7_launch_debug_args,
        DEFAULT_CDP_PORT,
        WEBDRIVER_OK,
    )
except ImportError as e:
    print(f"⚠️ Ошибка импорта r7_webdriver_connector: {e}")
    R7WebDriverConnector = r7_launch_debug_args = None
    DEFAULT_CDP_PORT = 8080
    WEBDRIVER_OK = False
