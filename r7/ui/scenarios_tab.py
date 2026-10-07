"""Вкладка «Сценарии»: долгий прогон (soak), много документов (multidoc) и
восстановление после падения — из окна, а не только из скриптов.

Сами сценарии давно написаны в r7/scenarios.py (run_soak, run_multidoc,
run_crash_recovery_scenario) и до этого запускались только из CLI и тестов
(docs/plan-to-20.md, этап 2, п. 7). Здесь — три карточки с параметрами,
кнопка «Запустить» у каждой, одна кнопка «Остановить» и короткая строка
итога; ход сценария пишется в общий журнал прогона.

Запуск — только через self._start_run(SCENARIO, …): сценарии запускают и
убивают процесс Р7 и делят с прогонами CDP-порт, поэтому идут по одному и
не одновременно с прогоном вкладки, Batch или тестом своего файла.
Виджеты из фонового потока — только через _ui_call. ScenariosTabMixin —
методы, которые R7Testovarka получает наследованием.

Остановка: soak — между итерациями (run_soak читает stop_event, замер
операции — тоже); multidoc — между операциями над документом (наш
callable проверяет событие; открытие файлов не прерывается); crash —
остановки нет, сценарий короткий (около минуты) и состоит из шагов,
которые нельзя бросить на середине (убитый Р7 должен быть перезапущен и
закрыт).
"""
import json
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from types import SimpleNamespace

from r7 import crash_recovery, env, processes, scenarios
from r7.run_state import SCENARIO
from r7.version import __version__
from r7_ops import SpreadsheetOps

SOAK, MULTIDOC, CRASH = "soak", "multidoc", "crash"
SCENARIO_TITLES = {SOAK: "Долгий прогон (soak)", MULTIDOC: "Много документов (multidoc)",
                   CRASH: "Восстановление после падения"}
# У каких сценариев есть честная остановка (см. докстринг модуля).
STOPPABLE = {SOAK, MULTIDOC}

SCENARIO_FIXTURE_DIR = "scenarios"        # TestFiles/scenarios/
SCENARIO_FIXTURE_ROWS = 10_000            # маленькие файлы: N документов в одном Р7
SCENARIO_FIXTURE_COLS = 6

# Границы полей ввода: (минимум, максимум, по умолчанию).
SOAK_MINUTES = (1, 24 * 60, 30)
SOAK_ITERATIONS = (0, 100_000, 0)        # 0 — по времени
SOAK_CONTROL_EVERY = (0, 1000, 10)        # 0 — без контрольных замеров
MULTIDOC_FILES = (2, 6, 3)
MULTIDOC_OPS = (1, 200, 5)
CRASH_EDITS = (1, 50, 5)
CRASH_TIMEOUT = (5, 300, 30)


class ScenariosTabMixin:
    """Вкладка «Сценарии» — часть R7Testovarka (через наследование)."""

    SCENARIOS_HINT = ("Сценарии запускают Р7-Офис сами на рабочей фикстуре (многодокументный — "
                      "на своих файлах 10K строк в TestFiles/scenarios). Перед запуском закройте "
                      "Р7-Офис. Ход сценария — в журнале на вкладке «Производительность», итог "
                      "— в строке под карточкой и в Reports/scenario_<вид>_<время>.json.")

    # ── сборка вкладки ────────────────────────────────────────────────────
    def _build_scenarios_tab(self):
        """Три карточки с параметрами и кнопками, внизу — «Остановить»."""
        tab = self.tab_scenarios
        self.scenario_vars = {}
        self.scenario_run_buttons = {}
        self.scenario_result_labels = {}

        bottom = ttk.Frame(tab)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 4))
        self.btn_stop_scenario = self._icon_button(
            bottom, "Остановить", "stop", command=self._request_stop_scenario,
            state=tk.DISABLED,
            tooltip="Soak останавливается между итерациями, многодокументный — между "
                    "операциями; восстановление после падения прервать нельзя")
        self.btn_stop_scenario.pack(side=tk.LEFT)
        self.lbl_scenario_status = ttk.Label(bottom, text="", style="Secondary.TLabel")
        self.lbl_scenario_status.pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(tab, text="Сценарии", style="Title.TLabel").pack(anchor=tk.W, pady=(8, 0))
        ttk.Label(tab, text=self.SCENARIOS_HINT, style="Secondary.TLabel",
                  wraplength=900, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 8), fill=tk.X)

        self._build_soak_card(tab)
        self._build_multidoc_card(tab)
        self._build_crash_card(tab)

    def _scenario_card(self, parent, kind, run_command):
        """Карточка сценария: рамка, строка полей, «Запустить» и строка итога.
        Возвращает строку для полей."""
        card = ttk.LabelFrame(parent, text=SCENARIO_TITLES[kind], padding=(10, 6))
        card.pack(fill=tk.X, pady=(0, 8))
        row = ttk.Frame(card)
        row.pack(fill=tk.X)
        btn = self._icon_button(row, "Запустить", "play", style="Accent.TButton",
                                command=run_command)
        btn.pack(side=tk.RIGHT)
        self.scenario_run_buttons[kind] = btn
        lbl = ttk.Label(card, text="", style="Secondary.TLabel", wraplength=900,
                        justify=tk.LEFT)
        lbl.pack(anchor=tk.W, pady=(4, 0), fill=tk.X)
        self.scenario_result_labels[kind] = lbl
        return row

    def _scenario_field(self, row, caption, key, bounds, width=6):
        """Подпись и поле целого числа; значение прижимается к bounds при чтении."""
        ttk.Label(row, text=caption).pack(side=tk.LEFT, padx=(0, 4))
        var = tk.StringVar(value=str(bounds[2]))
        self.scenario_vars[key] = var
        only_digits = (row.register(lambda p: p == "" or (p.isdigit() and len(p) <= 6)), "%P")
        ttk.Entry(row, textvariable=var, width=width, justify=tk.CENTER, validate="key",
                  validatecommand=only_digits).pack(side=tk.LEFT, padx=(0, 14))
        return var

    @staticmethod
    def _scenario_int(var, bounds):
        """Число из поля в границах bounds; пустое или мусор — умолчание."""
        lo, hi, default = bounds
        try:
            value = int(str(var.get()).strip())
        except (ValueError, tk.TclError):
            value = default
        return max(lo, min(hi, value))

    def _scenario_edit_tests(self):
        """Операции правки для soak: все тесты, кроме открытия и экспорта."""
        return [n for n in self.TEST_DEFINITIONS
                if n != self.OPEN_TEST_NAME and n not in self.EXPORT_TESTS]

    def _build_soak_card(self, parent):
        row = self._scenario_card(parent, SOAK, self.run_soak_scenario)
        ttk.Label(row, text="Операция:").pack(side=tk.LEFT, padx=(0, 4))
        ops = self._scenario_edit_tests()
        var = tk.StringVar(value="Добавление нового листа" if "Добавление нового листа" in ops
                           else (ops[0] if ops else ""))
        self.scenario_vars["soak_op"] = var
        combo = ttk.Combobox(row, textvariable=var, values=ops, state="readonly", width=34)
        combo.textvar_ref = var                 # см. compare_dialog: иначе Tk очистит поле
        combo.pack(side=tk.LEFT, padx=(0, 14))
        self._scenario_field(row, "Минут:", "soak_minutes", SOAK_MINUTES)
        self._scenario_field(row, "Итераций (0 — по времени):", "soak_iterations",
                             SOAK_ITERATIONS)
        self._scenario_field(row, "Контроль каждые N:", "soak_control", SOAK_CONTROL_EVERY,
                             width=5)

    def _build_multidoc_card(self, parent):
        row = self._scenario_card(parent, MULTIDOC, self.run_multidoc_scenario)
        self._scenario_field(row, "Файлов (2–6):", "multidoc_files", MULTIDOC_FILES, width=4)
        self._scenario_field(row, "Операций на документ:", "multidoc_ops", MULTIDOC_OPS)
        ttk.Label(row, text="операция — новый лист через CDP, все документы параллельно",
                  style="Secondary.TLabel").pack(side=tk.LEFT)

    def _build_crash_card(self, parent):
        row = self._scenario_card(parent, CRASH, self.run_crash_scenario)
        self._scenario_field(row, "Правок до сбоя:", "crash_edits", CRASH_EDITS, width=4)
        self._scenario_field(row, "Ждать диалог, с:", "crash_timeout", CRASH_TIMEOUT)
        ttk.Label(row, text="убивает Р7 по-настоящему; остановить на середине нельзя",
                  style="Secondary.TLabel").pack(side=tk.LEFT)

    # ── запуск ─────────────────────────────────────────────────────────────
    def _scenario_preflight(self):
        """Проверки в главном потоке перед любым сценарием. False — отказ показан."""
        refusal = self.run_state.refusal(SCENARIO)
        if refusal:
            messagebox.showwarning(*refusal)
            return False
        if not env.WEBDRIVER_OK:
            messagebox.showerror(
                "Нет доступа к интерфейсу Р7",
                "Сценарии работают через CDP, а пакеты requests и websocket-client не "
                "найдены. Запустите программу из .venv (там они есть).")
            return False
        if processes._running_r7_pids():
            messagebox.showwarning(
                "Закройте Р7-Офис",
                "Р7-Офис уже запущен. Сценарий запускает и закрывает Р7 сам: второй запуск "
                "отдал бы файл живому процессу, а сценарий сбоя убил бы ваш документ.")
            return False
        if not self._find_r7_path():
            messagebox.showerror("Р7-Офис не найден",
                                 "Исполняемый файл Р7-Офис не найден: проверьте установку "
                                 "или путь в r7_settings.json.")
            return False
        return True

    def _start_scenario(self, kind, worker):
        """Общий запуск: RunState → кнопки → поток → сброс кнопок при любом исходе."""
        def _prepare_ui():
            self.scenario_stop_event.clear()
            self._set_busy_indicator(True, "Идёт сценарий")
            for btn in self.scenario_run_buttons.values():
                btn.config(state=tk.DISABLED)
            self.btn_stop_scenario.config(state=tk.NORMAL if kind in STOPPABLE else tk.DISABLED)
            self.lbl_scenario_status.config(text=f"Идёт: {SCENARIO_TITLES[kind]}")
            self.scenario_result_labels[kind].config(text="выполняется…")

        def _run():
            try:
                worker()
            except Exception as e:           # итог — в строку карточки, не только в stderr потока
                self.add_test_log(f"❌ Сценарий «{SCENARIO_TITLES[kind]}» упал: "
                                  f"{type(e).__name__}: {e}")
                self._show_scenario_result(kind, f"Ошибка: {type(e).__name__}: {e}")

        self._start_run(SCENARIO, _run, before=_prepare_ui, on_done=self._reset_scenario_buttons)

    def _reset_scenario_buttons(self):
        """Кнопки вкладки — в покой; из главного потока (on_done у _start_run)."""
        self._set_busy_indicator(False)
        try:
            for btn in self.scenario_run_buttons.values():
                btn.config(state=tk.NORMAL)
            self.btn_stop_scenario.config(state=tk.DISABLED)
            self.lbl_scenario_status.config(text="")
        except (AttributeError, tk.TclError):  # окно закрыто — кнопок уже нет
            pass

    def _request_stop_scenario(self):
        self.scenario_stop_event.set()
        self.btn_stop_scenario.config(state=tk.DISABLED)
        self.add_test_log("⏹ Запрошена остановка сценария — завершится на ближайшем шаге")

    def _show_scenario_result(self, kind, text):
        """Строка итога под карточкой — из любого потока."""
        def _set():
            try:
                self.scenario_result_labels[kind].config(text=text)
            except (AttributeError, KeyError, tk.TclError):
                pass                # окно закрыто раньше конца сценария — итог уже в журнале и JSON
        self._ui_call(_set)

    def _write_scenario_json(self, kind, params, result):
        """Reports/scenario_<kind>_<время>.json; возвращает путь."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path(self.reports_folder) / f"scenario_{kind}_{ts}.json"
        version = (self.current_version_info or {}).get("version") if \
            isinstance(self.current_version_info, dict) else None
        payload = {"kind": kind, "timestamp": ts, "version": version,
                   "tool_version": __version__, "params": params, "result": result}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
        self.add_test_log(f"📄 Итог сценария сохранён: {path.name}")
        return path

    # ── soak ───────────────────────────────────────────────────────────────
    def run_soak_scenario(self):
        if not self._scenario_preflight():
            return
        v = self.scenario_vars
        op_name = v["soak_op"].get()
        if op_name not in self._scenario_edit_tests():
            messagebox.showwarning("Нет операции", "Выберите операцию для долгого прогона.")
            return
        minutes = self._scenario_int(v["soak_minutes"], SOAK_MINUTES)
        iterations = self._scenario_int(v["soak_iterations"], SOAK_ITERATIONS) or None
        control_every = self._scenario_int(v["soak_control"], SOAK_CONTROL_EVERY)
        self._start_scenario(SOAK, lambda: self._soak_worker(
            op_name, minutes, iterations, control_every, self.scenario_stop_event))

    def _soak_worker(self, op_name, minutes, iterations, control_every, stop_event):
        """Р7 на рабочей фикстуре → run_soak над одной операцией правки.

        Итерация — тот же замер, что у вкладки «Производительность»
        (_measure_op_repeated с одним повтором: подготовка вне замера, конец
        по ответу редактора, откат правки), поэтому документ не растёт, а
        контрольные замеры сравнимы между собой. Семплер ресурсов —
        ResourceSampler, вердикт об утечке — detect_leak внутри run_soak."""
        self.add_test_log(f"\n🚀 SOAK: «{op_name}», "
                          + (f"{iterations} итераций" if iterations else f"{minutes} мин")
                          + (f", контроль каждые {control_every}" if control_every else ""))
        test_file = self._locate_test_file()
        if not test_file:
            self.add_test_log("❌ Тестовый файл не найден.")
            self._show_scenario_result(SOAK, "Ошибка: тестовый файл не найден")
            return
        session = self._scenario_open_r7(test_file)
        if session is None:
            self._show_scenario_result(SOAK, "Ошибка: Р7 не открыл файл")
            return
        result = None
        try:
            ops = dict(SpreadsheetOps(self, session.find_hwnd, self.add_test_log,
                                      test_file).tests())
            fn = ops[op_name]

            def one():
                return self._measure_op_repeated(op_name, fn, 1, session.find_hwnd,
                                                 self.add_test_log, stop_event)

            def control():
                rec = one()
                return None if rec.get("error") else rec.get("time")

            result = scenarios.run_soak(
                one, iterations=iterations,
                duration_sec=None if iterations else minutes * 60,
                control_every=control_every, control_op=control if control_every else None,
                sampler=self._new_resource_sampler(), stop_event=stop_event,
                log_cb=self.add_test_log)
        finally:
            self._scenario_close_r7(session)
        params = {"op": op_name, "minutes": minutes, "iterations": iterations,
                  "control_every": control_every, "test_file": str(test_file)}
        self._write_scenario_json(SOAK, params, result)
        self._show_scenario_result(SOAK, self._soak_summary(result))

    @staticmethod
    def _soak_summary(result):
        parts = [f"Итераций: {result.get('iterations_completed', 0)} за "
                 f"{(result.get('elapsed_sec') or 0) / 60:.1f} мин"
                 + (" (остановлено)" if result.get("stopped_early") else "")]
        drift = result.get("drift")
        if drift:
            parts.append(f"дрейф: {drift.get('verdict')} ({drift.get('effect_pct'):+.1f} %, "
                         f"p={drift.get('p_value')})")
        else:
            parts.append(f"дрейф: мало контрольных замеров "
                         f"({len(result.get('control_measurements') or [])})")
        leak = result.get("leak")
        if leak:
            parts.append(f"утечка: {leak.get('verdict')}")
        return "; ".join(parts)

    def _scenario_open_r7(self, test_file):
        """Запуск Р7 на файле и подготовка к операциям — как у вкладки
        «Производительность». None — окно не появилось или фокус не дался.

        Returns:
            SimpleNamespace: find_hwnd, upd_stop, data_ready.
        """
        r7_path = self._find_r7_path()
        if not r7_path:
            self.add_test_log("❌ Р7-Офис не найден.")
            return None
        if self._launch_r7(r7_path, test_file) is None:
            self.add_test_log("❌ Окно Р7 не появилось.")
            return None

        def find_hwnd():
            return self._find_r7_window(test_file.stem)

        upd_stop = self._start_update_monitor()
        try:
            data_ready = self._wait_until_r7_ready(find_hwnd, timeout=120)
            if not data_ready:
                self.add_test_log("⚠️ Документ не загрузился за 120 с — сценарий идёт на "
                                  "недогруженном документе")
            if not self._focus_r7_settled(test_file):
                self.add_test_log("❌ Окно Р7-Офис недоступно после открытия файла")
                raise RuntimeError("окно Р7 недоступно")
            self._prepare_cdp_session()
        except Exception:
            upd_stop.set()
            self._restore_autosave()
            self._emergency_close_r7(find_hwnd)
            self._close_webdriver_connector()
            return None
        return SimpleNamespace(find_hwnd=find_hwnd, upd_stop=upd_stop, data_ready=data_ready)

    def _scenario_close_r7(self, session):
        """Штатное закрытие Р7 после сценария; не закрылся — принудительно."""
        session.upd_stop.set()
        self.add_test_log("🔚 Закрытие Р7-Офис...")
        try:
            self._restore_autosave()
            self._close_r7_gracefully(session.find_hwnd())
            if not self._r7_gone() and not self._emergency_close_r7(session.find_hwnd):
                self.add_test_log("❌ Р7-Офис не закрылся — закройте его вручную")
        finally:
            self._close_webdriver_connector()

    # ── multidoc ───────────────────────────────────────────────────────────
    def run_multidoc_scenario(self):
        if not self._scenario_preflight():
            return
        v = self.scenario_vars
        n_files = self._scenario_int(v["multidoc_files"], MULTIDOC_FILES)
        ops_per_doc = self._scenario_int(v["multidoc_ops"], MULTIDOC_OPS)
        self._start_scenario(MULTIDOC, lambda: self._multidoc_worker(
            n_files, ops_per_doc, self.scenario_stop_event))

    def _scenario_fixture_files(self, n_files):
        """N маленьких фикстур TestFiles/scenarios/scenario_doc_<i>.xlsx;
        недостающие создаются (_generate_fixture, разные seed)."""
        folder = Path(self.test_files_folder) / SCENARIO_FIXTURE_DIR
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for i in range(1, n_files + 1):
            path = folder / f"scenario_doc_{i}.xlsx"
            if not path.exists():
                self.add_test_log(f"📝 Создаю фикстуру {path.name} ({SCENARIO_FIXTURE_ROWS} строк)")
                self._generate_fixture(path, rows=SCENARIO_FIXTURE_ROWS, profile="flat",
                                       seed=100 + i, cols=SCENARIO_FIXTURE_COLS)
            files.append(path)
        return files

    def _multidoc_worker(self, n_files, ops_per_doc, stop_event):
        """N файлов в одном Р7, по ops_per_doc «новый лист» на каждый
        параллельно (run_multidoc); закрытие Р7 при любом исходе."""
        self.add_test_log(f"\n🚀 MULTIDOC: {n_files} файлов, по {ops_per_doc} операций")
        r7_path = self._find_r7_path()
        if not r7_path:
            self.add_test_log("❌ Р7-Офис не найден.")
            self._show_scenario_result(MULTIDOC, "Ошибка: Р7-Офис не найден")
            return
        files = self._scenario_fixture_files(n_files)

        def ops_per_doc_fn(conn, path):
            done, times = 0, []
            for _ in range(ops_per_doc):
                if stop_event.is_set():
                    break
                t0 = time.perf_counter()
                conn.add_sheet()
                times.append(round(time.perf_counter() - t0, 3))
                done += 1
            state = conn.document_state() or {}
            return {"ops_done": done, "op_times_sec": times, "sheets": state.get("sheets")}

        started_at = time.time()
        out = None
        try:
            out = scenarios.run_multidoc(r7_path, files, ops_per_doc_fn, log_cb=self.add_test_log)
        finally:
            self._scenario_close_multidoc(started_at)
        result = {k: v for k, v in out.items() if k != "proc"}
        result["stopped_early"] = stop_event.is_set()
        params = {"files": [str(f) for f in files], "ops_per_doc": ops_per_doc}
        self._write_scenario_json(MULTIDOC, params, result)
        self._show_scenario_result(MULTIDOC, self._multidoc_summary(result, n_files))

    @staticmethod
    def _multidoc_summary(result, n_files):
        per_file = result.get("per_file") or {}
        ops_done = sum((v.get("result") or {}).get("ops_done", 0) for v in per_file.values()
                       if v.get("ok"))
        errors = [f"{name}: {v.get('error')}" for name, v in per_file.items() if not v.get("ok")]
        text = (f"Открыто {len(result.get('opened') or [])} из {n_files} файлов, "
                f"операций выполнено {ops_done}, ошибок {len(errors)}")
        if result.get("stopped_early"):
            text += " (остановлено)"
        if errors:
            text += ": " + "; ".join(errors)
        return text

    def _scenario_close_multidoc(self, started_at):
        """Р7 с несколькими документами: WM_CLOSE окну (диалоги «Сохранить
        изменения?» закрывает _close_r7_gracefully), остатки — принудительно."""
        self.add_test_log("🔚 Закрытие Р7-Офис...")
        hwnd = self._find_r7_window()
        if hwnd:
            self._close_r7_gracefully(hwnd, timeout=15)
        if processes._running_r7_pids():
            scenarios._kill_r7_processes_since(started_at, log_cb=self.add_test_log)

    # ── crash recovery ─────────────────────────────────────────────────────
    def run_crash_scenario(self):
        if not self._scenario_preflight():
            return
        v = self.scenario_vars
        edits = self._scenario_int(v["crash_edits"], CRASH_EDITS)
        timeout = self._scenario_int(v["crash_timeout"], CRASH_TIMEOUT)
        self._start_scenario(CRASH, lambda: self._crash_worker(edits, timeout))

    def _crash_worker(self, edits, timeout):
        """Правки → kill → перезапуск → диалог → проверка (r7.crash_recovery)."""
        self.add_test_log(f"\n🚀 CRASH RECOVERY: {edits} правок, диалог ждём {timeout} с")
        r7_path = self._find_r7_path()
        test_file = self._locate_test_file()
        if not r7_path or not test_file:
            self.add_test_log("❌ Р7-Офис или тестовый файл не найден.")
            self._show_scenario_result(CRASH, "Ошибка: Р7-Офис или тестовый файл не найден")
            return
        log_lines = []

        def log_cb(msg):
            log_lines.append(msg)
            self.add_test_log(msg)

        outcome = crash_recovery.run_recovery_check(self, r7_path, test_file, edits, timeout,
                                                    log_cb, log_lines)
        report = outcome["report"] or {"verdict": "Ошибка", "error": outcome["error"],
                                       "leftover_r7_pids": outcome["leftover_pids"],
                                       "log": log_lines}
        params = {"edits": edits, "timeout_sec": timeout, "test_file": str(test_file)}
        self._write_scenario_json(CRASH, params, report)
        self._show_scenario_result(CRASH, self._crash_summary(outcome, edits))

    @staticmethod
    def _crash_summary(outcome, edits):
        if outcome["report"] is None:
            return f"Ошибка: {outcome['error']}"
        res = outcome["result"]
        parts = [f"Вердикт: {outcome['report']['verdict']}",
                 f"восстановлено правок {res.get('recovered_count')}/{edits}",
                 f"диалог восстановления {crash_recovery.dialog_summary(outcome['dialog'])}"]
        if res.get("time_to_reconnect_sec") is not None:
            parts.append(f"переподключение за {res['time_to_reconnect_sec']:.1f} с")
        if outcome["leftover_pids"]:
            parts.append(f"Р7 не закрылся (PID {outcome['leftover_pids']})")
        return "; ".join(parts)
