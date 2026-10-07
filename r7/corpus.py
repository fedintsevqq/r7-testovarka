"""Корпус реальных файлов: состав, манифест, отчёт (docs/corpus.md).

Папка `Corpus/` — обезличенные файлы клиентов. По каждому файлу прогон
«открытие, пересчёт, экспорт» (r7/corpus_runner.py), итог — матрица
«файл × шаг» и сравнение двух и более прогонов — «файл × версия»
(r7/corpus_report.py).

Здесь только чистые функции без Tk и без Р7: поиск файлов, разбор
необязательного манифеста `Corpus/corpus.toml`, план шагов на файл, сборка
и обезличивание отчёта. Файлы корпуса инструмент никуда не отправляет:
отчёт ссылается на них по id (начало sha256 содержимого) и имени, а с
--hide-names — только по id.

Формат манифеста (всё необязательно, битый файл — предупреждение, прогон
идёт по умолчаниям):

    [defaults]
    steps = ["open", "recalc", "export"]
    formats = ["pdf"]
    open_timeout_sec = 120
    open_runs = 5
    recalc_runs = 6
    export_runs = 6

    [files."отчёт-2025.xlsx"]          # путь от Corpus/ через «/» или имя файла
    steps = ["open", "export"]
    formats = ["pdf", "csv"]
    open_timeout_sec = 300
    notes = "тяжёлые сводные таблицы"
    skip = false
"""
from __future__ import annotations

import copy
import hashlib
import json
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from r7 import config
from r7.config import RUNS_MAX, RUNS_MIN

CORPUS_SUBDIR = "Corpus"
MANIFEST_NAME = "corpus.toml"
SUPPORTED_EXTS = (".xlsx", ".xls", ".ods", ".csv")

STEP_OPEN, STEP_RECALC, STEP_EXPORT = "open", "recalc", "export"
STEPS = (STEP_OPEN, STEP_RECALC, STEP_EXPORT)
STEP_TITLES = {STEP_OPEN: "Открытие", STEP_RECALC: "Пересчёт", STEP_EXPORT: "Экспорт"}
# Форматы «Сохранить как», для которых есть проверка файла (r7/x2t_files.py).
EXPORT_FORMATS = ("pdf", "ods", "csv", "xltx", "xlsx")

DEFAULT_STEPS = STEPS
DEFAULT_FORMATS = ("pdf",)
DEFAULT_OPEN_RUNS = 5          # открытия — независимые холодные старты, прогрев не отбрасывается
DEFAULT_RECALC_RUNS = 6        # 6 − прогрев = 5 годных: столько нужно compare_runs
DEFAULT_EXPORT_RUNS = 6
DEFAULT_OPEN_TIMEOUT_SEC = 120.0
OPEN_TIMEOUT_MIN_SEC, OPEN_TIMEOUT_MAX_SEC = 10.0, 1800.0

FILE_ID_LEN = 12
HASH_CHUNK = 1 << 20
REPORT_KIND = "corpus"
CORPUS_FORMAT = 1           # версия формата corpus_*.json (не схема замера)

# Имена операций в записях замера — как у вкладки: экспорт начинается с
# «Сохранение в», и детектор не ждёт от него точки в истории правок
# (NON_MUTATING_MARKERS); пересчёт её добавляет (живая проба 07.10.2026).
RECALC_OP_NAME = "Полный пересчёт (asc_calculate)"


def export_op_name(fmt: str) -> str:
    """Имя записи экспорта — то же, что у тестов вкладки (шум стенда общий)."""
    return f"Сохранение в {fmt.upper()} (конвертация x2t)"


class CorpusError(ValueError):
    """Корпус или его отчёт не читается; текст — по-русски, что сделать."""


@dataclass(frozen=True)
class Plan:
    """Что делать с каждым файлом, если манифест не сказал иное."""
    steps: tuple[str, ...] = DEFAULT_STEPS
    formats: tuple[str, ...] = DEFAULT_FORMATS
    open_runs: int = DEFAULT_OPEN_RUNS
    recalc_runs: int = DEFAULT_RECALC_RUNS
    export_runs: int = DEFAULT_EXPORT_RUNS
    open_timeout_sec: float = DEFAULT_OPEN_TIMEOUT_SEC

    def to_dict(self) -> dict[str, Any]:
        return {"steps": list(self.steps), "formats": list(self.formats),
                "open_runs": self.open_runs, "recalc_runs": self.recalc_runs,
                "export_runs": self.export_runs, "open_timeout_sec": self.open_timeout_sec}


@dataclass(frozen=True)
class CorpusFile:
    """Файл корпуса и его план."""
    path: Path
    rel: str                        # путь от папки корпуса, через «/»
    file_id: str                    # начало sha256 — устойчиво к переименованию
    sha256: str
    size_bytes: int
    plan: Plan
    notes: str | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def ext(self) -> str:
        return self.path.suffix.lower().lstrip(".")

    def step_keys(self) -> list[str]:
        return step_keys(self.plan)


@dataclass
class Manifest:
    """Разобранный corpus.toml: умолчания, записи файлов, ошибки разбора."""
    defaults: dict[str, Any] = field(default_factory=dict)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ── Шаги ─────────────────────────────────────────────────────────────────

def step_keys(plan: Plan) -> list[str]:
    """Ключи ячеек матрицы по порядку: open, recalc, export:pdf, ..."""
    keys: list[str] = []
    for step in STEPS:
        if step not in plan.steps:
            continue
        if step == STEP_EXPORT:
            keys.extend(f"{STEP_EXPORT}:{fmt}" for fmt in plan.formats)
        else:
            keys.append(step)
    return keys


def step_title(key: str) -> str:
    """«Открытие», «Пересчёт», «Экспорт PDF»."""
    if key.startswith(f"{STEP_EXPORT}:"):
        return f"{STEP_TITLES[STEP_EXPORT]} {key.split(':', 1)[1].upper()}"
    return STEP_TITLES.get(key, key)


def _split_list(value: str | Iterable[str]) -> list[str]:
    items = value.split(",") if isinstance(value, str) else list(value)
    return [str(x).strip().lower() for x in items if str(x).strip()]


def parse_steps(value: str | Iterable[str]) -> tuple[str, ...]:
    """Шаги из «open,recalc,export» или списка; порядок — всегда STEPS."""
    items = _split_list(value)
    unknown = [s for s in items if s not in STEPS]
    if unknown:
        raise CorpusError(f"неизвестные шаги: {', '.join(unknown)}; можно: {', '.join(STEPS)}")
    if not items:
        raise CorpusError(f"не задан ни один шаг; можно: {', '.join(STEPS)}")
    return tuple(s for s in STEPS if s in items)


def parse_formats(value: str | Iterable[str]) -> tuple[str, ...]:
    """Форматы экспорта из «pdf,csv» или списка, без повторов."""
    items = _split_list(value)
    unknown = [f for f in items if f not in EXPORT_FORMATS]
    if unknown:
        raise CorpusError(f"неизвестные форматы экспорта: {', '.join(unknown)}; "
                          f"можно: {', '.join(EXPORT_FORMATS)}")
    if not items:
        raise CorpusError(f"не задан ни один формат; можно: {', '.join(EXPORT_FORMATS)}")
    return tuple(dict.fromkeys(items))


def parse_runs(value: Any, what: str) -> int:
    """Повторы шага: целое, обрезанное до RUNS_MIN..RUNS_MAX."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise CorpusError(f"{what}: целое число {RUNS_MIN}..{RUNS_MAX}, а не {value!r}")
    return max(RUNS_MIN, min(RUNS_MAX, value))


def _timeout(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise CorpusError(f"{what}: число секунд больше нуля, а не {value!r}")
    return float(max(OPEN_TIMEOUT_MIN_SEC, min(OPEN_TIMEOUT_MAX_SEC, value)))


_PLAN_FIELDS = {
    "steps": parse_steps, "formats": parse_formats,
    "open_runs": lambda v: parse_runs(v, "open_runs"),
    "recalc_runs": lambda v: parse_runs(v, "recalc_runs"),
    "export_runs": lambda v: parse_runs(v, "export_runs"),
    "open_timeout_sec": lambda v: _timeout(v, "open_timeout_sec"),
}
_FILE_ONLY_FIELDS = ("notes", "skip")


def apply_overrides(plan: Plan, entry: Mapping[str, Any], label: str,
                    errors: list[str]) -> Plan:
    """План с полями из записи манифеста. Неверное поле — строка в errors,
    поле остаётся как было: одна опечатка не должна выкидывать файл."""
    changes: dict[str, Any] = {}
    for key, value in entry.items():
        if key in _FILE_ONLY_FIELDS:
            continue
        parse = _PLAN_FIELDS.get(key)
        if parse is None:
            errors.append(f"{label}: неизвестное поле «{key}» (можно: "
                          f"{', '.join([*_PLAN_FIELDS, *_FILE_ONLY_FIELDS])})")
            continue
        try:
            changes[key] = parse(value)
        except CorpusError as e:
            errors.append(f"{label}: {e}")
    return replace(plan, **changes) if changes else plan


# ── Манифест ─────────────────────────────────────────────────────────────

def parse_manifest(data: Any) -> Manifest:
    """Manifest из разобранного TOML. Чистая функция для тестов."""
    m = Manifest()
    if not isinstance(data, dict):
        m.errors.append(f"{MANIFEST_NAME}: ожидался TOML-документ с [defaults] и [files]")
        return m
    for key in data:
        if key not in ("defaults", "files"):
            m.errors.append(f"{MANIFEST_NAME}: неизвестный раздел [{key}] — пропущен")
    defaults = data.get("defaults", {})
    if isinstance(defaults, dict):
        m.defaults = dict(defaults)
    else:
        m.errors.append(f"{MANIFEST_NAME}: [defaults] должен быть разделом")
    files = data.get("files", {})
    if not isinstance(files, dict):
        m.errors.append(f"{MANIFEST_NAME}: [files] должен быть разделом [files.\"имя\"]")
        return m
    for name, entry in files.items():
        if isinstance(entry, dict):
            m.files[str(name).replace("\\", "/")] = dict(entry)
        else:
            m.errors.append(f"{MANIFEST_NAME}: запись [files.\"{name}\"] — не раздел, пропущена")
    return m


def load_manifest(folder: str | Path) -> Manifest:
    """Манифест папки корпуса. Нет файла — пустой; битый — пустой с ошибкой
    (прогон идёт по умолчаниям, ошибка — в журнал и отчёт)."""
    path = Path(folder) / MANIFEST_NAME
    if not path.is_file():
        return Manifest()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        return Manifest(errors=[f"{MANIFEST_NAME}: ошибка в TOML — {e}; манифест не учтён"])
    except OSError as e:
        return Manifest(errors=[f"{MANIFEST_NAME} не прочитан: {e}; манифест не учтён"])
    return parse_manifest(data)


# ── Поиск файлов ─────────────────────────────────────────────────────────

def corpus_dir() -> Path:
    """Папка корпуса по умолчанию — рядом с программой (читается при вызове,
    чтобы тесты могли подменить config.BASE_DIR)."""
    return config.BASE_DIR / CORPUS_SUBDIR


def is_lock_file(name: str) -> bool:
    """Файл блокировки офисного пакета: «~$книга.xlsx» (Excel, Р7) или
    «.~lock.книга.xlsx#» (LibreOffice)."""
    return name.startswith("~$") or (name.startswith(".~lock.") and name.endswith("#"))


def discover(folder: str | Path) -> list[Path]:
    """Файлы корпуса (с подпапками) с поддерживаемым расширением, без файлов
    блокировки и скрытых, по пути от папки."""
    root = Path(folder)
    if not root.is_dir():
        return []
    found = []
    for p in root.rglob("*"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith(".") and not is_lock_file(part) for part in rel_parts):
            continue
        if not p.is_file() or is_lock_file(p.name):
            continue
        if p.suffix.lower() in SUPPORTED_EXTS:
            found.append(p)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix().lower())


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def build_items(folder: str | Path, plan: Plan,
                manifest: Manifest | None = None) -> tuple[list[CorpusFile], list[str]]:
    """Файлы корпуса с планами: умолчания CLI ← [defaults] ← [files."…"].

    Returns:
        (файлы, предупреждения): ошибки манифеста, пропуски (skip = true),
        дубли по содержимому, нечитаемые файлы, записи манифеста без файла.
    """
    root = Path(folder)
    manifest = manifest or Manifest()
    warnings = list(manifest.errors)
    base = apply_overrides(plan, manifest.defaults, f"{MANIFEST_NAME} [defaults]", warnings)
    items: list[CorpusFile] = []
    seen: dict[str, str] = {}
    used_entries: set[str] = set()
    for path in discover(root):
        rel = path.relative_to(root).as_posix()
        key = rel if rel in manifest.files else (path.name if path.name in manifest.files else None)
        entry = manifest.files.get(key, {}) if key else {}
        if key:
            used_entries.add(key)
        if entry.get("skip") is True:
            warnings.append(f"{rel}: пропущен (skip = true в {MANIFEST_NAME})")
            continue
        try:
            sha = file_sha256(path)
            size = path.stat().st_size
        except OSError as e:
            warnings.append(f"{rel}: не прочитан ({e}) — пропущен")
            continue
        if sha in seen:
            warnings.append(f"{rel}: то же содержимое, что у {seen[sha]} — пропущен как дубль")
            continue
        seen[sha] = rel
        notes = entry.get("notes")
        items.append(CorpusFile(
            path=path, rel=rel, file_id=sha[:FILE_ID_LEN], sha256=sha, size_bytes=size,
            plan=apply_overrides(base, entry, f"{MANIFEST_NAME} [files.\"{key}\"]", warnings),
            notes=str(notes) if notes is not None else None))
    for key in manifest.files:
        if key not in used_entries:
            warnings.append(f"{MANIFEST_NAME}: файла «{key}» в корпусе нет — запись не использована")
    return items, warnings


# ── Отчёт ────────────────────────────────────────────────────────────────

def file_entry(item: CorpusFile, steps: Mapping[str, Any] | None = None,
               error: str | None = None, elapsed_sec: float | None = None) -> dict[str, Any]:
    """Запись файла в corpus_*.json: id, имя, план и записи шагов (как у
    _measure_op_repeated / _open_result)."""
    return {"id": item.file_id, "name": item.name, "rel": item.rel, "ext": item.ext,
            "sha256": item.sha256, "size_bytes": item.size_bytes,
            "plan": item.plan.to_dict(), "notes": item.notes,
            "steps": dict(steps or {}), "error": error,
            "elapsed_sec": None if elapsed_sec is None else round(elapsed_sec, 1)}


def build_report(files: Sequence[Mapping[str, Any]], plan: Plan, *, timestamp: str,
                 measure_schema: int, tool_version: str, version: str | None,
                 build: Mapping[str, Any] | None, system: Mapping[str, Any] | None,
                 corpus_dir: str | None, warnings: Sequence[str] = (),
                 stopped: bool = False) -> dict[str, Any]:
    """Содержимое corpus_<ts>.json. system — как у полного отчёта (там
    окружение и отпечаток стенда: r7.fingerprint.report_fingerprint)."""
    return {"kind": REPORT_KIND, "corpus_format": CORPUS_FORMAT,
            "timestamp": timestamp, "measure_schema": measure_schema,
            "tool_version": tool_version, "version": version,
            "build": dict(build) if build else None,
            "system": dict(system) if system else None,
            "corpus_dir": corpus_dir, "plan": plan.to_dict(),
            "warnings": list(warnings), "stopped": stopped, "hide_names": False,
            "files": [dict(f) for f in files]}


def hide_names(report: Mapping[str, Any]) -> dict[str, Any]:
    """Копия отчёта для передачи за пределы команды: имена файлов, пути и
    заметки заменены id (или убраны). Исходный отчёт не меняется."""
    out = copy.deepcopy(dict(report))
    repl: dict[str, str] = {}
    for f in out.get("files") or []:
        fid = str(f.get("id") or "[файл]")
        rel = str(f.get("rel") or "")
        for n in (f.get("name"), rel, Path(rel).name, Path(rel).stem):
            if n and len(str(n)) >= MIN_SCRUB_LEN:
                repl[str(n)] = fid
        f["name"] = f["rel"] = fid
        f["notes"] = None
    out["corpus_dir"] = None
    # Имена могли попасть в тексты: ошибки шагов, окна Р7, предупреждения.
    # Меняем только в них (не в именах операций и числах) и длинные первыми,
    # чтобы «книга.xlsx» не превратился в «<id>.xlsx».
    names = sorted(repl, key=len, reverse=True)
    out["warnings"] = _scrub(out.get("warnings") or [], names, repl)
    for f in out.get("files") or []:
        f["error"] = _scrub(f.get("error"), names, repl)
        for rec in (f.get("steps") or {}).values():
            if isinstance(rec, dict):
                for key in SCRUB_STEP_FIELDS:
                    if key in rec:
                        rec[key] = _scrub(rec[key], names, repl)
    out["hide_names"] = True
    return out


MIN_SCRUB_LEN = 4   # короче — слишком общая подстрока («a.csv» → stem «a»)
SCRUB_STEP_FIELDS = ("error", "r7_alerts", "disk_note")


def _scrub(obj: Any, names: Sequence[str], repl: Mapping[str, str]) -> Any:
    """Замена имён на id во всех строках вложенной структуры (ключи не трогаем)."""
    if isinstance(obj, dict):
        return {k: _scrub(v, names, repl) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v, names, repl) for v in obj]
    if isinstance(obj, str):
        for n in names:
            if n in obj:
                obj = obj.replace(n, repl[n])
        return obj
    return obj


def load_report(path: str | Path) -> dict[str, Any]:
    """corpus_*.json; CorpusError с причиной, если не читается."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CorpusError(f"отчёт не найден: {path}") from None
    except (OSError, ValueError) as e:
        raise CorpusError(f"отчёт {path.name} не прочитан: {e}") from None
    if not isinstance(data, dict) or data.get("kind") != REPORT_KIND \
            or not isinstance(data.get("files"), list):
        raise CorpusError(f"{path.name}: это не отчёт корпуса (corpus_*.json)")
    return data
