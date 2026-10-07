# -*- coding: utf-8 -*-
"""R7-Testovarka — замеры производительности Р7-Офис (табличный редактор).

Точка входа: запуск двойным щелчком, перезапуск под .venv, сборка класса
приложения из примесей пакета r7 (docs/plan-to-8.md, этап 2). Код — в r7/:
замер, процессы, окна, CDP, экспорт, отчёты, интерфейс (r7/ui/).
"""

import os
import sys
import subprocess
import time
import threading
import shutil
import ctypes
import statistics
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

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
except Exception:  # нет консоли — остаётся кодировка по умолчанию
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
# всех модулей пакета и для подмен в тестах (env.PSUTIL_OK, env.R7WebDriverConnector).
from r7 import env, logfile, privileges, settings  # noqa: E402
from r7.env import psutil, pyperclip  # noqa: E402
from r7.config import SERIES_COLORS  # noqa: E402

print(f"🔍 WEBDRIVER_OK после импорта: {env.WEBDRIVER_OK} (файл: {__file__}, cwd: {os.getcwd()})")

# Класс приложения собирается из примесей: каждая — свой модуль пакета.
# Модули r7.* (кроме r7.ui) tkinter не импортируют.
from r7.bold_button import BoldButtonMixin  # noqa: E402
from r7.cdp import CdpMixin  # noqa: E402
from r7.dialogs import DialogsMixin  # noqa: E402
from r7.export import ExportMixin  # noqa: E402
from r7.fixtures import FixturesMixin  # noqa: E402
from r7.measure import MeasureMixin  # noqa: E402
from r7.op_end import OpEndMixin  # noqa: E402
from r7.perf import PerfRunMixin  # noqa: E402
from r7.processes import ProcessesMixin  # noqa: E402
from r7.readiness import ReadinessMixin  # noqa: E402
from r7.resources import ResourcesMixin  # noqa: E402
from r7.results import ResultsMixin  # noqa: E402
from r7.run_state import RunState, RunStateMixin  # noqa: E402
from r7.runs import RunsMixin  # noqa: E402
from r7.stand import StandMixin  # noqa: E402
from r7.test_prep import TestPrepMixin  # noqa: E402
from r7.trace import TraceMixin  # noqa: E402
from r7.ui_fallback import UiFallbackMixin  # noqa: E402
from r7.ux_metrics import UxMetricsMixin  # noqa: E402
from r7.versions import VersionsMixin  # noqa: E402
from r7.windows import WindowsMixin  # noqa: E402
from r7.x2t_files import X2tFilesMixin  # noqa: E402
from r7.ui.base import UiBaseMixin  # noqa: E402
from r7.ui.batch import BatchUiMixin  # noqa: E402
from r7.ui.compare import CompareMixin  # noqa: E402
from r7.ui.main_window import MainWindowMixin  # noqa: E402
from r7.ui.perf_tab import PerfTabMixin  # noqa: E402
from r7.ui.scenarios_tab import ScenariosTabMixin  # noqa: E402
from r7.ui.versions_tab import VersionsTabMixin  # noqa: E402

# Имена, которые старые тесты и скрипты берут как r7_Testovarka.X (подмены
# time.sleep, subprocess.Popen и т. п. действуют глобально — это сами модули).
__all__ = ["R7Testovarka", "env", "psutil", "pyperclip", "tk", "os", "sys", "subprocess",
           "time", "threading", "shutil", "ctypes", "statistics", "Path",
           "get_base_dir", "BASE_DIR", "_venv_python_for_relaunch", "_ui_packages_present"]


class R7Testovarka(RunStateMixin, ProcessesMixin, WindowsMixin, MeasureMixin, CdpMixin, ReadinessMixin,
                   ExportMixin, DialogsMixin, VersionsMixin, FixturesMixin,
                   ResultsMixin, RunsMixin, ResourcesMixin, PerfRunMixin,
                   UiBaseMixin, MainWindowMixin, VersionsTabMixin, PerfTabMixin,
                   CompareMixin, BatchUiMixin, ScenariosTabMixin, OpEndMixin, TestPrepMixin,
                   BoldButtonMixin, UiFallbackMixin, X2tFilesMixin, StandMixin,
                   UxMetricsMixin, TraceMixin):
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
        self.root.title("R7-Testovarka")
        self.root.resizable(True, True)
        # Ниже сетка карточек на вкладке «Производительность» (Canvas шириной
        # 380px) и лог рядом с ней уже не помещаются вменяемо — без явного
        # предела окно можно было сжать до состояния, где всё наезжает друг
        # на друга.
        self.root.minsize(self.MIN_WIN_W, self.MIN_WIN_H)

        self._init_state()

        self.setup_ui()
        self._drain_test_log()          # журнал фоновых потоков → виджет, раз в 50 мс
        self.refresh_distributives()
        self.detect_current_version()
        self._start_update_check()      # новая версия в GitHub Releases — ссылка в шапке
        # Размер окна — после сборки интерфейса: только тогда известно,
        # сколько места ему нужно на самом деле (с учётом масштаба экрана).
        self._apply_default_geometry()

    def _init_state(self):
        """Состояние прогона и папки — всё, что не Tk.

        Отдельно от __init__, потому что командная строка (r7/cli.py,
        `python -m r7 run`) собирает тот же экземпляр без окна: R7Testovarka
        через __new__ и этот метод, а виджеты и диалоги подменяются
        заглушками. Прежде этот список жил только в __init__, и «голые»
        экземпляры в tests/manual_cdp_smoke.py повторяли его вручную.
        """
        self.distributives_folder = BASE_DIR / "Distributives"
        self.distributives_folder.mkdir(exist_ok=True)

        self.test_files_folder = BASE_DIR / "TestFiles"
        self.test_files_folder.mkdir(exist_ok=True)

        # Папка отчётов — из r7_settings.json (reports_folder), иначе Reports
        # рядом с программой. Недоступная папка из настроек — предупреждение в
        # журнал и штатная Reports: отчёты важнее настройки.
        self.reports_folder = self._resolve_reports_folder(settings.get("reports_folder"))
        self.reports_folder.mkdir(parents=True, exist_ok=True)

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
        self.scenario_stop_event = threading.Event()   # вкладка «Сценарии»: soak и multidoc
        # Какой прогон идёт (вкладка, Batch, свой файл, сценарий) — r7.run_state.RunState:
        # все работают с одним процессом Р7-Офис и не должны идти одновременно.
        self._run_state = RunState()


    # ---------------------- Вспомогательные методы (ресурсы, отчёты) ------

    @staticmethod
    def _resolve_reports_folder(custom):
        """Папка отчётов: custom из r7_settings.json, если задана и её можно
        создать, иначе BASE_DIR/Reports. Чистая функция — проверяется без Tk."""
        default = BASE_DIR / "Reports"
        if not custom:
            return default
        folder = Path(os.path.expandvars(str(custom)))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logfile.get_logger().warning("папка отчётов из r7_settings.json недоступна "
                                         "(%s: %s) — отчёты пишутся в %s", folder, e, default)
            return default
        logfile.get_logger().info("папка отчётов из r7_settings.json: %s", folder)
        return folder

    # Сброс файлового кэша ОС перед каждым холодным стартом (аудит 29.09.2026,
    # пункт 11). _clear_r7_cache чистит только %TEMP% Р7, а DLL редактора и
    # сам тестовый файл остаются в standby-кэше Windows: «холодный старт» без
    # сброса на деле был тёплым, и первый запуск после перезагрузки стенда
    # отличался от всех следующих. Требует прав администратора: без них сброс
    # пропускается с меткой в «Условиях прогона» отчёта (r7/privileges.py,
    # docs/first-run.md). Сброс кэша безопасен — это только освобождение
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


    HEAVY_CALC_CHECK_SEC = 0.3   # как часто искать модалку «пересчёт может занять время»
    HEAVY_CALC_EVAL_TIMEOUT_SEC = 0.5


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


    # ── CDP-триггер готовности (кнопка «Жирный» в DOM) ─────────────────────
    # В отличие от _wait_for_bold_button (win32gui) — реально видит кнопку:
    # панель инструментов Р7 рисуется как HTML внутри CEF-рендера, а не
    # набором нативных Win32-виджетов (см. r7_webdriver_connector.py и
    # коммит 7978206). Требует, чтобы Р7 в этом запуске был стартован с
    # --ascdesktop-support-debug-info (см. _prepare_webdriver_launch) — без
    # этого self._webdriver_connector остаётся None, и весь блок ниже
    # молча ничего не делает, оставляя работу win32gui/CPU-логике.


    # ── Страница трендов (этап 2, M5) ───────────────────────────────────────
    TRENDS_CHART_COLORS = SERIES_COLORS


    # --- Хранилище последних параметров тестового файла ---
    _LAST_PARAMS_FILE = "last_test_params.json"


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


    _NO_CDP_MENU_ERROR = (
        "тест через контекстное меню невыполним без CDP: меню Р7 не "
        "управляется стрелками, а пункт по подписи ищется через DOM. "
        "Запустите программу из .venv (там есть requests и websocket-client)")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        # Проверка сборки без окна и без прав (CI собирает .exe и зовёт это).
        from r7 import selfcheck
        sys.exit(selfcheck.run())
    # Файловый журнал — до запроса прав и до окна: сбой на старте тоже должен
    # оставить след в Reports/logs (см. r7/logfile.py).
    logfile.setup_logging(BASE_DIR)
    # Корень Tk — до любого messagebox: без него окно сообщения создаёт
    # своё пустое окно-родителя. Главное окно скрыто, пока интерфейс не собран.
    root = tk.Tk()
    root.withdraw()
    if not privileges.is_admin():
        if messagebox.askyesno("Права администратора", "Запустить от имени администратора?",
                               parent=root):
            from r7 import elevation
            logfile.get_logger().info("перезапуск от имени администратора")
            if elevation.relaunch_as_admin(sys.argv, sys.executable,
                                           getattr(sys, "frozen", False)):
                root.destroy()
                sys.exit()
            # Отказ в UAC или сбой запуска — работаем дальше без прав, но
            # предупреждаем, чего не будет.
            logfile.get_logger().warning("UAC отклонён или запуск не удался — работа без прав")
            messagebox.showwarning(
                "Права администратора",
                "Запуск без прав администратора: установка версий и сброс кэша ОС недоступны",
                parent=root)
    app = R7Testovarka(root)
    # Мастер первого запуска: проверки стенда, пока главное окно ещё скрыто.
    # Показывается, пока в r7_settings.json нет first_run_done; главное окно
    # открывается в любом случае — провал проверки только показан пользователю.
    if not settings.get("first_run_done"):
        from r7.ui.firstrun_dialog import show_first_run_dialog
        show_first_run_dialog(app)
    app.show_main_window()
    root.mainloop()

