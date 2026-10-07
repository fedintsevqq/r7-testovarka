"""Восстановление после сбоя: правки, диалог «Обнаружен файл блокировки…»,
проверка восстановленного, уборка следов и отчёт — общий код CLI
run_crash_recovery.py и вкладки «Сценарии» (r7/ui/scenarios_tab.py).

Сам сценарий (правки → kill → перезапуск → переподключение) —
r7.scenarios.run_crash_recovery_scenario; здесь то, что он сознательно не
знает: какими CDP-операциями править документ данного типа, как найти и
нажать диалог восстановления, что считать «восстановилось», как убрать
lock-файлы и запись в recover после прогона.

Модуль не импортирует tkinter и r7_Testovarka: экземпляр приложения (или
«голый» R7Testovarka из CLI) приходит параметром app — от него нужны
_get_r7_processes, _click_priority_button, _close_update_dialog_if_exists.

Что здесь СВЕРХ run_crash_recovery_scenario и почему:

1. Правки (edits) и проверка восстановления (verify_recovered) подбираются
   по расширению файла — только теми CDP-операциями, что реально
   существуют для этого типа документа. Для Cell (.xlsx) прямой операции
   «напечатать текст в ячейку» через CDP нет вовсе — сетка рисуется на
   <canvas>, а не в DOM, — поэтому правки для xlsx структурные (add_sheet),
   не текстовые. Для Word — insert_text + set_bold, для Slide — add_slide.

2. Поиск диалога восстановления (_find_and_handle_recovery_dialog).
   ОБНОВЛЕНО ЖИВЫМ ПРОГОНОМ 06.10.2026 (Р7 2026.3.2): диалог — нативное
   окно Qt процесса editors.exe, принадлежащее окну документа, а не
   HTML-оверлей. Текст и кнопки — те же, что ниже, но достаются только
   через UI Automation (_find_and_handle_recovery_dialog_uia, основной
   путь). Пока диалог открыт, в CDP /json есть только стартовая страница.
   Нажатие «Продолжить редактирование» открывает файл с диска: правки,
   сделанные до сбоя, НЕ возвращаются (1 лист из 4, история пуста).
   Исходные наблюдения 25.08.2026: текст «Обнаружен файл блокировки,
   оставшийся после аварийного завершения работы», кнопки «Продолжить
   редактирование» / «Только чтение» / «Отмена»; тогда диалог выглядел
   HTML-оверлеем внутри CEF, и клик шёл через _cdp_click_on_any_target
   (первая доступная CDP-цель без фильтра по типу документа). CDP- и
   win32-пути оставлены запасными — вдруг другая сборка рисует иначе.
   Если диалог не появился за timeout секунд, это не ошибка (сбоя не было
   или у Р7 нет такого диалога) — фиксируется честно, как и
   process_died_cleanly=False в самом сценарии.

   НАЙДЕНО ПОПУТНО: диалог возникает не только после настоящего сбоя —
   Р7 сопоставляет его по ИМЕНИ ФАЙЛА нестрого: старая осиротевшая запись
   в "%LOCALAPPDATA%/R7-Office/Editors/data/recover/" для файла
   "X.xlsx.xlsx" даёт этот же диалог при открытии "X.xlsx". Если сценарий
   сообщает про диалог там, где его не ждали, — сначала проверить эту
   папку на чужие записи (_cleanup_crash_leftovers убирает только свои).

3. verify_recovered — эвристика, не точное измерение: сетка/текст
   документа не читаются из DOM (тот же Canvas), поэтому проверяется число
   структурных единиц (листов/слайдов) или позиция в истории правок — см.
   _build_verify_recovered.
"""
import json
import os
import shutil
import time
import unicodedata
from pathlib import Path

from r7 import env, scenarios
from r7_webdriver_connector import _click_by_text_js

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
# диалогу «При открытии файла произошла ошибка» — там его безопасно искать
# ТОЛЬКО когда точно известно, что этот диалог на экране.
# Кнопки диалога, которые есть, но кликать НЕ надо — для документации и на
# случай параметра «открыть только для чтения» в будущем.
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
    окна ОС НЕТ — он рисуется внутри окна документа (win32gui.EnumWindows
    его не находит). Метод оставлен на случай другой сборки/версии Р7,
    которая нарисует его отдельным окном.

    Returns:
        dict: {"dialog_seen": bool, "dialog_title": str | None,
               "clicked": bool, "button_text": str | None,
               "elapsed_sec": float}
    """
    result = {"dialog_seen": False, "dialog_title": None,
              "clicked": False, "button_text": None, "elapsed_sec": 0.0}
    if not env.WIN32_OK:
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


def _uia_dialog_controls(hwnd):
    """Тексты и кнопки окна через UI Automation: у Qt-диалога нет дочерних
    окон Win32, BM_CLICK и GetWindowText до них не достают.

    Returns:
        tuple[list[str], dict]: тексты всех элементов и кнопки
        {текст в нижнем регистре: обёртка pywinauto}.
    """
    from pywinauto import Desktop
    root = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
    texts, buttons = [], {}
    for el in [root] + root.descendants():
        text = el.window_text() or ""
        if text:
            texts.append(text)
        if el.element_info.control_type == "Button" and text:
            buttons[text.strip().lower()] = el
    return texts, buttons


def _dialog_closed(hwnd, timeout=3.0, poll_sec=0.1):
    """True, если окно исчезло или скрылось за timeout: invoke() без
    исключения ещё не значит, что кнопка сработала."""
    import win32gui
    deadline = time.time() + timeout
    while True:
        try:
            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return True
        except Exception:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(poll_sec)


def _find_and_handle_recovery_dialog_uia(app, log_cb, timeout, poll_sec=0.5):
    """Основной путь для 2026.3.2: диалог «Обнаружен файл блокировки…» —
    нативное окно Qt процесса editors.exe, принадлежащее окну документа
    (живой прогон 06.10.2026, дамп UIA). Заголовок у него общий
    («Р7-Офис. Профессиональный…»), фраза — в тексте внутри, поэтому
    win32-поиск по заголовку его не видит, а CDP — тем более: пока диалог
    открыт, в /json есть только стартовая страница.

    Кандидаты — видимые окна процессов Р7 с окном-владельцем (диалоги);
    само окно документа владельца не имеет. Кнопка выбирается по ТОЧНОМУ
    тексту из RECOVERY_BUTTON_PRIORITY: подстрочный поиск однажды нажал
    «Локальные файлы» вместо нужной кнопки (см. выше).

    Returns:
        dict: как у _find_and_handle_recovery_dialog_win32.
    """
    result = {"dialog_seen": False, "dialog_title": None,
              "clicked": False, "button_text": None, "elapsed_sec": 0.0}
    if not (env.WIN32_OK and env.PYWINAUTO_OK):
        return result

    import win32con
    import win32gui
    import win32process

    def _candidates():
        pids = {p.pid for p in app._get_r7_processes(log_cb=lambda _m: None, fresh=True)}
        out = []

        def _enum(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                if not win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                    return
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid in pids:
                    out.append(hwnd)
            except Exception:
                pass            # окно исчезло между EnumWindows и запросом — не кандидат

        win32gui.EnumWindows(_enum, None)
        return out

    start = time.time()
    deadline = start + timeout
    reported = set()
    while True:
        for hwnd in _candidates():
            try:
                texts, buttons = _uia_dialog_controls(hwnd)
            except Exception as e:
                # Сломанный UIA выглядел бы как «диалог не появился».
                key = (hwnd, type(e).__name__)
                if key not in reported:
                    reported.add(key)
                    log_cb(f"⚠️ (UIA) Окно {hwnd} не прочитано: {type(e).__name__}: {e}")
                continue
            body = " ".join(texts).lower()
            if not any(s in body for s in RECOVERY_DIALOG_TITLES):
                continue
            result["dialog_seen"] = True
            result["dialog_title"] = next(
                (t for t in texts if any(s in t.lower() for s in RECOVERY_DIALOG_TITLES)), None)
            log_cb(f"🔍 (UIA) Найден диалог восстановления: {result['dialog_title']!r}")
            for wanted in RECOVERY_BUTTON_PRIORITY:
                btn = buttons.get(wanted)
                if btn is None:
                    continue
                # Текст — до нажатия: у закрывшегося диалога элемента уже нет.
                button_text = btn.window_text()
                try:
                    btn.invoke()
                except Exception as e:
                    log_cb(f"⚠️ (UIA) Кнопка «{wanted}» не нажалась: {type(e).__name__}: {e}")
                    break
                if not _dialog_closed(hwnd):
                    log_cb(f"⚠️ (UIA) После «{wanted}» диалог не закрылся")
                    break
                result["clicked"] = True
                result["button_text"] = button_text
                break
            result["elapsed_sec"] = round(time.time() - start, 2)
            return result
        if time.time() >= deadline:
            break
        time.sleep(poll_sec)
    result["elapsed_sec"] = round(time.time() - start, 2)
    return result


def _find_and_handle_recovery_dialog(app, log_cb, timeout, port=None):
    """Основной вход: сначала UIA-путь (_find_and_handle_recovery_dialog_uia,
    нативный диалог 2026.3.2, половина таймаута), затем CDP-путь
    (_cdp_click_on_any_target — HTML-оверлей, как описано 25.08.2026) и
    win32-путь по заголовку — остаток таймаута между ними поровну. Ни один
    диалог не найден за timeout секунд — не ошибка, возвращается честный
    «не появилось» (см. докстринг модуля, п. 2).

    Args:
        app: экземпляр R7Testovarka («голый» в CLI) — источник
            _get_r7_processes/_click_priority_button для win32-пути.
        log_cb: колбэк логирования.
        timeout: сколько секунд ждать появления диалога суммарно.
        port: CDP-порт для _cdp_click_on_any_target. По умолчанию
            env.DEFAULT_CDP_PORT.

    Returns:
        dict: {"dialog_seen": bool, "dialog_title": str | None,
               "clicked": bool, "button_text": str | None,
               "elapsed_sec": float, "method": "uia" | "cdp" | "win32" | None}
    """
    if port is None:
        port = env.DEFAULT_CDP_PORT
    start = time.time()

    # Нативный Qt-диалог (подтверждён 06.10.2026) — половина таймаута.
    uia_result = _find_and_handle_recovery_dialog_uia(app, log_cb, max(1.0, timeout / 2.0))
    if uia_result["dialog_seen"]:
        uia_result["method"] = "uia"
        if uia_result["clicked"]:
            log_cb(f"✅ (UIA) Нажата кнопка «{uia_result['button_text']}»")
        else:
            log_cb("⚠️ Диалог восстановления (UIA) найден, но подходящая кнопка не найдена")
        return uia_result

    half = max(1.0, (timeout - (time.time() - start)) / 2.0)
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


# ── Правки и проверка восстановления ─────────────────────────────────────

def _build_edits(file_path, n):
    """Правки перед «сбоем» — только реально существующими CDP-операциями
    для типа документа (см. докстринг модуля, п. 1)."""
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


def _structure_units(conn, suffix):
    """Число листов (Cell) или слайдов (Slide); None — не прочитать."""
    state = conn.slide_state() if suffix in (".pptx", ".ppt") else conn.document_state()
    if not state:
        return None
    return state.get("slideCount" if suffix in (".pptx", ".ppt") else "sheets")


def _build_baseline_snapshot(file_path, baseline):
    """Снимок до правок для run_crash_recovery_scenario(before_edits=...):
    сколько листов/слайдов в файле изначально. Без него файл с тремя
    листами дал бы «восстановлено 2» вообще без восстановления."""
    suffix = file_path.suffix.lower()

    def snapshot(conn):
        if suffix in (".docx", ".doc"):
            return
        units = _structure_units(conn, suffix)
        if units is not None:
            baseline["units"] = units

    return snapshot


def _build_verify_recovered(file_path, expected_ops, baseline=None):
    """Проверка восстановления — эвристика по структурным единицам
    (листы/слайды) или позиции в истории правок, не по содержимому (см.
    докстринг модуля, п. 3): сетка/текст документа не читаются из DOM.

    baseline — словарь, который заполняет _build_baseline_snapshot; без
    него исходным считается одна единица (как было до 06.10.2026)."""
    suffix = file_path.suffix.lower()
    if baseline is None:
        baseline = {}

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
            return min(expected_ops, max(0, count - baseline.get("units", 1)))
        else:
            state = conn.document_state()
            if not state:
                return 0
            sheets = state.get("sheets") or 1
            return min(expected_ops, max(0, sheets - baseline.get("units", 1)))

    return verify


# ── Уборка следов и отчёт ────────────────────────────────────────────────

def _recover_dir():
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "R7-Office" / "Editors" / "data" / "recover" if base else None


def _cleanup_crash_leftovers(file_path, since_ts, log_cb, recover_dir=None):
    """Убирает следы «сбоя» этого прогона, иначе следующий запуск на том же
    файле покажет диалог восстановления от прошлого прогона, а не от своего.

    Р7 пишет два lock-файла рядом с документом (~$имя и .~lock.имя#, см.
    docs/history/stage-3-L1-L3.md) и папку DE_xxxx в recover. Папка
    удаляется, только если создана во время прогона и asc_name.info
    называет именно этот файл: остальные записи — документы пользователя.

    Returns:
        int: сколько объектов удалено.
    """
    removed = 0
    # «й» в имени фикстуры хранится в NFD, а Р7 может записать NFC — сравнивать
    # в одной форме.
    name = unicodedata.normalize("NFC", file_path.name)
    locks = {"~$" + name, f".~lock.{name}#"}
    try:
        neighbours = list(file_path.parent.iterdir())
    except OSError:
        neighbours = []
    for lock in neighbours:
        if unicodedata.normalize("NFC", lock.name) not in locks:
            continue
        try:
            # Старый lock — чужой: файл мог быть открыт до прогона.
            if lock.stat().st_mtime < since_ts - 1.0:
                continue
            lock.unlink()
            removed += 1
        except FileNotFoundError:
            pass                # Р7 сам убрал lock, пока мы шли по списку — цель достигнута
        except OSError as e:
            log_cb(f"⚠️ Не удалён {lock.name}: {e}")
    rec = recover_dir if recover_dir is not None else _recover_dir()
    if rec is not None and rec.is_dir():
        for entry in rec.iterdir():
            try:
                if not entry.is_dir() or entry.stat().st_mtime < since_ts - 1.0:
                    continue
                if not any(entry.iterdir()):
                    # Перезапущенный Р7 создаёт папку сразу, а пишет в неё
                    # позже; убитый на диалоге оставляет её пустой.
                    entry.rmdir()
                    removed += 1
                    log_cb(f"🧹 Удалена пустая запись восстановления {entry.name}")
                    continue
                info = (entry / "asc_name.info").read_text(encoding="utf-8-sig",
                                                          errors="replace")
            except OSError:
                continue
            if f'name="{name}"' not in unicodedata.normalize("NFC", info):
                continue
            shutil.rmtree(entry, ignore_errors=True)
            if entry.exists():
                log_cb(f"⚠️ Не удалена запись восстановления {entry.name}")
                continue
            removed += 1
            log_cb(f"🧹 Удалена запись восстановления {entry.name}")
    return removed


def verdict_ok(scenario_result):
    """Успех = процесс до сбоя дал подключиться, правки применились, он
    реально умер после kill(), переподключение после перезапуска прошло, и
    verify_recovered увидела хотя бы одну восстановленную правку. Любое из
    условий не выполнено — вердикт «Ошибка», а не частичный успех: цель
    сценария — ответить да/нет на вопрос «восстанавливается ли документ»."""
    return bool(
        scenario_result.get("connected_before_crash")
        and scenario_result.get("edits_applied")
        and scenario_result.get("process_died_cleanly")
        and scenario_result.get("connected_after_crash")
        and scenario_result.get("recovered_count") is not None
        and scenario_result.get("recovered_count") > 0
    )


def build_report(file_path, ops, timeout, scenario_result, dialog_result,
                 total_elapsed_sec, log_lines, leftover_r7_pids=()):
    """JSON-отчёт из результата сценария — отдельная функция, чтобы формат
    проверялся без живого Р7.

    leftover_r7_pids — процессы Р7, пережившие уборку в конце: прогон с
    ними — «Ошибка», следующий запуск упрётся в живой Р7."""
    ok = verdict_ok(scenario_result) and not leftover_r7_pids
    return {
        "file": str(file_path),
        "ops_requested": ops,
        "timeout_sec": timeout,
        "timestamp": time.strftime("%Y%m%d_%H%M%S"),
        "total_elapsed_sec": round(total_elapsed_sec, 2),
        "verdict": "Успешно" if ok else "Ошибка",
        "scenario": {k: v for k, v in scenario_result.items() if k != "proc"},
        "recovery_dialog": dialog_result,
        "leftover_r7_pids": sorted(leftover_r7_pids),
        "log": log_lines,
    }


def dialog_summary(dialog_result):
    """Одной фразой: «найден и нажат», «найден, кнопка не нажата», «не появился»."""
    if dialog_result.get("clicked"):
        return "найден и нажат"
    if dialog_result.get("dialog_seen"):
        return "найден, кнопка не нажата"
    return "не появился"


def run_recovery_check(app, r7_path, file_path, ops, timeout, log_cb, log_lines=None):
    """Полный прогон: правки → сбой → перезапуск → диалог восстановления →
    проверка → уборка → отчёт. Общий для CLI и вкладки «Сценарии».

    Р7 закрывается при любом исходе (правило 10 CLAUDE.md): лаунчер из Popen
    давно завершился, поэтому убивается весь Р7, запущенный за время прогона.
    Следы сбоя (lock-файлы, запись в recover) убираются только если Р7 не
    пережил уборку — живому процессу они ещё нужны.

    Returns:
        dict: {"report": dict | None (None — сценарий упал), "result":
        результат сценария | None, "error": str | None, "dialog": dict,
        "leftover_pids": [int], "elapsed_sec": float}.
    """
    log_lines = log_lines if log_lines is not None else []
    file_path = Path(file_path)
    edits = _build_edits(file_path, ops)
    baseline = {}
    snapshot = _build_baseline_snapshot(file_path, baseline)
    verify_recovered = _build_verify_recovered(file_path, ops, baseline)
    dialog_result = {}

    def after_relaunch(proc):
        # Диалог обновления не связан с crash-recovery, но может закрыть
        # собой окно восстановления — гасим его первым, если он всплыл.
        app._close_update_dialog_if_exists(log_cb=log_cb, search_timeout=2)
        dialog_result.update(_find_and_handle_recovery_dialog(app, log_cb, timeout))
        return dict(dialog_result)

    log_cb(f"🚀 Запускаю crash-recovery сценарий: {file_path}, {ops} правок")
    start = time.time()
    result, error = None, None
    try:
        result = scenarios.run_crash_recovery_scenario(
            r7_path, file_path, edits, verify_recovered,
            after_relaunch=after_relaunch, log_cb=log_cb, before_edits=snapshot,
        )
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        log_cb(f"❌ Сценарий упал с исключением: {error}")
    finally:
        _, leftover = scenarios._kill_r7_processes_since(start, log_cb=log_cb)
        if not leftover:
            _cleanup_crash_leftovers(file_path, start, log_cb)
        else:
            log_cb("⚠️ Р7 не закрылся — следы сбоя не удаляю")
    elapsed = time.time() - start
    leftover_pids = [p for p in (getattr(x, "pid", None) for x in leftover) if p is not None]
    report = None
    if result is not None:
        report = build_report(file_path, ops, timeout, result, dialog_result, elapsed,
                              list(log_lines), leftover_r7_pids=leftover_pids)
    return {"report": report, "result": result, "error": error, "dialog": dialog_result,
            "leftover_pids": leftover_pids, "elapsed_sec": elapsed}
