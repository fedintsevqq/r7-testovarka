"""Процессы Р7-Офис: поиск по точному имени, завершение, конвертер x2t.

Главный процесс — editors.exe, editors_helper.exe — его дети
(рендереры и GPU), x2t — конвертер. Ищем только по этим именам: голые
подстроки «r7»/«р7» ловили сборки самого инструмента. ProcessesMixin —
методы, которые R7Testovarka получает наследованием; модульные функции
нужны сценариям без экземпляра приложения (аварийное восстановление).
"""
import os
import re
import threading
import time
from pathlib import Path

from r7 import env, windows
from r7.env import psutil

R7_EXIT_GRACE_SEC = 5.0   # сколько ждать ухода процессов Р7 после закрытия окна
R7_EXIT_POLL_SEC = 0.2


def _is_crash_snapshot(proc):
    """True, если процесс x2t — снимок упавшего процесса, а не конвертер.

    Когда x2t падает, запущенный из Р7, рядом появляется ещё один «x2t»:
    родитель — сам упавший x2t, 0 потоков, 0 CPU, 0 памяти, состояние
    «остановлен» (живой прогон 29.09.2026; при автономном запуске x2t его нет).
    Это снимок процесса для отчёта об ошибке, он живёт минутами. Работать он
    не может — потоков нет, — но по имени выглядел как живой конвертер: оба
    детектора считали Р7 занятым, ранний выход экспорта не срабатывал (120 с
    вместо 14), а следующая операция ждала до предохранителя 180 с.
    """
    try:
        if proc.num_threads() == 0:
            return True
        parent = proc.parent()
        return bool(parent and (parent.name() or "").lower().startswith("x2t"))
    except Exception:
        return False


class X2tTracker(threading.Thread):
    """Жизненный цикл конвертера x2t: запуск, параметры, длительность, код
    завершения (29.09.2026).

    x2t — отдельный процесс Р7 (родитель — editors.exe), им идут конвертация
    .xlsx при открытии и весь экспорт. Раньше он учитывался только как «Р7
    занят» и как источник CPU: по логу нельзя было сказать, запускался ли он
    при экспорте в ODS, сколько работал и чем закончился. Строка «Обнаружен
    процесс конвертации x2t» при этом не печаталась вовсе — её глушил опрос
    процессов с молчаливым log_cb.

    Опрос — разность psutil.pids() раз в POLL_SEC (~1 мс на вызов), имя
    читается только у новых PID. Код завершения: x2t не наш дочерний процесс,
    поэтому, пока он жив, держим его дескриптор (PROCESS_QUERY_LIMITED_
    INFORMATION | SYNCHRONIZE) и после смерти читаем GetExitCodeProcess.
    Параметры — из XML, путь к которому x2t получает в командной строке
    (m_sFileFrom / m_sFileTo / m_nFormatTo; проверено вживую).
    """

    POLL_SEC = 0.05
    STILL_ACTIVE = 259

    def __init__(self, log_cb=None):
        super().__init__(daemon=True)
        self.log_cb = log_cb or (lambda msg: None)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.runs = []                 # dict на каждый запуск x2t
        self._active = {}              # pid -> (run, handle, psutil.Process)
        try:
            self._known = set(psutil.pids()) if env.PSUTIL_OK else set()
        except Exception:
            self._known = set()

    @staticmethod
    def _read_params(cmdline):
        """Поля XML-параметров x2t, если файл ещё существует."""
        for arg in cmdline[1:]:
            if not arg.lower().endswith(".xml"):
                continue
            try:
                text = Path(arg).read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                return {"params_file": arg}
            out = {"params_file": arg}
            for tag, key in (("m_sFileFrom", "file_from"), ("m_sFileTo", "file_to"),
                             ("m_nFormatTo", "format_to")):
                mt = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
                if mt:
                    out[key] = mt.group(1)
            return out
        return {}

    def _poll(self):
        try:
            pids = set(psutil.pids())
        except Exception:
            return
        new_pids = pids - self._known
        # Известные — только PID, живые на этом опросе. Windows быстро
        # переиспользует номера процессов, и прежний «накопительный» набор
        # пропускал новый x2t, получивший номер давно завершившегося процесса:
        # полный прогон 29.09.2026 показал запуски x2t [0,1,1,1,1,0,0] на 7
        # экспортах PDF, хотя конвертер работал в каждом. Промах возможен,
        # только если процесс умер и номер переиспользован в пределах одного
        # POLL_SEC.
        self._known = pids
        for pid in new_pids:
            try:
                p = psutil.Process(pid)
                if not (p.name() or "").lower().startswith("x2t"):
                    continue
                if _is_crash_snapshot(p):
                    self.log_cb(f"   🔧 снимок упавшего x2t (PID {pid}) — не конвертер, "
                                f"не учитываю")
                    continue
                try:
                    cmd = p.cmdline()
                except Exception:
                    cmd = []
            except Exception:
                continue
            handle = None
            if env.WIN32_OK:
                try:
                    handle = windows.open_process_for_exit_code(pid)
                except Exception:
                    handle = None
            run = {"pid": pid, "start": time.perf_counter(), "end": None,
                   "exit_code": None, "cpu_sec": None,
                   "io_read_mb": None, "io_write_mb": None}
            run.update(self._read_params(cmd))
            with self._lock:
                self.runs.append(run)
                self._active[pid] = (run, handle, p)
            target = Path(run.get("file_to", "")).name or "?"
            self.log_cb(f"   🔧 x2t запущен (PID {pid}, формат {run.get('format_to', '?')}, "
                        f"результат {target})")
        for pid, (run, handle, p) in list(self._active.items()):
            code = None
            try:
                t = p.cpu_times()
                run["cpu_sec"] = round(t.user + t.system, 3)
            except Exception:  # x2t уже завершился — остаётся прошлое значение CPU
                pass
            # Ввод-вывод — последнее прочитанное значение: после смерти x2t
            # его счётчики недоступны, а диск при конвертации — основной
            # подозреваемый в разбросе открытия файла.
            try:
                io = p.io_counters()
                run["io_read_mb"] = round(io.read_bytes / 2**20, 1)
                run["io_write_mb"] = round(io.write_bytes / 2**20, 1)
            except Exception:  # x2t уже завершился — остаётся прошлое значение ввода-вывода
                pass
            if handle is not None:
                try:
                    code = windows.process_exit_code(handle)
                except Exception:
                    # Код не прочитался — это ещё не «завершился»: прежде живой
                    # x2t записывался законченным (аудит 06.10.2026).
                    code = None
                    try:
                        if p.is_running():
                            continue
                    except Exception:  # процесс не прочитался — считаем его завершённым
                        pass
                if code == self.STILL_ACTIVE:
                    continue
            else:
                try:
                    if p.is_running():
                        continue
                except Exception:  # процесс не прочитался — считаем его завершённым
                    pass
            run["end"] = time.perf_counter()
            run["exit_code"] = code
            with self._lock:
                self._active.pop(pid, None)
            if handle is not None:
                try:
                    windows.close_handle(handle)
                except Exception:  # хэндл уже недействителен — освобождать нечего
                    pass
            dur = run["end"] - run["start"]
            if code is None:
                # Без кода «завершён» звучало как успех, а упал ли конвертер —
                # неизвестно (нет доступа к процессу).
                self.log_cb(f"   ⚠️ x2t завершился (PID {pid}) за {dur:.2f} с, код выхода "
                            f"неизвестен — упал ли конвертер, не проверить")
            elif code == 0:
                self.log_cb(f"   🔧 x2t завершён (PID {pid}) за {dur:.2f} с, код 0")
            else:
                self.log_cb(f"   ❌ x2t упал (PID {pid}) через {dur:.2f} с, код "
                            f"{code & 0xFFFFFFFF:#010x}")

    def run(self):
        while not self._stop_event.wait(self.POLL_SEC):
            self._poll()

    def stop(self):
        self._stop_event.set()

    def since(self, mark):
        """Запуски x2t, начавшиеся не раньше mark (perf_counter), — копии."""
        with self._lock:
            return [dict(r) for r in self.runs if r["start"] >= mark]

    @staticmethod
    def summarize(runs, now=None):
        """Сводка по запускам: число, суммарная длительность, упавшие."""
        now = time.perf_counter() if now is None else now
        dur = sum(((r["end"] or now) - r["start"]) for r in runs)
        failed = [r for r in runs if r.get("exit_code") not in (0, None)]
        return {"count": len(runs), "sec": round(dur, 3),
                "cpu_sec": round(sum(r.get("cpu_sec") or 0.0 for r in runs), 3),
                "io_read_mb": round(sum(r.get("io_read_mb") or 0.0 for r in runs), 1),
                "io_write_mb": round(sum(r.get("io_write_mb") or 0.0 for r in runs), 1),
                "failed_codes": [f"{r['exit_code'] & 0xFFFFFFFF:#010x}" for r in failed],
                "formats": sorted({r.get("format_to") for r in runs if r.get("format_to")})}


# ── Сценарий восстановления после сбоя (этап 3, M4) ──────────────────────
#
# КОД СЦЕНАРИЯ, НЕ ПРОВЕРЕННОЕ ЖИВЫМ ПРОГОНОМ ПОВЕДЕНИЕ. В отличие от Н6/Н4/
# М3 выше, это единственный кусок этапа 3, который сознательно НЕ
# верифицирован на реальном Р7 — по прямой постановке задачи ("Тестирование
# Crash recovery на живом Р7" осталось на пользователя). Причина не лень, а
# честность: сам факт и механизм автовосстановления Р7 (есть ли диалог
# "Восстановить документы?", молча ли подхватывается автосохранение по пути
# файла, что происходит при отсутствии автосохранения вовсе) — это то, что
# отличается от продукта к продукту и от сборки к сборке, и гадать про
# него в JS/логике так же неверно, как раньше было гадать про
# "asc_insertText" (Н6). Поэтому verify_recovered ЦЕЛИКОМ вынесен
# наружу — сценарий не предполагает конкретный способ проверки, вызывающий
# код (после живой проверки, что именно означает "восстановлено" для
# конкретного типа документа) передаёт свою функцию.
#
# Что здесь реализовано и почему это безопасно как код: оркестрация
# процесса — kill() (жёсткий, не _close_r7_gracefully: сценарий намеренно
# симулирует сбой, а не штатное закрытие), переоткрытие того же пути,
# переподключение по CDP, замер времени до готовности. Это тот же набор
# примитивов (subprocess.Popen/R7WebDriverConnector), что уже проверен
# живьём в run_multidoc/run_soak — риск в НОВОЙ части (реакция Р7 на
# сбой), не в этой.
#
# Живой прогон 06.10.2026 (Р7 2026.3.2) показал, что «безопасная» часть
# была с ошибкой: kill() бил по лаунчеру, а не по Р7 (исправлено,
# _kill_r7_processes_since). Реакция Р7 на настоящий сбой: при повторном
# открытии файла — нативный диалог «Обнаружен файл блокировки…»; после
# «Продолжить редактирование» документ открывается с диска, правки до сбоя
# не возвращаются. При запуске без файла записи о сбое в recover уже не
# было, во вкладке «Для восстановления» тоже (docs/closing-and-dialogs.md).
def _running_r7_pids():
    """PID всех живых процессов Р7 (по точному имени, см. _matches_r7_process).

    Returns:
        set | None: None — psutil недоступен, проверить нельзя.
    """
    if not env.PSUTIL_OK:
        return None
    pids = set()
    for p in psutil.process_iter(["name"]):
        try:
            if ProcessesMixin._matches_r7_process(p.info["name"]):
                pids.add(p.pid)
        except Exception:
            continue
    return pids


def _kill_r7_processes_since(since_ts, timeout=10.0, log_cb=None, keep_pids=()):
    """Жёстко убивает процессы Р7, запущенные не раньше since_ts.

    Popen(DesktopEditors.exe) возвращает лаунчер, который сразу передаёт
    работу editors.exe и завершается, поэтому proc.kill() самого лаунчера
    Р7 не трогает (живой прогон 06.10.2026: editors.exe пережил «сбой»,
    второй запуск открыл файл в нём же, сценарий дал ложное «3/3»).
    Процесс с нечитаемым create_time (AccessDenied) тоже жертва: молча
    пропустить его — тот же ложный «сбой». Защита Р7 пользователя —
    keep_pids и отказ сценария, если Р7 уже запущен.

    Args:
        since_ts: time.time() до запуска Р7.
        timeout: сколько ждать завершения убитых процессов, сек.
        log_cb: колбэк логирования.
        keep_pids: PID, которые не трогать (были до запуска).

    Returns:
        tuple[int, list]: число убитых процессов и процессы, пережившие
        kill(). Без psutil — (0, [None]): смерть не подтвердить.
    """
    if log_cb is None:
        log_cb = lambda msg: None  # noqa: E731
    if not env.PSUTIL_OK:
        log_cb("⚠️ psutil недоступен — процессы Р7 не найти и не убить")
        return 0, [None]
    keep = set(keep_pids or ())
    victims = []
    for p in psutil.process_iter(["name", "create_time"]):
        try:
            if p.pid in keep or not ProcessesMixin._matches_r7_process(p.info["name"]):
                continue
            created = p.info["create_time"]
            if created is None:
                log_cb(f"⚠️ Время запуска {p.pid} не читается — считаю его процессом сценария")
                victims.append(p)
            elif created >= since_ts - 1.0:
                victims.append(p)
        except Exception:
            continue
    for p in victims:
        try:
            p.kill()
        except psutil.NoSuchProcess:  # процесс уже завершился сам — цель достигнута
            pass
        except Exception as e:
            log_cb(f"⚠️ Не удалось убить {p.pid}: {type(e).__name__}: {e}")
    _gone, alive = psutil.wait_procs(victims, timeout=timeout)
    if alive:
        log_cb(f"⚠️ Пережили kill(): {[p.pid for p in alive]}")
    return len(victims), alive


class ProcessesMixin:
    """Поиск и завершение процессов Р7 — часть R7Testovarka (через наследование)."""

    # Подстроки "r7"/"р7" ловили и собственные сборки инструмента —
    # R7-Testovarka.exe и R7Manager.exe совпадают с той же маской, что и
    # editors_helper.exe. Список сужен до реальных бинарников Р7-Офис.
    # Имена процессов Р7-Офис. "editors" здесь ОБЯЗАТЕЛЕН и добавлен не для
    # полноты: 25.08.2026 выяснилось, что главный процесс приложения на живой
    # сборке называется editors.exe, и под прежнюю маску он не подходил
    # (проверка "editors_helper" in "editors.exe" даёт False). Последствия
    # были втройне неприятными:
    #   1. _terminate_r7_processes не мог завершить Р7 вообще: убивались
    #      только дочерние рендереры editors_helper.exe, а живой родитель
    #      немедленно создавал их заново. Документированный «штатный
    #      запасной путь» через форс-килл на этой сборке не работал, и
    #      попытка закрыть Р7 уходила в бесконечный цикл respawn.
    #   2. Замеры RAM/CPU теряли главный процесс целиком (84 МБ RSS в
    #      наблюдавшемся случае) — все прежние цифры памяти занижены.
    #   3. _wait_operation_done определял «Р7 простаивает» по неполному
    #      набору процессов, то есть мог объявить операцию законченной,
    #      пока главный процесс ещё работал.
    # Дерево процессов на сборке 2026.2.2.x:
    #   DesktopEditors.exe (лаунчер, сразу завершается)
    #     └── editors.exe (главный процесс приложения)
    #           └── editors_helper.exe × N (рендереры CEF)
    #
    # Сравнение идёт по ТОЧНОМУ имени (см. _matches_r7_process), а не по
    # вхождению подстроки: маска "editors" как подстрока цепляла бы любой
    # сторонний процесс со словом editors в имени, а инструмент по этому
    # списку не только меряет, но и убивает процессы.
    _R7_PROCESS_NAMES = ("editors.exe", "editors_helper.exe", "desktopeditors.exe")

    # x2t — конвертер документов, отдельный короткоживущий процесс. Его имя
    # содержит версию (x2t.exe, x2t64.exe и т.п.), поэтому он единственный,
    # кто сопоставляется по префиксу, а не по точному имени.
    _R7_PROCESS_PREFIXES = ("x2t",)

    @classmethod
    def _matches_r7_process(cls, name):
        """True, если имя процесса принадлежит Р7-Офис.

        Args:
            name: Имя процесса, как его отдаёт psutil (с расширением).

        Returns:
            bool
        """
        low = (name or "").lower()
        if low in cls._R7_PROCESS_NAMES:
            return True
        return any(low.startswith(pref) for pref in cls._R7_PROCESS_PREFIXES)

    def _get_r7_processes(self, log_cb=None, fresh=False):
        """Returns list of psutil.Process objects for all R7-Office related processes.

        Сопоставление — по точному имени (_R7_PROCESS_NAMES) плюс префикс для
        x2t (_R7_PROCESS_PREFIXES), см. _matches_r7_process. Маски "r7"/"р7"
        сознательно отсутствуют: под них попадали собственные процессы
        инструмента, R7-Testovarka.exe и R7Manager.exe. Свой PID и PID родителя исключаются явно — второй рубеж
        защиты на случай, если Р7-Офис когда-нибудь переименует исполняемый файл
        во что-то похожее на маску.

        x2t — внутренний конвертер документов Р7-Офис, отдельный процесс, который
        может давать заметный вклад в общую RAM/CPU при открытии/сохранении файлов.
        If self._r7_pids is set (from a previous call), tries direct PID lookup first.

        Args:
            log_cb: Callback for the x2t detection line; defaults to self.add_test_log.
        """
        if log_cb is None:
            log_cb = self.add_test_log

        if not env.PSUTIL_OK:
            return []

        if not hasattr(self, "_x2t_logged_pids"):
            self._x2t_logged_pids = set()

        own_pid = os.getpid()
        try:
            parent_pid = psutil.Process(own_pid).ppid()
        except Exception:
            parent_pid = None
        excluded_pids = {own_pid} | ({parent_pid} if parent_pid else set())

        # Fast path: try previously discovered PIDs directly. fresh=True — всегда
        # полный обход: иначе конвертер x2t, запущенный после заполнения кэша,
        # не находился вовсе (живой прогон 29.09.2026: CPU экспорта в PDF 2%
        # ядра за 91 с — x2t в подсчёт не попал).
        if not fresh and getattr(self, "_r7_pids", None):
            procs = []
            for pid in self._r7_pids:
                if pid in excluded_pids:
                    continue
                try:
                    p = psutil.Process(pid)
                    p.name()  # raises NoSuchProcess if dead
                    procs.append(p)
                # процесс из кэша завершился — ниже полный обход
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if procs:
                return procs

        # Full scan
        found = []
        try:
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    pid = proc.info.get("pid")
                    if pid in excluded_pids:
                        continue
                    name = (proc.info.get("name") or "").lower()
                    if self._matches_r7_process(name):
                        found.append(proc)
                        # Запуски x2t логирует X2tTracker (_x2t): здесь лог
                        # глушился — первым процессы опрашивал наблюдатель
                        # ресурсов с молчаливым log_cb и помечал PID как
                        # «уже записанный».
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:  # частый опрос: при сбое обхода вернём найденное, вызов повторится
            pass

        # Cache PIDs for subsequent fast-path calls
        self._r7_pids = [p.pid for p in found]
        return found

    def _r7_gone(self, timeout=None):
        """True — ни одного процесса Р7 не осталось. Без psutil — False:
        проверить нечем, пусть finally закроет аварийно.

        Окно Р7 исчезает раньше процессов: editors.exe и его дети завершаются
        ещё доли секунды. Проверка сразу после закрытия окна видела их живыми,
        и finally воркера звал аварийное закрытие после штатного. Поэтому ждём
        до timeout (по умолчанию R7_EXIT_GRACE_SEC), пока процессы уйдут."""
        if not env.PSUTIL_OK:
            return False
        timeout = R7_EXIT_GRACE_SEC if timeout is None else timeout
        deadline = time.perf_counter() + timeout
        while True:
            self._r7_pids = None
            try:
                if not self._get_r7_processes(log_cb=lambda *_a: None):
                    return True
            except Exception:
                return False
            if time.perf_counter() >= deadline:
                return False
            time.sleep(R7_EXIT_POLL_SEC)

    def _terminate_r7_processes(self, log_cb=None):
        """Принудительно завершает все процессы Р7-Офис: terminate(), затем
        kill() для тех, что не откликнулись за 3 сек.

        Крайняя мера closing-последовательности — раньше её не было вовсе:
        если диалог «Сохранить изменения?» не распознавался слепой
        Alt+F4→Right→Enter, процесс Р7 оставался висеть до ручного
        вмешательства, держа файл заблокированным весь оставшийся прогон.

        Returns:
            bool: True, если процессов не осталось (включая случай, когда их
            не было изначально).
        """
        if log_cb is None:
            log_cb = self.add_test_log
        if not env.PSUTIL_OK:
            # Проверить нечем — не «закрыто» (прежде True, аудит 06.10.2026).
            log_cb("⚠️ psutil недоступен — не проверить, закрылся ли Р7")
            return False
        procs = self._get_r7_processes(log_cb=lambda _m: None)
        if not procs:
            return True

        # Родителя — первым. Наблюдалось 25.08.2026: главный процесс
        # (editors.exe) пересоздаёт убитые дочерние рендереры быстрее, чем
        # цикл успевает пройти по списку, и завершение уходит в бесконечный
        # respawn. Пока имя родителя вообще не попадало в маску поиска, это
        # выглядело как «процессы не убиваются»; теперь он в списке, но
        # порядок всё равно важен — сначала тот, кто порождает.
        procs = sorted(procs, key=lambda pr: self._R7_TERMINATE_ORDER.get(
            (self._safe_proc_name(pr) or ""), 99))

        for p in procs:
            try:
                p.terminate()
            except Exception:  # процесс уже завершился; оставшихся добьёт kill ниже
                pass
        try:
            _gone, alive = psutil.wait_procs(procs, timeout=3)
        except Exception:
            alive = procs
        for p in alive:
            try:
                p.kill()
            except Exception:  # процесс завершился сам; живые попадут в журнал ниже
                pass
        if alive:
            log_cb(f"🔪 Принудительно завершено процессов Р7-Офис: {len(alive)}")

        # Проверяем, а не рапортуем. Прежняя версия возвращала True всегда —
        # включая случай, когда процессы пережили и terminate, и kill: вызывающий
        # код считал Р7 закрытым, а тот держал файл заблокированным.
        self._r7_pids = None
        left = self._get_r7_processes(log_cb=lambda _m: None)
        if left:
            log_cb(f"⚠️ Процессы Р7-Офис пережили завершение: {len(left)} "
                   f"(PID: {', '.join(str(p.pid) for p in left)})")
            return False
        return True

    # Порядок завершения: сначала порождающие процессы, потом порождаемые.
    _R7_TERMINATE_ORDER = {"desktopeditors.exe": 0, "editors.exe": 1,
                           "editors_helper.exe": 2}

    @staticmethod
    def _safe_proc_name(proc):
        """Имя процесса в нижнем регистре, либо None — psutil.Process.name()
        поднимает исключение на процессе, умершем между сканом и вызовом."""
        try:
            return (proc.name() or "").lower()
        except Exception:
            return None
