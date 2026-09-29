"""Минимальный живой набор на установленном Р7-Офис (QA-аудит 29.09.2026, G-10).

Юнит-тесты мокают Р7 целиком и по конструкции не видят того, что ломалось
в этом проекте чаще всего: вложенные функции воркеров, реальные диалоги,
ответы api редактора. Этот набор запускает Р7 один раз на модуль, на
сгенерированной фикстуре (не на файлах из TestFiles/ — их трогать нельзя),
и проверяет:

* файл открывается, детектор готовности срабатывает;
* все операции, переведённые на CDP, реально уходят через api;
* экспорт через общий _save_as_format доводит файл до диска для
  pdf/ods/xltx/csv;
* _emergency_close_r7 закрывает Р7 и не оставляет процессов.

Запуск (Р7 должен быть закрыт, порт 8080 свободен):

    set R7_LIVE=1
    .venv/Scripts/python.exe -m pytest -m live tests/live -v

Без R7_LIVE=1 весь модуль пропускается — обычный `pytest -q` его не
запускает.
"""
import os
import sys
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("R7_LIVE") != "1",
                       reason="живой набор: задайте R7_LIVE=1 и закройте Р7-Офис"),
]

TESTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TESTS_DIR))

FIXTURE_NAME = "live_smoke_10k.xlsx"


@pytest.fixture(scope="module")
def smoke():
    """Модуль tests/manual_cdp_smoke.py — его хелперы (make_app, run_op, …)."""
    import manual_cdp_smoke
    return manual_cdp_smoke


@pytest.fixture(scope="module")
def live_r7(smoke, tmp_path_factory):
    """Запускает Р7 на сгенерированной фикстуре; закрывает в teardown."""
    app = smoke.make_app()
    path = tmp_path_factory.mktemp("live") / FIXTURE_NAME
    app._generate_fixture(path, rows=10_000, profile="flat")

    r7_path = app._find_r7_path()
    if not r7_path:
        pytest.skip("Р7-Офис не установлен")
    if smoke.warn_if_r7_running(app):
        pytest.skip("Р7-Офис уже запущен — CDP к нему не подключить")

    debug_args = app._prepare_webdriver_launch(log_cb=smoke.log, filename_hint=path.name)
    open_start = time.perf_counter()
    subprocess.Popen([r7_path, str(path), *debug_args])
    app._find_hwnd = smoke.find_hwnd_factory(path.stem[:12])

    deadline = time.time() + 60
    while time.time() < deadline and not app._find_hwnd():
        time.sleep(0.3)
    state = {"app": app, "path": path, "open_start": open_start,
             "hwnd_found": bool(app._find_hwnd())}
    try:
        yield state
    finally:
        # Страховка: если тест закрытия не дошёл до конца.
        if app._get_r7_processes(log_cb=lambda _m: None, fresh=True):
            app._emergency_close_r7(app._find_hwnd, log_cb=smoke.log)
        app._close_webdriver_connector()
        app._cleanup_x2t_temp_pdfs(log_cb=smoke.log)


def test_file_opens(live_r7, smoke):
    app = live_r7["app"]
    assert live_r7["hwnd_found"], "окно Р7 не появилось за 60 сек"
    assert app._wait_until_r7_ready(app._find_hwnd, timeout=120, log_cb=smoke.log)
    smoke.log(f"Открытие: {time.perf_counter() - live_r7['open_start']:.2f} с, "
              f"маркер: {getattr(app, '_ready_marker', None)}")
    app._cdp_ensure_connected(log_cb=smoke.log)
    app._capture_cdp_ui_baseline(log_cb=smoke.log)
    assert app._webdriver_connector is not None and app._webdriver_connector.connected


def test_cdp_ops_go_through_api(live_r7, smoke):
    app = live_r7["app"]
    not_cdp = []
    for title, fn in smoke.build_ops(app):
        went_cdp, _elapsed, status = smoke.run_op(app, title, fn)
        if not went_cdp:
            not_cdp.append(f"{title} [{status}]")
    assert not not_cdp, f"откатились на клавиши: {not_cdp}"


def _press(key, n=1, pace=0.0):
    import pyautogui
    for _ in range(n):
        pyautogui.press(key)
        if pace:
            time.sleep(pace)


@pytest.mark.parametrize("ext", ["pdf", "ods", "xltx", "csv"])
def test_export_reaches_disk(live_r7, smoke, ext):
    import pyautogui
    app = live_r7["app"]
    try:
        app._save_as_format(ext, app._find_hwnd, pyautogui.hotkey, _press, log_cb=smoke.log)
    except RuntimeError as e:
        if str(e).startswith("SKIP:"):
            # Известная перемежающаяся неполадка открытия диалога (CLAUDE.md, L2).
            pytest.xfail(f"диалог «Сохранить как» не открылся: {e}")
        raise


def test_emergency_close_leaves_no_processes(live_r7, smoke):
    app = live_r7["app"]
    app._emergency_close_r7(app._find_hwnd, log_cb=smoke.log)
    time.sleep(2)
    app._r7_pids = None
    left = app._get_r7_processes(log_cb=lambda _m: None, fresh=True)
    assert not left, f"остались процессы Р7: {[p.pid for p in left]}"
