"""Замер операции: цикл повторов, конец операции, ресурсы Р7, диск.

Главные правила (CLAUDE.md): замер отражает Р7, а не инструмент — свои
паузы только через _pace, они вычитаются; конец операции — простой Р7
(_wait_operation_done, на CDP-пути — пинг редактора), а не последнее
нажатие; только time.perf_counter(). MeasureMixin — методы, которые
R7Testovarka получает наследованием; пороги (OP_*, READY_*) пока остаются
константами R7Testovarka и читаются через self.
"""
import statistics
import time
from pathlib import Path

from r7 import env
from r7.env import psutil, win32gui
from r7.processes import X2tTracker, _is_crash_snapshot
from r7.resources import _disk_delta, _disk_snapshot


class MeasureMixin:
    """Цикл повторов, детекторы конца операции, ресурсы — часть R7Testovarka."""


    @staticmethod
    def _mad(values):
        """Median Absolute Deviation — устойчивая мера разброса, пара к
        медиане (в статистике нет готовой функции для этого — sample stdev
        есть, MAD нет, см. модуль statistics). Не масштабируется константой
        1.4826 (переводящей MAD в оценку, сравнимую со стандартным
        отклонением для нормального распределения) — здесь скорость
        операций Р7 ничем не гарантированно нормальна, само значение MAD
        интересно как «типичное отклонение от медианы» в секундах, не как
        оценка сигмы.

        Args:
            values: Непустая последовательность чисел.

        Returns:
            float: MAD. 0.0, если все значения совпадают.
        """
        center = statistics.median(values)
        return statistics.median(abs(v - center) for v in values)

    def _measure_op_repeated(self, name, func, runs, find_hwnd, log_cb, stop_event,
                             focus_cb=None, post_delay=None):
        """Замер одной операции: runs повторов, медиана/MAD, ресурсы за окно.

        ОБЩИЙ код вкладки «Производительность» (run_test_with_runs) и
        Batch-режима (measure). Раньше это были две копии, и Batch отстал:
        один прогон без прогрева, без "runs" в JSON — вердикт сравнения версий
        (compare_runs) для Batch был недоступен именно там, где сравнивают
        версии (аудит 29.09.2026, пункт 13). Одна реализация — одинаковые
        цифры в обоих режимах по построению, а не по дисциплине зеркалирования.

        На каждый повтор:
          * снимок истории правок и база CPU — ДО секундомера;
          * секундомер: от вызова func до начала простоя Р7
            (_wait_operation_done, уточнённый _resolve_op_end), минус
            собственные паузы (_paced_total);
          * после замера — добивание модалки, отложенная CDP-проверка, пауза;
          * между повторами (не после последнего) — откат правок, чтобы
            каждый повтор шёл на одном и том же документе, а следующие
            операции цепочки видели ровно одну применённую правку.

        Args:
            name: Имя операции (ключ отчёта).
            func: Тест-функция без аргументов.
            runs: Число повторов.
            find_hwnd: Функция поиска окна Р7.
            log_cb: Функция логирования.
            stop_event: threading.Event — прерывание между повторами.
            focus_cb: Фокус на окно Р7 перед первым повтором.
            post_delay: Пауза после повтора вне замера; по умолчанию 0.5 с.

        Returns:
            dict: запись results для этой операции.
        """
        runs = max(1, int(runs))
        log_cb(f"⏳ Тест: {name} (прогон 1/{runs})...")
        if focus_cb is not None:
            try:
                focus_cb()
            except Exception as e:
                log_cb(f"   ⚠️ Не удалось установить фокус: {e}")

        pass_times = []
        run_statuses = []     # статус детектора на КАЖДЫЙ прогон: ok/below_floor/timeout
        runs_independent = True   # каждый повтор откатан к исходному документу
        run_res = []              # ресурсы Р7 за окно каждого прогона (OpResourceWatch)
        run_x2t = []              # сводка по x2t на каждый прогон (X2tTracker)
        alerts_seen = []          # тексты окон Р7, закрытых после успешных прогонов
        run_disk = []             # дисковая активность за окно каждого прогона
        api_ms_values = []    # синхронное время api по прогонам, ушедшим через CDP
        error = None
        below_floor = False   # хоть один прогон оказался ниже порога измерения
        for i in range(runs):
            if stop_event is not None and stop_event.is_set():
                log_cb(f"⏹ {name}: остановлено пользователем "
                       f"(выполнено прогонов: {i}/{runs})")
                break
            if i > 0:
                log_cb(f"⏳ Тест: {name} (прогон {i + 1}/{runs})...")
            # Окно с опозданием от прошлой операции (например, «Нельзя
            # сохранить…» после упавшего экспорта) перехватило бы ввод.
            self._dismiss_info_alerts(log_cb)
            # Подготовка теста (рабочий лист, выделение, буфер обмена) — вне
            # замера, до ожидания простоя: переключение листа тоже работа Р7.
            prepare = getattr(func, "prepare", None)
            if prepare is not None:
                try:
                    prepare()
                except Exception as e:
                    error = f"подготовка теста не удалась: {e}"
                    log_cb(f"   ❌ прогон {i + 1}: {error}")
                    break
            # Секундомер стартует только на простаивающем Р7: иначе в замер
            # попадает асинхронный хвост предыдущей операции (агрегаты
            # статусной строки после выделения, отрисовка после вставки) —
            # живой прогон 29.09.2026. Вне замера; если Р7 уже свободен,
            # стоит 0.3 с.
            self._wait_operation_done(find_hwnd, log_cb=log_cb, start_grace=0.3)
            self._cdp_settle()     # и сам редактор свободен (точнее опроса CPU)
            self._paced_total = 0.0
            self._op_start_grace = None
            self._op_max_wait = None
            self._op_via_cdp = False
            self._op_unverified = None    # причина, если CDP не подтвердил результат
            self._cdp_api_ms = 0.0
            self._op_completed_at = None
            # Снимок истории и база CPU — ДО старта секундомера.
            hist_before = self._history_snapshot()
            watch = self._op_watch()
            watch.start()
            disk_before = _disk_snapshot()       # ~4 мс, до секундомера
            start = time.perf_counter()
            self._op_started_at = start          # для раннего выхода экспорта по x2t
            self._export_fail_reason = None
            try:
                func()
            except Exception as e:
                error = str(e)
                watch.stop()
                run_x2t.append(X2tTracker.summarize(self._x2t_since(start)))
                # Окно ошибки Р7 — часть диагноза: его текст идёт в ошибку прогона.
                alerts = self._dismiss_info_alerts(log_cb)
                if alerts:
                    error += "; Р7: " + " / ".join(f"«{t}»" for t in alerts)
                log_cb(f"   ❌ прогон {i + 1}: ошибка — {error}")
                break
            if self._op_completed_at is not None:
                # Экспорт: конец — запись файла, детектор не нужен.
                done_ts, status = self._op_completed_at, "ok"
            else:
                done_ts, status = self._resolve_op_end(
                    *self._wait_operation_done(find_hwnd, log_cb=log_cb))
            # Момент конца ожидания — до снимков диска/x2t/истории: при
            # таймауте время прогона считается от него, и round-trip снимков
            # в цифру не попадает (QA-аудит 29.09.2026, G-02).
            wait_end = time.perf_counter()
            run_res.append(watch.stop())
            run_x2t.append(X2tTracker.summarize(self._x2t_since(start)))
            run_disk.append(_disk_delta(disk_before, _disk_snapshot(),
                                        self._matches_r7_process, self._x2t_since(start)))
            # Предохранитель (живой прогон 29.09.2026): клавиатурный тест ВПР
            # три прогона подряд «работал» 0.34 с, а история правок не
            # сдвинулась ни разу — формула не вводилась, и цифра была временем
            # нажатий в пустоту. Операция, которая должна менять документ, но
            # не изменила его, — ошибка, а не результат. Проверка вне замера.
            if hist_before is not None and self._op_expects_change(name):
                hist_after = self._history_snapshot()
                if hist_after is not None and hist_after["index"] == hist_before["index"]:
                    error = ("операция не изменила документ (история правок не "
                             "сдвинулась) — замер недостоверен")
                    log_cb(f"   ❌ прогон {i + 1}: {error}")
                    break
            if status == "timeout":
                elapsed = wait_end - start - self._paced_total
            else:
                elapsed = max(0.0, done_ts - start - self._paced_total)
            pass_times.append(elapsed)
            run_statuses.append(status)
            if self._op_via_cdp:
                api_ms_values.append(self._cdp_api_ms)
            # Замер закрыт — только теперь добиваем модалку «Вставить ячейки»
            # и доводим отложенную проверку CDP-операции: их паузы и
            # round-trip не должны попадать в цифру.
            self._flush_pending_modal_confirm(log_cb=log_cb)
            self._flush_pending_cdp_verify(log_cb=log_cb)
            if self._op_unverified and run_statuses[-1] != "timeout":
                # Операция могла не выполниться вовсе (≈0 мс) — в медиану не
                # берём, как и таймаут (аудит 06.10.2026).
                run_statuses[-1] = "unverified"
                log_cb(f"   ⚠️ прогон {i + 1}: результат не подтверждён "
                       f"({self._op_unverified}) — в статистику не входит")
            if getattr(self, "_pending_sheet_clip_mark", False):
                # Копия листа в буфере — запоминаем состояние буфера
                # (см. _paste_big_prepare). После замера: буфер дописан.
                self._pending_sheet_clip_mark = False
                self._sheet_clip_seq = self._clipboard_seq()
            run_alerts = self._dismiss_info_alerts(log_cb)
            if run_alerts:
                alerts_seen.extend(run_alerts)
            if post_delay is not None:
                post_delay()
            else:
                time.sleep(0.5)
            # Откат — после КАЖДОГО повтора, и после последнего тоже. Прежде
            # последняя правка оставалась «для следующих операций цепочки»,
            # но с 30.09.2026 у каждого теста своя подготовка, и оставшийся
            # лист с 50К вставленных строк лишь утяжелял документ для всех
            # тестов после «Вставки большого массива»: экспорт XLTX на нём
            # шёл 37 с против 5.5 с на файле как есть (живой замер 07.10.2026).
            if not (stop_event is not None and stop_event.is_set()):
                restored = self._restore_history(hist_before, name, find_hwnd,
                                                 log_cb=log_cb)
                if restored is not True and i < runs - 1:
                    runs_independent = False
                elif restored is False:
                    log_cb(f"   ⚠️ {name}: последнюю правку откатить не удалось — "
                           f"следующие тесты пойдут на изменённом документе")
            # api_ms печатается рядом с elapsed (settle_ms), а не вместо него.
            _api_note = (f" [api: {self._cdp_api_ms:.2f} мс]"
                         if self._op_via_cdp else "")
            if status == "below_floor":
                # На CDP-пути это не «быстрее порога»: Р7 работал ВНУТРИ вызова
                # (asc_Paste на 50K строк — 28 с при 32 с процессорного
                # времени), и цифра — реальная. Пометка «<порога» в отчёте
                # висела на многосекундных операциях (30.09.2026).
                below_floor = below_floor or not self._op_via_cdp
                _grace = self._op_start_grace or self.OP_START_GRACE_SEC
                if self._op_via_cdp:
                    # На CDP-пути цифра — реальная длительность вызова api:
                    # Runtime.evaluate возвращается, когда api отработал.
                    log_cb(f"   ⏱ прогон {i + 1}: {elapsed:.3f} сек{_api_note} — "
                           f"вызов api отработал синхронно, Р7 не стал занятым")
                else:
                    log_cb(f"   ⏱ прогон {i + 1}: {elapsed:.3f} сек — Р7 не был занят "
                           f"дольше {_grace:.1f} сек, операция ниже порога измерения")
            elif status == "timeout":
                log_cb(f"   ⚠️ прогон {i + 1}: {elapsed:.3f} сек{_api_note} — "
                       f"Р7 так и не освободился")
            elif run_statuses[-1] == "unverified":
                log_cb(f"   ⚠️ прогон {i + 1}: {elapsed:.3f} сек{_api_note} — не подтверждён")
            else:
                log_cb(f"   ✅ прогон {i + 1}: {elapsed:.3f} сек{_api_note}")

        # Уборка за подготовкой (вне замера): то, что подготовка создала вне
        # истории повтора (свежий лист «Вставки большого массива»), иначе
        # осталось бы следующим тестам — см. _paste_big_cleanup.
        cleanup = getattr(func, "cleanup", None)
        if cleanup is not None and not (stop_event is not None and stop_event.is_set()):
            try:
                cleanup()
            except Exception as e:
                log_cb(f"   ⚠️ {name}: уборка после теста не удалась ({e}) — "
                       f"следующие тесты пойдут на изменённом документе")

        if not pass_times:
            return {"name": name, "time": 0.0, "error": error,
                    "ram": None, "cpu": None, "cpu_normalized": None,
                    "cpu_sec": None, "cpu_peak_core_pct": None,
                    "threads": None, "uptime_sec": None,
                    "runs": [], "run_statuses": [],
                    "avg": 0.0, "min": 0.0, "max": 0.0,
                    "median": 0.0, "mad": 0.0, "n_runs": 0,
                    "first_run_discarded": False, "n_timeouts": 0, "n_unverified": 0,
                    "runs_independent": runs_independent,
                    "below_floor": False, "api_ms": None,
                    # Экспорт, у которого упал x2t, — именно здесь: код
                    # конвертера нужен в отчёте, а не только в логе.
                    "x2t": self._aggregate_x2t(run_x2t, range(len(run_x2t)), log_cb)}

        # avg/min/max — старые ключи (совместимость с сохранёнными JSON).
        # Headline ("time") — медиана: среднее на бимодальной величине
        # сдвигается одним выбросом.
        avg_t = sum(pass_times) / len(pass_times)
        min_t = min(pass_times)
        max_t = max(pass_times)

        # Первый прогон — прогрев, таймауты — вне статистики (_stats_indices).
        stats_idx, first_run_discarded, n_timeouts = self._stats_indices(run_statuses)
        n_unverified = run_statuses.count("unverified")
        if n_unverified and not any(st not in ("unverified", "timeout") for st in run_statuses):
            # Ни одного годного прогона (только неподтверждённые и таймауты):
            # медиана из «≈0 мс, может, не выполнилось» и предохранителей —
            # не цифра, а ошибка.
            error = error or (f"ни один из {len(run_statuses)} прогонов не подтверждён "
                              f"через CDP — замер недостоверен")
            log_cb(f"   ❌ {error}")
        elif n_unverified:
            log_cb(f"   ⚠️ {n_unverified} прогон(ов) без подтверждения исключены из "
                   f"статистики")
        stats_times = [pass_times[k] for k in stats_idx]
        res_agg = self._aggregate_op_resources(run_res, stats_idx)
        if n_timeouts:
            log_cb(f"   ⚠️ {n_timeouts} прогон(ов) с таймаутом исключены из "
                   f"статистики: их время — предохранитель, а не длительность")
        median_t = statistics.median(stats_times)
        mad_t = self._mad(stats_times)

        # Среднее api_ms — только по прогонам через CDP.
        avg_api_ms = (round(sum(api_ms_values) / len(api_ms_values), 3)
                      if api_ms_values else None)
        _avg_api_note = (f", api {avg_api_ms:.2f} мс" if avg_api_ms is not None else "")
        _discard_note = " (1-й отброшен)" if first_run_discarded else ""
        log_cb(f"   📊 медиана {median_t:.3f} сек (MAD {mad_t:.3f}) — "
               f"{len(stats_times)}/{len(pass_times)} прогонов{_discard_note}, "
               f"среднее {avg_t:.3f} сек (мин {min_t:.3f}, макс "
               f"{max_t:.3f}{_avg_api_note})")

        # Потоки/аптайм — снимком после операции; CPU и пик RAM — из
        # OpResourceWatch за окно операции (cpu_percent(interval=0.1) здесь
        # блокировал бы ~1 с на уже простаивающем Р7).
        self._r7_pids = None
        sample = self._sample_r7_resources(
            self._get_r7_processes(log_cb=log_cb), measure_cpu=False)
        self._log_op_resources(res_agg, log_cb=log_cb)

        return {
            "name": name, "time": median_t, "error": error,
            "ram":            res_agg["ram"],
            "cpu":            res_agg["cpu"],
            "cpu_normalized": res_agg["cpu_normalized"],
            "cpu_sec":        res_agg["cpu_sec"],
            "cpu_peak_core_pct": res_agg["cpu_peak_core_pct"],
            "threads":        sample["threads"]      if sample else None,
            "uptime_sec":     sample["uptime_sec"]    if sample else None,
            "runs": pass_times, "run_statuses": run_statuses,
            "avg": avg_t, "min": min_t, "max": max_t,
            "median": median_t, "mad": mad_t, "n_runs": len(stats_times),
            "first_run_discarded": first_run_discarded,
            "n_timeouts": n_timeouts, "n_unverified": n_unverified,
            "runs_independent": runs_independent,
            "below_floor": below_floor, "api_ms": avg_api_ms,
            "x2t": self._aggregate_x2t(run_x2t, stats_idx, log_cb),
            "r7_alerts": alerts_seen,
            "disk": self._aggregate_disk(run_disk, stats_idx, log_cb),
        }


    def _op_expects_change(self, name):
        """True — после операции в истории правок должна появиться точка."""
        return not any(m in name for m in self.NON_MUTATING_MARKERS)

    def _resolve_op_end(self, done_ts, status):
        """Уточняет конец операции, если тест-функция сама дождалась результата.

        save_as_format ждёт файл экспорта внутри себя (_wait_for_export_file)
        и кладёт время последней записи файла в self._op_completed_at. К
        моменту _wait_operation_done x2t уже мёртв, Р7 простаивает, и
        детектор честно отвечает below_floor — а HTML помечал 100-секундный
        экспорт в PDF как «<порога» (аудит 29.09.2026). Если детектор видел
        работу Р7 и после файла (status ok), берётся его момент — он позже.

        Returns:
            tuple[float | None, str]: (момент конца, статус).
        """
        completed = getattr(self, "_op_completed_at", None)
        if completed is not None and status != "timeout":
            # Файл записан — экспорт закончен. Активность Р7 после этого
            # (сборка мусора, перерисовка) — не экспорт: с окном старта 6 с
            # детектор ловил её и сдвигал конец на секунды.
            return completed, "ok"
        return done_ts, status

    def _select_stats_runs(self, pass_times, run_statuses, discard_warmup=True):
        """Прогоны, которые идут в медиану/MAD.

        Прогоны с timeout исключаются: их «время» — предохранитель
        (OP_MAX_WAIT_SEC или укороченный _op_max_wait), а не длительность
        операции, и одна такая точка сдвигала медиану трёх прогонов на
        десятки секунд. Первый прогон отбрасывается как прогрев, если после
        этого останется хоть один. Если таймаут у всех — статистика по всем,
        иначе считать нечего (флаг n_timeouts в отчёте это покажет).

        Args:
            pass_times: Время каждого прогона, сек.
            run_statuses: Статус детектора на каждый прогон (та же длина).

        Returns:
            tuple[list[float], bool, int]: (времена для статистики,
            отброшен ли первый прогон, число таймаутов).
        """
        idx, first_run_discarded, n_timeouts = self._stats_indices(
            run_statuses, discard_warmup=discard_warmup)
        return [pass_times[i] for i in idx], first_run_discarded, n_timeouts


    def _stats_indices(self, run_statuses, discard_warmup=True):
        """Индексы прогонов для статистики — см. _select_stats_runs.

        Args:
            discard_warmup: False — первый прогон не отбрасывать (повторы
                открытия файла: каждый — независимый холодный старт).

        Returns:
            tuple[list[int], bool, int]: (индексы, отброшен ли первый, таймаутов).
            Прогоны «unverified» (схема 8) исключаются так же, как таймауты, но
            в число таймаутов не входят.
        """
        valid = [i for i, st in enumerate(run_statuses) if st not in ("timeout", "unverified")]
        n_timeouts = run_statuses.count("timeout")
        if not valid:
            return list(range(len(run_statuses))), False, n_timeouts
        first_run_discarded = (discard_warmup and valid[0] == 0
                               and len(valid) >= self.MIN_RUNS_FOR_STATS)
        if first_run_discarded:
            valid = valid[1:]
        return valid, first_run_discarded, n_timeouts


    def _pace(self, seconds):
        """Преднамеренная пауза внутри измеряемой операции.

        Нужна там, где Р7-Офис физически не успевает за клавиатурой: между
        открытием меню и выбором пункта, между Ctrl+C и Ctrl+V и т.п. В отличие
        от прежних time.sleep() и pyautogui.PAUSE, это время накапливается в
        self._paced_total и вычитается из результата замера — то есть пауза
        обеспечивает надёжность автоматизации, но не попадает в цифру
        производительности.

        Вычитается только та часть паузы, когда Р7 простаивал (аудит
        29.09.2026, пункт 15). Раньше вычиталась вся пауза вслепую — а часть
        пауз стоит там, где Р7 как раз работает: после Ctrl+C (наполняется
        буфер обмена), после переключения листа. Такое время реально
        принадлежит операции, и его вычитание занижало результат. Теперь
        снимается процессорное время процессов Р7 за паузу: ниже порога
        занятости (OP_BUSY_CORE_PCT) — Р7 ждал нас, вычитаем всё; выше — Р7
        работал, вычитаем только простойную долю. Если снять CPU не удалось
        (нет psutil/процессов), поведение прежнее.

        Args:
            seconds: Длительность паузы.
        """
        if seconds <= 0:
            return
        t0 = time.perf_counter()
        c0 = self._r7_cpu_seconds()
        time.sleep(seconds)
        c1 = self._r7_cpu_seconds()
        dur = time.perf_counter() - t0
        self._paced_total += self._idle_share_of_pause(dur, c0, c1)

    def _idle_share_of_pause(self, dur, c0, c1):
        """Сколько из паузы длиной dur Р7 простаивал — см. _pace.

        Args:
            dur: Длительность паузы, сек.
            c0, c1: Процессорное время Р7 в начале и в конце (None — неизвестно).

        Returns:
            float: Секунды, которые можно вычесть из замера.
        """
        if c0 is None or c1 is None or dur <= 0:
            return dur
        busy = max(0.0, c1 - c0)
        if busy / dur * 100.0 < self.OP_BUSY_CORE_PCT:
            return dur
        return dur * max(0.0, 1.0 - busy / dur)


    def _flush_pending_modal_confirm(self, log_cb=None):
        """Досылает страховочные Enter'ы по модалке — уже ВНЕ окна замера.

        Нужно на случай, когда модалка не успела появиться за OP_DIALOG_PACE:
        тогда подтверждающий Enter из _confirm_modal_enter ушёл в сетку, модалка
        всплыла позже и висит. Здесь она добивается, не искажая цифру: замер к
        этому моменту уже закрыт (_wait_operation_done отработал), поэтому пауза
        обычная time.sleep, а не _pace.

        Лишний Enter безвреден: если модалки нет, он лишь сдвигает активную
        ячейку на строку вниз, а следующий тест всё равно начинается с Ctrl+Home.
        Последовательность самоисправляющаяся — какой бы из Enter'ов ни совпал с
        появлением модалки, она закроется.

        Вызывать в обоих воркерах сразу после _wait_operation_done (см. правило
        зеркалирования в CLAUDE.md).

        Args:
            log_cb: Функция логирования; по умолчанию self.add_test_log.
        """
        if not self._pending_modal_confirm:
            return
        self._pending_modal_confirm = False
        # Диагностика (один раз за прогон): что на самом деле висит на экране
        # после подтверждающего Enter. Именно эти подписи нужны, чтобы чинить
        # слепую навигацию стрелками по контекстному меню — сейчас счётчик
        # `down` подобран вслепую и уезжает, стоит меню обзавестись пунктом.
        # Стоит вне замера, поэтому на цифры не влияет.
        self._cdp_dump_ui("после подтверждения модалки «Вставить ячейки»", log_cb=log_cb)
        for _ in range(max(0, self.OP_DIALOG_ATTEMPTS - 1)):
            time.sleep(self.OP_DIALOG_PACE)
            try:
                self._press('enter')
            except Exception as e:
                (log_cb or self.add_test_log)(
                    f"   ⚠️ Не удалось дослать Enter по модалке: {e}")
                return

    def _cdp_settle(self, connector=None, max_wait=5.0):
        """Ждёт, пока редактор доделает предыдущий шаг: пинг до быстрого ответа.

        Замена слепой паузе между шагами CDP-цепочки (копирование → вставка):
        пауза вычиталась из замера по процессорному времени Р7, а оно
        квантуется тиком 15.6 мс — шум ±16 мс на операции в 230 мс. Пинг
        возвращается ровно тогда, когда редактор свободен (после копирования —
        через 15–120 мс), и это время — работа Р7, оно остаётся в замере.

        Returns:
            bool: True — редактор ответил быстро (свободен).
        """
        if connector is None:
            connector = self._cdp_ops_connector()
        if connector is None:
            return False
        deadline = time.perf_counter() + max_wait
        while time.perf_counter() < deadline:
            t0 = time.perf_counter()
            if not connector.ping(timeout=max_wait):
                return False
            if time.perf_counter() - t0 <= self.OP_PING_FAST_SEC:
                return True
        return False

    def _wait_renderer_idle(self, log_cb=None):
        """Конец операции на CDP-пути: момент, когда редактор снова свободен.

        Трассировка на живом Р7 (30.09.2026, автосохранение отключено): после
        каждого вызова api редактор свободен через 0–4 мс, после копирования —
        через 15–120 мс (дописывается буфер обмена). Позже идут только
        редкие многопоточные всплески CPU — фоновая сборка мусора, интерфейс
        она не блокирует. Опрос CPU время от времени принимал такой всплеск
        за продолжение операции и прибавлял 0.2–1 с (Ctrl+A: 0.84 / 1.88 /
        0.84 с при вызове 0.84 с).

        Пинг (R7WebDriverConnector.ping) отвечает, когда главный поток
        редактора обработал пустую задачу. Медленный ответ = редактор был
        занят до момента ответа; конец операции — момент последнего
        медленного ответа, либо возврат вызова api, если медленных не было.
        Окно OP_PING_QUIET_SEC ловит отложенную работу, которая стартует
        сразу после вызова (так ведёт себя автосохранение: первый пинг 3 мс,
        второй — 2.7 с).

        Модалка тяжёлого пересчёта после операции: редактор простаивает,
        дожидаясь ответа, — закрываем «Нет», ожидание ответа относим к
        собственным паузам, дальше ждём конец пересчёта.

        Returns:
            tuple[float | None, str] | None: (момент, "ok"/"timeout");
            None — пинг недоступен (нет CDP, обрыв), вызывающий код
            переходит на опрос CPU.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        connector = self._cdp_ops_connector()
        if connector is None:
            return None
        start = time.perf_counter()
        max_wait = getattr(self, "_op_max_wait", None) or self.OP_MAX_WAIT_SEC
        deadline = start + max_wait
        end = start                  # возврат вызова api
        quiet_since = None
        prompt_checked = False
        while True:
            t0 = time.perf_counter()
            if t0 >= deadline:
                log_cb(f"   ⚠️ Р7-Офис не освободился за {max_wait:.0f} сек")
                return None, "timeout"
            answered = connector.ping(timeout=max(1.0, deadline - t0))
            t1 = time.perf_counter()
            if not answered:
                if not getattr(connector, "connected", False):
                    return None          # обрыв — дальше по CPU
                # Таймаут пинга: редактор всё ещё занят. Пауза — на случай
                # мгновенного отказа: без неё цикл крутился до дедлайна и
                # отнимал у Р7 ядро (аудит 06.10.2026). Конец операции берётся
                # из отметок времени, пауза в цифру не попадает.
                time.sleep(self.OP_PING_GAP_SEC)
                continue
            if t1 - t0 > self.OP_PING_FAST_SEC:
                end = t1                 # был занят до этого момента
                quiet_since = None
            else:
                if quiet_since is None:
                    quiet_since = t0
                if t1 - quiet_since >= self.OP_PING_QUIET_SEC:
                    if not prompt_checked:
                        prompt_checked = True
                        if self._dismiss_heavy_calc_prompt(log_cb):
                            clicked_at = time.perf_counter()
                            exact = getattr(self, "_last_prompt_wait_sec", None)
                            self._paced_total += (exact if exact is not None
                                                  else max(0.0, clicked_at - end))
                            end = clicked_at
                            quiet_since = None
                            prompt_checked = False
                            continue
                    return end, "ok"
            time.sleep(self.OP_PING_GAP_SEC)

    def _wait_operation_done(self, hwnd, log_cb=None, start_grace=None):
        """Ждёт, пока Р7-Офис закончит обрабатывать только что отправленную операцию.

        Прежде замер операции заканчивался на последнем нажатии клавиши:
        pyautogui только кладёт события во входную очередь и возвращается сразу,
        поэтому «время операции» равнялось сумме собственных задержек скрипта.
        Здесь секундомер останавливается по реальному признаку — Р7 перестал
        быть занятым.

        Занятость определяется по трём признакам (любой означает «занят»):
          1. Окно не прокачивает очередь сообщений за OP_RESPONSIVE_MS.
          2. Процессы Р7 грузят CPU не ниже OP_BUSY_CORE_PCT (% одного ядра).
          3. Жив конвертер x2t — им идёт экспорт в PDF.

        Возвращается момент НАЧАЛА простоя, а не конец окна подтверждения,
        поэтому OP_IDLE_SAMPLES не добавляется к результату замера. Все
        моменты — time.perf_counter(): вызывающий код обязан засекать старт
        тем же счётчиком.

        Начало простоя — не момент опроса, на котором детектор его заметил,
        а начало CPU-окна, в котором загрузка упала ниже порога (но не
        раньше последнего опроса с другим признаком занятости). Раньше
        бралось время опроса, и результат опаздывал на 0.2–0.4 с: для
        операций короче секунды это 30–50% ошибки (аудит 29.09.2026).
        Остаточная погрешность — в пределах одного OP_CPU_WINDOW_SEC, в
        сторону завышения.

        Args:
            hwnd: Дескриптор окна Р7 либо функция его поиска.
            log_cb: Функция логирования; по умолчанию self.add_test_log.
            start_grace: Сколько ждать начала работы Р7. По умолчанию
                OP_START_GRACE_SEC; экспорт в PDF просит больше через
                self._op_start_grace, потому что x2t стартует с задержкой.

        Returns:
            tuple[float | None, str]: (момент завершения, статус).
              "ok"          — работа началась и закончилась;
              "below_floor" — Р7 не стал занятым за OP_START_GRACE_SEC, то есть
                              операция быстрее порога измерения;
              "timeout"     — не дождались за OP_MAX_WAIT_SEC (момент — None).
        """
        if log_cb is None:
            log_cb = self.add_test_log

        # Операция ушла через api — конец определяет сам редактор (пинг),
        # а не опрос CPU. Явное окно старта (экспорт) оставляет прежний путь.
        if (start_grace is None and getattr(self, "_op_via_cdp", False)
                and getattr(self, "_op_start_grace", None) is None):
            res = self._wait_renderer_idle(log_cb)
            if res is not None:
                return res

        if start_grace is None:
            start_grace = getattr(self, "_op_start_grace", None)
        if start_grace is None and getattr(self, "_op_via_cdp", False):
            # Операция ушла через api: к возврату вызова она уже отработала, и
            # настоящий асинхронный хвост (вставка — ещё 2–3 с при 100%+ ядра)
            # начинается сразу. Занятость, начавшаяся позже, — фон Р7, а не
            # операция: с общим окном в 1 с она время от времени попадала в
            # замер, и Ctrl+A в полном прогоне дал 0.84 / 1.88 / 0.84 с при
            # вызове api 0.84 с каждый раз (30.09.2026).
            start_grace = self.OP_CDP_TAIL_GRACE_SEC
        if start_grace is None:
            start_grace = self.OP_START_GRACE_SEC

        # Предохранитель на операцию — как и start_grace, его может укоротить
        # сама тест-функция через self._op_max_wait (см. select_all).
        max_wait = getattr(self, "_op_max_wait", None) or self.OP_MAX_WAIT_SEC
        start    = time.perf_counter()
        deadline = start + max_wait

        cur_hwnd     = None if callable(hwnd) else hwnd
        tracked      = {}     # pid -> (psutil.Process с «прогретым» CPU, имя)
        last_refresh = 0.0
        last_cpu_at  = 0.0
        last_cpu     = 0.0
        prev_cpu     = 0.0      # CPU предыдущего окна — для подтверждения занятости
        cpu_win_start = start   # начало окна, к которому относится last_cpu
        last_signal_busy_at = None   # последний опрос с «не отвечает» / живым x2t
        seen_busy    = False
        idle_streak  = 0
        idle_since   = None
        last_prompt_check = 0.0

        while time.perf_counter() < deadline:
            now = time.perf_counter()

            if callable(hwnd):
                if not (cur_hwnd and env.WIN32_OK and win32gui.IsWindow(cur_hwnd)):
                    cur_hwnd = hwnd()

            if env.PSUTIL_OK and now - last_refresh >= self.OP_PROC_REFRESH_SEC:
                last_refresh = now
                self._r7_pids = None
                for p in self._get_r7_processes(log_cb=log_cb):
                    if p.pid in tracked:
                        continue
                    try:
                        name = (p.name() or "").lower()
                        p.cpu_percent(None)
                        tracked[p.pid] = (p, name)
                    # процесс завершился до первого замера CPU — считать нечего
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

            # x2t проверяем на каждом опросе — он короткоживущий, и лишние
            # 0.2 сек ожидания его смерти уехали бы прямо в замер PDF-экспорта.
            converter_alive = False
            for pid, (p, name) in list(tracked.items()):
                if "x2t" not in name:
                    continue
                try:
                    if p.is_running() and not _is_crash_snapshot(p):
                        converter_alive = True
                    else:
                        tracked.pop(pid, None)
                except Exception:
                    tracked.pop(pid, None)

            if env.PSUTIL_OK and now - last_cpu_at >= self.OP_CPU_WINDOW_SEC:
                cpu_win_start = last_cpu_at or start
                last_cpu_at = now
                prev_cpu = last_cpu
                total = 0.0
                dead  = []
                for pid, (p, _name) in tracked.items():
                    try:
                        total += p.cpu_percent(None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        dead.append(pid)
                for pid in dead:
                    tracked.pop(pid, None)
                # Сырая сумма в % одного ядра — см. READY_IDLE_CORE_PCT
                # (measure_schema 3): нормировка на число ядер прятала
                # однопоточную работу Р7 на многоядерных стендах.
                last_cpu = total

            responsive = self._window_responsive(cur_hwnd, self.OP_RESPONSIVE_MS)
            signal_busy = (not responsive) or converter_alive
            if signal_busy:
                # После проверки: неотзывчивое окно держит её до OP_RESPONSIVE_MS.
                last_signal_busy_at = time.perf_counter()
            # CPU — признак занятости, только если подтверждён: два окна
            # подряд выше OP_BUSY_CORE_PCT или одно выше OP_BUSY_STRONG_CORE_PCT.
            # Одиночный всплеск — фон GPU-процесса и рендерера Р7 (1–2 тика
            # таймера, 31–62% ядра на окне 50 мс), он есть и без всякой
            # операции. Раньше такой всплеск время от времени «ловился», и
            # детектор ждал, пока он утихнет: вставка 1–5 ячеек давала то
            # 0.2 с, то 0.6–1.2 с (живой прогон 29.09.2026). Настоящая работа
            # Р7 идёт с загрузкой 100% ядра и выше — её правило не теряет.
            cpu_busy = env.PSUTIL_OK and (
                last_cpu >= self.OP_BUSY_STRONG_CORE_PCT
                or (last_cpu >= self.OP_BUSY_CORE_PCT and prev_cpu >= self.OP_BUSY_CORE_PCT))
            busy = signal_busy or cpu_busy

            # Модалка тяжёлого пересчёта может всплыть и после операции
            # (большая вставка). Пока она ждёт ответа, Р7 простаивает, и
            # детектор закрыл бы замер ДО пересчёта. Закрываем «Нет», время
            # ожидания ответа относим к собственным паузам (_paced_total).
            if (not busy and now - last_prompt_check >= self.HEAVY_CALC_CHECK_SEC):
                last_prompt_check = now
                if self._dismiss_heavy_calc_prompt(log_cb):
                    clicked_at = time.perf_counter()
                    shown_since = idle_since if idle_since is not None else now
                    exact = getattr(self, "_last_prompt_wait_sec", None)
                    self._paced_total += (exact if exact is not None
                                          else max(0.0, clicked_at - shown_since))
                    last_signal_busy_at = clicked_at
                    idle_streak = 0
                    idle_since = None
                    continue

            if busy:
                seen_busy   = True
                idle_streak = 0
                idle_since  = None
            else:
                if idle_streak == 0:
                    # Простой начался не раньше начала простойного CPU-окна
                    # и не раньше последнего опроса с другим признаком
                    # занятости.
                    idle_since = max(cpu_win_start, last_signal_busy_at or start)
                idle_streak += 1
                if seen_busy and idle_streak >= self.OP_IDLE_SAMPLES:
                    return idle_since, "ok"
                if not seen_busy and now - start >= start_grace:
                    # Р7 вообще не стал занятым — операция быстрее, чем мы умеем мерить.
                    return start, "below_floor"

            time.sleep(self.OP_POLL_SEC)

        log_cb(f"   ⚠️ Р7-Офис не освободился за {max_wait:.0f} сек")
        return None, "timeout"

    def _wait_for_export_file(self, path_str, timeout=None, log_cb=None):
        """Ждёт, пока x2t допишет файл экспорта (save_as_format).

        _wait_operation_done меряет занятость Р7 по CPU и живому процессу
        x2t — но x2t короткоживущий, и на живом прогоне (50K строк) иногда
        укладывался в промежуток между двумя опросами CPU
        (OP_CPU_WINDOW_SEC=0.2 с) целиком: детектор ни разу не видел «занято»,
        и операция уходила в below_floor с результатом порядка нескольких
        миллисекунд — на той же самой операции, где другой прогон честно
        показывал ~48 сек. Прямая проверка файла на диске не зависит от того,
        успел ли опрос CPU поймать x2t: либо файл есть и дописан, либо нет.

        «Дописан» — размер не меняется OP_EXPORT_FILE_STABLE_CHECKS опросов
        подряд, а не просто наличие файла: x2t создаёт файл и заполняет его
        постепенно, голый exists() поймал бы файл нулевого/частичного размера
        и вернулся бы раньше, чем экспорт реально закончился.

        Вызывается СИНХРОННО внутри тест-функции, ДО _wait_operation_done —
        это не замена детектору, а подстраховка: время ожидания остаётся
        частью замера (не через self._pace()), потому что x2t в это время
        действительно работает.

        Args:
            path_str: Путь к ожидаемому файлу экспорта.
            timeout: Секунд ожидания. По умолчанию OP_EXPORT_FILE_TIMEOUT_SEC.
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            bool: True — файл появился и стабилизировался; False — таймаут.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if timeout is None:
            timeout = self.OP_EXPORT_FILE_TIMEOUT_SEC

        path = Path(path_str)
        deadline = time.perf_counter() + timeout
        last_size = None
        stable = 0
        op_start = getattr(self, "_op_started_at", None)
        while time.perf_counter() < deadline:
            # Ранний выход: x2t этой операции упал, а файла нет — ждать
            # остаток таймаута бессмысленно (раньше — молча 120 с).
            if op_start is not None and not path.exists():
                op_runs = self._x2t_since(op_start)
                failed = [r for r in op_runs if r.get("exit_code") not in (0, None)]
                # Р7 может перезапустить x2t (живой прогон ODS: второй x2t
                # стартовал за секунду до падения первого) — проваленным
                # экспорт считаем, только когда живых x2t этой операции нет.
                still_running = [r for r in op_runs if r.get("end") is None]
                if failed and not still_running:
                    code = failed[-1]["exit_code"] & 0xFFFFFFFF
                    self._export_fail_reason = (
                        f"конвертер x2t упал с кодом {code:#010x} — файл экспорта "
                        f"не записан (ошибка конвертера, а не инструмента)")
                    if code == 0xC0000409:
                        self._export_fail_reason += (
                            "; вероятная причина — лимит памяти x2t "
                            "(X2T_MEMORY_LIMIT, по умолчанию 4 ГБ)")
                    # Р7 показывает окно «Нельзя сохранить…» через 1–3 с после
                    # падения — ждём его, закрываем и кладём текст в ошибку.
                    alert_deadline = time.perf_counter() + self.ALERT_AFTER_X2T_CRASH_SEC
                    while time.perf_counter() < alert_deadline:
                        alerts = self._dismiss_info_alerts(log_cb)
                        if alerts:
                            self._export_fail_reason += "; Р7: " + " / ".join(
                                f"«{t}»" for t in alerts)
                            break
                        time.sleep(0.3)
                    log_cb(f"   ❌ {self._export_fail_reason}")
                    return False
            try:
                st = path.stat()
                size = st.st_size
            except OSError:
                st, size = None, None
            if size is not None and size > 0 and size == last_size:
                stable += 1
                if stable >= self.OP_EXPORT_FILE_STABLE_CHECKS:
                    # Конец экспорта — время ПОСЛЕДНЕЙ ЗАПИСИ файла (mtime),
                    # переведённое в шкалу perf_counter, а не момент, когда мы
                    # заметили стабильный размер: иначе в замер попадали окно
                    # стабильности и всё, что шло между Enter и этим вызовом
                    # (например, до 3 с _dismiss_saveas_format_warning на PDF,
                    # где предупреждения нет). Аудит 29.09.2026. Вызывающий
                    # воркер берёт self._op_completed_at вместо статуса
                    # below_floor детектора.
                    lag = max(0.0, time.time() - st.st_mtime)
                    self._op_completed_at = time.perf_counter() - lag
                    return True
            else:
                stable = 0
            last_size = size
            time.sleep(self.OP_EXPORT_FILE_POLL_SEC)

        log_cb(f"   ⚠️ Файл экспорта не появился/не стабилизировался за "
               f"{timeout:.0f} сек: {path.name}")
        return False
