"""Ручной прогон CDP-операций на ЖИВОМ Р7-Офис.

ЗАЧЕМ. Остальные тесты в этой папке коннектор мокают и живой Р7 не трогают —
они проверяют логику Python (какой статус получила операция, можно ли после
неё повторить действие клавишами). А вот сами `asc_*`-вызовы проверить без
запущенного редактора нельзя: имена методов и формат ответов зависят от
сборки. Этот скрипт запускает Р7 с CDP-флагом, выполняет все переведённые на
api операции и печатает, что подтвердилось.

Именно так был пойман единственный настоящий баг перевода: проверка Ctrl+A
ждала выделение вида «A1:XFD1048576», а живая сборка отдаёт «1:1048576» —
диапазон строк без букв столбцов (см. _cdp_check_whole_sheet_selected).

ПОЧЕМУ НЕ pytest-ТЕСТ. Имя файла намеренно не начинается с `test_`: pytest его
не собирает. Запуск открывает окно редактора, занимает порт 8080 и длится
около минуты — такому не место в автоматическом прогоне.

ЗАПУСК:
    .venv/Scripts/python.exe tests/manual_cdp_smoke.py                # test_50k.xlsx
    .venv/Scripts/python.exe tests/manual_cdp_smoke.py test_10k.xlsx
    .venv/Scripts/python.exe tests/manual_cdp_smoke.py C:/путь/свой.xlsx

Код возврата 0 — все операции ушли через CDP; 1 — хотя бы одна откатилась бы
на клавиши (в реальном прогоне это и произошло бы, здесь клавиши не шлются).

ЧТО СКРИПТ НЕ ДЕЛАЕТ:
* Не шлёт клавиши. Вызываются только методы `_cdp_*`; клавиатурный запасной
  путь тест-функций сюда не подключён — иначе при неудачном CDP нажатия
  улетели бы в то окно, которое окажется в фокусе, а оператор скрипта в этот
  момент занят чем-то своим.
* Не сохраняет файл. Р7 закрывается через `_close_r7_gracefully`, который
  жмёт «Не сохранять»; тестовый .xlsx остаётся нетронутым.
* Не воспроизводит воркеры дословно. Замер здесь — упрощённая копия
  `run_test_with_runs`: один прогон вместо трёх, без замеров RAM/CPU и без
  отчётов. Цифры отсюда годятся, чтобы увидеть порядок величины и поймать
  «операция не выполнилась», но не для сравнения версий.

ПЕРЕД ЗАПУСКОМ закройте Р7-Офис: к уже работающему процессу CDP-порт задним
числом не подключить (см. docstring r7_webdriver_connector.py), а открытие
файла уйдёт в существующее окно — скрипт этого не дождётся.
"""
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Эмодзи в выводе требуют UTF-8: консоль Windows по умолчанию cp1251/cp866 и
# роняет print ещё до первой строки лога (та же причина, что и в
# r7_Testovarka.py сразу после импортов).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import r7_Testovarka as r7mod  # noqa: E402

DEFAULT_FILE = "test_50k.xlsx"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def make_app():
    """«Голый» R7Testovarka без Tk — как фикстура bare_r7 в conftest.py.

    Атрибуты выставляются те же, что делает __init__, плюс подмена
    add_test_log на печать в консоль: методы _cdp_* по умолчанию логируют
    именно через него.
    """
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
    app._cached_cpu_count = None  # см. R7Testovarka._cpu_count (этап 1, M6) —
                                  # bare-инстанс не проходит через __init__,
                                  # где это выставляется в норме
    app.add_test_log = log
    return app


def find_hwnd_factory(stem):
    """Функция поиска окна Р7 по куску имени файла в заголовке.

    Передаётся в _wait_until_r7_ready и _wait_operation_done именно как
    функция, а не готовый дескриптор: устаревший hwnd тогда перерешивается.
    """
    import win32gui

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


def warn_if_r7_running(app):
    """Предупреждает о запущенном Р7: к нему CDP уже не подключить."""
    procs = app._get_r7_processes(log_cb=log)
    if procs:
        pids = ", ".join(str(p.pid) for p in procs)
        log(f"⚠️ Р7-Офис уже запущен (PID: {pids}). Файл откроется в существующем "
            f"окне, стартованном БЕЗ --ascdesktop-support-debug-info, и CDP не "
            f"поднимется. Закройте редактор и повторите.")
        return True
    return False


def run_op(app, title, fn):
    """Выполняет одну операцию и меряет её как воркер: func() + ожидание
    простоя Р7, минус собственные паузы (_paced_total).

    Печатает ДВЕ длительности рядом: settle_ms (elapsed, детектор простоя —
    то, что видел пользователь до этой правки) и api_ms (субмиллисекундное
    время внутри рендерера, см. docstring _cdp_sequence в r7_Testovarka.py).
    Разница между ними на коротких операциях и есть та самая бимодальность
    из отчёта по нагрузочному тестированию (25.08.2026): settle_ms прыгает
    между «поймали момент занятости CPU» и «below_floor» до 20× между
    прогонами одного файла, а api_ms — нет, он не зависит от опроса CPU.

    Returns:
        tuple[bool, float, str]: ушла ли операция через CDP, длительность
        (settle_ms), статус _wait_operation_done.
    """
    app._paced_total = 0.0
    app._op_start_grace = None
    app._op_max_wait = None
    app._op_via_cdp = False
    app._cdp_api_ms = 0.0
    log(f"⏳ {title}")
    t0 = time.time()
    went_cdp = fn()
    done_ts, status = app._wait_operation_done(app._find_hwnd, log_cb=log)
    if status == "timeout":
        elapsed = time.time() - t0 - app._paced_total
    else:
        elapsed = max(0.0, done_ts - t0 - app._paced_total)
    app._flush_pending_cdp_verify(log_cb=log)
    mark = {
        "below_floor": (" (api-вызов, Р7 не стал занятым)" if app._op_via_cdp
                        else " (ниже порога измерения)"),
        "timeout": " (Р7 не освободился)",
    }.get(status, "")
    api_note = f" [api: {app._cdp_api_ms:.2f} мс]" if app._op_via_cdp else ""
    verdict = "через CDP" if went_cdp else "❌ CDP не сработал (клавиши НЕ шлю)"
    log(f"   ⏱ {title}: {elapsed:.3f} сек{api_note}{mark} — {verdict}")
    time.sleep(0.5)
    return went_cdp, elapsed, status


def build_ops(app):
    """Операции в том же порядке, в каком их гоняет _spreadsheet_worker.

    Не перечислены `ВПР`, `Удаление столбца` и `Сохранение в PDF` — они на
    CDP не переводились и идут клавишами, то есть проверять здесь нечего.
    """
    return [
        ("Ctrl+A",              lambda: app._cdp_select_all(log_cb=log)),
        ("Ctrl+C",              lambda: app._cdp_copy(log_cb=log)),
        ("Ctrl+V (новый лист)", lambda: app._cdp_paste_big(log_cb=log)),
        ("Новый лист",          lambda: app._cdp_add_sheet(log_cb=log)),
        ("Столбец",             lambda: app._cdp_add_column(log_cb=log)),
        ("Вставка 1 (буфер)",   lambda: app._cdp_copy_paste(1, 10, log_cb=log)),
        ("Вставка 5 (буфер)",   lambda: app._cdp_copy_paste(5, 15, log_cb=log)),
        ("Вставка 1 (сдвиг)",   lambda: app._cdp_copy_paste(1, 10, shift="down",
                                                            log_cb=log)),
        ("Вставка 5 (сдвиг)",   lambda: app._cdp_copy_paste(5, 15, shift="down",
                                                            log_cb=log)),
    ]


def main(argv):
    raw = argv[1] if len(argv) > 1 else DEFAULT_FILE
    test_file = Path(raw)
    if not test_file.is_absolute():
        test_file = BASE_DIR / raw
    if not test_file.exists():
        log(f"❌ Файл не найден: {test_file}")
        return 2

    app = make_app()
    r7_path = app._find_r7_path()
    if not r7_path:
        log("❌ Р7-Офис не найден")
        return 2
    log(f"Р7-Офис: {r7_path}")
    log(f"Файл: {test_file} ({test_file.stat().st_size / 1024:.0f} КБ)")
    if warn_if_r7_running(app):
        return 2

    debug_args = app._prepare_webdriver_launch(log_cb=log)
    log(f"Аргументы запуска: {debug_args}, порт {app._current_webdriver_port}")

    open_start = time.time()
    subprocess.Popen([r7_path, str(test_file), *debug_args])
    app._find_hwnd = find_hwnd_factory(test_file.stem[:12])

    deadline = time.time() + 60
    hwnd = None
    while time.time() < deadline:
        hwnd = app._find_hwnd()
        if hwnd:
            break
        time.sleep(0.3)
    if not hwnd:
        log("❌ Окно Р7 не появилось за 60 сек")
        app._terminate_r7_processes(log_cb=log)
        return 2
    log(f"Окно найдено: {hwnd}")

    failed = []
    try:
        ready = app._wait_until_r7_ready(app._find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Файл открыт за {time.time() - open_start:.2f} сек "
            f"({'данные загружены' if ready else 'таймаут'})")

        # Порядок как в воркерах: соединение → базовый снимок → диагностика api.
        app._cdp_ensure_connected(log_cb=log)
        app._capture_cdp_ui_baseline(log_cb=log)
        base = app._cdp_ui_baseline
        log(f"Базовый DOM-снимок: "
            f"{len(base) if base is not None else 'не снят'} элементов")
        app._cdp_log_api_info(log_cb=log)

        results = []
        for title, fn in build_ops(app):
            went_cdp, elapsed, status = run_op(app, title, fn)
            # run_op сбрасывает _cdp_api_ms перед следующей операцией, а не
            # после этой — значение ещё то самое, что накопилось за только
            # что выполненный вызов.
            api_ms = app._cdp_api_ms if app._op_via_cdp else None
            results.append((title, went_cdp, elapsed, status, api_ms))
            if not went_cdp:
                failed.append(title)

        connector = app._webdriver_connector
        if connector is not None and connector.connected:
            log(f"Итоговое состояние документа: {connector.document_state(timeout=5)}")

        log("─" * 62)
        log("ИТОГ:")
        log(f"   {'':1s} {'операция':22s} {'settle':>8s}  {'api':>9s}  статус")
        for title, went_cdp, elapsed, status, api_ms in results:
            api_col = f"{api_ms:7.2f} мс" if api_ms is not None else "        —"
            log(f"   {'✅' if went_cdp else '❌'} {title:22s} "
                f"{elapsed:7.3f} с  {api_col}  [{status}]")
        if failed:
            log(f"❌ Через CDP не прошли: {', '.join(failed)}")
        else:
            log("✅ Все операции выполнены через api редактора")
    finally:
        log("🔚 Закрытие Р7-Офис (без сохранения)...")
        try:
            app._close_r7_gracefully(app._find_hwnd(), log_cb=log, timeout=15)
        except Exception as e:
            log(f"⚠️ Закрытие не удалось ({type(e).__name__}: {e}) — завершаю процессы")
            app._terminate_r7_processes(log_cb=log)
        app._close_webdriver_connector()
        app._cleanup_x2t_temp_pdfs(log_cb=log)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
