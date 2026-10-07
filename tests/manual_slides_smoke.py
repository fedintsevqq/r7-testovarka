"""Ручной прогон операций ПРЕЗЕНТАЦИИ (.pptx) на ЖИВОМ Р7-Офис — этап 5.

Как tests/manual_doc_smoke.py, но для редактора презентаций: запускает Р7 с
CDP-флагом на фикстуре презентации, ждёт окно, затем готовность (кнопка
«Добавить слайд» и устоявшееся число слайдов), печатает, что нашлось в api
(методы, число тем), состояние маркера готовности, и выполняет каждую правку
из r7_pptx_ops по одному разу: подготовка → операция → конец по пингу →
проверка → откат истории. Состояние до, после и после отката показывает,
верны ли допущения docs/presentation-ops.md (список тем и ChangeTheme,
ApplySlideTransition на всех выделенных слайдах, timing.TransitionType,
DublicateSlide после SelectAllSlides).

Клавиш не шлёт, экспорт не проверяет (он идёт общим «Сохранить как» — его
видно в `python -m r7 run --suite suites/slides.toml`). Р7 закрывается без
сохранения. Имя файла не начинается с test_ — pytest его не собирает.

ЗАПУСК (Р7 должен быть закрыт):
    .venv/Scripts/python.exe tests/manual_slides_smoke.py           # TestFiles/r7-test-slides-50.pptx
    .venv/Scripts/python.exe tests/manual_slides_smoke.py C:/путь/своя.pptx

Код возврата 0 — все правки прошли через api и подтвердились; 1 — нет.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manual_cdp_smoke as base  # noqa: E402  (UTF-8 консоли, make_app, поиск окна)

import r7_pptx_ops  # noqa: E402
from r7 import pptx_js  # noqa: E402
from r7.editors import EDITOR_PRESENTATION  # noqa: E402
from r7.pptx_fixtures import PPTX_FIXTURE_NAME, generate_pptx, pptx_stats  # noqa: E402

log = base.log
WINDOW_WAIT_SEC = 60


def ops(app):
    """Правки презентации через api — без клавиатурных путей и экспорта."""
    return [
        (r7_pptx_ops.ADD_SLIDES_TEST, lambda: app._pptx_cdp_add_slides(
            r7_pptx_ops.SLIDES_TO_ADD, log_cb=log)),
        (r7_pptx_ops.DUPLICATE_TEST, lambda: app._pptx_cdp_duplicate_all(log_cb=log)),
        (r7_pptx_ops.THEME_TEST, lambda: app._pptx_cdp_change_theme(
            r7_pptx_ops.THEME_CANDIDATES, log_cb=log)),
        (r7_pptx_ops.TRANSITION_TEST, lambda: app._pptx_cdp_transition_all(
            r7_pptx_ops.TRANSITION_DURATION_MS, log_cb=log)),
    ]


def main(argv):
    test_file = (Path(argv[1]) if len(argv) > 1
                 else base.BASE_DIR / "TestFiles" / PPTX_FIXTURE_NAME)
    if not test_file.exists():
        generate_pptx(test_file)
        log(f"Создана тестовая презентация: {test_file}")
    log(f"Файл: {test_file} ({test_file.stat().st_size / 1024:.0f} КБ), "
        f"состав: {pptx_stats(test_file)}")
    app = base.make_app()
    app._run_editor = EDITOR_PRESENTATION
    app._autosave_state = None
    app._restore_unavailable_logged = False
    r7_path = app._find_r7_path()
    if not r7_path:
        log("❌ Р7-Офис не найден")
        return 2
    if base.warn_if_r7_running(app):
        return 2
    debug_args = app._prepare_webdriver_launch(log_cb=log, filename_hint=test_file.name)
    start = time.perf_counter()
    subprocess.Popen([r7_path, str(test_file), *debug_args])
    app._find_hwnd = base.find_hwnd_factory(test_file.stem[:12])
    # Готовность — только после окна презентации: сразу после Popen есть лишь
    # лаунчер DesktopEditors.exe, он тут же завершается (тот же баг был в
    # пробном скрипте документа).
    deadline = time.perf_counter() + WINDOW_WAIT_SEC
    while time.perf_counter() < deadline and not app._find_hwnd():
        time.sleep(0.3)
    failed = []
    try:
        ready = app._wait_until_r7_ready(app._find_hwnd, timeout=120, log_cb=log)
        log(f"✅ Готовность: {ready}, маркер {app._ready_marker}, "
            f"открытие {(app._ready_at or time.perf_counter()) - start:.2f} с")
        app._cdp_ensure_connected(log_cb=log)
        log(f"   маркер «Добавить слайд»: {app._bold_ready_probe()}")
        app._cdp_log_api_info(log_cb=log)
        app._suspend_autosave(log_cb=log)
        for title, fn in ops(app):
            app._editor_prepare(log_cb=log)
            before = app._history_snapshot()
            log(f"   до: {app._doc_state()}")
            app._op_unverified = None
            went, elapsed, status = base.run_op(app, title, fn)
            log(f"   после: {app._doc_state()}")
            if not went or app._op_unverified:
                failed.append(title)
                log(f"   ❌ {title}: через CDP {went}, не подтверждено: {app._op_unverified}")
            restored = app._restore_history(before, title, app._find_hwnd, log_cb=log)
            log(f"   откат: {restored}, состояние {app._doc_state()}")
        log(f"Сырой снимок для разбора: {app._doc_eval(pptx_js.PPTX_STATE_JS)}")
        log("❌ Не прошли: " + ", ".join(failed) if failed else "✅ Все правки через api")
    finally:
        log("🔚 Закрытие Р7-Офис (без сохранения)...")
        try:
            app._restore_autosave(log_cb=log)
            app._close_r7_gracefully(app._find_hwnd(), log_cb=log, timeout=15)
        except Exception as e:
            log(f"⚠️ Закрытие не удалось ({type(e).__name__}: {e}) — завершаю процессы")
            app._terminate_r7_processes(log_cb=log)
        app._close_webdriver_connector()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
