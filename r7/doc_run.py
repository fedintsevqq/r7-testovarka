"""Прогон текстового редактора (документы .docx) тем же воркером, что и таблицы.

Этап 5, пункт 1 плана «до 20», часть «документы». Воркер вкладки
(_spreadsheet_worker) не копируется: _document_worker ставит режим
`_run_editor = "document"` и зовёт его, а в тех местах, где таблицы и
документы расходятся, эта примесь (она стоит в R7Testovarka первой)
перехватывает методы и для документа делает своё, для таблиц — super():

  * тестовый файл — фикстура документа (r7/doc_fixtures.py), нет — создаётся;
  * готовность — общий детектор (кнопка «Жирный» есть и в редакторе
    документов) плюс досчитанная вёрстка: FullRecalc без таймера либо число
    страниц не меняется два опроса подряд (_doc_layout_settle);
  * конец операции на CDP-пути — досчитанная вёрстка, потом пинг редактора:
    большой документ верстается порциями по таймеру, и в паузах между ними
    пинг отвечает быстро (_wait_renderer_idle);
  * снимок и откат истории, подтверждение результата, автосохранение,
    опрос api — через пролог документа (r7/doc_js.py);
  * операции — r7_doc_ops.DocumentOps; в отчёте — "editor": "document".

Общая часть (состояние через CDP, вёрстка и готовность, конец операции,
снимок и откат, подтверждение, автосохранение, отчёт) годится для любого
нетабличного редактора: JS берётся из профиля редактора (`_editor_profile`).
Презентации подключают свой профиль, фикстуру и операции в r7/pptx_run.py
(PresentationRunMixin стоит перед этой примесью).

Подробности и что проверить на живом Р7 — docs/document-ops.md,
docs/presentation-ops.md.
"""
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from r7 import config, doc_js
from r7.doc_fixtures import DOC_FIXTURE_NAME, find_doc_fixture, generate_docx
from r7.editors import EDITOR_DOCUMENT, EDITOR_PRESENTATION, EDITOR_SPREADSHEET, EDITORS
from r7_doc_ops import DEFAULT_DOC_RUNS, DOCUMENT_TEST_DEFINITIONS, DocumentOps

__all__ = ["EDITOR_DOCUMENT", "EDITOR_PRESENTATION", "EDITOR_SPREADSHEET", "EDITORS",
           "DOCUMENT_PROFILE", "DocumentRunMixin", "EditorProfile", "check_pages_added"]


@dataclass(frozen=True)
class EditorProfile:
    """JS и подписи журнала одного нетабличного редактора.

    Attributes:
        what: родительный падеж для журнала («документа», «презентации»).
        api_hint: как ищется api — для сообщения «api не найден».
        state_js: снимок состояния (docState) — dict или null.
        prepare_js: подготовка повтора вне замера → {ok, state}.
        prepare_fail: что не удалось при подготовке (для журнала).
        api_info_js: () → JS диагностики api.
        undo_to_js: (индекс, шагов) → JS отката.
        suspend_autosave_js: JS отключения автосохранения.
        restore_autosave_js: (состояние) → JS возврата автосохранения.
        describe: снимок → «страниц 100, блоков 600» для журнала.
        api_prelude: пролог с findApi/docState (r7/doc_js.py, r7/pptx_js.py) —
            им же взводятся и снимаются метрики интерфейса (r7/ux_metrics.py):
            метка __uxMark ищет api тем же прологом внутри _op_js.
    """
    what: str
    api_hint: str
    state_js: str
    prepare_js: str
    prepare_fail: str
    api_info_js: Callable[[], str]
    undo_to_js: Callable[[int, int], str]
    suspend_autosave_js: str
    restore_autosave_js: Callable[[dict], str]
    describe: Callable[[dict], str]
    api_prelude: str


DOCUMENT_PROFILE = EditorProfile(
    what="документа",
    api_hint="editor с WordControl",
    state_js=doc_js.DOC_STATE_JS,
    prepare_js=doc_js.DOC_CURSOR_START_JS,
    prepare_fail="Курсор в начало документа не поставлен",
    api_info_js=doc_js.doc_api_info_js,
    undo_to_js=doc_js.undo_to_js,
    suspend_autosave_js=doc_js.DOC_SUSPEND_AUTOSAVE_JS,
    restore_autosave_js=doc_js.restore_autosave_js,
    describe=lambda st: f"страниц {st.get('pages')}, блоков {st.get('blocks')}",
    api_prelude=doc_js.DOC_API_PRELUDE,
)


def check_pages_added(count):
    """Фабрика проверки «Вставки N страниц»: история правок сдвинулась и
    страниц стало больше хотя бы на половину N (сколько страниц даёт один
    asc_AddBlankPage, решает вёрстка; см. docs/document-ops.md). Сразу после
    вызова вёрстка ещё идёт — тогда проверка откладывается до конца замера."""
    need = max(1, int(count) // 2)

    def _check(before, after):
        moved, detail = _history_moved(before, after)
        if not moved:
            return (False, detail)
        b, a = (before or {}).get("pages"), (after or {}).get("pages")
        if not isinstance(b, int) or not isinstance(a, int):
            return (False, f"{detail}; число страниц прочитать не удалось")
        if a - b >= need:
            return (True, f"{detail}; страниц было {b}, стало {a}")
        return (False, f"{detail}; страниц было {b}, стало {a} — ожидалось не меньше +{need}")
    return _check


def _history_moved(before, after):
    """История правок документа сдвинулась (как CdpMixin._cdp_check_document_changed)."""
    b, a = (before or {}), (after or {})
    for key, human in (("historyIndex", "позиция в истории"),
                       ("historyPoints", "число точек истории")):
        bv, av = b.get(key), a.get(key)
        if isinstance(bv, int) and isinstance(av, int) and av != bv:
            return (True, f"{human}: {bv} → {av}")
    if not b.get("canUndo") and a.get("canUndo"):
        return (True, "появилась возможность отменить правку")
    if b.get("historyIndex") is None and b.get("historyPoints") is None:
        return (False, "историю правок прочитать не удалось")
    return (False, "история правок не сдвинулась")


class DocumentRunMixin:
    """Режим «документ» поверх общего воркера — часть R7Testovarka. Заодно
    общая часть нетабличных редакторов: с профилем презентации она же
    работает для презентаций (r7/pptx_run.py)."""

    _run_editor = EDITOR_SPREADSHEET

    # Вёрстка документа (готовность и конец операции).
    # Шаг опроса вёрстки. Конец операции — момент первого чтения итогового
    # состояния, поэтому шаг сидит в замере: 0.15 с завышали «Смену стиля»
    # (~0.7 с) в среднем на 0.07 с (проба 08.10.2026). 0.05 — как у пинга.
    DOC_LAYOUT_POLL_SEC = 0.05
    # Столько чтений подряд с тем же числом страниц и без FullRecalc — и при
    # recalcBusy False тоже: одно чтение между порциями вёрстки не решает.
    DOC_LAYOUT_STABLE_POLLS = 2
    DOC_READY_LAYOUT_TIMEOUT_SEC = 60.0   # после готовности по кнопке — ждать вёрстку не дольше
    DOC_UNDO_MAX_STEPS = 400          # откат «Вставки 100 страниц» — до сотни точек истории

    # ── режим ─────────────────────────────────────────────────────────────

    def _is_document_run(self):
        return self._run_editor == EDITOR_DOCUMENT

    def _is_editor_run(self):
        """Прогон нетабличного редактора (документ, презентация)."""
        return self._run_editor != EDITOR_SPREADSHEET

    def _editor_profile(self):
        """JS и подписи текущего редактора; презентация подменяет профиль в
        r7/pptx_run.py."""
        return DOCUMENT_PROFILE

    def _ux_prelude(self):
        """Метрики интерфейса (r7/ux_metrics.py) взводятся и снимаются тем же
        прологом, которым операция редактора ставит __uxMark."""
        if self._is_editor_run():
            return self._editor_profile().api_prelude
        return super()._ux_prelude()

    @classmethod
    def editor_test_names(cls):
        """Допустимые имена тестов по редактору — для наборов (r7/suites.py)."""
        return {EDITOR_SPREADSHEET: list(getattr(cls, "TEST_DEFINITIONS", [])),
                EDITOR_DOCUMENT: list(DOCUMENT_TEST_DEFINITIONS)}

    def _document_worker(self, enabled_tests=None, test_runs=None, stop_event=None):
        """Прогон документа: тот же _spreadsheet_worker в режиме «document».

        Args:
            enabled_tests: имена из DOCUMENT_TEST_DEFINITIONS; None — все.
            test_runs: имя → повторов; None — DEFAULT_DOC_RUNS.
            stop_event: как у _spreadsheet_worker.
        """
        if enabled_tests is None:
            enabled_tests = set(DOCUMENT_TEST_DEFINITIONS)
        if test_runs is None:
            test_runs = dict(DEFAULT_DOC_RUNS)
        self._run_editor = EDITOR_DOCUMENT
        try:
            return self._spreadsheet_worker(enabled_tests, test_runs, stop_event)
        finally:
            self._run_editor = EDITOR_SPREADSHEET

    def _make_run_ops(self, find_hwnd, log_cb, test_file):
        if self._is_document_run():
            return DocumentOps(self, find_hwnd, log_cb, test_file)
        return super()._make_run_ops(find_hwnd, log_cb, test_file)

    # ── тестовый файл ─────────────────────────────────────────────────────

    def _locate_test_file(self):
        if not self._is_document_run():
            return super()._locate_test_file()
        return self._locate_doc_test_file()

    def _locate_doc_test_file(self):
        """Фикстура документа в TestFiles, рядом с программой или в текущей
        папке; нет — генерируется в TestFiles (секунда, детерминированно)."""
        found = find_doc_fixture([self.test_files_folder, config.BASE_DIR, Path.cwd()])
        if found is not None:
            return found
        path = Path(self.test_files_folder) / DOC_FIXTURE_NAME
        try:
            generate_docx(path)
        except Exception as e:  # нет прав на папку, диск полон — прогон не начнётся
            self.add_test_log(f"❌ Не удалось создать тестовый документ {path}: {e}")
            return None
        self.add_test_log(f"✅ Создан тестовый документ: {path}")
        return path

    # ── состояние документа через CDP ─────────────────────────────────────

    def _doc_eval(self, js, timeout=None):
        """evaluate через коннектор операций; None — CDP нет или сбой."""
        connector = self._cdp_ops_connector()
        if connector is None:
            return None
        try:
            return connector.evaluate(js, timeout=timeout or self.CDP_OP_TIMEOUT_SEC)
        except Exception:
            return None

    def _doc_state(self, timeout=None):
        st = self._doc_eval(self._editor_profile().state_js, timeout)
        return st if isinstance(st, dict) else None

    def _doc_layout_settle(self, timeout, log_cb=None):
        """Ждёт, пока вёрстка документа досчитается.

        Признак — DOC_LAYOUT_STABLE_POLLS чтений подряд без FullRecalc
        (recalcBusy не True) с тем же числом страниц. Поле FullRecalc не
        читается (None) — только число страниц. Опрос — вне замера.

        Returns:
            dict | None: None — состояние не прочитать (нет CDP, api не найден).
            Иначе {"settled_at": момент первого чтения итогового состояния
            (perf_counter) или None по таймауту, "first_read": итоговое
            состояние было уже на первом чтении, "state": последний снимок}.
        """
        deadline = time.perf_counter() + timeout
        first_at = None
        last_pages, stable, seen_at, state = object(), 0, None, None
        while True:
            st = self._doc_state()
            t1 = time.perf_counter()
            if st is None:
                return None if state is None else {"settled_at": None, "first_read": False,
                                                   "state": state}
            state = st
            if first_at is None:
                first_at = t1
            busy, pages = st.get("recalcBusy"), st.get("pages")
            if busy is True:
                stable, last_pages, seen_at = 0, object(), None
            elif pages != last_pages:
                last_pages, stable, seen_at = pages, 1, t1
            else:
                stable += 1
            if busy is not True and stable >= self.DOC_LAYOUT_STABLE_POLLS:
                return {"settled_at": seen_at, "first_read": seen_at == first_at,
                        "state": st}
            if t1 >= deadline:
                return {"settled_at": None, "first_read": False, "state": st}
            time.sleep(self.DOC_LAYOUT_POLL_SEC)

    # ── готовность после открытия ─────────────────────────────────────────

    def _wait_until_r7_ready(self, hwnd, timeout=120, log_cb=None):
        ok = super()._wait_until_r7_ready(hwnd, timeout=timeout, log_cb=log_cb)
        if not (ok and self._is_editor_run()):
            return ok
        if log_cb is None:
            log_cb = self.add_test_log
        prof = self._editor_profile()
        self._cdp_ensure_connected(log_cb)
        res = self._doc_layout_settle(self.DOC_READY_LAYOUT_TIMEOUT_SEC, log_cb)
        marker = self._ready_marker
        if res is None:
            log_cb(f"   ⚠️ Вёрстку {prof.what} проверить нечем (нет CDP или api {prof.what}) — "
                   "готовность только по общему детектору")
            return ok
        st = res["state"] or {}
        if res["settled_at"] is None:
            self._ready_at = time.perf_counter()
            self._ready_marker = f"{marker}+layout_timeout"
            log_cb(f"   ⚠️ Вёрстка {prof.what} не досчиталась за "
                   f"{self.DOC_READY_LAYOUT_TIMEOUT_SEC:.0f} с ({prof.describe(st)}) — "
                   f"открытие — верхняя оценка")
            return ok
        if res["first_read"]:
            log_cb(f"   📄 Вёрстка {prof.what} готова: {prof.describe(st)}")
            return ok
        if self._ready_at is None or res["settled_at"] > self._ready_at:
            self._ready_at = res["settled_at"]
            self._ready_marker = f"{marker}+layout"
        log_cb(f"   📄 Вёрстка {prof.what} досчиталась позже общего признака готовности: "
               f"{prof.describe(st)} — момент готовности сдвинут")
        return ok

    # ── конец операции ────────────────────────────────────────────────────

    def _wait_renderer_idle(self, log_cb=None):
        """Конец операции нетабличного редактора: досчёт вёрстки, потом пинг.

        Вёрстка идёт порциями по таймеру, каждая короче OP_PING_FAST_SEC, и
        пинг её не видит. Поэтому сначала — опрос вёрстки сразу после вызова
        api: «Вставка 100 страниц» досчитывается ещё ~0.15 с после возврата
        (проба 08.10.2026), и при опросе после тишины пинга этот хвост в
        замер не попадал. Потом пинг — хвост работы после последней порции.
        """
        if not self._is_editor_run():
            return super()._wait_renderer_idle(log_cb)
        called_at = time.perf_counter()     # возврат вызова api
        log = log_cb or self.add_test_log
        max_wait = getattr(self, "_op_max_wait", None) or self.OP_MAX_WAIT_SEC
        settle = self._doc_layout_settle(max_wait, log_cb)
        if settle is None:
            # Снимок не прочитался: досчёт вёрстки не виден, конец — по пингу.
            # Молча это давало «Смене стиля» 0.47 с вместо 0.77 (08.10.2026).
            log(f"   ⚠️ Состояние {self._editor_profile().what} не прочитать — "
                f"конец операции по пингу, без досчёта вёрстки")
            return super()._wait_renderer_idle(log_cb)
        if settle["settled_at"] is None:
            log(f"   ⚠️ Вёрстка {self._editor_profile().what} не досчиталась за "
                f"{max_wait:.0f} с")
            return None, "timeout"
        # Вёрстка досчитана уже к первому чтению — её конец не позже возврата.
        layout_end = called_at if settle["first_read"] else settle["settled_at"]
        ping_start = time.perf_counter()
        res = super()._wait_renderer_idle(log_cb)
        if res is None:
            return layout_end, "ok"
        if res[1] != "ok":
            return res
        # Пинг без медленных ответов возвращает момент своего начала — это
        # время подтверждения вёрстки, а не работа Р7.
        ping_end = res[0] if res[0] - ping_start > self.OP_PING_FAST_SEC else layout_end
        return max(ping_end, layout_end), "ok"

    # ── подготовка тестов ─────────────────────────────────────────────────

    def _doc_prepare(self, log_cb=None):
        """Перед повтором, вне замера: вёрстка досчитана, курсор в начале
        документа, выделения нет. Нет CDP — ничего (операция сама честно
        упадёт)."""
        self._editor_prepare(log_cb)

    def _editor_prepare(self, log_cb=None):
        """Подготовка повтора нетабличного редактора, вне замера: вёрстка
        досчитана, затем prepare_js профиля (документ — курсор в начало,
        презентация — текущий первый слайд)."""
        if log_cb is None:
            log_cb = self.add_test_log
        if self._cdp_ops_connector() is None:
            return
        prof = self._editor_profile()
        self._doc_layout_settle(self.OP_MAX_WAIT_SEC, log_cb)
        res = self._doc_eval(prof.prepare_js)
        if not (isinstance(res, dict) and res.get("ok")):
            if not getattr(self, "_doc_cursor_warned", False):
                self._doc_cursor_warned = True
                log_cb(f"   ⚠️ {prof.prepare_fail} (api не ответил) — "
                       "операции пойдут от текущей позиции")

    # ── операции через api документа ──────────────────────────────────────

    def _doc_cdp_add_pages(self, count, log_cb=None):
        return self._cdp_sequence(
            f"Вставка {count} страниц",
            [(f"asc_AddBlankPage×{count}",
              lambda c, t: c.evaluate(doc_js.add_blank_pages_js(count), timeout=t),
              self.CDP_LONG_OP_TIMEOUT_SEC, 0)],
            check_pages_added(count), log_cb)

    def _doc_cdp_restyle_all(self, names, log_cb=None):
        return self._cdp_sequence(
            "Смена стиля документа",
            [("put_Style", lambda c, t: c.evaluate(doc_js.restyle_all_js(names), timeout=t),
              self.CDP_LONG_OP_TIMEOUT_SEC, 0)],
            _history_moved, log_cb)

    def _doc_cdp_replace_all(self, find_text, replace_with, log_cb=None):
        return self._cdp_sequence(
            "Поиск и замена",
            [("asc_replaceText",
              lambda c, t: c.evaluate(doc_js.replace_all_js(find_text, replace_with), timeout=t),
              self.CDP_LONG_OP_TIMEOUT_SEC, 0)],
            _history_moved, log_cb)

    # ── снимок и откат истории ────────────────────────────────────────────

    def _history_snapshot(self, log_cb=None):
        if not self._is_editor_run():
            return super()._history_snapshot(log_cb)
        st = self._doc_state()
        idx = (st or {}).get("historyIndex")
        if not isinstance(idx, int):
            return None
        return {"index": idx, "pages": st.get("pages"), "blocks": st.get("blocks")}

    def _restore_history(self, before, label, hwnd=None, log_cb=None):
        if not self._is_editor_run():
            return super()._restore_history(before, label, hwnd, log_cb=log_cb)
        if log_cb is None:
            log_cb = self.add_test_log
        if before is None:
            if not getattr(self, "_restore_unavailable_logged", False):
                self._restore_unavailable_logged = True
                log_cb("   ⚠️ Откат правок между прогонами недоступен (нет CDP): "
                       "прогоны работают с накопленными изменениями "
                       f"{self._editor_profile().what}, "
                       "цифры повторов зависимы")
            return None
        if self._cdp_ops_connector() is None:
            return False
        cur = (self._doc_state() or {}).get("historyIndex")
        if not (isinstance(cur, int) and cur <= before["index"]):
            res = self._doc_eval(self._editor_profile().undo_to_js(
                before["index"], self.DOC_UNDO_MAX_STEPS), timeout=self.OP_MAX_WAIT_SEC)
            if not (isinstance(res, dict) and res.get("reached")):
                log_cb(f"   ⚠️ {label}: правки не отменены до исходного состояния "
                       f"(ответ: {res}) — следующие замеры идут на изменённом файле")
                return False
            log_cb(f"   ↩️ {label}: отменено шагов {res.get('steps')} "
                   f"({res.get('undo_ms', 0):.0f} мс, вне замера)")
        # Отмена большой правки — перевёрстка; она не должна попасть в
        # следующий замер.
        self._doc_layout_settle(self.OP_MAX_WAIT_SEC, log_cb)
        self._wait_operation_done(hwnd, log_cb=log_cb, start_grace=0.3)
        return True

    def _flush_pending_cdp_verify(self, log_cb=None):
        if not self._is_editor_run():
            return super()._flush_pending_cdp_verify(log_cb)
        pending = getattr(self, "_pending_cdp_verify", None)
        if not pending:
            return
        self._pending_cdp_verify = None
        if log_cb is None:
            log_cb = self.add_test_log
        label, before, checker = pending
        after = self._doc_state()
        if after is None:
            log_cb(f"   ⚠️ CDP-проверка «{label}»: состояние "
                   f"{self._editor_profile().what} прочитать не удалось")
            self._op_unverified = f"проверка «{label}»: состояние не прочитано"
            return
        ok, detail = checker(before, after)
        if ok:
            log_cb(f"   ✅ CDP-проверка «{label}»: {detail}")
        else:
            log_cb(f"   ⚠️ CDP-проверка «{label}»: не подтверждено — {detail}")
            self._op_unverified = f"проверка «{label}»: {detail}"

    # ── сессия CDP: api и автосохранение ──────────────────────────────────

    def _cdp_log_api_info(self, log_cb=None):
        if not self._is_editor_run():
            return super()._cdp_log_api_info(log_cb)
        if log_cb is None:
            log_cb = self.add_test_log
        prof = self._editor_profile()
        self._cdp_ensure_connected(log_cb)
        if self._cdp_ops_connector() is None:
            log_cb(f"🧩 CDP-операции недоступны в этом запуске — правки {prof.what} "
                   "не пойдут (клавишами они не повторяются)")
            return
        info = self._doc_eval(prof.api_info_js())
        if not isinstance(info, dict) or not info.get("found"):
            log_cb(f"⚠️ CDP: api {prof.what} ({prof.api_hint}) не найден — "
                   f"правки {prof.what} не пойдут")
            return
        st = info.get("state") or {}
        themes = f", тем в редакторе {info['themes']}" if info.get("themes") is not None else ""
        log_cb(f"🧩 CDP: api {prof.what} найден (iframe глубины {info.get('frame')}), "
               f"{prof.describe(st)}, история {st.get('historyIndex')}{themes}")
        missing = sorted(n for n, present in (info.get("methods") or {}).items()
                         if not present)
        if missing:
            log_cb(f"   ℹ️ у api нет методов: {', '.join(missing)}")

    def _suspend_autosave(self, log_cb=None):
        if not self._is_editor_run():
            return super()._suspend_autosave(log_cb)
        if log_cb is None:
            log_cb = self.add_test_log
        self._autosave_state = None
        state = self._doc_eval(self._editor_profile().suspend_autosave_js)
        if not isinstance(state, dict):
            log_cb("   ⚠️ Автосохранение Р7 не отключено (нет CDP или api не ответил) — "
                   "его запись может попасть в замеры")
            return
        self._autosave_state = state
        log_cb(f"💾 Автосохранение Р7 отключено на время замеров "
               f"(было: правки раз в {(state.get('gap_ms') or 0) / 1000:.0f} с, "
               f"периодическое {'вкл' if state.get('periodic') else 'выкл'})")

    def _restore_autosave(self, log_cb=None):
        if not self._is_editor_run():
            return super()._restore_autosave(log_cb)
        if log_cb is None:
            log_cb = self.add_test_log
        state = getattr(self, "_autosave_state", None)
        if not state:
            return
        self._autosave_state = None
        if self._doc_eval(self._editor_profile().restore_autosave_js(state)):
            time.sleep(self.AUTOSAVE_RESTORE_FLUSH_SEC)   # см. CdpMixin._restore_autosave
            log_cb("💾 Автосохранение Р7 возвращено")
        elif state.get("periodic"):
            log_cb("❌ Не удалось вернуть периодическое автосохранение Р7 — включите "
                   "его вручную: Файл → Дополнительные параметры")

    # ── отчёт ─────────────────────────────────────────────────────────────

    def _build_full_report(self, ts, version, test_file, results, summary):
        """Полный JSON + "editor" (какой редактор мерили). Ключ новый и
        необязательный: читатели без него считают отчёт табличным, схема не
        поднимается (правило 11)."""
        data = super()._build_full_report(ts, version, test_file, results, summary)
        data["editor"] = self._run_editor
        return data
