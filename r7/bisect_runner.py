"""Бисект по сборкам на стенде: установка сборки, замер одной операции,
возврат исходной версии (docs/plan-to-20.md, этап 3, п. 10; docs/cli.md).

Алгоритм — r7/bisect.py (чистый). Здесь только функция замера, которую он
зовёт на каждую пробу, и обвязка вокруг:

  * установка — тем же путём, что у Batch (_batch_version_step):
    uninstall_current_version → пауза → install_version (ключи тихой
    установки по типу дистрибутива, r7/installers.py) → detect_current_version.
    Если нужная сборка уже стоит, второй раз она не ставится: добор
    повторов и проба исходной версии обходятся без переустановки;
  * замер — как у вкладки «Производительность»: Р7 на рабочей фикстуре
    (_scenario_open_r7: запуск с CDP, ожидание готовности, автосохранение
    выключено), операция из r7_ops.SpreadsheetOps, цикл повторов
    _measure_op_repeated, годные повторы — r7_reports.valid_runs (без
    прогрева, таймаутов и неподтверждённых), закрытие Р7 при любом исходе;
  * порог — из профиля шума стенда (r7/noise.py), без профиля — 10 %.

Права администратора обязательны (msiexec и установщики без них не
работают). Бисект исключает любой другой прогон и установку
(RunState, вид BISECT). Стенд остаётся таким, каким был: в конце — и при
ошибке, и после Ctrl+C — ставится исходная версия, если она стоит не та.
Дистрибутив исходной версии должен лежать среди сборок; нет его — бисект не
начинается (или restore=False, тогда остаётся последняя проверенная сборка).

BisectMixin — методы, которые R7Testovarka получает наследованием; модуль
не импортирует tkinter.
"""
import time
from dataclasses import replace

from r7 import noise, privileges
from r7.bisect import (DEFAULT_MAX_RUNS, DEFAULT_RUNS, BisectError, find_build_for_installed,
                       run_bisect)
from r7.run_state import BISECT
from r7.stand import power_plan_during_run

RESTORE_OK, RESTORE_FAILED, RESTORE_NOT_NEEDED, RESTORE_OFF, RESTORE_NO_ORIGINAL = (
    "restored", "failed", "not_needed", "off", "no_original")


class BisectMixin:
    """Бисект по сборкам — часть R7Testovarka (через наследование)."""

    BISECT_UNINSTALL_SETTLE_SEC = 2      # пауза между удалением и установкой, как у Batch

    @power_plan_during_run
    def bisect_builds(self, builds, good, bad, op_name, test_file, runs=DEFAULT_RUNS,
                      max_runs=DEFAULT_MAX_RUNS, restore=True, log_cb=None, stop_event=None):
        """Бисект между сборками good и bad по операции op_name.

        Args:
            builds: сборки (r7.bisect.Build с path), обычно все из Distributives.
            good, bad: база и плохая.
            op_name: тест из TEST_DEFINITIONS, кроме открытия файла.
            test_file: рабочая фикстура.
            restore: вернуть исходную версию в конце.

        Returns:
            BisectResult; в extra — original_version, restore, restore_text,
            installs, sessions_log (записи замеров по заходам).

        Raises:
            BisectError: нет прав, идёт другой прогон, нет дистрибутива
                исходной версии при restore=True, неверные сборки.
        """
        log_cb = log_cb or self.add_test_log
        if not privileges.is_admin():
            raise BisectError("нужны права администратора: бисект ставит и удаляет версии "
                              "Р7-Офис (запустите консоль от имени администратора)")
        refusal = self.run_state.try_start(BISECT)
        if refusal is not None:
            raise BisectError(f"{refusal[0]}. {refusal[1]}")
        try:
            return self._bisect_session(builds, good, bad, op_name, test_file, runs, max_runs,
                                        restore, log_cb, stop_event)
        finally:
            self.run_state.finish(BISECT)

    def _bisect_session(self, builds, good, bad, op_name, test_file, runs, max_runs,
                        restore, log_cb, stop_event):
        """Тело бисекта под захваченным RunState: исходная версия, порог,
        поиск, возврат исходной версии в finally."""
        orig_info = self._read_current_version_from_registry()
        orig_version = (orig_info or {}).get("version")
        orig_build = find_build_for_installed(builds, orig_version)
        if restore and orig_info and orig_build is None:
            raise BisectError(f"исходная версия {orig_version} не найдена среди дистрибутивов — "
                              f"вернуть её после бисекта будет не из чего. Положите её "
                              f"дистрибутив в Distributives или запустите с --no-restore")
        self.current_version_info = orig_info
        log_cb(f"ℹ️ Исходная версия: {orig_version or 'не установлена'}"
               + (f" ({orig_build.name})" if orig_build else ""))
        thr, src = self._bisect_threshold(op_name, log_cb)
        state = {"installed": orig_build, "installs": 0, "sessions": []}

        def measure(build, n):
            return self._bisect_measure(build, n, op_name, test_file, state, log_cb, stop_event)

        def should_stop():
            return stop_event is not None and stop_event.is_set()

        result = None
        try:
            result = run_bisect(builds, good, bad, measure, thr, runs=runs, max_runs=max_runs,
                                should_stop=should_stop, log=log_cb, op=op_name,
                                threshold_source=src)
        finally:
            restored, restore_text = self._bisect_restore(restore, orig_info, orig_build, state,
                                                          log_cb)
        return replace(result, extra={
            "original_version": orig_version, "restore": restored, "restore_text": restore_text,
            "installs": state["installs"], "sessions_log": state["sessions"],
            "test_file": str(test_file)})

    def _bisect_threshold(self, op_name, log_cb):
        """(порог %, источник) для операции: профиль шума этого стенда или 10 %."""
        env_info = self._capture_environment(log_cb=log_cb)
        self._run_environment = env_info
        fp_hash = env_info.get("fingerprint_hash") if isinstance(env_info, dict) else None
        profile = noise.load_noise_profile(self.reports_folder, fp_hash)
        thr, src, cv = noise.threshold_for(profile, op_name)
        log_cb(f"ℹ️ Порог «{op_name}»: {thr:g} % ({src}"
               + (f", CV {cv:g} %" if cv is not None else "") + ")")
        return thr, src

    def _bisect_install(self, build, state, log_cb):
        """Ставит сборку, если стоит другая (удаление текущей — как у Batch).
        Returns: True — сборка стоит."""
        if state["installed"] == build:
            return True
        self.detect_current_version()
        log_cb(f"🗑️ Удаление текущей версии ({(self.current_version_info or {}).get('version') or 'нет'})...")
        if not self.uninstall_current_version():
            state["installed"] = None
            log_cb("❌ Удаление текущей версии не завершилось успешно")
            return False
        state["installed"] = None
        time.sleep(self.BISECT_UNINSTALL_SETTLE_SEC)
        log_cb(f"📥 Установка {build.name}...")
        if not self.install_version(build.path):
            log_cb(f"❌ {build.name}: установка не завершилась успешно (таймаут или код ошибки)")
            return False
        self.detect_current_version()
        state["installed"] = build
        state["installs"] += 1
        # Дистрибутив для build.installer_file отчёта — как у Batch.
        self._session_installer = (build.name, (self.current_version_info or {}).get("version"))
        log_cb(f"✅ Установлена: {(self.current_version_info or {}).get('version') or build.label}")
        return True

    def _bisect_measure(self, build, n, op_name, test_file, state, log_cb, stop_event):
        """Заход на сборку: установка, Р7 на фикстуре, n повторов операции,
        закрытие. Returns: годные повторы или None (не удалось)."""
        import r7_reports                     # лениво: тянет jinja2
        from r7_ops import SpreadsheetOps     # лениво, как в r7/trace.py
        entry = {"build": build.name, "requested": n, "runs": None, "error": None}
        state["sessions"].append(entry)
        if not self._bisect_install(build, state, log_cb):
            entry["error"] = "установка не удалась"
            return None
        log_cb(f"▶ {build.label}: «{op_name}», {n} повторов")
        session = self._scenario_open_r7(test_file)
        if session is None:
            entry["error"] = "Р7 не открыл файл"
            return None
        try:
            fn = dict(SpreadsheetOps(self, session.find_hwnd, log_cb, test_file).tests()).get(op_name)
            if fn is None:
                entry["error"] = f"операции «{op_name}» нет в наборе"
                return None
            rec = self._measure_op_repeated(op_name, fn, n, session.find_hwnd, log_cb, stop_event)
        finally:
            self._scenario_close_r7(session)
        runs = r7_reports.valid_runs(rec)
        entry.update(runs=runs, median=rec.get("time"), error=rec.get("error"))
        if not runs:
            log_cb(f"❌ {build.label}: годных повторов нет ({rec.get('error') or 'все отброшены'})")
            return None
        return runs

    def _bisect_restore(self, restore, orig_info, orig_build, state, log_cb):
        """Возврат исходной версии. Returns: (код RESTORE_*, текст для отчёта)."""
        installed = state["installed"]
        now = installed.label if installed else "неизвестно"
        if not restore:
            text = f"исходная версия не возвращалась (--no-restore); установлена {now}"
            log_cb(f"ℹ️ {text}")
            return RESTORE_OFF, text
        if not orig_info:
            text = f"до бисекта Р7 не был установлен; оставлена последняя сборка ({now})"
            log_cb(f"ℹ️ {text}")
            return RESTORE_NO_ORIGINAL, text
        if installed == orig_build:
            return RESTORE_NOT_NEEDED, f"исходная версия {orig_build.label} на месте"
        log_cb(f"↩️ Возврат исходной версии {orig_build.name}...")
        try:
            ok = self._bisect_install(orig_build, state, log_cb)
        except Exception as e:   # возврат идёт и после сбоя бисекта — не терять исходную ошибку
            log_cb(f"❌ Возврат исходной версии упал: {type(e).__name__}: {e}")
            ok = False
        if ok:
            text = f"исходная версия {orig_build.label} возвращена"
            log_cb(f"✅ {text}")
            return RESTORE_OK, text
        text = (f"исходную версию {orig_build.label} вернуть не удалось — поставьте "
                f"{orig_build.name} вручную (вкладка «Версии»)")
        log_cb(f"❌ {text}")
        return RESTORE_FAILED, text
