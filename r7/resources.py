"""Ресурсы Р7 во время замера: CPU, память, потоки, диск, окружение стенда.

CPU — в % ОДНОГО ядра, без нормировки на число ядер. Диск — счётчики
ввода-вывода системы и процессов Р7 до и после операции; x2t учитывается
отдельно (X2tTracker). ResourcesMixin — методы, которые R7Testovarka
получает наследованием.
"""
import os
import platform
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time

from r7 import build_meta, calibration, cpu_freq, env, fingerprint
from r7.env import psutil


class ResourceSampler(threading.Thread):
    """Фоновый семплер RAM/JS-heap процессов Р7 — снимает точку раз в
    interval секунд, пока не остановлен, независимо от того, что в этот
    момент делает основной поток теста.

    ЗАЧЕМ ОТДЕЛЬНЫЙ ПОТОК, А НЕ ЗАМЕР В КОНЦЕ ТЕСТА: утечка — это НАКЛОН
    ряда во времени, а не одна точка. Существующие _sample_r7_resources
    снимают RAM один раз после каждой операции — этого достаточно для
    отчёта по одному прогону, но не для soak-теста в несколько часов,
    где интересен именно дрейф.

    ЗАЧЕМ JS-HEAP, А НЕ ТОЛЬКО RSS ПРОЦЕССА: утечку внутри самого редактора
    RSS процесса маскирует поведением аллокатора — память, освобождённая
    JS, не всегда сразу возвращается ОС (см. Performance.getMetrics в
    r7_webdriver_connector.py). RSS снимается всегда (psutil, не требует
    CDP); JS-heap — только если передан подключённый коннектор.

    Использование:
        sampler = ResourceSampler(get_procs=self._get_r7_processes,
                                   connector=self._webdriver_connector)
        sampler.start()
        ...
        sampler.stop()
        sampler.join(timeout=5)
        verdict = detect_leak(sampler.snapshot())
    """

    def __init__(self, get_procs, connector=None, interval=1.0, log_cb=None,
                 freq_probe=None):
        """Args:
            get_procs: Callable() -> list[psutil.Process] — например,
                self._get_r7_processes. Вызывается заново на каждом
                замере (не список, зафиксированный один раз при
                создании) — процессы Р7 могут появляться/исчезать
                (x2t, новые окна) за время жизни семплера.
            connector: R7WebDriverConnector текущего запуска, либо None —
                тогда собирается только rss_mb, heap_mb/doc_count всегда
                None.
            interval: Пауза между замерами, сек.
            log_cb: Функция логирования; по умолчанию no-op (сэмплер живёт
                в отдельном потоке, лишний шум на каждую секунду soak-теста
                не нужен — ошибки одного замера не логируются, только
                накапливаются как None в ряду).
            freq_probe: r7.cpu_freq.CpuFreqProbe — частота CPU в % номинальной
                (cpu_freq_pct в каждой точке); None — поле всегда None.
        """
        super().__init__(daemon=True)
        self._get_procs = get_procs
        self._connector = connector
        self._freq_probe = freq_probe
        self._interval = interval
        self.log_cb = log_cb or (lambda msg: None)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.samples = []

    def run(self):
        # Первый замер — сразу, не через interval секунд: иначе короткий
        # прогон (соак остановили раньше) рискует не набрать ни одной точки.
        self._sample_once()
        while not self._stop_event.wait(self._interval):
            self._sample_once()

    def _sample_once(self):
        """Снимает одну точку ряда. Ошибка одного источника (psutil упал,
        CDP не ответил) не должна ронять весь поток — соответствующее поле
        остаётся None, семплер продолжает работать дальше."""
        row = {"t": time.time(), "rss_mb": None, "heap_mb": None, "doc_count": None,
               "cpu_freq_pct": None}
        if self._freq_probe is not None:
            try:
                row["cpu_freq_pct"] = self._freq_probe.sample()
            except Exception:  # частота — необязательное поле ряда
                row["cpu_freq_pct"] = None
        try:
            procs = self._get_procs()
            if procs:
                row["rss_mb"] = sum(p.memory_info().rss for p in procs) / (1024 * 1024)
        except Exception as e:
            self.log_cb(f"⚠️ ResourceSampler: RSS не снят ({type(e).__name__}: {e})")

        if self._connector is not None and getattr(self._connector, "connected", False):
            try:
                metrics = self._connector.performance_metrics(timeout=2.0)
            except Exception as e:
                metrics = None
                self.log_cb(f"⚠️ ResourceSampler: JS-heap не снят ({type(e).__name__}: {e})")
            if metrics:
                if "JSHeapUsedSize" in metrics:
                    row["heap_mb"] = metrics["JSHeapUsedSize"] / (1024 * 1024)
                if "Documents" in metrics:
                    row["doc_count"] = metrics["Documents"]

        with self._lock:
            self.samples.append(row)

    def stop(self):
        """Останавливает цикл run(). Не блокирует — вызывающий код сам
        решает, ждать ли завершения потока (join)."""
        self._stop_event.set()

    def snapshot(self):
        """Копия накопленного ряда замеров — безопасно вызывать из другого
        потока, пока семплер продолжает работать (список копируется под
        тем же _lock, которым run() защищает append)."""
        with self._lock:
            return list(self.samples)


def _disk_snapshot():
    """Снимок дисковой активности (29.09.2026): физический диск системы и
    ввод-вывод каждого процесса.

    Нужен, чтобы отличать медленную работу Р7 от занятого диска: в полном
    прогоне конвертация .xlsx при открытии шла 8.9 / 6.8 / 3.9 с при
    простаивающем CPU, и без счётчиков диска причину было не установить.
    Снимок по ~340 процессам стоит ~4 мс (замерено), поэтому делается до
    секундомера и после замера, а не в опросе.

    Две разные величины, их нельзя вычитать друг из друга:
      sys   — psutil.disk_io_counters(): ФИЗИЧЕСКОЕ чтение/запись диска;
      procs — Process.io_counters(): весь ввод-вывод процесса, включая
              чтение из файлового кэша.

    Returns:
        dict | None: {"t", "sys": (read_bytes, write_bytes), "procs":
        {pid: (name, read_bytes, write_bytes)}}; None без psutil.
    """
    if not env.PSUTIL_OK:
        return None
    snap = {"t": time.perf_counter(), "sys": None, "procs": {}}
    try:
        c = psutil.disk_io_counters()
        snap["sys"] = (c.read_bytes, c.write_bytes)
    except Exception:  # нет счётчиков диска — фон диска в отчёте будет пустым
        pass
    for p in psutil.process_iter(["name"]):
        try:
            io = p.io_counters()
            snap["procs"][p.pid] = ((p.info.get("name") or "?"), io.read_bytes, io.write_bytes)
        except Exception:  # процесс завершился или закрыт — его ввод-вывод не нужен
            pass
    return snap


def _disk_delta(a, b, is_r7_name, x2t_runs=None, top_n=3):
    """Дисковая активность между двумя снимками _disk_snapshot.

    Процессы Р7 — по имени (is_r7_name). x2t, умерший внутри окна, в снимке
    «после» уже не виден — его ввод-вывод берётся из X2tTracker (x2t_runs).
    Процесс, родившийся внутри окна, учитывается с нуля.

    Returns:
        dict | None: {"sec", "sys_read_mb", "sys_write_mb", "sys_mb_per_sec",
        "r7_read_mb", "r7_write_mb", "top_other": [{"name", "read_mb",
        "write_mb"}]}.
    """
    if not a or not b:
        return None
    mb = 2 ** 20
    dur = max(1e-6, b["t"] - a["t"])
    out = {"sec": round(dur, 3), "sys_read_mb": None, "sys_write_mb": None,
           "sys_mb_per_sec": None}
    if a["sys"] and b["sys"]:
        r = max(0, b["sys"][0] - a["sys"][0]) / mb
        w = max(0, b["sys"][1] - a["sys"][1]) / mb
        out.update(sys_read_mb=round(r, 1), sys_write_mb=round(w, 1),
                   sys_mb_per_sec=round((r + w) / dur, 1))
    r7_r = r7_w = 0.0
    others = {}
    for pid, (name, rb, wb) in b["procs"].items():
        prev = a["procs"].get(pid)
        if prev is not None and prev[0] == name:
            dr, dw = max(0, rb - prev[1]), max(0, wb - prev[2])
        else:
            dr, dw = rb, wb                       # родился внутри окна
        if is_r7_name(name.lower()):
            r7_r += dr
            r7_w += dw
        elif dr + dw > 0:
            # По имени: у одного приложения бывает несколько процессов
            # (Termius, Chrome) — в «фоне» они шли отдельными строками.
            prev_r, prev_w = others.get(name, (0, 0))
            others[name] = (prev_r + dr, prev_w + dw)
    live = set(b["procs"])
    for run in x2t_runs or []:
        if run.get("pid") not in live:           # x2t умер внутри окна
            r7_r += (run.get("io_read_mb") or 0.0) * mb
            r7_w += (run.get("io_write_mb") or 0.0) * mb
    out["r7_read_mb"] = round(r7_r / mb, 1)
    out["r7_write_mb"] = round(r7_w / mb, 1)
    ranked = sorted(others.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
    out["top_other"] = [{"name": n, "read_mb": round(r / mb, 1), "write_mb": round(w / mb, 1)}
                        for n, (r, w) in ranked[:top_n] if (r + w) >= mb]
    return out


def _format_disk(d):
    """Строка лога по _disk_delta."""
    if not d:
        return ""
    parts = []
    if d.get("sys_read_mb") is not None:
        parts.append(f"диск: чтение {d['sys_read_mb']:.0f} МБ, запись {d['sys_write_mb']:.0f} МБ "
                     f"({d['sys_mb_per_sec']:.0f} МБ/с)")
    parts.append(f"Р7: чтение {d['r7_read_mb']:.0f} МБ, запись {d['r7_write_mb']:.0f} МБ")
    if d.get("top_other"):
        parts.append("фон: " + ", ".join(
            f"{o['name']} {o['read_mb'] + o['write_mb']:.0f} МБ" for o in d["top_other"]))
    return "; ".join(parts)


class OpResourceWatch(threading.Thread):
    """Ресурсы Р7 за окно ОДНОЙ операции (аудит 29.09.2026, пункт 10).

    Раньше RAM/CPU операции снимались ПОСЛЕ неё (после post_action_delay),
    когда Р7 уже простаивал, а cpu_percent(interval=0.1) по процессам шёл
    последовательно — N разных окон и ~1 с блокировки. Колонка CPU отчёта
    была шумом.

    Здесь поток живёт от старта до конца замера и копит:
      cpu_sec           — процессорное время всех процессов Р7 за операцию
                          (user+system из cpu_times, счётчики ОС). Не зависит
                          ни от опроса, ни от детектора простоя — самая
                          воспроизводимая из ресурсных метрик;
      cpu_peak_core_pct — пик суммарной загрузки, % одного ядра;
      cpu_avg_core_pct  — cpu_sec / длительность окна, % одного ядра;
      ram_peak_mb       — пик суммарного RSS;
      cpu_freq_min_pct  — минимум частоты CPU, % номинальной (r7.cpu_freq),
                          по интервалам ~0.5 с и хвосту до stop(); None —
                          частоту снять нечем. Ниже порога — троттлинг.
    Процесс, родившийся внутри окна (x2t), учитывается с нуля; умерший —
    по последнему прочитанному значению.
    """

    FREQ_EVERY_POLLS = 5      # частота — раз в 5 опросов (~0.5 с)

    def __init__(self, get_procs, interval=0.1, freq_probe=None):
        super().__init__(daemon=True)
        self._get_procs = get_procs
        self._interval = interval
        self._freq_probe = freq_probe
        self._freq_min = None
        self._stop_event = threading.Event()
        self._base = {}       # pid -> cpu-секунды на старте (0 для родившихся позже)
        self._last = {}       # pid -> последние прочитанные cpu-секунды
        self._procs = {}      # pid -> psutil.Process
        self._peak_core = 0.0
        self._peak_rss = 0.0
        self._t0 = None
        self._t1 = None
        self._first_scan = True

    @staticmethod
    def _cpu_s(p):
        t = p.cpu_times()
        return t.user + t.system

    def _scan(self):
        try:
            procs = self._get_procs() or []
        except Exception:
            procs = []
        for p in procs:
            if p.pid in self._procs:
                continue
            try:
                self._procs[p.pid] = p
                cur = self._cpu_s(p)
                self._base[p.pid] = cur if self._first_scan else 0.0
                self._last[p.pid] = cur
                p.cpu_percent(None)
            except Exception:
                self._procs.pop(p.pid, None)
        self._first_scan = False

    def _poll(self):
        core = 0.0
        rss = 0.0
        for pid, p in list(self._procs.items()):
            try:
                self._last[pid] = self._cpu_s(p)
                core += p.cpu_percent(None)
                rss += p.memory_info().rss
            except Exception:
                self._procs.pop(pid, None)   # умер — остаётся последнее значение
        self._peak_core = max(self._peak_core, core)
        self._peak_rss = max(self._peak_rss, rss)

    def start(self):
        # Базовая линия снимается синхронно в вызывающем потоке, ДО старта
        # секундомера операции — иначе первые миллисекунды работы Р7
        # попали бы в базу.
        self._t0 = time.perf_counter()
        self._scan()
        self._sample_freq(record=False)   # начало интервала счётчика — до секундомера
        super().start()

    def _sample_freq(self, record=True):
        """Точка частоты CPU; record=False — только сдвинуть начало интервала
        PDH-счётчика (значение относится ко времени ДО окна операции)."""
        if self._freq_probe is None:
            return
        try:
            pct = self._freq_probe.sample()
        except Exception:  # частота — необязательная метрика окна
            return
        if record and pct is not None:
            self._freq_min = pct if self._freq_min is None else min(self._freq_min, pct)

    def run(self):
        n = 0
        while not self._stop_event.wait(self._interval):
            n += 1
            if n % 5 == 0:
                self._scan()        # ловим x2t, запущенный внутри операции
            self._poll()
            if n % self.FREQ_EVERY_POLLS == 0:
                self._sample_freq()

    def stop(self):
        """Останавливает наблюдение и возвращает итог окна.

        Returns:
            dict | None: None — ни один процесс Р7 так и не попал под
            наблюдение (не найден или нет доступа). Прежде это давало
            cpu_sec 0 и средний CPU 0 % — неотличимо от «Р7 простаивал»
            (аудит 06.10.2026); _aggregate_op_resources пустые окна пропускает.
        """
        self._stop_event.set()
        self.join(timeout=2)
        self._scan()
        self._poll()
        self._sample_freq()               # хвост окна после последней точки
        self._t1 = time.perf_counter()
        if not self._last:
            return None
        cpu_sec = sum(max(0.0, self._last[pid] - self._base.get(pid, 0.0))
                      for pid in self._last)
        dur = max(1e-6, self._t1 - self._t0)
        return {
            "cpu_sec": round(cpu_sec, 3),
            "cpu_peak_core_pct": round(self._peak_core, 1),
            "cpu_avg_core_pct": round(cpu_sec / dur * 100.0, 1),
            "ram_peak_mb": round(self._peak_rss / (1024 * 1024), 1) if self._peak_rss else None,
            "cpu_freq_min_pct": self._freq_min,
        }


class ResourcesMixin:
    """Ресурсы Р7, диск и окружение стенда — часть R7Testovarka (через наследование)."""

    def _cpu_count(self):
        """Число логических ядер, закэшированное на экземпляр.

        `psutil.cpu_count()` не меняется на живой машине за время одного
        прогона, а вызывающий код (детекторы простоя) опрашивает его на
        каждой итерации цикла (каждые 0.05–0.15 с) — кэш убирает системный
        вызов из горячего пути. `or 1` — на случай, если psutil не смог
        определить число ядер (документированная возможность API, а не
        гипотетический случай): без подстраховки деление на None упало бы.

        Тот же паттерн, что у `_find_r7_path`/`self._cached_r7_path` —
        прямая проверка атрибута, выставленного в `__init__`, а не
        `getattr(..., None)`.

        Returns:
            int: число логических ядер, минимум 1.
        """
        if self._cached_cpu_count:
            return self._cached_cpu_count
        self._cached_cpu_count = (psutil.cpu_count() or 1) if env.PSUTIL_OK else 1
        return self._cached_cpu_count

    def _wait_system_quiet(self, log_cb=None):
        """Ждёт, пока система успокоится, перед холодным стартом Р7.

        Сразу после закрытия предыдущего экземпляра система ещё занята (Р7
        дописывает кэши, антивирус проверяет записанное), и повторы
        «Открытия файла» внутри воркера разъезжались на 1.6 с (8.36 / 9.29 /
        7.65), а с паузой между запусками — 7.78–8.09 с (живой прогон
        29.09.2026). Ждём, пока загрузка системы не опустится ниже
        ENV_BUSY_SYSTEM_CPU_PCT, но не дольше QUIET_SYSTEM_MAX_WAIT_SEC.

        Returns:
            float | None: загрузка системы на момент старта, %.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if not env.PSUTIL_OK:
            return None
        deadline = time.perf_counter() + self.QUIET_SYSTEM_MAX_WAIT_SEC

        def _sample():
            # CPU и физический диск за одно и то же окно 0.5 с.
            try:
                d0 = psutil.disk_io_counters()
            except Exception:
                d0 = None
            cpu = psutil.cpu_percent(interval=0.5)
            disk = 0.0
            if d0 is not None:
                try:
                    d1 = psutil.disk_io_counters()
                    disk = ((d1.read_bytes - d0.read_bytes) + (d1.write_bytes - d0.write_bytes)) / 2**20 / 0.5
                except Exception:  # семплер: при сбое диск за этот шаг считаем нулём
                    pass
            return cpu, disk

        load, disk = _sample()
        while ((load >= self.ENV_BUSY_SYSTEM_CPU_PCT or disk >= self.QUIET_DISK_MB_PER_SEC)
               and time.perf_counter() < deadline):
            load, disk = _sample()
        if load >= self.ENV_BUSY_SYSTEM_CPU_PCT or disk >= self.QUIET_DISK_MB_PER_SEC:
            log_cb(f"   ⚠️ Система не успокоилась за {self.QUIET_SYSTEM_MAX_WAIT_SEC:.0f} с "
                   f"(CPU {load:.0f}%, диск {disk:.0f} МБ/с) — холодный старт на занятой системе")
        return load

    # Метка отчёта («Условия прогона»), когда прогон шёл без прав
    # администратора и файловый кэш ОС перед открытием не сбрасывался.
    NO_ADMIN_CACHE_WARNING = ("Запуск без прав администратора: файловый кэш ОС не "
                              "сбрасывался — открытие файла могло быть тёплым")

    def _note_os_cache_not_purged(self):
        """Запоминает, что кэш ОС не сброшен из-за прав, и добавляет метку в
        предупреждения окружения прогона. Окружение снимается до сброса кэша
        (_capture_environment идёт первым во всех трёх воркерах), поэтому
        метка дописывается в уже снятый снимок; повторный вызов её не дублирует."""
        self._os_cache_not_purged = True
        env_info = getattr(self, "_run_environment", None)
        if isinstance(env_info, dict):
            warnings = env_info.setdefault("warnings", [])
            if self.NO_ADMIN_CACHE_WARNING not in warnings:
                warnings.append(self.NO_ADMIN_CACHE_WARNING)

    def _capture_environment(self, log_cb=None):
        """Снимок окружения ДО запуска Р7 (аудит 29.09.2026, пункт 11).

        Прежде в отчёт шли только ОС/RAM/модель CPU, и прогон на ноутбуке от
        батареи, в экономичном плане питания или с идущей в фоне сборкой был
        неотличим от чистого. Секунда замера системной загрузки — вне замеров.

        Returns:
            dict: system_cpu_pct, top_processes, ram_available_gb,
            cpu_freq_mhz, power_plan, on_ac_power, warnings, disk_free_gb,
            fingerprint, fingerprint_hash (r7.fingerprint), calibration
            (r7.calibration: cpu_ms, disk_mb_s).
        """
        if log_cb is None:
            log_cb = self.add_test_log
        self._interference = {}                # вмешательства стенда — заново на каждый прогон
        self._os_cache_not_purged = False      # выставит _purge_os_file_cache, если прав нет
        info = {"system_cpu_pct": None, "top_processes": [], "disk_background": None,
               "ram_available_gb": None,
               "cpu_freq_mhz": None, "power_plan": None, "on_ac_power": None,
               "warnings": []}
        if env.PSUTIL_OK:
            try:
                procs = list(psutil.process_iter(["name"]))
                for p in procs:
                    try:
                        p.cpu_percent(None)
                    except Exception:  # процесс завершился — в топ фоновых не попадёт
                        pass
                _d0 = _disk_snapshot()
                info["system_cpu_pct"] = psutil.cpu_percent(interval=1.0)
                info["disk_background"] = _disk_delta(_d0, _disk_snapshot(),
                                                     self._matches_r7_process)
                top = []
                for p in procs:
                    try:
                        if p.pid == 0:
                            continue
                        top.append((p.cpu_percent(None), p.info.get("name") or "?"))
                    except Exception:  # процесс завершился за секунду замера — в топ не попадёт
                        pass
                top.sort(reverse=True)
                info["top_processes"] = [{"name": n, "cpu_core_pct": round(c, 1)}
                                        for c, n in top[:5] if c > 0]
            except Exception as e:
                log_cb(f"   ⚠️ Фоновая загрузка CPU не замерена ({type(e).__name__}: {e})")
            try:
                info["ram_available_gb"] = round(psutil.virtual_memory().available / 1024**3, 1)
            except Exception as e:
                log_cb(f"   ⚠️ Свободная RAM не прочиталась ({type(e).__name__}: {e})")
            try:
                f = psutil.cpu_freq()
                if f:
                    info["cpu_freq_mhz"] = {"current": f.current, "max": f.max}
            except Exception as e:
                log_cb(f"   ⚠️ Частота CPU не прочиталась ({type(e).__name__}: {e})")
            try:
                b = psutil.sensors_battery()
                info["on_ac_power"] = None if b is None else bool(b.power_plugged)
            except Exception as e:
                log_cb(f"   ⚠️ Питание от сети или батареи не прочиталось "
                       f"({type(e).__name__}: {e})")
        try:
            out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True,
                                 timeout=5).stdout.decode("cp866", errors="replace")
            # «GUID схемы питания: ...  (GameTurbo (High Performance))» — имя
            # может само содержать скобки, поэтому берём всё между первой «(»
            # и последней «)».
            out = out.strip()
            info["power_plan"] = (out.split("(", 1)[1].rsplit(")", 1)[0]
                                 if "(" in out else out or None)
        except Exception as e:
            log_cb(f"   ⚠️ План питания не прочитался ({type(e).__name__}: {e})")

        if info["system_cpu_pct"] is not None and info["system_cpu_pct"] > self.ENV_BUSY_SYSTEM_CPU_PCT:
            names = ", ".join(t["name"] for t in info["top_processes"][:3])
            info["warnings"].append(
                f"фоновая загрузка системы {info['system_cpu_pct']:.0f}% ({names})")
        _db = info.get("disk_background") or {}
        if (_db.get("sys_mb_per_sec") or 0) > self.QUIET_DISK_MB_PER_SEC:
            names = ", ".join(o["name"] for o in _db.get("top_other") or [])
            info["warnings"].append(f"фоновая работа с диском {_db['sys_mb_per_sec']:.0f} МБ/с"
                                   + (f" ({names})" if names else ""))
        # План питания до прогона и на время прогона (r7.stand): окружение
        # снимается уже после переключения, power_plan выше — «во время».
        _plan_env = getattr(self, "_power_plan_environment", None)
        info.update(_plan_env() if _plan_env else
                    {"power_plan_before": None, "power_plan_during": None})
        info["disk_free_gb"] = self._work_disks_free_gb()
        for _drive, _free in info["disk_free_gb"].items():
            if _free < self.ENV_MIN_FREE_DISK_GB:
                info["warnings"].append(
                    f"на диске {_drive} свободно {_free:.1f} ГБ — Р7 пишет туда сотни "
                    f"мегабайт при открытии и экспорте; при нехватке места конвертер "
                    f"падает, а запись замедляется")
        if info["on_ac_power"] is False:
            info["warnings"].append("ноутбук работает от батареи")
        if info["power_plan"] and re.search(r"эконом|saver|balanced|сбаланс",
                                           info["power_plan"], re.I):
            info["warnings"].append(f"план питания «{info['power_plan']}» — частота CPU плавает")
        for w in info["warnings"]:
            log_cb(f"⚠️ Окружение: {w} — цифры прогона могут быть завышены и шумными")
        _db = info.get("disk_background")
        log_cb(f"🖥 Окружение: CPU системы {info['system_cpu_pct']}%, "
               f"план питания «{info['power_plan']}», свободно RAM {info['ram_available_gb']} ГБ"
               + (f"; фон {_format_disk(_db)}" if _db else ""))
        # Отпечаток машины и калибровка — после секунды замера фоновой
        # загрузки (нагрузка калибровки попала бы в system_cpu_pct) и до
        # _wait_system_quiet: тот дождётся, пока диск после записи 64 МБ
        # успокоится, и в открытие файла калибровка не попадёт.
        info["fingerprint"] = self._machine_fingerprint(info.get("power_plan"))
        info["fingerprint_hash"] = fingerprint.fingerprint_hash(info["fingerprint"])
        info["calibration"] = self._calibrate_stand(log_cb)
        # sha256 exe (~50 МБ) — здесь же, вне измеряемых окон; в отчёт попадёт
        # из кэша (_build_metadata), даже если Р7 к тому времени ещё открыт.
        build_meta.exe_sha256(getattr(self, "_cached_r7_path", None))
        return info

    def _machine_fingerprint(self, power_plan=None):
        """Словарь отпечатка машины (r7.fingerprint.collect): модель CPU, ядра,
        RAM, ОС, масштаб экрана, диски с данными Р7 и с отчётами, план
        питания. Каждое поле берётся независимо: что не прочиталось — None,
        хэш всё равно считается."""
        ram_gb = None
        if env.PSUTIL_OK:
            try:
                ram_gb = psutil.virtual_memory().total / 1024 ** 3
            except Exception:  # поле отпечатка, не условие прогона
                ram_gb = None
        try:
            dpi = self._get_dpi_scale_pct()
        except Exception:
            dpi = None
        try:
            cpu_logical = self._cpu_count()
        except Exception:
            cpu_logical = None
        return fingerprint.collect(
            cpu_model=platform.processor() or None,
            cpu_logical=cpu_logical,
            ram_gb=ram_gb,
            os_name=platform.platform(),
            dpi_scale_pct=dpi,
            r7_data_drive=fingerprint.drive_of(os.environ.get("LOCALAPPDATA")),
            reports_drive=fingerprint.drive_of(getattr(self, "reports_folder", None)),
            power_plan=power_plan,
        )

    def _calibrate_stand(self, log_cb=None):
        """Калибровка стенда (r7.calibration) один раз на прогон: CPU-индекс и
        скорость диска папки отчётов. Одна строка в журнал; сбой — None в
        полях, прогон идёт дальше."""
        if log_cb is None:
            log_cb = self.add_test_log
        folder = getattr(self, "reports_folder", None) or tempfile.gettempdir()
        cal = calibration.calibrate(folder)
        text = calibration.format_calibration(cal)
        log_cb(f"🧪 Калибровка стенда: {text}" if text
               else "⚠️ Калибровка стенда не удалась — индексы в отчёте пустые")
        return cal

    @staticmethod
    def _work_disks_free_gb():
        """Свободное место на дисках, куда пишет Р7 во время прогона: папка
        данных Р7 (%LOCALAPPDATA%, там recover с Editor.bin) и %TEMP% (файлы
        экспорта). На стенде с 1.4 ГБ свободных на C: третий экспорт в XLTX
        упал в конвертере с кодом 0x50 (30.09.2026).

        Returns:
            dict: {"C:": свободно_ГБ, ...} — по одному значению на диск.
        """
        free = {}
        for var in ("LOCALAPPDATA", "TEMP"):
            path = os.environ.get(var)
            if not path:
                continue
            drive = os.path.splitdrive(path)[0].upper() or path
            if drive in free:
                continue
            try:
                free[drive] = round(shutil.disk_usage(path).free / (1024 ** 3), 1)
            except OSError:  # диск недоступен — в отчёте просто не будет этого диска
                pass
        return free

    @staticmethod
    def _aggregate_disk(run_disk, idx, log_cb=None):
        """Сводка диска по операции: медианы по прогонам статистики и
        посторонние процессы, заметно работавшие с диском в любом прогоне.

        Returns:
            dict | None
        """
        rows = [run_disk[i] for i in idx if i < len(run_disk) and run_disk[i]]
        if not rows:
            return None

        def med(key):
            vals = [r[key] for r in rows if r.get(key) is not None]
            return round(statistics.median(vals), 1) if vals else None
        agg = {k: med(k) for k in ("sys_read_mb", "sys_write_mb", "sys_mb_per_sec",
                                   "r7_read_mb", "r7_write_mb")}
        other = {}
        for r in run_disk:
            for o in (r or {}).get("top_other") or []:
                other[o["name"]] = max(other.get(o["name"], 0.0), o["read_mb"] + o["write_mb"])
        agg["top_other"] = [{"name": n, "max_mb": round(v, 1)}
                            for n, v in sorted(other.items(), key=lambda kv: -kv[1])[:3]]
        if log_cb is not None:
            line = (f"   💽 Диск (медиана): чтение {agg['sys_read_mb']} МБ, запись "
                    f"{agg['sys_write_mb']} МБ ({agg['sys_mb_per_sec']} МБ/с); Р7 чтение "
                    f"{agg['r7_read_mb']} МБ, запись {agg['r7_write_mb']} МБ")
            if agg["top_other"]:
                line += "; фон: " + ", ".join(f"{o['name']} до {o['max_mb']:.0f} МБ"
                                             for o in agg["top_other"])
            log_cb(line)
        return agg

    def _open_disk_wait_note(self, opens):
        """Пометка «открытие зависело от диска» по повторам открытия.

        При открытии .xlsx конвертер x2t пишет сотни мегабайт в папку данных
        Р7 (%LOCALAPPDATA%\\R7-Office\\Editors\\data\\recover). На стенде, где
        она лежит на нестабильном SATA-SSD, сброс 330 МБ занимал то 0.8 с,
        то 4.7 с — и x2t работал то 4 с, то 8–9 с при одном и том же
        процессорном времени 2.9 с (30.09.2026). Разница «время работы минус
        процессорное время» — ожидание ввода-вывода; если она гуляет между
        открытиями, разброс времени открытия вызван диском, а не Р7.

        Args:
            opens: записи повторов открытия с ключом "x2t" (X2tTracker.summarize).

        Returns:
            tuple[str | None, list]: (текст пометки, ожидание x2t по повторам).
        """
        waits = []
        for o in opens:
            x = o.get("x2t") or {}
            sec, cpu = x.get("sec"), x.get("cpu_sec")
            if x.get("count") and isinstance(sec, (int, float)) and isinstance(cpu, (int, float)):
                waits.append(round(max(0.0, sec - cpu), 3))
            else:
                waits.append(None)
        known = [w for w in waits if w is not None]
        note = None
        if len(known) >= 2 and max(known) - min(known) >= self.OPEN_DISK_WAIT_SPREAD_SEC:
            note = (f"открытие зависело от диска: конвертер x2t ждал ввода-вывода от "
                    f"{min(known):.1f} до {max(known):.1f} с при одинаковой работе "
                    f"процессора — разброс вызван диском с данными Р7, а не самим Р7")
        return note, waits

    def _x2t_since(self, mark):
        """Запуски x2t с момента mark, либо [] если отслеживатель не работает."""
        tracker = getattr(self, "_x2t_tracker", None)
        return tracker.since(mark) if tracker is not None else []

    def _op_watch(self):
        """Наблюдатель ресурсов на одну операцию (см. OpResourceWatch)."""
        return OpResourceWatch(
            lambda: self._get_r7_processes(log_cb=lambda *_a: None, fresh=True),
            freq_probe=self._op_freq_probe())

    def _op_freq_probe(self):
        """Зонд частоты CPU для наблюдателей операций — один на экземпляр
        (PDH-запрос открывается один раз, у семплера прогона — свой)."""
        probe = getattr(self, "_cpu_freq_probe", None)
        if probe is None:
            probe = self._cpu_freq_probe = cpu_freq.CpuFreqProbe()
        return probe

    def _aggregate_op_resources(self, run_res, idx):
        """Сводит ресурсы прогонов в поля результата операции.

        cpu_sec и средний CPU — медиана по прогонам статистики, пики — максимум
        по ним же. Поля ram/cpu/cpu_normalized сохранены ради совместимости
        отчётов, но теперь это пик RSS и средний CPU ЗА ОКНО операции, а не
        снимок после неё.
        """
        rows = [run_res[i] for i in idx if i < len(run_res) and run_res[i]]
        if not rows:
            return {"cpu_sec": None, "cpu_peak_core_pct": None, "ram": None,
                    "cpu": None, "cpu_normalized": None}
        cpu_avg = statistics.median(r["cpu_avg_core_pct"] for r in rows)
        rams = [r["ram_peak_mb"] for r in rows if r["ram_peak_mb"] is not None]
        return {
            "cpu_sec": round(statistics.median(r["cpu_sec"] for r in rows), 3),
            "cpu_peak_core_pct": max(r["cpu_peak_core_pct"] for r in rows),
            "ram": max(rams) if rams else None,
            "cpu": round(cpu_avg, 1),
            "cpu_normalized": round(cpu_avg / self._cpu_count(), 1),
        }

    def _sample_r7_resources(self, procs, measure_cpu=True):
        """Снимает агрегированные метрики RAM/CPU/потоков/аптайма по списку процессов Р7.

        CPU нормализуется делением на psutil.cpu_count(): «сырое» значение psutil
        может превышать 100% на многоядерных системах (сумма по всем ядрам), а
        «норм.» приводит его к шкале 0–100%, как в диспетчере задач Windows.

        Args:
            procs: список psutil.Process, обычно результат _get_r7_processes().

        Returns:
            dict | None: {"ram_mb", "cpu_raw_pct", "cpu_norm_pct", "threads",
            "uptime_sec"}, или None если psutil недоступен или ни один процесс не жив.
        """
        if not (env.PSUTIL_OK and procs):
            return None

        total_ram_mb  = 0.0
        total_cpu_raw = 0.0
        total_threads = 0
        oldest_create = None
        alive = 0
        now = time.time()

        for p in procs:
            # Память: основной метрик; процесс считается "живым" если RAM читается успешно
            try:
                total_ram_mb += p.memory_info().rss / (1024 * 1024)
                alive += 1
            # процесс завершился — в сумму не входит, как и задумано
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            # CPU: суммируем по всем процессам Р7 (редактор + x2t могут работать
            # одновременно), а не берём max — max одного процесса занижал бы
            # реальную суммарную нагрузку на систему.
            if measure_cpu:
                try:
                    total_cpu_raw += p.cpu_percent(interval=0.1)
                # процесс завершился — его CPU не входит в сумму
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # Потоки: каждый вызов независим
            try:
                total_threads += p.num_threads()
            # процесс завершился — его потоки не считаем
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            # Время создания: каждый вызов независим
            try:
                create_ts = p.create_time()
                if oldest_create is None or create_ts < oldest_create:
                    oldest_create = create_ts
            # процесс завершился — возраст берём по оставшимся
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if alive == 0:
            return None

        cpu_count = self._cpu_count()
        return {
            "ram_mb":       round(total_ram_mb, 1),
            "cpu_raw_pct":  round(total_cpu_raw, 1),
            "cpu_norm_pct": round(total_cpu_raw / cpu_count, 1),
            "threads":      total_threads,
            "uptime_sec":   round(now - oldest_create, 1) if oldest_create is not None else None,
        }

    def _log_op_resources(self, agg, log_cb=None):
        """Строка лога ресурсов операции из _aggregate_op_resources."""
        if log_cb is None:
            log_cb = self.add_test_log
        if not agg or agg.get("cpu_sec") is None:
            return
        ram = f"{agg['ram']:.1f} МБ" if agg.get("ram") is not None else "—"
        log_cb(f"   📊 CPU Р7 за операцию: {agg['cpu_sec']:.3f} с процессорного времени, "
               f"среднее {agg['cpu']:.0f}% ядра, пик {agg['cpu_peak_core_pct']:.0f}% ядра; "
               f"пик RAM {ram}")

    def _log_resources(self, sample, log_cb=None):
        """Форматированный вывод одного замера ресурсов с цветовой индикацией CPU.

        Индикатор считается по нормализованному CPU (0–100%, все ядра):
        🟢 < 50% — обычная нагрузка, 🟡 50–79.9% — средняя, 🔴 ≥ 80% — высокая.

        Args:
            sample: dict из _sample_r7_resources(), либо None (тогда ничего не пишет).
            log_cb: функция логирования; по умолчанию self.add_test_log.
        """
        if sample is None:
            return
        if log_cb is None:
            log_cb = self.add_test_log

        cpu_norm = sample.get("cpu_norm_pct")
        if cpu_norm is None:
            icon = "⚪"
        elif cpu_norm < 50:
            icon = "🟢"
        elif cpu_norm < 80:
            icon = "🟡"
        else:
            icon = "🔴"

        uptime = sample.get("uptime_sec")
        uptime_str = f"{uptime:.0f} сек" if uptime is not None else "—"

        log_cb(
            f"   📊 RAM: {sample['ram_mb']:.1f} МБ  "
            f"CPU: {sample['cpu_raw_pct']:.1f}% (норм. {cpu_norm if cpu_norm is not None else '—'}%) {icon}  "
            f"Потоки: {sample['threads']}  Аптайм: {uptime_str}"
        )

    def _r7_cpu_seconds(self):
        """Суммарное процессорное время (user+system) процессов Р7, сек.

        Список процессов — полным обходом (fresh=True, ~5 мс, замерено), чтобы
        учесть x2t; вызов делается в начале и в конце паузы.

        Returns:
            float | None: None, если psutil недоступен или процессов нет.
        """
        if not env.PSUTIL_OK:
            return None
        try:
            procs = self._get_r7_processes(log_cb=lambda *_a: None, fresh=True)
        except Exception:
            return None
        if not procs:
            return None
        total = 0.0
        for p in procs:
            try:
                t = p.cpu_times()
                total += t.user + t.system
            except Exception:  # процесс завершился между обходом и чтением — пропускаем
                pass
        return total
