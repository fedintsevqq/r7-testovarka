"""Прогон редактора презентаций (.pptx) тем же воркером, что и таблицы.

Этап 5, пункт 1 плана «до 20», часть «презентации». Устроено как режим
«документ» (r7/doc_run.py): _presentation_worker ставит
`_run_editor = "presentation"` и зовёт _spreadsheet_worker. Общую часть
нетабличных редакторов — готовность с досчётом, конец операции по пингу,
снимок и откат истории через Undo, подтверждение, автосохранение, ключ
`editor` в отчёте — даёт DocumentRunMixin; здесь только то, чем презентация
отличается:

  * профиль JS (r7/pptx_js.py): снимок со слайдами, темой и переходами,
    подготовка — первый слайд текущий;
  * тестовый файл — фикстура презентации (r7/pptx_fixtures.py), нет —
    создаётся;
  * маркер готовности — кнопка «Добавить слайд» вместо «Жирного»: тот
    без текста под курсором недоступен (r7/pptx_js.py);
  * операции — r7_pptx_ops.PresentationOps и их проверки.

Признака идущего пересчёта у презентации не найдено (recalcBusy = null),
поэтому «досчитано» — число слайдов не меняется DOC_LAYOUT_STABLE_POLLS
опроса подряд, а конец операции — тишина пинга редактора. Подробности и
живые проверки — docs/presentation-ops.md.
"""
from pathlib import Path

from r7 import config, pptx_js
from r7.doc_run import EditorProfile, _history_moved
from r7.editors import EDITOR_PRESENTATION, EDITOR_SPREADSHEET
from r7.pptx_fixtures import PPTX_FIXTURE_NAME, find_pptx_fixture, generate_pptx
from r7_pptx_ops import DEFAULT_PPTX_RUNS, PRESENTATION_TEST_DEFINITIONS, PresentationOps

PRESENTATION_PROFILE = EditorProfile(
    what="презентации",
    api_hint="editor с WordControl и Slides",
    state_js=pptx_js.PPTX_STATE_JS,
    prepare_js=pptx_js.PPTX_FIRST_SLIDE_JS,
    prepare_fail="Первый слайд не выбран",
    api_info_js=pptx_js.pptx_api_info_js,
    undo_to_js=pptx_js.undo_to_js,
    suspend_autosave_js=pptx_js.PPTX_SUSPEND_AUTOSAVE_JS,
    restore_autosave_js=pptx_js.restore_autosave_js,
    describe=lambda st: f"слайдов {st.get('slides')}",
)


def _slides(before, after):
    b, a = (before or {}).get("slides"), (after or {}).get("slides")
    return (b, a) if isinstance(b, int) and isinstance(a, int) else (None, None)


def check_slides_added(count):
    """Фабрика проверки «Добавления N слайдов»: история сдвинулась и слайдов
    стало ровно на N больше."""
    def _check(before, after):
        moved, detail = _history_moved(before, after)
        if not moved:
            return (False, detail)
        b, a = _slides(before, after)
        if b is None:
            return (False, f"{detail}; число слайдов прочитать не удалось")
        if a - b == count:
            return (True, f"{detail}; слайдов было {b}, стало {a}")
        return (False, f"{detail}; слайдов было {b}, стало {a} — ожидалось +{count}")
    return _check


def check_slides_doubled(before, after):
    """«Дублирование всех слайдов»: история сдвинулась, слайдов стало вдвое
    больше (продублирован не один текущий, а все выделенные)."""
    moved, detail = _history_moved(before, after)
    if not moved:
        return (False, detail)
    b, a = _slides(before, after)
    if b is None:
        return (False, f"{detail}; число слайдов прочитать не удалось")
    if a == 2 * b:
        return (True, f"{detail}; слайдов было {b}, стало {a}")
    return (False, f"{detail}; слайдов было {b}, стало {a} — ожидалось {2 * b}")


def check_theme_changed(before, after):
    """«Смена темы»: история сдвинулась; если имя темы читается до и после —
    оно другое."""
    moved, detail = _history_moved(before, after)
    if not moved:
        return (False, detail)
    b, a = (before or {}).get("theme"), (after or {}).get("theme")
    if b is not None and a is not None:
        if a == b:
            return (False, f"{detail}; тема осталась «{a}»")
        return (True, f"{detail}; тема «{b}» → «{a}»")
    return (True, f"{detail}; имя темы не читается")


def check_transitions_all(before, after):
    """«Переход ко всем слайдам»: история сдвинулась; если переходы слайдов
    читаются — переход есть у каждого слайда."""
    moved, detail = _history_moved(before, after)
    if not moved:
        return (False, detail)
    a = after or {}
    n, slides = a.get("transitions"), a.get("slides")
    if isinstance(n, int) and isinstance(slides, int):
        if n == slides:
            return (True, f"{detail}; переход у всех {slides} слайдов")
        return (False, f"{detail}; переход у {n} слайдов из {slides}")
    return (True, f"{detail}; переходы слайдов не читаются")


class PresentationRunMixin:
    """Режим «презентация» — часть R7Testovarka, в MRO перед DocumentRunMixin."""

    def _is_presentation_run(self):
        return self._run_editor == EDITOR_PRESENTATION

    def _editor_profile(self):
        if self._is_presentation_run():
            return PRESENTATION_PROFILE
        return super()._editor_profile()

    @classmethod
    def editor_test_names(cls):
        """Допустимые имена тестов по редактору — для наборов (r7/suites.py)."""
        names = super().editor_test_names()
        names[EDITOR_PRESENTATION] = list(PRESENTATION_TEST_DEFINITIONS)
        return names

    def _presentation_worker(self, enabled_tests=None, test_runs=None, stop_event=None):
        """Прогон презентации: тот же _spreadsheet_worker в режиме «presentation».

        Args:
            enabled_tests: имена из PRESENTATION_TEST_DEFINITIONS; None — все.
            test_runs: имя → повторов; None — DEFAULT_PPTX_RUNS.
            stop_event: как у _spreadsheet_worker.
        """
        if enabled_tests is None:
            enabled_tests = set(PRESENTATION_TEST_DEFINITIONS)
        if test_runs is None:
            test_runs = dict(DEFAULT_PPTX_RUNS)
        self._run_editor = EDITOR_PRESENTATION
        try:
            return self._spreadsheet_worker(enabled_tests, test_runs, stop_event)
        finally:
            self._run_editor = EDITOR_SPREADSHEET

    def _make_run_ops(self, find_hwnd, log_cb, test_file):
        if self._is_presentation_run():
            return PresentationOps(self, find_hwnd, log_cb, test_file)
        return super()._make_run_ops(find_hwnd, log_cb, test_file)

    # ── тестовый файл ─────────────────────────────────────────────────────

    def _locate_test_file(self):
        if not self._is_presentation_run():
            return super()._locate_test_file()
        found = find_pptx_fixture([self.test_files_folder, config.BASE_DIR, Path.cwd()])
        if found is not None:
            return found
        path = Path(self.test_files_folder) / PPTX_FIXTURE_NAME
        try:
            generate_pptx(path)
        except Exception as e:  # нет прав на папку, диск полон — прогон не начнётся
            self.add_test_log(f"❌ Не удалось создать тестовую презентацию {path}: {e}")
            return None
        self.add_test_log(f"✅ Создана тестовая презентация: {path}")
        return path

    # ── маркер готовности ─────────────────────────────────────────────────

    def _bold_ready_probe(self):
        """У презентации — проба кнопки «Добавить слайд» (r7/pptx_js.py);
        ответ того же вида, что у «Жирного», детектор готовности не меняется."""
        if not self._is_presentation_run():
            return super()._bold_ready_probe()
        connector = self._early_connector()
        if connector is None:
            return None
        try:
            return connector.evaluate(pptx_js.PPTX_READY_PROBE_JS,
                                      timeout=self.BOLD_PROBE_TIMEOUT_SEC)
        except Exception:
            return None

    # ── операции через api презентации ────────────────────────────────────

    def _pptx_op(self, label, caption, js, checker, log_cb):
        return self._cdp_sequence(
            label, [(caption, lambda c, t: c.evaluate(js, timeout=t),
                     self.CDP_LONG_OP_TIMEOUT_SEC, 0)],
            checker, log_cb)

    def _pptx_cdp_add_slides(self, count, log_cb=None):
        return self._pptx_op(f"Добавление {count} слайдов", f"AddSlide×{count}",
                             pptx_js.add_slides_js(count), check_slides_added(count), log_cb)

    def _pptx_cdp_duplicate_all(self, log_cb=None):
        return self._pptx_op("Дублирование всех слайдов", "DublicateSlide",
                             pptx_js.duplicate_all_js(), check_slides_doubled, log_cb)

    def _pptx_cdp_change_theme(self, candidates, log_cb=None):
        return self._pptx_op("Смена темы", "ChangeTheme",
                             pptx_js.change_theme_js(candidates), check_theme_changed, log_cb)

    def _pptx_cdp_transition_all(self, duration_ms, log_cb=None):
        return self._pptx_op("Переход ко всем слайдам", "ApplySlideTransition",
                             pptx_js.apply_transition_all_js(duration_ms),
                             check_transitions_all, log_cb)
