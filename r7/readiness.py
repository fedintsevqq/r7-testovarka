"""Запуск Р7 с CDP и готовность документа к замеру.

Основной маркер готовности — кнопка «Жирный» через CDP (docs/readiness.md),
запасной — win32gui и простой CPU. Здесь же выбор свободного CDP-порта,
снятие устаревших lock-файлов и модалки, которые Р7 показывает при
открытии. ReadinessMixin — методы, которые R7Testovarka получает
наследованием, и пороги READY_* (OPEN_* — константы R7Testovarka).
"""
import sys
from pathlib import Path

from r7 import env
from r7.env import DEFAULT_CDP_PORT, r7_launch_debug_args
from r7.readiness_wait import ReadinessWait, wait_without_psutil


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

    # ── Пороги определения «документ открыт» ────────────────────────────────
    # Одни на все три режима (одиночный тест, тест своего файла, Batch), чтобы
    # они больше не разъезжались, как разъехались STABLE_SECS=10 и STABLE_SECS=8
    # у двух прежних копий ожидания загрузки.
    READY_POLL_SEC          = 0.15   # шаг опроса
    READY_RESPONSIVE_MS     = 300    # окно прокачало очередь быстрее — оно отзывчиво
    # Порог простоя — в процентах ОДНОГО ядра (сырая сумма cpu_percent() по
    # процессам Р7), measure_schema 3. История: в schema 1 сравнивалась сырая
    # сумма с порогом без обоснования, в schema 2 — сумма, делённая на число
    # ядер (шкала Task Manager). Нормировка оказалась ошибкой: работа Р7 почти
    # вся однопоточная (пересчёт, раскладка, x2t), и один полностью занятый
    # поток на 16 ядрах даёт 100/16 = 6.25%, на 32 — 3.1%, то есть ниже
    # порога. Детектор переставал видеть занятость на многоядерных стендах, и
    # операции массово уходили в below_floor (аудит 29.09.2026: 10 из 13 на
    # 16-ядерном стенде). Доля одного ядра от числа ядер не зависит: «занят
    # хотя бы четверть одного потока» значит одно и то же на любой машине.
    # Нормированный CPU по-прежнему пишется в отчёт — но только для чтения.
    # Калибровка на живом Р7 2026.3.2 (29.09.2026, 16 ядер, test_50k.xlsx):
    # простой — медиана 0, p95 7.5, максимум 15.2% ядра (шум квантуется
    # тиком таймера 15.6 мс: на окне 0.2 с тик = 7.8%, на 0.15 с — 10.4%);
    # загрузка — 45–290% ядра. Старый нормированный порог 4% на этом стенде
    # означал 64% ядра и считал простоем реальную загрузку в 45–60%.
    # 25 — выше двух тиков шума на окне опроса READY_POLL_SEC.
    READY_IDLE_CORE_PCT     = 25.0   # % одного ядра: сумма по процессам Р7 ниже — простой
    READY_IDLE_SAMPLES      = 20     # столько простоев подряд → документ открыт (≈3 с)
    READY_PROC_REFRESH_SEC  = 1.0    # как часто пересобирать список процессов (ловим x2t)
    READY_MIN_BUSY_SEC      = 0.5    # не выносить вердикт раньше — даём Р7 начать работу

    # Без CDP модалку пересчёта закрывает Esc (см. _wait_until_r7_ready).
    # Проверено на живом Р7 2026.3.2: Esc закрывает её с ответом «не Да» —
    # пересчёт автоматический (calcPr.calcMode не выставлен).
    READY_ESC_WITHOUT_CDP = True



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

        # Шаги ожидания — r7/readiness_wait.py (прежде один цикл на 385 строк).
        waiter = ReadinessWait(self, hwnd, timeout, log_cb)
        self._ready_at = None
        self._ready_marker = None
        if not env.PSUTIL_OK:
            return wait_without_psutil(self, hwnd, waiter.deadline, log_cb)
        return waiter.run()


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
