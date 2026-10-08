"""Проверка дистрибутивов по хэшам — без окна (до 10, шаги 3–4).

Эталоны — Distributives/hashes.json: {"имя файла": {"md5": …, "sha256": …}}.
Окно «Проверка хэшей» только рисует таблицу и спрашивает пользователя;
расчёт, сверка, чтение и запись эталонов — здесь, с тестами.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

CHUNK = 1024 * 1024   # 1 МБ: дистрибутивы весят сотни МБ, мелкий чанк — лишние системные вызовы
MD5_LEN, SHA256_LEN = 32, 64
ERROR_MARKS = ("ОШИБКА", "—")

OK, NO_REF, FAIL = "ok", "no_ref", "fail"
STATUS_TEXT = {OK: "✅ Совпадает", NO_REF: "⚠️ Нет эталона", FAIL: "❌ Не совпадает"}

StrPath = str | os.PathLike[str]


def file_hashes(path: StrPath, chunk: int = CHUNK) -> tuple[str, str, float]:
    """(md5, sha256, размер в МБ) файла за один проход."""
    md5h, sha256h = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            md5h.update(block)
            sha256h.update(block)
    return md5h.hexdigest(), sha256h.hexdigest(), Path(path).stat().st_size / (1024 * 1024)


def status_against(row: Mapping[str, Any],
                   reference: Mapping[str, Any] | None) -> tuple[str, str]:
    """(текст статуса, метка) строки результата относительно эталонов.
    Строка с ошибкой чтения сохраняет свой статус при любых эталонах."""
    if row["md5"] in ERROR_MARKS:
        return row["status"], row["tag"]
    entry = (reference or {}).get(row["name"]) or {}
    if not entry:
        return STATUS_TEXT[NO_REF], NO_REF
    if (str(entry.get("md5", "")).lower() == row["md5"].lower()
            and str(entry.get("sha256", "")).lower() == row["sha256"].lower()):
        return STATUS_TEXT[OK], OK
    return STATUS_TEXT[FAIL], FAIL


def hash_row(path: StrPath, reference: Mapping[str, Any] | None) -> dict[str, str]:
    """Строка результата для одного файла; ошибка чтения — строка с пометкой."""
    file_path = Path(path)
    try:
        md5_val, sha_val, size_mb = file_hashes(file_path)
    except Exception as e:
        return {"name": file_path.name, "size": "—", "md5": "ОШИБКА", "sha256": str(e),
                "status": "❌ Ошибка чтения", "tag": FAIL}
    row = {"name": file_path.name, "size": f"{size_mb:.2f}", "md5": md5_val, "sha256": sha_val}
    row["status"], row["tag"] = status_against(dict(row, status="", tag=""), reference)
    return row


def summary(rows: Sequence[Mapping[str, Any]]) -> tuple[int, int, int]:
    """(всего, совпадают, не совпадают)."""
    return (len(rows), sum(1 for r in rows if r["tag"] == OK),
            sum(1 for r in rows if r["tag"] == FAIL))


def load_reference(path: StrPath) -> tuple[dict[str, Any], str | None]:
    """(эталоны, ошибка). Нет файла — ({}, None). Файл не читается или не
    словарь — ({}, текст ошибки): сохранять поверх такого файла нельзя, иначе
    все прочие эталоны пропадут."""
    ref_path = Path(path)
    if not ref_path.exists():
        return {}, None
    try:
        data = json.loads(ref_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {}, f"{ref_path.name} не читается: {type(e).__name__}: {e}"
    if not isinstance(data, dict):
        return {}, f"{ref_path.name}: ожидался словарь «файл → хэши»"
    return data, None


def save_reference(path: StrPath, reference: Mapping[str, Any]) -> None:
    ref_path = Path(path)
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(json.dumps(reference, indent=2, ensure_ascii=False), encoding="utf-8")


def set_reference(path: StrPath, name: str, md5_val: str, sha256_val: str) -> str | None:
    """Записывает эталон файла. Возвращает ошибку (текст) или None; при
    нечитаемом hashes.json ничего не пишет."""
    ref, err = load_reference(path)
    if err:
        return err + " — эталон не сохранён, чтобы не затереть остальные"
    ref[name] = {"md5": md5_val, "sha256": sha256_val}
    save_reference(path, ref)
    return None


def delete_reference(path: StrPath, name: str) -> tuple[bool, str | None]:
    """Удаляет эталон файла. (удалён ли, ошибка)."""
    ref, err = load_reference(path)
    if err:
        return False, err + " — ничего не удалено"
    if name not in ref:
        return False, None
    del ref[name]
    save_reference(path, ref)
    return True, None


def validate_hex(value: str | None, expected_len: int,
                 label: str) -> tuple[str | None, str | None]:
    """(очищенная строка, ошибка) для введённого вручную хэша."""
    s = (value or "").strip().lower()
    if len(s) != expected_len:
        return None, f"{label}: длина должна быть {expected_len} символов (введено {len(s)})"
    if not all(c in "0123456789abcdef" for c in s):
        return None, f"{label}: допустимы только символы 0–9 и a–f"
    return s, None


def write_csv(path: StrPath, rows: Iterable[Mapping[str, Any]]) -> None:
    """Отчёт проверки в CSV (UTF-8 с BOM — Excel открывает без кракозябр)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Имя файла", "Размер (МБ)", "MD5", "SHA256", "Статус"])
        for r in rows:
            w.writerow([r["name"], r["size"], r["md5"], r["sha256"], r["status"]])
