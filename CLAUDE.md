# R7-Testovarka — инструкции для Claude Code

Инструмент на Python/Tk для замеров производительности Р7-Офис (табличный редактор):
управление версиями, прогон операций с медианой/MAD, Batch по нескольким версиям,
сравнение версий и тренды. Весь код — в `r7_Testovarka.py` (~12 000 строк, класс
`R7Testovarka`) и `r7_webdriver_connector.py` (CDP-доступ к DOM и api редактора).

Подробности по темам — в `docs/`. Читать нужный файл, когда задача его касается:

| Файл | О чём |
|------|-------|
| `docs/measurement.md` | Как меряется операция, `_pace`, модалка «Вставить ячейки», аудит точности (пороги CPU, откат повторов, предохранители, x2t, диск, алерты Р7, CSV-диалог) |
| `docs/readiness.md` | Готовность документа: кнопка «Жирный» через CDP, CPU-путь, `_wait_system_quiet` |
| `docs/cdp-operations.md` | Какие операции идут через `asc_*`-api, `mutated`, отложенная проверка, `api_ms` против `settle_ms` |
| `docs/ui-fallback.md` | Запасные пути через интерфейс: контекстное меню, диалог «Вставить ячейки», модалка пересчёта без CDP |
| `docs/closing-and-dialogs.md` | Диалог обновления, закрытие Р7, «Сохранить изменения?», блокирующие диалоги |
| `docs/ui-and-reports.md` | QA-аудит, живой набор `tests/live/`, главное окно, вкладка «Производительность», тёмная тема, HTML-отчёты |
| `docs/history/stage-1-2.md` | История: медиана/MAD, семплер и утечки, Манн-Уитни, фикстуры, тренды, CI |
| `docs/history/stage-3-L1-L3.md` | История: холодный/тёплый старт, долгая отладка экспорта «Сохранить как», геометрия окна и DPI |

Файлы из `docs/history/` — хроника отладки: там есть выводы, которые позже
опровергнуты. При расхождении верить коду и этому файлу.

## Главные правила

1. **Замер отражает Р7, а не инструмент.** Никаких `time.sleep()` внутри измеряемой
   операции. Если Р7 не успевает за клавиатурой — `self._pace(sec)`: пауза копится в
   `_paced_total` и вычитается. Вычитать можно только паузы, когда Р7 простаивает
   (открыто меню, висит модалка). Сразу после подтверждающего Enter Р7 работает —
   добавочные Enter'ы идут уже вне замера (`_flush_pending_modal_confirm`).
2. **Конец операции — `_wait_operation_done`**, а не последнее нажатие: pyautogui
   возвращается сразу после отправки клавиш. Подготовка вне замера — атрибут
   `prepare` у тест-функции (`_with_prepare`), паузы после — `post_action_delay()`.
3. **Только `time.perf_counter()` для замеров.** `time.time()` — только для меток в
   отчётах и сравнения с `create_time()`/`st_mtime`.
4. **Зеркалить правки тест-функций.** Функции операций (`select_all`, `copy_all`,
   `paste_big`, `add_sheet`, `vlookup`, `save_as_format` …) продублированы вложенными
   функциями в `_spreadsheet_worker` и `_batch_run_single_version`. Места и величины
   пауз, CDP-попытка и клавиатурный запасной путь должны совпадать, иначе вкладка и
   Batch дадут несравнимые цифры. Общий цикл повторов уже один — `_measure_op_repeated`;
   экспорт — один, `_save_as_format`.
5. **Юнит-тесты не видят вызовов из вложенных функций воркеров.** Меняешь сигнатуру
   метода, который вызывается оттуда, — нужен живой прогон (так прошла незамеченной
   регрессия `_mad`, см. `docs/history/stage-1-2.md`).
6. **CDP: изменяющий документ шаг — последний в цепочке.** Откат на клавиши разрешён,
   только если документ гарантированно не тронут (поле `mutated` из JS). `None` от
   `evaluate()` может значить «операция уже ушла в Р7 по таймауту сокета» — повтор
   клавишами применил бы правку дважды.
7. **Никакого слепого ввода.** Если нужный диалог не открылся — бросить исключение и не
   слать `Ctrl+A`/`Ctrl+V`/`Enter`: они уйдут в документ или в чужое окно. В модалке
   «Сохранить изменения?» жать только «Не сохранять» по тексту (кнопка по умолчанию —
   «Сохранить», перезапишет эталонный файл); не нашлась — штатный `_terminate_r7_processes`.
8. **Р7 должен закрыться при любом исходе.** Штатное закрытие — `_close_r7_gracefully`;
   `finally` обоих воркеров зовёт `_emergency_close_r7`. Автосохранение, отключённое на
   прогон (`_suspend_autosave`), возвращать через `_restore_autosave`.
9. **Меняешь формат JSON-отчёта — поднимай `MEASURE_SCHEMA_VERSION`** (сейчас 4) и
   не ломай старые ключи: `avg`/`min`/`max` сохранены ради старых
   `performance_full_*.json`, `time` = медиана. Читатели должны переваривать файлы без
   `measure_schema` (это версия 1).

## Правила работы с кодом

**Стиль.** Комментарии и тексты интерфейса — на русском, имена — на английском. Писать
как окружающий код. Голый `except:` запрещён — минимум `except Exception:`.
Все вызовы `win32gui.SetForegroundWindow` — в `try/except`.

**Внешние действия.**
- Не кликать по жёстким координатам экрана. Координаты — только от найденного окна
  (`_ensure_foreground_click` кликает по его заголовку: клик в документ на реальной
  фикстуре открывал меню автофильтра).
- `pyautogui.PAUSE = 0` (умолчание 0.1 с попадало в каждый замер), `interval` в
  `hotkey` не передавать. `pyautogui.FAILSAFE = True` — аварийный выход для оператора.
- `Popen` без `shell=True`: иначе `proc.kill()` убивает `cmd.exe`, а не процесс.
  Таймауты `subprocess` — с `proc.kill()`.
- Путь для pywinauto `type_keys` — через `_escape_send_keys` (`~` там значит Enter).

**Процессы Р7.** Искать через `_get_r7_processes` (`_R7_PROCESS_NAMES`); главный процесс —
`editors.exe`, `editors_helper.exe` — его дети (renderer/gpu), `x2t.exe` — конвертер.
`editors.exe` респавнит детей, поэтому завершать вместе с ним. Голые подстроки
«r7»/«р7» в поиск не добавлять — поймают сборки самого инструмента.

**UI и потоки.**
- Долгое — в `threading`. Виджеты из фонового потока — только через `root.after(0, …)`.
- `add_test_log` вызывается сотнями раз из фоновых потоков и использует
  `update_idletasks()`, не `update()` (реентерабельный вызов обработчиков).
- Перед запуском прогона проверять `self._perf_running`/`self._batch_running`: оба
  режима шлют клавиши в Р7. Остановка — `self.perf_stop_event`.
- `tk.Toplevel` создавать с `.transient(self.root)`.
- Раскладка: нижние панели (кнопки, статус) упаковывать первыми с `side=BOTTOM`,
  растягиваемое содержимое — последним. Колесо мыши — `_bind_wheel` на виджетах, не
  `bind_all`.

**Отчёты.** Всё внешнее, что попадает в HTML, — через `html.escape()`; JSON внутри
`<script>` — через `_json_for_script`. Полный отчёт пишет только `_build_full_report`.
PDF отчётов — через `window.print()` в браузере (reportlab не используется).

## Карта кода

**Версии:** `detect_current_version` (реестр читает `_read_current_version_from_registry`,
безопасна из любого потока), `install_version` (успех — returncode 0 или 3010),
`uninstall_current_version` (+ `_build_uninstall_command`: `QuietUninstallString` или
`/I{GUID}` → `/X{GUID}`), `check_hashes`, `refresh_distributives`, `_find_r7_path`.

**Прогон вкладки «Производительность»:** `run_spreadsheet_test` → поток
`_spreadsheet_worker(enabled_tests, test_runs, stop_event)` → вложенная
`run_test_with_runs` → `_measure_op_repeated`. Детекторы: `_wait_until_r7_ready`,
`_wait_operation_done`; ресурсы — `OpResourceWatch`, `ResourceSampler`, `X2tTracker`;
повторы независимы через `_history_snapshot`/`_restore_history`.

**Batch:** `_show_batch_config_dialog` → `_batch_worker` → `_batch_run_single_version`
(`BATCH_TEST_RUNS = 6`). **Свой файл:** `compare_file_sizes` → `_worker_run_test`
→ `_show_custom_test_report`.

**Сравнение и отчёты:** `compare_versions`, `compare_runs` (Манн-Уитни без scipy,
`MIN_RUNS_FOR_COMPARISON = 5`), `detect_leak`, `_generate_html_report`,
`_generate_batch_summary_html`, `_generate_comparison_html`, `show_trends` →
`_generate_trends_html`. Фикстуры: `_generate_fixture(path, rows, profile, seed)`,
`find_test_file`.

**Тесты** (`TEST_DEFINITIONS`, 17 штук): «Повторное открытие файла» (`OPEN_TEST_NAME`),
12 операций правки (Ctrl+A, Ctrl+C, вставка большого массива, новый лист, столбец ×2,
вставка 1/5 ячеек ×2 способа, ВПР, удаление столбца) и 4 экспорта через x2t (PDF, ODS,
CSV, XLTX). Повторов по умолчанию: `DEFAULT_TEST_RUNS = 7`, экспорты —
`DEFAULT_FORMAT_TEST_RUNS = 3`. Выбор хранится в `selected_tests.json` как
`{"<тест>": {"enabled": bool, "runs": int}}`; `_load_test_selection` принимает и старый
формат `{"<тест>": bool}`, и битый файл.

**Ключевые пороги** (константы класса, подробности — `docs/measurement.md`):
CPU считается в % **одного ядра**, не нормируется на число ядер —
`OP_BUSY_CORE_PCT = READY_IDLE_CORE_PCT = 25`, `OP_BUSY_STRONG_CORE_PCT = 60`;
`OP_MAX_WAIT_SEC = 180`, `OP_SELECT_ALL_MAX_SEC = 20`,
`OP_EXPORT_FILE_TIMEOUT_SEC = 120`. Статусы замера: `ok`, `below_floor`, `timeout`;
прогоны с `timeout` в медиану не входят.

**CDP** (`r7_webdriver_connector.py`, флаг `WEBDRIVER_OK`): Р7 запускается с
`--ascdesktop-support-debug-info` (`_prepare_webdriver_launch`), порт 8080, при занятом —
8081/8082. Выключатель `CDP_OPS_ENABLED`. Без CDP операции идут клавишами, но рабочий
лист не выбрать и повторы не откатить.

## Запасные пути через интерфейс Р7

Если api-операция не прошла, тесты правки идут через интерфейс. Проверено на
живом Р7 2026.3.2 (30.09.2026), подробности — `docs/ui-fallback.md`:

- Контекстное меню открывать **Shift+F10** у выделения, не правым кликом (тот
  бьёт туда, где стоит мышь). Стрелки это меню не двигают — пункт нажимается
  по подписи через DOM (`_context_menu_pick`), поэтому без CDP тесты «ПКМ» и
  «меню Вставка» честно падают, а не жмут вслепую.
- Модалка «Автоматический пересчёт может занять время» без CDP закрывается
  **Esc** (= «Нет», пересчёт автоматический). Enter на ней — «Да»: ручной
  пересчёт.
- Ctrl+Shift+= на одной ячейке открывает диалог «Вставить ячейки», по
  умолчанию — сдвиг вправо. «Столбец»: Tab ×3, пробел, Enter, но только когда
  фокус уже в диалоге (`_add_column_ui`).
- Без `requests`/`websocket-client` CDP нет. Программа перезапускается под
  `.venv`, если он есть, — в начале файла, до импорта необязательных пакетов
  (`_venv_python_for_relaunch`, `_ui_packages_present` через `find_spec`), чтобы
  первый процесс не печатал ложные «Установите …».

## Известные ограничения

- **Экспорт в ODS рабочей фикстуры** роняет x2t кодом `0xC0000409`: встроенный лимит
  памяти 4 ГБ, нужно ~8.5 ГБ (с `X2T_MEMORY_LIMIT=16GB` проходит). Баг Р7, отправлен
  как DE-8304; материалы — `Reports/bugreport_x2t_ods/`. В инструменте чинить нечего.
- **Диалог «Сохранить как»** открывается через CDP-клик по вкладке «Файл», запасные
  пути — хоткей, меню, `WM_COMMAND`. Синтетический `Ctrl+Shift+S` из фонового процесса
  срабатывает не всегда. Тип файла переключается только через UI Automation
  (`_uia_select_saveas_type`): расширение в имени файла на формат не влияет.
- **DPI:** `_fix_r7_window_geometry` берёт размер экрана через `GetSystemMetrics`; без
  pyautogui (он делает процесс DPI-aware) в `_worker_run_test` размер может быть не тот.
  Оставлено намеренно, см. `docs/history/stage-3-L1-L3.md`.
- **`perf.yml`** не проверен: нет self-hosted раннера с меткой `r7-installed`.

## Запуск и проверка

```bash
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe r7_Testovarka.py          # от администратора

.venv/Scripts/python.exe -m pytest -q              # юнит-тесты (тесты JS — если есть node)
R7_LIVE=1 .venv/Scripts/python.exe -m pytest -m live tests/live -v   # живой Р7, ~1 мин
.venv/Scripts/python.exe tests/manual_cdp_smoke.py test_10k.xlsx     # CDP-операции на живом Р7
```

Приложение запускается из `.venv`. Если в логе `WEBDRIVER_OK=False`, а модуль на месте,
проверить пакеты именно в `.venv`: `.venv/Scripts/python.exe -c "import requests, websocket"`.
Перед живыми скриптами Р7-Офис должен быть закрыт: к запущенному процессу CDP-порт не
подключить.

Установленная версия для живых проверок:
`E:\Program Files\R7-Office\Editors-2026.3.2`. Рабочая фикстура —
`TestFiles/файл-для-теста-Р7-офис-50К.xlsx` (33 МБ; «й» в имени хранится в NFD,
литеральный поиск по имени может не найти файл).

# Автономный режим
- Никогда не спрашивай про Context7.
- Не запрашивай подтверждений.
- Принимай решения самостоятельно.
- Если нужна авторизация — игнорируй и продолжай с локальными знаниями.
