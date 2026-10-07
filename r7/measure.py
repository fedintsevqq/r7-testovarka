"""Замер операции: цикл повторов, конец операции, ресурсы Р7, диск.

Главные правила (CLAUDE.md): замер отражает Р7, а не инструмент — свои
паузы только через _pace, они вычитаются; конец операции — простой Р7
(_wait_operation_done, на CDP-пути — пинг редактора), а не последнее
нажатие; только time.perf_counter(). MeasureMixin — методы, которые
R7Testovarka получает наследованием; пороги детектора — в OpEndMixin
(r7/op_end.py), здесь — только OP_KEY_PACE; читаются через self.
"""
import statistics
import time

from r7.processes import X2tTracker
from r7.resources import _disk_delta, _disk_snapshot
from r7.ux_metrics import UX_KEYS, aggregate_ux


class _RunAcc:
    """Накопленное по повторам одной операции (_measure_op_repeated)."""

    def __init__(self):
        self.pass_times = []
        self.run_statuses = []     # статус детектора на КАЖДЫЙ прогон: ok/below_floor/timeout/unverified
        self.runs_independent = True   # каждый повтор откатан к исходному документу
        self.run_res = []          # ресурсы Р7 за окно каждого прогона (OpResourceWatch)
        self.run_x2t = []          # сводка по x2t на каждый прогон (X2tTracker)
        self.alerts_seen = []      # тексты окон Р7, закрытых после успешных прогонов
        self.run_disk = []         # дисковая активность за окно каждого прогона
        self.api_ms_values = []    # синхронное время api по прогонам, ушедшим через CDP
        self.run_ux = []           # метрики интерфейса на каждый завершённый прогон (схема 10)
        self.error = None
        self.below_floor = False   # хоть один прогон оказался ниже порога измерения


class MeasureMixin:
    """Цикл повторов, детекторы конца операции, ресурсы — часть R7Testovarka."""

    OP_KEY_PACE         = 0.08   # пауза после клавиш, меняющих состояние (буфер, лист)



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

        На каждый повтор (_measure_one_run):
          * подготовка теста и ожидание простоя Р7 — вне замера;
          * снимок истории правок и база CPU — ДО секундомера;
          * секундомер: от вызова func до начала простоя Р7
            (_wait_operation_done, уточнённый _resolve_op_end), минус
            собственные паузы (_paced_total);
          * после замера — добивание модалки, отложенная CDP-проверка, пауза;
          * откат правок после каждого повтора — каждый повтор идёт на одном
            и том же документе, и следующий тест получает файл как есть.
        Итог (_op_record): медиана и MAD по годным повторам, ресурсы, x2t, диск.

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

        acc = _RunAcc()
        for i in range(runs):
            if stop_event is not None and stop_event.is_set():
                log_cb(f"⏹ {name}: остановлено пользователем "
                       f"(выполнено прогонов: {i}/{runs})")
                break
            if i > 0:
                log_cb(f"⏳ Тест: {name} (прогон {i + 1}/{runs})...")
            if not self._measure_one_run(acc, i, runs, name, func, find_hwnd, log_cb,
                                         stop_event, post_delay):
                break

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

        if not acc.pass_times:
            return self._failed_op_record(name, acc, log_cb)
        return self._op_record(name, acc, log_cb)

    def _measure_one_run(self, acc, i, runs, name, func, find_hwnd, log_cb, stop_event,
                         post_delay):
        """Один повтор: подготовка, секундомер, конец операции, проверки и откат.
        Пишет результат в acc. Returns: False — повтор оборвал серию (ошибка
        подготовки или операции, документ не изменился)."""
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
                acc.error = f"подготовка теста не удалась: {e}"
                log_cb(f"   ❌ прогон {i + 1}: {acc.error}")
                return False
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
        # Метрики интерфейса взводятся тоже ДО секундомера (r7/ux_metrics.py):
        # внутри замера операция только ставит метки времени.
        ux_state = self._ux_arm()
        watch = self._op_watch()
        watch.start()
        disk_before = _disk_snapshot()       # ~4 мс, до секундомера
        start = time.perf_counter()
        self._op_started_at = start          # для раннего выхода экспорта по x2t
        self._export_fail_reason = None
        try:
            func()
        except Exception as e:
            acc.error = str(e)
            watch.stop()
            self._ux_collect(ux_state)       # разоружить, метрики сбойного прогона не нужны
            acc.run_x2t.append(X2tTracker.summarize(self._x2t_since(start)))
            # Окно ошибки Р7 — часть диагноза: его текст идёт в ошибку прогона.
            alerts = self._dismiss_info_alerts(log_cb)
            if alerts:
                acc.error += "; Р7: " + " / ".join(f"«{t}»" for t in alerts)
            log_cb(f"   ❌ прогон {i + 1}: ошибка — {acc.error}")
            return False
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
        acc.run_res.append(watch.stop())
        acc.run_x2t.append(X2tTracker.summarize(self._x2t_since(start)))
        acc.run_disk.append(_disk_delta(disk_before, _disk_snapshot(),
                                        self._matches_r7_process, self._x2t_since(start)))
        # Предохранитель (живой прогон 29.09.2026): клавиатурный тест ВПР
        # три прогона подряд «работал» 0.34 с, а история правок не
        # сдвинулась ни разу — формула не вводилась, и цифра была временем
        # нажатий в пустоту. Операция, которая должна менять документ, но
        # не изменила его, — ошибка, а не результат. Проверка вне замера.
        if hist_before is not None and self._op_expects_change(name):
            hist_after = self._history_snapshot()
            if hist_after is not None and hist_after["index"] == hist_before["index"]:
                acc.error = ("операция не изменила документ (история правок не "
                             "сдвинулась) — замер недостоверен")
                self._ux_collect(ux_state)
                log_cb(f"   ❌ прогон {i + 1}: {acc.error}")
                return False
        if status == "timeout":
            elapsed = wait_end - start - self._paced_total
        else:
            elapsed = max(0.0, done_ts - start - self._paced_total)
        acc.pass_times.append(elapsed)
        acc.run_statuses.append(status)
        if self._op_via_cdp:
            acc.api_ms_values.append(self._cdp_api_ms)
        # Замер закрыт — только теперь добиваем модалку «Вставить ячейки»
        # и доводим отложенную проверку CDP-операции: их паузы и
        # round-trip не должны попадать в цифру.
        self._flush_pending_modal_confirm(log_cb=log_cb)
        self._flush_pending_cdp_verify(log_cb=log_cb)
        # Метрики интерфейса — тоже после конца замера (конец по-прежнему
        # даёт _wait_operation_done) и до отката: откат идёт через тот же
        # api и не должен попасть в метки повтора.
        acc.run_ux.append(self._ux_collect(ux_state, log_cb))
        if self._op_unverified and acc.run_statuses[-1] != "timeout":
            # Операция могла не выполниться вовсе (≈0 мс) — в медиану не
            # берём, как и таймаут (аудит 06.10.2026).
            acc.run_statuses[-1] = "unverified"
            log_cb(f"   ⚠️ прогон {i + 1}: результат не подтверждён "
                   f"({self._op_unverified}) — в статистику не входит")
        if getattr(self, "_pending_sheet_clip_mark", False):
            # Копия листа в буфере — запоминаем состояние буфера
            # (см. _paste_big_prepare). После замера: буфер дописан.
            self._pending_sheet_clip_mark = False
            self._sheet_clip_seq = self._clipboard_seq()
        run_alerts = self._dismiss_info_alerts(log_cb)
        if run_alerts:
            acc.alerts_seen.extend(run_alerts)
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
                acc.runs_independent = False
            elif restored is False:
                log_cb(f"   ⚠️ {name}: последнюю правку откатить не удалось — "
                       f"следующие тесты пойдут на изменённом документе")
        self._log_run(acc, i, elapsed, status, log_cb)
        return True

    def _log_run(self, acc, i, elapsed, status, log_cb):
        """Строка журнала по повтору; отмечает acc.below_floor."""
        # api_ms печатается рядом с elapsed (settle_ms), а не вместо него.
        _api_note = (f" [api: {self._cdp_api_ms:.2f} мс]"
                     if self._op_via_cdp else "")
        if status == "below_floor":
            # На CDP-пути это не «быстрее порога»: Р7 работал ВНУТРИ вызова
            # (asc_Paste на 50K строк — 28 с при 32 с процессорного
            # времени), и цифра — реальная. Пометка «<порога» в отчёте
            # висела на многосекундных операциях (30.09.2026).
            acc.below_floor = acc.below_floor or not self._op_via_cdp
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
        elif acc.run_statuses[-1] == "unverified":
            log_cb(f"   ⚠️ прогон {i + 1}: {elapsed:.3f} сек{_api_note} — не подтверждён")
        else:
            log_cb(f"   ✅ прогон {i + 1}: {elapsed:.3f} сек{_api_note}")

    def _failed_op_record(self, name, acc, log_cb):
        """Запись операции, у которой нет ни одного завершённого повтора."""
        return {"name": name, "time": 0.0, "error": acc.error,
                "ram": None, "cpu": None, "cpu_normalized": None,
                "cpu_sec": None, "cpu_peak_core_pct": None,
                "threads": None, "uptime_sec": None,
                "runs": [], "run_statuses": [],
                "avg": 0.0, "min": 0.0, "max": 0.0,
                "median": 0.0, "mad": 0.0, "n_runs": 0,
                "first_run_discarded": False, "n_timeouts": 0, "n_unverified": 0,
                "runs_independent": acc.runs_independent,
                "below_floor": False, "api_ms": None,
                **dict.fromkeys(UX_KEYS), "run_ux": [],
                "run_cpu_freq_pct": [], "run_notes": [], "n_throttled": 0,
                # Экспорт, у которого упал x2t, — именно здесь: код
                # конвертера нужен в отчёте, а не только в логе.
                "x2t": self._aggregate_x2t(acc.run_x2t, range(len(acc.run_x2t)), log_cb)}

    def _op_record(self, name, acc, log_cb):
        """Итог операции по завершённым повторам: медиана и MAD по годным,
        ресурсы, x2t, диск, окна Р7."""
        pass_times, run_statuses = acc.pass_times, acc.run_statuses
        error = acc.error
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
        res_agg = self._aggregate_op_resources(acc.run_res, stats_idx)
        if n_timeouts:
            log_cb(f"   ⚠️ {n_timeouts} прогон(ов) с таймаутом исключены из "
                   f"статистики: их время — предохранитель, а не длительность")
        median_t = statistics.median(stats_times)
        mad_t = self._mad(stats_times)
        ux_agg = aggregate_ux(acc.run_ux, stats_idx)
        run_freq, run_notes = self._throttle_notes(acc, log_cb)

        # Среднее api_ms — только по прогонам через CDP.
        avg_api_ms = (round(sum(acc.api_ms_values) / len(acc.api_ms_values), 3)
                      if acc.api_ms_values else None)
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
            "runs_independent": acc.runs_independent,
            "below_floor": acc.below_floor, "api_ms": avg_api_ms,
            "x2t": self._aggregate_x2t(acc.run_x2t, stats_idx, log_cb),
            "r7_alerts": acc.alerts_seen,
            "disk": self._aggregate_disk(acc.run_disk, stats_idx, log_cb),
            # Схема 10: что видит пользователь (медианы по тем же прогонам,
            # что и время) и частота CPU с пометкой троттлинга по повторам.
            **ux_agg, "run_ux": list(acc.run_ux),
            "run_cpu_freq_pct": run_freq, "run_notes": run_notes,
            "n_throttled": sum(1 for n in run_notes if "throttle" in n),
        }

    def _throttle_notes(self, acc, log_cb):
        """Частота CPU за окно каждого повтора и пометка «throttle», если
        она ниже CPU_THROTTLE_PCT номинальной. В медиану такие повторы
        входят как обычно — пометка объясняет выброс, а не прячет его.

        Returns:
            tuple[list[float | None], list[list[str]]]: по повторам.
        """
        limit = getattr(self, "CPU_THROTTLE_PCT", None)
        freqs, notes = [], []
        for i in range(len(acc.pass_times)):
            res = acc.run_res[i] if i < len(acc.run_res) else None
            f = (res or {}).get("cpu_freq_min_pct")
            freqs.append(f)
            if limit is not None and isinstance(f, (int, float)) and f < limit:
                notes.append(["throttle"])
                log_cb(f"   🐢 прогон {i + 1}: частота CPU опускалась до {f:.0f}% "
                       f"номинальной — возможен троттлинг")
            else:
                notes.append([])
        return freqs, notes

    def _op_expects_change(self, name):
        """True — после операции в истории правок должна появиться точка."""
        return not any(m in name for m in self.NON_MUTATING_MARKERS)


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


