"""Мастер первого запуска: проверки стенда без Tk (окно — r7/ui/firstrun_dialog.py).

Инструмент ставит коллега без автора рядом, и первый отчёт должен
получиться с первого раза. Каждая проверка отвечает (имя, статус, что
нашли, как починить): «fail» — прогон не пойдёт (нет Р7, нет фикстуры,
сборка без модуля), «warn» — пойдёт, но цифры будут с оговоркой (нет прав,
занят порт CDP, масштаб не 100 %, мало места). Сами проверки берутся там,
где уже есть: сборка — r7.selfcheck, порт — ReadinessMixin._cdp_port_free,
фикстура — batch_config.find_test_file, процессы — _get_r7_processes,
путь к Р7 и где искали — _find_r7_path (tests/ci_preflight.py тоже зовёт
проверки отсюда).
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from r7 import config, env, privileges, selfcheck
from r7.batch_config import TEST_FILE_PATTERNS, find_test_file
from r7.readiness import ReadinessMixin
from r7.run_state import missing_packages

OK, WARN, FAIL = "ok", "warn", "fail"

MIN_FREE_DISK_GB = 5.0          # меньше на диске отчётов — предупреждение
EXPECTED_DPI_SCALE_PCT = 100    # то же, что R7Testovarka.EXPECTED_DPI_SCALE_PCT

# Фикстуру ищем и по кириллическому имени (batch_config), и по новому
# латинскому: «й» в кириллическом имени хранится в NFD и на другом ПК может
# не совпасть с шаблоном (docs/plan-to-20.md, этап 1, пункт 4).
LATIN_FIXTURE_PATTERNS = ("r7-test-50k*.xlsx",)
FIXTURE_PATTERNS = tuple(TEST_FILE_PATTERNS) + LATIN_FIXTURE_PATTERNS


@dataclass(frozen=True)
class Check:
    """Итог одной проверки: статус OK/WARN/FAIL, что нашли и как починить."""
    name: str
    status: str
    detail: str
    fix: str = ""


def fixture_search_dirs(test_files_folder: str | os.PathLike[str]) -> list[Path]:
    """Где лежит рабочая фикстура — те же папки, что у _locate_test_file
    (r7/perf.py): TestFiles, папка программы, загрузки, текущая."""
    return [Path(test_files_folder), config.BASE_DIR, Path.home() / "Downloads",
            Path.home() / "Загрузки", Path.cwd()]


# ── Проверки ──────────────────────────────────────────────────────────────

def check_build() -> Check:
    """Сборка: все модули пакета, зависимости и шаблоны отчётов на месте
    (r7.selfcheck). В .exe PyInstaller теряет их молча."""
    lines: list[str] = []
    code = selfcheck.run(out=lines.append)
    problems = [ln.strip().lstrip("✗ ") for ln in lines if ln.strip().startswith("✗")]
    if code == 0:
        return Check("Сборка программы", OK, "модули, зависимости и шаблоны отчётов на месте")
    return Check("Сборка программы", FAIL, "; ".join(problems) or "самопроверка не прошла",
                 "Переустановите зависимости: .venv\\Scripts\\python.exe -m pip install "
                 "-r requirements.txt, или возьмите свежий .exe")


def check_admin(is_admin: bool | None = None) -> Check:
    """Права администратора: без них прогон идёт, но кэш ОС не сбрасывается
    и версии не ставятся."""
    admin = privileges.is_admin() if is_admin is None else is_admin
    if admin:
        return Check("Права администратора", OK, "есть")
    return Check("Права администратора", WARN,
                 "нет: файловый кэш ОС перед открытием не сбросится (открытие может быть "
                 "тёплым), установка версий и Batch недоступны",
                 "Для холодного старта и установки версий запустите программу от имени "
                 "администратора")


def check_r7_found(app: Any) -> Check:
    """Р7-Офис найден: явный путь из r7_settings.json, реестр, запасные пути.
    Если нет — показываем, где искали (_find_r7_path запоминает)."""
    path = app._find_r7_path()
    if path:
        version = (getattr(app, "current_version_info", None) or {}).get("version")
        return Check("Р7-Офис", OK, f"{path}" + (f" (версия {version})" if version else ""))
    searched = getattr(app, "_r7_path_searched", None) or []
    where = "; ".join(str(s) for s in searched) or "реестр и Program Files"
    return Check("Р7-Офис", FAIL, f"не найден. Искали: {where}",
                 "Установите Р7-Офис или укажите путь к DesktopEditors.exe в "
                 "r7_settings.json (ключ r7_path)")


def check_r7_running(app: Any) -> Check:
    """Р7 сейчас не запущен: к работающему процессу CDP-порт не подключить,
    а клавиши ушли бы в чужой документ."""
    procs = app._get_r7_processes(log_cb=lambda *_: None)
    if not procs:
        return Check("Р7-Офис закрыт", OK, "процессов Р7 нет")
    pids = ", ".join(str(p.pid) for p in procs)
    return Check("Р7-Офис закрыт", WARN, f"Р7-Офис уже запущен (PID: {pids})",
                 "Закройте Р7-Офис перед прогоном: инструмент запускает его сам")


def check_cdp_port(port: int | None = None,
                   port_free: Callable[[int], bool] | None = None) -> Check:
    """Порт CDP свободен: иначе Р7 запустится без отладочного порта и
    операции пойдут клавишами (или на 8081/8082, если они свободны)."""
    port = env.DEFAULT_CDP_PORT if port is None else port
    probe = ReadinessMixin._cdp_port_free if port_free is None else port_free
    if probe(port):
        return Check("Порт CDP", OK, f"{port} свободен")
    return Check("Порт CDP", WARN, f"порт {port} занят другой программой",
                 "Закройте программу, которая слушает порт, или оставьте: инструмент "
                 "попробует 8081 и 8082")


def check_fixture(search_dirs: Iterable[str | os.PathLike[str]]) -> Check:
    """Рабочая фикстура 50К: кириллическое или латинское имя в известных папках."""
    found, _locks = find_test_file(search_dirs, FIXTURE_PATTERNS)
    if found:
        size_mb = found.stat().st_size / (1024 * 1024)
        return Check("Тестовый файл", OK, f"{found} ({size_mb:.0f} МБ)")
    return Check("Тестовый файл", FAIL,
                 "файл 50К строк не найден в TestFiles, папке программы и загрузках",
                 "Создайте его кнопкой «Тестовые файлы» (профиль 50К) или положите "
                 "r7-test-50k.xlsx в папку TestFiles")


def check_disk(folder: str | os.PathLike[str], min_free_gb: float = MIN_FREE_DISK_GB) -> Check:
    """Свободное место на диске с отчётами: экспорт пишет сотни мегабайт."""
    probe = Path(folder)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free_gb = shutil.disk_usage(probe).free / (1024 ** 3)
    except OSError as e:
        return Check("Место на диске", WARN, f"не прочиталось ({e})",
                     "Проверьте, что папка отчётов доступна")
    drive = os.path.splitdrive(str(probe))[0] or str(probe)
    if free_gb >= min_free_gb:
        return Check("Место на диске", OK, f"{drive} свободно {free_gb:.1f} ГБ")
    return Check("Место на диске", WARN,
                 f"{drive} свободно {free_gb:.1f} ГБ, нужно хотя бы {min_free_gb:.0f}",
                 "Освободите место: при нехватке конвертер x2t падает, а запись замедляется")


def check_dpi(scale_pct: int | None, expected: int = EXPECTED_DPI_SCALE_PCT) -> Check:
    """Масштаб экрана: окно Р7 фиксированного размера при 125–150 % не
    помещается, и такие прогоны с прогонами при 100 % не сравнить."""
    if scale_pct is None:
        return Check("Масштаб экрана", WARN, "не определён",
                     "Проверьте в параметрах экрана Windows, что масштаб 100 %")
    if scale_pct == expected:
        return Check("Масштаб экрана", OK, f"{scale_pct} %")
    return Check("Масштаб экрана", WARN, f"{scale_pct} %, ожидается {expected} %",
                 "Поставьте масштаб 100 % в параметрах экрана Windows: иначе окно Р7 "
                 "другого размера и результаты не сравнимы с другими ПК")


def check_packages() -> Check:
    """Необязательные пакеты: без CDP операции идут клавишами, без
    pywinauto не переключить тип файла в «Сохранить как»."""
    missing = missing_packages(env.PYAUTOGUI_OK, bool(env.pyperclip), env.EXCEL_OK,
                               env.WIN32_OK)
    if not env.WEBDRIVER_OK:
        missing.append("requests websocket-client (CDP)")
    if not env.PYWINAUTO_OK:
        missing.append("pywinauto")
    if not env.PSUTIL_OK:
        missing.append("psutil")
    if not missing:
        return Check("Пакеты Python", OK, "все на месте, CDP доступен")
    return Check("Пакеты Python", WARN, "нет: " + ", ".join(missing),
                 "Запустите программу из .venv или поставьте пакеты: "
                 ".venv\\Scripts\\python.exe -m pip install -r requirements.txt")


def run_checks(app: Any) -> list[Check]:
    """Все проверки по порядку — для окна мастера. app — R7Testovarka."""
    return [
        check_build(),
        check_admin(),
        check_r7_found(app),
        check_r7_running(app),
        check_cdp_port(),
        check_fixture(fixture_search_dirs(app.test_files_folder)),
        check_disk(app.reports_folder),
        check_dpi(app._get_dpi_scale_pct()),
        check_packages(),
    ]


def has_failures(checks: Sequence[Check]) -> bool:
    return any(c.status == FAIL for c in checks)
