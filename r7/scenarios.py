"""Сценарии без окна приложения: несколько документов сразу (run_multidoc),
длительная нагрузка (run_soak) и аварийное восстановление
(run_crash_recovery_scenario, CLI — run_crash_recovery.py).

Работают через CDP и процессы Р7 напрямую, экземпляр R7Testovarka им не
нужен.
"""
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from r7 import env
from r7.env import r7_launch_debug_args
from r7.processes import _kill_r7_processes_since, _running_r7_pids
from r7.readiness import _pick_cdp_port
from r7.stats import MIN_RUNS_FOR_COMPARISON, compare_runs, detect_leak


def run_multidoc(r7_path, files, ops_per_doc, port=None,
                  launch_wait_sec=14.0, additional_wait_sec=6.0,
                  connect_timeout=15.0, max_workers=None, log_cb=None):
    """Открывает несколько документов в одном экземпляре Р7 и выполняет
    ops_per_doc параллельно по каждому через ThreadPoolExecutor.

    Не завершает процесс Р7 и не закрывает документы — жизненным циклом
    процесса управляет вызывающий код (proc в возвращаемом словаре).
    Коннекторы закрываются перед возвратом (освобождают websocket), сам
    Р7 при этом продолжает работать.

    Args:
        r7_path: путь к исполняемому файлу Р7 (см. _find_r7_path()).
        files: пути к файлам (2 и более). Первый открывается запуском
            процесса, остальные — дополнительными Popen в тот же процесс.
        ops_per_doc: callable(connector, file_path) -> JSON-совместимый
            результат. Вызывается в отдельном потоке на файл; у каждого
            потока свой R7WebDriverConnector (свой filename_hint), но все
            они делят один и тот же процесс Р7 и один CDP-порт.
        port: CDP-порт. По умолчанию DEFAULT_CDP_PORT.
        launch_wait_sec: пауза после запуска ПЕРВОГО файла (холодный старт
            процесса — дольше, чем открытие документа в уже запущенном Р7).
        additional_wait_sec: пауза после запуска КАЖДОГО последующего файла
            (короче launch_wait_sec — процесс уже поднят).
        connect_timeout: таймаут R7WebDriverConnector.connect() на файл.
        max_workers: размер пула потоков для ops_per_doc (по умолчанию —
            по числу успешно подключённых файлов).
        log_cb: колбэк логирования; по умолчанию — молчаливый. Вызывается
            параллельно из разных потоков (и во время подключения, и во
            время ops_per_doc) — оборачивается внутренним замком, поэтому
            передавать сюда небезопасный к конкурентному вызову callback
            (например, Tk-виджет напрямую) не опаснее, чем передать его в
            один поток: r7_Testovarka.py:add_test_log вызывается из фона и
            дальше сама уходит на root.after(0, ...), но до этой правки
            конкурентные вызовы ИЗ ЭТОЙ функции были бы первым местом в
            кодовой базе, где log_cb зовётся не из одного потока.

    Returns:
        dict: {
            "proc": Popen первого (владеющего процессом) запуска,
            "opened": [имена файлов, к которым подключились],
            "failed_to_open": [имена файлов, к которым connect() не удался],
            "per_file": {имя_файла: {"ok": bool, "result": ..., "error": str|None}},
        }

    Raises:
        ValueError: пустой список файлов, либо среди файлов есть
            совпадающие имена (даже из разных папок) — filename_hint (H5)
            различает документы ТОЛЬКО по базовому имени, совпадение
            сделало бы маршрутизацию по CDP-целям недетерминированной.
        RuntimeError: WEBDRIVER_OK=False (пакеты requests/websocket-client
            не установлены, либо r7_webdriver_connector.py не
            импортировался вовсе) — без них подключиться в принципе
            невозможно, лучше явная ошибка здесь, чем NameError на
            R7WebDriverConnector ниже.
    """
    if len(files) < 1:
        raise ValueError("run_multidoc: нужен хотя бы один файл")
    if not env.WEBDRIVER_OK:
        raise RuntimeError("run_multidoc: WEBDRIVER_OK=False — CDP недоступен "
                           "(requests/websocket-client не установлены?)")
    if log_cb is None:
        log_cb = lambda msg: None  # noqa: E731
    log_lock = threading.Lock()
    raw_log_cb = log_cb

    def log_cb(msg):
        with log_lock:
            raw_log_cb(msg)

    files = [Path(f) for f in files]
    names = [f.name for f in files]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"run_multidoc: файлы с одинаковым именем не "
                         f"поддерживаются (совпадают: {dupes}) — filename_hint "
                         f"не различит их как CDP-цели")

    if port is None:
        picked = _pick_cdp_port(log_cb=log_cb)
        if picked is None:
            raise RuntimeError("run_multidoc: свободный CDP-порт не найден")
        port, debug_args = picked
    else:
        debug_args = r7_launch_debug_args(port=port)

    proc = subprocess.Popen([r7_path, str(files[0])] + debug_args)
    time.sleep(launch_wait_sec)
    for f in files[1:]:
        subprocess.Popen([r7_path, str(f)] + debug_args)
        time.sleep(additional_wait_sec)

    path_by_name = {f.name: f for f in files}
    connectors = {}
    opened, failed_to_open = [], []

    def _connect_one(f):
        name = f.name
        conn = env.R7WebDriverConnector(port=port, filename_hint=name, log_cb=log_cb)
        return name, conn, conn.connect(timeout=connect_timeout)

    with ThreadPoolExecutor(max_workers=len(files)) as pool:
        for name, conn, ok in pool.map(_connect_one, files):
            if ok:
                connectors[name] = conn
                opened.append(name)
            else:
                failed_to_open.append(name)
                log_cb(f"⚠️ run_multidoc: не удалось подключиться к {name!r}")

    per_file = {}

    def _run_one(name, conn):
        try:
            result = ops_per_doc(conn, path_by_name[name])
            return name, {"ok": True, "result": result, "error": None}
        except Exception as e:
            return name, {"ok": False, "result": None, "error": f"{type(e).__name__}: {e}"}

    if connectors:
        workers = max_workers or len(connectors)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_one, name, conn) for name, conn in connectors.items()]
            for fut in as_completed(futures):
                name, outcome = fut.result()
                per_file[name] = outcome

    for name in failed_to_open:
        per_file[name] = {"ok": False, "result": None, "error": "connect() не удался"}

    for conn in connectors.values():
        try:
            conn.close()
        except Exception:  # соединение уже оборвано — освобождать нечего
            pass

    return {"proc": proc, "opened": opened, "failed_to_open": failed_to_open,
            "per_file": per_file}


# ── Соак-режим: инфраструктура (этап 3, M3) ──────────────────────────────
#
# Только каркас цикла — сам многочасовой прогон и его анализ остаются на
# пользователе (см. постановку задачи этапа 3). Что уже сделано и
# переиспользовано:
#   - фоновый сбор ресурсов и детектор утечки — ResourceSampler/detect_leak
#     (этап 2, H3), тот же класс, что и в _spreadsheet_worker;
#   - сравнение "было/стало" для контрольных замеров — compare_runs (этап
#     2, M5): первые baseline_count контрольных замеров против остальных,
#     тот же критерий Манна-Уитни, что и при сравнении версий.
def soak_drift_verdict(control_measurements, baseline_count=5):
    """Сравнивает первые baseline_count контрольных замеров с остальными —
    признак деградации по ходу соака (не нового кода: обёртка над
    compare_runs, этап 2, M5).

    Args:
        control_measurements: список {"value": float, ...} — как в
            результате run_soak.
        baseline_count: сколько первых замеров считать базой сравнения.

    Returns:
        dict | None: результат compare_runs (verdict/effect_pct/p_value),
        либо None, если данных недостаточно (см. MIN_RUNS_FOR_COMPARISON).
    """
    values = [m["value"] for m in control_measurements
             if isinstance(m.get("value"), (int, float))]
    if len(values) < baseline_count + MIN_RUNS_FOR_COMPARISON:
        return None
    return compare_runs(values[:baseline_count], values[baseline_count:])


def run_soak(op, iterations=None, duration_sec=None, control_every=30,
             control_op=None, sampler=None, history_path=None,
             stop_event=None, log_cb=None):
    """Крутит op() в цикле — по числу итераций или по времени — с
    периодическим контрольным замером и (опционально) фоновым сбором
    ресурсов через ResourceSampler.

    Args:
        op: callable() -> любой результат — нагрузочная операция одной
            итерации соака (например, правка документа через CDP).
        iterations: число итераций. Если задан — duration_sec игнорируется.
        duration_sec: секунд работы (используется только при
            iterations=None). Хотя бы один из iterations/duration_sec
            обязателен.
        control_every: раз в сколько итераций снимать контрольный замер
            (0 или None — не снимать вовсе).
        control_op: callable() -> float — контрольная операция (например,
            обёрнутая по времени select_all), значение которой копится в
            control_measurements. None — контрольные замеры не снимаются,
            даже если задан control_every.
        sampler: уже созданный, но НЕ запущенный ResourceSampler. Стартует
            в начале run_soak и останавливается в finally — при исключении
            внутри op()/control_op() фоновый поток не остаётся висеть.
            None — фоновый сбор ресурсов не идёт.
        history_path: путь для сохранения результата в JSON. None — только
            вернуть в памяти, на диск не писать.
        stop_event: threading.Event — проверяется между итерациями; тот же
            принцип, что у self.perf_stop_event в остальных тестах
            (досрочная, но чистая остановка, а не убийство потока).
        log_cb: колбэк логирования. По умолчанию — молчаливый.

    Returns:
        dict: {
            "iterations_completed": int,
            "elapsed_sec": float,
            "stopped_early": bool,
            "control_measurements": [{"iteration": i, "value": ..., "t": ...}],
            "resource_samples": [...] (только если был sampler),
            "leak": вердикт detect_leak (только если был sampler),
            "drift": вердикт soak_drift_verdict (только если контрольных
                замеров набралось достаточно),
        }
    """
    if iterations is None and duration_sec is None:
        raise ValueError("run_soak: нужен iterations или duration_sec")
    if log_cb is None:
        log_cb = lambda msg: None  # noqa: E731

    start = time.time()
    if sampler is not None:
        sampler.start()

    control_measurements = []
    i = 0
    stopped_early = False
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                stopped_early = True
                break
            if iterations is not None:
                if i >= iterations:
                    break
            elif (time.time() - start) >= duration_sec:
                break

            op()
            i += 1

            if control_op is not None and control_every:
                if i % control_every == 0:
                    value = control_op()
                    control_measurements.append(
                        {"iteration": i, "value": value, "t": time.time() - start})
                    log_cb(f"🔎 Соак: контрольный замер #{i}: {value}")
    finally:
        # ВСЁ — включая построение result и сохранение history_path — стоит
        # в finally (найдено code-review): если op()/control_op() бросит
        # исключение посреди многочасового прогона, исходная версия теряла
        # весь накопленный control_measurements/resource_samples целиком,
        # хотя history_path существует именно для того, чтобы не терять
        # данные соака. Исключение по-прежнему пробрасывается вызывающему
        # коду ПОСЛЕ finally (Python делает это сам) — контракт с тестами
        # ("op() падает — падает и run_soak") не меняется, меняется только
        # то, что на диске к этому моменту уже лежит частичный результат.
        if sampler is not None:
            sampler.stop()
            sampler.join(timeout=5)

        result = {
            "iterations_completed": i,
            "elapsed_sec": time.time() - start,
            "stopped_early": stopped_early,
            "control_measurements": control_measurements,
        }
        if sampler is not None:
            samples = sampler.snapshot()
            result["resource_samples"] = samples
            result["leak"] = detect_leak(samples)
        drift = soak_drift_verdict(control_measurements)
        if drift is not None:
            result["drift"] = drift

        if history_path is not None:
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            log_cb(f"💾 Соак: история сохранена в {history_path}")

    return result


def run_crash_recovery_scenario(r7_path, file_path, edits, verify_recovered,
                                 kill_delay_sec=1.0, launch_wait_sec=14.0,
                                 relaunch_wait_sec=14.0, connect_timeout=20.0,
                                 process_death_timeout=10.0, port=None,
                                 after_relaunch=None, log_cb=None, before_edits=None):
    """Правки → жёсткое убийство процесса (симуляция сбоя) → перезапуск с
    тем же файлом → переподключение → проверка, что восстановилось.

    Args:
        r7_path: путь к исполняемому файлу Р7.
        file_path: путь к документу — открывается дважды (до и после
            "сбоя") по одному и тому же пути, иначе автовосстановлению (если
            оно есть) нечего будет связать со старым сеансом.
        edits: список callable(connector) -> любой результат — правки,
            прикладываемые последовательно к документу до убийства
            процесса. Исключение одной правки не прерывает остальные
            (считается непрошедшей, попадает в errors).
        verify_recovered: callable(connector) -> int — вызывается ПОСЛЕ
            переподключения к перезапущенному Р7; возвращает число правок
            (0..len(edits)), которые сценарий считает восстановленными.
            Формат проверки (текст документа, число строк, historyPoints
            и т.п.) зависит от типа документа и от того, что конкретно
            показал живой прогон, — сознательно не встроен сюда (см.
            комментарий над функцией).
        kill_delay_sec: пауза между последней правкой и kill() — даёт
            автосохранению (если оно периодическое) шанс сработать.
        launch_wait_sec: пауза после ПЕРВОГО запуска перед тем, как
            пытаться подключиться (холодный старт процесса).
        relaunch_wait_sec: пауза после перезапуска (после "сбоя") перед
            попыткой подключиться — возможно, дольше обычного холодного
            старта, если Р7 показывает диалог восстановления или
            сканирует автосохранения.
        connect_timeout: таймаут R7WebDriverConnector.connect() на каждое
            из двух подключений.
        process_death_timeout: сколько ждать реального завершения процесса
            после kill() (proc.wait) перед перезапуском — без этого
            перезапуск может упереться в файл, ещё удерживаемый умирающим
            процессом.
        port: CDP-порт. По умолчанию DEFAULT_CDP_PORT.
        after_relaunch: необязательный callable(proc) -> любой JSON-совместимый
            результат, вызывается сразу ПОСЛЕ Popen перезапущенного процесса,
            ДО паузы relaunch_wait_sec и до переподключения по CDP. Место для
            логики, которую сам сценарий не знает, как делать в общем виде —
            например, поиск и закрытие диалога восстановления средствами
            win32gui (реальный заголовок/кнопки такого диалога для этой
            сборки Р7 не подтверждены, см. run_crash_recovery.py). Исключение
            внутри колбэка не прерывает сценарий — логируется и попадает в
            result["after_relaunch_error"], сам result["after_relaunch"]
            остаётся тем, что успел вернуть колбэк (или отсутствует, если
            колбэк не задан или упал до return).
        log_cb: колбэк логирования. По умолчанию — молчаливый.
        before_edits: необязательный callable(connector) — вызывается после
            первого подключения, ДО правок. Место для снимка исходного
            состояния документа, от которого verify_recovered считает
            восстановленное (иначе листы самого файла сойдут за
            восстановленные правки). Исключение логируется.

    Returns:
        dict: {
            "edits_applied": int,
            "edits_failed": int,
            "connected_before_crash": bool,
            "process_died_cleanly": bool,
            "connected_after_crash": bool,
            "time_to_reconnect_sec": float | None,
            "recovered_count": int | None,
            "recovered_fraction": float | None,
            "proc": Popen перезапущенного процесса (для очистки вызывающим
                кодом) | None, если перезапустить не удалось,
            "r7_processes_killed": int — сколько процессов Р7 убито при
                «сбое» (лаунчер из Popen сюда не входит),
        }
        process_died_cleanly — лаунчер мёртв, убит хотя бы один процесс Р7 и
        ни одного не осталось. recovered_fraction считается от правок,
        которые реально применились (edits_applied).
    Raises:
        RuntimeError: Р7 уже запущен — сценарий не может ни отличить свой
            процесс, ни устроить сбой (второй запуск отдаёт файл живому Р7).

        Если process_died_cleanly=False — verify_recovered НЕ вызывается,
        recovered_count/recovered_fraction остаются None (см. код ниже,
        найдено code-review): без подтверждённой смерти старого процесса
        второй Popen с тем же путём может не запустить новый процесс, а
        просто заново открыть файл в ЕЩЁ ЖИВОМ старом — та самая механика,
        на которой строится run_multidoc (H4). В этом случае verify_recovered
        увидела бы исходный документ и без единого сбоя, дав ложный
        "100% восстановлено" вердикт вместо честного "сценарий не
        выполнился, как задумано".

    Raises:
        RuntimeError: WEBDRIVER_OK=False — CDP недоступен (см. run_multidoc).
    """
    if not env.WEBDRIVER_OK:
        raise RuntimeError("run_crash_recovery_scenario: WEBDRIVER_OK=False — "
                           "CDP недоступен (requests/websocket-client не установлены?)")
    if log_cb is None:
        log_cb = lambda msg: None  # noqa: E731
    file_name = Path(file_path).name

    if port is None:
        picked = _pick_cdp_port(log_cb=log_cb)
        if picked is None:
            raise RuntimeError("run_crash_recovery_scenario: свободный CDP-порт не найден")
        port, debug_args = picked
    else:
        debug_args = r7_launch_debug_args(port=port)

    result = {
        "edits_applied": 0, "edits_failed": 0,
        "connected_before_crash": False, "process_died_cleanly": False,
        "connected_after_crash": False, "time_to_reconnect_sec": None,
        "recovered_count": None, "recovered_fraction": None, "proc": None,
        "r7_processes_killed": 0,
    }

    before = _running_r7_pids()
    if before:
        raise RuntimeError(f"run_crash_recovery_scenario: Р7 уже запущен (PID {sorted(before)}) — "
                           "закройте его: второй запуск отдал бы файл живому процессу")
    launched_at = time.time()
    proc = subprocess.Popen([r7_path, str(file_path)] + debug_args)
    try:
        _crash_and_relaunch(result, proc, launched_at, r7_path, file_path, debug_args,
                            edits, verify_recovered, port, file_name, kill_delay_sec,
                            launch_wait_sec, relaunch_wait_sec, connect_timeout,
                            process_death_timeout, after_relaunch, before_edits, log_cb)
    except BaseException:
        # Р7 должен закрыться при любом исходе (правило 10 CLAUDE.md).
        _kill_r7_processes_since(launched_at, timeout=process_death_timeout, log_cb=log_cb)
        raise
    return result


def _close_quietly(conn, log_cb):
    try:
        conn.close()
    except Exception as e:
        log_cb(f"⚠️ Не закрылось соединение CDP: {type(e).__name__}: {e}")


def _crash_and_relaunch(result, proc, launched_at, r7_path, file_path, debug_args,
                        edits, verify_recovered, port, file_name, kill_delay_sec,
                        launch_wait_sec, relaunch_wait_sec, connect_timeout,
                        process_death_timeout, after_relaunch, before_edits, log_cb):
    """Правки → сбой → перезапуск → проверка; заполняет result на месте.
    Вынесено из run_crash_recovery_scenario, чтобы тот держал try вокруг
    всего хода целиком."""
    time.sleep(launch_wait_sec)

    conn = env.R7WebDriverConnector(port=port, filename_hint=file_name, log_cb=log_cb)
    result["connected_before_crash"] = conn.connect(timeout=connect_timeout)

    if result["connected_before_crash"]:
        if before_edits is not None:
            try:
                before_edits(conn)
            except Exception as e:
                log_cb(f"⚠️ Сбой снимка до правок: {type(e).__name__}: {e}")
        for edit in edits:
            try:
                edit(conn)
                result["edits_applied"] += 1
            except Exception as e:
                result["edits_failed"] += 1
                log_cb(f"⚠️ Сбой правки перед crash-тестом: {type(e).__name__}: {e}")
    else:
        log_cb("⚠️ Не удалось подключиться до сбоя — правки не применены")

    time.sleep(kill_delay_sec)

    log_cb("💥 Симулирую сбой: proc.kill()")
    proc.kill()
    launcher_dead = True
    try:
        proc.wait(timeout=process_death_timeout)
    except subprocess.TimeoutExpired:
        launcher_dead = False
        log_cb(f"⚠️ Процесс не завершился за {process_death_timeout} с после kill()")
    # Сам Р7 — editors.exe и его рендереры, а не лаунчер из Popen.
    killed, alive = _kill_r7_processes_since(launched_at, timeout=process_death_timeout,
                                             log_cb=log_cb)
    result["r7_processes_killed"] = killed
    log_cb(f"💥 Убито процессов Р7: {killed}")
    leftover = _running_r7_pids()
    if killed == 0:
        log_cb("⚠️ Не убит ни один процесс Р7 — сбоя не было")
    if leftover:
        log_cb(f"⚠️ После сбоя живы процессы Р7: {sorted(leftover)}")
    result["process_died_cleanly"] = (launcher_dead and killed > 0 and not alive
                                      and leftover is not None and not leftover)

    _close_quietly(conn, log_cb)

    new_proc = subprocess.Popen([r7_path, str(file_path)] + debug_args)
    result["proc"] = new_proc

    if after_relaunch is not None:
        try:
            result["after_relaunch"] = after_relaunch(new_proc)
        except Exception as e:
            result["after_relaunch_error"] = f"{type(e).__name__}: {e}"
            log_cb(f"⚠️ Сбой after_relaunch: {result['after_relaunch_error']}")

    time.sleep(relaunch_wait_sec)

    reconnect_start = time.time()
    new_conn = env.R7WebDriverConnector(port=port, filename_hint=file_name, log_cb=log_cb)
    result["connected_after_crash"] = new_conn.connect(timeout=connect_timeout)
    if result["connected_after_crash"]:
        result["time_to_reconnect_sec"] = time.time() - reconnect_start
        if not result["process_died_cleanly"]:
            log_cb("⚠️ Старый процесс не подтвердил завершение — verify_recovered "
                   "пропущена (см. process_died_cleanly в докстроке): второй "
                   "запуск мог просто переоткрыть файл в ещё живом старом "
                   "процессе, а не восстановить его в новом")
        else:
            try:
                recovered = verify_recovered(new_conn)
                result["recovered_count"] = recovered
                if result["edits_applied"]:
                    result["recovered_fraction"] = recovered / result["edits_applied"]
            except Exception as e:
                log_cb(f"⚠️ Сбой verify_recovered: {type(e).__name__}: {e}")
    else:
        log_cb("⚠️ Не удалось переподключиться после перезапуска")

    _close_quietly(new_conn, log_cb)
