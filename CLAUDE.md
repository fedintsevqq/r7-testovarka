# R7-Testovarka — инструкции для Claude Code

Инструмент на Python/Tk для замеров производительности Р7-Офис (табличный редактор):
управление версиями, прогон операций с медианой/MAD, Batch по нескольким версиям,
сравнение версий и тренды. Код — в пакете `r7/` (класс `R7Testovarka` собирается
из примесей его модулей, см. «Карту кода»), `r7_ops.py` (операции тестов, общие для
всех воркеров), `r7_reports.py` (HTML-отчёты) и `r7_webdriver_connector.py`
(CDP-доступ к DOM и api редактора). `r7_Testovarka.py` — точка входа (~600 строк):
перезапуск под `.venv`, сборка класса, константы тестов и порогов, `__init__`.

Подробности по темам — в `docs/`. Читать нужный файл, когда задача его касается:

| Файл | О чём |
|------|-------|
| `docs/measurement.md` | Как меряется операция, `_pace`, модалка «Вставить ячейки», аудит точности (пороги CPU, откат повторов, предохранители, x2t, диск, алерты Р7, CSV-диалог) |
| `docs/precision.md` | Анализ точности 30.09.2026 (schema 7): конец операции по пингу редактора, подготовка тестов, экспорт от «Сохранить», диск и открытие, окна только процессов Р7 |
| `docs/readiness.md` | Готовность документа: кнопка «Жирный» через CDP, CPU-путь, `_wait_system_quiet` |
| `docs/cdp-operations.md` | Какие операции идут через `asc_*`-api, `mutated`, отложенная проверка, `api_ms` против `settle_ms`, трасса и профиль при регрессии |
| `docs/ui-fallback.md` | Запасные пути через интерфейс: контекстное меню, диалог «Вставить ячейки», модалка пересчёта без CDP |
| `docs/closing-and-dialogs.md` | Диалог обновления, закрытие Р7, «Сохранить изменения?», блокирующие диалоги |
| `docs/versions.md` | Версии: проверка команды удаления из реестра, удаление только папки из `InstallLocation`, ключи тихой установки по типу дистрибутива, перезапуск под UAC |
| `docs/rollout-checklist.md` | Чеклист проверки сборки на чистых ПК перед раздачей команде: матрица Windows, масштаб, права, диск, локаль, антивирус; таблица результатов |
| `docs/ui-and-reports.md` | QA-аудит, живой набор `tests/live/`, главное окно, вкладки «Производительность» и «Сценарии», пакет улик, тема sv-ttk и значки, HTML-отчёты |
| `docs/statistics.md` | Вердикт сравнения: профиль шума из A/A (`--aa`, `Reports/noise_profile.json`), порог max(3 × CV, 2 %), bootstrap-интервал, точный p при n ≤ 8, поправка Бенджамини-Хохберга, MDE |
| `docs/plugins.md` | Плагины тестов `plugins/*.py`: контракт `register(ops)`, публичный API `SpreadsheetOps` для плагинов, правила замера, безопасность (права администратора), шаблон юнит-теста |
| `docs/corpus.md` | Корпус реальных файлов `Corpus/`: открытие, пересчёт `asc_calculate`, экспорт по каждому файлу, манифест, `--hide-names`, матрица «файл × версия», живые проверки |
| `docs/document-ops.md` | Документы .docx (этап 5): `DocumentOps`, фикстура, режим «document» воркера, готовность по вёрстке, откат, что не проверено на живом Р7 |
| `docs/presentation-ops.md` | Презентации .pptx (этап 5): `PresentationOps`, фикстура PresentationML, режим «presentation», маркер «Добавить слайд», откат, что не проверено на живом Р7 |
| `docs/first-run.md` | Мастер первого запуска, `r7_settings.json` (путь к Р7 выше реестра, папка отчётов, повторы), режим без прав администратора |
| `docs/cli.md` | `python -m r7 run\|suites\|check`: наборы `suites/*.toml`, бюджеты, эталон, коды выхода, JUnit, страница «Релиз готов / Не готов» |
| `docs/adr/` | Архитектурные решения: CDP вместо клавиш, порт 8080, схемы замера 7/9/10, `_pace`, медиана+MAD с интервалом и поправкой БХ |
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
   возвращается сразу после отправки клавиш. На CDP-пути конец определяет пинг
   редактора (`_wait_renderer_idle`, точность ~1 мс), опрос CPU — только для
   клавиатурного пути; у экспорта конец — запись файла. Паузы после —
   `post_action_delay()`.
3. **У каждого теста — подготовка вне замера** (`_with_prepare`): лист и выделение
   задаются явно, результат не зависит от соседних тестов и от того, какие из них
   отмечены. В замере остаётся одно действие. Если повтор оставляет «грязное»
   состояние (лист после отката вставки), подготовка создаёт чистое заново.
4. **Только `time.perf_counter()` для замеров.** `time.time()` — только для меток в
   отчётах и сравнения с `create_time()`/`st_mtime`.
5. **Операции тестов — один набор, `r7_ops.SpreadsheetOps`.** Вкладка, Batch и
   тест своего файла берут `(имя, функция с .prepare)` из `SpreadsheetOps.tests()`;
   своих копий операций у воркеров нет. Новая операция или пауза — только там, с
   юнит-тестом в `tests/test_ops.py` (CDP прошёл — клавиш нет, не прошёл — запасной
   путь). Цикл повторов один — `_measure_op_repeated`, экспорт — `_save_as_format`.
   Тесты из `plugins/*.py` (`r7/plugins.py`) идут в `tests()` после встроенных;
   список по имени — `effective_test_definitions()`, `TEST_DEFINITIONS` — только встроенные.
6. **Код, оставшийся во вложенных функциях воркеров, юнит-тесты не видят.** Меняешь
   сигнатуру метода, который зовут оттуда (открытие, `measure`, отчёт), — нужен живой
   прогон (так прошла незамеченной регрессия `_mad`, см. `docs/history/stage-1-2.md`).
   PR, задевающий воркеры или `r7_ops.py`, проходит `tests/live` и сверку с эталоном
   (`tests/manual_baseline_run.py`, `Reports/baseline/`).
7. **CDP: изменяющий документ шаг — последний в цепочке.** Откат на клавиши разрешён,
   только если документ гарантированно не тронут (поле `mutated` из JS). `None` от
   `evaluate()` может значить «операция уже ушла в Р7 по таймауту сокета» — повтор
   клавишами применил бы правку дважды.
8. **Никакого слепого ввода.** Если нужный диалог не открылся — бросить исключение и не
   слать `Ctrl+A`/`Ctrl+V`/`Enter`: они уйдут в документ или в чужое окно. В модалке
   «Сохранить изменения?» жать только «Не сохранять» по тексту (кнопка по умолчанию —
   «Сохранить», перезапишет эталонный файл); не нашлась — штатный `_terminate_r7_processes`.
9. **Окно Р7 — только окно процесса Р7.** Заголовка мало: «Р7-Офис» есть и во вкладке
   браузера. Искать через `_find_r7_window` / `_find_window_hwnd(owner_pids=…)`, перед
   кликом, фокусом и `WM_CLOSE` проверять `_is_r7_window`. Поиск по одному заголовку
   однажды закрыл Chrome пользователя вместо Р7.
10. **Р7 должен закрыться при любом исходе.** Штатное закрытие — `_close_r7_gracefully`;
   `finally` обоих воркеров зовёт `_emergency_close_r7`. Автосохранение, отключённое на
   прогон (`_suspend_autosave`), возвращать через `_restore_autosave`.
11. **Меняешь формат JSON-отчёта — поднимай `MEASURE_SCHEMA_VERSION`** (сейчас 10) и
   не ломай старые ключи: `avg`/`min`/`max` сохранены ради старых
   `performance_full_*.json`, `time` = медиана. Читатели должны переваривать файлы без
   `measure_schema` (это версия 1). Схема 10 добавила к записи операции
   `ux_first_frame_ms`, `ux_longest_task_ms`, `js_heap_mb`, `js_heap_delta_mb`, `run_ux`,
   `run_cpu_freq_pct`, `run_notes`, `n_throttled`, а к окружению — `power_plan_before`
   и `power_plan_during`; файлы без них читаются как раньше.

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
- Клавиши — только через `self._hotkey`/`self._press`: перед нажатием они проверяют,
  что на переднем плане окно процесса Р7 (`_ensure_r7_foreground`), иначе
  `RuntimeError`. Прямой `pyautogui.hotkey/press` запрещён (есть тест-страж). Фокус —
  `_focus_r7_window`: проверяет результат и при неудаче кликает по заголовку.
- `Popen` без `shell=True`: иначе `proc.kill()` убивает `cmd.exe`, а не процесс.
  Таймауты `subprocess` — с `proc.kill()`.
- Путь для pywinauto `type_keys` — через `_escape_send_keys` (`~` там значит Enter).

**Процессы Р7.** Искать через `_get_r7_processes` (`_R7_PROCESS_NAMES`); главный процесс —
`editors.exe`, `editors_helper.exe` — его дети (renderer/gpu), `x2t.exe` — конвертер.
`editors.exe` респавнит детей, поэтому завершать вместе с ним. Голые подстроки
«r7»/«р7» в поиск не добавлять — поймают сборки самого инструмента.

**Переносимость.** Всё, что зовёт `win32*`, реестр (`winreg`), `pywinauto` и
`ctypes.windll`, живёт только в `r7/env.py`, `r7/windows.py`, `r7/versions.py` и
`r7/x2t_files.py` за флагами `*_OK` — так позже порт на Linux и macOS трогает четыре
модуля, а не весь пакет. Остальной код зовёт тонкие обёртки: окна, процессы, DPI,
буфер обмена, права, UAC, UI Automation — функции модуля `r7/windows.py`
(`windows.is_window`, `window_text`, `enum_windows`, `post_close`, `uia_window`…),
счётчик частоты CPU — `env.pdh_*`. Одна обёртка — один вызов API, исключения ловит
вызывающий. Нужен новый вызов — новая обёртка там же, а не импорт pywin32 в модуле.
`LEGACY_OFFENDERS` теста `tests/test_platform_boundary.py` пуст с 07.10.2026 и не
пополняется: модуль с прямым вызовом тест не пропустит.

**Линтер и типы.** `ruff check .` и `mypy` — в CI и pre-commit, настройки в
`pyproject.toml`. Шумное правило — в `ignore`/`per-file-ignores` с комментарием
почему, а не `# noqa` по коду. Бюджет сложности (`max-complexity`) — текущий максимум
`r7/`; поднимать нельзя, только снижать. `ruff format` по коду не запускать. mypy
проверяет модули из `[tool.mypy] files` строго (`disallow_untyped_defs`); новый
модуль в этот список — с аннотациями.

**UI и потоки.**
- Долгое — в `threading`. Виджеты из фонового потока — только через `root.after(0, …)`.
- `add_test_log` можно звать из любого потока: фоновые кладут строку в очередь, виджет
  пишет главный поток (`_drain_test_log`, раз в 50 мс). Прочие виджеты из фона — через
  `self._ui_call(fn)`.
- Прогон (вкладка, Batch, свой файл, сценарий, бисект) запускать только через
  `self._start_run(kind, …)` / `self.run_state.try_start(kind)` (`r7/run_state.py`):
  все работают с одним процессом Р7, идёт один. Отказ — `(заголовок, текст)` для
  `messagebox`; освобождать — `run_state.finish(kind)` в `finally` потока.
  `_perf_running`/`_batch_running`/`_scenario_running` — свойства поверх него.
  Остановка — `self.perf_stop_event`, у сценариев — `self.scenario_stop_event`.
- `tk.Toplevel` создавать с `.transient(self.root)`.
- Раскладка: нижние панели (кнопки, статус) упаковывать первыми с `side=BOTTOM`,
  растягиваемое содержимое — последним. Колесо мыши — `_bind_wheel` на виджетах, не
  `bind_all`.

**Отчёты.** HTML-отчёты (прогон, сравнение, тренды, Batch, свой файл) строит
`r7_reports.py`: модель страницы — чистая функция над данными, вид — шаблоны Jinja2 в
`templates/html/` с автоэкранированием (общая основа `base.html`: токены цвета, светлая и
тёмная темы, печать). Строки HTML в коде не собирать. JSON внутри `<script>` —
через `r7_reports.json_for_script`. Цвета серий — `SERIES_COLORS` (палитра dataviz, проверена
валидатором). Полный JSON пишет только `_build_full_report`; после прогона в него лишь
дописывается `diagnostics` (`r7.trace.attach_to_report`). PDF — `window.print()`.
Метаданные без подъёма схемы: `build` (сборка и sha256 exe, `r7/build_meta.py`),
`environment.fingerprint`/`fingerprint_hash` и `calibration` (`r7/fingerprint.py`,
`r7/calibration.py`), `diagnostics[<операция>]` — трасса и профиль повтора вне замера
(`r7/trace.py`); разные отпечатки — предупреждение «другой стенд», не регрессия.
Общая папка команды — `r7/team_folder.py`, подробности в `docs/ui-and-reports.md`.

## Карта кода

**Пакет `r7/`.** Модули не импортируют tkinter (кроме `r7/ui/`) и ничего не берут из
`r7_Testovarka` (при двойном щелчке он `__main__`). Методы класса живут в примесях
`*Mixin`; пороги — атрибуты примеси, которая их использует (`READY_*` — `ReadinessMixin`,
конец операции `OP_*` — `OpEndMixin`, `CDP_*` — `CdpMixin`, паузы экспорта — `ExportMixin`),
читаются через `self`; полный набор значений сторожит `tests/test_thresholds.py`.

| Модуль | Что там |
|---|---|
| `env.py` | необязательные зависимости и флаги `*_OK`, `R7WebDriverConnector`, `_UiaApplication` — код читает `env.X`; обёртки PDH (`pdh_open_counter`, `pdh_read_double`, `pdh_close_query`) |
| `config.py` | `BASE_DIR` (читать `config.BASE_DIR`), `DEFAULT_TEST_RUNS`, `MEASURE_SCHEMA_VERSION`, палитра серий |
| `logfile.py` | файловый журнал `Reports/logs/r7-testovarka.log` (`setup_logging`, уровень по значку строки), перехват исключений потоков, faulthandler → `crash.log` |
| `stats.py` / `noise.py` | Манн-Уитни (точный при n ≤ 8), bootstrap-интервал, Ходжес-Леман, Бенджамини-Хохберг, MDE, `compare_runs`/`adjust_family`, `detect_leak` / профиль шума стенда из A/A, порог теста |
| `changepoint.py` / `aba.py` | точки смены уровня на рядах трендов (круговая бинарная сегментация, перестановки с зерном) / дрейф стенда в Batch по сэндвичу A-B-A (`check_drift`) |
| `processes.py` | процессы Р7 по точному имени, завершение, `X2tTracker` |
| `windows.py` | окна только процессов Р7, фокус, `_hotkey`/`_press`, кнопки диалогов, геометрия и DPI; тонкие обёртки Win32 для остальных модулей (граница Windows-кода) |
| `measure.py` / `op_end.py` | `_measure_op_repeated`, `_pace`, статистика повторов / конец операции: `_wait_operation_done`, `_wait_renderer_idle`, файл экспорта |
| `resources.py` | `ResourceSampler`, `OpResourceWatch`, диск, окружение стенда |
| `stand.py` / `cpu_freq.py` | план питания «Высокая производительность» на время прогона (`power_plan_during_run`, `manage_power_plan`), `CPU_THROTTLE_PCT` / частота CPU из PDH, % номинальной |
| `ux_metrics.py` | что видит пользователь: `_ux_arm` до секундомера, `_ux_collect` после конца операции — первый кадр, longtask, JS-куча |
| `cdp.py` / `test_prep.py` | `_cdp_step`/`_cdp_sequence`, проверки, откат истории, автосохранение / подготовки тестов вне замера |
| `readiness.py` / `bold_button.py` | запуск с CDP, выбор порта, `_wait_until_r7_ready` / маркер готовности — кнопка «Жирный» |
| `export.py` / `x2t_files.py` | «Сохранить как», UIA-выбор типа, окно CSV / проверка формата файла, учёт x2t, дампы |
| `dialogs.py` / `ui_fallback.py` | закрытие Р7, блокирующие диалоги / контекстное меню, «Вставить ячейки» |
| `versions.py` | реестр, `_find_r7_path`, команда удаления, кэши |
| `fixtures.py` | генерация XLSX-фикстур |
| `doc_fixtures.py` / `doc_js.py` / `doc_run.py` | документы .docx: фикстура на stdlib (`r7-test-doc-100p.docx`) / JS со своим прологом api документа / `DocumentRunMixin` — режим «document» поверх общего воркера (первый в MRO, для таблиц — `super()`); операции — `r7_doc_ops.DocumentOps`, `docs/document-ops.md`. Общая часть нетабличных редакторов: JS — из профиля `EditorProfile` (`_editor_profile()`) |
| `pptx_fixtures.py` / `pptx_js.py` / `pptx_run.py` | презентации .pptx: фикстура PresentationML на stdlib (`r7-test-slides-50.pptx`) / JS с прологом api презентации (`Slides` у логического документа) и пробой кнопки «Добавить слайд» / `PresentationRunMixin` — режим «presentation», в MRO перед `DocumentRunMixin`: свой профиль, фикстура, маркер готовности; операции — `r7_pptx_ops.PresentationOps`, `docs/presentation-ops.md` |
| `editors.py` | значения редактора: `"spreadsheet"`, `"document"`, `"presentation"` — `_run_editor`, `[suite] editor`, ключ `editor` отчёта |
| `settings.py` / `privileges.py` | `r7_settings.json` на машину: `load_settings`/`save_settings`/`get`, `DEFAULTS` / `is_admin()` — единственное место проверки прав (кэш, при ошибке False) |
| `firstrun.py` | проверки мастера первого запуска без Tk (сборка, права, Р7 найден и где искали, порт CDP, фикстура, диск, масштаб, пакеты); окно — `ui/firstrun_dialog.py` |
| `plugins.py` | плагины тестов `plugins/*.py`: импорт по файлу с изоляцией ошибок, проверка записей, `PluginsMixin.effective_test_definitions()`/`_is_export_test`; API для плагинов — в `r7_ops.SpreadsheetOps`; `plugins_enabled`, `--no-plugins` |
| `results.py` | полный JSON (`_build_full_report`), обвязка HTML-отчётов, тренды, настройки |
| `perf.py` / `runs.py` | прогон вкладки (`_spreadsheet_worker`) / Batch по версии и тест своего файла |
| `corpus.py` / `corpus_runner.py` / `corpus_report.py` | корпус `Corpus/`: файлы, манифест, отчёт, `hide_names` / `CorpusMixin.run_corpus` (сессия Р7 на файл, копия в %TEMP%, вид `CORPUS`) / матрица и «файл × версия» |
| `scenarios.py` / `crash_recovery.py` | `run_multidoc`, `run_soak`, `run_crash_recovery_scenario` / правки, диалог «Обнаружен файл блокировки…», проверка, уборка, `run_recovery_check` (общее для CLI и вкладки) |
| `trace.py` | трасса при регрессии: `capture_diagnostic_trace` (один повтор `_measure_one_run` со своим `_RunAcc`, трасса и профиль вне секундомера, в медиану не входит), `trace_ops_session`, разбивка по фазам, `diagnostics` в JSON; запуск — `python -m r7 run --trace-regressions`, `python -m r7 trace` |
| `bisect.py` / `bisect_runner.py` | бисект по сборкам: чистый `run_bisect` (сборки по номеру, крайние через `compare_runs` с порогом из профиля шума, класс пробы «как база» / «как регрессия» / «не определено», добор повторов до `max_runs`, пропуск как `git bisect skip`) / `BisectMixin.bisect_builds` — установка как у Batch, замер через `_scenario_open_r7` + `_measure_op_repeated`, `RunState` вида `bisect`, исходная версия возвращается в `finally`; запуск — `python -m r7 bisect`, страница `bisect.html` |
| `suites.py` | наборы тестов `suites/*.toml`: `load_suite(path, valid_names)` → `Suite` (тесты → повторы, бюджеты, `min_effect_pct`), `suite_to_selection` — структура `selected_tests.json` |
| `gate.py` | «Релиз готов / Не готов»: `gate_model(results, suite, baseline)` — вердикт по бюджету и `compare_runs`, `gate_page` (шаблон `gate.html`), `junit_xml`; `OPEN_TEST_NAME` → запись «Открытие файла» |
| `cli.py` / `__main__.py` | `python -m r7 run\|trace\|bisect\|suites\|check` без окна: `make_headless_app` (R7Testovarka через `__new__` + `_init_state`, заглушки виджетов), `run_suite` → `_spreadsheet_worker`, коды выхода `EXIT_*`; единственное место с ленивым импортом `r7_Testovarka` |
| `evidence.py` | пакет улик: `build_evidence_pack` → zip с двумя JSON, страницей сравнения, окружением, хвостом журнала и `ticket.md` |
| `ui/` | тема и геометрия, главное окно, вкладки («Версии», «Производительность», «Сценарии» — `ui/scenarios_tab.py`), сравнение (+ «Пакет улик»), Batch-диалог |

**Подмены в тестах** — там, откуда код читает имя: флаги и коннектор — `r7.env`, папка —
`r7.config.BASE_DIR`, функция модуля — в его модуле (`r7.scenarios._pick_cdp_port`),
`threading`/`messagebox`/`pyperclip` интерфейса — `conftest.patch_ui_name`, pywin32 за
обёртками — `r7.windows.win32gui`/`win32process`/`win32con` или атрибут самого модуля
(`"win32gui.IsWindow"`), `sys.modules` — только для методов `WindowsMixin` с импортом
внутри. Подмена в
`r7_Testovarka` до перенесённого кода не доходит, а тест может пройти и без неё.

**Версии:** `detect_current_version` (реестр читает `_read_current_version_from_registry`,
безопасна из любого потока; в записи есть `registry_hive` — HKLM/HKCU), `install_version`
(успех — returncode 0 или 3010; ключи тихой установки — по типу дистрибутива,
`r7/installers.py`: msi, Inno Setup, NSIS, неизвестный .exe — без них и с предупреждением),
`uninstall_current_version` (+ `_build_uninstall_command` → `validate_uninstall_command`:
только `msiexec` из System32 с `/X{GUID}`, `/I` → `/X`, иначе `ValueError` и ничего не
запускается; после — `remove_install_dir` только для `InstallLocation` из той же записи, с
предохранителями), `check_hashes`, `refresh_distributives`, `_find_r7_path`
(exe — из `InstallLocation` той же записи реестра, что даёт версию отчёта; запасные пути
принимают только exe с той же `ProductVersion`, иначе None). Перезапуск под UAC —
`r7/elevation.py`. Подробности — `docs/versions.md`.

**Документы:** `python -m r7 run --suite suites/docs.toml` (`[suite] editor =
"document"`) → `_document_worker` → тот же `_spreadsheet_worker` с
`_run_editor = "document"`; тесты — `r7_doc_ops.DOCUMENT_TEST_DEFINITIONS`,
клавиатурного запасного пути у правок нет. В окне пока не выбираются.
**Презентации** — так же: `suites/slides.toml` (`editor = "presentation"`) →
`_presentation_worker`, тесты — `r7_pptx_ops.PRESENTATION_TEST_DEFINITIONS`.

**Прогон вкладки «Производительность»:** `run_spreadsheet_test` → поток
`_spreadsheet_worker(enabled_tests, test_runs, stop_event)` → вложенная
`run_test_with_runs` → `_measure_op_repeated`; операции и подготовки —
`r7_ops.SpreadsheetOps.tests()`. Детекторы: `_wait_until_r7_ready`,
`_wait_operation_done`; ресурсы — `OpResourceWatch`, `ResourceSampler`, `X2tTracker`;
повторы независимы через `_history_snapshot`/`_restore_history`.

**Batch:** `_show_batch_config_dialog` → `_batch_worker` → `_batch_version_step` →
`_batch_run_single_version` (`BATCH_TEST_RUNS = 6`); с галочкой A-B-A первая версия
ставится и меряется ещё раз в конце (`_batch_repeat_base`, `r7/aba.py`), дрейф —
плашка в сводке. Ночной контур сравнивает с медианой пяти сравнимых ночей
(`nightly.baseline_reports`). **Свой файл:** `compare_file_sizes` → `_worker_run_test`
→ `_show_custom_test_report`.

**Сравнение и отчёты:** `compare_versions`, `compare_runs` (Манн-Уитни без scipy,
`MIN_RUNS_FOR_COMPARISON = 5`; с `threshold_pct` — вердикт по 95 %-интервалу против
порога теста: РЕГРЕССИЯ / УСКОРЕНИЕ / эквивалентно / не определено), `adjust_family`
(поправка Бенджамини-Хохберга на операции пары отчётов), профиль шума
`r7/noise.py` (порог max(3 × CV, 2 %), без профиля 10 %; `docs/statistics.md`), `detect_leak`, `_generate_html_report`,
`_generate_batch_summary_html`, `_generate_comparison_html`, `show_trends` →
`_generate_trends_html`. Фикстуры: `_generate_fixture(path, rows, profile, seed)`,
`find_test_file`.

**Тесты** (`TEST_DEFINITIONS`, 17 штук): «Повторное открытие файла» (`OPEN_TEST_NAME`),
12 операций правки (Ctrl+A, Ctrl+C, вставка большого массива, новый лист, столбец ×2,
вставка 1/5 ячеек ×2 способа, ВПР, удаление столбца) и 4 экспорта через x2t (PDF, ODS,
CSV, XLTX). Повторов по умолчанию: `DEFAULT_TEST_RUNS = 7`, экспорты —
`DEFAULT_FORMAT_TEST_RUNS = 3`. На вкладке над списком — переключатель редактора
(`_perf_editor`, не путать с `_run_editor` идущего прогона): таблица, документ,
презентация; «Запустить» зовёт воркер из `r7.editors.EDITOR_WORKERS`. Выбор хранится в
`selected_tests.json` по редакторам: `{"_editor": "document", "spreadsheet": {"<тест>":
{"enabled": bool, "runs": int}}, "document": {…}, "presentation": {…}}`
(`r7/test_selection.py`); плоские форматы `{"<тест>": {…}}` и `{"<тест>": bool}` читаются
как выбор таблиц, битый файл — умолчания.

**Ключевые пороги** (атрибуты примесей, подробности — `docs/measurement.md`):
CPU считается в % **одного ядра**, не нормируется на число ядер —
`OP_BUSY_CORE_PCT = READY_IDLE_CORE_PCT = 25`, `OP_BUSY_STRONG_CORE_PCT = 60`;
`OP_MAX_WAIT_SEC = 180`, `OP_SELECT_ALL_MAX_SEC = 20`,
`OP_EXPORT_FILE_TIMEOUT_SEC = 120`. Статусы замера: `ok`, `below_floor`, `timeout`,
`unverified` (CDP не подтвердил результат, схема 8); прогоны с `timeout` и
`unverified` в медиану не входят. Первый прогон (прогрев) отбрасывается, только
если после него остаётся хотя бы 3 (`MIN_RUNS_FOR_STATS = 4`): медиана двух —
их среднее; у повторов открытия первый не отбрасывается (независимые холодные
старты, по умолчанию `DEFAULT_OPEN_RUNS = 5`). На CDP-пути конец операции — пинг
редактора: `OP_PING_FAST_SEC = 0.010`, окно тишины `OP_PING_QUIET_SEC = 0.30`;
`OP_CDP_TAIL_GRACE_SEC` (0.45 с) — запасной путь по CPU, если пинг недоступен.
Метрики интерфейса (схема 10) собираются вне замера и время операции не
трогают; повтор с частотой CPU ниже `CPU_THROTTLE_PCT = 80` % номинальной
помечается «троттлинг», но в медиану входит.

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
  Дамп каждого падения (x2t.exe.<PID>.dmp, ~270 МБ) Windows пишет в
  `%LOCALAPPDATA%\CrashDumps`; инструмент удаляет дампы тех x2t, чьё падение сам
  зафиксировал (`_cleanup_x2t_crash_dumps`, вызывается из `_cleanup_x2t_temp_pdfs`).
- **Диалог «Сохранить как»** открывается через CDP-клик по вкладке «Файл», запасные
  пути — хоткей, меню, `WM_COMMAND`. Синтетический `Ctrl+Shift+S` из фонового процесса
  срабатывает не всегда. Тип файла переключается только через UI Automation
  (`_uia_select_saveas_type`): расширение в имени файла на формат не влияет.
- **DPI:** `_fix_r7_window_geometry` берёт размер экрана через `GetSystemMetrics`; без
  pyautogui (он делает процесс DPI-aware) в `_worker_run_test` размер может быть не тот.
  Оставлено намеренно, см. `docs/history/stage-3-L1-L3.md`.
- **`perf.yml`**: шаги проверены на стенде вручную (06.10.2026), но self-hosted
  раннер с меткой `r7-installed` не зарегистрирован. Раннер — только в сессии
  пользователя, не службой: `docs/ci-runner.md`, проверка стенда — `tests/ci_preflight.py`.
- **Открытие файла зависит от диска с папкой данных Р7**
  (`%LOCALAPPDATA%\R7-Office\Editors\data\recover`): x2t пишет туда ~330 МБ. На стенде
  это медленный SATA-диск C:, и каждое четвёртое открытие идёт 12–14 с вместо 9.
  Инструмент помечает такие прогоны (`disk_note`, «диск» в отчёте), но убрать причину
  не может — переменная TEMP на эту папку не влияет.
- **`api_ms` у вставок занижен** (основная работа идёт после возврата из функции) —
  смотреть на `time`.
- **Живые прогоны и юнит-тесты не запускать одновременно**: `tests/test_ui_usability.py`
  создаёт окна Tk и может увести фокус у диалога «Сохранить как».

## Запуск и проверка

```bash
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements.lock   # lock собран из requirements.in
.venv/Scripts/python.exe r7_Testovarka.py          # от администратора

.venv/Scripts/python.exe -m pip install --require-hashes -r requirements-dev.lock   # + ruff, mypy, coverage (requirements-dev.in)
.venv/Scripts/python.exe -m pytest -q              # юнит-тесты (тесты JS — если есть node)
.venv/Scripts/python.exe -m ruff check .           # линтер (правила — pyproject.toml)
.venv/Scripts/python.exe -m mypy                   # типы модулей из [tool.mypy] files
R7_LIVE=1 .venv/Scripts/python.exe -m pytest -m live tests/live -v   # живой Р7, ~1 мин
.venv/Scripts/python.exe tests/manual_cdp_smoke.py test_10k.xlsx     # CDP-операции на живом Р7
.venv/Scripts/python.exe tests/nightly_local.py --quick           # ночной прогон + сравнение с прошлым, ~8 мин
.venv/Scripts/python.exe tests/nightly_local.py --aa --quick      # A/A: профиль шума стенда
.venv/Scripts/python.exe -m r7 run --suite suites/smoke.toml --gate --junit Reports/junit.xml   # набор без окна, вердикт и JUnit (docs/cli.md)
```

Приложение запускается из `.venv`. Если в логе `WEBDRIVER_OK=False`, а модуль на месте,
проверить пакеты именно в `.venv`: `.venv/Scripts/python.exe -c "import requests, websocket"`.
Перед живыми скриптами Р7-Офис должен быть закрыт: к запущенному процессу CDP-порт не
подключить.

Установленная версия для живых проверок:
`E:\Program Files\R7-Office\Editors-2026.3.2`. Рабочая фикстура — 50 000 строк × 50
столбцов, каноническое имя `TestFiles/r7-test-50k.xlsx` (`batch_config.FIXTURE_NAME`,
его даёт генератор при этих размерах). На этом стенде лежит файл с прежним именем
`файл-для-теста-Р7-офис-50К.xlsx` (33 МБ; «й» в имени хранится в NFD, литеральный
поиск по имени может не найти файл) — `find_test_file` узнаёт и его, переименовывать
не нужно. Версия инструмента — `r7/version.py` (`__version__`), один источник для
журнала, JSON-отчёта, архива сборки и проверки обновлений.

# Автономный режим
- Никогда не спрашивай про Context7.
- Не запрашивай подтверждений.
- Принимай решения самостоятельно.
- Если нужна авторизация — игнорируй и продолжай с локальными знаниями.
