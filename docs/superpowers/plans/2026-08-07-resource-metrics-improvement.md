# Улучшение замеров CPU/RAM в стресс-тестах Р7-Офис — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CPU/RAM measurement in the R7-Testovarka stress tests (`r7_Testovarka.py`) more accurate and informative — track the `x2t` converter process, normalize CPU to a 0–100% scale, capture thread count and process uptime, enrich JSON/HTML reports, and add color-coded resource logging.

**Architecture:** All logic lives in the single existing file `E:\R7Manager\r7_Testovarka.py` (one class `R7Testovarka`, no test framework, no other source files). Two near-duplicate resource-sampling closures (`sample_resources()` inside `_spreadsheet_worker`, `_sample()` inside `_batch_run_single_version`) get replaced by one shared instance method `_sample_r7_resources(procs)`, paired with a new `_log_resources()` formatter. `_get_r7_processes()` gains `x2t` to its search list. Both JSON writers and `_generate_html_report()` gain new **additive** fields/columns — nothing existing is renamed or removed, so old `performance_full_*.json` files stay readable by `compare_versions()`.

**Tech Stack:** Python 3.14, Tkinter, psutil 7.2 (verified against Context7 docs — `Process.cpu_percent()` is NOT normalized by core count by default; `p.cpu_percent() / psutil.cpu_count()` is the documented way to emulate Windows Task Manager's 0–100% scale), win32gui, openpyxl, Chart.js (CDN, in generated HTML only).

## Global Constraints

- **No test framework exists in this repo** (verified: the only `test_*.py` files under `E:\R7Manager` live inside `.venv/Lib/site-packages`, none belong to this project). `_get_r7_processes`, `_sample_r7_resources`, `_log_resources` are regular methods that only touch `self._r7_pids` and `self.add_test_log` — each task's "test" step calls them unbound (`R7Testovarka.<method>(fake_self)`) against a lightweight stand-in object via `.venv/Scripts/python.exe -c "..."`, so no live Tkinter window or real Р7-Офис process is required to verify the logic. This is a deliberate substitute for pytest, not an oversight.
- **Backward compatibility is mandatory** (explicit user requirement): every new field is additive (`results[i]["cpu_normalized"]`, `["threads"]`, `["uptime_sec"]`; `summary["peak_cpu_normalized_pct"]`, `["avg_cpu_normalized_pct"]`; `system["cpu_model"]`). Existing keys (`"ram"`, `"cpu"`, `"peak_cpu_pct"`, `"os"`, `"ram_total_gb"`) keep their exact old meaning (raw, unnormalized) so `compare_versions()` reading old JSON files alongside new ones stays correct.
- **Defensive coding**: every `psutil` call that can raise `NoSuchProcess`/`AccessDenied` is wrapped in try/except, matching the existing codebase convention (see `_get_r7_processes`, `sample_resources`).
- **Comments**: short Russian comments only where the WHY is non-obvious (e.g. why CPU is divided by core count, why x2t matters). No comment blocks, no restating what the code already says.
- **CLAUDE.md** gets new rows in the "Тестирование производительности" method table for `_get_r7_processes`, `_sample_r7_resources`, `_log_resources` (final task).
- After all tasks, run `/code-review` on the diff and the `code-simplifier` agent on the touched regions before considering the work done (per user's explicit instructions) — folded into the final task rather than after every micro-edit, since intermediate states within a single task are not independently meaningful (e.g. the shared helper isn't wired to anything until Task 4/5).

---

## File Structure

Only one file changes:

- **Modify: `E:\R7Manager\r7_Testovarka.py`**
  - `_get_r7_processes` (~line 977): add `"x2t"` to the search list + detection log.
  - New `_sample_r7_resources` method (insert after `_get_r7_processes`): shared RAM/CPU/threads/uptime sampler, replaces two duplicated closures.
  - New `_log_resources` method (insert right after `_sample_r7_resources`): formatted, color-coded log line.
  - `_spreadsheet_worker` (~line 475): wire in the shared sampler, extend `results` dicts, extend JSON `full_data`.
  - `_batch_run_single_version` (~line 2200): same wiring, extend JSON dump.
  - `_generate_html_report` (~line 1017): normalized-CPU card, note, table column, chart series.
- **Modify: `E:\R7Manager\CLAUDE.md`** — document the three new/changed methods.

---

### Task 1: Track `x2t` in `_get_r7_processes`

**Files:**
- Modify: `r7_Testovarka.py:977-1015` (method `_get_r7_processes`)

**Interfaces:**
- Produces: `_get_r7_processes(self) -> list[psutil.Process]` — same signature/return type as before, now also matches processes whose `name()` contains `"x2t"` and logs a detection line via `self.add_test_log`.

- [ ] **Step 1: Confirm current behavior (no x2t tracking) with a throwaway probe**

Run:
```bash
.venv/Scripts/python.exe -c "
import ast, pathlib
src = pathlib.Path('r7_Testovarka.py').read_text(encoding='utf-8')
tree = ast.parse(src)
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_get_r7_processes':
        body_src = ast.get_source_segment(src, node)
        print('x2t' in body_src)
"
```
Expected: `False` (x2t is not yet in the search list).

- [ ] **Step 2: Edit `_get_r7_processes`**

Replace the full method body (currently `r7_Testovarka.py:977-1015`):

```python
    def _get_r7_processes(self):
        """Returns list of psutil.Process objects for all R7-Office related processes.

        Searches by name substrings: editors_helper, desktopeditors, r7, р7 (Cyrillic),
        x2t (внутренний конвертер документов Р7-Офис — отдельный процесс, который
        может давать заметный вклад в общую RAM/CPU при открытии/сохранении файлов).
        If self._r7_pids is set (from a previous call), tries direct PID lookup first.
        """
        if not PSUTIL_OK:
            return []

        # Fast path: try previously discovered PIDs directly
        if getattr(self, "_r7_pids", None):
            procs = []
            for pid in self._r7_pids:
                try:
                    p = psutil.Process(pid)
                    p.name()  # raises NoSuchProcess if dead
                    procs.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if procs:
                return procs

        # Full scan with expanded name list
        search_substrings = ("editors_helper", "desktopeditors", "r7", "р7", "x2t")
        found = []
        try:
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if any(s in name for s in search_substrings):
                        found.append(proc)
                        if "x2t" in name:
                            self.add_test_log(
                                f"🔧 Обнаружен процесс конвертации x2t: "
                                f"PID={proc.info.get('pid')}, имя={proc.info.get('name')}"
                            )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

        # Cache PIDs for subsequent fast-path calls
        self._r7_pids = [p.pid for p in found]
        return found
```

- [ ] **Step 3: Verify the edit landed and syntax is valid**

Run:
```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
```
Expected: `OK` (no `SyntaxError`).

- [ ] **Step 4: Verify x2t detection logic in isolation (no GUI, no real Р7-Офис needed)**

Run:
```bash
.venv/Scripts/python.exe -c "
import types, sys
sys.modules.setdefault('pyautogui', types.ModuleType('pyautogui'))
import unittest.mock as mock
import psutil
import r7_Testovarka as m

logs = []
fake_self = types.SimpleNamespace(_r7_pids=None, add_test_log=lambda msg: logs.append(msg))

class FakeProc:
    def __init__(self, pid, name):
        self.pid = pid
        self.info = {'pid': pid, 'name': name}

fake_procs = [FakeProc(111, 'x2t.exe'), FakeProc(222, 'DesktopEditors.exe'), FakeProc(333, 'notepad.exe')]

with mock.patch.object(psutil, 'process_iter', return_value=fake_procs):
    result = m.R7Testovarka._get_r7_processes(fake_self)

names = sorted(p.info['name'] for p in result)
assert names == ['DesktopEditors.exe', 'x2t.exe'], names
assert any('x2t' in l and '111' in l for l in logs), logs
print('PASS:', names, '|', logs)
"
```
Expected: `PASS: ['DesktopEditors.exe', 'x2t.exe'] | ['🔧 Обнаружен процесс конвертации x2t: PID=111, имя=x2t.exe']`

(`PYAUTOGUI_OK`/`WIN32_OK` import guards in the module already print warnings and continue on import when those libs are missing in a bare check — harmless. If pywin32/openpyxl/pyperclip are missing in the venv, the import still succeeds because every third-party import in the module is wrapped in try/except at module load time.)

- [ ] **Step 5: Commit**

```bash
git add r7_Testovarka.py
git commit -m "Учитывать процесс x2t в поиске процессов Р7-Офис"
```

---

### Task 2: Shared resource sampler `_sample_r7_resources`

**Files:**
- Modify: `r7_Testovarka.py` — insert new method directly after `_get_r7_processes` (after the line ending Task 1's edit, before `_generate_html_report`).

**Interfaces:**
- Consumes: a list of `psutil.Process` as returned by `_get_r7_processes()`.
- Produces: `_sample_r7_resources(self, procs) -> dict | None` with keys `ram_mb` (float), `cpu_raw_pct` (float, psutil's native — can exceed 100 on multi-core), `cpu_norm_pct` (float, `cpu_raw_pct / psutil.cpu_count()`, 0–100 scale matching Task Manager), `threads` (int, summed `num_threads()` across processes), `uptime_sec` (float | None, seconds since the *oldest* matched process's `create_time()`). Returns `None` when psutil is unavailable or no process in `procs` is alive — this is the exact same "no data" contract the two old closures used (`return None, None`), so callers that already do `if sample is not None` / `.get(...)` keep working. Tasks 4 and 5 consume this.

- [ ] **Step 1: Confirm the method doesn't exist yet**

Run:
```bash
.venv/Scripts/python.exe -c "
import r7_Testovarka as m
print(hasattr(m.R7Testovarka, '_sample_r7_resources'))
"
```
Expected: `False`.

- [ ] **Step 2: Insert the method**

Insert immediately after the `_get_r7_processes` method body (i.e. right before `def _generate_html_report`):

```python
    def _sample_r7_resources(self, procs):
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
        if not (PSUTIL_OK and procs):
            return None

        total_ram_mb  = 0.0
        max_cpu_raw   = 0.0
        total_threads = 0
        oldest_create = None
        alive = 0
        now = time.time()

        for p in procs:
            try:
                total_ram_mb += p.memory_info().rss / (1024 * 1024)
                max_cpu_raw = max(max_cpu_raw, p.cpu_percent(interval=0.1))
                alive += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

            try:
                total_threads += p.num_threads()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            try:
                create_ts = p.create_time()
                if oldest_create is None or create_ts < oldest_create:
                    oldest_create = create_ts
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if alive == 0:
            return None

        cpu_count = psutil.cpu_count() or 1
        return {
            "ram_mb":       round(total_ram_mb, 1),
            "cpu_raw_pct":  round(max_cpu_raw, 1),
            "cpu_norm_pct": round(max_cpu_raw / cpu_count, 1),
            "threads":      total_threads,
            "uptime_sec":   round(now - oldest_create, 1) if oldest_create is not None else None,
        }
```

- [ ] **Step 3: Syntax check**

Run:
```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
```
Expected: `OK`.

- [ ] **Step 4: Verify aggregation math against the real current process (no mocking needed for this one)**

Run:
```bash
.venv/Scripts/python.exe -c "
import types, sys, os, psutil
import r7_Testovarka as m

fake_self = types.SimpleNamespace()
me = psutil.Process(os.getpid())
me.cpu_percent(interval=None)  # первый вызов psutil всегда возвращает 0.0 — прогреваем счётчик
import time; time.sleep(0.15)

sample = m.R7Testovarka._sample_r7_resources(fake_self, [me])
assert sample is not None
assert sample['ram_mb'] > 0
assert sample['threads'] >= 1
assert sample['uptime_sec'] >= 0
assert abs(sample['cpu_norm_pct'] - sample['cpu_raw_pct'] / (psutil.cpu_count() or 1)) < 0.05
print('PASS:', sample)

none_sample = m.R7Testovarka._sample_r7_resources(fake_self, [])
assert none_sample is None
print('PASS: empty procs -> None')
"
```
Expected: two `PASS:` lines, no `AssertionError`.

- [ ] **Step 5: Commit**

```bash
git add r7_Testovarka.py
git commit -m "Добавить общий метод _sample_r7_resources для замеров RAM/CPU/потоков/аптайма"
```

---

### Task 3: Color-coded logger `_log_resources`

**Files:**
- Modify: `r7_Testovarka.py` — insert new method directly after `_sample_r7_resources`.

**Interfaces:**
- Consumes: the `dict | None` produced by `_sample_r7_resources`.
- Produces: `_log_resources(self, sample, log_cb=None) -> None`. `log_cb` defaults to `self.add_test_log`; `_batch_run_single_version` (Task 5) passes its own `log_cb` closure so batch-mode resource lines land in the batch progress window's log, not just the main tab. Writes nothing when `sample` is `None` (mirrors the old `if ram is not None:` guard).

- [ ] **Step 1: Confirm the method doesn't exist yet**

Run:
```bash
.venv/Scripts/python.exe -c "
import r7_Testovarka as m
print(hasattr(m.R7Testovarka, '_log_resources'))
"
```
Expected: `False`.

- [ ] **Step 2: Insert the method**

Insert immediately after `_sample_r7_resources` (still before `_generate_html_report`):

```python
    def _log_resources(self, sample, log_cb=None):
        """Форматированный вывод одного замера ресурсов с цветовой индикацией CPU.

        Индикатор считается по нормализованному CPU (0–100%, все ядра):
        🟢 < 50% — обычная нагрузка, 🟡 50–80% — средняя, 🔴 > 80% — высокая.

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
```

- [ ] **Step 3: Syntax check**

Run:
```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
```
Expected: `OK`.

- [ ] **Step 4: Verify formatting and the three color thresholds**

Run:
```bash
.venv/Scripts/python.exe -c "
import types
import r7_Testovarka as m

logs = []
fake_self = types.SimpleNamespace(add_test_log=lambda s: logs.append(s))

for cpu_norm, expected_icon in [(10.0, '🟢'), (65.0, '🟡'), (95.0, '🔴')]:
    logs.clear()
    sample = {'ram_mb': 512.3, 'cpu_raw_pct': cpu_norm * 4, 'cpu_norm_pct': cpu_norm, 'threads': 12, 'uptime_sec': 42.0}
    m.R7Testovarka._log_resources(fake_self, sample)
    assert len(logs) == 1, logs
    assert expected_icon in logs[0], (cpu_norm, logs)
    print('PASS:', logs[0])

logs.clear()
m.R7Testovarka._log_resources(fake_self, None)
assert logs == [], 'None sample must not log anything'
print('PASS: None sample -> no log')
"
```
Expected: three `PASS:` lines showing the formatted string with the right emoji, plus `PASS: None sample -> no log`.

- [ ] **Step 5: Commit**

```bash
git add r7_Testovarka.py
git commit -m "Добавить _log_resources для цветового логирования замеров ресурсов"
```

---

### Task 4: Wire the shared sampler into `_spreadsheet_worker`

**Files:**
- Modify: `r7_Testovarka.py:770-824` (the `sample_resources` closure, its two call sites, and `measure()`)
- Modify: `r7_Testovarka.py:894-905` (peak/avg/min stats block)
- Modify: `r7_Testovarka.py:925-946` (`full_data` JSON dict)

**Interfaces:**
- Consumes: `self._sample_r7_resources` (Task 2), `self._log_resources` (Task 3).
- Produces: each dict in `results` now additionally carries `cpu_normalized`, `threads`, `uptime_sec` (all `None` when no sample). `full_data["summary"]` additionally carries `peak_cpu_normalized_pct`, `avg_cpu_normalized_pct`. `full_data["system"]` additionally carries `cpu_model`. Task 6 (HTML report) reads these new keys via `.get()`.

- [ ] **Step 1: Confirm current shape (no `cpu_normalized` key yet)**

Run:
```bash
.venv/Scripts/python.exe -c "
import ast, pathlib
src = pathlib.Path('r7_Testovarka.py').read_text(encoding='utf-8')
print('cpu_normalized' in src)
"
```
Expected: `False`.

- [ ] **Step 2: Replace the `sample_resources` closure and its call site at result[0]**

Find (currently `r7_Testovarka.py:770-791`):

```python
        def sample_resources():
            """Returns (total_ram_mb, max_cpu_pct) summed across all R7 processes."""
            if not (PSUTIL_OK and r7_procs):
                return None, None
            total_ram = 0.0
            max_cpu   = 0.0
            alive = 0
            for p in r7_procs:
                try:
                    total_ram += p.memory_info().rss / (1024 * 1024)
                    max_cpu    = max(max_cpu, p.cpu_percent(interval=0.1))
                    alive += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if alive == 0:
                return None, None
            return round(total_ram, 1), round(max_cpu, 1)

        # ----- 4. Тесты ----------------------------------------------------------------
        ram0, cpu0 = sample_resources()
        results = [{"name": "Открытие файла", "time": open_elapsed, "error": None,
                    "ram": ram0, "cpu": cpu0}]
```

Replace with:

```python
        # ----- 4. Тесты ----------------------------------------------------------------
        sample0 = self._sample_r7_resources(r7_procs)
        results = [{
            "name": "Открытие файла", "time": open_elapsed, "error": None,
            "ram":            sample0["ram_mb"]       if sample0 else None,
            "cpu":            sample0["cpu_raw_pct"]   if sample0 else None,
            "cpu_normalized": sample0["cpu_norm_pct"]  if sample0 else None,
            "threads":        sample0["threads"]       if sample0 else None,
            "uptime_sec":     sample0["uptime_sec"]    if sample0 else None,
        }]
```

- [ ] **Step 3: Replace `measure()` to use the shared sampler + logger**

Find (currently `r7_Testovarka.py:793-824`):

```python
        def measure(name, func):
            """Times a single UI operation, samples resources, and logs the result.

            Skips the operation if name is not in enabled_tests.

            Args:
                name: Human-readable label; must match an entry in TEST_DEFINITIONS.
                func: Callable that performs the operation.

            Returns:
                dict with keys name/time/error/ram/cpu, or None if test is skipped.
            """
            if name not in enabled_tests:
                return None
            self.add_test_log(f"⏳ {name}...")
            try:
                focus_window()
            except Exception as e:
                self.add_test_log(f"   ⚠️ Не удалось установить фокус: {e}")
            start = time.time()
            error = None
            try:
                func()
            except Exception as e:
                error = str(e)
            elapsed = time.time() - start
            post_action_delay()
            ram, cpu = sample_resources()
            if ram is not None:
                self.add_test_log(f"   📊 RAM: {ram:.1f} МБ, CPU: {cpu:.1f}%")
            self.add_test_log(f"   ✅ {name}: {elapsed:.2f} сек" + (f" (ошибка: {error})" if error else ""))
            return {"name": name, "time": elapsed, "error": error, "ram": ram, "cpu": cpu}
```

Replace with:

```python
        def measure(name, func):
            """Times a single UI operation, samples resources, and logs the result.

            Skips the operation if name is not in enabled_tests.

            Args:
                name: Human-readable label; must match an entry in TEST_DEFINITIONS.
                func: Callable that performs the operation.

            Returns:
                dict with keys name/time/error/ram/cpu/cpu_normalized/threads/uptime_sec,
                or None if test is skipped.
            """
            if name not in enabled_tests:
                return None
            self.add_test_log(f"⏳ {name}...")
            try:
                focus_window()
            except Exception as e:
                self.add_test_log(f"   ⚠️ Не удалось установить фокус: {e}")
            start = time.time()
            error = None
            try:
                func()
            except Exception as e:
                error = str(e)
            elapsed = time.time() - start
            post_action_delay()
            sample = self._sample_r7_resources(r7_procs)
            self._log_resources(sample)
            self.add_test_log(f"   ✅ {name}: {elapsed:.2f} сек" + (f" (ошибка: {error})" if error else ""))
            return {
                "name": name, "time": elapsed, "error": error,
                "ram":            sample["ram_mb"]      if sample else None,
                "cpu":            sample["cpu_raw_pct"]  if sample else None,
                "cpu_normalized": sample["cpu_norm_pct"] if sample else None,
                "threads":        sample["threads"]      if sample else None,
                "uptime_sec":     sample["uptime_sec"]    if sample else None,
            }
```

- [ ] **Step 4: Extend the peak/avg/min stats block**

Find (currently `r7_Testovarka.py:894-905`):

```python
        # ----- 5. Статистика ресурсов --------------------------------------------------
        ram_vals = [r["ram"] for r in results if r.get("ram") is not None]
        cpu_vals = [r["cpu"] for r in results if r.get("cpu") is not None]
        peak_ram = max(ram_vals) if ram_vals else None
        avg_ram  = round(sum(ram_vals) / len(ram_vals), 1) if ram_vals else None
        min_ram  = min(ram_vals) if ram_vals else None
        peak_cpu = max(cpu_vals) if cpu_vals else None
        if peak_ram is not None:
            self.add_test_log(
                f"📊 Пик RAM: {peak_ram:.1f} МБ  Средн: {avg_ram:.1f} МБ  Мин: {min_ram:.1f} МБ")
        if peak_cpu is not None:
            self.add_test_log(f"📊 Пик CPU: {peak_cpu:.1f}%")
```

Replace with:

```python
        # ----- 5. Статистика ресурсов --------------------------------------------------
        ram_vals      = [r["ram"] for r in results if r.get("ram") is not None]
        cpu_vals      = [r["cpu"] for r in results if r.get("cpu") is not None]
        cpu_norm_vals = [r["cpu_normalized"] for r in results if r.get("cpu_normalized") is not None]
        peak_ram = max(ram_vals) if ram_vals else None
        avg_ram  = round(sum(ram_vals) / len(ram_vals), 1) if ram_vals else None
        min_ram  = min(ram_vals) if ram_vals else None
        peak_cpu = max(cpu_vals) if cpu_vals else None
        peak_cpu_norm = max(cpu_norm_vals) if cpu_norm_vals else None
        avg_cpu_norm  = round(sum(cpu_norm_vals) / len(cpu_norm_vals), 1) if cpu_norm_vals else None
        if peak_ram is not None:
            self.add_test_log(
                f"📊 Пик RAM: {peak_ram:.1f} МБ  Средн: {avg_ram:.1f} МБ  Мин: {min_ram:.1f} МБ")
        if peak_cpu is not None:
            self.add_test_log(
                f"📊 Пик CPU: {peak_cpu:.1f}% (сырое)  {peak_cpu_norm:.1f}% (норм., "
                f"{psutil.cpu_count() if PSUTIL_OK else '?'} ядер)")
```

- [ ] **Step 5: Extend `full_data` (JSON report)**

Find (currently `r7_Testovarka.py:925-946`):

```python
            # JSON (полные данные для последующего сравнения версий)
            json_path = self.reports_folder / f"performance_full_{ts}.json"
            sys_mem_gb = round(psutil.virtual_memory().total / (1024**3), 1) if PSUTIL_OK else None
            full_data = {
                "timestamp": ts,
                "version": self.current_version_info.get("name") if self.current_version_info else None,
                "test_file": str(test_file),
                "system": {
                    "os": platform.platform(),
                    "ram_total_gb": sys_mem_gb,
                },
                "summary": {
                    "peak_ram_mb": peak_ram,
                    "avg_ram_mb": avg_ram,
                    "min_ram_mb": min_ram,
                    "peak_cpu_pct": peak_cpu,
                },
                "results": results,
            }
```

Replace with:

```python
            # JSON (полные данные для последующего сравнения версий)
            json_path = self.reports_folder / f"performance_full_{ts}.json"
            sys_mem_gb = round(psutil.virtual_memory().total / (1024**3), 1) if PSUTIL_OK else None
            full_data = {
                "timestamp": ts,
                "version": self.current_version_info.get("name") if self.current_version_info else None,
                "test_file": str(test_file),
                "system": {
                    "os": platform.platform(),
                    "ram_total_gb": sys_mem_gb,
                    "cpu_model": platform.processor() or None,
                },
                "summary": {
                    "peak_ram_mb": peak_ram,
                    "avg_ram_mb": avg_ram,
                    "min_ram_mb": min_ram,
                    "peak_cpu_pct": peak_cpu,
                    "peak_cpu_normalized_pct": peak_cpu_norm,
                    "avg_cpu_normalized_pct": avg_cpu_norm,
                },
                "results": results,
            }
```

- [ ] **Step 6: Syntax check**

Run:
```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
```
Expected: `OK`.

- [ ] **Step 7: Static structural check — old field names still present, new ones added, no leftover reference to the deleted closure**

Run:
```bash
.venv/Scripts/python.exe -c "
import pathlib
src = pathlib.Path('r7_Testovarka.py').read_text(encoding='utf-8')
assert 'def sample_resources()' not in src, 'old closure must be removed'
assert 'ram0, cpu0 = sample_resources()' not in src
assert src.count('cpu_normalized') >= 3, 'expect uses in measure(), stats block, and later HTML/report reads'
assert 'peak_cpu_normalized_pct' in src
assert 'cpu_model' in src
print('PASS')
"
```
Expected: `PASS`.

- [ ] **Step 8: Commit**

```bash
git add r7_Testovarka.py
git commit -m "Использовать _sample_r7_resources/_log_resources в _spreadsheet_worker, расширить JSON-отчёт"
```

---

### Task 5: Wire the shared sampler into `_batch_run_single_version`

**Files:**
- Modify: `r7_Testovarka.py:2295-2332` (the `_sample()` closure, its call site, and `measure()`)
- Modify: `r7_Testovarka.py:2402-2407` (peak/avg stats block)
- Modify: `r7_Testovarka.py:2421-2453` (JSON dump + return dict)

**Interfaces:**
- Consumes: `self._sample_r7_resources`, `self._log_resources(sample, log_cb=log_cb)` — note batch mode passes its own `log_cb` (the batch progress window's logger), unlike `_spreadsheet_worker` which uses the default `self.add_test_log`.
- Produces: same additive fields as Task 4, applied to batch-mode's `results`/JSON/return dict, so `_generate_batch_summary_html` (untouched by this plan) keeps working off the fields it already reads (`open_elapsed`, `vlookup_elapsed`, `peak_ram`, `avg_ram`, `peak_cpu`) while the richer JSON file on disk is available for anyone inspecting it directly or feeding it to `compare_versions()`.

- [ ] **Step 1: Confirm current shape**

Run:
```bash
.venv/Scripts/python.exe -c "
import pathlib
src = pathlib.Path('r7_Testovarka.py').read_text(encoding='utf-8')
print(src.count('def _sample():'))
"
```
Expected: `1` (the closure still exists, to be removed in this task).

- [ ] **Step 2: Replace the `_sample` closure and its call site**

Find (currently `r7_Testovarka.py:2291-2310`):

```python
        # ── Мониторинг ресурсов ───────────────────────────────────────────────
        self._r7_pids = None
        r7_procs = self._get_r7_processes()

        def _sample():
            if not (PSUTIL_OK and r7_procs):
                return None, None
            tr, mc, alive = 0.0, 0.0, 0
            for p in r7_procs:
                try:
                    tr += p.memory_info().rss / (1024 * 1024)
                    mc  = max(mc, p.cpu_percent(interval=0.1))
                    alive += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return (round(tr, 1), round(mc, 1)) if alive else (None, None)

        ram0, cpu0 = _sample()
        results = [{"name": "Открытие файла", "time": open_elapsed,
                    "error": None, "ram": ram0, "cpu": cpu0}]
```

Replace with:

```python
        # ── Мониторинг ресурсов ───────────────────────────────────────────────
        self._r7_pids = None
        r7_procs = self._get_r7_processes()

        sample0 = self._sample_r7_resources(r7_procs)
        results = [{
            "name": "Открытие файла", "time": open_elapsed, "error": None,
            "ram":            sample0["ram_mb"]       if sample0 else None,
            "cpu":            sample0["cpu_raw_pct"]   if sample0 else None,
            "cpu_normalized": sample0["cpu_norm_pct"]  if sample0 else None,
            "threads":        sample0["threads"]       if sample0 else None,
            "uptime_sec":     sample0["uptime_sec"]    if sample0 else None,
        }]
```

- [ ] **Step 3: Replace `measure()` to use the shared sampler + logger**

Find (currently `r7_Testovarka.py:2312-2332`):

```python
        def measure(name, func):
            if stop_event.is_set():
                return
            if pause_event.is_set():
                log_cb("⏸ Пауза...")
                pause_event.wait()
                log_cb("▶ Продолжение...")
            log_cb(f"⏳ {name}...")
            _focus()
            t0  = time.time()
            err = None
            try:
                func()
            except Exception as e:
                err = str(e)
            elapsed = time.time() - t0
            time.sleep(0.5)
            ram, cpu = _sample()
            log_cb(f"   ✅ {name}: {elapsed:.2f} сек" + (f" (ошибка: {err})" if err else ""))
            results.append({"name": name, "time": elapsed,
                            "error": err, "ram": ram, "cpu": cpu})
```

Replace with:

```python
        def measure(name, func):
            if stop_event.is_set():
                return
            if pause_event.is_set():
                log_cb("⏸ Пауза...")
                pause_event.wait()
                log_cb("▶ Продолжение...")
            log_cb(f"⏳ {name}...")
            _focus()
            t0  = time.time()
            err = None
            try:
                func()
            except Exception as e:
                err = str(e)
            elapsed = time.time() - t0
            time.sleep(0.5)
            sample = self._sample_r7_resources(r7_procs)
            self._log_resources(sample, log_cb=log_cb)
            log_cb(f"   ✅ {name}: {elapsed:.2f} сек" + (f" (ошибка: {err})" if err else ""))
            results.append({
                "name": name, "time": elapsed, "error": err,
                "ram":            sample["ram_mb"]      if sample else None,
                "cpu":            sample["cpu_raw_pct"]  if sample else None,
                "cpu_normalized": sample["cpu_norm_pct"] if sample else None,
                "threads":        sample["threads"]      if sample else None,
                "uptime_sec":     sample["uptime_sec"]    if sample else None,
            })
```

- [ ] **Step 4: Extend the stats block**

Find (currently `r7_Testovarka.py:2402-2407`):

```python
        # ── Статистика ────────────────────────────────────────────────────────
        ram_vals = [r["ram"] for r in results if r.get("ram") is not None]
        cpu_vals = [r["cpu"] for r in results if r.get("cpu") is not None]
        peak_ram = max(ram_vals) if ram_vals else None
        avg_ram  = round(sum(ram_vals) / len(ram_vals), 1) if ram_vals else None
        peak_cpu = max(cpu_vals) if cpu_vals else None
```

Replace with:

```python
        # ── Статистика ────────────────────────────────────────────────────────
        ram_vals      = [r["ram"] for r in results if r.get("ram") is not None]
        cpu_vals      = [r["cpu"] for r in results if r.get("cpu") is not None]
        cpu_norm_vals = [r["cpu_normalized"] for r in results if r.get("cpu_normalized") is not None]
        peak_ram = max(ram_vals) if ram_vals else None
        avg_ram  = round(sum(ram_vals) / len(ram_vals), 1) if ram_vals else None
        peak_cpu = max(cpu_vals) if cpu_vals else None
        peak_cpu_norm = max(cpu_norm_vals) if cpu_norm_vals else None
        avg_cpu_norm  = round(sum(cpu_norm_vals) / len(cpu_norm_vals), 1) if cpu_norm_vals else None
```

- [ ] **Step 5: Extend the JSON dump and the function's return dict**

Find (currently `r7_Testovarka.py:2421-2453`):

```python
        # ── Сохранение JSON ───────────────────────────────────────────────────
        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = self.reports_folder / f"performance_full_{ts_now}.json"
        sys_mem_gb = (round(psutil.virtual_memory().total / (1024 ** 3), 1)
                      if PSUTIL_OK else None)
        try:
            with open(json_path, "w", encoding="utf-8") as jf:
                json.dump({
                    "timestamp": ts_now,
                    "version":   version_label,
                    "test_file": str(test_file),
                    "system": {"os": platform.platform(), "ram_total_gb": sys_mem_gb},
                    "summary": {
                        "peak_ram_mb": peak_ram, "avg_ram_mb": avg_ram,
                        "min_ram_mb":  min(ram_vals) if ram_vals else None,
                        "peak_cpu_pct": peak_cpu,
                    },
                    "results": results,
                }, jf, indent=2, ensure_ascii=False)
            log_cb(f"📄 JSON сохранён: {json_path.name}")
        except Exception as e:
            log_cb(f"⚠️ Ошибка сохранения JSON: {e}")

        vpr_r = next((r for r in results if r["name"] == "Функция ВПР (50K строк)"), None)
        return {
            "open_elapsed":    open_elapsed,
            "vlookup_elapsed": vpr_r["time"] if vpr_r else None,
            "peak_ram":        peak_ram,
            "avg_ram":         avg_ram,
            "peak_cpu":        peak_cpu,
            "results":         results,
            "json_path":       str(json_path),
        }
```

Replace with:

```python
        # ── Сохранение JSON ───────────────────────────────────────────────────
        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = self.reports_folder / f"performance_full_{ts_now}.json"
        sys_mem_gb = (round(psutil.virtual_memory().total / (1024 ** 3), 1)
                      if PSUTIL_OK else None)
        try:
            with open(json_path, "w", encoding="utf-8") as jf:
                json.dump({
                    "timestamp": ts_now,
                    "version":   version_label,
                    "test_file": str(test_file),
                    "system": {
                        "os": platform.platform(),
                        "ram_total_gb": sys_mem_gb,
                        "cpu_model": platform.processor() or None,
                    },
                    "summary": {
                        "peak_ram_mb": peak_ram, "avg_ram_mb": avg_ram,
                        "min_ram_mb":  min(ram_vals) if ram_vals else None,
                        "peak_cpu_pct": peak_cpu,
                        "peak_cpu_normalized_pct": peak_cpu_norm,
                        "avg_cpu_normalized_pct": avg_cpu_norm,
                    },
                    "results": results,
                }, jf, indent=2, ensure_ascii=False)
            log_cb(f"📄 JSON сохранён: {json_path.name}")
        except Exception as e:
            log_cb(f"⚠️ Ошибка сохранения JSON: {e}")

        vpr_r = next((r for r in results if r["name"] == "Функция ВПР (50K строк)"), None)
        return {
            "open_elapsed":     open_elapsed,
            "vlookup_elapsed":  vpr_r["time"] if vpr_r else None,
            "peak_ram":         peak_ram,
            "avg_ram":          avg_ram,
            "peak_cpu":         peak_cpu,
            "peak_cpu_normalized": peak_cpu_norm,
            "results":          results,
            "json_path":        str(json_path),
        }
```

- [ ] **Step 6: Syntax check**

Run:
```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
```
Expected: `OK`.

- [ ] **Step 7: Static check — closure removed, new fields present**

Run:
```bash
.venv/Scripts/python.exe -c "
import pathlib
src = pathlib.Path('r7_Testovarka.py').read_text(encoding='utf-8')
assert 'def _sample():' not in src, 'old batch closure must be removed'
assert 'ram0, cpu0 = _sample()' not in src
assert 'peak_cpu_normalized' in src
print('PASS')
"
```
Expected: `PASS`.

- [ ] **Step 8: Commit**

```bash
git add r7_Testovarka.py
git commit -m "Использовать _sample_r7_resources/_log_resources в _batch_run_single_version"
```

---

### Task 6: Normalized CPU in the HTML report

**Files:**
- Modify: `r7_Testovarka.py:1017-1157` (method `_generate_html_report`)

**Interfaces:**
- Consumes: `results` list already enriched by Task 4 (each row may have `cpu_normalized`). No signature change — `_generate_html_report` keeps the exact same parameter list, so the call site in `_spreadsheet_worker` (`r7_Testovarka.py:951-954`) needs **no edit**.
- Produces: an HTML report with a new "Пик CPU (норм.)" stat card, a "CPU норм. (%)" table column, a second line series on the CPU chart, and an explanatory note. Old reports generated by old code (without `cpu_normalized` keys) still render correctly — the new column/card just show `—`/nothing via the same `.get()` pattern already used for `error`/`ram`/`cpu`.

- [ ] **Step 1: Confirm the note text isn't present yet**

Run:
```bash
.venv/Scripts/python.exe -c "
import pathlib
src = pathlib.Path('r7_Testovarka.py').read_text(encoding='utf-8')
print('относительно всех ядер' in src)
"
```
Expected: `False`.

- [ ] **Step 2: Add normalized-CPU derivation + `cpu_count_display` near the top of the method**

Find (currently right after the docstring, `r7_Testovarka.py:1020-1030`):

```python
        ts_display = datetime.now().strftime("%d.%m.%Y %H:%M")
        os_info = platform.platform()
        try:
            cpu_info = platform.processor() or "N/A"
        except Exception:
            cpu_info = "N/A"
        sys_mem_gb = (round(psutil.virtual_memory().total / (1024 ** 3), 1)
                      if PSUTIL_OK else "N/A")
        file_size_mb = (round(test_file.stat().st_size / (1024 * 1024), 2)
                        if test_file.exists() else "N/A")
```

Replace with:

```python
        ts_display = datetime.now().strftime("%d.%m.%Y %H:%M")
        os_info = platform.platform()
        try:
            cpu_info = platform.processor() or "N/A"
        except Exception:
            cpu_info = "N/A"
        sys_mem_gb = (round(psutil.virtual_memory().total / (1024 ** 3), 1)
                      if PSUTIL_OK else "N/A")
        file_size_mb = (round(test_file.stat().st_size / (1024 * 1024), 2)
                        if test_file.exists() else "N/A")
        cpu_count_display = psutil.cpu_count() if PSUTIL_OK else "N/A"

        # CPU-показатели psutil не нормализованы по числу ядер (могут быть >100%);
        # норм. значение делит их на cpu_count(), получая шкалу 0–100% как в Task Manager.
        cpu_norm_vals = [r.get("cpu_normalized") for r in results if r.get("cpu_normalized") is not None]
        peak_cpu_norm = max(cpu_norm_vals) if cpu_norm_vals else None
```

- [ ] **Step 3: Add the normalized series to chart data**

Find (currently `r7_Testovarka.py:1032-1036`):

```python
        # Chart data
        labels_json = json.dumps([r["name"] for r in results], ensure_ascii=False)
        times_json  = json.dumps([round(r["time"], 3) for r in results])
        ram_json    = json.dumps([r.get("ram") for r in results])
        cpu_json    = json.dumps([r.get("cpu") for r in results])
```

Replace with:

```python
        # Chart data
        labels_json   = json.dumps([r["name"] for r in results], ensure_ascii=False)
        times_json    = json.dumps([round(r["time"], 3) for r in results])
        ram_json      = json.dumps([r.get("ram") for r in results])
        cpu_json      = json.dumps([r.get("cpu") for r in results])
        cpu_norm_json = json.dumps([r.get("cpu_normalized") for r in results])
```

- [ ] **Step 4: Add the normalized stat card**

Find (currently `r7_Testovarka.py:1046-1050`):

```python
        cpu_warn = peak_cpu is not None and peak_cpu > 80
        cards_html = (stat_card("Пик RAM", peak_ram, " МБ") +
                      stat_card("Средн. RAM", avg_ram, " МБ") +
                      stat_card("Мин. RAM", min_ram, " МБ") +
                      stat_card("Пик CPU", peak_cpu, "%", warn=cpu_warn))
```

Replace with:

```python
        # Порог предупреждения считаем по нормализованному CPU — «сырое» значение
        # может законно превышать 100% на многоядерной системе и не годится для warn.
        cpu_warn = peak_cpu_norm is not None and peak_cpu_norm > 80
        cards_html = (stat_card("Пик RAM", peak_ram, " МБ") +
                      stat_card("Средн. RAM", avg_ram, " МБ") +
                      stat_card("Мин. RAM", min_ram, " МБ") +
                      stat_card("Пик CPU (сырое)", peak_cpu, "%") +
                      stat_card("Пик CPU (норм.)", peak_cpu_norm, "%", warn=cpu_warn))
```

- [ ] **Step 5: Add the table column**

Find (currently `r7_Testovarka.py:1052-1064`):

```python
        # Results table rows
        rows_html = ""
        for r in results:
            err_class = "row-error" if r.get("error") else ""
            ram_cell = f"{r['ram']:.1f}" if r.get("ram") is not None else "—"
            cpu_cell = f"{r['cpu']:.1f}" if r.get("cpu") is not None else "—"
            err_cell = r.get("error") or ""
            rows_html += (f"<tr class='{err_class}'>"
                          f"<td>{r['name']}</td>"
                          f"<td>{r['time']:.3f}</td>"
                          f"<td>{ram_cell}</td>"
                          f"<td>{cpu_cell}</td>"
                          f"<td>{err_cell}</td></tr>\n")
```

Replace with:

```python
        # Results table rows
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

- [ ] **Step 6: Add `.cpu-note` CSS**

Find (currently `r7_Testovarka.py:1082`):

```python
  .card-value{{font-size:1.6em;font-weight:bold;margin-top:4px}}
```

Replace with:

```python
  .card-value{{font-size:1.6em;font-weight:bold;margin-top:4px}}
  .cpu-note{{font-size:.85em;color:#555;margin:-6px 0 20px;padding:8px 12px;
    background:#eef3fb;border-radius:6px}}
```

- [ ] **Step 7: Add the explanatory note and the table header column**

Find (currently `r7_Testovarka.py:1119`):

```python
<div class="cards">{cards_html}</div>
```

Replace with:

```python
<div class="cards">{cards_html}</div>
<p class="cpu-note">ℹ️ CPU показан относительно всех ядер (0–100%). «Сырое» значение — как
в диспетчере задач Windows на вкладке «Подробности» (может превышать 100% на многоядерных
системах), «норм.» — то же значение, делённое на количество логических ядер
({cpu_count_display}).</p>
```

Find (currently `r7_Testovarka.py:1128`):

```python
<thead><tr><th>Операция</th><th>Время (сек)</th><th>RAM (МБ)</th><th>CPU (%)</th><th>Ошибка</th></tr></thead>
```

Replace with:

```python
<thead><tr><th>Операция</th><th>Время (сек)</th><th>RAM (МБ)</th><th>CPU (%)</th><th>CPU норм. (%)</th><th>Ошибка</th></tr></thead>
```

- [ ] **Step 8: Update the chart script block**

Find (currently `r7_Testovarka.py:1132-1154`):

```python
<script>
const labels = {labels_json};
const times  = {times_json};
const rams   = {ram_json};
const cpus   = {cpu_json};
const defOpts = (title) => ({{
  responsive: true,
  plugins: {{legend:{{display:false}}, title:{{display:true, text:title}}}},
  scales: {{y:{{beginAtZero:true}}}}
}});
new Chart(document.getElementById('timeChart'), {{
  type:'bar', data:{{labels, datasets:[{{label:'сек',data:times,backgroundColor:'#3498db'}}]}},
  options: defOpts('Время выполнения (сек)')
}});
new Chart(document.getElementById('ramChart'), {{
  type:'line', data:{{labels, datasets:[{{label:'МБ',data:rams,borderColor:'#27ae60',backgroundColor:'rgba(39,174,96,.15)',fill:true,tension:.3}}]}},
  options: defOpts('Потребление RAM (МБ)')
}});
new Chart(document.getElementById('cpuChart'), {{
  type:'line', data:{{labels, datasets:[{{label:'%',data:cpus,borderColor:'#e67e22',backgroundColor:'rgba(230,126,34,.15)',fill:true,tension:.3}}]}},
  options: defOpts('Нагрузка на CPU (%)')
}});
</script>
```

Replace with:

```python
<script>
const labels   = {labels_json};
const times    = {times_json};
const rams     = {ram_json};
const cpus     = {cpu_json};
const cpusNorm = {cpu_norm_json};
const defOpts = (title) => ({{
  responsive: true,
  plugins: {{legend:{{display:false}}, title:{{display:true, text:title}}}},
  scales: {{y:{{beginAtZero:true}}}}
}});
new Chart(document.getElementById('timeChart'), {{
  type:'bar', data:{{labels, datasets:[{{label:'сек',data:times,backgroundColor:'#3498db'}}]}},
  options: defOpts('Время выполнения (сек)')
}});
new Chart(document.getElementById('ramChart'), {{
  type:'line', data:{{labels, datasets:[{{label:'МБ',data:rams,borderColor:'#27ae60',backgroundColor:'rgba(39,174,96,.15)',fill:true,tension:.3}}]}},
  options: defOpts('Потребление RAM (МБ)')
}});
new Chart(document.getElementById('cpuChart'), {{
  type:'line',
  data:{{labels, datasets:[
    {{label:'CPU сырое (%)', data:cpus, borderColor:'#e67e22', backgroundColor:'rgba(230,126,34,.12)', fill:false, tension:.3}},
    {{label:'CPU норм. (%)', data:cpusNorm, borderColor:'#8e44ad', backgroundColor:'rgba(142,68,173,.15)', fill:true, tension:.3}}
  ]}},
  options: {{
    responsive: true,
    plugins: {{legend:{{display:true}}, title:{{display:true, text:'Нагрузка на CPU (%)'}}}},
    scales: {{y:{{beginAtZero:true}}}}
  }}
}});
</script>
```

- [ ] **Step 9: Syntax check**

Run:
```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo OK
```
Expected: `OK`.

- [ ] **Step 10: Render a real report offline and inspect it (no GUI, no live R7 process)**

Run:
```bash
.venv/Scripts/python.exe -c "
import types, sys
sys.modules.setdefault('pyautogui', types.ModuleType('pyautogui'))
from pathlib import Path
import r7_Testovarka as m

fake_self = types.SimpleNamespace()
results = [
    {'name': 'Открытие файла', 'time': 4.2, 'error': None, 'ram': 512.3, 'cpu': 340.0, 'cpu_normalized': 21.3, 'threads': 34, 'uptime_sec': 6.0},
    {'name': 'Выделение всех ячеек (Ctrl+A)', 'time': 0.9, 'error': None, 'ram': 530.1, 'cpu': 1280.0, 'cpu_normalized': 80.0, 'threads': 36, 'uptime_sec': 7.0},
    {'name': 'Функция ВПР (50K строк)', 'time': 12.4, 'error': 'timeout', 'ram': None, 'cpu': None, 'cpu_normalized': None, 'threads': None, 'uptime_sec': None},
]
html = m.R7Testovarka._generate_html_report(
    fake_self, results, Path('test_50k.xlsx'), 4.2, 'R7-Office (v2026.2.2.2864)',
    [512.3, 530.1], [340.0, 1280.0], 530.1, 521.2, 512.3, 1280.0,
)
assert 'относительно всех ядер' in html
assert 'CPU норм. (%)' in html
assert 'Пик CPU (норм.)' in html
assert 'cpuChart' in html
out = Path('_verify_report.html')
out.write_text(html, encoding='utf-8')
print('PASS, wrote', out, len(html), 'bytes')
"
```
Expected: `PASS, wrote _verify_report.html <N> bytes`. Open the file in a browser to visually confirm the new card/column/note/chart legend, then delete it:
```bash
rm -f _verify_report.html
```

- [ ] **Step 11: Commit**

```bash
git add r7_Testovarka.py
git commit -m "Показывать нормализованный CPU в HTML-отчёте (карточка, колонка, график, пояснение)"
```

---

### Task 7: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (table "Тестирование производительности")

**Interfaces:** none (documentation only).

- [ ] **Step 1: Confirm the new method names aren't documented yet**

Run:
```bash
.venv/Scripts/python.exe -c "
import pathlib
src = pathlib.Path('CLAUDE.md').read_text(encoding='utf-8')
print('_sample_r7_resources' in src, '_log_resources' in src)
"
```
Expected: `False False`.

- [ ] **Step 2: Add three rows to the "Тестирование производительности" table**

The current table (in `CLAUDE.md`) is:

```markdown
### Тестирование производительности

| Метод | Описание |
|-------|----------|
| `run_full_benchmark` | Запуск полного теста (13 операций) |
| `run_quick_benchmark` | Быстрый тест (запуск + RAM/CPU) |
| `_spreadsheet_worker` | Основной рабочий поток стресс-теста |
| `_bench_launch` | Замер времени запуска Р7-Офис |
| `_bench_text_input` | Тест ввода текста (10000 символов) |
| `_bench_memory` | Замер потребления RAM |
| `_bench_cpu` | Замер загрузки CPU |
| `_bench_close` | Закрытие Р7-Офис |
```

Append these rows immediately after the existing table (leave the existing rows untouched — several of them, e.g. `_bench_launch`, don't currently exist in the code and are out of scope for this change; only add the new rows):

```markdown
| `_get_r7_processes` | Поиск процессов Р7-Офис по psutil (editors_helper, desktopeditors, r7, x2t — конвертер документов); кэширует PID |
| `_sample_r7_resources(procs)` | Снимает RAM (МБ), CPU (сырое и нормализованное делением на `psutil.cpu_count()`), число потоков и аптайм по списку процессов; общий метод для `_spreadsheet_worker` и `_batch_run_single_version` |
| `_log_resources(sample, log_cb=None)` | Форматированный вывод замера в лог с цветовым индикатором нагрузки CPU: 🟢 <50% (норм.), 🟡 50–80%, 🔴 >80% |
```

- [ ] **Step 3: Verify the edit**

Run:
```bash
.venv/Scripts/python.exe -c "
import pathlib
src = pathlib.Path('CLAUDE.md').read_text(encoding='utf-8')
assert '_sample_r7_resources' in src
assert '_log_resources' in src
assert '_get_r7_processes' in src
print('PASS')
"
```
Expected: `PASS`.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "Документировать _get_r7_processes/_sample_r7_resources/_log_resources в CLAUDE.md"
```

---

### Task 8: Final review pass

**Files:** none new — reviews the cumulative diff from Tasks 1–7.

- [ ] **Step 1: Full-file syntax + import sanity check**

Run:
```bash
.venv/Scripts/python.exe -m py_compile r7_Testovarka.py && echo COMPILE_OK
.venv/Scripts/python.exe -c "import r7_Testovarka; print('IMPORT_OK')"
```
Expected: `COMPILE_OK` then `IMPORT_OK` (import must succeed with no unhandled exception — all third-party libs are optional at import time per the existing try/except pattern).

- [ ] **Step 2: Re-run every verification snippet from Tasks 1–6 in one pass**

Run each of: Task 1 Step 4, Task 2 Step 4, Task 3 Step 4, Task 4 Step 7, Task 5 Step 7, Task 6 Step 10. All must print their `PASS`/`OK` markers with no assertion errors.

- [ ] **Step 3: Run `/code-review` on the branch diff**

Invoke the `code-review` skill against the working tree changes (`r7_Testovarka.py`, `CLAUDE.md`). Address any CONFIRMED findings before proceeding; use judgment (per `superpowers:receiving-code-review`) on PLAUSIBLE ones.

- [ ] **Step 4: Run `code-simplifier` on the touched regions**

Dispatch the `code-simplifier` agent scoped to the diff introduced by Tasks 1–6 (the two `measure()` functions, the two JSON-writing blocks, `_generate_html_report`). Since this plan already de-duplicated the two resource-sampling closures into `_sample_r7_resources`, expect the agent to find little else — but let it check for any remaining near-duplication between the `_spreadsheet_worker` and `_batch_run_single_version` `measure()` bodies now that both call the same helpers.

- [ ] **Step 5: Manual smoke test (requires Windows + admin + Р7-Офис installed — cannot be automated here)**

Document for the user to run once: launch `r7_Testovarka.py` as administrator, go to the "Производительность" tab, run a subset of tests against a small test file, and confirm:
- The log shows `📊 RAM: ... CPU: ...% (норм. ...%) 🟢/🟡/🔴  Потоки: ...  Аптайм: ... сек` lines.
- If x2t ever spawns during the run, a `🔧 Обнаружен процесс конвертации x2t: PID=..., имя=...` line appears.
- The generated HTML report (`Reports/Performance_Report.html`) shows the "Пик CPU (норм.)" card, the note, the extra table column, and a two-line CPU chart with a visible legend.
- The generated `performance_full_*.json` has `system.cpu_model`, `summary.peak_cpu_normalized_pct`, and per-result `cpu_normalized`/`threads`/`uptime_sec`.

- [ ] **Step 6: Final commit (only if Steps 3–4 produced additional fixes)**

```bash
git add -A
git commit -m "Правки по code review и code-simplifier для замеров ресурсов"
```

---

## Self-Review

**Spec coverage:**
1. x2t tracking → Task 1 (search list + detection log + inclusion via `_sample_r7_resources` since x2t procs are just members of `r7_procs`). ✅
2. Flexible process search (name().lower(), try/except, PID caching) → already present in `_get_r7_processes`, preserved and extended in Task 1; `_sample_r7_resources` (Task 2) wraps every psutil call in try/except. ✅
3. CPU normalization (`/ psutil.cpu_count()`, store both values, HTML note) → Task 2 (`cpu_norm_pct`), Task 4/5 (`cpu_normalized` field + summary fields), Task 6 (card/column/chart/note text). ✅
4. Additional metrics (`num_threads`, `create_time`→uptime, system info: OS/CPU model/RAM) → Task 2 (`threads`, `uptime_sec`), Task 4/5 (`system.cpu_model`; `os`/`ram_total_gb` already existed). ✅
5. `_log_resources()` with 🟢/🟡/🔴 → Task 3, wired in Task 4/5. ✅
6. Backward compatibility, defensive coding, Russian comments, CLAUDE.md update → Global Constraints + Task 7 + verified throughout (additive-only fields, try/except everywhere, all new logic has short Russian docstrings/comments explaining the *why*). ✅

**Placeholder scan:** no TBD/"add error handling"/"similar to Task N" — every step has full code or an exact runnable command with an expected output.

**Type consistency:** `_sample_r7_resources` returns `dict | None` with fixed keys `ram_mb/cpu_raw_pct/cpu_norm_pct/threads/uptime_sec` — Tasks 4, 5, and 6 all consume exactly those key names. `_log_resources(sample, log_cb=None)` signature matches both call sites (default in Task 4, explicit `log_cb=log_cb` in Task 5).