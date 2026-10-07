"""CLI-обёртка над run_crash_recovery_scenario (этап 3, M4) — запускает
сценарий восстановления после сбоя на конкретном файле и печатает вердикт.

Использование:
    python run_crash_recovery.py --file "TestFiles/файл.xlsx" --ops 5
    python run_crash_recovery.py --file "TestFiles/отчёт.docx" --ops 3 --timeout 45

Требует установленного Р7-Офис и пакетов requests/websocket-client (CDP).
Убивает процесс Р7 по-настоящему (proc.kill(), симуляция сбоя) — не
запускать на машине с несохранёнными документами пользователя.

Правки, диалог восстановления, проверка, уборка следов и отчёт — в
r7/crash_recovery.py (общий код с вкладкой «Сценарии»); здесь только разбор
аргументов, «голый» экземпляр приложения и печать итога. Имена из
r7.crash_recovery реэкспортированы ради прежних импортов.
"""
import argparse
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import r7_Testovarka as r7mod  # noqa: E402
from r7 import crash_recovery as cr  # noqa: E402

# Реэкспорт для прежних импортов (tests/test_run_crash_recovery_cli.py и
# docs): сами функции живут в r7.crash_recovery, подменять их — там.
RECOVERY_DIALOG_TITLES = cr.RECOVERY_DIALOG_TITLES
RECOVERY_BUTTON_PRIORITY = cr.RECOVERY_BUTTON_PRIORITY
RECOVERY_DIALOG_OTHER_BUTTONS = cr.RECOVERY_DIALOG_OTHER_BUTTONS
_cdp_click_on_any_target = cr._cdp_click_on_any_target
_find_and_handle_recovery_dialog_win32 = cr._find_and_handle_recovery_dialog_win32
_uia_dialog_controls = cr._uia_dialog_controls
_dialog_closed = cr._dialog_closed
_find_and_handle_recovery_dialog_uia = cr._find_and_handle_recovery_dialog_uia
_find_and_handle_recovery_dialog = cr._find_and_handle_recovery_dialog
_build_edits = cr._build_edits
_structure_units = cr._structure_units
_build_baseline_snapshot = cr._build_baseline_snapshot
_build_verify_recovered = cr._build_verify_recovered
_recover_dir = cr._recover_dir
_cleanup_crash_leftovers = cr._cleanup_crash_leftovers
build_report = cr.build_report
verdict_ok = cr.verdict_ok


def _resolve_file_path(raw):
    """Path(raw), устойчивый к рассинхронизации форм Юникода в argv.

    НАЙДЕНО ЖИВЫМ ПРОГОНОМ (25.08.2026): при вызове из Git Bash на
    Windows с кириллическим путём в --file MSYS2 передаёт argv в
    НОРМАЛИЗОВАННОЙ ПО-РАЗНОМУ форме относительно того, как имя реально
    лежит на NTFS (характерный симптом: путь визуально совпадает при
    print(), но path.exists() всё равно даёт False, а сравнение
    "entry == p.name" в os.listdir() — тоже False). Без этой правки
    скрипт рапортовал бы "Файл не найден" на существующем файле.

    Раз как именно нормализован конкретный запуск — заранее не известно
    (зависит от версии Git for Windows/MSYS2 и от того, чем изначально
    создавался файл), пробуем путь как есть, потом NFC, потом NFD —
    первый, что реально существует, и используем.

    Args:
        raw: сырое значение args.file.

    Returns:
        Path: как есть, если он уже существует или ни один вариант не
        нашёлся (тогда ошибку "файл не найден" покажет вызывающий код);
        иначе — первый существующий нормализованный вариант.
    """
    candidate = Path(raw)
    if candidate.exists():
        return candidate
    for form in ("NFC", "NFD"):
        normalized = Path(unicodedata.normalize(form, raw))
        if normalized.exists():
            return normalized
    return candidate


def _make_bare_app():
    """"Голый" экземпляр R7Testovarka без Tk — тот же приём, что и в
    tests/conftest.py bare_r7, но для CLI-скрипта: даёт доступ к
    _find_r7_path/_get_r7_processes/_click_priority_button/
    _close_update_dialog_if_exists без создания окна."""
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app._cached_r7_path = None
    app._r7_pids = None
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Сценарий восстановления после сбоя Р7-Офис (этап 3, M4)")
    parser.add_argument("--file", required=True, help="Путь к тестовому файлу")
    parser.add_argument("--ops", type=int, default=5,
                        help="Число правок перед сбоем (по умолчанию 5)")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Таймаут ожидания диалога восстановления, сек "
                             "(по умолчанию 30)")
    args = parser.parse_args(argv)

    file_path = _resolve_file_path(args.file)
    if not file_path.exists():
        print(f"❌ Файл не найден: {file_path}")
        return 1

    log_lines = []

    def log_cb(msg):
        print(msg)
        log_lines.append(msg)

    if not r7mod.env.WEBDRIVER_OK:
        print("❌ WEBDRIVER_OK=False — requests/websocket-client не установлены, "
              "CDP недоступен (см. .venv/Scripts/python.exe -m pip install "
              "requests websocket-client)")
        return 1

    app = _make_bare_app()
    r7_path = app._find_r7_path()
    if not r7_path:
        print("❌ Р7-Офис не найден — проверьте, установлен ли он на этой машине")
        return 1

    outcome = cr.run_recovery_check(app, r7_path, file_path, args.ops, args.timeout,
                                    log_cb, log_lines)
    report = outcome["report"]
    if report is None:
        return 1
    result = outcome["result"]

    reports_dir = Path("Reports")
    reports_dir.mkdir(exist_ok=True)
    out_path = reports_dir / f"crash_recovery_{report['timestamp']}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print(f"Вердикт: {report['verdict']}")
    print(f"Процесс до сбоя подключился: {result.get('connected_before_crash')}")
    print(f"Процесс подтверждённо завершился после kill(): "
          f"{result.get('process_died_cleanly')}")
    print(f"Переподключение после перезапуска: {result.get('connected_after_crash')}")
    print(f"Время до переподключения: {result.get('time_to_reconnect_sec')} с")
    print(f"Диалог восстановления: {cr.dialog_summary(outcome['dialog'])}")
    print(f"Восстановлено правок: {result.get('recovered_count')}/{args.ops}")
    print(f"Отчёт сохранён: {out_path}")
    print("=" * 60)

    return 0 if report["verdict"] == "Успешно" else 1


if __name__ == "__main__":
    sys.exit(main())
