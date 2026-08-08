# R7-Manager: x2t-тест, повторные прогоны, тёмная тема — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Autonomous mode:** the human partner has explicitly pre-authorized proceeding through plan-mandated review findings and design ambiguities via best-practice judgment, without pausing to ask — apply that authorization to every task in this plan. Escalate to BLOCKED only for a genuinely load-bearing, unresolvable structural problem (per the skill's own breaker rule), never for an ordinary judgment call.

**Goal:** Add a PDF-export test operation that reliably exercises the x2t converter process; let each stress-test operation run a configurable number of times (1–10) with averaged results; and restyle the whole Tkinter UI with a dark, IDE-like theme — all in one PR, on branch `worktree-ui-and-tests-improvement`.

**Architecture:** Same single-file app, `r7_Testovarka.py`. Three largely independent subsystems bundled into one plan/PR per the human partner's explicit instruction ("три улучшения в одном цикле разработки"): (A) one new test operation mirroring the existing 12, (B) a UI + JSON-schema change to run each operation N times, (C) a `ttk.Style`-based visual overhaul with zero behavioral change. Ordered so B lands before C touches `_build_perf_tab` (C restyles the widgets B introduces, so C must read B's actual structure, not guess at it).

**Tech Stack:** Python 3.14, Tkinter/ttk (`clam` theme — confirmed via Context7/tkdocs that only Tk's own renderer themes, not native `vista`/`winnative`, honor full `ttk.Style` color overrides on Windows), psutil, pyautogui, pywin32, openpyxl.

## Global Constraints

- **No test framework in this repo.** Verification throughout is `.venv/Scripts/python.exe -c "..."` / `py_compile`, exactly as in the prior plan (`docs/superpowers/plans/2026-08-07-resource-metrics-improvement.md`) — never a persisted test file.
- **Backward compatibility mandatory.** `compare_versions()` / `_generate_comparison_html()` must keep reading `results[].time` / `.ram` / `.cpu` and `summary.*` unchanged in meaning. New fields (`runs`, `avg`, `min`, `max` per result; the new test-selection JSON shape) are additive; `_batch_run_single_version`'s output shape is untouched by Task B (batch mode has no per-test run-count UI — it keeps producing single-run results, which the new-format reader must still accept).
- **Defensive coding.** Every new UI-automation step and every new file I/O (`os.remove` on the temp PDF, JSON load/save) wrapped in try/except, matching the existing codebase convention — log and continue, never crash the run.
- **Comments:** short Russian comments only where the WHY is non-obvious.
- **ttk theme reality check (documented, not glossed over):** plain `ttk.Style` cannot produce true CSS-style rounded corners, drop shadows, or per-row Treeview hover — those are native-OS-drawn or require custom Canvas widgets, out of scope for a `ttk.Style`-only implementation as the human partner explicitly specified. Where the spec asks for an effect ttk can't natively do, the task approximates it with the closest achievable technique and states the approximation in a code comment (e.g. "shadow" under the header → a 2px `tk.Frame` in a darker shade acting as a hairline, not a blurred shadow; "rounded button" → flat colored `ttk.Button` with `clam`-theme padding, not literal rounded corners).
- **Every task ends with `py_compile` passing** and a real commit with a `feat:`/`refactor:`/`docs:`-style message (never bare "fix").
- **GUI automation cannot be live-verified by any subagent or the controller** — no tool in this environment can click inside a real Tkinter/R7-Office window. Task A's PDF-export automation is implemented per spec with the documented hotkey→menu fallback, verified only by static code inspection and `py_compile`; this is called out explicitly in the final report as needing the human partner's manual smoke test, per the human partner's own instruction ("если хоткей не сработает — используй альтернативный способ... и задокументируй это").

## Color Palette (Task C — copy verbatim, single source of truth)

Add near the top of `r7_Testovarka.py`, after the existing imports and before `class R7Testovarka:`:

```python
COLORS = {
    "bg":            "#1E1E2E",  # основной фон
    "bg_card":       "#2A2A3E",  # фон карточек/фреймов
    "accent":        "#6C63FF",  # акцент: кнопки, активные элементы, заголовки
    "accent_hover":  "#5750D9",  # затемнение акцента при наведении
    "text":          "#E0E0E0",  # основной текст
    "text_secondary":"#A0A0B0",  # вторичный текст
    "border":        "#3A3A5A",  # границы/разделители, фон обычных кнопок
    "border_hover":  "#4A4A6A",  # фон кнопок при наведении
    "log_bg":        "#1A1A2E",  # фон лога
    "success":       "#4CAF50",  # INFO / ✅
    "warn":          "#FF9800",  # WARN / ⚠️
    "error":         "#F44336",  # ERROR / ❌
}
FONT_UI  = ("Segoe UI", 10)
FONT_LOG = ("Consolas", 9)
```

## File Structure

Only `r7_Testovarka.py` and `CLAUDE.md` change. No new files.

---

### Task A: x2t PDF-export test operation

**Files:** Modify `r7_Testovarka.py`: `TEST_DEFINITIONS` (~line 83-96), a new pair of shared class methods placed near `_close_update_dialog_if_exists` (~line 3810, an existing method with the exact same "enumerate windows, match title substrings" shape this task reuses), inside `_spreadsheet_worker` (the block of `run_test(...)` calls, ~line 881-892, plus the nested-closure definitions just above them, ~line 831-880), and inside `_batch_run_single_version` (the block of `measure(...)` calls — re-locate by content, this method has drifted to ~line 2352-2619 since earlier plans were written, plus its nested closures near `def del_col():`).

**Interfaces:** The per-operation closure (`save_as_pdf`) is still duplicated once per worker, matching the existing convention where every operation (`vlookup`, `del_column`, `paste_big`, ...) is a small nested closure using that worker's own local helpers (`safe_hotkey`/`_hk`, `safe_press`/`_pr`, `focus_window`/`_focus`) — a shared top-level method can't call either worker's differently-named local helpers without restructuring well beyond this task's scope. BUT the two sub-pieces of that closure that use *only* class-level state (no worker-local helpers) are pulled into two new shared class methods, avoiding a third near-duplicate copy that Task F would otherwise introduce:
- `self._win_title_contains(*substrings) -> bool` — returns whether any currently visible top-level window's title contains any of the given (lowercased) substrings. Generalizes the exact window-enumeration pattern already used by `_close_update_dialog_if_exists`.
- `self._cleanup_x2t_temp_pdfs(log_cb=None) -> None` — deletes every `%TEMP%\temp_export_x2t_*.pdf` left over from `save_as_pdf`, logging failures via `log_cb` (defaults to `self.add_test_log`, same optional-callback pattern as `_get_r7_processes`/`_log_resources`). Called from both workers AND from Task F's data-driven test loop — one implementation, three call sites, not three copies.

- [ ] **Step 1: Add the TEST_DEFINITIONS entry**

In `TEST_DEFINITIONS` (currently ending `"Удаление столбца (Del)",`), append one more entry:

```python
        "Удаление столбца (Del)",
        "Сохранение в PDF (конвертация x2t)",
    ]
```

- [ ] **Step 2: Add the two shared helper methods**

Insert immediately before `_close_update_dialog_if_exists` (so they sit next to the window-automation code they're conceptually part of):

```python
    def _win_title_contains(self, *substrings):
        """Returns True if any visible top-level window's title contains
        any of the given substrings (case-insensitive).

        Args:
            *substrings: One or more strings to search for.

        Returns:
            bool
        """
        if not WIN32_OK:
            return False
        import win32gui
        found = []
        needles = [s.lower() for s in substrings]
        def _cb(h, _):
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h).lower()
                if any(n in t for n in needles):
                    found.append(h)
        win32gui.EnumWindows(_cb, found)
        return bool(found)

    def _cleanup_x2t_temp_pdfs(self, log_cb=None):
        """Removes leftover temp_export_x2t_*.pdf files from %TEMP%.

        Safe to call even if save_as_pdf never ran or failed mid-save —
        glob simply matches nothing in that case.

        Args:
            log_cb: Callable for error logging; defaults to self.add_test_log.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        try:
            temp_dir = Path(os.environ.get("TEMP", "."))
            for leftover in temp_dir.glob("temp_export_x2t_*.pdf"):
                leftover.unlink(missing_ok=True)
        except Exception as e:
            log_cb(f"⚠️ Не удалось удалить временный PDF: {e}")
```

- [ ] **Step 3: Implement `save_as_pdf` inside `_spreadsheet_worker`**

Add this closure among the other nested closures inside `_spreadsheet_worker` (near `def del_column():`), using that worker's own `safe_hotkey`, `safe_press`, `pyautogui`, `focus_window`, `test_file`, and the two new shared methods:

```python
        def save_as_pdf():
            """Экспортирует текущий файл в PDF — надёжно запускает x2t (конвертер).

            Приоритет — хоткей Ctrl+Shift+S (Save As в Р7-Офис). Если диалог
            «Сохранить как» не появился за 3 сек, откатываемся на меню
            Файл → Сохранить как (Alt+F, затем навигация вниз и Enter) —
            конкретный пункт меню не проверен вживую на реальном Р7-Офис,
            это задокументированный запасной путь, требующий ручной проверки.
            """
            tmp_pdf = str(Path(os.environ.get("TEMP", ".")) /
                          f"temp_export_x2t_{int(time.time())}.pdf")

            safe_hotkey('ctrl', 'shift', 's')
            time.sleep(1)
            if not self._win_title_contains("сохранить как", "save as"):
                self.add_test_log("   ⚠️ Ctrl+Shift+S не открыл диалог, пробуем меню Файл")
                safe_hotkey('alt', 'f')
                time.sleep(0.5)
                safe_press('down', 3)
                safe_press('enter')
                time.sleep(1)

            pyperclip.copy(tmp_pdf)
            safe_hotkey('ctrl', 'a')
            safe_hotkey('ctrl', 'v')
            time.sleep(0.3)
            safe_press('enter')
            time.sleep(2)
            focus_window()
```

- [ ] **Step 4: Wire it into `_spreadsheet_worker`'s run_test block and clean up afterward**

Find the last line of that block:

```python
        run_test("Удаление столбца (Del)",                   del_column)
```

Replace with:

```python
        run_test("Удаление столбца (Del)",                   del_column)
        run_test("Сохранение в PDF (конвертация x2t)",        save_as_pdf)
        self._cleanup_x2t_temp_pdfs()
```

(Cleanup runs after `run_test` returns — `measure()` inside `run_test` already samples resources right after `func()` completes, so the file has been on disk for the full duration of that sampling; deleting it here, right after, is correct and matches the original per-worker inline version this replaces.)

- [ ] **Step 5: Implement and wire `save_as_pdf` inside `_batch_run_single_version`**

Mirror Step 3 using that method's own helpers (`_hk`, `_pr`, `_focus`) plus the same shared `self._win_title_contains`/`self._cleanup_x2t_temp_pdfs`. Add the closure among the sibling closures (near `def del_col():`):

```python
        def save_as_pdf():
            tmp_pdf = str(Path(os.environ.get("TEMP", ".")) /
                          f"temp_export_x2t_{int(time.time())}.pdf")

            _hk('ctrl', 'shift', 's')
            time.sleep(1)
            if not self._win_title_contains("сохранить как", "save as"):
                log_cb("   ⚠️ Ctrl+Shift+S не открыл диалог, пробуем меню Файл")
                _hk('alt', 'f')
                time.sleep(0.5)
                _pr('down', 3)
                _pr('enter')
                time.sleep(1)

            pyperclip.copy(tmp_pdf)
            _hk('ctrl', 'a')
            _hk('ctrl', 'v')
            time.sleep(0.3)
            _pr('enter')
            time.sleep(2)
            _focus()
```

Find the last line of the `measure(...)` block:

```python
        measure("Удаление столбца (Del)",               del_col)
```

Replace with:

```python
        measure("Удаление столбца (Del)",               del_col)
        measure("Сохранение в PDF (конвертация x2t)",   save_as_pdf)
        self._cleanup_x2t_temp_pdfs(log_cb=log_cb)
```

- [ ] **Step 6: Verify**

```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
.venv/Scripts/python.exe -c "
import r7_Testovarka as m
assert 'Сохранение в PDF (конвертация x2t)' in m.R7Testovarka.TEST_DEFINITIONS
assert hasattr(m.R7Testovarka, '_win_title_contains')
assert hasattr(m.R7Testovarka, '_cleanup_x2t_temp_pdfs')
print('PASS: TEST_DEFINITIONS has', len(m.R7Testovarka.TEST_DEFINITIONS), 'entries')
"
```
Expected: `OK` then `PASS: TEST_DEFINITIONS has 13 entries`.

- [ ] **Step 7: Commit**

```bash
git add r7_Testovarka.py
git commit -m "feat: add PDF export test operation to exercise x2t converter"
```

---

### Task B: per-test run-count UI + persistence

**Files:** Modify `r7_Testovarka.py`: `_build_perf_tab` (~line 181-244), `_load_test_selection`/`_save_test_selection` (~line 405-428), `__init__`'s `self.test_vars = {}` line (~line 122).

**Interfaces:**
- Produces: `self.test_vars: dict[str, tk.BooleanVar]` (unchanged name/type — still means "is this test enabled"). New `self.test_runs: dict[str, tk.IntVar]` — run count per test, 1-10, default 3.
- New JSON shape for `selected_tests.json`: `{"<test_name>": {"enabled": bool, "runs": int}, ...}`. `_load_test_selection` must also accept the OLD shape (`{"<test_name>": bool}`, written by every version of this app before today) and upgrade it in memory to `{"enabled": bool, "runs": 3}` — this is the backward-compatibility surface for this task; get it right, Task C's card grid reads straight from `self.test_vars`/`self.test_runs` and doesn't touch the JSON layer at all.

- [ ] **Step 1: Extend `_load_test_selection` / `_save_test_selection`**

Find (currently `r7_Testovarka.py:405-428`):

```python
    def _load_test_selection(self):
        """Loads saved test-selection state from selected_tests.json.

        Returns:
            dict: Mapping test_name → bool. Missing keys default to True.
        """
        path = BASE_DIR / "selected_tests.json"
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_test_selection(self):
        """Persists the current checkbox state to selected_tests.json."""
        path = BASE_DIR / "selected_tests.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({name: var.get() for name, var in self.test_vars.items()},
                          f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.add_test_log(f"⚠️ Не удалось сохранить настройки тестов: {e}")
```

Replace with:

```python
    def _load_test_selection(self):
        """Loads saved test-selection state from selected_tests.json.

        Accepts both the old shape ({name: bool}) and the current one
        ({name: {"enabled": bool, "runs": int}}), upgrading the old one
        in memory so files saved by earlier versions of the app keep working.

        Returns:
            dict: Mapping test_name → {"enabled": bool, "runs": int}.
        """
        path = BASE_DIR / "selected_tests.json"
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return {}
        upgraded = {}
        for name, value in raw.items():
            if isinstance(value, dict):
                upgraded[name] = {
                    "enabled": bool(value.get("enabled", True)),
                    "runs": int(value.get("runs", 3)),
                }
            else:
                # Старый формат: значение — просто bool.
                upgraded[name] = {"enabled": bool(value), "runs": 3}
        return upgraded

    def _save_test_selection(self):
        """Persists the current checkbox + run-count state to selected_tests.json."""
        path = BASE_DIR / "selected_tests.json"
        try:
            data = {
                name: {"enabled": var.get(), "runs": self.test_runs[name].get()}
                for name, var in self.test_vars.items()
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.add_test_log(f"⚠️ Не удалось сохранить настройки тестов: {e}")
```

- [ ] **Step 2: Add `self.test_runs = {}` next to `self.test_vars = {}` in `__init__`**

Find (`r7_Testovarka.py:122`):

```python
        self.test_vars = {}   # populated by _build_perf_tab
```

Replace with:

```python
        self.test_vars = {}   # populated by _build_perf_tab
        self.test_runs = {}   # populated by _build_perf_tab — IntVar per test, 1-10 runs
```

- [ ] **Step 3: Rebuild the test-selection panel in `_build_perf_tab`**

Find the block from `saved = self._load_test_selection()` through the `cv.bind("<Configure>", _on_canvas_configure)` line (currently `r7_Testovarka.py:209-224`):

```python
        saved = self._load_test_selection()
        self.test_vars = {}
        for name in self.TEST_DEFINITIONS:
            var = tk.BooleanVar(value=saved.get(name, True))
            tk.Checkbutton(inner, text=name, variable=var,
                           wraplength=210, anchor=tk.W, justify=tk.LEFT
                           ).pack(anchor=tk.W, pady=1, padx=2)
            self.test_vars[name] = var

        def _on_inner_configure(event):
            cv.configure(scrollregion=cv.bbox("all"))
        def _on_canvas_configure(event):
            cv.itemconfig(cv_win, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        cv.bind("<Configure>", _on_canvas_configure)
```

Replace with:

```python
        saved = self._load_test_selection()
        self.test_vars = {}
        self.test_runs = {}
        for name in self.TEST_DEFINITIONS:
            entry = saved.get(name, {"enabled": True, "runs": 3})
            var = tk.BooleanVar(value=entry.get("enabled", True))
            runs_var = tk.IntVar(value=entry.get("runs", 3))

            row = ttk.Frame(inner)
            row.pack(fill=tk.X, pady=1, padx=2)
            ttk.Checkbutton(row, variable=var).pack(side=tk.LEFT)
            ttk.Spinbox(row, from_=1, to=10, increment=1, width=4,
                        textvariable=runs_var).pack(side=tk.LEFT, padx=(2, 4))
            ttk.Label(row, text=name, wraplength=200, anchor=tk.W, justify=tk.LEFT
                      ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            self.test_vars[name] = var
            self.test_runs[name] = runs_var

        def _on_inner_configure(event):
            cv.configure(scrollregion=cv.bbox("all"))
        def _on_canvas_configure(event):
            cv.itemconfig(cv_win, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        cv.bind("<Configure>", _on_canvas_configure)
```

(This satisfies Task B's own UI requirement structurally — one `ttk.Checkbutton` + `ttk.Spinbox` + `ttk.Label` per row inside the existing scrollable `Canvas`. Task C later restyles these rows into a card grid; it must preserve `self.test_vars`/`self.test_runs` exactly as built here, since Task D/E's `run_test_with_runs` wiring — Task C, below — reads them by these exact names.)

- [ ] **Step 4: Snapshot `test_runs` on the main thread — mirror the existing `enabled_tests` pattern**

`run_spreadsheet_test` (the method that validates prerequisites and spawns the worker thread) already deliberately reads `self.test_vars` on the MAIN thread and passes a plain `set` into the background thread, specifically to avoid touching Tk variables from a non-UI thread. `self.test_runs` must follow the exact same rule — do not let `_spreadsheet_worker` call `self.test_runs[name].get()` itself (that would read a Tk `IntVar` from the worker thread, inconsistent with how `enabled_tests` is already handled).

Find (currently `r7_Testovarka.py:445-473`):

```python
    def run_spreadsheet_test(self):
        """Entry point for the stress test — validates prerequisites then launches worker thread."""
        if not ctypes.windll.shell32.IsUserAnAdmin():
            messagebox.showerror(
                "Ошибка прав",
                "Стресс-тест требует запуска от имени администратора.\n"
                "Перезапустите программу с правами администратора."
            )
            return
        if not self.current_version_info:
            messagebox.showwarning("Нет версии", "Р7-Офис не установлен или не определён.")
            return
        if not PYAUTOGUI_OK or not pyperclip or not EXCEL_OK or not WIN32_OK:
            missing = []
            if not PYAUTOGUI_OK: missing.append("pyautogui")
            if not pyperclip: missing.append("pyperclip")
            if not EXCEL_OK: missing.append("openpyxl")
            if not WIN32_OK: missing.append("pywin32")
            messagebox.showerror("Ошибка",
                                 f"Отсутствуют библиотеки:\n{', '.join(missing)}\n"
                                 f"Установите: pip install " + " ".join(missing))
            return
        enabled = ({n for n, v in self.test_vars.items() if v.get()}
                   if self.test_vars else set(self.TEST_DEFINITIONS))
        if not enabled:
            messagebox.showwarning("Нет тестов", "Выберите хотя бы один тест для выполнения.")
            return
        self._save_test_selection()
        threading.Thread(target=self._spreadsheet_worker, args=(enabled,), daemon=True).start()
```

Replace with (adds a main-thread `runs_snapshot` dict and passes it as a second positional argument):

```python
    def run_spreadsheet_test(self):
        """Entry point for the stress test — validates prerequisites then launches worker thread."""
        if not ctypes.windll.shell32.IsUserAnAdmin():
            messagebox.showerror(
                "Ошибка прав",
                "Стресс-тест требует запуска от имени администратора.\n"
                "Перезапустите программу с правами администратора."
            )
            return
        if not self.current_version_info:
            messagebox.showwarning("Нет версии", "Р7-Офис не установлен или не определён.")
            return
        if not PYAUTOGUI_OK or not pyperclip or not EXCEL_OK or not WIN32_OK:
            missing = []
            if not PYAUTOGUI_OK: missing.append("pyautogui")
            if not pyperclip: missing.append("pyperclip")
            if not EXCEL_OK: missing.append("openpyxl")
            if not WIN32_OK: missing.append("pywin32")
            messagebox.showerror("Ошибка",
                                 f"Отсутствуют библиотеки:\n{', '.join(missing)}\n"
                                 f"Установите: pip install " + " ".join(missing))
            return
        enabled = ({n for n, v in self.test_vars.items() if v.get()}
                   if self.test_vars else set(self.TEST_DEFINITIONS))
        if not enabled:
            messagebox.showwarning("Нет тестов", "Выберите хотя бы один тест для выполнения.")
            return
        # Снимок self.test_runs на главном потоке — как enabled_tests, чтобы
        # фоновый поток не трогал Tk-переменные напрямую.
        runs_snapshot = {n: v.get() for n, v in self.test_runs.items()}
        self._save_test_selection()
        threading.Thread(target=self._spreadsheet_worker,
                         args=(enabled, runs_snapshot), daemon=True).start()
```

- [ ] **Step 5: Accept the snapshot in `_spreadsheet_worker`'s signature**

Find (currently `r7_Testovarka.py:475`):

```python
    def _spreadsheet_worker(self, enabled_tests=None):
```

Replace with:

```python
    def _spreadsheet_worker(self, enabled_tests=None, test_runs=None):
```

And find the two lines immediately below it that default `enabled_tests`:

```python
        if enabled_tests is None:
            enabled_tests = set(self.TEST_DEFINITIONS)
```

Replace with:

```python
        if enabled_tests is None:
            enabled_tests = set(self.TEST_DEFINITIONS)
        if test_runs is None:
            test_runs = {}
```

(Task C's `run_test_with_runs` and its call sites read run counts via `test_runs.get(name, 3)` — a plain dict lookup on this parameter, never `self.test_runs[name].get()` — see Task C Step 1/2 below.)

- [ ] **Step 6: Verify**

```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
.venv/Scripts/python.exe -c "
import types, json, tempfile, pathlib
import r7_Testovarka as m

fake_self = types.SimpleNamespace(add_test_log=lambda s: None)

# old-format file upgrades correctly
old = {'Тест А': True, 'Тест Б': False}
p = pathlib.Path(tempfile.mktemp(suffix='.json'))
p.write_text(json.dumps(old), encoding='utf-8')
orig_base = m.BASE_DIR
try:
    m.BASE_DIR = p.parent
    (p.parent / 'selected_tests.json').write_text(json.dumps(old), encoding='utf-8')
    loaded = m.R7Testovarka._load_test_selection(fake_self)
    assert loaded['Тест А'] == {'enabled': True, 'runs': 3}, loaded
    assert loaded['Тест Б'] == {'enabled': False, 'runs': 3}, loaded
    print('PASS: old format upgraded correctly')

    new = {'Тест В': {'enabled': True, 'runs': 5}}
    (p.parent / 'selected_tests.json').write_text(json.dumps(new), encoding='utf-8')
    loaded2 = m.R7Testovarka._load_test_selection(fake_self)
    assert loaded2['Тест В'] == {'enabled': True, 'runs': 5}, loaded2
    print('PASS: new format round-trips correctly')
finally:
    m.BASE_DIR = orig_base
    (p.parent / 'selected_tests.json').unlink(missing_ok=True)
"
```
Expected: `OK`, then both `PASS:` lines.

Also confirm the signature/snapshot change landed:

```bash
.venv/Scripts/python.exe -c "
import inspect
import r7_Testovarka as m
sig = inspect.signature(m.R7Testovarka._spreadsheet_worker)
assert list(sig.parameters) == ['self', 'enabled_tests', 'test_runs'], sig
print('PASS: _spreadsheet_worker accepts test_runs')
"
```
Expected: `PASS: _spreadsheet_worker accepts test_runs`.

- [ ] **Step 7: Commit**

```bash
git add r7_Testovarka.py
git commit -m "feat: add per-test run-count UI (Spinbox) with backward-compatible settings format"
```

---

### Task C: `run_test_with_runs` + multi-run wiring + report schema

**Files:** Modify `r7_Testovarka.py` inside `_spreadsheet_worker`: the `measure`/`run_test` definitions (~line 793-829), the 13 `run_test(...)` call sites (~line 881-893, now 13 after Task A), the stats block (~line 894-905), the `full_data`/HTML-call section (~line 907-957). Modify `_generate_html_report` (results table rendering, ~line 1052-1064 pre-existing-line-numbers — re-locate by content, this file has shifted since the last plan).

**Interfaces:**
- Produces: `run_test_with_runs(name, func, runs) -> None` (mutates the enclosing `results` list, mirrors `run_test`'s existing "append if not None" contract). Each appended result dict gains additive keys `"runs": [t0, t1, ...]`, `"avg": float`, `"min": float`, `"max": float`; `"time"` is set to the SAME value as `"avg"` (not removed, not renamed) so every existing reader (`compare_versions`, `_generate_html_report`'s current columns, the Excel writer) keeps working unchanged on files old and new.
- Consumes: `enabled_tests` (already a `_spreadsheet_worker` parameter) and the new `test_runs` parameter added in Task B Step 5 — a plain `dict[str, int]`, NOT `self.test_runs` (that dict holds live Tk `IntVar`s and must never be read from this background-thread method; Task B Step 4 already snapshots it to plain ints on the main thread before the thread starts).
- **Correctness-critical:** `run_test_with_runs` MUST refresh the process snapshot before sampling, exactly like the existing `measure()` does. The already-shipped fix in `measure()` (`r7_Testovarka.py:795, 811-812`) refreshes `r7_procs` via `nonlocal r7_procs; self._r7_pids = None; r7_procs = self._get_r7_processes()` immediately before every `_sample_r7_resources()` call — this is specifically what makes x2t (a short-lived process) reliably detected instead of missed by a stale snapshot taken once after file-open. `run_test_with_runs` replaces `run_test`/`measure` as the ONLY per-operation sampling path once Task C Step 2 lands, so if it skips this refresh, resource sampling silently degrades to the pre-fix stale-snapshot behavior for all 13 operations, defeating Task A's entire purpose. Do not drop this — it is the most important line in this task.

- [ ] **Step 1: Add `run_test_with_runs` next to the existing `run_test`/`measure`**

Find (currently, inside `_spreadsheet_worker`):

```python
        def run_test(name, func):
            r = measure(name, func)
            if r is not None:
                results.append(r)
```

Replace with:

```python
        def run_test(name, func):
            r = measure(name, func)
            if r is not None:
                results.append(r)

        def run_test_with_runs(name, func, runs):
            """Runs `func` `runs` times, logging each pass, and appends one
            averaged result to `results` (or nothing if the test is disabled).

            The resource sample (RAM/CPU/threads/uptime) is taken once, after
            the LAST pass — sampling on every pass would multiply the
            0.1s-per-process cpu_percent() blocking cost by `runs` for no
            benefit, since RAM/CPU during repeated identical operations don't
            need a separate reading per pass the way timing does. The process
            list IS still refreshed right before that single sample, exactly
            like measure() does — otherwise short-lived processes such as
            x2t, spawned during one of the `runs` passes, would be missed by
            a stale r7_procs snapshot (the same bug the shipped fix in
            measure() exists to prevent).
            """
            nonlocal r7_procs
            if name not in enabled_tests:
                return
            runs = max(1, int(runs))
            self.add_test_log(f"⏳ Тест: {name} (прогон 1/{runs})...")
            try:
                focus_window()
            except Exception as e:
                self.add_test_log(f"   ⚠️ Не удалось установить фокус: {e}")

            pass_times = []
            error = None
            for i in range(runs):
                if i > 0:
                    self.add_test_log(f"⏳ Тест: {name} (прогон {i + 1}/{runs})...")
                start = time.time()
                try:
                    func()
                except Exception as e:
                    error = str(e)
                    self.add_test_log(f"   ❌ прогон {i + 1}: ошибка — {e}")
                    break
                elapsed = time.time() - start
                pass_times.append(elapsed)
                post_action_delay()
                self.add_test_log(f"   ✅ прогон {i + 1}: {elapsed:.2f} сек")

            if not pass_times:
                results.append({"name": name, "time": 0.0, "error": error,
                                 "ram": None, "cpu": None, "cpu_normalized": None,
                                 "threads": None, "uptime_sec": None,
                                 "runs": [], "avg": 0.0, "min": 0.0, "max": 0.0})
                return

            avg_t = sum(pass_times) / len(pass_times)
            min_t = min(pass_times)
            max_t = max(pass_times)
            self.add_test_log(
                f"   📊 Среднее: {avg_t:.2f} сек (мин {min_t:.2f}, макс {max_t:.2f})")

            # Обновляем список процессов перед замером — как в measure(),
            # иначе короткоживущий x2t может быть пропущен устаревшим снимком.
            self._r7_pids = None
            r7_procs = self._get_r7_processes()
            sample = self._sample_r7_resources(r7_procs)
            self._log_resources(sample)

            results.append({
                "name": name, "time": avg_t, "error": error,
                "ram":            sample["ram_mb"]      if sample else None,
                "cpu":            sample["cpu_raw_pct"]  if sample else None,
                "cpu_normalized": sample["cpu_norm_pct"] if sample else None,
                "threads":        sample["threads"]      if sample else None,
                "uptime_sec":     sample["uptime_sec"]    if sample else None,
                "runs": pass_times, "avg": avg_t, "min": min_t, "max": max_t,
            })
```

- [ ] **Step 2: Replace the 13 `run_test(...)` calls with a data-driven `run_test_with_runs(...)` loop**

Find the full block of 13 calls (12 original + Task A's new one):

```python
        run_test("Выделение всех ячеек (Ctrl+A)",          lambda: safe_hotkey('ctrl', 'a'))
        run_test("Копирование всех ячеек (Ctrl+C)",         lambda: safe_hotkey('ctrl', 'c'))
        run_test("Вставка большого массива (Ctrl+V)",        paste_big)
        run_test("Добавление нового листа",                  lambda: safe_hotkey('shift', 'f11'))
        run_test("Добавление столбца (горячие клавиши)",     lambda: add_column('hotkey'))
        run_test("Добавление столбца (меню Вставка)",        lambda: add_column('menu'))
        run_test("Вставка 1 ячейки (горячие клавиши)",       lambda: copy_paste_hotkey(1, 10))
        run_test("Вставка 5 ячеек (горячие клавиши)",        lambda: copy_paste_hotkey(5, 15))
        run_test("Вставка 1 ячейки (ПКМ)",                   lambda: copy_paste_context(1, 10))
        run_test("Вставка 5 ячеек (ПКМ)",                    lambda: copy_paste_context(5, 15))
        run_test("Функция ВПР (50K строк)",                  vlookup)
        run_test("Удаление столбца (Del)",                   del_column)
        run_test("Сохранение в PDF (конвертация x2t)",        save_as_pdf)
        try:
            for _leftover in Path(os.environ.get("TEMP", ".")).glob("temp_export_x2t_*.pdf"):
                _leftover.unlink(missing_ok=True)
        except Exception as e:
            self.add_test_log(f"⚠️ Не удалось удалить временный PDF: {e}")
```

Replace with a data-driven loop over the same 13 operations, in the same order, reading each test's run count from the `test_runs` parameter (never `self.test_runs`, per this task's Interfaces note) — Task F Step 3 later ADDS progress-bar calls around this exact loop, it does not rebuild it, so get the structure right here:

```python
        _test_ops = [
            ("Выделение всех ячеек (Ctrl+A)",      lambda: safe_hotkey('ctrl', 'a')),
            ("Копирование всех ячеек (Ctrl+C)",     lambda: safe_hotkey('ctrl', 'c')),
            ("Вставка большого массива (Ctrl+V)",    paste_big),
            ("Добавление нового листа",              lambda: safe_hotkey('shift', 'f11')),
            ("Добавление столбца (горячие клавиши)", lambda: add_column('hotkey')),
            ("Добавление столбца (меню Вставка)",    lambda: add_column('menu')),
            ("Вставка 1 ячейки (горячие клавиши)",   lambda: copy_paste_hotkey(1, 10)),
            ("Вставка 5 ячеек (горячие клавиши)",    lambda: copy_paste_hotkey(5, 15)),
            ("Вставка 1 ячейки (ПКМ)",               lambda: copy_paste_context(1, 10)),
            ("Вставка 5 ячеек (ПКМ)",                lambda: copy_paste_context(5, 15)),
            ("Функция ВПР (50K строк)",              vlookup),
            ("Удаление столбца (Del)",               del_column),
            ("Сохранение в PDF (конвертация x2t)",   save_as_pdf),
        ]
        for _name, _func in _test_ops:
            run_test_with_runs(_name, _func, test_runs.get(_name, 3))
        self._cleanup_x2t_temp_pdfs()
```

(`run_test` itself stays defined and unused here — harmless dead code from the caller's perspective; removing it is out of scope for this task. If the final code-simplifier pass flags it as unused, that's fine to act on then.)

- [ ] **Step 3: Update `_generate_html_report`'s results table to show avg + (min–max)**

Find the table-row-building block (locate by content — it starts with `rows_html = ""` inside `_generate_html_report` and builds `<td>{r['time']:.3f}</td>`):

```python
        rows_html = ""
        for r in results:
            err_class = "row-error" if r.get("error") else ""
            ram_cell = f"{r['ram']:.1f}" if r.get("ram") is not None else "—"
            cpu_cell = f"{r['cpu']:.1f}" if r.get("cpu") is not None else "—"
            cpu_norm_cell = (f"{r['cpu_normalized']:.1f}"
                              if r.get("cpu_normalized") is not None else "—")
            err_cell = r.get("error") or ""
            rows_html += (f"<tr class='{err_class}'>"
                          f"<td>{r['name']}</td>"
                          f"<td>{r['time']:.3f}</td>"
                          f"<td>{ram_cell}</td>"
                          f"<td>{cpu_cell}</td>"
                          f"<td>{cpu_norm_cell}</td>"
                          f"<td>{err_cell}</td></tr>\n")
```

Replace with (adds a "Прогоны" column showing `avg (min–max)` when the field is present, otherwise falling back to the plain time value for old-format/no-runs data — additive, no existing column removed):

```python
        rows_html = ""
        for r in results:
            err_class = "row-error" if r.get("error") else ""
            ram_cell = f"{r['ram']:.1f}" if r.get("ram") is not None else "—"
            cpu_cell = f"{r['cpu']:.1f}" if r.get("cpu") is not None else "—"
            cpu_norm_cell = (f"{r['cpu_normalized']:.1f}"
                              if r.get("cpu_normalized") is not None else "—")
            err_cell = r.get("error") or ""
            if r.get("runs") and len(r["runs"]) > 1:
                time_cell = (f"{r['avg']:.3f} "
                             f"<span style='color:#888'>({r['min']:.2f}–{r['max']:.2f})</span>")
            else:
                time_cell = f"{r['time']:.3f}"
            rows_html += (f"<tr class='{err_class}'>"
                          f"<td>{r['name']}</td>"
                          f"<td>{time_cell}</td>"
                          f"<td>{ram_cell}</td>"
                          f"<td>{cpu_cell}</td>"
                          f"<td>{cpu_norm_cell}</td>"
                          f"<td>{err_cell}</td></tr>\n")
```

- [ ] **Step 4: Verify**

```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
.venv/Scripts/python.exe -c "
import pathlib
src = pathlib.Path('r7_Testovarka.py').read_text(encoding='utf-8')
assert 'def run_test_with_runs' in src
assert 'nonlocal r7_procs' in src, 'run_test_with_runs must refresh r7_procs before sampling'
assert '_test_ops = [' in src
assert src.count('lambda: safe_hotkey') + src.count('paste_big),') + src.count('vlookup),') >= 1
assert 'test_runs.get(_name, 3)' in src
assert '\"runs\": pass_times' in src
print('PASS')
"
```
Expected: `OK` then `PASS`.

- [ ] **Step 5: Commit**

```bash
git add r7_Testovarka.py
git commit -m "feat: run each stress-test operation N times and report avg/min/max"
```

---

### Task D: dark theme infrastructure + window shell

**Files:** Modify `r7_Testovarka.py`: add the `COLORS`/`FONT_UI`/`FONT_LOG` dict (top of file, see Color Palette section above, placed right after the third-party-import try/except blocks and before `class R7Testovarka:`), add a new method `_apply_dark_theme(self)`, call it first thing in `setup_ui`, restyle the window shell (header, notebook, status bar) in `setup_ui`.

**Interfaces:** Produces `self._apply_dark_theme() -> None`, called once from `setup_ui` before any widget is built (so every subsequently-created `ttk.*` widget picks up the new default styles automatically). Every other task in this plan that creates widgets (Task E, Task F) relies on styles this method defines existing already — do not rename the style names introduced here (`Dark.TFrame`, `Card.TFrame`, `Accent.TButton`, `Secondary.TButton`, `Dark.TNotebook`, `Dark.TLabel`, `Secondary.TLabel`, `Header.TLabel`, `Dark.Horizontal.TProgressbar`) without updating every consumer.

- [ ] **Step 1: Insert the color/font constants**

Insert the `COLORS`/`FONT_UI`/`FONT_LOG` block from this plan's "Color Palette" section immediately before `class R7Testovarka:`.

- [ ] **Step 2: Add `_apply_dark_theme`**

Add as a new method, placed right before `setup_ui` in the class body:

```python
    def _apply_dark_theme(self):
        """Настраивает тёмную тему через ttk.Style.

        Тема 'clam' выбрана намеренно: нативные темы Windows ('vista'/
        'winnative') рисуют кнопки/вкладки/скроллбары средствами ОС и
        игнорируют цветовые переопределения ttk.Style для многих опций —
        подтверждено документацией Tk. 'clam' — собственный рендерер Tk,
        поддерживающий полную кастомизацию цвета для всех использованных
        здесь виджетов.
        """
        self.root.configure(bg=COLORS["bg"])
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"],
                         font=FONT_UI)
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["bg_card"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Card.TLabel", background=COLORS["bg_card"], foreground=COLORS["text"])
        style.configure("Secondary.TLabel", background=COLORS["bg"],
                         foreground=COLORS["text_secondary"])
        style.configure("Header.TLabel", background=COLORS["bg"],
                         foreground=COLORS["accent"], font=("Segoe UI", 16, "bold"))
        style.configure("StatusOk.TLabel", background=COLORS["bg"], foreground=COLORS["success"])
        style.configure("StatusErr.TLabel", background=COLORS["bg"], foreground=COLORS["error"])

        style.configure("TLabelframe", background=COLORS["bg"], foreground=COLORS["text"],
                         bordercolor=COLORS["border"])
        style.configure("TLabelframe.Label", background=COLORS["bg"], foreground=COLORS["text_secondary"])

        style.configure("TButton", background=COLORS["border"], foreground=COLORS["text"],
                         bordercolor=COLORS["border"], focusthickness=0, padding=6)
        style.map("TButton",
                  background=[("active", COLORS["border_hover"]), ("pressed", COLORS["border_hover"])])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="#FFFFFF",
                         font=("Segoe UI", 12, "bold"), padding=10)
        style.map("Accent.TButton",
                  background=[("active", COLORS["accent_hover"]), ("pressed", COLORS["accent_hover"])])

        style.configure("TCheckbutton", background=COLORS["bg_card"], foreground=COLORS["text"])
        style.map("TCheckbutton", background=[("active", COLORS["bg_card"])])

        style.configure("TSpinbox", fieldbackground=COLORS["bg"], background=COLORS["bg"],
                         foreground=COLORS["text"], arrowcolor=COLORS["text"])

        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=COLORS["bg"], foreground=COLORS["text_secondary"],
                         padding=(14, 8), borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", COLORS["bg"])],
                  foreground=[("selected", COLORS["accent"])])

        style.configure("TScrollbar", background=COLORS["border"], troughcolor=COLORS["bg"],
                         bordercolor=COLORS["bg"], arrowcolor=COLORS["text_secondary"])
        style.map("TScrollbar", background=[("active", COLORS["border_hover"])])

        style.configure("Treeview", background=COLORS["bg_card"], fieldbackground=COLORS["bg_card"],
                         foreground=COLORS["text"], bordercolor=COLORS["border"], rowheight=26)
        style.configure("Treeview.Heading", background=COLORS["border"], foreground=COLORS["text"],
                         relief="flat")
        style.map("Treeview",
                  background=[("selected", COLORS["accent"])],
                  foreground=[("selected", "#FFFFFF")])

        style.configure("Horizontal.TProgressbar", background=COLORS["accent"],
                         troughcolor=COLORS["bg_card"], bordercolor=COLORS["bg"])
```

- [ ] **Step 3: Call it from `setup_ui`, restyle the header and status bar**

Find (currently, start of `setup_ui`):

```python
    def setup_ui(self):
        """Builds the main UI layout with notebook tabs and status bar."""
        main = ttk.Frame(self.root, padding="10")
        main.pack(fill=tk.BOTH, expand=True)

        info = ttk.LabelFrame(main, text="Текущая версия", padding="5")
        info.pack(fill=tk.X, pady=(0, 10))
        self.lbl_current = ttk.Label(info, text="Не определена", foreground="red", font=("Arial", 10))
        self.lbl_current.pack(anchor=tk.W)

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_versions = ttk.Frame(self.notebook)
        self.tab_perf = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_versions, text="📦 Версии")
        self.notebook.add(self.tab_perf, text="⚡ Производительность")

        self._build_versions_tab()
        self._build_perf_tab()

        self.status_var = tk.StringVar(value="Готов")
        status = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2))
        status.pack(side=tk.BOTTOM, fill=tk.X)
```

Replace with:

```python
    def setup_ui(self):
        """Builds the main UI layout with notebook tabs and status bar."""
        self._apply_dark_theme()

        main = ttk.Frame(self.root, padding="10")
        main.pack(fill=tk.BOTH, expand=True)

        # ── Шапка: логотип + статус вместо стандартного заголовка окна ────────
        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(header, text="⚡ R7 Testovarka", style="Header.TLabel").pack(side=tk.LEFT)
        self.lbl_status_dot = ttk.Label(header, text="●  Готов", style="StatusOk.TLabel")
        self.lbl_status_dot.pack(side=tk.RIGHT)
        # «Тень» под шапкой: одна тёмная линия — ttk.Style не умеет рисовать
        # настоящую размытую тень, это ближайшее достижимое приближение.
        shadow = tk.Frame(main, height=2, bg=COLORS["border"])
        shadow.pack(fill=tk.X, pady=(0, 8))

        info = ttk.Frame(main, style="Card.TFrame", padding="10")
        info.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(info, text="Текущая версия", style="Secondary.TLabel").pack(anchor=tk.W)
        self.lbl_current = ttk.Label(info, text="Не определена", style="Card.TLabel",
                                     font=("Segoe UI", 16, "bold"))
        self.lbl_current.pack(anchor=tk.W, pady=(2, 0))

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_versions = ttk.Frame(self.notebook)
        self.tab_perf = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_versions, text="📦 Версии")
        self.notebook.add(self.tab_perf, text="⚡ Производительность")

        self._build_versions_tab()
        self._build_perf_tab()

        self.status_var = tk.StringVar(value="Готов")
        status = ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W, padding=(8, 4),
                           style="Secondary.TLabel")
        status.pack(side=tk.BOTTOM, fill=tk.X)
```

Note: `self.lbl_current`'s call sites elsewhere in the file (`detect_current_version`, using `self.lbl_current.config(text=..., foreground="green"/"orange")`) still work unchanged — `ttk.Label.config(foreground=...)` overrides the style's foreground per-instance, which remains valid on a `ttk.Label` regardless of which named style it uses.

- [ ] **Step 4: Dark-background the 7 secondary dialog windows so they don't clash with the new global ttk styles**

`ttk.Style()` is a per-interpreter singleton — every plain `ttk.*` widget created ANYWHERE in the app, including inside dialogs this plan otherwise never touches, automatically inherits the dark palette from Step 2 the moment it's instantiated. But each dialog's own `tk.Toplevel` window background is set independently and stays whatever Tk's default is (light) unless configured — so without this step, every one of the app's 7 secondary windows would end up as a light window frame containing dark-styled ttk content, which is a visible regression this plan's styling work would otherwise introduce silently. Fully redesigning these 7 dialogs is out of scope (only the main window + Versions/Performance tabs were in the user's literal spec) — this step is the minimal, correctness-preserving mitigation: just make each window's own background dark too, so it doesn't clash.

There are exactly 7 `tk.Toplevel(self.root)` call sites (confirmed via `grep -n "tk.Toplevel(self.root)" r7_Testovarka.py`): the post-test dialog (~line 1315), compare-versions dialog (~1440), batch-config dialog (~2024), batch-progress window (~2122), compare-file-sizes dialog (~2783), hash-check progress window (~3522), hash-results window (~3635). Locate each by content (line numbers may have shifted from earlier tasks' edits) and add a `.configure(bg=COLORS["bg"])` call on the line immediately after each one. For example, find:

```python
        dlg = tk.Toplevel(self.root)
```

(the first occurrence, inside the post-test dialog method) and replace with:

```python
        dlg = tk.Toplevel(self.root)
        dlg.configure(bg=COLORS["bg"])
```

Repeat this exact pattern (`<var> = tk.Toplevel(self.root)` → same line + `<var>.configure(bg=COLORS["bg"])`) at all 7 sites — the local variable name differs per site (`dlg`, `prog`, `prog_win`, `win`), match whichever name that specific call site already uses.

- [ ] **Step 5: Darken the hash-check Treeview's hardcoded light row-tag colors**

`_show_hash_results`'s Treeview uses `tag_configure` with light pastel backgrounds designed for a white table — once Step 2's base `"Treeview"` style sets a dark `background`, these tag-configured rows (tags override the base style per-row) would still render as light green/yellow/red rows on an otherwise-dark table. Find (currently, inside `_show_hash_results`):

```python
        tree.tag_configure("ok",     background="#d4edda")
        tree.tag_configure("no_ref", background="#fff3cd")
        tree.tag_configure("fail",   background="#f8d7da")
```

Replace with dark-theme-appropriate equivalents (muted, desaturated versions of the same success/warn/error hues, still clearly distinguishable from `COLORS["bg_card"]` and from each other, with light-enough foreground text to stay readable):

```python
        tree.tag_configure("ok",     background="#2E4A3A", foreground=COLORS["text"])
        tree.tag_configure("no_ref", background="#4A4326", foreground=COLORS["text"])
        tree.tag_configure("fail",   background="#4A2E2E", foreground=COLORS["text"])
```

- [ ] **Step 6: Verify**

```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
.venv/Scripts/python.exe -c "
import pathlib
src = pathlib.Path('r7_Testovarka.py').read_text(encoding='utf-8')
assert 'COLORS = {' in src
assert 'def _apply_dark_theme' in src
assert 'self._apply_dark_theme()' in src
assert \"theme_use('clam')\" in src or 'theme_use(\"clam\")' in src
assert src.count('tk.Toplevel(self.root)') == src.count('.configure(bg=COLORS[\"bg\"])') + 1  # +1 for the main root.configure() call in _apply_dark_theme
assert 'tag_configure(\"ok\",     background=\"#2E4A3A\"' in src
print('PASS')
"
```
Expected: `OK` then `PASS`.

- [ ] **Step 7: Commit**

```bash
git add r7_Testovarka.py
git commit -m "feat: add dark IDE-style ttk theme infrastructure and restyled window shell"
```

---

### Task E: Versions tab redesign (Listbox → Treeview)

**Files:** Modify `r7_Testovarka.py`: `_build_versions_tab` (~line 155-179), `refresh_distributives` (~line 283-298), `on_select_distributive` (~line 312-321). These are the only three places `self.listbox` is referenced (confirmed by exhaustive grep against the pre-Task-A file — Task A/B/C do not touch the Versions tab, so this count is still accurate for this task).

**Interfaces:** Replaces `self.listbox` (a `tk.Listbox`) with `self.tree` (a `ttk.Treeview`, columns `name`/`version`/`size`). `self.distributives` (list of `{"path":, "name":}` dicts, built by `refresh_distributives`) keeps its exact shape — Task E only changes how it's rendered and how the current selection is read back, not what it stores. `on_select_distributive`'s signature and its effect on `self.selected_distributive`/`self.btn_install` must stay identical, since `install_selected` (unchanged, out of scope) reads `self.selected_distributive` directly.

- [ ] **Step 1: Rebuild `_build_versions_tab`**

Find (currently `r7_Testovarka.py:155-179`):

```python
    def _build_versions_tab(self):
        """Builds the distributives list and install controls."""
        ttk.Label(self.tab_versions, text="Дистрибутивы:", font=("Arial", 10, "bold")).pack(anchor=tk.W, pady=(0, 5))
        frame = ttk.Frame(self.tab_versions)
        frame.pack(fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(frame, yscrollcommand=scroll.set, font=("Consolas", 10))
        self.listbox.pack(fill=tk.BOTH, expand=True)
        scroll.config(command=self.listbox.yview)

        self.lbl_file_info = ttk.Label(self.tab_versions, text="", foreground="gray")
        self.lbl_file_info.pack(anchor=tk.W, pady=5)

        btn_frame = ttk.Frame(self.tab_versions)
        btn_frame.pack(fill=tk.X, pady=10)
        self.btn_install = ttk.Button(btn_frame, text="📥 Установить", command=self.install_selected, state=tk.DISABLED)
        self.btn_install.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🔄 Обновить", command=self.refresh_distributives).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📁 Добавить", command=self.add_distributive).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📂 Открыть папку", command=self.open_distributives_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🔐 Проверить хеш-суммы", command=self.check_hashes).pack(side=tk.LEFT, padx=5)

        self.listbox.bind('<<ListboxSelect>>', self.on_select_distributive)
```

Replace with:

```python
    def _build_versions_tab(self):
        """Builds the distributives table and install controls."""
        ttk.Label(self.tab_versions, text="Дистрибутивы", style="Secondary.TLabel").pack(
            anchor=tk.W, pady=(4, 6))
        frame = ttk.Frame(self.tab_versions, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(frame)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(
            frame, columns=("name", "version", "size"), show="headings",
            selectmode="browse", yscrollcommand=scroll.set)
        self.tree.heading("name", text="Имя")
        self.tree.heading("version", text="Версия")
        self.tree.heading("size", text="Размер (МБ)")
        self.tree.column("name", width=320, anchor=tk.W)
        self.tree.column("version", width=110, anchor=tk.CENTER)
        self.tree.column("size", width=110, anchor=tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True)
        scroll.config(command=self.tree.yview)

        self.lbl_file_info = ttk.Label(self.tab_versions, text="", style="Secondary.TLabel")
        self.lbl_file_info.pack(anchor=tk.W, pady=5)

        btn_frame = ttk.Frame(self.tab_versions)
        btn_frame.pack(fill=tk.X, pady=10)
        self.btn_install = ttk.Button(btn_frame, text="📥 Установить", style="Accent.TButton",
                                      command=self.install_selected, state=tk.DISABLED)
        self.btn_install.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🔄 Обновить", command=self.refresh_distributives).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📁 Добавить", command=self.add_distributive).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📂 Открыть папку", command=self.open_distributives_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🔐 Проверить хеш-суммы", command=self.check_hashes).pack(side=tk.LEFT, padx=5)

        self.tree.bind('<<TreeviewSelect>>', self.on_select_distributive)
```

(The task spec's "Действие" column with an inline "Установить" button isn't directly implementable as a real clickable button inside a `ttk.Treeview` cell — Tk's Treeview cells render text/images, not embedded widgets, without a much larger custom-drawing workaround. The existing bottom-of-tab "📥 Установить" button, now styled `Accent.TButton` and enabled by row selection exactly as before, is the closest faithful equivalent and is called out here rather than silently dropped.)

- [ ] **Step 2: Update `refresh_distributives`**

Find (currently `r7_Testovarka.py:283-298`):

```python
    def refresh_distributives(self):
        """Rescans the Distributives folder and refreshes the listbox."""
        self.listbox.delete(0, tk.END)
        self.distributives = []
        files = list(self.distributives_folder.glob("*.msi")) + list(self.distributives_folder.glob("*.exe"))
        if not files:
            self.listbox.insert(tk.END, "--- нет дистрибутивов ---")
            self.btn_install.config(state=tk.DISABLED)
            return
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for f in files:
            ver = self._extract_version(f.stem)
            display = f"{f.name} [{ver}]" if ver else f.name
            self.distributives.append({"path": f, "name": f.name})
            self.listbox.insert(tk.END, display)
        self.status_var.set(f"Найдено: {len(files)}")
```

Replace with:

```python
    def refresh_distributives(self):
        """Rescans the Distributives folder and refreshes the table."""
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.distributives = []
        files = list(self.distributives_folder.glob("*.msi")) + list(self.distributives_folder.glob("*.exe"))
        if not files:
            self.btn_install.config(state=tk.DISABLED)
            return
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for f in files:
            ver = self._extract_version(f.stem) or "—"
            size_mb = round(f.stat().st_size / (1024 * 1024), 1)
            self.distributives.append({"path": f, "name": f.name})
            self.tree.insert("", tk.END, iid=str(len(self.distributives) - 1),
                              values=(f.name, ver, size_mb))
        self.status_var.set(f"Найдено: {len(files)}")
```

(`--- нет дистрибутивов ---` as a fake row doesn't translate to an empty Treeview meaningfully — an empty table already communicates "nothing here," and `self.btn_install` staying disabled is the same signal the old code gave. `iid=str(index into self.distributives)` is what makes `on_select_distributive` below trivial and correct.)

- [ ] **Step 3: Update `on_select_distributive`**

Find (currently `r7_Testovarka.py:312-321`):

```python
    def on_select_distributive(self, event):
        """Handles listbox selection — enables Install button and shows file size."""
        sel = self.listbox.curselection()
        if sel and self.distributives:
            self.selected_distributive = self.distributives[sel[0]]
            self.btn_install.config(state=tk.NORMAL)
            mb = self.selected_distributive["path"].stat().st_size / (1024 * 1024)
            self.lbl_file_info.config(text=f"{self.selected_distributive['name']} ({mb:.1f} МБ)")
        else:
            self.btn_install.config(state=tk.DISABLED)
```

Replace with:

```python
    def on_select_distributive(self, event):
        """Handles Treeview selection — enables Install button and shows file size."""
        sel = self.tree.selection()
        if sel and self.distributives:
            idx = int(sel[0])
            self.selected_distributive = self.distributives[idx]
            self.btn_install.config(state=tk.NORMAL)
            mb = self.selected_distributive["path"].stat().st_size / (1024 * 1024)
            self.lbl_file_info.config(text=f"{self.selected_distributive['name']} ({mb:.1f} МБ)")
        else:
            self.btn_install.config(state=tk.DISABLED)
```

- [ ] **Step 4: Confirm no other `self.listbox` references remain**

```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
.venv/Scripts/python.exe -c "
import pathlib
src = pathlib.Path('r7_Testovarka.py').read_text(encoding='utf-8')
assert 'self.listbox' not in src, 'no listbox references should remain'
assert 'self.tree' in src
print('PASS')
"
```
Expected: `OK` then `PASS`.

- [ ] **Step 5: Commit**

```bash
git add r7_Testovarka.py
git commit -m "feat: replace distributives Listbox with a styled Treeview table"
```

---

### Task F: Performance tab redesign — cards, colored log, run button, progress bar, status bar

**Files:** Modify `r7_Testovarka.py`: `_build_perf_tab` (as rebuilt by Task B — restyle its containers/buttons, keep its `test_vars`/`test_runs`-building logic from Task B intact), `add_test_log` (~line 431-442, add severity tags), `_spreadsheet_worker`'s progress reporting (add a progress-bar update alongside the existing `add_test_log` calls, without changing test logic).

**Interfaces:** Produces `self.progress_var: tk.DoubleVar` and a `ttk.Progressbar` in the Performance tab, updated by a new small helper `self._set_perf_progress(done, total)`; `add_test_log` gains a severity-tag lookup (`ERROR`/`WARN`/`INFO`) applied to each inserted line based on its leading emoji, purely cosmetic — its signature and the fact that it's the sole logging entry point for both workers stays unchanged, so `_batch_run_single_version`'s `log_cb`/the batch progress window's own separate `log_text` widget are NOT in scope for this task (that's a different `tk.Text` instance built in `_start_batch_run`, restyling it wasn't requested and touching it risks the untouched batch-mode code paths).

- [ ] **Step 1: Add color tags to `add_test_log`**

Find (currently `r7_Testovarka.py:431-442`):

```python
    def add_test_log(self, msg):
        """Appends a timestamped message to the performance test log widget.

        Args:
            msg: The text to append.
        """
        try:
            self.test_log.insert(tk.END, f"[{datetime.now():%H:%M:%S}] {msg}\n")
            self.test_log.see(tk.END)
            self.root.update()
        except:
            print(msg)
```

Replace with:

```python
    def add_test_log(self, msg):
        """Appends a timestamped, severity-colored message to the performance log.

        Severity is inferred from the leading emoji already used consistently
        throughout the codebase (❌/⚠️ for errors/warnings, everything else
        default) — no call site elsewhere in the file needs to change.

        Args:
            msg: The text to append.
        """
        try:
            if msg.startswith("❌"):
                tag = "ERROR"
            elif msg.startswith("⚠️"):
                tag = "WARN"
            else:
                tag = "INFO"
            line = f"[{datetime.now():%H:%M:%S}] {msg}\n"
            self.test_log.insert(tk.END, line, tag)
            self.test_log.see(tk.END)
            self.root.update()
        except:
            print(msg)
```

- [ ] **Step 2: Rebuild `_build_perf_tab`'s containers/log/cards/buttons with the dark styling and progress bar**

Find the full current `_build_perf_tab` (as it stands after Task B's Step 3 edit — the log widget setup, the `sel_outer`/`cv`/`inner` scaffolding, the row-building loop from Task B, the select-all/deselect-all mini buttons, and the bottom action-button row) and replace it with:

```python
    def _build_perf_tab(self):
        """Builds the performance tab: dark log + card grid of tests + run controls."""
        top = ttk.Frame(self.tab_perf)
        top.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        # ── Log (dark, tagged by severity) ────────────────────────────────────
        log_frame = ttk.Frame(top)
        log_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.test_log = tk.Text(log_frame, font=FONT_LOG, bg=COLORS["log_bg"],
                                fg=COLORS["text"], insertbackground=COLORS["text"],
                                borderwidth=0, highlightthickness=0)
        self.test_log.tag_configure("INFO", foreground=COLORS["success"])
        self.test_log.tag_configure("WARN", foreground=COLORS["warn"])
        self.test_log.tag_configure("ERROR", foreground=COLORS["error"])
        scroll_log = ttk.Scrollbar(log_frame, command=self.test_log.yview)
        self.test_log.configure(yscrollcommand=scroll_log.set)
        scroll_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.test_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Test selection: scrollable grid of cards (2 columns) ──────────────
        sel_outer = ttk.LabelFrame(top, text="Выберите тесты", padding="4")
        sel_outer.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))

        cv = tk.Canvas(sel_outer, width=380, borderwidth=0, highlightthickness=0,
                       bg=COLORS["bg"])
        vsb = ttk.Scrollbar(sel_outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        cv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = ttk.Frame(cv)
        cv_win = cv.create_window((0, 0), window=inner, anchor="nw")

        saved = self._load_test_selection()
        self.test_vars = {}
        self.test_runs = {}
        CARD_COLS = 2
        for idx, name in enumerate(self.TEST_DEFINITIONS):
            entry = saved.get(name, {"enabled": True, "runs": 3})
            var = tk.BooleanVar(value=entry.get("enabled", True))
            runs_var = tk.IntVar(value=entry.get("runs", 3))

            card = tk.Frame(inner, bg=COLORS["bg_card"], bd=1, relief=tk.SOLID,
                            highlightbackground=COLORS["border"], highlightthickness=1)
            card.grid(row=idx // CARD_COLS, column=idx % CARD_COLS,
                      padx=4, pady=4, sticky="nsew")

            def _on_enter(_e, c=card):
                c.configure(bg=COLORS["border_hover"])
                for w in c.winfo_children():
                    if isinstance(w, tk.Label):
                        w.configure(bg=COLORS["border_hover"])
            def _on_leave(_e, c=card):
                c.configure(bg=COLORS["bg_card"])
                for w in c.winfo_children():
                    if isinstance(w, tk.Label):
                        w.configure(bg=COLORS["bg_card"])
            card.bind("<Enter>", _on_enter)
            card.bind("<Leave>", _on_leave)

            row1 = ttk.Frame(card, style="Card.TFrame")
            row1.pack(fill=tk.X, padx=6, pady=(6, 2))
            ttk.Checkbutton(row1, variable=var).pack(side=tk.LEFT)
            ttk.Spinbox(row1, from_=1, to=10, increment=1, width=4,
                        textvariable=runs_var).pack(side=tk.LEFT, padx=(4, 0))
            lbl = tk.Label(card, text=name, bg=COLORS["bg_card"], fg=COLORS["text"],
                          wraplength=150, anchor=tk.W, justify=tk.LEFT,
                          font=FONT_UI)
            lbl.pack(fill=tk.X, padx=6, pady=(0, 6))

            self.test_vars[name] = var
            self.test_runs[name] = runs_var

        for c in range(CARD_COLS):
            inner.columnconfigure(c, weight=1)

        def _on_inner_configure(event):
            cv.configure(scrollregion=cv.bbox("all"))
        def _on_canvas_configure(event):
            cv.itemconfig(cv_win, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        cv.bind("<Configure>", _on_canvas_configure)

        mini = ttk.Frame(sel_outer)
        mini.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(mini, text="☑ Все", width=7,
                   command=lambda: [v.set(True) for v in self.test_vars.values()]).pack(side=tk.LEFT)
        ttk.Button(mini, text="☐ Снять", width=7,
                   command=lambda: [v.set(False) for v in self.test_vars.values()]).pack(side=tk.LEFT, padx=3)

        # ── Progress bar ────────────────────────────────────────────────────
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(self.tab_perf, variable=self.progress_var, maximum=100,
                        mode="determinate").pack(fill=tk.X, padx=2, pady=(4, 0))

        # ── Bottom action buttons ───────────────────────────────────────────
        btn_frame = ttk.Frame(self.tab_perf)
        btn_frame.pack(fill=tk.X, pady=4)
        ttk.Button(btn_frame, text="▶ Запустить выбранные тесты", style="Accent.TButton",
                   command=self.run_spreadsheet_test).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📊 Сравнить размеры файлов",
                   command=self.compare_file_sizes).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="📊 Сравнить версии",
                   command=self.compare_versions).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="🚀 Batch-режим (все версии)",
                   command=self.run_batch_mode).pack(side=tk.LEFT, padx=5)
```

(Per-card hover recoloring is implemented with plain `tk.Frame`/`tk.Label` — not `ttk` — inside each card specifically because `ttk` widgets don't expose a simple per-instance `bg`/`<Enter>`/`<Leave>` recolor the way classic `tk` widgets do without defining a whole new named style per card; this is the documented approximation for "card highlights on hover" using achievable Tk mechanics, consistent with this plan's ttk-limitations constraint.)

- [ ] **Step 3: Add a progress helper and call it from `_spreadsheet_worker`**

Add this small method near `add_test_log`:

```python
    def _set_perf_progress(self, done, total):
        """Updates the Performance tab's progress bar (0-100%). Safe to call
        even if the widget doesn't exist yet or the app is in another mode."""
        try:
            pct = 100 * done / total if total else 0
            self.progress_var.set(pct)
            self.root.update_idletasks()
        except Exception:
            pass
```

Task C Step 2 already built a `_test_ops` list and a `for _name, _func in _test_ops: run_test_with_runs(_name, _func, test_runs.get(_name, 3))` loop inside `_spreadsheet_worker` — this step ADDS progress reporting around that existing loop, it does not rebuild it (do not reintroduce `self.test_runs[...]` here — the loop must keep reading the `test_runs` parameter, per Task C's Interfaces note about not touching Tk variables from the worker thread).

Find (as Task C Step 2 left it):

```python
        for _name, _func in _test_ops:
            run_test_with_runs(_name, _func, test_runs.get(_name, 3))
        self._cleanup_x2t_temp_pdfs()
```

Replace with:

```python
        self._set_perf_progress(0, len(_test_ops))
        for _i, (_name, _func) in enumerate(_test_ops, start=1):
            run_test_with_runs(_name, _func, test_runs.get(_name, 3))
            self._set_perf_progress(_i, len(_test_ops))
        self._cleanup_x2t_temp_pdfs()
```

- [ ] **Step 4: Verify**

```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
.venv/Scripts/python.exe -c "
import pathlib
src = pathlib.Path('r7_Testovarka.py').read_text(encoding='utf-8')
assert 'tag_configure(\"ERROR\"' in src or \"tag_configure('ERROR'\" in src
assert 'def _set_perf_progress' in src
assert 'self.progress_var' in src
assert '_test_ops' in src
print('PASS')
"
```
Expected: `OK` then `PASS`.

- [ ] **Step 5: Commit**

```bash
git add r7_Testovarka.py
git commit -m "feat: restyle Performance tab with card grid, colored log, and progress bar"
```

---

### Task G: CLAUDE.md update + code-simplifier pass

**Files:** Modify `CLAUDE.md` (method tables); review/lightly refactor `r7_Testovarka.py` per code-simplifier findings — this task's own diff should be small (documentation-sized), the code-simplifier work happens as a dispatched pass whose findings get folded in here or in the final review, per the human partner's explicit "после завершения всех задач примени code-simplifier".

- [ ] **Step 1: Add CLAUDE.md rows for the new methods**

Add to the "Тестирование производительности" table: `run_test_with_runs(name, func, runs)`, `_win_title_contains(*substrings)`, `_cleanup_x2t_temp_pdfs(log_cb=None)`, `_apply_dark_theme`, `_set_perf_progress(done, total)`. Note `_spreadsheet_worker`'s signature is now `(self, enabled_tests=None, test_runs=None)`. Add a new subsection "Тёмная тема" documenting the `COLORS` dict's purpose and the `clam`-theme rationale in one or two lines, matching the file's existing terse style, and one explicit line noting that the main window keeps its native OS titlebar/frame (no `overrideredirect`) — the "custom header" requirement is satisfied by added in-content header widgets below the native titlebar, a deliberate choice to avoid the much larger risk of a hand-reimplemented frameless window (drag-to-move, minimize/restore, DPI/snap behavior) — so a future reader doesn't mistake the native titlebar for an oversight.

- [ ] **Step 2: Note the new JSON fields and settings-file shape**

In CLAUDE.md's description of `selected_tests.json` (search for any existing mention; if none exists, add one near the performance-testing section): document the new `{"enabled": bool, "runs": int}` per-test shape and that the app auto-upgrades the old plain-bool shape on load.

- [ ] **Step 3: Verify and commit**

```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
```

```bash
git add CLAUDE.md
git commit -m "docs: document new test-run, theme, and settings-format changes"
```

---

## Self-Review

**Spec coverage:**
- Задача 1 (x2t/PDF): TEST_DEFINITIONS entry (Task A Step 1), `save_as_pdf` in both workers with hotkey→menu fallback (Task A Steps 2, 5), wiring into `run_test`/`measure` blocks (Task A Steps 3, 5), temp-file cleanup via `os.remove`-equivalent (Task A Steps 4, 5), log lines (`⏳`/`✅` via existing `measure`/`add_test_log` conventions plus the explicit fallback-warning line), x2t capture via the already-shipped per-measurement refresh (no new code needed — inherited from the merged resource-metrics-improvement branch). ✅
- Задача 2 (run counts): Checkbutton+Spinbox+Label rows (Task B Step 3, restyled into cards in Task F Step 2), `self.test_runs` dict (Task B Steps 2-3), JSON format extension with backward-compat load (Task B Step 1), `run_test_with_runs` (Task C Step 1), per-pass logs matching the requested format (Task C Step 1's `add_test_log` calls), JSON `runs`/`avg`/`min`/`max` fields (Task C Step 1), HTML avg+range display (Task C Step 3), Batch-mode/comparison non-breakage (Global Constraints + Task C's additive-only field design — verified `_batch_run_single_version` is untouched by Tasks B/C). ✅
- Задача 3 (dark theme): color constants (Task D Step 1), ttk.Style setup for TButton/TLabel/TFrame/TNotebook/TProgressbar/Treeview/Scrollbar (Task D Step 2), header+status indicator+shadow-approximation (Task D Step 3), Versions tab Treeview+styled buttons (Task E), Performance tab dark log+tags, card grid, accent run button, progress bar, status bar (Task F). Every place the spec asked for an effect plain ttk can't do (rounded corners, blurred shadow, in-cell Treeview button, Treeview row hover) is implemented with a documented nearest-equivalent, not silently skipped. ✅

**Placeholder scan:** every step carries literal code or an exact runnable verification command; no "add appropriate styling" or "similar to Task X" without inline code.

**Type consistency:** `self.test_vars` built identically in Task B Step 3 and rebuilt identically in Task F Step 2 — Task F's version is the one that ships (it runs after and replaces Task B's `_build_perf_tab` body), Task B's version exists so Task C can be reviewed/tested against a working UI before Task F's restyle lands. `run_test_with_runs(name, func, runs)` signature is identical across its Task C definition and its one call site (inside the `_test_ops` loop, built once in Task C Step 2 and only extended — not rebuilt — by Task F Step 3). `test_runs` (a plain `dict[str, int]`, snapshotted on the main thread in Task B Step 4 and threaded through `_spreadsheet_worker`'s new parameter in Task B Step 5) is read via `test_runs.get(name, 3)` everywhere inside the worker thread; `self.test_runs` (the live `dict[str, tk.IntVar]`) is only ever read on the main thread (`_save_test_selection`, `_build_perf_tab`'s row-building code) — this distinction is deliberate and must not be collapsed by an implementer "simplifying" the two dicts into one.

**Validation pass:** before dispatching any implementer, this plan was reviewed by a dedicated Plan agent against the actual current `r7_Testovarka.py` (not just this document). It confirmed every cited file:line/structure claim, validated the backward-compatibility design against the real `compare_versions`/`_generate_comparison_html`/Excel-writer code (not just a description of it), and confirmed the `clam`-theme technical claim by grepping the file for pre-existing `ttk.Style`/`winfo_rgb` usage (none found — a clean foundation). It found one **critical** defect (this document's original `run_test_with_runs` never refreshed `r7_procs` before sampling, silently reverting the x2t short-lived-process fix for every operation once it replaced `run_test`/`measure` as the sole per-op sampling path) and one **real thread-safety inconsistency** (`self.test_runs[name].get()` would have been called from the worker thread, unlike the existing `enabled_tests` pattern which is deliberately snapshotted on the main thread) — both are fixed in Task C Step 1/Task B Steps 4-5 above, not left as known issues. It also flagged two lower-severity visual side-effects of Task D's global `ttk.Style` singleton (light Toplevel frames around now-dark ttk content in 7 untouched dialogs; hardcoded light Treeview row-tag colors in the hash-check window) — both mitigated in Task D Steps 4-5 with the smallest change that removes the regression without expanding scope into a full redesign of those 7 dialogs. One design ambiguity (does "custom header replacing reliance on the OS titlebar" mean a frameless window?) was resolved via best-practice judgment per this plan's autonomous-mode authorization: keep the native OS frame, add header content below it — documented explicitly in Task G Step 1 so it reads as a deliberate choice, not an oversight, when reviewed later.
