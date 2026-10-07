"""Прогон корпуса реальных файлов: открытие, пересчёт, экспорт по каждому
файлу, Р7 закрывается после каждого (docs/corpus.md, план «до 20», этап 5, п. 2).

На файл — одна сессия Р7, как у вкладки «Производительность»:

  * открытие — те же _extra_opens + _launch_r7 (холодный старт: очистка
    временных объектов Р7, сброс файлового кэша ОС при правах
    администратора, удаление lock-файлов), готовность _wait_until_r7_ready,
    запись _open_result — медиана по повторам, первый не отбрасывается;
  * пересчёт — asc_calculate(c_oAscCalculateType.All) через CDP, цикл
    повторов _measure_op_repeated: подготовка и ожидание простоя вне замера,
    конец — пинг редактора. Пересчёт добавляет точку в историю правок (живая
    проба 07.10.2026), поэтому повторы откатываются, как у правок. Клавиш у
    шага нет: без api шаг честно падает, а не жмёт F9 вслепую;
  * экспорт — _save_as_format через SpreadsheetOps (файл в %TEMP%, удаляется
    _cleanup_x2t_temp_pdfs после файла).

Р7 открывает КОПИЮ файла во временной папке: исходник корпуса не получает
lock-файлов и не может быть перезаписан; копия удаляется после файла.
Ошибка на файле не останавливает корпус — она пишется в ячейку файла.
Р7 закрывается при любом исходе (finally: _emergency_close_r7).

Прогон корпуса исключает все прочие прогоны (RunState, вид CORPUS).
CorpusMixin — методы, которые R7Testovarka получает наследованием; модуль
не импортирует tkinter.
"""
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

from r7 import corpus
from r7.config import MEASURE_SCHEMA_VERSION
from r7.processes import X2tTracker
from r7.run_state import CORPUS
from r7.stand import power_plan_during_run
from r7.version import __version__
from r7.versions import version_label


class CorpusRunError(RuntimeError):
    """Корпус не начался: идёт другой прогон, Р7 не найден."""


class CorpusMixin:
    """Прогон корпуса файлов — часть R7Testovarka (через наследование)."""

    # Не «r7…»: _clear_r7_cache перед каждым холодным стартом удаляет из %TEMP%
    # всё по шаблону r7*, и рабочая копия исчезала до запуска — Р7 открывал
    # пустой «Документ1.docx» (живой прогон 07.10.2026).
    CORPUS_WORKDIR_PREFIX = "testovarka_corpus_"

    @power_plan_during_run
    def run_corpus(self, items, plan, corpus_dir=None, warnings=(), log_cb=None,
                   stop_event=None):
        """Прогон корпуса. Returns: dict отчёта (r7.corpus.build_report).

        Args:
            items: файлы с планами (r7.corpus.build_items).
            plan: общий план (в отчёт).
            corpus_dir: папка корпуса (в отчёт; --hide-names её уберёт).
            warnings: предупреждения разбора корпуса — в отчёт.

        Raises:
            CorpusRunError: идёт другой прогон или Р7 не найден.
        """
        log_cb = log_cb or self.add_test_log
        refusal = self.run_state.try_start(CORPUS)
        if refusal is not None:
            raise CorpusRunError(f"{refusal[0]}. {refusal[1]}")
        try:
            return self._corpus_session_all(items, plan, corpus_dir, warnings, log_cb,
                                            stop_event)
        finally:
            self.run_state.finish(CORPUS)

    def _corpus_session_all(self, items, plan, corpus_dir, warnings, log_cb, stop_event):
        # Окружение — до запуска Р7, пока он не грузит систему.
        self._run_environment = self._capture_environment(log_cb=log_cb)
        r7_path = self._find_r7_path()
        if not r7_path:
            raise CorpusRunError("Р7-Офис не найден — установите его или укажите путь в "
                                 "r7_settings.json (docs/first-run.md)")
        entries, stopped = [], False
        for k, item in enumerate(items, start=1):
            if stop_event is not None and stop_event.is_set():
                stopped = True
                log_cb(f"⏹ Корпус остановлен: файлов пройдено {k - 1}/{len(items)}")
                break
            log_cb(f"\n📁 [{k}/{len(items)}] {item.rel} ({item.file_id}, "
                   f"{item.size_bytes / 1024 / 1024:.1f} МБ): {', '.join(item.step_keys())}")
            entries.append(self._corpus_file(item, r7_path, log_cb, stop_event))
        return corpus.build_report(
            entries, plan, timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            measure_schema=MEASURE_SCHEMA_VERSION, tool_version=__version__,
            version=version_label(self.current_version_info), build=self._build_metadata(),
            system=self._build_system_info(),
            corpus_dir=str(corpus_dir) if corpus_dir else None,
            warnings=list(warnings), stopped=stopped)

    def _corpus_file(self, item, r7_path, log_cb, stop_event):
        """Один файл: рабочая копия, сессия Р7, уборка. Ошибка — в запись
        файла, не наверх: корпус идёт дальше."""
        started = time.perf_counter()
        steps, error = {}, None
        workdir = Path(tempfile.mkdtemp(prefix=self.CORPUS_WORKDIR_PREFIX))
        try:
            work = workdir / item.path.name
            shutil.copy2(item.path, work)
            error = self._corpus_session(item, work, r7_path, steps, log_cb, stop_event)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            log_cb(f"❌ {item.rel}: {error}")
        finally:
            try:
                self._cleanup_x2t_temp_pdfs(log_cb=log_cb)
            except Exception as e:  # уборка не должна терять запись файла
                log_cb(f"⚠️ Временные файлы экспорта не убраны: {e}")
            shutil.rmtree(workdir, ignore_errors=True)
            if workdir.exists():
                log_cb(f"⚠️ Рабочая копия не удалена: {workdir} — удалите вручную")
        return corpus.file_entry(item, steps, error, time.perf_counter() - started)

    def _corpus_session(self, item, work, r7_path, steps, log_cb, stop_event):
        """Сессия Р7 на рабочей копии: шаги плана по порядку, записи — в
        steps. Р7 закрывается в finally при любом исходе.
        Returns: ошибка файла (Р7 не открыл документ и т. п.) или None."""
        plan = item.plan

        def find_hwnd():
            return self._find_r7_window(work.stem)

        open_n = plan.open_runs if corpus.STEP_OPEN in plan.steps else 1
        upd_stop, closed = None, False
        try:
            # Лишние повторы открытия — до основного запуска, Р7 каждый раз
            # закрывается (_extra_opens); основной запуск остаётся для шагов.
            extra = self._extra_opens(r7_path, work, open_n, stop_event)
            launched = self._launch_r7(r7_path, work)
            if launched is None:
                return "окно Р7 не появилось — файл не открыт"
            upd_stop = self._start_update_monitor(log_cb)
            error = self._corpus_open_and_measure(item, work, launched, extra, find_hwnd,
                                                  steps, log_cb, stop_event)
            # Штатное закрытие — и когда документ не загрузился: «Не
            # сохранять» по тексту, иначе принудительно (_close_r7_gracefully).
            upd_stop.set()
            self._restore_autosave()
            log_cb("🔚 Закрытие Р7-Офис...")
            self._close_r7_gracefully(find_hwnd())
            closed = self._r7_gone()
            return error
        finally:
            if upd_stop is not None:
                upd_stop.set()
            self._restore_autosave()
            if not closed and not self._emergency_close_r7(find_hwnd):
                log_cb("❌ Р7-Офис не закрылся — закройте его вручную, иначе следующий "
                       "файл упрётся в занятый порт CDP")
            self._close_webdriver_connector()

    def _corpus_open_and_measure(self, item, work, launched, extra, find_hwnd, steps, log_cb,
                                 stop_event):
        """Готовность, запись открытия, затем пересчёт и экспорт.
        Returns: ошибка файла или None."""
        plan = item.plan
        open_start, window_ts, setup = launched
        data_ready = self._wait_until_r7_ready(find_hwnd, timeout=plan.open_timeout_sec)
        main = self._main_open_record(open_start, window_ts, setup, data_ready)
        if corpus.STEP_OPEN in plan.steps:
            opens = extra + [dict(main, x2t=X2tTracker.summarize(self._x2t_since(open_start)))]
            steps[corpus.STEP_OPEN] = self._open_result(opens, data_ready, None)
        if not data_ready:
            return (f"документ не загрузился за {plan.open_timeout_sec:.0f} с — пересчёт и "
                    f"экспорт не мерились (open_timeout_sec в {corpus.MANIFEST_NAME})")
        if not self._focus_r7_settled(work):
            return "окно Р7 недоступно после открытия файла"
        self._prepare_cdp_session()
        self._corpus_measure_steps(item, work, find_hwnd, steps, log_cb, stop_event)
        return None

    def _corpus_measure_steps(self, item, work, find_hwnd, steps, log_cb, stop_event):
        """Пересчёт и экспорт открытого файла — общий цикл повторов."""
        from r7_ops import SpreadsheetOps     # лениво, как в r7/bisect_runner.py
        plan = item.plan

        def focus():
            return self._focus_r7_settled(work)

        if corpus.STEP_RECALC in plan.steps and not self._corpus_stopped(stop_event):
            fn = self._corpus_recalc_op(log_cb)
            steps[corpus.STEP_RECALC] = self._measure_op_repeated(
                corpus.RECALC_OP_NAME, fn, plan.recalc_runs, find_hwnd, log_cb, stop_event,
                focus_cb=focus)
        if corpus.STEP_EXPORT not in plan.steps:
            return
        ops = SpreadsheetOps(self, find_hwnd, log_cb, work)
        for fmt in plan.formats:
            if self._corpus_stopped(stop_event):
                return
            steps[f"{corpus.STEP_EXPORT}:{fmt}"] = self._measure_op_repeated(
                corpus.export_op_name(fmt), lambda f=fmt: ops.save_as_format(f),
                plan.export_runs, find_hwnd, log_cb, stop_event, focus_cb=focus)

    @staticmethod
    def _corpus_stopped(stop_event):
        return stop_event is not None and stop_event.is_set()

    def _corpus_recalc_op(self, log_cb):
        """Тест-функция полного пересчёта для _measure_op_repeated."""
        def recalc():
            if self._cdp_recalculate(log_cb=log_cb):
                return
            # Клавиатурного пути нет сознательно: F9 в Р7 не проверен живым
            # прогоном, а слепой ввод запрещён (правило 8 CLAUDE.md).
            raise RuntimeError("полный пересчёт идёт только через api редактора (CDP), а "
                               "он недоступен или в сборке нет asc_calculate")
        return recalc

    def _cdp_recalculate(self, log_cb=None):
        """Полный пересчёт → asc_calculate(All). Проверка — сдвиг истории
        правок (пересчёт добавляет точку, проба 07.10.2026)."""
        return self._cdp_sequence(
            "Полный пересчёт",
            [("asc_calculate(All)", lambda c, t: c.recalculate(timeout=t),
              self.CDP_LONG_OP_TIMEOUT_SEC, 0)],
            self._cdp_check_document_changed, log_cb)
