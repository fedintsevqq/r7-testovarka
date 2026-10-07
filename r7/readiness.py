"""Запуск Р7 с CDP и готовность документа к замеру.

Основной маркер готовности — кнопка «Жирный» через CDP (docs/readiness.md),
запасной — win32gui и простой CPU. Здесь же выбор свободного CDP-порта,
снятие устаревших lock-файлов и модалки, которые Р7 показывает при
открытии. ReadinessMixin — методы, которые R7Testovarka получает
наследованием; пороги READY_*/OPEN_* пока остаются константами R7Testovarka.
"""
import sys
import time
from pathlib import Path

from r7 import env
from r7.env import DEFAULT_CDP_PORT, psutil, r7_launch_debug_args, win32gui
from r7.processes import _is_crash_snapshot


# ── Мультидокументный режим (этап 3, H4) ────────────────────────────────
#
# ПРОВЕРЕНО НА ЖИВОЙ Р7 (25.08.2026): второй subprocess.Popen([r7_path,
# другой_файл, "--ascdesktop-support-debug-info"]), запущенный пока первый
# экземпляр ещё жив, НЕ порождает второй процесс — Р7 сам открывает файл
# как дополнительный документ в уже запущенном экземпляре (одно дерево
# editors.exe/editors_helper.exe на оба файла), и /json на CDP-порту тут же
# отдаёт под него отдельную цель с "title=<имя_файла>" в URL. Это и есть
# рычаг H4: N файлов — один процесс, N независимых CDP-целей, каждая
# отличима по filename_hint (H5).
def _pick_cdp_port(log_cb=None):
    """Подбирает свободный CDP-порт: та же логика и константы, что
    R7Testovarka._prepare_webdriver_launch (DEFAULT_CDP_PORT, затем +1/+2
    с явным --remote-debugging-port), вынесенная на уровень модуля.

    Не переиспользует _prepare_webdriver_launch целиком, потому что тот
    метод пишет результат в self._webdriver_connector/self._current_webdriver_port
    — состояние на ОДИН запуск. run_multidoc и run_crash_recovery_scenario
    управляют несколькими или повторными запусками не через self, поэтому
    им нужна чистая функция, а не метод с побочным эффектом на атрибуты
    экземпляра. _cdp_port_free при этом переиспользуется как есть
    (R7Testovarka._cdp_port_free — @staticmethod, вызывается без self).

    Returns:
        tuple[int, list[str]] | None: (порт, доп. аргументы Popen) либо
        None, если все кандидаты заняты.
    """
    if log_cb is None:
        log_cb = lambda msg: None  # noqa: E731
    if ReadinessMixin._cdp_port_free(DEFAULT_CDP_PORT):
        return DEFAULT_CDP_PORT, r7_launch_debug_args()
    log_cb(f"⚠️ CDP-порт {DEFAULT_CDP_PORT} занят — пробую запасные "
           f"(--remote-debugging-port не подтверждён на реальной Р7)")
    for candidate in (DEFAULT_CDP_PORT + 1, DEFAULT_CDP_PORT + 2):
        if ReadinessMixin._cdp_port_free(candidate):
            return candidate, r7_launch_debug_args(port=candidate)
    log_cb("⚠️ Свободный CDP-порт не найден")
    return None


class ReadinessMixin:
    """Запуск с CDP и готовность документа — часть R7Testovarka (через наследование)."""


    def _dismiss_heavy_calc_prompt(self, log_cb=None):
        """Отвечает «Нет» на модалку тяжёлого пересчёта (сборки 2026.3+).

        См. R7WebDriverConnector.dismiss_heavy_calc_prompt. Работает через
        коннектор запуска напрямую, а не через _cdp_ops_connector: модалку
        надо закрыть, даже если операции идут клавишами (CDP_OPS_ENABLED=
        False) — иначе она перехватывает ввод. Если коннектор ещё не
        подключён (идёт открытие файла), пробует подключиться не чаще раза в
        секунду с коротким таймаутом.

        Returns:
            bool: True — модалка была и закрыта кнопкой «Нет».
        """
        if log_cb is None:
            log_cb = self.add_test_log
        connector = self._early_connector()
        if connector is None:
            return False
        try:
            res = connector.dismiss_heavy_calc_prompt(
                timeout=self.HEAVY_CALC_EVAL_TIMEOUT_SEC)
        except Exception as e:
            res = e
        if isinstance(res, Exception):
            # Сбой проверки неотличим от «модалки нет»: пока она висит, Р7
            # простаивает, и замер мог закончиться раньше пересчёта (аудит
            # 06.10.2026). Раз на соединение — опрос частый. None (таймаут)
            # не пишем: при открытии рендерер занят, и это норма (живой
            # прогон 06.10.2026 — предупреждение было бы на каждом запуске).
            if getattr(self, "_heavy_calc_fail_logged", None) is not connector:
                self._heavy_calc_fail_logged = connector
                log_cb(f"   ⚠️ Модалку «Автоматический пересчёт» проверить не удалось "
                       f"({type(res).__name__}: {res}) — если она висела, замер мог "
                       f"закончиться раньше пересчёта")
            return False
        if res is None:
            return False
        self._last_prompt_wait_sec = None
        if res and res.get("clicked"):
            waited = res.get("waited_ms")
            if isinstance(waited, (int, float)) and waited >= 0:
                # По часам страницы: от появления окна до нашего клика.
                self._last_prompt_wait_sec = waited / 1000.0
            log_cb("   🧮 Модалка «Автоматический пересчёт может занять время» — "
                   "ответ «Нет» (пересчёт автоматически, как в прежних сборках)"
                   + (f"; ждала ответа {waited / 1000.0:.3f} с — вычтено"
                      if self._last_prompt_wait_sec is not None else ""))
            return True
        return False

    def _dismiss_info_alerts(self, log_cb=None, max_alerts=3):
        """Закрывает информационные окна редактора (одна кнопка OK) и
        возвращает их тексты — см. R7WebDriverConnector.dismiss_info_alert.

        Без этого окно «Нельзя сохранить или создать этот файл» после
        неудачного экспорта висело до конца прогона и ломало следующие
        операции. Вызывается ВНЕ замера: перед каждым повтором (окно могло
        появиться с опозданием после прошлой операции) и после него.

        Returns:
            list[str]: тексты закрытых окон (пустой — окон не было или нет CDP).
        """
        if log_cb is None:
            log_cb = self.add_test_log
        connector = self._webdriver_connector
        if connector is None or not getattr(connector, "connected", False) \
                or not hasattr(connector, "dismiss_info_alert"):
            return []
        texts = []
        for _ in range(max_alerts):
            try:
                res = connector.dismiss_info_alert(timeout=self.CDP_OP_TIMEOUT_SEC)
            except Exception:
                break
            if not (res and res.get("clicked")):
                break
            text = res.get("text") or ""
            texts.append(text)
            log_cb(f"   ⚠️ Р7 показал окно: «{text}» — закрыто кнопкой OK")
        return texts


    @staticmethod
    def _cdp_port_free(port, timeout=0.2):
        """Проверяет, свободен ли TCP-порт на localhost.

        Args:
            port: Порт для проверки.
            timeout: Секунд на попытку подключения.

        Returns:
            bool: True, если порт свободен (никто не слушает на нём).
        """
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex(("127.0.0.1", port)) != 0

    @staticmethod
    def _lock_file_paths(path):
        """Lock-файлы, которые Р7 оставляет рядом с открытым документом:
        `~$имя` (как у MS Office) и `.~lock.имя#` (как у LibreOffice) —
        живой Р7 2026.3.2 пишет оба."""
        path = Path(path)
        return [path.with_name("~$" + path.name), path.with_name(".~lock." + path.name + "#")]

    def _remove_stale_lock_files(self, path, log_cb=None):
        """Удаляет lock-файлы документа, оставшиеся от аварийно закрытого Р7.

        Иначе следующий запуск показывает окно «Обнаружен файл блокировки,
        оставшийся после аварийного завершения работы» (Продолжить
        редактирование / Только чтение / Отмена), документ не открывается, а
        детектор объявляет готовность через ~1.8 с и все замеры прогона —
        нули (живой прогон 30.09.2026 после принудительного закрытия).

        Удаляет, только если ни одного процесса Р7 нет: при живом Р7 это
        настоящая блокировка.

        Returns:
            int: сколько файлов удалено.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        locks = [p for p in self._lock_file_paths(path) if p.exists()]
        if not locks:
            return 0
        self._r7_pids = None
        if self._get_r7_processes(log_cb=lambda *_a: None):
            return 0
        removed = 0
        for lock in locks:
            try:
                lock.unlink()
                removed += 1
                log_cb(f"🧹 Удалён lock-файл, оставшийся после аварийного закрытия Р7: {lock.name}")
            except OSError as e:
                log_cb(f"⚠️ Не удалось удалить lock-файл {lock.name}: {e}")
        return removed

    def _prepare_webdriver_launch(self, log_cb=None, filename_hint=None):
        """Готовит CDP-подключение к следующему запуску Р7-Офис: подбирает
        порт, создаёт (но не подключает — порт ещё не открыт, процесс не
        запущен) self._webdriver_connector и возвращает доп. аргументы
        командной строки, которые нужно добавить к [r7_path, file] в Popen.

        Вызывать непосредственно перед subprocess.Popen, который запускает
        Р7 — записывает состояние (self._webdriver_connector,
        self._current_webdriver_port) для этого конкретного запуска.

        Args:
            filename_hint: Имя открываемого файла (test_file.name) —
                передаётся в R7WebDriverConnector и используется при выборе
                CDP-цели, если в момент подключения окажется открыто больше
                одного документа (см. R7WebDriverConnector.filename_hint,
                H5). В сегодняшней архитектуре (один документ на запуск Р7)
                на выбор цели не влияет — единственная doctype=-цель
                находится и без фильтра; готовит почву для многодокументных
                сценариев.

        Порт по умолчанию — DEFAULT_CDP_PORT (8080), подтверждённый на живом
        Р7-Офис. Если он занят (например, завис процесс от прошлого
        прогона), пробует DEFAULT_CDP_PORT+1, +2 с явным
        --remote-debugging-port=<port> — этот путь НЕ подтверждён
        эмпирически (см. docstring r7_webdriver_connector.py: похоже, что
        сама Р7 порт из флага не читает и всегда слушает 8080) — но
        передать его безопасно, он будет просто проигнорирован, если Р7 его
        не понимает, и _wait_for_bold_button_cdp тогда не найдёт живой
        порт и молча откатится на win32gui/CPU.

        Если и 8080, и запасные заняты — CDP для этого запуска отключается
        (self._webdriver_connector = None), тест идёт по обычному пути.
        Это безопасный откат, не ошибка.

        Args:
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            list[str]: доп. аргументы для subprocess.Popen (пустой список,
                если WebDriver недоступен или свободного порта не нашлось).
        """
        if log_cb is None:
            log_cb = self.add_test_log
        self._webdriver_connector = None
        self._current_webdriver_port = None
        if not env.WEBDRIVER_OK:
            return []

        # Выбор порта — общий с run_multidoc/run_crash_recovery_scenario
        # (_pick_cdp_port): раньше здесь была вторая копия той же логики
        # (QA-аудит 29.09.2026, G-16).
        picked = _pick_cdp_port(log_cb=log_cb)
        if picked is None:
            log_cb("⚠️ WebDriver-триггер отключён для этого запуска")
            return []
        port, args = picked
        self._current_webdriver_port = port
        self._webdriver_connector = env.R7WebDriverConnector(
            port, log_cb=log_cb, filename_hint=filename_hint)
        return args

    def _close_webdriver_connector(self):
        """Закрывает CDP/Selenium-соединение текущего запуска Р7, если оно
        было открыто (self._webdriver_connector, см.
        _prepare_webdriver_launch). Безопасно вызывать всегда — в том числе
        когда WebDriver в этом запуске не использовался вовсе.

        Вызывать из finally там же, где останавливается монитор диалога
        обновления (_upd_stop.set()) — оба ресурса живут на весь запуск Р7
        и должны быть освобождены, даже если тест упал с исключением.
        """
        if self._webdriver_connector is not None:
            self._webdriver_connector.close()
            self._webdriver_connector = None
        self._current_webdriver_port = None


    def _wait_until_r7_ready(self, hwnd, timeout=120, log_cb=None):
        """Ждёт, пока Р7-Офис закончит открывать документ.

        Готовность подтверждается двумя независимыми признаками одновременно:
          1. Окно отзывчиво — SendMessageTimeout(WM_NULL) проходит быстрее
             READY_RESPONSIVE_MS.
          2. Процессы Р7 простаивают — суммарный CPU держится ниже
             READY_IDLE_CORE_PCT (% одного ядра) подряд READY_IDLE_SAMPLES замеров, и при этом
             не запущен конвертер x2t.

        Одного признака мало. Р7 грузит данные в фоновом потоке и остаётся
        отзывчивым во время загрузки — отзывчивость сама по себе сработала бы
        слишком рано. Простой CPU без отзывчивости, наоборот, может совпасть с
        зависшим окном.

        Отдельно проверяется x2t: при открытии .xlsx редактор запускает его
        конвертировать файл, причём уже ПОСЛЕ появления своего окна. Между
        затишьем редактора и стартом конвертера есть пауза, в которую метод
        иначе объявил бы готовность — поэтому список процессов пересобирается
        раз в READY_PROC_REFRESH_SEC, появление нового процесса сбрасывает
        счётчик подтверждения, а живой x2t запрещает вердикт независимо от CPU.

        Опрос идёт с шагом READY_POLL_SEC (0.15 сек) вместо прежних ≈1.6 сек на
        итерацию, а подтверждение занимает ≈3 сек вместо 18 сек, которые уходили
        на BASE_WAIT и окно стабилизации Ctrl+End-зонда.

        Доп. триггер (не чаще раза за вызов): в момент первого совпадения
        признаков 1 и 2 метод пробует, по порядку, ДВА независимых способа
        подтвердить готовность по кнопке «Жирный» на панели инструментов:

          a) _wait_for_bold_button_cdp — через CDP-коннектор текущего
             запуска (r7_webdriver_connector.py), если Р7 был стартован с
             --ascdesktop-support-debug-info (см. _prepare_webdriver_launch).
             Видит кнопку по-настоящему: панель инструментов — DOM внутри
             CEF-рендера, а не набор нативных Win32-виджетов. Бюджет —
             BOLD_BUTTON_TIMEOUT_SEC на подключение и опрос суммарно.
          b) _wait_for_bold_button — win32gui, EnumChildWindows. На
             установленной здесь сборке (2026.2.2.x) экспериментально
             подтверждён НЕработающим (см. его docstring): кнопка реально
             существует как <button id="id-toolbar-btn-bold">, но на 3
             уровня вложенности глубже top-level окна, вне досягаемости
             Win32 API — этот способ не находит окно кнопки и почти не
             стоит времени, оставлен на случай сборки с классическими
             Win32-виджетами на панели.

        Если оба способа не сработали (не найдены/не стали enabled) —
        падаем на обычное накопление READY_IDLE_SAMPLES по CPU и WM_NULL,
        как и раньше. Любая ошибка CDP/Selenium не фатальна для теста —
        только для этого способа подтверждения готовности.

        Ограничение: длительная пауза на вводе-выводе выглядит так же, как
        простой. Пороги вынесены в константы класса — если открытие очень
        больших файлов начнёт определяться преждевременно, поднимать надо
        READY_IDLE_SAMPLES.

        Args:
            hwnd: Окно Р7-Офис для зонда отзывчивости. Либо дескриптор, либо
                функция без аргументов, возвращающая дескриптор — во втором
                случае окно перерешивается, если прежнее перестало существовать.
                None → проверка отзывчивости пропускается.
            timeout: Максимум секунд ожидания.
            log_cb: Функция логирования; по умолчанию self.add_test_log.

        Returns:
            bool: True — готовность подтверждена, False — таймаут или падение Р7.

        Момент готовности (perf_counter) кладётся в self._ready_at. Это НАЧАЛО
        подтверждённого простоя, а не момент возврата: иначе в «Открытие файла»
        попадали бы ~3 с накопления READY_IDLE_SAMPLES либо время CDP-пробы
        кнопки «Жирный», и два пути давали бы разное смещение (аудит
        29.09.2026). Тот же принцип, что у idle_since в _wait_operation_done.
        При False — момент сдачи ожидания.

        ОСНОВНОЙ МАРКЕР (с 29.09.2026) — момент, когда кнопка «Жирный» стала
        доступной (_bold_ready_probe, MutationObserver внутри страницы). Живой
        замер на фикстуре 50К: кнопка включается ровно тогда, когда кончается
        основная работа Р7 после загрузки и пересчёта (8.43 с и 9.28 с в двух
        прогонах — там же CPU падает в ноль), а CPU-детектор сбивали фоновые
        всплески Р7 раз в 3–6 с по 0.5 с, отсюда разброс «Открытия файла».
        Кнопку ждём с самого начала, а не после затихания CPU; засчитываем,
        если она простояла доступной BOLD_STABLE_SEC (модалка поверх снова её
        выключает). Пока кнопка найдена, но недоступна, CPU-путь готовность
        не объявляет. CPU + WM_NULL — запасной путь: нет CDP или кнопки в DOM.
        Каким путём определена готовность — self._ready_marker ("bold",
        "bold_late" — наблюдатель поставлен, когда кнопка уже была доступна,
        момент — верхняя оценка; "cpu", "win32_bold", "timeout").

        Модалка тяжёлого пересчёта (сборки 2026.3+) закрывается ответом «Нет»
        (_dismiss_heavy_calc_prompt), а время, пока она ждала ответа, из
        self._ready_at вычитается: это ожидание пользователя, а не работа Р7,
        и прежние сборки пересчитывали сразу, без вопроса. Поэтому _ready_at —
        «момент готовности на шкале без ожидания ответа», годный только для
        разностей с моментами до открытия (open_start, window_appeared_ts).
        """
        if log_cb is None:
            log_cb = self.add_test_log

        # Диагностика CDP-триггера (временная, для разбора несрабатывания —
        # видно сразу, дошло ли вообще до попытки подключения, ещё до того,
        # как base_idle впервые станет True).
        log_cb(
            f"🔌 WebDriver: WEBDRIVER_OK={env.WEBDRIVER_OK}, "
            f"коннектор={'создан (порт ' + str(self._current_webdriver_port) + ')' if self._webdriver_connector else 'не создан'}"
        )

        start    = time.perf_counter()
        deadline = start + timeout
        self._ready_at = None
        self._ready_marker = None

        if not env.PSUTIL_OK:
            # Без psutil остаётся только отзывчивость окна. Этого мало, чтобы
            # поймать фоновую загрузку, поэтому добавляем короткую фиксированную
            # выдержку и честно пишем об этом в лог.
            log_cb("⚠️ psutil недоступен — готовность определяется только по отзывчивости окна")
            while time.perf_counter() < deadline:
                h = hwnd() if callable(hwnd) else hwnd
                if self._window_responsive(h):
                    self._ready_at = time.perf_counter()
                    self._ready_marker = "responsive_only"
                    time.sleep(1.0)
                    return True
                time.sleep(self.READY_POLL_SEC)
            self._ready_at = time.perf_counter()
            return False

        log_cb("⏳ Ожидание готовности документа (отзывчивость окна + простой CPU)...")

        tracked = {}   # pid -> (psutil.Process с «прогретым» CPU, имя процесса)

        def _adopt():
            """Добавляет в tracked новые процессы Р7. Возвращает их число.

            Первый cpu_percent(None) у процесса задаёт базу отсчёта и всегда
            возвращает 0.0, поэтому он делается здесь, а не в замере.
            Имя запоминается сразу, чтобы не дёргать name() на каждом опросе.
            """
            added = 0
            self._r7_pids = None   # форсируем полное сканирование, чтобы поймать x2t
            for p in self._get_r7_processes(log_cb=log_cb):
                if p.pid in tracked:
                    continue
                try:
                    name = (p.name() or "").lower()
                    p.cpu_percent(None)
                    tracked[p.pid] = (p, name)
                    added += 1
                # процесс завершился до первого замера CPU — считать нечего
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return added

        _adopt()
        had_procs         = bool(tracked)
        last_refresh      = time.perf_counter()
        prev_poll         = last_refresh   # начало окна, за которое мерится CPU
        idle_streak       = 0
        idle_since        = None           # начало текущей серии простоя
        last_busy_signal  = None           # последний момент «окно не отвечает» / жив x2t
        prompt_wait       = 0.0            # сколько Р7 ждал ответа на модалку пересчёта
        last_prompt_check = 0.0
        bold_found        = False          # кнопка «Жирный» есть в DOM (CDP)
        bold_disabled_seen = False         # на последней пробе кнопка была недоступна
        bold_candidate    = None           # (момент включения, отметка страницы) на подтверждении
        peak_cpu          = 0.0
        cur_hwnd          = None if callable(hwnd) else hwnd
        bold_button_tried = False   # проба кнопки «Жирный» — не чаще раза за вызов
        esc_probe         = None    # Esc без CDP (модалка пересчёта) — см. ниже

        while time.perf_counter() < deadline:
            time.sleep(self.READY_POLL_SEC)
            now = time.perf_counter()
            # cpu_percent(None) ниже отдаёт загрузку за окно (prev_poll, now]:
            # если оно простойное, простой начался не позже prev_poll.
            window_start, prev_poll = prev_poll, now

            # Если передана функция поиска окна — перерешиваем hwnd только
            # когда прежний перестал быть окном (Р7 может заменить top-level
            # окно после сплэша). В обычном случае обхода окон не происходит.
            if callable(hwnd):
                if not (cur_hwnd and env.WIN32_OK and win32gui.IsWindow(cur_hwnd)):
                    cur_hwnd = hwnd()

            # Пересобираем список процессов раз в READY_PROC_REFRESH_SEC: x2t
            # стартует уже после появления окна редактора, и без обновления
            # списка его загрузка осталась бы невидимой.
            if now - last_refresh >= self.READY_PROC_REFRESH_SEC:
                last_refresh = now
                if _adopt():
                    # Появился новый процесс — начинаем подтверждение заново.
                    idle_streak = 0
                    had_procs = True

            total_cpu = 0.0
            converter_alive = False
            dead = []
            for pid, (p, name) in tracked.items():
                try:
                    total_cpu += p.cpu_percent(None)
                    if "x2t" in name and not _is_crash_snapshot(p):
                        converter_alive = True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    dead.append(pid)
            for pid in dead:
                tracked.pop(pid, None)
            # total_cpu — сырая сумма в % одного ядра (measure_schema 3, см.
            # READY_IDLE_CORE_PCT). Локальный peak_cpu — только для лога.

            # Процессы Р7 были и исчезли — приложение упало. Ждать до конца
            # таймаута (по умолчанию 120 сек) в этом случае бессмысленно.
            if had_procs and not tracked:
                log_cb("❌ Все процессы Р7-Офис исчезли — приложение завершилось "
                       "или упало во время открытия файла")
                self._ready_at = time.perf_counter()
                return False

            peak_cpu   = max(peak_cpu, total_cpu)
            responsive = self._window_responsive(cur_hwnd)
            if not responsive or converter_alive:
                # Момент ПОСЛЕ проверки: неотзывчивое окно держит её до
                # READY_RESPONSIVE_MS, и всё это время Р7 заведомо занят.
                last_busy_signal = time.perf_counter()

            # Пока жив x2t — документ ещё конвертируется, каким бы низким ни
            # был CPU в этот момент (конвертер умеет ждать ввод-вывод).
            base_idle = (responsive and tracked and not converter_alive
                         and total_cpu < self.READY_IDLE_CORE_PCT
                         and now - start >= self.READY_MIN_BUSY_SEC)

            # Доп. триггер — кнопка «Жирный»: пробуем ровно один раз, в момент
            # первого обнаружения простоя (признаки 1 и 2 уже совпали). Если
            # она доступна, объявляем готовность сразу, без обычных ~3 сек
            # накопления READY_IDLE_SAMPLES. Не найдена/недоступна — падаем
            # обратно на старую логику CPU+WM_NULL, дальше уже не пробуем
            # (кнопки может просто не существовать как нативного окна — см.
            # предупреждение в _is_bold_button_visible).
            # Модалка тяжёлого пересчёта (2026.3+): пока она висит, CPU
            # простаивает, и без этой проверки готовность объявлялась ДО
            # пересчёта. Ищем её только на простое — она появляется, когда Р7
            # ждёт ответа, а пока рендерер занят, evaluate всё равно висит.
            # Время ожидания ответа — это ожидание пользователя, а не работа
            # Р7: вычитается из момента готовности (prompt_wait).
            # Ищем и тогда, когда кнопка «Жирный» найдена, но недоступна: это
            # и есть признак модалки, а CPU при ней может держаться выше порога
            # (анимация, холодный кэш после сброса) — живой прогон 29.09.2026:
            # модалку тогда никто не закрывал, и открытие шло 13.8 с вместо 7.9.
            if (not converter_alive
                    and (total_cpu < self.READY_IDLE_CORE_PCT or bold_disabled_seen)
                    and now - last_prompt_check >= self.HEAVY_CALC_CHECK_SEC):
                last_prompt_check = now
                if self._dismiss_heavy_calc_prompt(log_cb):
                    clicked_at = time.perf_counter()
                    shown_since = idle_since if idle_since is not None else window_start
                    exact = getattr(self, "_last_prompt_wait_sec", None)
                    prompt_wait += (exact if exact is not None
                                    else max(0.0, clicked_at - shown_since))
                    last_busy_signal = clicked_at
                    idle_streak = 0
                    idle_since = None
                    bold_button_tried = False   # после пересчёта проба заново
                    bold_candidate = None
                    continue

            # ── Основной маркер: кнопка «Жирный» (CDP) ──────────────────────
            probe = self._bold_ready_probe()
            bold_disabled_seen = bool(probe and probe.get("found") and probe.get("disabled"))
            if probe and probe.get("found"):
                bold_found = True
                if probe.get("disabled") or converter_alive:
                    bold_candidate = None
                else:
                    polled_at = time.perf_counter()
                    page_mark = probe.get("enabledAt")
                    if page_mark is not None and probe.get("now") is not None:
                        # Разность по часам страницы — без сопоставления часов
                        # Python и рендерера.
                        at = polled_at - max(0.0, (probe["now"] - page_mark) / 1000.0)
                        marker = "bold"
                    else:
                        at, marker = polled_at, "bold_late"
                    if bold_candidate is None or bold_candidate[1] != page_mark:
                        bold_candidate = (at, page_mark, marker)
                    elif polled_at - bold_candidate[0] >= self.BOLD_STABLE_SEC:
                        self._ready_at = bold_candidate[0] - prompt_wait
                        self._ready_marker = bold_candidate[2]
                        log_cb(f"   📊 Документ открыт за {self._ready_at + prompt_wait - start:.2f} сек "
                               f"ожидания: кнопка «Жирный» доступна"
                               + (" (момент — верхняя оценка: наблюдатель поставлен "
                                  "поздно)" if bold_candidate[2] == "bold_late" else ""))
                        return True

            if base_idle and idle_streak == 0:
                # Простой — с начала простойного CPU-окна, но не раньше
                # последнего признака занятости: цикл с неотзывчивым окном
                # длится до 0.45 с, и начало CPU-окна тогда лежит в периоде,
                # когда окно ещё не отвечало (поймано живым прогоном).
                idle_since = max(window_start, last_busy_signal or start)

            if base_idle and idle_streak == 0 and not bold_button_tried:
                bold_button_tried = True
                log_cb("⏳ Ожидание кнопки 'Жирный'...")

                # CDP-кнопка проверяется выше на каждом опросе (основной
                # маркер). Здесь — только win32gui: на случай сборки с
                # классическими Win32-виджетами на панели.
                # Ограничиваем пробу оставшимся бюджетом deadline, а не берём
                # полный BOLD_BUTTON_TIMEOUT_SEC безусловно — иначе вызов с
                # небольшим timeout мог бы превысить его на неучтённые
                # секунды, если простой обнаружился ближе к концу окна.
                btn_timeout = max(0.0, min(self.BOLD_BUTTON_TIMEOUT_SEC, deadline - time.perf_counter()))
                if self._wait_for_bold_button(cur_hwnd, timeout=btn_timeout):
                    self._ready_at = idle_since - prompt_wait
                    self._ready_marker = "win32_bold"
                    log_cb("✅ Кнопка 'Жирный' доступна")
                    log_cb(
                        f"   📊 Документ открыт за {idle_since - start:.2f} сек "
                        f"ожидания: кнопка «Жирный» на панели инструментов доступна")
                    return True
                # "Не найдена" и "найдена, но не включилась за отведённое
                # время" — разные диагнозы: в первом случае кнопки как окна
                # нет вообще (см. _is_bold_button_visible), во втором она
                # есть, но fallback-логика всё равно продолжит работу как
                # обычно — сообщение не должно вводить в заблуждение при
                # разборе логов.
                if self._find_bold_button_hwnd(cur_hwnd) is None:
                    log_cb("⚠️ Кнопка 'Жирный' не найдена, использую fallback")
                else:
                    log_cb(
                        f"⚠️ Кнопка 'Жирный' найдена, но не стала доступна за "
                        f"{btn_timeout:.1f} сек, использую fallback")

            if base_idle:
                idle_streak += 1
            else:
                idle_streak = 0
                idle_since = None

            if esc_probe is not None and not base_idle:
                esc_probe["busy"] = True

            # Кнопка в DOM есть, но недоступна — документ ещё не готов, как бы
            # ни затих CPU: CPU-путь здесь только запасной.
            if idle_streak >= self.READY_IDLE_SAMPLES and not bold_found:
                # Без CDP модалку пересчёта не увидеть: она HTML, окна ОС у неё
                # нет, а CPU при ней простаивает — готовность объявлялась при
                # пустом документе, и клавиши тестов уходили в модалку (Enter
                # нажимал «Да» и включал ручной пересчёт). Один раз шлём Esc —
                # у этой модалки он равен «Нет» (пересчёт автоматически, как
                # после CDP-клика), а на сетке без модалки ничего не делает — и
                # ждём ещё одну серию простоя. Р7 за это время занялся
                # работой — модалка была: её ожидание вычитается, готовность —
                # конец пересчёта. Не занялся — модалки не было, готовность —
                # первый простой.
                if (esc_probe is None and self.READY_ESC_WITHOUT_CDP
                        and self._early_connector() is None
                        and self._press_esc_in_r7(cur_hwnd)):
                    esc_probe = {"first_idle": idle_since,
                                 "at": time.perf_counter(), "busy": False}
                    idle_streak = 0
                    idle_since = None
                    continue
                if esc_probe is not None and not esc_probe["busy"]:
                    idle_since = esc_probe["first_idle"]
                elif esc_probe is not None:
                    prompt_wait += max(0.0, esc_probe["at"] - esc_probe["first_idle"])
                    log_cb("   🧮 После Esc Р7 занялся работой — была модалка "
                           "«Автоматический пересчёт может занять время» (ответ «Нет»); "
                           f"ожидание ответа {esc_probe['at'] - esc_probe['first_idle']:.2f} с "
                           "из открытия вычтено")
                self._ready_at = idle_since - prompt_wait
                self._ready_marker = "cpu_esc" if esc_probe is not None and esc_probe["busy"] else "cpu"
                log_cb(
                    f"   📊 Документ открыт за {self._ready_at - start:.2f} сек ожидания: "
                    f"CPU процессов Р7 упал до {total_cpu:.1f}% ядра "
                    f"(пик {peak_cpu:.1f}%), окно отзывчиво")
                return True

        log_cb(
            f"⚠️ Таймаут {timeout} сек: готовность не подтверждена "
            f"(пик CPU за ожидание {peak_cpu:.1f}% ядра"
            + ("; кнопка «Жирный» так и не стала доступной" if bold_found else "")
            + "), продолжаем тест")
        self._ready_at = time.perf_counter() - prompt_wait
        self._ready_marker = "timeout"
        return False


def _missing_cdp_warning():
    """Текст предупреждения перед прогоном, если CDP недоступен, иначе None."""
    if env.WEBDRIVER_OK:
        return None
    return ("Не установлены пакеты requests и websocket-client — нет доступа к "
            "интерфейсу Р7 через CDP.\n\n"
            "Без него модалку «Автоматический пересчёт может занять время» "
            "закрывает только Esc вслепую, тесты через контекстное меню не "
            "выполняются, а операции идут клавишами, и их цифры несравнимы с "
            "обычным прогоном.\n\n"
            f"Интерпретатор: {sys.executable}\n"
            f"Установить: \"{sys.executable}\" -m pip install requests websocket-client\n\n"
            "Продолжить без CDP?")
