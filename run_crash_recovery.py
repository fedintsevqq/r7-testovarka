"""CLI-обёртка над run_crash_recovery_scenario (r7_Testovarka.py, этап 3,
M4) — запускает сценарий восстановления после сбоя на конкретном файле и
печатает вердикт.

Использование:
    python run_crash_recovery.py --file "TestFiles/файл.xlsx" --ops 5
    python run_crash_recovery.py --file "TestFiles/отчёт.docx" --ops 3 --timeout 45

Требует установленного Р7-Офис и пакетов requests/websocket-client (CDP).
Убивает процесс Р7 по-настоящему (proc.kill(), симуляция сбоя) — не
запускать на машине с несохранёнными документами пользователя.

Что здесь СВЕРХ run_crash_recovery_scenario и почему:

1. Правки (edits) и проверка восстановления (verify_recovered) подбираются
   по расширению файла — только теми CDP-операциями, что реально
   существуют для этого типа документа (см. Н6 в r7_Testovarka.py). Для
   Cell (.xlsx) прямой операции "напечатать текст в ячейку" через CDP НЕТ
   вовсе — сетка рисуется на <canvas>, а не в DOM, — поэтому правки для
   xlsx структурные (add_sheet), не текстовые. Для Word — insert_text +
   set_bold, для Slide — add_slide.

2. Поиск диалога восстановления (_find_and_handle_recovery_dialog) —
   НЕ ПРОВЕРЕНО ЖИВЫМ ПРОГОНОМ. Реальный заголовок такого диалога у этой
   сборки Р7 (если он вообще есть — см. M4 в реестре пробелов отчёта
   "Нагрузочный контур R7") не подтверждён, в отличие от диалога
   обновления (_close_update_dialog_if_exists), чьи заголовки сверены с
   живым Р7. Поэтому список ключевых слов — предположение по аналогии с
   другими продуктами линейки, нарочно широкое. Если диалог не появился
   за --timeout секунд, сценарий не считает это ошибкой (возможно, у Р7
   такого диалога нет, восстановление — если оно есть — происходит
   молча) — так же, как process_died_cleanly=False в самом сценарии не
   считается фатальной ошибкой, а честно фиксируется в отчёте.

3. verify_recovered тоже эвристика, не точное измерение: сетка/текст
   документа не читаются из DOM (тот же Canvas), поэтому проверяется не
   содержимое, а число структурных единиц (листов/слайдов) или позиция в
   истории правок — см. docstring _build_verify_recovered.

Живая проверка того, что здесь реально описывает поведение Р7 (появляется
ли диалог, какой у него заголовок и кнопки, восстанавливаются ли правки на
самом деле), — то, что явно осталось на пользователя при закрытии этапа 3
(см. M4 в отчёте). Если что-то из угаданного здесь не совпадёт с реальным
Р7 — RECOVERY_DIALOG_TITLES/RECOVERY_BUTTON_PRIORITY ниже надо будет
поправить по факту живого прогона, не переписывая остальной скрипт.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import r7_Testovarka as r7mod  # noqa: E402


# ── Диалог восстановления: ключевые слова НЕ подтверждены живым Р7 ──────
# Та же техника, что _close_update_dialog_if_exists использует для диалога
# обновления (составные фразы, owner-PID фильтр), но, в отличие от него,
# список здесь не сверен с реальной сборкой — см. докстринг модуля, п.2.
RECOVERY_DIALOG_TITLES = (
    "восстановление документов",
    "восстановить документы",
    "восстановление файлов",
    "document recovery",
    "recover documents",
    "автовосстановление",
    "autorecover",
)
RECOVERY_BUTTON_PRIORITY = (
    "восстановить",
    "recover",
    "да",
    "yes",
    "ok",
    "ок",
)


def _find_and_handle_recovery_dialog(app, log_cb, timeout):
    """Ищет окно восстановления среди top-level окон, принадлежащих
    процессам Р7 (owner-PID через GetWindowThreadProcessId — та же
    защита от ложных совпадений с чужими окнами, что и в
    _close_update_dialog_if_exists), и кликает по кнопке из
    RECOVERY_BUTTON_PRIORITY через app._click_priority_button. Не
    находит окно за timeout секунд — не ошибка, возвращается честный
    "не появилось".

    Args:
        app: "голый" экземпляр R7Testovarka (см. main()) — источник
            _get_r7_processes/_click_priority_button.
        log_cb: колбэк логирования.
        timeout: сколько секунд ждать появления диалога.

    Returns:
        dict: {"dialog_seen": bool, "dialog_title": str | None,
               "clicked": bool, "button_text": str | None,
               "elapsed_sec": float}
    """
    result = {"dialog_seen": False, "dialog_title": None,
              "clicked": False, "button_text": None, "elapsed_sec": 0.0}
    if not r7mod.WIN32_OK:
        log_cb("⚠️ win32gui недоступен — пропускаю поиск диалога восстановления")
        return result

    import win32gui
    import win32process

    def _r7_pids():
        return {
            p.pid for p in app._get_r7_processes(log_cb=lambda _m: None)
            if "x2t" not in (p.name() or "").lower()
        }

    def _owned_by_r7(hwnd, pids):
        if not pids:
            return False
        try:
            _, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return False
        return owner_pid in pids

    start = time.time()
    deadline = start + timeout
    found = []

    while True:
        pids = _r7_pids()

        def _enum(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).lower()
                if any(s in title for s in RECOVERY_DIALOG_TITLES) and _owned_by_r7(hwnd, pids):
                    found.append(hwnd)

        win32gui.EnumWindows(_enum, None)
        if found or time.time() >= deadline:
            break
        time.sleep(0.5)

    result["elapsed_sec"] = round(time.time() - start, 2)
    if not found:
        log_cb(f"ℹ️ Диалог восстановления не появился за {timeout} с "
              f"(это ожидаемо, если у Р7 такого диалога нет вовсе)")
        return result

    hwnd = found[0]
    result["dialog_seen"] = True
    result["dialog_title"] = win32gui.GetWindowText(hwnd)
    log_cb(f"🔍 Найдено окно восстановления: {result['dialog_title']!r}")

    clicked, button_text = app._click_priority_button(
        hwnd, RECOVERY_BUTTON_PRIORITY, log_cb=log_cb)
    result["clicked"] = clicked
    result["button_text"] = button_text
    if clicked:
        log_cb(f"✅ Нажата кнопка «{button_text}»")
    else:
        log_cb("⚠️ Диалог восстановления найден, но подходящая кнопка не найдена")
    return result


def _build_edits(file_path, n):
    """Правки перед "сбоем" — только реально существующими CDP-операциями
    для типа документа (см. докстринг модуля, п.1)."""
    suffix = file_path.suffix.lower()
    edits = []
    if suffix in (".docx", ".doc"):
        for i in range(n):
            edits.append(lambda c, i=i: c.insert_text(f"crash-test правка #{i + 1} "))
        edits.append(lambda c: c.set_bold(True))
    elif suffix in (".pptx", ".ppt"):
        for _ in range(n):
            edits.append(lambda c: c.add_slide(0))
    else:  # .xlsx и всё, что не Word/Slide, — считаем Cell
        for _ in range(n):
            edits.append(lambda c: c.add_sheet())
    return edits


def _build_verify_recovered(file_path, expected_ops):
    """Проверка восстановления — эвристика по структурным единицам
    (листы/слайды) или позиции в истории правок, не по содержимому (см.
    докстринг модуля, п.3): сетка/текст документа не читаются из DOM."""
    suffix = file_path.suffix.lower()

    def verify(conn):
        if suffix in (".docx", ".doc"):
            state = conn.word_state()
            if not state:
                return 0
            return expected_ops if (state.get("historyPoints") or 0) > 0 else 0
        elif suffix in (".pptx", ".ppt"):
            state = conn.slide_state()
            if not state:
                return 0
            count = state.get("slideCount") or 1
            return min(expected_ops, max(0, count - 1))
        else:
            state = conn.document_state()
            if not state:
                return 0
            sheets = state.get("sheets") or 1
            return min(expected_ops, max(0, sheets - 1))

    return verify


def _make_bare_app():
    """"Голый" экземпляр R7Testovarka без Tk — тот же приём, что и в
    tests/conftest.py bare_r7, но для CLI-скрипта: даёт доступ к
    _find_r7_path/_get_r7_processes/_click_priority_button/
    _close_update_dialog_if_exists без создания окна."""
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app._cached_r7_path = None
    app._r7_pids = None
    return app


def build_report(file_path, ops, timeout, scenario_result, dialog_result,
                 total_elapsed_sec, log_lines):
    """Собирает JSON-отчёт из результата сценария — вынесено в отдельную
    функцию, чтобы формат отчёта был тестируем без живого Р7."""
    ok = verdict_ok(scenario_result)
    return {
        "file": str(file_path),
        "ops_requested": ops,
        "timeout_sec": timeout,
        "timestamp": time.strftime("%Y%m%d_%H%M%S"),
        "total_elapsed_sec": round(total_elapsed_sec, 2),
        "verdict": "Успешно" if ok else "Ошибка",
        "scenario": {k: v for k, v in scenario_result.items() if k != "proc"},
        "recovery_dialog": dialog_result,
        "log": log_lines,
    }


def verdict_ok(scenario_result):
    """Успех = процесс до сбоя дал подключиться, реально умер после kill(),
    переподключение после перезапуска прошло, и verify_recovered увидела
    хотя бы одну восстановленную правку. Любое из условий не выполнено —
    вердикт "Ошибка", а не частичный успех: цель сценария — ответить
    да/нет на вопрос "восстанавливается ли документ", а не намекать."""
    return bool(
        scenario_result.get("connected_before_crash")
        and scenario_result.get("process_died_cleanly")
        and scenario_result.get("connected_after_crash")
        and scenario_result.get("recovered_count") is not None
        and scenario_result.get("recovered_count") > 0
    )


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

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"❌ Файл не найден: {file_path}")
        return 1

    log_lines = []

    def log_cb(msg):
        print(msg)
        log_lines.append(msg)

    if not r7mod.WEBDRIVER_OK:
        print("❌ WEBDRIVER_OK=False — requests/websocket-client не установлены, "
             "CDP недоступен (см. .venv/Scripts/python.exe -m pip install "
             "requests websocket-client)")
        return 1

    app = _make_bare_app()
    r7_path = app._find_r7_path()
    if not r7_path:
        print("❌ Р7-Офис не найден — проверьте, установлен ли он на этой машине")
        return 1

    edits = _build_edits(file_path, args.ops)
    verify_recovered = _build_verify_recovered(file_path, args.ops)

    dialog_result = {}

    def after_relaunch(proc):
        nonlocal dialog_result
        # Диалог обновления не связан с crash-recovery, но может закрыть
        # собой окно восстановления — гасим его первым, если он всплыл.
        app._close_update_dialog_if_exists(log_cb=log_cb, search_timeout=2)
        dialog_result = _find_and_handle_recovery_dialog(app, log_cb, args.timeout)
        return dialog_result

    log_cb(f"🚀 Запускаю crash-recovery сценарий: {file_path}, {args.ops} правок")
    start = time.time()
    try:
        result = r7mod.run_crash_recovery_scenario(
            r7_path, file_path, edits, verify_recovered,
            after_relaunch=after_relaunch, log_cb=log_cb,
        )
    except Exception as e:
        print(f"❌ Сценарий упал с исключением: {type(e).__name__}: {e}")
        return 1
    total_elapsed = time.time() - start

    proc = result.get("proc")
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass

    report = build_report(file_path, args.ops, args.timeout, result,
                          dialog_result, total_elapsed, log_lines)

    reports_dir = Path("Reports")
    reports_dir.mkdir(exist_ok=True)
    out_path = reports_dir / f"crash_recovery_{report['timestamp']}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    dialog_summary = (
        "найден и нажат" if dialog_result.get("clicked")
        else "найден, кнопка не нажата" if dialog_result.get("dialog_seen")
        else "не появился"
    )
    print()
    print("=" * 60)
    print(f"Вердикт: {report['verdict']}")
    print(f"Процесс до сбоя подключился: {result.get('connected_before_crash')}")
    print(f"Процесс подтверждённо завершился после kill(): "
         f"{result.get('process_died_cleanly')}")
    print(f"Переподключение после перезапуска: {result.get('connected_after_crash')}")
    print(f"Время до переподключения: {result.get('time_to_reconnect_sec')} с")
    print(f"Диалог восстановления: {dialog_summary}")
    print(f"Восстановлено правок: {result.get('recovered_count')}/{args.ops}")
    print(f"Отчёт сохранён: {out_path}")
    print("=" * 60)

    return 0 if verdict_ok(result) else 1


if __name__ == "__main__":
    sys.exit(main())
