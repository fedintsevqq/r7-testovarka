"""Разовая диагностика: что именно всплывает после «Сохранить» в диалоге
«Сохранить как», из-за чего файл экспорта не появляется даже когда сам
диалог открылся (см. CLAUDE.md, L2, «продолжение №4», и PR #18) — прогон
пользователя сообщил «скрипт не реагирует на всплывающие окна».

`_dump_visible_window_titles` в r7_Testovarka.py ФИЛЬТРУЕТ окна с пустым
заголовком (`if t:`) — если блокирующий попап без текста в заголовке (как
у многих системных message box), его вообще не видно в существующем логе.
Этот скрипт дампит ВСЕ видимые top-level окна (заголовок МОЖЕТ быть
пустым) вместе с классом окна и владеющим процессом — раз в секунду, в
течение SCAN_SECONDS после отправки Enter.

Открывает файл СВЕЖИМ (без предварительных 12 CDP-тестов из полного
набора) — специально, чтобтобы не гадать, вызвано ли зависание раздутостью
документа: если попап появляется и на чистом файле, дело не в размере.

ЗАПУСК (закройте Р7-Офис перед стартом):
    .venv/Scripts/python.exe tests/manual_saveas_popup_probe.py
    .venv/Scripts/python.exe tests/manual_saveas_popup_probe.py ods
"""
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import subprocess           # noqa: E402
import r7_Testovarka as r7mod  # noqa: E402
import win32gui             # noqa: E402
import win32process         # noqa: E402
import psutil               # noqa: E402

DEFAULT_FILE = "test_50k.xlsx"
SCAN_SECONDS = 20


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def make_app():
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app._paced_total = 0.0
    app._pending_modal_confirm = False
    app._pending_cdp_verify = None
    app._op_via_cdp = False
    app._cdp_api_ms = 0.0
    app._op_start_grace = None
    app._op_max_wait = None
    app._webdriver_connector = None
    app._current_webdriver_port = None
    app._cdp_ui_baseline = None
    app._cdp_dump_seen = set()
    app._r7_pids = None
    app._x2t_logged_pids = set()
    app._cached_r7_path = None
    app._cached_cpu_count = None
    app.add_test_log = log
    return app


def find_hwnd_factory(stem):
    def _find():
        found = [None]

        def _cb(h, _):
            try:
                title = win32gui.GetWindowText(h)
            except Exception:
                return
            if stem in title or "Р7-Офис" in title or "R7-Office" in title:
                found[0] = h

        win32gui.EnumWindows(_cb, None)
        return found[0]

    return _find


def dump_all_windows(label):
    rows = []

    def _cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        try:
            title = win32gui.GetWindowText(h)
        except Exception:
            title = "<err>"
        try:
            cls = win32gui.GetClassName(h)
        except Exception:
            cls = "<err>"
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            pname = psutil.Process(pid).name()
        except Exception:
            pname = "?"
        rows.append((h, cls, pname, title))
    win32gui.EnumWindows(_cb, None)
    log(f"--- {label}: {len(rows)} видимых top-level окон ---")
    for h, cls, pname, title in rows:
        log(f"   hwnd={h:>10} class={cls:<28} proc={pname:<22} title={title!r}")


def main(argv):
    ext = argv[1] if len(argv) > 1 else "pdf"
    raw = argv[2] if len(argv) > 2 else DEFAULT_FILE
    test_file = Path(raw)
    if not test_file.is_absolute():
        test_file = BASE_DIR / raw
    if not test_file.exists() and test_file.parent.exists():
        import unicodedata
        target = unicodedata.normalize("NFC", test_file.name)
        for candidate in test_file.parent.iterdir():
            if unicodedata.normalize("NFC", candidate.name) == target:
                test_file = candidate
                break
    if not test_file.exists():
        log(f"❌ Файл не найден: {test_file}")
        return 2

    app = make_app()
    r7_path = app._find_r7_path()
    if not r7_path:
        log("❌ Р7-Офис не найден")
        return 2
    procs = app._get_r7_processes(log_cb=log)
    if procs:
        pids = ", ".join(str(p.pid) for p in procs)
        log(f"⚠️ Р7-Офис уже запущен (PID: {pids}). Закройте его и повторите.")
        return 2

    log(f"Р7-Офис: {r7_path}")
    log(f"Файл: {test_file}")
    log(f"Формат: .{ext}")

    debug_args = app._prepare_webdriver_launch(log_cb=log, filename_hint=test_file.name)
    find_hwnd = find_hwnd_factory(test_file.stem[:12])
    subprocess.Popen([r7_path, str(test_file), *debug_args])

    deadline = time.time() + 60
    hwnd = None
    while time.time() < deadline:
        hwnd = find_hwnd()
        if hwnd:
            break
        time.sleep(0.3)
    if not hwnd:
        log("❌ Окно Р7 не появилось за 60 сек")
        app._terminate_r7_processes(log_cb=log)
        return 2
    log(f"Окно найдено: {hwnd}")

    try:
        ready = app._wait_until_r7_ready(find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Файл открыт ({'данные загружены' if ready else 'таймаут ожидания'})")

        app._cdp_ensure_connected(log_cb=log)
        if not app._try_cdp_saveas(hwnd, log_cb=log):
            log("❌ CDP не открыл диалог «Сохранить как» — на чистом файле "
                "это отдельная проблема, диагностика попапа тут бессильна")
            return 2

        dlg_hwnd = app._find_window_hwnd("сохранить как", "save as")
        log(f"Диалог: hwnd={dlg_hwnd}")
        tmp_path = str(Path(__import__("os").environ.get("TEMP", ".")) /
                        f"popup_probe_{int(time.time())}.{ext}")
        log(f"Целевой путь: {tmp_path}")

        dump_all_windows("ПЕРЕД UIA-сохранением")

        if dlg_hwnd is None or not app._uia_select_saveas_type(dlg_hwnd, ext, tmp_path, log_cb=log):
            log("❌ UIA-сохранение не удалось (тип файла/имя/кнопка «Сохранить»)")
            return 2
        log("✅ UIA отработал (тип выбран, путь набран, «Сохранить» нажата)")

        # Зеркало save_as_format(): гасит диалог-предупреждение о потере
        # функций формата (CSV и т.п.), main_hwnd — регрессионный фикс
        # 27.08.2026 (см. docstring _dismiss_saveas_format_warning).
        dismissed = app._dismiss_saveas_format_warning(dlg_hwnd, main_hwnd=hwnd, timeout=3.0, log_cb=log)
        log(f"Диалог-предупреждение формата: {'найден и закрыт' if dismissed else 'не появился/не закрыт'}")

        log("Сканирую окна каждую секунду...")
        for i in range(SCAN_SECONDS):
            time.sleep(1.0)
            dump_all_windows(f"t+{i + 1}s")
            if Path(tmp_path).exists():
                log(f"✅ Файл экспорта появился: {tmp_path} "
                    f"({Path(tmp_path).stat().st_size} байт)")
                break
        else:
            log(f"❌ Файл экспорта НЕ появился за {SCAN_SECONDS} сек")

    finally:
        log("🔚 Закрытие Р7-Офис (без сохранения)...")
        try:
            h = find_hwnd()
            if h:
                app._close_r7_gracefully(h, log_cb=log, timeout=15)
        except Exception as e:
            log(f"⚠️ Закрытие не удалось ({type(e).__name__}: {e}) — завершаю процессы")
            app._terminate_r7_processes(log_cb=log)
        app._close_webdriver_connector()
        app._cleanup_x2t_temp_pdfs(log_cb=log)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
