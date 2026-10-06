# -*- coding: utf-8 -*-
"""
R7-Testovarka Light – управление версиями + стресс-тест таблиц
"""

import os
import sys
import subprocess
import time
import threading
import shutil
import re
import json
import hashlib
import csv
import ctypes
import statistics
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import webbrowser

def get_base_dir() -> Path:
    """Возвращает базовую директорию приложения (портативный режим).

    В режиме PyInstaller (.exe, sys.frozen == True) — папка рядом с .exe.
    В режиме Python-скрипта — папка, содержащая .py файл.
    Использование sys.executable вместо sys.argv[0] надёжнее при запуске
    через ярлык или другой лаунчер.
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller: sys.executable указывает на собранный .exe
        return Path(sys.executable).parent
    # Обычный запуск: берём папку скрипта, а не текущую рабочую директорию
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()

# Консоль Windows по умолчанию — cp1251/cp866, а не UTF-8: print() с эмодзи
# (используются ниже в диагностике опциональных зависимостей) падает на такой
# консоли с UnicodeEncodeError и рушит запуск ещё до создания UI. sys.stdout
# бывает и None (pythonw.exe без консоли) — reconfigure на None кидает
# AttributeError, поэтому весь блок в try/except.
try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def _venv_python_for_relaunch(packages_ok):
    """Интерпретатор .venv проекта, если программа запущена не им и без
    пакетов для CDP/UI Automation. Иначе None.

    Двойной щелчок по .py открывает его через py.exe — системным Python, где
    requests/websocket-client/pywinauto может не быть. Тогда CDP выключен:
    модалку пересчёта при открытии никто не закрывает, контекстное меню и
    диалоги Р7 не управляются, и все замеры становятся бессмысленными
    (живой прогон 30.09.2026). R7_NO_VENV_RELAUNCH=1 — защита от цикла.

    Вызывается до импорта необязательных пакетов: иначе первый процесс
    успевал напечатать «Установите pywinauto» и WEBDRIVER_OK: False, хотя
    ставить ничего не нужно — программа тут же перезапускалась под .venv.
    """
    if packages_ok:
        return None
    if getattr(sys, "frozen", False) or os.environ.get("R7_NO_VENV_RELAUNCH"):
        return None
    exe_name = "pythonw.exe" if Path(sys.executable).name.lower() == "pythonw.exe" else "python.exe"
    venv_python = BASE_DIR / ".venv" / "Scripts" / exe_name
    if not venv_python.is_file():
        return None
    if os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(str(venv_python)):
        return None
    return venv_python


def _ui_packages_present():
    """Есть ли пакеты для доступа к интерфейсу Р7 (CDP и UI Automation).
    find_spec ищет модуль, не импортируя его, — без побочных эффектов."""
    import importlib.util
    return all(importlib.util.find_spec(m) is not None
               for m in ("requests", "websocket", "pywinauto"))


if __name__ == "__main__":
    _venv_python = _venv_python_for_relaunch(_ui_packages_present())
    if _venv_python is not None:
        print(f"↻ В {sys.executable} нет пакетов для доступа к интерфейсу Р7 — "
              f"перезапуск через .venv ({_venv_python})")
        subprocess.Popen([str(_venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
                         env={**os.environ, "R7_NO_VENV_RELAUNCH": "1"})
        sys.exit()

# Необязательные зависимости и флаги *_OK — в r7/env.py: один источник для
# всех модулей пакета и для подмен в тестах. Имена модулей переэкспортируются
# сюда, чтобы код ниже писал psutil.X, а не env.psutil.X. Флаги, коннектор
# CDP и UI Automation читаются как env.X: тесты подменяют их в r7.env.
from r7 import env  # noqa: E402
from r7.env import psutil, pyperclip  # noqa: E402
from r7.windows import WindowsMixin  # noqa: E402
from r7.export import ExportMixin  # noqa: E402
from r7.dialogs import DialogsMixin  # noqa: E402
from r7.versions import VersionsMixin  # noqa: E402
from r7.fixtures import FixturesMixin  # noqa: E402
from r7.results import ResultsMixin  # noqa: E402
from r7.runs import RunsMixin  # noqa: E402
from r7.config import (  # noqa: E402
    _OPEN_NOT_READY, DEFAULT_TEST_RUNS, RUNS_MAX, RUNS_MIN, SERIES_COLORS,
)
from r7.cdp import CdpMixin  # noqa: E402
from r7.readiness import ReadinessMixin  # noqa: E402
from r7.measure import MeasureMixin  # noqa: E402
from r7.resources import (  # noqa: E402
    ResourcesMixin, ResourceSampler, _disk_delta, _disk_snapshot, _format_disk,
)
from r7.processes import ProcessesMixin, X2tTracker  # noqa: E402

print(f"🔍 WEBDRIVER_OK после импорта: {env.WEBDRIVER_OK} (файл: {__file__}, cwd: {os.getcwd()})")

# Свои модули — обязательные, вне try выше: прежде r7_reports стоял внутри
# него, и без jinja2 программа молча считала, что нет CDP.
import r7_reports  # noqa: E402  HTML-отчёты: модели страниц и шаблоны Jinja2
from r7_ops import SpreadsheetOps  # noqa: E402  тест-операции всех воркеров
from r7.stats import detect_leak  # noqa: E402


COLORS = {
    "bg":            "#1E1E2E",  # основной фон
    "bg_card":       "#2A2A3E",  # фон карточек/фреймов
    "accent":        "#6C63FF",  # акцент: кнопки, активные элементы, заголовки
    "accent_hover":  "#5750D9",  # затемнение акцента при наведении
    "text":          "#E0E0E0",  # основной текст
    "text_secondary":"#A0A0B0",  # вторичный текст
    "border":        "#3A3A5A",  # границы/разделители, фон обычных кнопок
    "border_hover":  "#4A4A6A",  # фон кнопок при наведении
    "log_bg":        "#1A1A2E",  # фон лога
    "success":       "#4CAF50",  # INFO / ✅
    "warn":          "#FF9800",  # WARN / ⚠️
    "error":         "#F44336",  # ERROR / ❌
}
FONT_UI  = ("Segoe UI", 10)
FONT_LOG = ("Consolas", 9)


class R7Testovarka(ProcessesMixin, WindowsMixin, MeasureMixin, CdpMixin, ReadinessMixin,
                   ExportMixin, DialogsMixin, VersionsMixin, FixturesMixin,
                   ResultsMixin, RunsMixin, ResourcesMixin):
    TEST_DEFINITIONS = [
        "Повторное открытие файла",   # см. OPEN_TEST_NAME
        "Выделение всех ячеек (Ctrl+A)",
        "Копирование всех ячеек (Ctrl+C)",
        "Вставка большого массива (Ctrl+V)",
        "Добавление нового листа",
        "Добавление столбца (горячие клавиши)",
        "Добавление столбца (меню Вставка)",
        "Вставка 1 ячейки (горячие клавиши)",
        "Вставка 5 ячеек (горячие клавиши)",
        "Вставка 1 ячейки (ПКМ)",
        "Вставка 5 ячеек (ПКМ)",
        "Функция ВПР (50K строк)",
        "Удаление столбца (Del)",
        "Сохранение в PDF (конвертация x2t)",
        # L2 (этап 3): Save As с явным переключением «Тип файла» через UI
        # Automation (см. _uia_select_saveas_type в save_as_format) — расширение
        # в имени файла на выбор конвертера не влияет, комбобокс переключается
        # отдельно. ODS/CSV(частично) подтверждены живым Р7 26.08.2026.
        "Сохранение в ODS (конвертация x2t)",
        "Сохранение в CSV (конвертация x2t)",
        "Сохранение в XLTX (конвертация x2t)",
    ]

    # Эти три формата — самые долгие тесты (по 5–10 мин на формат при
    # DEFAULT_TEST_RUNS=7, живой прогон на 50K подтвердил). Обычные тесты
    # включены и на 7 прогонов по умолчанию; эти — выключены и на 3, чтобы
    # обычный прогон вкладки «Производительность» не раздувался форматами,
    # которые пользователь явно не просил измерить.
    EXTRA_FORMAT_TESTS = {
        "Сохранение в ODS (конвертация x2t)",
        "Сохранение в CSV (конвертация x2t)",
        "Сохранение в XLTX (конвертация x2t)",
    }
    DEFAULT_FORMAT_TEST_RUNS = 3
    # Все тесты экспорта, включая PDF: в полном прогоне идут на
    # DEFAULT_FORMAT_TEST_RUNS повторов, а не на DEFAULT_TEST_RUNS. Один экспорт
    # фикстуры 50К — до 96 с, и при 7 повторах экспорты занимали ~28 из 44 мин
    # полного прогона (живой прогон 29.09.2026) при MAD меньше 1% медианы.
    EXPORT_TESTS = EXTRA_FORMAT_TESTS | {"Сохранение в PDF (конвертация x2t)"}
    # Повторы операции в Batch-режиме (аудит 29.09.2026, пункт 13): не меньше
    # MIN_RUNS_FOR_COMPARISON, иначе вердикт compare_runs недоступен. Первый
    # прогон — прогрев, поэтому в статистику войдут BATCH_TEST_RUNS − 1.
    BATCH_TEST_RUNS = 6

    # Повторы открытия файла (аудит 29.09.2026, пункт 4): каждый повтор —
    # полный цикл «очистка кеша → запуск → готовность → закрытие», поэтому
    # по умолчанию их меньше, чем у операций. Имя в отчёте — прежнее
    # «Открытие файла», чтобы не рвать тренды и сравнение версий.
    OPEN_TEST_NAME = "Повторное открытие файла"
    DEFAULT_OPEN_RUNS = 5   # нечётное: медиана держит до двух выбросов. На стенде с
                            # нестабильным системным диском каждое четвёртое
                            # открытие шло 12–14 с вместо 9 (30.09.2026)
    OPEN_DISK_WAIT_SPREAD_SEC = 1.0   # разброс ожидания x2t между открытиями → пометка

    # ── Пороги определения «документ открыт» ────────────────────────────────
    # Одни на все три режима (одиночный тест, тест своего файла, Batch), чтобы
    # они больше не разъезжались, как разъехались STABLE_SECS=10 и STABLE_SECS=8
    # у двух прежних копий ожидания загрузки.
    READY_POLL_SEC          = 0.15   # шаг опроса
    READY_RESPONSIVE_MS     = 300    # окно прокачало очередь быстрее — оно отзывчиво
    # Порог простоя — в процентах ОДНОГО ядра (сырая сумма cpu_percent() по
    # процессам Р7), measure_schema 3. История: в schema 1 сравнивалась сырая
    # сумма с порогом без обоснования, в schema 2 — сумма, делённая на число
    # ядер (шкала Task Manager). Нормировка оказалась ошибкой: работа Р7 почти
    # вся однопоточная (пересчёт, раскладка, x2t), и один полностью занятый
    # поток на 16 ядрах даёт 100/16 = 6.25%, на 32 — 3.1%, то есть ниже
    # порога. Детектор переставал видеть занятость на многоядерных стендах, и
    # операции массово уходили в below_floor (аудит 29.09.2026: 10 из 13 на
    # 16-ядерном стенде). Доля одного ядра от числа ядер не зависит: «занят
    # хотя бы четверть одного потока» значит одно и то же на любой машине.
    # Нормированный CPU по-прежнему пишется в отчёт — но только для чтения.
    # Калибровка на живом Р7 2026.3.2 (29.09.2026, 16 ядер, test_50k.xlsx):
    # простой — медиана 0, p95 7.5, максимум 15.2% ядра (шум квантуется
    # тиком таймера 15.6 мс: на окне 0.2 с тик = 7.8%, на 0.15 с — 10.4%);
    # загрузка — 45–290% ядра. Старый нормированный порог 4% на этом стенде
    # означал 64% ядра и считал простоем реальную загрузку в 45–60%.
    # 25 — выше двух тиков шума на окне опроса READY_POLL_SEC.
    READY_IDLE_CORE_PCT     = 25.0   # % одного ядра: сумма по процессам Р7 ниже — простой
    READY_IDLE_SAMPLES      = 20     # столько простоев подряд → документ открыт (≈3 с)
    READY_PROC_REFRESH_SEC  = 1.0    # как часто пересобирать список процессов (ловим x2t)
    READY_MIN_BUSY_SEC      = 0.5    # не выносить вердикт раньше — даём Р7 начать работу

    # Доп. триггер: кнопка «Жирный» на панели инструментов. Работает только
    # если она существует как отдельное нативное окно Win32 (класс "Button"
    # или "ToolbarButton") — см. предупреждение в docstring _wait_for_bold_button.
    # На практике это условие не выполняется ни для CEF-панели (текущая
    # сборка — HTML в одном render-окне), ни для классического Win32
    # ToolbarWindow32 (общий контрол сам рисует кнопки, у них тоже нет
    # отдельного HWND) — реалистичного билда, где сработает эта ветка, не
    # определено; см. docstring _is_bold_button_visible.
    # Однобуквенные метки ("b", "ж") сознательно из спецификации — риск: на
    # гипотетической сборке, где EnumChildWindows всё же находит что-то
    # подходящее по классу, ЛЮБАЯ кнопка с такой короткой подписью (не
    # обязательно именно "Жирный") будет принята без дополнительной проверки.
    BOLD_BUTTON_LABELS     = ("b", "ж", "жирный", "bold")  # регистронезависимо
    BOLD_BUTTON_CLASSES    = ("Button", "ToolbarButton")
    BOLD_BUTTON_POLL_SEC   = 0.1
    BOLD_BUTTON_TIMEOUT_SEC = 3.0
    # Верхняя граница именно на ПОДКЛЮЧЕНИЕ к CDP-порту (connector.connect()
    # внутри _wait_for_bold_button_cdp), не на весь бюджет BOLD_BUTTON_TIMEOUT_SEC.
    # _prepare_webdriver_launch создаёт коннектор всегда, когда порт 8080
    # свободен, — независимо от того, откроет ли его сама сборка Р7. Если
    # не откроет, connect() без этой границы опрашивал бы /json циклом до
    # 2 с впустую (порт закрыт => _pick_target() сразу None => sleep(poll_sec)
    # по кругу), а _wait_for_bold_button_cdp вызывается уже ПОСЛЕ того, как
    # остальные признаки готовности совпали — то есть это время инфлировало
    # бы прямо замер «Открытие файла» на каждой сборке без реального CDP.
    BOLD_BUTTON_CDP_CONNECT_TIMEOUT_SEC = 0.5

    # ── Замер отдельной операции ────────────────────────────────────────────
    # Операция считается завершённой, когда Р7 перестал быть занятым. Занятость
    # определяется по двум признакам сразу: окно не прокачивает очередь
    # сообщений ИЛИ процессы Р7 грузят CPU (плюс отдельно — жив ли конвертер x2t).
    OP_POLL_SEC         = 0.05   # шаг опроса состояния Р7
    OP_RESPONSIVE_MS    = 40     # окно не ответило за это — считаем занятым
    # Шкала — % одного ядра, см. комментарий у READY_IDLE_CORE_PCT. Порог выше,
    # чем у READY_IDLE_CORE_PCT: окно усреднения здесь короче (OP_CPU_WINDOW_SEC
    # против READY_POLL_SEC·READY_IDLE_SAMPLES), и короткое окно дрожит сильнее.
    OP_BUSY_CORE_PCT    = 25.0   # % одного ядра: сумма по процессам Р7 не ниже — занято
                                 # (если держится два окна подряд, см. ниже)
    OP_BUSY_STRONG_CORE_PCT = 60.0  # одно окно выше — занято сразу. Фон GPU/рендерера
                                    # на окне 0.2 с — до ~25% (живой замер 29.09.2026)
    OP_CPU_WINDOW_SEC   = 0.20   # окно усреднения CPU: квант GetProcessTimes ≈15.6 мс,
                                 # на окне 50 мс это давало бы шум в десятки процентов
    OP_IDLE_SAMPLES     = 6      # подряд «не занято» → операция завершена (0.3 с)
    OP_START_GRACE_SEC  = 1.00   # ждём начала работы столько, прежде чем признать
                                 # операцию слишком быстрой для измерения.
                                 # В замер это ожидание НЕ попадает — стоит только
                                 # времени прогона, поэтому взято с запасом
    OP_CDP_TAIL_GRACE_SEC = 0.45 # после вызова api: хвост операции начинается сразу —
                                 # хватает на две 0.2-секундные выборки CPU подряд
                                 # (запасной путь, если пинг редактора недоступен)
    # Конец операции на CDP-пути — по пингу редактора (_wait_renderer_idle).
    OP_PING_FAST_SEC  = 0.010    # ответ быстрее — поток редактора свободен (обычно 0–4 мс)
    OP_PING_QUIET_SEC = 0.30     # столько подряд свободен → операция завершена
    OP_PING_GAP_SEC   = 0.05     # пауза между пингами в окне тишины
    OP_PDF_GRACE_SEC    = 6.00   # для экспорта в PDF: x2t стартует не сразу после
                                 # Enter в диалоге «Сохранить как»
    OP_PROC_REFRESH_SEC = 0.50   # пересбор списка процессов (ловим x2t)
    OP_MAX_WAIT_SEC     = 180    # предохранитель на одну операцию
    OP_SELECT_ALL_MAX_SEC = 20   # отдельный, куда более короткий предохранитель
    # save_as_format(): прямое ожидание появления/дозаписи файла экспорта —
    # независимая от CPU/PID-эвристик подстраховка. Живой прогон на 50K
    # показал разброс 0.009 → 48 сек на одной и той же операции: x2t иногда
    # укладывается в окно между двумя опросами CPU (OP_CPU_WINDOW_SEC) и
    # busy-детектор его просто не ловит.
    OP_EXPORT_FILE_POLL_SEC      = 0.05   # шаг опроса
    OP_EXPORT_FILE_STABLE_CHECKS = 8      # опросов подряд с неизменным размером (0.4 с) = файл
                                          # дописан. Длительность окна в замер не идёт: конец
                                          # экспорта берётся по mtime (см. _wait_for_export_file)
    OP_EXPORT_FILE_TIMEOUT_SEC   = 120.0  # первая калибровка (живой прогон видел ~48 сек)
    EXPORT_LOCK_WAIT_SEC         = 5.0    # файл экспорта ещё держит Р7/x2t — ждать до
                                          # проверки формата (вне замера, эталон 06.10.2026)
                                 # для Ctrl+A: выделив 25 млн ячеек, Р7 считает
                                 # по ним агрегаты в статусной строке и держит
                                 # CPU занятым десятками секунд. Общие 180 с
                                 # выглядели как зависание приложения; честнее
                                 # отметить операцию как timeout и идти дальше
    OP_KEY_PACE         = 0.08   # пауза после клавиш, меняющих состояние (буфер, лист)
    OP_MENU_PACE        = 0.12   # пауза на отрисовку меню или диалога
    OP_DIALOG_PACE      = 0.60   # отрисовка МОДАЛЬНОГО диалога («Вставить ячейки»).
                                 # OP_MENU_PACE=0.12 для него мало: модалка Р7 —
                                 # HTML внутри CEF, и на нагруженном документе она
                                 # не успевает появиться за 120 мс. Enter уходил в
                                 # сетку, а диалог оставался висеть (см. PR #4:
                                 # второй Enter добавили, но гонку не убрали)
    OP_DIALOG_ATTEMPTS  = 3      # столько раз подтверждаем модалку (см. _confirm_modal_enter)
    OP_CONTEXT_MENU_WAIT_SEC = 30.0  # меню у выделения (Shift+F10) открывается, когда
                                     # Р7 доделает предыдущий шаг — на 50K строк это секунды
    OP_CDP_PANEL_PACE_SEC = 0.40 # отрисовка полноэкранной панели «Файл» после клика
                                 # по ribbon-вкладке (см. _try_cdp_saveas) — панель
                                 # рисуется не мгновенно, второй клик («Сохранить
                                 # как» внутри неё) раньше этого мог промахнуться
                                 # мимо ещё не отрисованных пунктов. Величина взята
                                 # из живого прогона (27.08.2026,
                                 # tests/manual_saveas_cdp_probe.py), не откалибрована
                                 # на минимум
    CLOSE_CDP_RETRY_SEC = 1.00   # как часто опрашивать CDP при закрытии Р7
                                 # (_close_r7_gracefully): реже шага цикла в 0.2 с,
                                 # чтобы не спамить websocket-запросами и логом

    # ── Статистика по прогонам одной операции (run_test_with_runs) ─────────
    # Среднее по 3 прогонам на бимодальной величине (см. измеренный разброс
    # api_ms/settle_ms) — способ увидеть регрессию там, где её нет: один
    # выброс сдвигает среднее непропорционально. Медиана устойчивее, MAD
    # (Median Absolute Deviation) — устойчивая мера разброса рядом с ней.
    WINDOW_POLL_SEC = 0.03   # опрос появления окна Р7 = разрешение cold_start_ms

    MIN_RUNS_FOR_STATS = 4  # первый прогон (прогрев) отбрасывается, только если
                            # после него остаётся хотя бы 3: медиана двух — это
                            # их среднее, один выброс сдвигает её наполовину.
                            # При 3 повторах Ctrl+A 0.84 / 1.88 / 0.84 давал
                            # медиану 1.36 вместо 0.84 (30.09.2026, schema 6).

    # ── Нагрузочный стенд, этап 3 (L1/L3): геометрия окна и DPI ─────────────
    # Фиксированный размер вместо maximize() — физический размер сетки (и
    # значит скорость перерисовки/раскладки) иначе зависит от монитора
    # стенда, и результаты между машинами становятся несравнимы. Если экран
    # меньше цели — _fix_r7_window_geometry откатывается на maximize и это
    # видно в system.window_size отчёта (значение меньше константы).
    R7_WINDOW_W = 1920
    R7_WINDOW_H = 1080
    # Множитель масштабирования Windows (100 = 100%) не меняется этим
    # инструментом — это системная настройка, менять её из скрипта рискованно
    # и на части систем требует перезахода. Вместо этого текущее значение
    # снимается и кладётся в отчёт (system.dpi_scale_pct); расхождение с этой
    # константой — сигнал, что прогон нельзя напрямую сравнивать с другим,
    # снятым при другом масштабе.
    EXPECTED_DPI_SCALE_PCT = 100

    def __init__(self, root):
        """Initializes the main application window and state.

        Args:
            root: The tkinter root window.
        """
        self.root = root
        self.root.title("R7-Testovarka Light")
        self.root.resizable(True, True)
        # Ниже сетка карточек на вкладке «Производительность» (Canvas шириной
        # 380px) и лог рядом с ней уже не помещаются вменяемо — без явного
        # предела окно можно было сжать до состояния, где всё наезжает друг
        # на друга.
        self.root.minsize(self.MIN_WIN_W, self.MIN_WIN_H)

        self.distributives_folder = BASE_DIR / "Distributives"
        self.distributives_folder.mkdir(exist_ok=True)

        self.test_files_folder = BASE_DIR / "TestFiles"
        self.test_files_folder.mkdir(exist_ok=True)

        self.reports_folder = BASE_DIR / "Reports"
        self.reports_folder.mkdir(exist_ok=True)

        self.current_version_info = None
        self.distributives = []
        self.selected_distributive = None
        self._cached_r7_path = None
        self._cached_cpu_count = None  # psutil.cpu_count(), см. _cpu_count()
        self._ready_at = None          # начало простоя по _wait_until_r7_ready (perf_counter)
        self._ready_marker = None      # чем определена готовность: bold / cpu / ...
        self._op_completed_at = None   # конец операции по файлу экспорта, см. _resolve_op_end
        self._window_seen_at = None    # момент появления окна Р7 (perf_counter), L1
        self._applied_r7_window_size = None  # см. _fix_r7_window_geometry (L3)
        self._paced_total = 0.0    # сумма преднамеренных пауз внутри текущего замера
        self._pending_modal_confirm = False  # модалку «Вставить ячейки» надо
                                             # добить Enter'ами уже вне замера
                                             # (см. _flush_pending_modal_confirm)
        self._op_start_grace = None  # операция может попросить больше времени на старт
        self._op_max_wait = None     # ...и свой, более короткий, предохранитель
        self._webdriver_connector = None   # R7WebDriverConnector текущего запуска Р7, либо None
        self._current_webdriver_port = None  # CDP-порт текущего запуска, либо None
        self._op_via_cdp = False     # операция текущего замера ушла через api,
                                     # а не клавишами (влияет на трактовку below_floor)
        self._cdp_api_ms = 0.0       # сумма api_ms по всем шагам текущего замера —
                                     # синхронное время внутри рендерера, не зависящее
                                     # от опроса CPU детектором простоя (см. _cdp_sequence)
        self._pending_cdp_verify = None  # отложенная проверка CDP-операции
                                          # (см. _flush_pending_cdp_verify)
        self._cdp_ui_baseline = None  # DOM-снимок до первой операции — см. _capture_cdp_ui_baseline
        self.test_vars = {}   # populated by _build_perf_tab
        self.test_runs = {}   # populated by _build_perf_tab — IntVar per test, RUNS_MIN..RUNS_MAX
        self.perf_stop_event = threading.Event()
        self._perf_running = False   # защита от повторного запуска, пока прогон идёт
        self._batch_running = False  # тот же самый флаг для Batch-режима — оба
                                      # шлют клавиши в Р7-Офис и не должны идти одновременно

        self.setup_ui()
        self.refresh_distributives()
        self.detect_current_version()
        # Размер окна — после сборки интерфейса: только тогда известно,
        # сколько места ему нужно на самом деле (с учётом масштаба экрана).
        self._apply_default_geometry()

    # ---------------------- UI ----------------------
    # Желаемый размер окна при старте. Числа не на глаз: собранному UI нужно
    # winfo_reqwidth x winfo_reqheight = 1149x669 (вкладка «Производительность»
    # одна требует 1125 по ширине — там сетка карточек Canvas 380px и лог
    # стоят рядом). Прежние 800x600 обрезали её на 325px по ширине — отсюда и
    # «интерфейс обрезан». Ниже — требуемое плюс запас на будущие виджеты.
    DEFAULT_WIN_W = 1220
    DEFAULT_WIN_H = 780
    # Минимальный размер, при котором вся раскладка ещё работает: список
    # тестов и лог прокручиваются, а шапка и панель кнопок видны всегда.
    MIN_WIN_W = 820
    MIN_WIN_H = 560

    @staticmethod
    def _work_area():
        """Рабочая область основного монитора без панели задач: (x, y, w, h).

        winfo_screenheight() отдаёт весь экран, включая панель задач, — окно
        по его высоте уходило низом под панель. SPI_GETWORKAREA возвращает
        именно видимую область. None, если API недоступен.
        """
        try:
            from ctypes import wintypes
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return (rect.left, rect.top,
                        rect.right - rect.left, rect.bottom - rect.top)
        except Exception:
            pass
        return None

    @classmethod
    def _fit_window(cls, need_w, need_h, area):
        """Считает геометрию окна по нужному размеру и рабочей области.

        Чистая функция — проверяется тестами без Tk.

        Args:
            need_w, need_h: сколько просит собранный интерфейс (winfo_req*).
            area: (x, y, w, h) рабочей области экрана.

        Returns:
            tuple: (w, h, x, y, zoomed). zoomed=True — интерфейс не
            помещается даже в рабочую область, окно надо развернуть.
        """
        ax, ay, aw, ah = area
        # Поля под рамку окна и заголовок, их нет в winfo_req*.
        frame_w, frame_h = 16, 40
        want_w = max(cls.DEFAULT_WIN_W, need_w)
        want_h = max(cls.DEFAULT_WIN_H, need_h)
        if need_w + frame_w > aw or need_h + frame_h > ah:
            return aw - frame_w, ah - frame_h, ax, ay, True
        w = min(want_w, aw - frame_w)
        h = min(want_h, ah - frame_h)
        x = ax + max(0, (aw - w - frame_w) // 2)
        y = ay + max(0, (ah - h - frame_h) // 3)  # чуть выше центра — визуально ровнее
        return w, h, x, y, False

    def _apply_default_geometry(self):
        """Ставит стартовый размер окна так, чтобы весь интерфейс был виден.

        Вызывается ПОСЛЕ setup_ui: размер берётся из того, что интерфейсу
        реально нужно (winfo_reqwidth/height), а не из констант. На экране с
        масштабом 125–150% шрифты крупнее, интерфейс просит больше места, и
        прежние фиксированные 1220x780 обрезали низ вкладки вместе с кнопкой
        «Запустить». Окно вписывается в рабочую область (без панели задач);
        если интерфейс не помещается и в неё — окно разворачивается.
        """
        try:
            self.root.update_idletasks()
            area = self._work_area() or (0, 0, self.root.winfo_screenwidth(),
                                         self.root.winfo_screenheight() - 48)
            w, h, x, y, zoomed = self._fit_window(
                self.root.winfo_reqwidth(), self.root.winfo_reqheight(), area)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            if zoomed:
                self.root.state("zoomed")
        except Exception:
            # winfo_* теоретически может отказать до полной инициализации Tk —
            # окно без явной геометрии всё равно откроется, просто по умолчанию.
            self.root.geometry(f"{self.DEFAULT_WIN_W}x{self.DEFAULT_WIN_H}")

    def _apply_dark_theme(self):
        """Настраивает тёмную тему через ttk.Style.

        Тема 'clam' выбрана намеренно: нативные темы Windows ('vista'/
        'winnative') рисуют кнопки/вкладки/скроллбары средствами ОС и
        игнорируют цветовые переопределения ttk.Style для многих опций —
        подтверждено документацией Tk. 'clam' — собственный рендерер Tk,
        поддерживающий полную кастомизацию цвета для всех использованных
        здесь виджетов.
        """
        self.root.configure(bg=COLORS["bg"])
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"],
                         font=FONT_UI)
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["bg_card"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Card.TLabel", background=COLORS["bg_card"], foreground=COLORS["text"])
        style.configure("Secondary.TLabel", background=COLORS["bg"],
                         foreground=COLORS["text_secondary"])
        style.configure("Header.TLabel", background=COLORS["bg"],
                         foreground=COLORS["accent"], font=("Segoe UI", 16, "bold"))
        style.configure("StatusOk.TLabel", background=COLORS["bg"], foreground=COLORS["success"])
        style.configure("StatusErr.TLabel", background=COLORS["bg"], foreground=COLORS["error"])

        style.configure("TLabelframe", background=COLORS["bg"], foreground=COLORS["text"],
                         bordercolor=COLORS["border"])
        style.configure("TLabelframe.Label", background=COLORS["bg"], foreground=COLORS["text_secondary"])

        style.configure("TButton", background=COLORS["border"], foreground=COLORS["text"],
                         bordercolor=COLORS["border"], focusthickness=0, padding=6)
        style.map("TButton",
                  background=[("active", COLORS["border_hover"]), ("pressed", COLORS["border_hover"])])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="#FFFFFF",
                         font=("Segoe UI", 12, "bold"), padding=10)
        style.map("Accent.TButton",
                  background=[("active", COLORS["accent_hover"]), ("pressed", COLORS["accent_hover"])])

        style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["text"])
        style.map("TCheckbutton", background=[("active", COLORS["bg"])])
        style.configure("Card.TCheckbutton", background=COLORS["bg_card"], foreground=COLORS["text"])
        style.map("Card.TCheckbutton", background=[("active", COLORS["bg_card"])])

        style.configure("TSpinbox", fieldbackground=COLORS["bg_card"], background=COLORS["bg_card"],
                         foreground=COLORS["text"], arrowcolor=COLORS["text"])

        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=COLORS["bg"], foreground=COLORS["text_secondary"],
                         padding=(14, 8), borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", COLORS["bg"])],
                  foreground=[("selected", COLORS["accent"])])

        style.configure("TScrollbar", background=COLORS["border"], troughcolor=COLORS["bg"],
                         bordercolor=COLORS["bg"], arrowcolor=COLORS["text_secondary"])
        style.map("TScrollbar", background=[("active", COLORS["border_hover"])])

        style.configure("Treeview", background=COLORS["bg_card"], fieldbackground=COLORS["bg_card"],
                         foreground=COLORS["text"], bordercolor=COLORS["border"], rowheight=26)
        style.configure("Treeview.Heading", background=COLORS["border"], foreground=COLORS["text"],
                         relief="flat")
        style.map("Treeview",
                  background=[("selected", COLORS["accent"])],
                  foreground=[("selected", "#FFFFFF")])

        style.configure("Horizontal.TProgressbar", background=COLORS["accent"],
                         troughcolor=COLORS["bg_card"], bordercolor=COLORS["bg"])

        # Флажки: в clam отмеченный флажок — крестик на белом, на тёмном фоне
        # его почти не отличить от пустого. Отмеченный — заливка акцентом.
        for name, bg in (("TCheckbutton", COLORS["bg"]), ("Card.TCheckbutton", COLORS["bg_card"])):
            style.configure(name, indicatorbackground=COLORS["bg"],
                            indicatorforeground="#FFFFFF", indicatormargin=(2, 2, 6, 2),
                            upperbordercolor=COLORS["text_secondary"],
                            lowerbordercolor=COLORS["text_secondary"])
            style.map(name, background=[("active", bg)],
                      indicatorbackground=[("selected", COLORS["accent"]),
                                           ("active", COLORS["border"])])

        # Маленькие кнопки «−»/«+» у числа повторов и кнопки в заголовках панелей.
        style.configure("Small.TButton", padding=(6, 1), font=("Segoe UI", 9))
        style.configure("Step.TButton", padding=(0, 0), width=2,
                         font=("Segoe UI", 10, "bold"))
        style.map("Step.TButton", background=[("active", COLORS["accent"]),
                                              ("pressed", COLORS["accent_hover"])])
        style.configure("Runs.TEntry", fieldbackground=COLORS["bg"], foreground=COLORS["text"],
                         insertcolor=COLORS["text"], bordercolor=COLORS["border"],
                         padding=(2, 1))
        style.configure("Group.TLabel", background=COLORS["bg"], foreground=COLORS["accent"],
                         font=("Segoe UI", 9, "bold"))
        style.configure("Version.TLabel", background=COLORS["bg"], foreground=COLORS["text"],
                         font=("Segoe UI", 11, "bold"))
        # Поля ввода и выпадающие списки. Без этого clam рисовал их светлым
        # полем, а текст брал светлый из общего стиля «.» — число строк в
        # диалоге тестовых файлов и путь в Batch-режиме было почти не прочесть.
        for name in ("TEntry", "TCombobox"):
            style.configure(name, fieldbackground=COLORS["bg_card"], foreground=COLORS["text"],
                            insertcolor=COLORS["text"], bordercolor=COLORS["border"],
                            lightcolor=COLORS["bg_card"], darkcolor=COLORS["bg_card"],
                            selectbackground=COLORS["accent"], selectforeground="#FFFFFF",
                            arrowcolor=COLORS["text"], background=COLORS["border"])
            style.map(name,
                      fieldbackground=[("readonly", COLORS["bg_card"]),
                                       ("disabled", COLORS["bg"])],
                      foreground=[("disabled", COLORS["text_secondary"]),
                                  ("readonly", COLORS["text"])],
                      bordercolor=[("focus", COLORS["accent"])])
        self.root.option_add("*TCombobox*Listbox.background", COLORS["bg_card"])
        self.root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", COLORS["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        style.configure("Runs.TEntry", fieldbackground=COLORS["bg"])
        style.configure("TPanedwindow", background=COLORS["bg"])
        style.configure("Sash", sashthickness=6, gripcount=0, background=COLORS["border"])

    def _center_dialog(self, dlg, w=None, h=None):
        """Ставит диалог по центру главного окна, не выходя за рабочую область.

        Без w/h меняется только положение: размер остаётся за диалогом, иначе
        окно, которое потом добавляет себе виджеты, обрезало бы их.
        """
        dlg.update_idletasks()
        size_given = bool(w and h)
        w = w or max(dlg.winfo_width(), dlg.winfo_reqwidth())
        h = h or max(dlg.winfo_height(), dlg.winfo_reqheight())
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3
        area = self._work_area()
        if area:
            ax, ay, aw, ah = area
            x = max(ax, min(x, ax + aw - w - 16))
            y = max(ay, min(y, ay + ah - h - 40))
        pos = f"+{max(0, x)}+{max(0, y)}"
        dlg.geometry(f"{w}x{h}{pos}" if size_given else pos)

    def _on_toplevel_map(self, event):
        """Первое появление диалога: если он открылся не над главным окном
        (Windows по умолчанию кладёт новые окна в левый верхний угол экрана),
        переносит его в центр главного окна. Диалоги, которые уже поставили
        себя сами (сравнение версий, simpledialog), не трогаются."""
        dlg = event.widget
        if not isinstance(dlg, tk.Toplevel) or getattr(dlg, "_placed_once", False):
            return
        dlg._placed_once = True
        try:
            cx = dlg.winfo_rootx() + dlg.winfo_width() // 2
            cy = dlg.winfo_rooty() + dlg.winfo_height() // 2
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            inside = (rx <= cx <= rx + self.root.winfo_width()
                      and ry <= cy <= ry + self.root.winfo_height())
            if not inside:
                self._center_dialog(dlg)
        except tk.TclError:
            pass

    def setup_ui(self):
        """Builds the main UI layout with notebook tabs and status bar."""
        self._apply_dark_theme()
        self.root.bind_class("Toplevel", "<Map>", self._on_toplevel_map, add="+")

        # Строка статуса упаковывается ПЕРВОЙ и снизу: упаковщик раздаёт место
        # в порядке упаковки, и при низком окне последний виджет обрезается
        # первым. Раньше это была именно она.
        self.status_var = tk.StringVar(value="Готов")
        status = ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W, padding=(10, 3),
                           style="Secondary.TLabel")
        status.pack(side=tk.BOTTOM, fill=tk.X)

        main = ttk.Frame(self.root, padding=(10, 8, 10, 4))
        main.pack(fill=tk.BOTH, expand=True)

        # ── Шапка в одну строку: название, установленная версия, состояние ───
        # Раньше версия занимала отдельную карточку шрифтом 16 — ~70 px высоты,
        # которых на ноутбуке не хватало самой вкладке.
        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(header, text="⚡ R7 Testovarka", style="Header.TLabel").pack(side=tk.LEFT)
        self.lbl_status_dot = ttk.Label(header, text="●  Готов", style="StatusOk.TLabel")
        self.lbl_status_dot.pack(side=tk.RIGHT)
        ver_box = ttk.Frame(header)
        ver_box.pack(side=tk.LEFT, padx=(24, 12), fill=tk.X, expand=True)
        ttk.Label(ver_box, text="Установлен:", style="Secondary.TLabel").pack(side=tk.LEFT)
        self.lbl_current = ttk.Label(ver_box, text="определяется…", style="Version.TLabel")
        self.lbl_current.pack(side=tk.LEFT, padx=(6, 0))
        # «Тень» под шапкой: одна тёмная линия — ttk.Style не умеет рисовать
        # настоящую размытую тень, это ближайшее достижимое приближение.
        shadow = tk.Frame(main, height=1, bg=COLORS["border"])
        shadow.pack(fill=tk.X, pady=(0, 6))

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_versions = ttk.Frame(self.notebook)
        self.tab_perf = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_versions, text="📦 Версии")
        self.notebook.add(self.tab_perf, text="⚡ Производительность")

        self._build_versions_tab()
        self._build_perf_tab()

    def _build_versions_tab(self):
        """Builds the distributives table and install controls.

        Кнопки и подсказка упакованы снизу ДО таблицы: при низком окне
        сжимается таблица (у неё своя прокрутка), а не панель кнопок.
        """
        tab = self.tab_versions
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 4))
        self.btn_install = ttk.Button(btn_frame, text="📥 Установить", style="Accent.TButton",
                                      command=self.install_selected, state=tk.DISABLED)
        self.btn_install.pack(side=tk.LEFT, padx=(0, 8))
        self.quiet_install_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btn_frame, text="Тихая установка",
                        variable=self.quiet_install_var).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Button(btn_frame, text="🔐 Проверить хеш-суммы",
                   command=self.check_hashes).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_frame, text="📂 Открыть папку",
                   command=self.open_distributives_folder).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_frame, text="📁 Добавить",
                   command=self.add_distributive).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_frame, text="🔄 Обновить",
                   command=self.refresh_distributives).pack(side=tk.RIGHT, padx=(6, 0))

        self.lbl_file_info = ttk.Label(
            tab, text="Выберите дистрибутив в таблице, чтобы установить его.",
            style="Secondary.TLabel")
        self.lbl_file_info.pack(side=tk.BOTTOM, anchor=tk.W, pady=(4, 0))

        ttk.Label(tab, text="Дистрибутивы (папка Distributives)", style="Secondary.TLabel").pack(
            anchor=tk.W, pady=(6, 4))
        frame = ttk.Frame(tab, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(
            frame, columns=("name", "version", "size"), show="headings",
            selectmode="browse", yscrollcommand=scroll.set, height=6)
        self.tree.heading("name", text="Имя")
        self.tree.heading("version", text="Версия")
        self.tree.heading("size", text="Размер (МБ)")
        # Растягивается только имя: версия и размер короткие, и раньше
        # таблица разносила их на полэкрана от имени.
        self.tree.column("name", width=360, anchor=tk.W, stretch=True)
        self.tree.column("version", width=150, anchor=tk.CENTER, stretch=False)
        self.tree.column("size", width=110, anchor=tk.E, stretch=False)
        self.tree.pack(fill=tk.BOTH, expand=True)
        scroll.config(command=self.tree.yview)

        self.tree.bind('<<TreeviewSelect>>', self.on_select_distributive)
        # Двойной щелчок и Enter по строке — то же, что кнопка «Установить».
        self.tree.bind('<Double-1>', lambda _e: self._install_if_selected())
        self.tree.bind('<Return>', lambda _e: self._install_if_selected())

    def _install_if_selected(self):
        """Запускает установку, только если строка выбрана и кнопка доступна."""
        if self.tree.selection() and str(self.btn_install.cget("state")) != tk.DISABLED:
            self.install_selected()

    # ---------------------- Вкладка «Производительность» ----------------------
    LOG_HINT = ("Здесь появится ход прогона.\n\n"
                "1. Отметьте тесты в списке слева. Щелчок по названию тоже "
                "включает и выключает тест.\n"
                "2. Задайте число повторов кнопками «−» и «+» или введите его "
                f"с клавиатуры ({RUNS_MIN}–{RUNS_MAX}).\n"
                "3. Нажмите «Запустить выбранные тесты».\n\n"
                "Выбор тестов и число повторов сохраняются сами.")


    def _make_runs_control(self, parent, runs_var):
        """Поле числа повторов: «−» [N] «+».

        Вместо ttk.Spinbox: у него стрелки по 8 px, в которые трудно
        попасть, и он принимал любой текст. Здесь в поле можно ввести только
        цифры, значение прижимается к RUNS_MIN..RUNS_MAX при уходе фокуса или
        Enter, стрелки ↑/↓ в поле меняют его на 1.

        Returns:
            ttk.Frame: контейнер; у него есть метод commit() — применить то,
            что введено, но ещё не подтверждено.
        """
        box = ttk.Frame(parent)
        text = tk.StringVar(value=str(runs_var.get()))

        def commit(*_):
            value = self._clamp_runs(text.get(), runs_var.get())
            if value != runs_var.get():
                runs_var.set(value)
            text.set(str(value))

        def step(delta):
            commit()
            runs_var.set(max(RUNS_MIN, min(RUNS_MAX, runs_var.get() + delta)))
            return "break"

        runs_var.trace_add("write", lambda *_: text.set(str(runs_var.get())))
        only_digits = (box.register(lambda p: p == "" or (p.isdigit() and len(p) <= 2)), "%P")
        ttk.Button(box, text="−", width=2, style="Step.TButton", takefocus=False,
                   command=lambda: step(-1)).pack(side=tk.LEFT)
        entry = ttk.Entry(box, textvariable=text, width=3, justify=tk.CENTER,
                          validate="key", validatecommand=only_digits, style="Runs.TEntry")
        entry.pack(side=tk.LEFT, padx=2)
        ttk.Button(box, text="+", width=2, style="Step.TButton", takefocus=False,
                   command=lambda: step(1)).pack(side=tk.LEFT)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Return>", commit)
        entry.bind("<Up>", lambda _e: step(1))
        entry.bind("<Down>", lambda _e: step(-1))
        box.commit = commit
        return box

    def _bind_wheel(self, widget, canvas, content):
        """Прокрутка колёсиком над списком тестов — на каждом его виджете.

        Не bind_all: диалоги сравнения версий и Batch-режима при закрытии
        зовут unbind_all("<MouseWheel>") и отключили бы прокрутку и здесь.
        """
        def _on_wheel(event):
            if content.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        def _walk(w):
            w.bind("<MouseWheel>", _on_wheel, add="+")
            for child in w.winfo_children():
                _walk(child)
        _walk(widget)

    def _build_test_list(self, parent):
        """Панель выбора тестов: список по группам с прокруткой.

        Returns:
            ttk.Frame: панель для Panedwindow.
        """
        panel = ttk.Frame(parent, padding=(0, 0, 8, 0))
        saved = self._load_test_selection()
        self.test_vars = {}
        self.test_runs = {}
        self._runs_controls = []

        head = ttk.Frame(panel)
        head.pack(fill=tk.X)
        ttk.Label(head, text="Тесты", style="Version.TLabel").pack(side=tk.LEFT)
        ttk.Button(head, text="Снять все", style="Small.TButton",
                   command=lambda: self._set_all_tests(False)).pack(side=tk.RIGHT)
        ttk.Button(head, text="Отметить все", style="Small.TButton",
                   command=lambda: self._set_all_tests(True)).pack(side=tk.RIGHT, padx=(0, 4))

        bulk = ttk.Frame(panel)
        bulk.pack(fill=tk.X, pady=(6, 6))
        ttk.Label(bulk, text="Повторов у отмеченных:", style="Secondary.TLabel").pack(side=tk.LEFT)
        self._bulk_runs = tk.IntVar(value=DEFAULT_TEST_RUNS)
        self._bulk_runs_control = self._make_runs_control(bulk, self._bulk_runs)
        self._bulk_runs_control.pack(side=tk.LEFT, padx=6)
        ttk.Button(bulk, text="Применить", style="Small.TButton",
                   command=self._apply_bulk_runs).pack(side=tk.LEFT)

        self.lbl_tests_summary = ttk.Label(panel, text="", style="Secondary.TLabel")
        self.lbl_tests_summary.pack(side=tk.BOTTOM, anchor=tk.W, pady=(6, 0))

        area = ttk.Frame(panel, style="Card.TFrame")
        area.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(area, bg=COLORS["bg_card"], highlightthickness=0, borderwidth=0,
                           width=10, height=160, yscrollincrement=24)
        vsb = ttk.Scrollbar(area, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = ttk.Frame(canvas, style="Card.TFrame", padding=(6, 4, 6, 6))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.columnconfigure(1, weight=1)

        ttk.Label(inner, text="Повторы", style="Secondary.TLabel",
                  background=COLORS["bg_card"]).grid(row=0, column=2, sticky=tk.E, pady=(0, 2))
        row = 1
        self._building_test_list = True
        for title, names in self._test_groups():
            grp = ttk.Label(inner, text=title, style="Group.TLabel",
                            background=COLORS["bg_card"], cursor="hand2")
            grp.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=(10 if row > 1 else 0, 2))
            # Щелчок по заголовку группы — включить всю группу, а если она уже
            # вся включена — выключить.
            grp.bind("<Button-1>", lambda _e, ns=names: self._toggle_group(ns))
            row += 1
            for name in names:
                entry = saved.get(name) or self._default_test_entry(name)
                default = self._default_test_entry(name)
                var = tk.BooleanVar(value=bool(entry.get("enabled", default["enabled"])))
                runs_var = tk.IntVar(value=self._clamp_runs(entry.get("runs"), default["runs"]))
                ttk.Checkbutton(inner, variable=var, style="Card.TCheckbutton",
                                takefocus=False).grid(row=row, column=0, sticky=tk.W, pady=1)
                lbl = ttk.Label(inner, text=name, style="Card.TLabel", cursor="hand2")
                lbl.grid(row=row, column=1, sticky=tk.W, padx=(2, 12))
                lbl.bind("<Button-1>", lambda _e, v=var: v.set(not v.get()))
                ctl = self._make_runs_control(inner, runs_var)
                ctl.grid(row=row, column=2, sticky=tk.E, pady=1)
                self._runs_controls.append(ctl)

                def _refresh(*_a, v=var, label=lbl):
                    label.configure(foreground=COLORS["text"] if v.get()
                                    else COLORS["text_secondary"])
                    self._on_test_selection_changed()
                var.trace_add("write", _refresh)
                runs_var.trace_add("write", lambda *_a: self._on_test_selection_changed())
                _refresh()
                self.test_vars[name] = var
                self.test_runs[name] = runs_var
                row += 1
        self._building_test_list = False

        def _on_inner_configure(_event):
            # Холст по ширине содержимого: панель просит ровно столько места,
            # сколько занимают строки, остальное отдаётся логу.
            canvas.configure(scrollregion=canvas.bbox("all"), width=inner.winfo_reqwidth())
        inner.bind("<Configure>", _on_inner_configure)
        self._bind_wheel(area, canvas, inner)
        self._update_tests_summary()
        return panel

    def _set_all_tests(self, enabled):
        for var in self.test_vars.values():
            var.set(enabled)

    def _toggle_group(self, names):
        enable = not all(self.test_vars[n].get() for n in names)
        for n in names:
            self.test_vars[n].set(enable)

    def _commit_runs_inputs(self):
        """Применяет недоподтверждённый ввод во всех полях повторов."""
        for ctl in getattr(self, "_runs_controls", []):
            ctl.commit()

    def _apply_bulk_runs(self):
        """«Применить»: число повторов из общего поля — всем отмеченным тестам."""
        self._bulk_runs_control.commit()
        value = self._bulk_runs.get()
        for name, var in self.test_vars.items():
            if var.get():
                self.test_runs[name].set(value)

    def _selection_summary(self):
        """(отмечено, всего, сумма повторов, отмечен ли экспорт)."""
        chosen = [n for n, v in self.test_vars.items() if v.get()]
        runs = 0
        for n in chosen:
            try:
                runs += int(self.test_runs[n].get())
            except (tk.TclError, ValueError):
                pass
        return (len(chosen), len(self.test_vars), runs,
                any(n in self.EXPORT_TESTS for n in chosen))

    def _update_tests_summary(self):
        chosen, total, runs, export = self._selection_summary()
        text = f"Отмечено {chosen} из {total} · всего повторов: {runs}"
        if export:
            text += " · экспорт идёт долго"
        try:
            self.lbl_tests_summary.config(text=text)
            self.btn_run_perf.config(
                state=tk.NORMAL if chosen and not self._perf_running else tk.DISABLED)
        except (AttributeError, tk.TclError):
            pass  # виджеты ещё не созданы — первая сводка при сборке панели

    def _on_test_selection_changed(self):
        """Сводка сразу, сохранение в selected_tests.json — с задержкой.

        Раньше выбор сохранялся только при нажатии «Запустить», и закрытое
        без запуска окно теряло всё, что пользователь отметил.
        """
        self._update_tests_summary()
        if getattr(self, "_building_test_list", False):
            return  # начальные значения при сборке — сохранять нечего
        pending = getattr(self, "_save_selection_job", None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except tk.TclError:
                pass
        self._save_selection_job = self.root.after(800, self._save_test_selection)

    def _set_busy_indicator(self, busy, text=None):
        """Индикатор в правом верхнем углу: «● Готов» / «● Идёт прогон»."""
        try:
            self.lbl_status_dot.config(
                text=f"●  {text or ('Идёт прогон' if busy else 'Готов')}",
                style="StatusErr.TLabel" if busy else "StatusOk.TLabel")
        except (AttributeError, tk.TclError):
            pass

    def _clear_test_log(self):
        self.test_log.delete("1.0", tk.END)
        self._log_hint_shown = False

    def _build_perf_tab(self):
        """Builds the performance tab: test list | log, run bar, tools.

        Панели кнопок упакованы снизу ДО содержимого: при низком окне
        сжимаются список тестов и лог (у обоих своя прокрутка), а кнопка
        «Запустить» остаётся видна всегда.
        """
        tab = self.tab_perf

        # ── Нижняя панель: запуск, прогресс, инструменты ──────────────────────
        tools = ttk.Frame(tab)
        tools.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 2))
        ttk.Label(tools, text="Инструменты:", style="Secondary.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        for caption, command in (("🚀 Batch-режим (все версии)", self.run_batch_mode),
                                 ("📊 Сравнить версии", self.compare_versions),
                                 ("📈 Тренды", self.show_trends),
                                 ("📄 Тестовые файлы", self.compare_file_sizes)):
            ttk.Button(tools, text=caption, command=command).pack(side=tk.LEFT, padx=(0, 6))

        run_row = ttk.Frame(tab)
        run_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        self.btn_run_perf = ttk.Button(
            run_row, text="▶ Запустить выбранные тесты", style="Accent.TButton",
            command=self.run_spreadsheet_test)
        self.btn_run_perf.pack(side=tk.LEFT)
        self.btn_stop_perf = ttk.Button(
            run_row, text="⏹ Остановить", command=self._request_stop_perf_test,
            state=tk.DISABLED)
        self.btn_stop_perf.pack(side=tk.LEFT, padx=(8, 12))
        self.progress_var = tk.DoubleVar(value=0)
        self.lbl_progress = ttk.Label(run_row, text="", width=5, anchor=tk.E,
                                      style="Secondary.TLabel")
        self.lbl_progress.pack(side=tk.RIGHT)
        ttk.Progressbar(run_row, variable=self.progress_var, maximum=100,
                        mode="determinate").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.progress_var.trace_add(
            "write", lambda *_: self.lbl_progress.config(
                text=f"{self.progress_var.get():.0f}%" if self.progress_var.get() else ""))

        # ── Список тестов | лог — с перетаскиваемой границей ─────────────────
        paned = ttk.Panedwindow(tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        paned.add(self._build_test_list(paned), weight=0)

        log_panel = ttk.Frame(paned, padding=(8, 0, 0, 0))
        paned.add(log_panel, weight=1)
        log_head = ttk.Frame(log_panel)
        log_head.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(log_head, text="Лог прогона", style="Version.TLabel").pack(side=tk.LEFT)
        ttk.Button(log_head, text="📂 Папка отчётов", style="Small.TButton",
                   command=lambda: os.startfile(str(self.reports_folder))).pack(side=tk.RIGHT)
        ttk.Button(log_head, text="Очистить", style="Small.TButton",
                   command=self._clear_test_log).pack(side=tk.RIGHT, padx=(0, 4))

        log_frame = ttk.Frame(log_panel)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.test_log = tk.Text(log_frame, font=FONT_LOG, bg=COLORS["log_bg"],
                                fg=COLORS["text"], insertbackground=COLORS["text"],
                                borderwidth=0, highlightthickness=0, wrap=tk.WORD,
                                width=40, height=8, padx=8, pady=6)
        self.test_log.tag_configure("INFO", foreground=COLORS["success"])
        self.test_log.tag_configure("WARN", foreground=COLORS["warn"])
        self.test_log.tag_configure("ERROR", foreground=COLORS["error"])
        self.test_log.tag_configure("HINT", foreground=COLORS["text_secondary"],
                                    font=FONT_UI, spacing1=2)
        scroll_log = ttk.Scrollbar(log_frame, command=self.test_log.yview)
        self.test_log.configure(yscrollcommand=scroll_log.set)
        scroll_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.test_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Лог — только для чтения с клавиатуры: копировать можно (Ctrl+C,
        # Ctrl+A), печатать в него — нет. Программа пишет в него через insert.
        self.test_log.bind("<Key>", lambda e: None if e.state & 0x4 else "break")
        self.test_log.insert("1.0", self.LOG_HINT, "HINT")
        self._log_hint_shown = True
        self._update_tests_summary()


    def detect_current_version(self):
        """Reads Windows registry and updates the "Текущая версия" label.

        self.current_version_info обновляется синхронно в вызывающем потоке —
        это обычное присваивание Python, оно безопасно из любого потока и
        нужно немедленно там, где detect_current_version вызывается из
        фонового потока и код сразу же читает результат (например,
        _batch_worker). Обновление самого виджета — единственная часть,
        которую нельзя делать не из главного потока, — маршалится туда через
        root.after(), если вызов пришёл не из главного потока.
        """
        info = self._read_current_version_from_registry()
        self.current_version_info = info

        def _update_label():
            if info:
                self.lbl_current.config(
                    text=self._short_version_text(info), foreground=COLORS["success"])
            else:
                self.lbl_current.config(text="Не установлена", foreground=COLORS["warn"])

        if threading.current_thread() is threading.main_thread():
            _update_label()
        else:
            self.root.after(0, _update_label)


    def refresh_distributives(self):
        """Rescans the Distributives folder and refreshes the table."""
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.distributives = []
        files = list(self.distributives_folder.glob("*.msi")) + list(self.distributives_folder.glob("*.exe"))
        if not files:
            self.btn_install.config(state=tk.DISABLED)
            self.status_var.set("Дистрибутивы не найдены")
            return
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for f in files:
            ver = self._extract_version(f.stem) or "—"
            size_mb = round(f.stat().st_size / (1024 * 1024), 1)
            self.distributives.append({"path": f, "name": f.name})
            self.tree.insert("", tk.END, iid=str(len(self.distributives) - 1),
                              values=(f.name, ver, size_mb))
        self.status_var.set(f"Найдено: {len(files)}")


    def on_select_distributive(self, event):
        """Handles Treeview selection — enables Install button and shows file size."""
        sel = self.tree.selection()
        if sel and self.distributives:
            idx = int(sel[0])
            self.selected_distributive = self.distributives[idx]
            self.btn_install.config(state=tk.NORMAL)
            mb = self.selected_distributive["path"].stat().st_size / (1024 * 1024)
            self.lbl_file_info.config(text=f"{self.selected_distributive['name']} ({mb:.1f} МБ)")
        else:
            self.btn_install.config(state=tk.DISABLED)

    # msiexec.exe возвращает 3010 при успешном завершении, если требуется
    # перезагрузка — это тоже успех, а не ошибка.
    _MSIEXEC_SUCCESS_CODES = (0, 3010)


    def uninstall_current_version(self):
        """Silently uninstalls the currently detected R7-Office version.

        Returns:
            bool: True если удаление подтверждено (код возврата 0/3010, либо
            версия изначально не была установлена). False при таймауте или
            ненулевом коде возврата — в этом случае каталоги программы НЕ
            удаляются, чтобы не рассинхронизировать файлы с реестром.
        """
        if not self.current_version_info:
            return True
        self.status_var.set("Удаление...")
        cmd = self._build_uninstall_command(self.current_version_info)
        try:
            # shell=False: командная строка уже полностью собрана, а без
            # обёртки cmd.exe proc.kill() ниже завершает реальный процесс
            # деинсталлятора, а не промежуточный cmd.exe.
            proc = subprocess.Popen(cmd, shell=False)
        except OSError as e:
            self.status_var.set(f"⚠️ Не удалось запустить удаление: {e}")
            return False
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.status_var.set("⚠️ Удаление не завершилось за 60 сек, процесс завершён принудительно")
            return False

        if proc.returncode not in self._MSIEXEC_SUCCESS_CODES:
            self.status_var.set(f"⚠️ Удаление завершилось с кодом {proc.returncode}")
            return False

        time.sleep(3)
        for p in [r"C:\Program Files\R7-Office", r"C:\Program Files (x86)\R7-Office"]:
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)
        return True

    def install_version(self, path, quiet=True):
        """Installs an R7-Office distributive.

        Args:
            path: Path object pointing to the .msi or .exe installer.
            quiet: If True (default), adds /quiet and installs silently.
                If False, the installer shows its normal UI.

        Returns:
            bool: True on success (return code 0 or 3010), False if the
            process timed out or exited with any other code.
        """
        self.status_var.set(f"Установка {path.name}...")
        if path.suffix == ".msi":
            cmd = ["msiexec", "/i", str(path), "/norestart"]
        else:
            cmd = [str(path)]
        if quiet:
            cmd.append("/quiet")
        # Тихая установка не требует участия пользователя — 5 минут с запасом.
        # Интерактивная показывает мастер установки, который пользователь
        # проходит вручную, поэтому таймаут увеличен, чтобы не убить процесс
        # посреди диалогов (EULA, выбор папки и т.д.).
        timeout_sec = 300 if quiet else 1800
        # shell=False: список аргументов не требует обёртки cmd.exe, и без неё
        # proc.kill() по таймауту завершает реальный установщик, а не cmd.exe.
        try:
            proc = subprocess.Popen(cmd, shell=False)
        except OSError as e:
            self.status_var.set(f"⚠️ Не удалось запустить установку: {e}")
            return False
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.status_var.set(
                f"⚠️ Установка не завершилась за {timeout_sec // 60} мин, процесс завершён принудительно")
            return False
        if proc.returncode not in self._MSIEXEC_SUCCESS_CODES:
            self.status_var.set(f"⚠️ Установка завершилась с кодом {proc.returncode}")
            return False
        time.sleep(3)
        self.detect_current_version()
        return True

    def install_selected(self):
        """Confirms and launches uninstall + install in a background thread."""
        if not self.selected_distributive:
            return
        if self.current_version_info:
            if not messagebox.askyesno("Подтверждение",
                                       f"Удалить текущую и установить\n{self.selected_distributive['name']}?"):
                return
        self.btn_install.config(state=tk.DISABLED)
        quiet = self.quiet_install_var.get()

        def worker():
            uninstalled = self.uninstall_current_version()
            installed = False
            if uninstalled:
                installed = self.install_version(self.selected_distributive["path"], quiet=quiet)

            if installed:
                self.root.after(0, lambda: messagebox.showinfo("Готово", "Установка завершена"))
            elif not uninstalled:
                self.root.after(0, lambda: messagebox.showerror(
                    "Ошибка", "Не удалось удалить текущую версию — установка отменена.\n"
                             "Подробности в строке статуса."))
            else:
                self.root.after(0, lambda: messagebox.showerror(
                    "Ошибка", "Установка не завершилась успешно.\n"
                             "Подробности в строке статуса."))
            self.root.after(0, self.refresh_distributives)
            self.root.after(0, self.detect_current_version)
            self.root.after(0, lambda: self.btn_install.config(state=tk.NORMAL))
        threading.Thread(target=worker, daemon=True).start()

    def add_distributive(self):
        """Opens a file dialog to copy installers into the Distributives folder."""
        files = filedialog.askopenfilenames(filetypes=[("Installer", "*.msi *.exe")])
        for f in files:
            dst = self.distributives_folder / Path(f).name
            shutil.copy2(f, dst)
        self.refresh_distributives()

    def open_distributives_folder(self):
        """Opens the Distributives folder in Windows Explorer."""
        os.startfile(str(self.distributives_folder))


    # ---------------------- Лог ----------------------
    def add_test_log(self, msg):
        """Appends a timestamped, severity-colored message to the performance log.

        Severity is inferred from the leading emoji already used consistently
        throughout the codebase (❌/⚠️ for errors/warnings, everything else
        default) — no call site elsewhere in the file needs to change.

        Args:
            msg: The text to append.
        """
        try:
            if getattr(self, "_log_hint_shown", False):
                # Первое настоящее сообщение убирает подсказку «как запустить».
                self.test_log.delete("1.0", tk.END)
                self._log_hint_shown = False
            if msg.startswith("❌"):
                tag = "ERROR"
            elif msg.startswith("⚠️"):
                tag = "WARN"
            else:
                tag = "INFO"
            line = f"[{datetime.now():%H:%M:%S}] {msg}\n"
            self.test_log.insert(tk.END, line, tag)
            self.test_log.see(tk.END)
            # update_idletasks (не update!): перерисовывает накопившиеся
            # изменения без обработки очереди событий Tk. add_test_log
            # вызывается сотнями раз за прогон из фоновых потоков — update()
            # заходил бы в главный цикл Tk и обрабатывал там события, включая
            # нажатия кнопок, реентерабельно посреди стека фонового потока.
            self.root.update_idletasks()
        except Exception:
            print(msg)

    def _set_perf_progress(self, done, total):
        """Updates the Performance tab's progress bar (0-100%). Safe to call
        even if the widget doesn't exist yet or the app is in another mode.
        Marshals the actual Tk update onto the main thread via root.after,
        since this is called from the worker thread during a test run."""
        try:
            pct = 100 * done / total if total else 0
            self.root.after(0, lambda: self.progress_var.set(pct))
        except Exception:
            pass

    # ---------------------- Стресс-тест таблиц ----------------------
    def _reset_perf_buttons(self):
        """Возвращает кнопки вкладки «Производительность» в состояние покоя.

        Вызывается из главного потока (через root.after) в finally-обёртке
        рабочего потока — при любом исходе: нормальном завершении,
        досрочной остановке или исключении.
        """
        self._perf_running = False
        self._set_busy_indicator(False)
        try:
            self.btn_run_perf.config(state=tk.NORMAL)
            self.btn_stop_perf.config(state=tk.DISABLED)
        except Exception:
            pass
        self._update_tests_summary()  # «Запустить» недоступна, если ничего не отмечено

    def _request_stop_perf_test(self):
        """Обработчик кнопки «⏹ Остановить»: просит рабочий поток прерваться
        между операциями. Р7-Офис закрывается штатно, отчёт по уже
        выполненным операциям всё равно сохраняется."""
        self.perf_stop_event.set()
        self.btn_stop_perf.config(state=tk.DISABLED)
        self.add_test_log("⏹ Запрошена остановка теста...")

    def run_spreadsheet_test(self):
        """Entry point for the stress test — validates prerequisites then launches worker thread."""
        if self._perf_running:
            messagebox.showwarning("Тест уже выполняется",
                                   "Дождитесь завершения текущего прогона или нажмите «Остановить».")
            return
        if self._batch_running:
            messagebox.showwarning("Выполняется Batch-режим",
                                   "Оба режима управляют клавиатурой Р7-Офис и не могут "
                                   "работать одновременно. Дождитесь завершения Batch-режима.")
            return
        if not ctypes.windll.shell32.IsUserAnAdmin():
            messagebox.showerror(
                "Ошибка прав",
                "Стресс-тест требует запуска от имени администратора.\n"
                "Перезапустите программу с правами администратора."
            )
            return
        if not self.current_version_info:
            messagebox.showwarning("Нет версии", "Р7-Офис не установлен или не определён.")
            return
        _warn = _missing_cdp_warning()
        if _warn and not messagebox.askyesno("Нет доступа к интерфейсу Р7", _warn):
            return
        if not env.PYAUTOGUI_OK or not pyperclip or not env.EXCEL_OK or not env.WIN32_OK:
            missing = []
            if not env.PYAUTOGUI_OK: missing.append("pyautogui")
            if not pyperclip: missing.append("pyperclip")
            if not env.EXCEL_OK: missing.append("openpyxl")
            if not env.WIN32_OK: missing.append("pywin32")
            messagebox.showerror("Ошибка",
                                 f"Отсутствуют библиотеки:\n{', '.join(missing)}\n"
                                 f"Установите: pip install " + " ".join(missing))
            return
        enabled = ({n for n, v in self.test_vars.items() if v.get()}
                   if self.test_vars else set(self.TEST_DEFINITIONS))
        if not enabled:
            messagebox.showwarning("Нет тестов", "Выберите хотя бы один тест для выполнения.")
            return
        # Снимок self.test_runs на главном потоке — как enabled_tests, чтобы
        # фоновый поток не трогал Tk-переменные напрямую. Сначала применяется
        # ввод, который ещё не подтверждён (число набрано, фокус не уходил).
        self._commit_runs_inputs()
        runs_snapshot = {}
        for n, v in self.test_runs.items():
            try:
                runs_snapshot[n] = self._clamp_runs(v.get(), self._default_test_entry(n)["runs"])
            except tk.TclError:
                runs_snapshot[n] = self._default_test_entry(n)["runs"]
        self._save_test_selection()

        self.perf_stop_event.clear()
        self._perf_running = True
        self._set_busy_indicator(True)
        self.progress_var.set(0)
        self.btn_run_perf.config(state=tk.DISABLED)
        self.btn_stop_perf.config(state=tk.NORMAL)

        def _worker():
            try:
                self._spreadsheet_worker(enabled, runs_snapshot, self.perf_stop_event)
            finally:
                # root.after — восстановление кнопок делает виджеты только
                # из главного потока. Покрывает любой исход: нормальное
                # завершение, досрочный return, необработанное исключение.
                self.root.after(0, self._reset_perf_buttons)

        threading.Thread(target=_worker, daemon=True).start()

    def _spreadsheet_worker(self, enabled_tests=None, test_runs=None, stop_event=None):
        """Runs selected spreadsheet performance tests sequentially and saves reports.

        Args:
            enabled_tests: Set of test-name strings to execute. None → all tests.
            test_runs: Dict of test-name → run count, snapshotted from
                self.test_runs on the main thread. None → empty dict.
            stop_event: threading.Event — установка прерывает прогон между
                операциями (и между повторами внутри одной операции). Р7-Офис
                при этом закрывается штатно, отчёт по уже выполненным
                операциям сохраняется. None → создаётся локально, никогда не
                устанавливается (для вызовов в обход UI).
        """
        if enabled_tests is None:
            enabled_tests = set(self.TEST_DEFINITIONS)
        if test_runs is None:
            test_runs = {}
        if stop_event is None:
            stop_event = threading.Event()
        self.add_test_log("\n🚀 ЗАПУСК СТРЕСС-ТЕСТА ТАБЛИЦ")
        # Диагностика раньше по вызовам: если пакеты requests/websocket-client
        # не видны интерпретатору, которым реально запущен инструмент (venv
        # vs системный python — см. CLAUDE.md, "смотреть на интерпретатор, а
        # не на код"), CDP-триггер отключается ещё до первой попытки
        # подключения, а без этой строки это неотличимо от "порт занят"/
        # "Р7 запущен без --ascdesktop-support-debug-info".
        self.add_test_log(f"🔌 WebDriver: WEBDRIVER_OK={env.WEBDRIVER_OK}")
        # Окружение — до запуска Р7, пока он не грузит систему (пункт 11 аудита).
        self._run_environment = self._capture_environment()

        # ----- 1. Поиск тестового файла -----
        def find_test_file():
            """Searches known directories for the 50K-row test spreadsheet.

            Ignores Office lock-файлы (`~$...`) — они появляются, пока файл
            открыт в другом приложении (или остаются после сбоя), и без
            фильтра glob() находил их вместо настоящего файла.

            Returns:
                Path: Path to the found file, or None.
            """
            patterns = ["файл-для-теста-Р7-офис-50К*.xlsx", "файл-для-теста-Р7-офис-50К*.xls", "*50К*.xlsx"]
            search_dirs = [self.test_files_folder, BASE_DIR, Path.home() / "Downloads", Path.home() / "Загрузки", Path.cwd()]

            real_file = None
            lock_files = []
            seen_locks = set()
            for sd in search_dirs:
                if not sd.exists():
                    continue
                for pat in patterns:
                    for f in sd.glob(pat):
                        if f.name.startswith("~$"):
                            if f not in seen_locks:
                                seen_locks.add(f)
                                lock_files.append(f)
                        elif real_file is None:
                            real_file = f
                if real_file is not None:
                    break

            # Lock-файл рядом с настоящим файлом — не нужен, чистим его
            # заранее, чтобы он не мешал следующему запуску теста.
            for lock in lock_files:
                real_name = lock.name[2:]
                if lock.with_name(real_name).exists():
                    self.add_test_log(f"⚠️ Рядом с рабочим файлом найден lock-файл ({lock.name}) — удаляю.")
                    try:
                        lock.unlink()
                    except OSError as e:
                        self.add_test_log(f"❌ Не удалось удалить lock-файл: {e}")

            if real_file is not None:
                return real_file

            if lock_files:
                lock = lock_files[0]
                self.add_test_log(
                    f"⚠️ Настоящий тестовый файл не найден — есть только lock-файл "
                    f"({lock.name}). Файл открыт в другом приложении либо остался "
                    f"после сбоя. Удаляю lock-файл.")
                try:
                    lock.unlink()
                except OSError as e:
                    self.add_test_log(f"❌ Не удалось удалить lock-файл: {e}")
                new_path = self.test_files_folder / lock.name[2:]
                try:
                    self._generate_fixture(new_path, rows=50_000, profile="flat")
                    self.add_test_log(f"✅ Создан новый тестовый файл: {new_path}")
                    return new_path
                except Exception as e:
                    self.add_test_log(f"❌ Не удалось создать тестовый файл: {e}")

            return None

        test_file = find_test_file()
        if not test_file:
            self.add_test_log("❌ Тестовый файл не найден.")
            return
        self.add_test_log(f"✅ Найден файл: {test_file}")

        # ----- 2. Вспомогательные функции для окон -----
        def find_r7_window():
            """Returns the hwnd of the visible R7-Office window, or None —
            только окно процесса Р7 (см. _find_r7_window)."""
            return self._find_r7_window(test_file.stem)

        def wait_for_window(title_part, timeout=60):
            """Polls for a visible window containing title_part, sets it foreground when found.

            Args:
                title_part: Substring to search for in window titles.
                timeout: Maximum seconds to wait.

            Returns:
                bool: True if window found, False on timeout.
            """
            import win32gui
            start = time.perf_counter()
            while time.perf_counter() - start < timeout:
                wins = []
                def enum_cb(hwnd, _):
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        # Чужое окно с тем же текстом в заголовке (вкладка
                        # браузера) давало «холодный старт 0.00 с».
                        if title_part.lower() in title.lower() and self._is_r7_window(hwnd):
                            wins.append(hwnd)
                win32gui.EnumWindows(enum_cb, wins)
                if wins:
                    # Момент появления окна снимается ДО SetForegroundWindow —
                    # это граница холодного старта (L1).
                    self._window_seen_at = time.perf_counter()
                    try:
                        win32gui.SetForegroundWindow(wins[0])
                    except Exception:
                        pass
                    return True
                # Шаг опроса = разрешение cold_start_ms. Прежние 0.5 с
                # квантовали холодный старт на полсекунды (аудит 29.09.2026);
                # EnumWindows стоит ~1 мс, 30 мс его не нагружают.
                time.sleep(self.WINDOW_POLL_SEC)
            return False

        def maximize_window():
            """Fixes the R7-Office window to R7_WINDOW_W×R7_WINDOW_H (L3,
            этап 3) instead of a plain maximize — see _fix_r7_window_geometry.

            Returns:
                bool: True if window was found and geometry was applied.
            """
            hwnd = find_r7_window()
            return bool(self._fix_r7_window_geometry(hwnd, log_cb=self.add_test_log))

        def focus_window():
            """Brings the R7-Office window to the foreground.

            Returns:
                bool: True if window was found and focused.
            """
            hwnd = find_r7_window()
            if hwnd:
                ok = self._focus_r7_window(hwnd)
                time.sleep(0.3)
                return ok
            return False

        def close_update_dialog(search_timeout=0):
            return self._close_update_dialog_if_exists(search_timeout=search_timeout)

        def post_action_delay(seconds=0.5):
            """Waits after an operation completes — called outside measure() timing window."""
            time.sleep(seconds)

        # ----- 3. Запуск Р7 и замер времени открытия -----
        r7_path = self._find_r7_path()
        if not r7_path:
            self.add_test_log("❌ Р7-Офис не найден.")
            return

        def launch_r7():
            """Холодный запуск Р7 с тестовым файлом и подготовка окна.

            Общий код основного запуска и дополнительных циклов «Повторного
            открытия файла» — чтобы они не разъехались (аудит 29.09.2026).

            Returns:
                tuple | None: (open_start, window_appeared_ts, setup_elapsed)
                в perf_counter, либо None, если окно не появилось.
            """
            # L1 (этап 3): без очистки кеша «открытие файла» мерило бы не
            # холодный старт, а тёплый — R7-Офис переиспользует temp-объекты
            # прошлого запуска.
            _cleared = self._clear_r7_cache()
            if _cleared:
                self.add_test_log(f"🧹 Очищено {_cleared} временных объектов Р7 из %TEMP% (холодный старт)")
            # Плюс файловый кэш ОС: иначе DLL Р7 и тестовый файл читаются из
            # памяти, и «холодный» старт на деле тёплый (пункт 11 аудита).
            # Сначала — спокойная система (хвост закрытия прошлого экземпляра).
            self._wait_system_quiet()
            self._purge_os_file_cache()

            self.add_test_log(f"🔄 Запуск Р7-Офис с файлом: {test_file.name}")
            # Порт проверяется ДО старта секундомера — иначе TCP-connect_ex
            # внутри _prepare_webdriver_launch попадает в open_elapsed.
            self._remove_stale_lock_files(test_file)
            debug_args = self._prepare_webdriver_launch(filename_hint=test_file.name)
            self._x2t()                       # отслеживатель x2t — до запуска Р7
            self._open_disk_before = _disk_snapshot()
            open_start = time.perf_counter()
            # shell=False: с shell=True в холодный старт попадал запуск cmd.exe,
            # а proc.kill() убил бы cmd.exe, а не Р7 (см. правила в CLAUDE.md).
            subprocess.Popen([r7_path, str(test_file), *debug_args])

            if not wait_for_window(test_file.stem, timeout=60) and not wait_for_window("Р7-Офис", timeout=10):
                return None
            # L1: граница холодного/тёплого старта — окно уже нарисовано ОС,
            # но документ Р7 ещё не распарсил. Момент снимается ДО подготовки
            # окна, иначе она сдвинула бы границу cold/warm на своё время.
            window_ts = self._window_seen_at

            # Подготовка окна (геометрия, фокус, снятие диалога обновления).
            # Р7 грузит документ параллельно с ней, поэтому из открытия она
            # не вычитается — засекается только для лога.
            _setup_start = time.perf_counter()
            maximize_window()
            focus_window()
            # Один проход без опроса: дальше диалог обновления ловит фоновый монитор.
            close_update_dialog(search_timeout=0)
            return open_start, window_ts, time.perf_counter() - _setup_start

        # ----- 3.0 Повторное открытие файла (аудит 29.09.2026, пункт 4) -----
        # Одно открытие — одна точка, медиану и MAD из неё не посчитать, а
        # сравнение версий по «Открытию файла» было самым шумным. Лишние
        # циклы «запуск → готовность → закрытие» идут ДО основного запуска,
        # основной — последний повтор, Р7 после него остаётся для операций.
        open_runs_n = 1
        if self.OPEN_TEST_NAME in enabled_tests:
            open_runs_n = max(1, int(test_runs.get(self.OPEN_TEST_NAME, self.DEFAULT_OPEN_RUNS)))
        extra_opens = []   # [{"open_elapsed", "cold_start_ms", "warm_start_ms", "status"}]
        for _k in range(open_runs_n - 1):
            if stop_event.is_set():
                break
            self.add_test_log(f"⏳ Повторное открытие файла: {_k + 1}/{open_runs_n}")
            _l = launch_r7()
            if _l is None:
                self.add_test_log("❌ Окно Р7 не появилось — повторы открытия прерваны.")
                self._terminate_r7_processes(log_cb=self.add_test_log)
                break
            _os, _wts, _ = _l
            _ok = self._wait_until_r7_ready(find_r7_window, timeout=120)
            _disk = _disk_delta(self._open_disk_before, _disk_snapshot(),
                                self._matches_r7_process, self._x2t_since(_os))
            if _disk:
                self.add_test_log(f"   💽 Открытие: {_format_disk(_disk)}")
            _t = self._split_open_timing(_os, _wts, self._ready_at)
            extra_opens.append({"x2t": X2tTracker.summarize(self._x2t_since(_os)),
                                "disk": _disk,
                                "open_elapsed": self._ready_at - _os,
                                "cold_start_ms": _t["cold_start_ms"],
                                "warm_start_ms": _t["warm_start_ms"],
                                "status": "ok" if _ok else "timeout",
                                "ready_marker": self._ready_marker})
            self.add_test_log(f"   ✅ открытие {_k + 1}: {self._ready_at - _os:.3f} сек")
            self._close_r7_gracefully(find_r7_window(), log_cb=self.add_test_log, timeout=15)
            self._close_webdriver_connector()
            # Ждём, пока процессы Р7 уйдут: иначе следующий запуск отдаст
            # файл в живой экземпляр, и это будет уже не холодный старт.
            _gone_deadline = time.perf_counter() + 15
            while time.perf_counter() < _gone_deadline:
                self._r7_pids = None
                if not self._get_r7_processes(log_cb=lambda *_a: None):
                    break
                time.sleep(0.2)
            else:
                self._terminate_r7_processes(log_cb=self.add_test_log)

        _launched = launch_r7()
        if _launched is None:
            self.add_test_log("❌ Окно Р7 не появилось.")
            return
        open_start, _window_appeared_ts, _setup_elapsed = _launched

        # Фоновый мониторинг окна обновления на весь период теста
        _upd_stop = threading.Event()
        threading.Thread(
            target=self._monitor_update_dialog,
            args=(_upd_stop,),
            daemon=True,
        ).start()
        self.add_test_log("🔍 Запущен мониторинг окна обновления (проверка каждые 2 сек)")

        # Фоновый семплер ресурсов (этап 2, H3) — создаётся здесь (не внутри
        # try ниже), тем же паттерном, что и _upd_stop чуть выше: чтобы имя
        # было гарантированно определено к моменту finally, даже если try
        # упадёт на первой же строке. .start() — позже, у начала прогона
        # операций (см. там же), здесь ещё рано: RAM/CPU только формируются
        # открытием файла, замерять эту фазу как часть теста не нужно.
        _resource_sampler = ResourceSampler(
            get_procs=self._get_r7_processes,
            connector=self._webdriver_connector,
            interval=1.0,
            log_cb=self.add_test_log,
        )

        _r7_closed = False   # штатное закрытие прошло — finally не трогает Р7 (G-05)
        try:
            data_ready = self._wait_until_r7_ready(find_r7_window, timeout=120)
            _open_disk = _disk_delta(self._open_disk_before, _disk_snapshot(),
                                     self._matches_r7_process, self._x2t_since(open_start))
            if _open_disk:
                self.add_test_log(f"   💽 Открытие: {_format_disk(_open_disk)}")
            # Начало простоя, а не момент возврата — см. _wait_until_r7_ready.
            _ready_ts = self._ready_at
            # Подготовка окна больше НЕ вычитается (аудит 29.09.2026): Р7
            # грузит документ в своём процессе параллельно с ней, и вычитание
            # занижало открытие на всё время подготовки.
            open_elapsed = _ready_ts - open_start
            # L1: раздельные холодный/тёплый старт — см. _split_open_timing.
            # window_found=True: цикл ожидания окна выше уже вернул бы
            # False на всю функцию, если бы окно не появилось.
            _open_timing = self._split_open_timing(
                open_start, _window_appeared_ts, _ready_ts)
            cold_start_ms = _open_timing["cold_start_ms"]
            warm_start_ms = _open_timing["warm_start_ms"]
            self.add_test_log(
                f"✅ Файл открыт за {open_elapsed:.2f} сек "
                f"(холодный старт {cold_start_ms / 1000:.2f} с, тёплый {warm_start_ms / 1000:.2f} с; "
                f"{'данные загружены' if data_ready else 'таймаут — возможна частичная загрузка'};"
                f" подготовка окна {_setup_elapsed:.2f} сек шла параллельно с загрузкой)")

            if not focus_window():
                _upd_stop.set()
                self.add_test_log("❌ Окно Р7-Офис недоступно после открытия файла — тест прерван")
                return

            # Подключаемся к CDP до снятия базового снимка: без соединения
            # снимок был бы пустым, и вычитать из дампов меню стало бы нечего.
            self._cdp_ensure_connected()
            # Базовый DOM-снимок ДО первой операции — см. _cdp_dump_ui и
            # _capture_cdp_ui_baseline (issue #9).
            self._capture_cdp_ui_baseline()
            # Один раз за запуск: найден ли внутренний api редактора. От этого
            # зависит, пойдут тесты через CDP или клавишами.
            self._cdp_log_api_info()
            self._suspend_autosave()

            # ----- 3.5 Мониторинг ресурсов ------------------------------------------------
            self._r7_pids = None  # сбросить кэш перед новым поиском
            self._x2t_logged_pids = set()  # сбросить дедуп x2t перед новым тестом
            self._restore_unavailable_logged = False
            r7_procs = self._get_r7_processes()
            if env.PSUTIL_OK and r7_procs:
                try:
                    _init_ram = round(
                        sum(p.memory_info().rss for p in r7_procs) / (1024 * 1024), 1
                    )
                    pids_str = ", ".join(str(p.pid) for p in r7_procs)
                    self.add_test_log(
                        f"🔍 Поиск процесса Р7: найдено {len(r7_procs)} процессов "
                        f"(PID: {pids_str}), суммарная RAM = {_init_ram:.1f} МБ"
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    self.add_test_log(f"🔍 Найдено {len(r7_procs)} процессов Р7, RAM недоступна")
            else:
                self.add_test_log(
                    "⚠️ Процесс Р7 не найден — замеры RAM/CPU будут недоступны"
                    if env.PSUTIL_OK else
                    "⚠️ psutil не установлен — замеры RAM/CPU недоступны"
                )

            # ----- 4. Тесты ----------------------------------------------------------------
            sample0 = self._sample_r7_resources(r7_procs)
            # Все повторы открытия: дополнительные циклы + основной запуск.
            _opens = extra_opens + [{
                "open_elapsed": open_elapsed, "cold_start_ms": cold_start_ms,
                "warm_start_ms": warm_start_ms,
                "status": "ok" if data_ready else "timeout",
                "ready_marker": self._ready_marker,
                "x2t": X2tTracker.summarize(self._x2t_since(open_start)),
                "disk": _open_disk}]
            _open_times = [o["open_elapsed"] for o in _opens]
            _open_statuses = [o["status"] for o in _opens]
            # Открытия — независимые холодные старты (кэш сбрасывается перед
            # каждым), систематического «прогрева» у первого нет (6 открытий
            # подряд: 9.14 / 9.00 / 9.04 / 8.98 / 9.64 / 11.70 с). Поэтому
            # первый повтор не отбрасывается — в медиану идут все.
            _open_stats, _open_first_discarded, _open_timeouts = self._select_stats_runs(
                _open_times, _open_statuses, discard_warmup=False)
            _open_disk_note, _open_x2t_waits = self._open_disk_wait_note(_opens)
            if _open_disk_note:
                self.add_test_log(f"   ⚠️ {_open_disk_note}")
            _open_median = statistics.median(_open_stats)
            _open_mad = self._mad(_open_stats)
            # Холодный/тёплый старт — медианы по тем же повторам, что вошли
            # в статистику времени.
            _all_timeout = _open_timeouts == len(_opens)
            _stat_idx = [i for i, st in enumerate(_open_statuses)
                         if _all_timeout or st != "timeout"]
            if _open_first_discarded:
                _stat_idx = _stat_idx[1:]

            def _med(key):
                vals = [_opens[i][key] for i in _stat_idx if _opens[i][key] is not None]
                return round(statistics.median(vals), 1) if vals else None

            if len(_opens) > 1:
                self.add_test_log(
                    f"   📊 Открытие файла: медиана {_open_median:.3f} сек (MAD {_open_mad:.3f}), "
                    f"{len(_open_stats)}/{len(_opens)} повторов"
                    + (" (1-й отброшен: холодный файловый кэш ОС)" if _open_first_discarded else ""))
            # Все открытия — таймаут: «время открытия» — предохранитель, а не
            # длительность (аудит 06.10.2026: прежде error оставался None).
            _open_error = ("все открытия упёрлись в таймаут — время открытия "
                           "недостоверно" if _all_timeout else None)
            if not data_ready:
                # Основной запуск — тот, на котором дальше идут тесты правки.
                self.add_test_log(f"❌ {_OPEN_NOT_READY}")
                _open_error = _open_error or _OPEN_NOT_READY
            results = [{
                "name": "Открытие файла", "time": _open_median, "error": _open_error,
                # L1 (этап 3): раздельные холодный/тёплый старт — см.
                # _split_open_timing. С аудита 29.09.2026 — медианы по повторам.
                "cold_start_ms":  _med("cold_start_ms"),
                "warm_start_ms":  _med("warm_start_ms"),
                "total_open_ms":  round(_open_median * 1000, 1),
                # Чем определена готовность на каждом повторе: "bold" — кнопка
                # «Жирный» (основной маркер), "cpu" — запасной путь и т.д.
                "ready_markers":  [o.get("ready_marker") for o in _opens],
                # Конвертация .xlsx при открытии (x2t) — по каждому повтору.
                "x2t_at_open":    [o.get("x2t") for o in _opens],
                # Диск за время открытия — по каждому повтору (_disk_delta).
                "disk_at_open":   [o.get("disk") for o in _opens],
                # Сколько x2t ждал (не работал) на каждом открытии и пометка,
                # если это ожидание «гуляет» — см. _open_disk_wait_note.
                "x2t_wait_sec":   _open_x2t_waits,
                "disk_note":      _open_disk_note,
                "runs": _open_times, "run_statuses": _open_statuses,
                "avg": sum(_open_times) / len(_open_times),
                "min": min(_open_times), "max": max(_open_times),
                "median": _open_median, "mad": _open_mad, "n_runs": len(_open_stats),
                "first_run_discarded": _open_first_discarded,
                "n_timeouts": _open_timeouts, "runs_independent": True,
                "ram":            sample0["ram_mb"]       if sample0 else None,
                "cpu":            sample0["cpu_raw_pct"]   if sample0 else None,
                "cpu_normalized": sample0["cpu_norm_pct"]  if sample0 else None,
                "threads":        sample0["threads"]       if sample0 else None,
                "uptime_sec":     sample0["uptime_sec"]    if sample0 else None,
            }]

            def run_test_with_runs(name, func, runs):
                """Замер операции вкладки «Производительность» — общий цикл
                повторов _measure_op_repeated (тот же, что у Batch-режима).
                Если тест снят чекбоксом, ничего не делает. Документ не
                загрузился — тоже (зеркало measure в Batch)."""
                if name not in enabled_tests or not data_ready:
                    return
                results.append(self._measure_op_repeated(
                    name, func, runs, find_r7_window, self.add_test_log,
                    stop_event, focus_cb=focus_window, post_delay=post_action_delay))

            # Операции — один набор на оба воркера (r7_ops.SpreadsheetOps):
            # прежде они жили здесь и в Batch двумя копиями, которые
            # приходилось зеркалить вручную (docs/plan-to-8.md, этап 1).
            _ops = SpreadsheetOps(self, find_r7_window, self.add_test_log, test_file)
            _test_ops = _ops.tests()

            def _update_status(text):
                """Safely updates the status bar from this worker thread —
                marshals onto the main thread via root.after and swallows
                errors from a window closed mid-run."""
                try:
                    self.root.after(0, lambda: self.status_var.set(text))
                except Exception:
                    pass

            # Прогресс и статус считаются только по включённым тестам — раньше
            # цикл шёл по всем 13 операциям и показывал «⚙ Название — N/13»
            # даже для снятых чекбоксом тестов, которые run_test_with_runs
            # молча пропускает.
            _active_ops = [op for op in _test_ops if op[0] in enabled_tests]
            # Семплер (запущен раньше, до try — см. комментарий там же)
            # начинает копить точки именно с этого момента: до сих пор RAM/CPU
            # ещё формировались самим открытием файла (переходный процесс), а
            # detect_leak() интересует дрейф ВО ВРЕМЯ теста, не старт.
            _resource_sampler.start()

            _run_start = time.time()
            self._set_perf_progress(0, len(_active_ops))
            for _i, (_name, _func) in enumerate(_active_ops, start=1):
                if stop_event.is_set():
                    self.add_test_log(
                        f"⏹ Остановлено пользователем ({_i - 1}/{len(_active_ops)} тестов выполнено)")
                    break
                _update_status(
                    f"⚙ {_name} — {_i}/{len(_active_ops)} "
                    f"(прошло {time.time() - _run_start:.0f} сек)")
                run_test_with_runs(_name, _func, test_runs.get(_name, DEFAULT_TEST_RUNS))
                self._set_perf_progress(_i, len(_active_ops))
            if not stop_event.is_set():
                _update_status(
                    f"✅ Готово: {len(_active_ops)}/{len(_active_ops)} "
                    f"(всего {time.time() - _run_start:.0f} сек)")
            self._cleanup_x2t_temp_pdfs()

            # ----- 5. Статистика ресурсов --------------------------------------------------
            ram_vals      = [r["ram"] for r in results if r.get("ram") is not None]
            cpu_vals      = [r["cpu"] for r in results if r.get("cpu") is not None]
            cpu_norm_vals = [r["cpu_normalized"] for r in results if r.get("cpu_normalized") is not None]
            peak_ram = max(ram_vals) if ram_vals else None
            avg_ram  = round(sum(ram_vals) / len(ram_vals), 1) if ram_vals else None
            min_ram  = min(ram_vals) if ram_vals else None
            peak_cpu = max(cpu_vals) if cpu_vals else None
            peak_cpu_norm = max(cpu_norm_vals) if cpu_norm_vals else None
            avg_cpu_norm  = round(sum(cpu_norm_vals) / len(cpu_norm_vals), 1) if cpu_norm_vals else None
            if peak_ram is not None:
                self.add_test_log(
                    f"📊 Пик RAM: {peak_ram:.1f} МБ  Средн: {avg_ram:.1f} МБ  Мин: {min_ram:.1f} МБ")
            if peak_cpu is not None:
                self.add_test_log(
                    f"📊 Пик CPU: {peak_cpu:.1f}% (сырое)  {peak_cpu_norm:.1f}% (норм., "
                    f"{psutil.cpu_count() if env.PSUTIL_OK else '?'} ядер)")

            # ── Детектор утечек (этап 2, H3) ────────────────────────────────────
            # Останавливаем сразу после операций теста, до сохранения отчётов и
            # закрытия Р7 — семплер должен покрывать сам прогон, не переходные
            # процессы вокруг него. finally ниже вызовет stop() повторно на
            # случай исключения выше (идемпотентно, безопасно).
            _resource_sampler.stop()
            _resource_sampler.join(timeout=5)
            leak_verdict = detect_leak(_resource_sampler.snapshot())
            # В прогоне операций объём данных меняют сами операции (вставка
            # массива, новые листы, откаты между повторами) — наклон RAM здесь
            # утечку не показывает, и прежний вердикт «утечки не обнаружено
            # (наклон −10011 МБ/ч)» вводил в заблуждение (аудит 29.09.2026,
            # пункт 16). Наклон остаётся в отчёте для справки, вердикт —
            # только от soak-теста, где документ не меняется.
            if leak_verdict.get("slope_mb_per_hour") is not None:
                leak_verdict = dict(leak_verdict, leak=None, applicable=False,
                                    verdict=(f"не оценивается: операции прогона меняют "
                                             f"объём данных (наклон "
                                             f"{leak_verdict['slope_mb_per_hour']:.1f} МБ/ч "
                                             f"для справки); утечки ищет soak-тест"))
                self.add_test_log(f"ℹ️ Утечки памяти: {leak_verdict['verdict']}")
            # leak is None (мало замеров — короткий прогон/мало включённых
            # тестов) — логировать нечего, это ожидаемо, не предупреждение.

            # ----- 6. Сохранение отчётов ---------------------------------------------------
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Timestamp в имени — иначе каждый следующий прогон затирает Excel-
            # и HTML-отчёт предыдущего (performance_full_*.json и так уже был
            # уникальным на прогон, эти два — нет).
            REPORT_FILE = self.reports_folder / f"Performance_Report_{ts}.xlsx"
            HTML_REPORT_PATH = REPORT_FILE.with_suffix(".html")
            # Три отчёта пишутся независимо: прежде один try на все три, и
            # открытый в Excel .xlsx (PermissionError) лишал прогон JSON, на
            # котором держатся сравнение версий и тренды (аудит 06.10.2026).
            # JSON — первым.
            try:
                REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
                # JSON (полные данные для последующего сравнения версий)
                json_path = self.reports_folder / f"performance_full_{ts}.json"
                full_data = self._build_full_report(
                    ts, self.current_version_info.get("name") if self.current_version_info else None,
                    test_file, results, {
                        "peak_ram_mb": peak_ram,
                        "avg_ram_mb": avg_ram,
                        "min_ram_mb": min_ram,
                        "peak_cpu_pct": peak_cpu,
                        "peak_cpu_normalized_pct": peak_cpu_norm,
                        "avg_cpu_normalized_pct": avg_cpu_norm,
                        "leak_detection": leak_verdict,
                    })
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(full_data, f, indent=2, ensure_ascii=False)
                self.add_test_log(f"📄 JSON-данные сохранены: {json_path.name}")
            except Exception as e:
                self.add_test_log(f"❌ JSON-отчёт не сохранён — прогон не попадёт в "
                                  f"сравнение и тренды: {type(e).__name__}: {e}")

            try:
                from openpyxl import Workbook as WB
                wb = WB()
                ws = wb.active
                ws.title = "Результаты"
                ws.append(["Операция", "Время (сек)", "RAM (МБ)", "CPU (%)", "Ошибка"])
                for r in results:
                    ws.append([r["name"], round(r["time"], 2),
                               r.get("ram") or "", r.get("cpu") or "",
                               r.get("error") or ""])
                wb.save(str(REPORT_FILE))
                self.add_test_log(f"📊 Excel-отчёт сохранён: {REPORT_FILE}")
            except Exception as e:
                self.add_test_log(f"⚠️ Excel-отчёт не сохранён: {type(e).__name__}: {e}")

            try:
                version_str = (self.current_version_info.get("name")
                               if self.current_version_info else None)
                _full = locals().get("full_data") or {}
                html_content = self._generate_html_report(
                    results, test_file, open_elapsed, version_str,
                    ram_vals, cpu_vals, peak_ram, avg_ram, min_ram, peak_cpu,
                    summary=_full.get("summary"), system=_full.get("system"),
                )
                with open(HTML_REPORT_PATH, "w", encoding="utf-8") as f:
                    f.write(html_content)
                self.add_test_log(f"📄 HTML-отчёт сохранён: {HTML_REPORT_PATH}")
            except Exception as e:
                self.add_test_log(f"⚠️ HTML-отчёт не сохранён: {type(e).__name__}: {e}")

            # ----- 7. Закрытие -------------------------------------------------------------
            _upd_stop.set()
            self.add_test_log("🔍 Мониторинг окна обновления остановлен")
            self.add_test_log("🔚 Закрытие Р7-Офис...")
            self._restore_autosave()
            # Флаг — «процессов Р7 не осталось»: False от _close_r7_gracefully
            # значит лишь «пришлось убить», а не «жив». Прежде флаг ставился
            # без проверки, и Р7 мог остаться (аудит 06.10.2026).
            self._close_r7_gracefully(find_r7_window())
            _r7_closed = self._r7_gone()
            # После «Сохранить как» в XLTX Р7 держит сохранённый файл открытым,
            # и очистка до закрытия его не удаляла (34 МБ в %TEMP% на прогон).
            self._cleanup_x2t_temp_pdfs()
            self.add_test_log("🏁 Тест завершён.")

            # ----- 8. Диалог после теста ---------------------------------------------------
            self.root.after(0, lambda: self._show_post_test_dialog(HTML_REPORT_PATH, ts))
        finally:
            # Поток-монитор диалога обновления не должен пережить эту функцию —
            # раньше _upd_stop.set() стоял в линейном коде, и любое исключение
            # выше оставляло монитор сканировать все окна системы до закрытия
            # приложения. CDP/Selenium-соединение и семплер ресурсов — тот же
            # случай: должны остановиться независимо от того, как функция
            # завершилась. _resource_sampler.stop() безопасно вызывать даже
            # если .start() выше так и не случился (исключение до него) —
            # это просто Event.set(), не требует живого потока.
            _upd_stop.set()
            _resource_sampler.stop()
            # Исключение до штатного закрытия — Р7 ещё жив, вернуть настройку
            # пользователя можно. После штатного закрытия это no-op.
            self._restore_autosave()
            if not _r7_closed and not self._emergency_close_r7(find_r7_window):
                self.add_test_log("❌ Р7-Офис не закрылся — закройте его вручную, "
                                  "иначе следующий прогон упрётся в занятый порт CDP")
            self._close_webdriver_connector()

    # ---------------------- Вспомогательные методы (ресурсы, отчёты) ------


    # Сброс файлового кэша ОС перед каждым холодным стартом (аудит 29.09.2026,
    # пункт 11). _clear_r7_cache чистит только %TEMP% Р7, а DLL редактора и
    # сам тестовый файл остаются в standby-кэше Windows: «холодный старт» без
    # сброса на деле был тёплым, и первый запуск после перезагрузки стенда
    # отличался от всех следующих. Требует прав администратора (инструмент и
    # так запускается от них). Сброс кэша безопасен — это только освобождение
    # страниц, данные не теряются, — но на пару секунд замедляет остальные
    # программы. False — вернуть прежнее поведение.
    PURGE_OS_FILE_CACHE = True
    # Фоновая загрузка системы выше этого перед прогоном — предупреждение в лог
    # и в отчёт (environment.warnings): чужая нагрузка делит с Р7 ядра и кэши.
    ENV_BUSY_SYSTEM_CPU_PCT = 10.0

    QUIET_SYSTEM_MAX_WAIT_SEC = 10.0   # дольше тишины не ждём — прогон идёт дальше
    QUIET_DISK_MB_PER_SEC = 20.0       # физический диск быстрее — система «занята»
                                       # (первая калибровка: простой стенда ~0 МБ/с)
    ALERT_AFTER_X2T_CRASH_SEC = 6.0    # сколько ждать окна ошибки Р7 после падения x2t


    ENV_MIN_FREE_DISK_GB = 5.0   # меньше — предупреждение: экспорт пишет ~0.5 ГБ за раз


    BOLD_STABLE_SEC = 0.5        # кнопка «Жирный» должна простоять доступной столько
    BOLD_PROBE_TIMEOUT_SEC = 0.3 # таймаут одной пробы кнопки (рендерер занят — не ждём)


    HEAVY_CALC_CHECK_SEC = 0.3   # как часто искать модалку «пересчёт может занять время»
    HEAVY_CALC_EVAL_TIMEOUT_SEC = 0.5
    # Без CDP модалку пересчёта закрывает Esc (см. _wait_until_r7_ready).
    # Проверено на живом Р7 2026.3.2: Esc закрывает её с ответом «не Да» —
    # пересчёт автоматический (calcPr.calcMode не выставлен).
    READY_ESC_WITHOUT_CDP = True


    AUTOSAVE_RESTORE_FLUSH_SEC = 3.0   # запас над подтверждёнными 1.5 с


    # ── Тесты правки на рабочем листе (ВПР, ПКМ, удаление столбца) ─────────
    # Переделаны 29.09.2026: живой прогон показал, что все три меряли пустоту.
    # ВПР вставлял формулу клавишами, а Р7 её не принимал, да и ссылалась она
    # на несуществующий «Лист1». «Вставка ячеек (ПКМ)» попадала на лист с
    # автофильтром, где Р7 молча отказывает в asc_insertCells. «Удаление
    # столбца (Del)» очищало одну ячейку B1. Теперь каждый тест в подготовке
    # (вне замера) переходит на рабочий лист и готовит выделение, а в замере —
    # одна операция, результат которой проверен по значениям ячеек.


    # Операции, которые документ НЕ меняют: для них отсутствие новой точки в
    # истории правок — норма (см. предохранитель в _measure_op_repeated).
    NON_MUTATING_MARKERS = ("Выделение всех ячеек", "Копирование всех ячеек", "Сохранение в")


    # ---------------------- Замер операции ----------------------


    CSV_OPTIONS_TIMEOUT_SEC = 20.0   # окно параметров CSV появляется через ~6 с после
                                     # предупреждения о потере функций (живой прогон)
    CSV_OPTIONS_TITLES = ("выбрать параметры csv", "choose csv options")


    # ---------------------- Готовность документа ----------------------


    # ── CDP-триггер готовности (кнопка «Жирный» в DOM) ─────────────────────
    # В отличие от _wait_for_bold_button (win32gui) — реально видит кнопку:
    # панель инструментов Р7 рисуется как HTML внутри CEF-рендера, а не
    # набором нативных Win32-виджетов (см. r7_webdriver_connector.py и
    # коммит 7978206). Требует, чтобы Р7 в этом запуске был стартован с
    # --ascdesktop-support-debug-info (см. _prepare_webdriver_launch) — без
    # этого self._webdriver_connector остаётся None, и весь блок ниже
    # молча ничего не делает, оставляя работу win32gui/CPU-логике.


    @staticmethod
    def _json_for_script(obj, **kwargs):
        r"""json.dumps(), но безопасный для вставки прямо внутрь <script>...</script>.

        Строковое значение, содержащее буквальную последовательность
        "</script", закрыло бы окружающий тег раньше времени — HTML-парсер
        браузера не знает, что находится внутри JS-строкового литерала, и
        видит закрывающий тег буквально. Версия/имя теста, попадающие сюда,
        приходят из простых текстов (реестр, simpledialog, JSON-файлы с
        диска), но ничто не мешает им случайно содержать такую подстроку.

        "<\/" — валидный экранированный слэш в JS-строках (не спецсимвол,
        декодируется в тот же "/"), который ломает поиск тега парсером HTML,
        не меняя значение после разбора JSON.
        """
        return json.dumps(obj, **kwargs).replace("</", r"<\/")


    def _show_post_test_dialog(self, html_path, ts):
        """Shows dialog after test completion: open report, new test, or exit."""
        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.configure(bg=COLORS["bg"])
        dlg.title("Тест завершён")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.focus_set()

        ttk.Label(dlg, text="Тест завершён!", font=("Arial", 12, "bold")).pack(
            pady=(24, 6), padx=40)
        ttk.Label(dlg, text="Что делать дальше?", foreground=COLORS["text_secondary"]).pack(pady=(0, 20))

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(pady=(0, 24), padx=40)

        def show_report():
            if not Path(html_path).exists():
                # Сохранение HTML могло упасть — не открывать пустую ссылку молча.
                messagebox.showerror("Отчёт не сохранён",
                                     "HTML-отчёт не записан, причина — в журнале теста.",
                                     parent=dlg)
                return
            webbrowser.open(str(html_path))
            dlg.destroy()
            if messagebox.askyesno("Сохранить копию", "Сохранить копию HTML-отчёта?"):
                save_path = filedialog.asksaveasfilename(
                    defaultextension=".html",
                    filetypes=[("HTML files", "*.html"), ("All files", "*.*")],
                    initialfile=f"Performance_Report_{ts}.html"
                )
                if save_path:
                    shutil.copy(str(html_path), save_path)
                    self.add_test_log(f"📎 Копия отчёта сохранена: {save_path}")

        def new_test():
            dlg.destroy()
            self._reset_test_state()

        def exit_app():
            dlg.destroy()
            self.root.quit()

        ttk.Button(btn_frame, text="📊 Показать отчёт", command=show_report, width=20
                   ).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🔄 Новый тест", command=new_test, width=14
                   ).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="❌ Выход", command=exit_app, width=10
                   ).pack(side=tk.LEFT, padx=5)

        dlg.update_idletasks()
        w = dlg.winfo_reqwidth()
        h = dlg.winfo_reqheight()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")

    def _reset_test_state(self):
        """Clears the test log and resets the status bar for a new run."""
        self.test_log.delete("1.0", tk.END)
        self.status_var.set("Готов")
        self.add_test_log("🔄 Готов к новому тесту.")

    # ---------------------- Сравнение версий ----------------------


    def compare_versions(self):
        """Opens dialog to select 2-10 performance JSON files and builds a comparison report."""
        # Не больше цветов палитры: девятая версия на графике получила бы
        # повтор цвета и слилась бы с первой.
        MAX_FILES = len(SERIES_COLORS)
        CHART_COLORS = list(SERIES_COLORS)

        settings = self._load_comparison_settings()
        custom_names = settings.get("custom_names", {})
        last_selected = set(settings.get("last_selected_files", []))
        last_base = settings.get("last_base_version", "")

        def scan_files():
            json_files = sorted(
                self.reports_folder.glob("performance_full_*.json"),
                key=lambda fp: fp.stat().st_mtime, reverse=True
            )
            result = []
            for jf in json_files:
                key = str(jf)
                try:
                    with open(jf, encoding="utf-8") as fh:
                        jdata = json.load(fh)
                    version = jdata.get("version") or jf.stem
                    ts_raw = jdata.get("timestamp", "")
                    ts_disp = (f"{ts_raw[6:8]}.{ts_raw[4:6]}.{ts_raw[:4]} "
                               f"{ts_raw[9:11]}:{ts_raw[11:13]}"
                               if len(ts_raw) >= 13 else ts_raw)
                except Exception:
                    jdata = None
                    version = jf.stem
                    ts_disp = ""
                result.append({
                    "path": jf, "key": key, "version": version,
                    "ts": ts_disp, "data": jdata,
                    "display_name": custom_names.get(key, version),
                })
            return result

        initial_meta = scan_files()
        if len(initial_meta) < 2:
            self.add_test_log(
                f"⚠️ Сравнение версий: найдено {len(initial_meta)} файлов "
                f"performance_full_*.json (нужно минимум 2)")
            messagebox.showwarning(
                "Недостаточно данных",
                "Для сравнения нужно минимум 2 файла performance_full_*.json.\n"
                "Запустите тесты для нескольких версий R7-Office."
            )
            return

        # Mutable state shared by all closures
        file_meta_by_key = {}   # key -> meta dict
        sel_vars = {}           # key -> BooleanVar
        combo_keys_ref = []     # ordered list of keys matching combo values

        # ── Dialog ──────────────────────────────────────────────────────────
        try:
            dlg = tk.Toplevel(self.root)
            dlg.transient(self.root)
            dlg.configure(bg=COLORS["bg"])
            dlg.title("Сравнение версий")
            dlg.resizable(True, True)
            dlg.minsize(580, 400)
            dlg.grab_set()

            ttk.Label(dlg, text="Выберите 2–10 файлов для сравнения:",
                      font=("Arial", 10, "bold")).pack(pady=(12, 4), padx=14, anchor=tk.W)

            # ── Scrollable list ──────────────────────────────────────────────────
            list_outer = ttk.LabelFrame(dlg, text="Доступные результаты", padding="4")
            list_outer.pack(fill=tk.BOTH, expand=True, padx=14, pady=4)

            list_canvas = tk.Canvas(list_outer, highlightthickness=0)
            vsb = ttk.Scrollbar(list_outer, orient=tk.VERTICAL, command=list_canvas.yview)
            list_canvas.configure(yscrollcommand=vsb.set)
            vsb.pack(side=tk.RIGHT, fill=tk.Y)
            list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            inner = ttk.Frame(list_canvas)
            inner_id = list_canvas.create_window((0, 0), window=inner, anchor="nw")

            def _on_inner_cfg(e):
                list_canvas.configure(scrollregion=list_canvas.bbox("all"))
            inner.bind("<Configure>", _on_inner_cfg)

            def _on_canvas_cfg(e):
                list_canvas.itemconfig(inner_id, width=e.width)
            list_canvas.bind("<Configure>", _on_canvas_cfg)

            def _on_mwheel(e):
                list_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            list_canvas.bind_all("<MouseWheel>", _on_mwheel)

            # ── Row builder ─────────────────────────────────────────────────────
            def build_row(meta, idx):
                key = meta["key"]
                var = tk.BooleanVar(value=(key in last_selected))
                sel_vars[key] = var
                color = CHART_COLORS[idx % len(CHART_COLORS)]

                rf = ttk.Frame(inner)
                rf.pack(fill=tk.X, pady=1, padx=2)

                ttk.Checkbutton(rf, variable=var).pack(side=tk.LEFT)

                dot = tk.Canvas(rf, width=14, height=14, highlightthickness=0,
                                bg=dlg.cget("bg"))
                dot.create_oval(2, 2, 12, 12, fill=color, outline="")
                dot.pack(side=tk.LEFT, padx=(2, 4))

                ts_val = meta.get("ts", "")
                name_txt = meta.get("display_name", meta["version"])
                lbl_txt = f"{name_txt}  •  {ts_val}" if ts_val else name_txt
                lbl = ttk.Label(rf, text=lbl_txt, anchor=tk.W)
                # Упаковывается ПОСЛЕ кнопок (ниже): упаковщик раздаёт место по
                # порядку, и длинное имя раньше вытесняло кнопки за край строки.

                def make_rename(m, lb):
                    def do_rename():
                        new_name = simpledialog.askstring(
                            "Переименовать", "Новое название:",
                            initialvalue=m.get("display_name", m["version"]),
                            parent=dlg
                        )
                        if new_name and new_name.strip():
                            m["display_name"] = new_name.strip()
                            custom_names[m["key"]] = new_name.strip()
                            ts = m.get("ts", "")
                            lb.config(text=f"{new_name.strip()}  •  {ts}" if ts
                                      else new_name.strip())
                            refresh_base_combo()
                    return do_rename

                def make_delete(m, row_frame):
                    def do_delete():
                        file_meta_by_key.pop(m["key"], None)
                        sel_vars.pop(m["key"], None)
                        custom_names.pop(m["key"], None)
                        row_frame.destroy()
                        refresh_base_combo()
                    return do_delete

                btn_ren = ttk.Button(rf, text="✏️", width=3,
                                     command=make_rename(meta, lbl))
                btn_ren.pack(side=tk.RIGHT, padx=1)
                btn_del = ttk.Button(rf, text="🗑️", width=3,
                                     command=make_delete(meta, rf))
                btn_del.pack(side=tk.RIGHT, padx=1)
                lbl.pack(side=tk.LEFT, padx=(0, 6), fill=tk.X, expand=True)

                ctx = tk.Menu(dlg, tearoff=0)

                def make_ctx_handler(m, lb, row_frame):
                    def show(e):
                        try:
                            ctx.delete(0, tk.END)
                            ctx.add_command(label="✏️ Переименовать",
                                            command=make_rename(m, lb))
                            ctx.add_command(label="🗑️ Удалить из списка",
                                            command=make_delete(m, row_frame))
                            ctx.add_separator()
                            ctx.add_command(label="📌 Сделать базовой",
                                            command=lambda: _set_base_by_key(m["key"]))
                            ctx.tk_popup(e.x_root, e.y_root)
                        finally:
                            ctx.grab_release()
                    return show

                show_ctx = make_ctx_handler(meta, lbl, rf)
                rf.bind("<Button-3>", show_ctx)
                lbl.bind("<Button-3>", show_ctx)

            # ── Populate initial rows ────────────────────────────────────────────
            for i, m in enumerate(initial_meta[:MAX_FILES]):
                file_meta_by_key[m["key"]] = m
                build_row(m, i)

            # ── Toolbar ─────────────────────────────────────────────────────────
            toolbar = ttk.Frame(dlg)
            toolbar.pack(fill=tk.X, padx=14, pady=(4, 0))

            def add_file():
                if len(file_meta_by_key) >= MAX_FILES:
                    messagebox.showwarning("Лимит",
                                           f"Максимум {MAX_FILES} файлов.", parent=dlg)
                    return
                path_str = filedialog.askopenfilename(
                    parent=dlg,
                    title="Выбрать JSON-файл результатов",
                    filetypes=[("JSON файлы", "*.json"), ("Все файлы", "*.*")],
                    initialdir=str(self.reports_folder)
                )
                if not path_str:
                    return
                from pathlib import Path as _Path
                jf = _Path(path_str)
                key = str(jf)
                if key in file_meta_by_key:
                    messagebox.showinfo("Уже добавлен",
                                        "Этот файл уже есть в списке.", parent=dlg)
                    return
                try:
                    with open(jf, encoding="utf-8") as fh:
                        jdata = json.load(fh)
                    version = jdata.get("version") or jf.stem
                    ts_raw = jdata.get("timestamp", "")
                    ts_disp = (f"{ts_raw[6:8]}.{ts_raw[4:6]}.{ts_raw[:4]} "
                               f"{ts_raw[9:11]}:{ts_raw[11:13]}"
                               if len(ts_raw) >= 13 else ts_raw)
                except Exception as ex:
                    messagebox.showerror("Ошибка",
                                         f"Не удалось прочитать файл:\n{ex}", parent=dlg)
                    return
                meta = {
                    "path": jf, "key": key, "version": version,
                    "ts": ts_disp, "data": jdata,
                    "display_name": custom_names.get(key, version),
                }
                idx = len(file_meta_by_key)
                file_meta_by_key[key] = meta
                build_row(meta, idx)
                refresh_base_combo()

            def refresh_list():
                new_meta = scan_files()
                added = 0
                for m in new_meta:
                    if m["key"] not in file_meta_by_key:
                        if len(file_meta_by_key) >= MAX_FILES:
                            break
                        idx = len(file_meta_by_key)
                        file_meta_by_key[m["key"]] = m
                        build_row(m, idx)
                        added += 1
                if added:
                    refresh_base_combo()
                    messagebox.showinfo("Обновлено",
                                        f"Добавлено новых файлов: {added}", parent=dlg)
                else:
                    messagebox.showinfo("Нет изменений",
                                        "Новых файлов не найдено.", parent=dlg)

            ttk.Button(toolbar, text="➕ Добавить файл",
                       command=add_file).pack(side=tk.LEFT, padx=(0, 6))
            ttk.Button(toolbar, text="🔄 Обновить список",
                       command=refresh_list).pack(side=tk.LEFT)

            ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=14, pady=8)

            # ── Base version selector ────────────────────────────────────────────
            base_frame = ttk.LabelFrame(dlg, text="Базовая версия (для расчёта Δ%)", padding="6")
            base_frame.pack(fill=tk.X, padx=14, pady=4)

            base_var = tk.StringVar()
            base_combo = ttk.Combobox(base_frame, textvariable=base_var,
                                      state="readonly", width=60)
            base_combo.pack(fill=tk.X, padx=4, pady=2)

            def refresh_base_combo():
                prev_key = (combo_keys_ref[base_combo.current()]
                            if combo_keys_ref and 0 <= base_combo.current() < len(combo_keys_ref)
                            else "")
                keys = list(file_meta_by_key.keys())
                combo_keys_ref.clear()
                combo_keys_ref.extend(keys)
                values = []
                for k in keys:
                    m = file_meta_by_key[k]
                    nm = m.get("display_name", m["version"])
                    ts = m.get("ts", "")
                    values.append(f"{nm}  •  {ts}" if ts else nm)
                base_combo["values"] = values
                if prev_key and prev_key in keys:
                    base_combo.current(keys.index(prev_key))
                elif last_base and last_base in keys:
                    base_combo.current(keys.index(last_base))
                elif keys:
                    base_combo.current(0)

            def _set_base_by_key(key):
                if key in combo_keys_ref:
                    base_combo.current(combo_keys_ref.index(key))

            refresh_base_combo()

            # ── Action buttons ───────────────────────────────────────────────────
            btn_frame = ttk.Frame(dlg)
            btn_frame.pack(pady=10, padx=14, fill=tk.X)

            def _cleanup():
                list_canvas.unbind_all("<MouseWheel>")
                dlg.destroy()

            def do_compare():
                selected_keys = [k for k, v in sel_vars.items() if v.get()]
                if len(selected_keys) < 2:
                    messagebox.showwarning("Мало файлов",
                                           "Выберите минимум 2 файла.", parent=dlg)
                    return
                if len(selected_keys) > MAX_FILES:
                    messagebox.showwarning("Много файлов",
                                           f"Выберите не более {MAX_FILES} файлов.", parent=dlg)
                    return
                cidx = base_combo.current()
                if cidx < 0 or cidx >= len(combo_keys_ref):
                    messagebox.showwarning("Базовая версия",
                                           "Выберите базовую версию.", parent=dlg)
                    return
                base_key = combo_keys_ref[cidx]
                if base_key not in selected_keys:
                    messagebox.showwarning(
                        "Базовая версия",
                        "Базовая версия должна быть среди выбранных файлов.", parent=dlg)
                    return

                datasets = []
                for k in selected_keys:
                    m = file_meta_by_key[k]
                    jdata = m.get("data")
                    if jdata is None:
                        try:
                            with open(m["path"], encoding="utf-8") as fh:
                                jdata = json.load(fh)
                        except Exception as ex:
                            messagebox.showerror(
                                "Ошибка",
                                f"Не удалось загрузить {m['path'].name}:\n{ex}",
                                parent=dlg)
                            return
                    datasets.append({
                        "path": str(m["path"]),
                        "version": m.get("display_name", m["version"]),
                        "data": jdata,
                    })

                self._save_comparison_settings({
                    "custom_names": custom_names,
                    "last_selected_files": selected_keys,
                    "last_base_version": base_key,
                })
                _cleanup()
                html = self._generate_comparison_html(
                    datasets, str(file_meta_by_key[base_key]["path"]))
                ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_path = self.reports_folder / f"comparison_{ts_now}.html"
                try:
                    out_path.write_text(html, encoding="utf-8")
                    self.add_test_log(f"📊 Отчёт сравнения сохранён: {out_path.name}")
                    webbrowser.open(str(out_path))
                except Exception as ex:
                    messagebox.showerror("Ошибка", f"Не удалось сохранить отчёт:\n{ex}")

            ttk.Button(btn_frame, text="📊 Сравнить",
                       command=do_compare).pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_frame, text="Отмена",
                       command=_cleanup).pack(side=tk.LEFT)

            dlg.protocol("WM_DELETE_WINDOW", _cleanup)

            dlg.update_idletasks()
            row_h = max(len(file_meta_by_key) * 34 + 20, 80)
            list_canvas.configure(height=min(row_h, 220))
            w = max(680, dlg.winfo_reqwidth())
            h = min(700, max(440, dlg.winfo_reqheight()))
            self._center_dialog(dlg, w, h)
        except Exception as ex:
            self.add_test_log(f"❌ Ошибка при построении окна сравнения версий: {ex}")
            try:
                list_canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass
            try:
                dlg.destroy()
            except Exception:
                pass
            messagebox.showerror("Ошибка", f"Не удалось открыть окно сравнения версий:\n{ex}")

    # ── Страница трендов (этап 2, M5) ───────────────────────────────────────
    TRENDS_CHART_COLORS = SERIES_COLORS

    def show_trends(self):
        """Строит и открывает в браузере страницу трендов по всем
        накопленным performance_full_*.json. Точка входа из UI (кнопка
        «📈 Тренды» рядом с «Сравнить версии»)."""
        runs = self._load_trends_runs()
        if len(runs) < 2:
            messagebox.showinfo(
                "Недостаточно данных",
                f"Найдено {len(runs)} файлов performance_full_*.json "
                f"(нужно минимум 2 для тренда).\nЗапустите тесты несколько раз.")
            return
        html_content = self._generate_trends_html(runs)
        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.reports_folder / f"trends_{ts_now}.html"
        try:
            out_path.write_text(html_content, encoding="utf-8")
            self.add_test_log(f"📈 Страница трендов: {out_path.name}")
            webbrowser.open(str(out_path))
        except Exception as e:
            self.add_test_log(f"⚠️ Ошибка сохранения страницы трендов: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить страницу трендов:\n{e}")


    # ---------------------- Batch-режим ----------------------

    def run_batch_mode(self):
        """Entry point for Batch mode — validates prerequisites then shows config dialog."""
        if self._batch_running:
            messagebox.showwarning("Batch уже выполняется",
                                   "Дождитесь завершения текущего Batch-прогона.")
            return
        if self._perf_running:
            messagebox.showwarning("Выполняется тест производительности",
                                   "Оба режима управляют клавиатурой Р7-Офис и не могут "
                                   "работать одновременно. Дождитесь завершения теста "
                                   "или нажмите «Остановить» на вкладке «Производительность».")
            return
        if not ctypes.windll.shell32.IsUserAnAdmin():
            messagebox.showerror(
                "Ошибка прав",
                "Batch-режим требует прав администратора.\n"
                "Перезапустите программу от имени администратора."
            )
            return
        _warn = _missing_cdp_warning()
        if _warn and not messagebox.askyesno("Нет доступа к интерфейсу Р7", _warn):
            return
        if not env.PYAUTOGUI_OK or not pyperclip or not env.EXCEL_OK or not env.WIN32_OK:
            missing = []
            if not env.PYAUTOGUI_OK: missing.append("pyautogui")
            if not pyperclip:    missing.append("pyperclip")
            if not env.EXCEL_OK:     missing.append("openpyxl")
            if not env.WIN32_OK:     missing.append("pywin32")
            messagebox.showerror("Ошибка",
                                 f"Отсутствуют библиотеки: {', '.join(missing)}\n"
                                 "Установите: pip install " + " ".join(missing))
            return
        files = (list(self.distributives_folder.glob("*.msi")) +
                 list(self.distributives_folder.glob("*.exe")))
        files.sort(key=lambda f: self._extract_version(f.stem) or f.name)
        if not files:
            messagebox.showwarning("Нет дистрибутивов",
                                   "В папке Distributives не найдено .msi/.exe файлов.")
            return
        self._show_batch_config_dialog(files)

    def _show_batch_config_dialog(self, files):
        """Shows batch configuration dialog: version checkboxes, test file, options."""
        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.configure(bg=COLORS["bg"])
        dlg.title("Batch-режим")
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(dlg, text=f"Найдено дистрибутивов: {len(files)}",
                  font=("Arial", 10, "bold")).pack(pady=(14, 4), padx=16, anchor=tk.W)

        # ── Список версий ─────────────────────────────────────────────────────
        # Прокручиваемый список вместо обычного pack() — при resizable(False, False)
        # и десятке+ дистрибутивов список раньше выталкивал кнопки «Запустить»/
        # «Отмена» за нижнюю границу экрана без какой-либо возможности прокрутки.
        ver_frame = ttk.LabelFrame(dlg, text="Выберите версии для тестирования", padding="8")
        ver_frame.pack(fill=tk.BOTH, padx=16, pady=4)

        MAX_LIST_HEIGHT = 220
        ver_canvas = tk.Canvas(ver_frame, borderwidth=0, highlightthickness=0,
                               bg=COLORS["bg"])
        ver_vsb = ttk.Scrollbar(ver_frame, orient=tk.VERTICAL, command=ver_canvas.yview)
        ver_canvas.configure(yscrollcommand=ver_vsb.set)
        ver_inner = ttk.Frame(ver_canvas)
        ver_inner_id = ver_canvas.create_window((0, 0), window=ver_inner, anchor="nw")

        def _ver_on_inner_cfg(_e):
            ver_canvas.configure(scrollregion=ver_canvas.bbox("all"))
        def _ver_on_canvas_cfg(e):
            ver_canvas.itemconfig(ver_inner_id, width=e.width)
        ver_inner.bind("<Configure>", _ver_on_inner_cfg)
        ver_canvas.bind("<Configure>", _ver_on_canvas_cfg)

        ver_vars = {}
        for f in files:
            var = tk.BooleanVar(value=True)
            ver_vars[f] = var
            ttk.Checkbutton(ver_inner, text=f.name, variable=var).pack(anchor=tk.W, pady=1)
        # Колесо — на каждой строке списка. Прежняя схема (bind_all на <Enter>
        # холста, unbind_all на <Leave>) отключала прокрутку, как только курсор
        # заходил на строку: для Tk это уход с холста на дочерний виджет.
        self._bind_wheel(ver_canvas, ver_canvas, ver_inner)

        dlg.update_idletasks()
        content_h = min(MAX_LIST_HEIGHT, max(ver_inner.winfo_reqheight(), 24))
        ver_canvas.configure(height=content_h)
        ver_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ver_vsb.pack(side=tk.RIGHT, fill=tk.Y)

        mini = ttk.Frame(dlg)
        mini.pack(fill=tk.X, padx=16, pady=(0, 4))
        ttk.Button(mini, text="☑ Все", width=7,
                   command=lambda: [v.set(True) for v in ver_vars.values()]).pack(side=tk.LEFT)
        ttk.Button(mini, text="☐ Снять", width=7,
                   command=lambda: [v.set(False) for v in ver_vars.values()]).pack(
                       side=tk.LEFT, padx=3)

        ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        # ── Тестовый файл ─────────────────────────────────────────────────────
        file_frame = ttk.LabelFrame(dlg, text="Тестовый файл", padding="8")
        file_frame.pack(fill=tk.X, padx=16, pady=4)

        test_file_var = tk.StringVar()
        for sd in [self.test_files_folder, BASE_DIR, Path.home() / "Downloads", Path.home() / "Загрузки"]:
            if not sd.exists():
                continue
            for pat in ["файл-для-теста-Р7-офис-50К*.xlsx", "*50К*.xlsx"]:
                for found in sd.glob(pat):
                    if found.name.startswith("~$"):
                        continue
                    test_file_var.set(str(found))
                    break
            if test_file_var.get():
                break

        file_row = ttk.Frame(file_frame)
        file_row.pack(fill=tk.X)
        ttk.Label(file_row, text="Файл:").pack(side=tk.LEFT)
        ttk.Entry(file_row, textvariable=test_file_var, width=38).pack(
            side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        def browse_test_file():
            path = filedialog.askopenfilename(
                parent=dlg, title="Выберите тестовый файл",
                filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")])
            if path:
                test_file_var.set(path)

        ttk.Button(file_row, text="Обзор", command=browse_test_file).pack(side=tk.LEFT)

        ttk.Separator(dlg, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        # ── Опции ─────────────────────────────────────────────────────────────
        opt_frame = ttk.LabelFrame(dlg, text="Параметры", padding="8")
        opt_frame.pack(fill=tk.X, padx=16, pady=4)

        stop_on_error_var = tk.BooleanVar(value=True)
        cleanup_var       = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="Останавливаться при первой ошибке",
                        variable=stop_on_error_var).pack(anchor=tk.W)
        ttk.Checkbutton(opt_frame, text="Удалять временные файлы кеша после каждого теста",
                        variable=cleanup_var).pack(anchor=tk.W, pady=(4, 0))

        # ── Кнопки ────────────────────────────────────────────────────────────
        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(pady=12, padx=16, fill=tk.X)

        def on_start():
            selected = [f for f, v in ver_vars.items() if v.get()]
            if not selected:
                messagebox.showwarning("Нет выбора",
                                       "Выберите хотя бы одну версию.", parent=dlg)
                return
            tf = test_file_var.get().strip()
            if not tf or not Path(tf).exists():
                messagebox.showwarning("Файл не найден",
                                       "Укажите существующий тестовый файл.", parent=dlg)
                return
            dlg.destroy()
            self._start_batch_run(selected, Path(tf),
                                  stop_on_error_var.get(), cleanup_var.get())

        ttk.Button(btn_frame, text="▶ Запустить", command=on_start).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Отмена", command=dlg.destroy).pack(side=tk.LEFT)

        dlg.update_idletasks()
        dlg.minsize(460, dlg.winfo_reqheight())

    def _start_batch_run(self, versions, test_file, stop_on_error, cleanup):
        """Creates the progress window and launches the batch worker thread."""
        prog = tk.Toplevel(self.root)
        prog.transient(self.root)
        prog.configure(bg=COLORS["bg"])
        prog.title("Batch-режим: выполнение")
        prog.geometry("680x540")
        prog.resizable(True, True)

        # ── Шапка прогресса ───────────────────────────────────────────────────
        top = ttk.Frame(prog, padding="10")
        top.pack(fill=tk.X)

        lbl_current = ttk.Label(top, text="Подготовка...", font=("Arial", 10, "bold"))
        lbl_current.pack(anchor=tk.W)

        progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(top, variable=progress_var,
                        maximum=len(versions), mode="determinate").pack(
                            fill=tk.X, pady=(4, 0))

        # ── Список версий с иконками ──────────────────────────────────────────
        ver_list_frame = ttk.LabelFrame(prog, text="Версии", padding="6")
        ver_list_frame.pack(fill=tk.X, padx=10, pady=4)

        ver_labels = {}
        for f in versions:
            var = tk.StringVar(value=f"⏳ {f.name}")
            ttk.Label(ver_list_frame, textvariable=var, anchor=tk.W).pack(anchor=tk.W, pady=1)
            ver_labels[f] = var

        # ── Лог ───────────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(prog, text="Лог", padding="4")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        log_text = tk.Text(log_frame, font=("Consolas", 9), wrap=tk.WORD,
                          bg=COLORS["log_bg"], fg=COLORS["text"],
                          insertbackground=COLORS["text"],
                          borderwidth=0, highlightthickness=0)
        log_scroll = ttk.Scrollbar(log_frame, command=log_text.yview)
        log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Управление ────────────────────────────────────────────────────────
        ctrl = ttk.Frame(prog, padding="6")
        ctrl.pack(fill=tk.X)

        stop_event  = threading.Event()
        pause_event = threading.Event()
        paused = [False]

        def toggle_pause():
            if paused[0]:
                paused[0] = False
                pause_event.clear()
                btn_pause.config(text="⏸ Пауза")
            else:
                paused[0] = True
                pause_event.set()
                btn_pause.config(text="▶ Продолжить")

        def request_stop():
            stop_event.set()
            pause_event.clear()
            btn_stop.config(state=tk.DISABLED)
            _log("⏹ Запрошена остановка...")

        btn_pause = ttk.Button(ctrl, text="⏸ Пауза", command=toggle_pause)
        btn_stop  = ttk.Button(ctrl, text="⏹ Остановить", command=request_stop)
        btn_pause.pack(side=tk.LEFT, padx=5)
        btn_stop.pack(side=tk.LEFT, padx=5)

        # ── UI-callback-и ──────────────────────────────────────────────────────
        def _log(msg):
            def _do():
                try:
                    ts = datetime.now().strftime("%H:%M:%S")
                    log_text.insert(tk.END, f"[{ts}] {msg}\n")
                    log_text.see(tk.END)
                    self.add_test_log(msg)
                except tk.TclError:
                    pass
            try:
                prog.after(0, _do)
            except tk.TclError:
                pass

        def _set_current(text):
            try:
                prog.after(0, lambda: lbl_current.config(text=text))
            except tk.TclError:
                pass

        def _set_ver_status(f, text):
            def _do():
                try:
                    if f in ver_labels:
                        ver_labels[f].set(text)
                except tk.TclError:
                    pass
            try:
                prog.after(0, _do)
            except tk.TclError:
                pass

        def _set_progress(n):
            try:
                prog.after(0, lambda: progress_var.set(n))
            except tk.TclError:
                pass

        def _on_done(batch_results, errors):
            def _do():
                try:
                    btn_pause.config(state=tk.DISABLED)
                    btn_stop.config(state=tk.DISABLED)
                    ok = sum(1 for r in batch_results if r.get("success"))
                    _log(f"✅ Batch-режим завершён. Успешно: {ok}, Ошибок: {errors}")
                    self.status_var.set(f"Batch завершён: {ok}/{len(batch_results)} успешно")
                    self.detect_current_version()
                    if batch_results:
                        html = self._generate_batch_summary_html(batch_results)
                        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
                        out_path = self.reports_folder / f"batch_summary_{ts_now}.html"
                        try:
                            out_path.write_text(html, encoding="utf-8")
                            _log(f"📊 Сводный отчёт: {out_path.name}")
                            webbrowser.open(str(out_path))
                        except Exception as e:
                            _log(f"⚠️ Ошибка сохранения отчёта: {e}")
                except tk.TclError:
                    pass
            try:
                prog.after(0, _do)
            except tk.TclError:
                pass

        self._batch_running = True
        self._set_busy_indicator(True, "Идёт Batch-режим")

        def _batch_thread():
            try:
                self._batch_worker(versions, test_file, stop_on_error, cleanup,
                                   _log, _set_current, _set_ver_status, _set_progress,
                                   _on_done, stop_event, pause_event)
            finally:
                self._batch_running = False
                self.root.after(0, lambda: self._set_busy_indicator(False))

        threading.Thread(target=_batch_thread, daemon=True).start()


    # --- Хранилище последних параметров тестового файла ---
    _LAST_PARAMS_FILE = "last_test_params.json"


    def compare_file_sizes(self):
        """Opens the test-file generation dialog with 4 separate action buttons."""
        last = self._load_last_params()

        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.configure(bg=COLORS["bg"])
        dlg.title("Генерация тестового файла")
        dlg.resizable(False, False)
        dlg.grab_set()

        PAD = {"padx": 16, "pady": 5}

        # ── Строки ──────────────────────────────────────────────────────────
        ttk.Label(dlg, text="Количество строк:").grid(
            row=0, column=0, sticky=tk.W, **PAD)
        rows_var = tk.StringVar(value=str(last.get("rows", 50000)))
        rows_entry = ttk.Entry(dlg, textvariable=rows_var, width=14)
        rows_entry.grid(row=0, column=1, sticky=tk.W, **PAD)
        ttk.Label(dlg, text="(1 000 – 1 000 000)", foreground=COLORS["text_secondary"]).grid(
            row=0, column=2, sticky=tk.W, padx=(0, 16))

        # ── Столбцы ─────────────────────────────────────────────────────────
        ttk.Label(dlg, text="Количество столбцов:").grid(
            row=1, column=0, sticky=tk.W, **PAD)
        cols_var = tk.StringVar(value=str(last.get("cols", 50)))
        cols_entry = ttk.Entry(dlg, textvariable=cols_var, width=14)
        cols_entry.grid(row=1, column=1, sticky=tk.W, **PAD)
        ttk.Label(dlg, text="(1 – 100)", foreground=COLORS["text_secondary"]).grid(
            row=1, column=2, sticky=tk.W, padx=(0, 16))

        ttk.Separator(dlg, orient=tk.HORIZONTAL).grid(
            row=2, column=0, columnspan=3, sticky=tk.EW, padx=16, pady=8)

        # ── Перезаписать ─────────────────────────────────────────────────────
        overwrite_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(dlg, text="Перезаписать если существует",
                        variable=overwrite_var).grid(
            row=3, column=0, columnspan=3, sticky=tk.W, padx=16, pady=2)

        # ── Имя файла ────────────────────────────────────────────────────────
        ttk.Label(dlg, text="Имя файла:").grid(row=4, column=0, sticky=tk.W, **PAD)
        filename_var = tk.StringVar(value=last.get("filename", "test_data_50000x50.xlsx"))
        filename_entry = ttk.Entry(dlg, textvariable=filename_var, width=36)
        filename_entry.grid(row=4, column=1, columnspan=2, sticky=tk.EW,
                            padx=(0, 16), pady=5)

        # Авто-имя при смене размеров; сбрасывается при ручном редактировании
        _auto_name = [True]
        _ext_path  = [None]   # полный путь из filedialog

        def _on_dim_change(*_):
            if _auto_name[0]:
                try:
                    filename_var.set(
                        f"test_data_{int(rows_var.get())}x{int(cols_var.get())}.xlsx")
                    _ext_path[0] = None
                except ValueError:
                    pass

        def _on_filename_edit(*_):
            try:
                expected = (
                    f"test_data_{int(rows_var.get())}x{int(cols_var.get())}.xlsx")
            except ValueError:
                expected = ""
            _auto_name[0] = (filename_var.get() == expected)
            _ext_path[0]  = None

        rows_var.trace_add("write", _on_dim_change)
        cols_var.trace_add("write", _on_dim_change)
        filename_var.trace_add("write", _on_filename_edit)

        ttk.Separator(dlg, orient=tk.HORIZONTAL).grid(
            row=5, column=0, columnspan=3, sticky=tk.EW, padx=16, pady=8)

        # ── Кнопки 2×2 ───────────────────────────────────────────────────────
        bf = ttk.Frame(dlg)
        bf.grid(row=6, column=0, columnspan=3, sticky=tk.EW, padx=16)
        bf.columnconfigure(0, weight=1)
        bf.columnconfigure(1, weight=1)

        btn_create = ttk.Button(bf, text="1. Создать файл")
        btn_choose = ttk.Button(bf, text="2. Выбрать файл")
        btn_test   = ttk.Button(bf, text="3. Протестировать")
        btn_cancel = ttk.Button(bf, text="4. Отмена", command=dlg.destroy)

        btn_create.grid(row=0, column=0, sticky=tk.EW, padx=(0, 3), pady=(0, 5))
        btn_choose.grid(row=0, column=1, sticky=tk.EW, padx=(3, 0), pady=(0, 5))
        btn_test  .grid(row=1, column=0, sticky=tk.EW, padx=(0, 3))
        btn_cancel.grid(row=1, column=1, sticky=tk.EW, padx=(3, 0))

        # ── Статус ───────────────────────────────────────────────────────────
        status_var = tk.StringVar(value="Статус: Готов")
        status_lbl = ttk.Label(dlg, textvariable=status_var, anchor=tk.W,
                               foreground=COLORS["text_secondary"])
        status_lbl.grid(row=7, column=0, columnspan=3, sticky=tk.EW,
                        padx=16, pady=(10, 14))

        # ── Вспомогательные функции ──────────────────────────────────────────
        _action_btns = [btn_create, btn_test]

        def _set_status(text, color=COLORS["text_secondary"]):
            def _do():
                try:
                    status_var.set(f"Статус: {text}")
                    status_lbl.config(foreground=color)
                except tk.TclError:
                    pass
            try:
                dlg.after(0, _do)
            except tk.TclError:
                pass

        def _lock():
            def _do():
                try:
                    for b in _action_btns:
                        b.config(state="disabled")
                except tk.TclError:
                    pass
            try:
                dlg.after(0, _do)
            except tk.TclError:
                pass

        def _unlock():
            def _do():
                try:
                    for b in _action_btns:
                        b.config(state="normal")
                except tk.TclError:
                    pass
            try:
                dlg.after(0, _do)
            except tk.TclError:
                pass

        def _validate_dims():
            try:
                r = int(rows_var.get())
                assert 1_000 <= r <= 1_000_000
            except (ValueError, AssertionError):
                messagebox.showwarning(
                    "Ошибка", "Строки: от 1 000 до 1 000 000.", parent=dlg)
                rows_entry.focus_set()
                return None, None
            try:
                c = int(cols_var.get())
                assert 1 <= c <= 100
            except (ValueError, AssertionError):
                messagebox.showwarning(
                    "Ошибка", "Столбцы: от 1 до 100.", parent=dlg)
                cols_entry.focus_set()
                return None, None
            return r, c

        def _resolve_path():
            if _ext_path[0]:
                return Path(_ext_path[0])
            fname = filename_var.get().strip()
            if not fname:
                return None
            if not fname.endswith(".xlsx"):
                fname += ".xlsx"
            return self.test_files_folder / fname

        # ── Кнопка 1: только создать файл ────────────────────────────────────
        def on_create():
            r, c = _validate_dims()
            if r is None:
                return
            if _ext_path[0]:
                messagebox.showwarning(
                    "Внимание",
                    "Файл выбран через диалог — кнопка «Создать файл» работает\n"
                    "только с именем в поле «Имя файла».\n"
                    "Введите имя файла вручную или очистите поле.",
                    parent=dlg)
                return
            fname = filename_var.get().strip()
            if not fname or not re.fullmatch(r"[A-Za-z0-9_.]+", fname):
                messagebox.showwarning(
                    "Ошибка",
                    "Имя файла: только латиница, цифры, '_' и '.'.",
                    parent=dlg)
                filename_entry.focus_set()
                return
            if not fname.endswith(".xlsx"):
                fname += ".xlsx"
                filename_var.set(fname)
            file_path = self.test_files_folder / fname
            if file_path.exists() and not overwrite_var.get():
                _set_status(f"⚠️ Файл уже существует: {fname}", "#e67e22")
                self.add_test_log(f"⚠️ Файл уже существует: {file_path}")
                return
            self._save_last_params(r, c, fname)
            _lock()
            _set_status("⏳ Создание файла...", "#2980b9")

            def _worker():
                try:
                    self._generate_custom_test_file(r, c, file_path)
                    self.add_test_log(
                        f"📊 Создан тестовый файл: {fname} ({r} строк, {c} столбцов)")
                    _set_status(f"✅ Файл создан: {fname}", "#27ae60")
                except Exception as e:
                    self.add_test_log(f"❌ Ошибка создания файла: {e}")
                    _set_status(f"❌ Ошибка: {e}", "#e74c3c")
                finally:
                    _unlock()

            threading.Thread(target=_worker, daemon=True).start()

        # ── Кнопка 2: выбрать любой xlsx ─────────────────────────────────────
        def on_choose():
            path = filedialog.askopenfilename(
                parent=dlg,
                title="Выбрать xlsx-файл для тестирования",
                filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")]
            )
            if path:
                _ext_path[0]  = path
                _auto_name[0] = False
                filename_var.set(path)
                self.add_test_log(f"📁 Выбран файл: {path}")
                _set_status(f"📁 Выбран файл: {Path(path).name}", "#2980b9")

        # ── Кнопка 3: только тестирование ────────────────────────────────────
        def on_test():
            file_path = _resolve_path()
            if not file_path:
                _set_status("❌ Укажите имя или путь к файлу", "#e74c3c")
                return
            if not file_path.exists():
                msg = f"❌ Файл не найден: {file_path.name}"
                _set_status(msg, "#e74c3c")
                self.add_test_log(msg)
                return
            try:
                r, c = int(rows_var.get()), int(cols_var.get())
            except ValueError:
                r, c = 0, 0
            self._save_last_params(r, c, file_path.name)
            _lock()
            _set_status("⏳ Тестирование...", "#2980b9")

            def _done(success):
                _set_status(
                    "✅ Тест завершён" if success else "❌ Тест завершён с ошибкой",
                    "#27ae60" if success else "#e74c3c")
                _unlock()

            threading.Thread(
                target=self._worker_run_test,
                args=(file_path, r, c, _done),
                daemon=True
            ).start()

        btn_create.config(command=on_create)
        btn_choose.config(command=on_choose)
        btn_test  .config(command=on_test)

        dlg.columnconfigure(1, weight=1)
        rows_entry.focus_set()
        dlg.wait_window()


    def _show_custom_test_report(self, result):
        """Отчёт по своему файлу (templates/reports/custom.html): строит,
        сохраняет и открывает в браузере."""
        html_content = r7_reports.render("custom.html", **r7_reports.custom_model(result))
        ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.reports_folder / f"custom_test_{ts_file}.html"
        try:
            out_path.write_text(html_content, encoding="utf-8")
            self.add_test_log(f"📊 Отчёт готов: {out_path.name}")
            webbrowser.open(str(out_path))
        except Exception as e:
            self.add_test_log(f"⚠️ Ошибка записи отчёта: {e}")

    # ── Генератор фикстур с профилями нагрузки (этап 2, M2) ────────────────
    #
    # ПОЧЕМУ ПРОФИЛИ, А НЕ ПРОСТО «БОЛЬШЕ СТРОК»: «жирный текст» (вставка
    # большого объёма символов) нагружает путь ввода и парсер текста, но не
    # трогает то, что в реальности чаще всего медленно — таблицу стилей,
    # движок пересчёта формул, парсинг .xlsx при открытии. Один и тот же
    # объём данных по-разному нагружает Р7 в зависимости от ЧЕГО он состоит
    # (см. отчёт по нагрузочному тестированию, 25.08.2026, раздел 06).
    #
    # ПОЧЕМУ БЕЗ ОДНОРОДНОСТИ: сгенерированные значения намеренно РАЗНЫЕ
    # (rnd.randint / f-строка с индексом), а не повторяющаяся константа —
    # однородный текст даёт быстрый путь дедупликации в sharedStrings и
    # завышает результат по сравнению с реальным документом (та же ловушка,
    # что и у «жирного текста», см. отчёт).
    FIXTURE_PROFILES = ("flat", "formula", "styled", "mixed")


    _FIXTURE_STYLE_PALETTE = ("FFECE0", "E0F0FF", "E8FFE0", "FFF6D5", "F0E0FF")


    # ---------------------- Хеш-суммы дистрибутивов ----------------------
    def check_hashes(self):
        """Entry point for hash verification — creates progress window then spawns worker thread."""
        files = (list(self.distributives_folder.glob("*.msi")) +
                 list(self.distributives_folder.glob("*.exe")))
        if not files:
            messagebox.showwarning("Нет файлов", "В папке Distributives нет файлов для проверки.")
            return

        prog_win = tk.Toplevel(self.root)
        prog_win.transient(self.root)
        prog_win.configure(bg=COLORS["bg"])
        prog_win.title("Вычисление хеш-сумм...")
        prog_win.geometry("440x120")
        prog_win.resizable(False, False)
        prog_win.grab_set()

        lbl_file = ttk.Label(prog_win, text="Подготовка...", wraplength=410, anchor=tk.W)
        lbl_file.pack(pady=(14, 4), padx=15, fill=tk.X)

        progressbar = ttk.Progressbar(prog_win, maximum=len(files), mode="determinate")
        progressbar.pack(fill=tk.X, padx=15)

        lbl_count = ttk.Label(prog_win, text=f"0 / {len(files)}")
        lbl_count.pack(pady=4)

        threading.Thread(
            target=self._hash_worker,
            args=(files, prog_win, progressbar, lbl_file, lbl_count),
            daemon=True,
        ).start()

    def _hash_worker(self, files, prog_win, progressbar, lbl_file, lbl_count):
        """Computes MD5/SHA256 for each file in a background thread, then shows results.

        Args:
            files: List of Path objects to hash.
            prog_win: Progress Toplevel window (destroyed when done).
            progressbar: ttk.Progressbar widget to update.
            lbl_file: Label showing the current filename.
            lbl_count: Label showing N / total progress.
        """
        hashes_json = self.distributives_folder / "hashes.json"
        reference = {}
        if hashes_json.exists():
            try:
                with open(hashes_json, encoding="utf-8") as f:
                    reference = json.load(f)
            except Exception as e:
                self.add_test_log(f"⚠️ Ошибка загрузки hashes.json: {e}")

        def _update_progress(filename, idx):
            lbl_file.config(text=f"Обработка: {filename}")
            progressbar.config(value=idx)
            lbl_count.config(text=f"{idx + 1} / {len(files)}")

        results = []
        for i, path in enumerate(files):
            self.root.after(0, lambda fn=path.name, idx=i: _update_progress(fn, idx))
            try:
                size_mb = path.stat().st_size / (1024 * 1024)
                md5h = hashlib.md5()
                sha256h = hashlib.sha256()
                # 1 МБ вместо прежних 8 КБ — дистрибутивы весят сотни МБ/ГБ,
                # и мелкий чанк умножает накладные расходы на системные вызовы.
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        md5h.update(chunk)
                        sha256h.update(chunk)
                md5_val = md5h.hexdigest()
                sha256_val = sha256h.hexdigest()

                ref = reference.get(path.name, {})
                if not ref:
                    status, tag = "⚠️ Нет эталона", "no_ref"
                elif (ref.get("md5", "").lower() == md5_val and
                      ref.get("sha256", "").lower() == sha256_val):
                    status, tag = "✅ Совпадает", "ok"
                else:
                    status, tag = "❌ Не совпадает", "fail"

                results.append({
                    "name": path.name,
                    "size": f"{size_mb:.2f}",
                    "md5": md5_val,
                    "sha256": sha256_val,
                    "status": status,
                    "tag": tag,
                })
                self.add_test_log(f"🔐 {path.name}: {status}")

            except Exception as e:
                results.append({
                    "name": path.name,
                    "size": "—",
                    "md5": "ОШИБКА",
                    "sha256": str(e),
                    "status": "❌ Ошибка чтения",
                    "tag": "fail",
                })
                self.add_test_log(f"❌ {path.name}: ошибка чтения — {e}")

        ok_count   = sum(1 for r in results if r["tag"] == "ok")
        fail_count = sum(1 for r in results if r["tag"] == "fail")
        self.add_test_log(
            f"🔐 Проверка завершена: {len(results)} файлов  "
            f"✅ {ok_count} совпадают  ❌ {fail_count} не совпадают"
        )
        self.root.after(0, prog_win.destroy)
        self.root.after(0, lambda: self._show_hash_results(results))

    def _show_hash_results(self, results):
        """Opens a Treeview window with hash results.

        Supports: copy-on-double-click, reference editing/deletion via button and
        context menu, status refresh without re-scanning, and CSV export.

        Args:
            results: List of dicts with keys name, size, md5, sha256, status, tag.
                     Dicts are mutated in place when references are saved/deleted.
        """
        hashes_path = self.distributives_folder / "hashes.json"

        win = tk.Toplevel(self.root)
        win.transient(self.root)
        win.configure(bg=COLORS["bg"])
        win.title("Хеш-суммы дистрибутивов")
        win.geometry("1120x480")
        win.resizable(True, True)

        # ── Treeview ──────────────────────────────────────────────────────────
        columns = ("name", "size", "md5", "sha256", "status")
        tree = ttk.Treeview(win, columns=columns, show="headings", selectmode="browse")

        tree.heading("name",   text="Имя файла")
        tree.heading("size",   text="Размер (МБ)")
        tree.heading("md5",    text="MD5")
        tree.heading("sha256", text="SHA256")
        tree.heading("status", text="Статус")

        tree.column("name",   width=260, anchor=tk.W,      stretch=True)
        tree.column("size",   width=90,  anchor=tk.CENTER, stretch=False)
        tree.column("md5",    width=245, anchor=tk.W,      stretch=False)
        tree.column("sha256", width=370, anchor=tk.W,      stretch=False)
        tree.column("status", width=130, anchor=tk.CENTER, stretch=False)

        tree.tag_configure("ok",     background="#2E4A3A", foreground=COLORS["text"])
        tree.tag_configure("no_ref", background="#4A4326", foreground=COLORS["text"])
        tree.tag_configure("fail",   background="#4A2E2E", foreground=COLORS["text"])

        sb_y = ttk.Scrollbar(win, orient=tk.VERTICAL,   command=tree.yview)
        sb_x = ttk.Scrollbar(win, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)

        tree.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")

        win.rowconfigure(0, weight=1)
        win.columnconfigure(0, weight=1)

        # row_id → result dict, built while populating the tree
        id_to_result = {}
        for r in results:
            iid = tree.insert("", tk.END,
                              values=(r["name"], r["size"], r["md5"], r["sha256"], r["status"]),
                              tags=(r["tag"],))
            id_to_result[iid] = r

        # ── hashes.json helpers ───────────────────────────────────────────────
        def load_reference():
            """Returns current hashes.json content or an empty dict."""
            if hashes_path.exists():
                try:
                    with open(hashes_path, encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    return {}
            return {}

        def save_reference(ref):
            """Persists the reference dict to hashes.json (creates if absent).

            Args:
                ref: Dict mapping filename → {md5, sha256}.
            """
            hashes_path.parent.mkdir(parents=True, exist_ok=True)
            with open(hashes_path, "w", encoding="utf-8") as f:
                json.dump(ref, f, indent=2, ensure_ascii=False)

        def recompute_status(row_data, ref):
            """Returns (status_str, tag) for row_data against current reference.

            Files with read errors keep their error status regardless of the reference.

            Args:
                row_data: Result dict for one file.
                ref: Current hashes.json dict.

            Returns:
                Tuple[str, str]: Human-readable status and Treeview tag name.
            """
            if row_data["md5"] in ("ОШИБКА", "—"):
                return row_data["status"], row_data["tag"]
            entry = ref.get(row_data["name"], {})
            if not entry:
                return "⚠️ Нет эталона", "no_ref"
            if (entry.get("md5", "").lower() == row_data["md5"].lower() and
                    entry.get("sha256", "").lower() == row_data["sha256"].lower()):
                return "✅ Совпадает", "ok"
            return "❌ Не совпадает", "fail"

        def refresh_row(iid, row_data):
            """Redraws one Treeview row from the (already updated) row_data dict."""
            tree.item(iid, values=(
                row_data["name"], row_data["size"],
                row_data["md5"], row_data["sha256"], row_data["status"],
            ), tags=(row_data["tag"],))

        # ── Selection helper ──────────────────────────────────────────────────
        def get_selected():
            """Returns (iid, row_data) for the selected row, or warns and returns (None, None)."""
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Нет выбора", "Выберите файл в таблице.", parent=win)
                return None, None
            iid = sel[0]
            return iid, id_to_result[iid]

        # ── Edit dialog ───────────────────────────────────────────────────────
        def open_edit_dialog(iid, row_data):
            """Opens the reference-hash editing dialog for a single file.

            Args:
                iid: Treeview item id for the file.
                row_data: Mutable result dict for the file.
            """
            if row_data["md5"] in ("ОШИБКА", "—"):
                messagebox.showwarning(
                    "Недоступно",
                    "Нельзя добавить эталон для файла с ошибкой чтения.",
                    parent=win,
                )
                return

            ref = load_reference()
            current = ref.get(row_data["name"], {})

            dlg = tk.Toplevel(win)
            dlg.transient(win)
            dlg.title(f"Редактирование эталона: {row_data['name']}")
            dlg.geometry("520x185")
            dlg.resizable(False, False)
            dlg.grab_set()

            ttk.Label(dlg, text="MD5 (32 hex-символа):").grid(
                row=0, column=0, sticky=tk.W, padx=12, pady=(16, 5))
            md5_var = tk.StringVar(value=current.get("md5", row_data["md5"]))
            md5_entry = ttk.Entry(dlg, textvariable=md5_var, width=46, font=("Consolas", 10))
            md5_entry.grid(row=0, column=1, padx=(0, 12), pady=(16, 5), sticky=tk.EW)

            ttk.Label(dlg, text="SHA256 (64 hex-символа):").grid(
                row=1, column=0, sticky=tk.W, padx=12, pady=5)
            sha256_var = tk.StringVar(value=current.get("sha256", row_data["sha256"]))
            sha256_entry = ttk.Entry(dlg, textvariable=sha256_var, width=46, font=("Consolas", 10))
            sha256_entry.grid(row=1, column=1, padx=(0, 12), pady=5, sticky=tk.EW)

            dlg.columnconfigure(1, weight=1)

            def validate_hex(value, expected_len, label):
                """Returns (cleaned_str, error_msg_or_None)."""
                s = value.strip().lower()
                if len(s) != expected_len:
                    return None, f"{label}: длина должна быть {expected_len} символов (введено {len(s)})"
                if not all(c in "0123456789abcdef" for c in s):
                    return None, f"{label}: допустимы только символы 0–9 и a–f"
                return s, None

            def on_save():
                md5_clean, err = validate_hex(md5_var.get(), 32, "MD5")
                if err:
                    messagebox.showerror("Ошибка ввода", err, parent=dlg)
                    return
                sha256_clean, err = validate_hex(sha256_var.get(), 64, "SHA256")
                if err:
                    messagebox.showerror("Ошибка ввода", err, parent=dlg)
                    return

                ref = load_reference()
                ref[row_data["name"]] = {"md5": md5_clean, "sha256": sha256_clean}
                try:
                    save_reference(ref)
                except Exception as e:
                    messagebox.showerror("Ошибка записи",
                                         f"Не удалось сохранить hashes.json:\n{e}", parent=dlg)
                    return

                new_status, new_tag = recompute_status(row_data, ref)
                row_data["status"] = new_status
                row_data["tag"]    = new_tag
                refresh_row(iid, row_data)

                self.add_test_log(f"✏️ Добавлен эталон для {row_data['name']}")
                messagebox.showinfo("Готово", "Эталон сохранён", parent=dlg)
                dlg.destroy()

            btn_row = ttk.Frame(dlg)
            btn_row.grid(row=2, column=0, columnspan=2, pady=14)
            ttk.Button(btn_row, text="Сохранить", command=on_save).pack(side=tk.LEFT, padx=10)
            ttk.Button(btn_row, text="Отмена",    command=dlg.destroy).pack(side=tk.LEFT)

            md5_entry.focus_set()
            dlg.bind("<Return>", lambda _: on_save())
            dlg.bind("<Escape>", lambda _: dlg.destroy())

        # ── Delete reference ──────────────────────────────────────────────────
        def delete_reference(iid, row_data):
            """Removes the reference entry for this file from hashes.json.

            Args:
                iid: Treeview item id.
                row_data: Mutable result dict for the file.
            """
            ref = load_reference()
            if row_data["name"] not in ref:
                messagebox.showinfo("Нет эталона",
                                    f"Для файла «{row_data['name']}» эталон не задан.",
                                    parent=win)
                return
            if not messagebox.askyesno("Подтверждение",
                                       f"Удалить эталон для:\n{row_data['name']}?",
                                       parent=win):
                return
            del ref[row_data["name"]]
            try:
                save_reference(ref)
            except Exception as e:
                messagebox.showerror("Ошибка записи",
                                     f"Не удалось сохранить hashes.json:\n{e}", parent=win)
                return

            new_status, new_tag = recompute_status(row_data, ref)
            row_data["status"] = new_status
            row_data["tag"]    = new_tag
            refresh_row(iid, row_data)

            self.add_test_log(f"🗑️ Удалён эталон для {row_data['name']}")
            messagebox.showinfo("Готово", "Эталон удалён", parent=win)

        # ── Context menu ──────────────────────────────────────────────────────
        ctx_menu = tk.Menu(win, tearoff=0)

        def show_context_menu(event):
            iid = tree.identify_row(event.y)
            if not iid:
                return
            tree.selection_set(iid)
            ctx_menu.post(event.x_root, event.y_root)

        def ctx_edit():
            iid, row_data = get_selected()
            if iid:
                open_edit_dialog(iid, row_data)

        def ctx_delete():
            iid, row_data = get_selected()
            if iid:
                delete_reference(iid, row_data)

        def ctx_copy(col_idx, label):
            iid, row_data = get_selected()
            if not iid:
                return
            value = tree.item(iid, "values")[col_idx]
            if value in ("ОШИБКА", "—", ""):
                return
            win.clipboard_clear()
            win.clipboard_append(value)
            messagebox.showinfo("Скопировано",
                                f"{label} скопирован в буфер обмена:\n{value}", parent=win)

        ctx_menu.add_command(label="✏️ Добавить/редактировать эталон", command=ctx_edit)
        ctx_menu.add_command(label="🗑️ Удалить эталон",                command=ctx_delete)
        ctx_menu.add_separator()
        ctx_menu.add_command(label="Скопировать MD5",    command=lambda: ctx_copy(2, "MD5"))
        ctx_menu.add_command(label="Скопировать SHA256", command=lambda: ctx_copy(3, "SHA256"))

        tree.bind("<Button-3>", show_context_menu)
        win.bind("<Button-1>", lambda e: ctx_menu.unpost())

        # ── Double-click copies MD5 / SHA256 ─────────────────────────────────
        HASH_COLS = {"#3": ("MD5", 2), "#4": ("SHA256", 3)}

        def on_double_click(event):
            col   = tree.identify_column(event.x)
            iid   = tree.identify_row(event.y)
            if not iid or col not in HASH_COLS:
                return
            label, idx = HASH_COLS[col]
            value = tree.item(iid, "values")[idx]
            if value in ("ОШИБКА", "—", ""):
                return
            win.clipboard_clear()
            win.clipboard_append(value)
            messagebox.showinfo("Скопировано",
                                f"{label} скопирован в буфер обмена:\n{value}", parent=win)

        tree.bind("<Double-1>", on_double_click)

        # ── Bottom bar ────────────────────────────────────────────────────────
        bottom = ttk.Frame(win)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=6, padx=8)

        hint = ttk.Label(bottom,
                         text="ПКМ или двойной клик по MD5/SHA256 — дополнительные действия",
                         foreground=COLORS["text_secondary"])
        hint.pack(side=tk.LEFT)

        def on_edit_btn():
            iid, row_data = get_selected()
            if iid:
                open_edit_dialog(iid, row_data)

        def on_delete_btn():
            iid, row_data = get_selected()
            if iid:
                delete_reference(iid, row_data)

        def save_csv():
            path = filedialog.asksaveasfilename(
                parent=win,
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=f"hash_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            )
            if not path:
                return
            try:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Имя файла", "Размер (МБ)", "MD5", "SHA256", "Статус"])
                    for r in results:
                        writer.writerow([r["name"], r["size"], r["md5"], r["sha256"], r["status"]])
                messagebox.showinfo("Сохранено", f"Отчёт сохранён:\n{path}", parent=win)
                self.add_test_log(f"💾 Отчёт хешей сохранён: {path}")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}", parent=win)

        ttk.Button(bottom, text="💾 Сохранить отчёт (CSV)", command=save_csv).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="🗑️ Удалить эталон",        command=on_delete_btn).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bottom, text="✏️ Добавить/редактировать эталон", command=on_edit_btn).pack(side=tk.RIGHT, padx=4)


    # Заголовки модальных окон, которые блокируют закрытие Р7 и которые
    # безопасно отменять: файловый диалог экспорта и вопрос о перезаписи.
    # Только составные фразы — по голому «сохранить» под маску попал бы и сам
    # вопрос «Сохранить изменения?», у которого отмена означает «не закрывать».
    BLOCKING_DIALOG_TITLES = (
        "сохранить как", "save as",
        "подтверждение сохранения", "confirm save as",
        "подтвердите перезапись", "confirm overwrite",
    )
    CANCEL_BUTTONS = ("отмена", "cancel", "отменить")


    # Пункт из дампа считается «уже был в базовом снимке» только если совпал
    # и ключ (тег/id/класс/текст), И положение на экране — с допуском.
    # Строгое совпадение только по ключу пряталось бы за один и тот же
    # generic-маркап: у Р7 и overflow-меню тулбара, и реальный пункт
    # контекстного меню могут быть `<li id="" class="">` с одинаковым
    # текстом (см. issue #9 — ровно так дамп раньше путал два разных
    # элемента). Допуск нужен ровно настолько, чтобы пережить субпиксельный
    # дрожащий рендер одного и того же попапа между снимками, а не чтобы
    # маскировать элемент, реально сидящий в другом месте экрана.
    CDP_ITEM_POSITION_TOLERANCE_PX = 30


    # ── Операции теста через CDP ─────────────────────────────────────────
    #
    # Тест-функции обоих воркеров сначала пробуют выполнить операцию через
    # внутренний api редактора по CDP (r7_webdriver_connector: asc_EditSelectAll,
    # asc_Copy/asc_Paste, asc_addWorksheet, asc_insertCells, asc_findCell) и
    # только если это не получилось — шлют клавиши через pyautogui, как раньше.
    #
    # Почему это лучше клавиатуры: клавиши уходят в то окно, которое сейчас в
    # фокусе, требуют развёрнутого окна Р7 и слепой навигации по меню (счётчик
    # `down` уезжает от любого лишнего пункта — см. issue #9), а результат
    # операции ничем не подтверждается. Вызов api идёт мимо фокуса и оконного
    # менеджера и возвращает состояние документа до и после — поэтому каждая
    # операция ниже ещё и проверяется.
    #
    # ГЛАВНОЕ ПРАВИЛО ПОСЛЕДОВАТЕЛЬНОСТЕЙ: изменяющий документ шаг (mutated в
    # ответе JS — вставка, новый лист, insertCells) должен быть В КОНЦЕ. Откат
    # на pyautogui повторяет операцию целиком, и если документ уже изменён
    # предыдущим шагом, правка применится дважды. _cdp_sequence на такой случай
    # откат запрещает (см. mutated_already), но полагаться на это как на
    # штатный путь нельзя — цифра замера в этот момент уже испорчена.
    CDP_OPS_ENABLED = True      # общий выключатель: False → всё идёт клавишами,
                                # как до перевода тестов на CDP (нужно, чтобы
                                # сравнить цифры двух путей на одной сборке)
    CDP_OP_TIMEOUT_SEC = 10.0        # обычная операция
    # Было 30 с — меньше предохранителя детектора (OP_MAX_WAIT_SEC = 180),
    # хотя вставка на test_50k уже занимала 27.9 с. На раздутом документе
    # вызов выходил за таймаут сокета, evaluate() отдавал None, откат на
    # клавиши запрещён (операция уже ушла в Р7) — и прогон терял цифру.
    # Длинная операция ждёт ответа столько же, сколько её ждёт детектор
    # (аудит 29.09.2026, пункт 14).
    CDP_LONG_OP_TIMEOUT_SEC = float(OP_MAX_WAIT_SEC)   # Ctrl+A, вставка, insertCells на большом
                                     # файле: Runtime.evaluate возвращается
                                     # только когда JS отработал, а это и есть
                                     # время самой операции


    # ── Чем подтверждается результат операции ────────────────────────────
    # Все проверки терпимы к None: снимок состояния собирается из отдельных
    # asc_-геттеров, и любой из них в чужой сборке может не существовать —
    # тогда поле приходит None, и «проверить не удалось» честнее, чем упасть.

    # Точное число строк листа зависит от сборки, поэтому проверяем не
    # совпадение с константой, а порядок величины: столько строк вручную не
    # выделяют.
    CDP_WHOLE_SHEET_MIN_ROWS = 10000


    # ── Сами операции ────────────────────────────────────────────────────
    # Каждая возвращает True, если делать что-то клавишами уже не нужно.

    # Сколько ждать подключения к CDP перед первой операцией. Больше, чем
    # BOLD_BUTTON_CDP_CONNECT_TIMEOUT_SEC (0.5 с): там короткий таймаут защищал
    # замер «Открытие файла», в который попадало ожидание, а здесь файл уже
    # открыт и торопиться некуда — зато от этого подключения зависит, пойдут
    # тесты через api или клавишами.
    CDP_CONNECT_TIMEOUT_SEC = 2.0


    _NO_CDP_MENU_ERROR = (
        "тест через контекстное меню невыполним без CDP: меню Р7 не "
        "управляется стрелками, а пункт по подписи ищется через DOM. "
        "Запустите программу из .venv (там есть requests и websocket-client)")


def _missing_cdp_warning():
    """Текст предупреждения перед прогоном, если CDP недоступен, иначе None."""
    if env.WEBDRIVER_OK:
        return None
    return ("Не установлены пакеты requests и websocket-client — нет доступа к "
            "интерфейсу Р7 через CDP.\n\n"
            "Без него модалку «Автоматический пересчёт может занять время» "
            "закрывает только Esc вслепую, тесты через контекстное меню не "
            "выполняются, а операции идут клавишами, и их цифры несравнимы с "
            "обычным прогоном.\n\n"
            f"Интерпретатор: {sys.executable}\n"
            f"Установить: \"{sys.executable}\" -m pip install requests websocket-client\n\n"
            "Продолжить без CDP?")


if __name__ == "__main__":
    if not ctypes.windll.shell32.IsUserAnAdmin():
        result = messagebox.askyesno("Права администратора", "Запустить от имени администратора?")
        if result:
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
            sys.exit()
    root = tk.Tk()
    app = R7Testovarka(root)
    root.mainloop()

