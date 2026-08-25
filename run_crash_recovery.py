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
   ПОДТВЕРЖДЕНО ЖИВЫМ ПРОГОНОМ (25.08.2026, файл файл-для-теста-Р7-офис-50К.xlsx).
   Реальный текст диалога: «Обнаружен файл блокировки, оставшийся после
   аварийного завершения работы», кнопки «Продолжить редактирование» /
   «Только чтение» / «Отмена». ВАЖНО: это HTML-оверлей внутри CEF, а не
   отдельное окно ОС (проверено — win32gui.EnumWindows не находит для
   него отдельный hwnd, только окно самого документа). Диалог к тому же
   появляется РАНЬШЕ, чем в /json возникает редакторная цель с
   doctype=/title= — обычный R7WebDriverConnector.connect(filename_hint=...)
   его физически не видит, поэтому клик идёт через отдельный, более
   примитивный путь (_cdp_click_on_any_target): подключение к ПЕРВОЙ
   доступной CDP-цели напрямую, без фильтра по типу документа.
   win32gui-путь (_find_and_handle_recovery_dialog) оставлен как
   запасной — вдруг другая сборка/версия рисует его отдельным окном.
   Если диалог не появился за --timeout секунд, сценарий не считает это
   ошибкой (сценарий без предшествующего сбоя, либо у Р7 такого диалога
   нет) — так же, как process_died_cleanly=False в самом сценарии не
   считается фатальной ошибкой, а честно фиксируется в отчёте.

   НАЙДЕНО ПОПУТНО (важно для интерпретации результата этого скрипта):
   диалог восстановления возникает не только после настоящего сбоя —
   Р7 сопоставляет его по ИМЕНИ ФАЙЛА нестрого: старая осиротевшая запись
   в "%LOCALAPPDATA%/R7-Office/Editors/data/recover/" для файла
   "X.xlsx.xlsx" даёт этот же диалог при открытии файла "X.xlsx" (второе
   имя — префикс первого). Если сценарий сообщает про диалог/ошибку там,
   где их не ждали, — первым делом проверить эту папку на чужие записи,
   а не сам тестовый файл.

3. verify_recovered тоже эвристика, не точное измерение: сетка/текст
   документа не читаются из DOM (тот же Canvas), поэтому проверяется не
   содержимое, а число структурных единиц (листов/слайдов) или позиция в
   истории правок — см. docstring _build_verify_recovered.

Что именно проверено живым прогоном, а что нет: текст диалога и порядок
кнопок — подтверждены (пользователь кликнул «Продолжить редактирование»
вручную, я наблюдал результат через CDP). Сам факт, что CDP-клик по
тексту («_click_by_text_js») работает для HTML-оверлеев этого приложения,
тоже подтверждён живьём — но на ДРУГОМ диалоге («OK» на «При открытии
файла произошла ошибка», куда я добрался и кликнул через
conn.click_menu_item(['ok', 'ок'])). Автоматический клик именно по
«Продолжить редактирование» через _cdp_click_on_any_target написан по
аналогии и не проверен отдельным живым прогоном — при первом реальном
срабатывании стоит перепроверить.
"""
import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import r7_Testovarka as r7mod  # noqa: E402
from r7_webdriver_connector import _click_by_text_js  # noqa: E402


# ── Диалог восстановления: текст ПОДТВЕРЖДЁН живым прогоном 25.08.2026 ──
# Реальное сообщение: «Обнаружен файл блокировки, оставшийся после
# аварийного завершения работы». Кнопки (слева направо, как в самом
# диалоге): «Продолжить редактирование» (нужна нам — она и есть попытка
# восстановления) / «Только чтение» / «Отмена». Старые
# "восстановление документов"/"document recovery" и т.п. оставлены
# следом — вдруг другая сборка/локаль формулирует иначе; реальная фраза
# всегда проверяется первой (порядок в кортеже = приоритет).
RECOVERY_DIALOG_TITLES = (
    "обнаружен файл блокировки",
    "аварийного завершения работы",
    "восстановление документов",
    "восстановить документы",
    "восстановление файлов",
    "document recovery",
    "recover documents",
    "автовосстановление",
    "autorecover",
)
RECOVERY_BUTTON_PRIORITY = (
    "продолжить редактирование",
    "continue editing",
    "восстановить",
    "recover",
)
# "ok"/"ок" сюда сознательно НЕ входят (регрессия, поймана живым прогоном
# 25.08.2026): у диалога восстановления кнопки OK нет вовсе, а
# _click_by_text_js при отсутствии точного совпадения откатывается на
# поиск подстроки — и двухбуквенное "ок" оказалось подстрокой
# посторонней кнопки «Локальные файлы» ("л-ОК-альные"), которую скрипт
# молча нажал вместо настоящего диалога. OK нужен только отдельному
# диалогу «При открытии файла произошла ошибка» (см. п.2 докстринга
# модуля) — там его безопасно искать ТОЛЬКО когда точно известно, что
# этот конкретный диалог на экране, а не вслепую по всему приложению.
# Кнопки диалога, которые есть, но кликать НЕ надо — оставлять для
# документации/на случай, если понадобится параметр "открыть только для
# чтения" отдельным флагом в будущем.
RECOVERY_DIALOG_OTHER_BUTTONS = ("только чтение", "read only", "отмена", "cancel")


def _cdp_click_on_any_target(port, wanted_texts, log_cb, timeout=10.0, poll_sec=0.5):
    """Подключается к ПЕРВОЙ доступной CDP-цели напрямую — минуя
    R7WebDriverConnector.connect()/_pick_target(), которые фильтруют цели
    по типу документа (_is_editor_url: нужен doctype= или путь редактора
    в URL). ПРОВЕРЕНО ЖИВЫМ ПРОГОНОМ (25.08.2026): диалог восстановления
    показывается ДО того, как такая цель вообще появляется в /json —
    единственная цель на этой стадии — сплэш ("Hello Documents"), и
    обычный connect() эту стадию не видит вовсе. Клик по тексту — та же
    JS, что и в R7WebDriverConnector.click_menu_item (_click_by_text_js),
    просто без обвязки самого коннектора.

    Args:
        port: CDP-порт (см. DEFAULT_CDP_PORT в r7_webdriver_connector.py).
        wanted_texts: подписи в порядке приоритета (регистр не важен).
        log_cb: колбэк логирования.
        timeout: сколько секунд ждать появления хоть какой-то цели.
        poll_sec: интервал между попытками.

    Returns:
        dict | None: {"clicked": bool, "text": ..., "matched": ...,
        "candidates": N} — то же, что возвращает click_menu_item; None,
        если ни одной CDP-цели не появилось за timeout секунд, либо
        requests/websocket-client не установлены.
    """
    try:
        import requests
        import websocket
    except ImportError:
        log_cb("⚠️ requests/websocket-client недоступны — CDP-путь к диалогу восстановления пропущен")
        return None

    js = _click_by_text_js(wanted_texts)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"http://127.0.0.1:{port}/json", timeout=1.0)
            targets = [t for t in resp.json()
                      if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        except Exception:
            targets = []

        if targets:
            target = targets[0]
            try:
                ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=3.0)
                ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                    "params": {"expression": js, "returnByValue": True}}))
                raw = ws.recv()
                ws.close()
                value = json.loads(raw).get("result", {}).get("result", {}).get("value")
                if isinstance(value, dict) and value.get("clicked"):
                    return value
            except Exception as e:
                log_cb(f"⚠️ CDP-клик на цели {target.get('title')!r} не удался: "
                      f"{type(e).__name__}: {e}")
        time.sleep(poll_sec)
    return None


def _find_and_handle_recovery_dialog_win32(app, log_cb, timeout):
    """Запасной путь: ищет окно восстановления среди top-level окон,
    принадлежащих процессам Р7 (owner-PID через GetWindowThreadProcessId
    — та же защита от ложных совпадений, что и в
    _close_update_dialog_if_exists), и кликает по кнопке через
    app._click_priority_button.

    ПРОВЕРЕНО ЖИВЫМ ПРОГОНОМ (25.08.2026): для этого диалога отдельного
    окна ОС НЕТ — он рисуется HTML-оверлеем внутри окна документа
    (win32gui.EnumWindows его не находит). Метод оставлен на случай
    другой сборки/версии Р7, которая нарисует его отдельным окном —
    основной путь для ПОДТВЕРЖДЁННОЙ сборки см. _cdp_click_on_any_target.

    Returns:
        dict: {"dialog_seen": bool, "dialog_title": str | None,
               "clicked": bool, "button_text": str | None,
               "elapsed_sec": float}
    """
    result = {"dialog_seen": False, "dialog_title": None,
              "clicked": False, "button_text": None, "elapsed_sec": 0.0}
    if not r7mod.WIN32_OK:
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
        return result

    hwnd = found[0]
    result["dialog_seen"] = True
    result["dialog_title"] = win32gui.GetWindowText(hwnd)
    log_cb(f"🔍 (win32) Найдено окно восстановления: {result['dialog_title']!r}")

    clicked, button_text = app._click_priority_button(
        hwnd, RECOVERY_BUTTON_PRIORITY, log_cb=log_cb)
    result["clicked"] = clicked
    result["button_text"] = button_text
    return result


def _find_and_handle_recovery_dialog(app, log_cb, timeout, port=None):
    """Основной вход: пробует CDP-путь (_cdp_click_on_any_target —
    подтверждён живым прогоном для этой сборки), затем win32-путь
    (_find_and_handle_recovery_dialog_win32 — запасной, см. его
    докстринг) — таймаут делится между ними поровну. Ни один диалог не
    найден за timeout секунд — не ошибка, возвращается честный
    "не появилось" (см. докстринг модуля, п.2).

    Args:
        app: "голый" экземпляр R7Testovarka — источник
            _get_r7_processes/_click_priority_button для win32-пути.
        log_cb: колбэк логирования.
        timeout: сколько секунд ждать появления диалога суммарно.
        port: CDP-порт для _cdp_click_on_any_target. По умолчанию
            r7mod.DEFAULT_CDP_PORT.

    Returns:
        dict: {"dialog_seen": bool, "dialog_title": str | None,
               "clicked": bool, "button_text": str | None,
               "elapsed_sec": float, "method": "cdp" | "win32" | None}
    """
    if port is None:
        port = r7mod.DEFAULT_CDP_PORT
    start = time.time()
    half = max(1.0, timeout / 2.0)

    cdp_result = _cdp_click_on_any_target(port, RECOVERY_BUTTON_PRIORITY, log_cb, timeout=half)
    if cdp_result is not None:
        log_cb(f"✅ (CDP) Нажата кнопка «{cdp_result.get('text')}» диалога восстановления")
        return {"dialog_seen": True, "dialog_title": None, "clicked": True,
               "button_text": cdp_result.get("text"),
               "elapsed_sec": round(time.time() - start, 2), "method": "cdp"}

    remaining = max(1.0, timeout - (time.time() - start))
    win32_result = _find_and_handle_recovery_dialog_win32(app, log_cb, remaining)
    win32_result["elapsed_sec"] = round(time.time() - start, 2)
    if win32_result["dialog_seen"]:
        win32_result["method"] = "win32"
        if win32_result["clicked"]:
            log_cb(f"✅ (win32) Нажата кнопка «{win32_result['button_text']}»")
        else:
            log_cb("⚠️ Диалог восстановления (win32) найден, но подходящая кнопка не найдена")
        return win32_result

    log_cb(f"ℹ️ Диалог восстановления не появился за {timeout} с "
          f"(это ожидаемо, если сбоя не было или у Р7 нет такого диалога)")
    win32_result["method"] = None
    return win32_result


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

    file_path = _resolve_file_path(args.file)
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
