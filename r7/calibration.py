"""Калибровка стенда (этап 2 плана, п. 2): фиксированная нагрузка на CPU и
диск до запуска Р7, индекс скорости в отчёте.

Отделяет «стенд медленный» от «Р7 замедлился»: один и тот же чистый
Python-цикл на двух ПК даёт разное время, и по нему видно, насколько
машины сопоставимы, не зная их моделей. Запускается один раз на прогон из
_capture_environment — до _wait_system_quiet и до запуска Р7, поэтому в
замеры не попадает. Обе функции никогда не бросают: любой сбой — None.
"""
from __future__ import annotations

import os
import statistics
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CPU_ITERATIONS = 2_000_000
CPU_REPEATS = 3
CPU_TIMEOUT_SEC = 60.0

DISK_SIZE_MB = 64
DISK_TIMEOUT_SEC = 120.0
_DISK_CHUNK = 1 << 20


def _cpu_workload(iterations: int) -> float:
    """Целочисленный LCG плюс деление с плавающей точкой: воспроизводимо,
    без аллокаций, не оптимизируется интерпретатором."""
    x = 12345
    acc = 0.0
    for i in range(iterations):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        acc += (x & 0xFFFF) * 0.5 / (i + 1)
    return acc


def cpu_index(iterations: int = CPU_ITERATIONS, repeats: int = CPU_REPEATS,
              timeout_sec: float = CPU_TIMEOUT_SEC) -> float | None:
    """Время фиксированной нагрузки, мс: медиана repeats повторов.

    Returns:
        float | None: None — нагрузка не уложилась в timeout_sec или упала.
    """
    try:
        deadline = time.perf_counter() + timeout_sec
        times: list[float] = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            _cpu_workload(iterations)
            times.append((time.perf_counter() - t0) * 1000.0)
            if time.perf_counter() > deadline:
                return None
        return round(statistics.median(times), 1) if times else None
    except Exception:
        return None


def disk_index(folder: str | os.PathLike[str], size_mb: int = DISK_SIZE_MB,
               timeout_sec: float = DISK_TIMEOUT_SEC) -> float | None:
    """Скорость диска папки отчётов, МБ/с: запись size_mb случайных байт с
    fsync, чтение обратно, удаление. Одно число — суммарный объём (запись +
    чтение) на суммарное время; чтение может идти из кэша ОС, поэтому это
    индекс для сравнения стендов, а не паспорт диска.

    Returns:
        float | None: None — папки нет, нет прав, не уложились в timeout_sec.
    """
    path: Path | None = None
    try:
        folder_path = Path(folder)
        folder_path.mkdir(parents=True, exist_ok=True)
        path = folder_path / f".r7_calibration_{os.getpid()}.tmp"
        chunk = os.urandom(_DISK_CHUNK)     # случайные байты — сжатие диска не поможет
        deadline = time.perf_counter() + timeout_sec
        t0 = time.perf_counter()
        with open(path, "wb") as f:
            for _ in range(size_mb):
                f.write(chunk)
                if time.perf_counter() > deadline:
                    return None
            f.flush()
            os.fsync(f.fileno())
        t_write = time.perf_counter() - t0
        t0 = time.perf_counter()
        with open(path, "rb") as f:
            while f.read(_DISK_CHUNK):
                if time.perf_counter() > deadline:
                    return None
        t_read = time.perf_counter() - t0
        total = t_write + t_read
        return round(size_mb * 2 / total, 1) if total > 0 else None
    except Exception:
        return None
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:  # файл занят антивирусом — останется 64 МБ мусора, не падаем
                pass


def calibrate(folder: str | os.PathLike[str]) -> dict[str, float | None]:
    """Оба индекса одним словарём для `environment.calibration`."""
    return {"cpu_ms": cpu_index(), "disk_mb_s": disk_index(folder)}


def format_calibration(cal: Mapping[str, Any] | None) -> str | None:
    """«CPU 312 мс, диск 410 МБ/с» для журнала и блока «Стенд»; None — нет данных."""
    cal = cal or {}
    parts: list[str] = []
    if cal.get("cpu_ms") is not None:
        parts.append(f"CPU {cal['cpu_ms']:.0f} мс")
    if cal.get("disk_mb_s") is not None:
        parts.append(f"диск {cal['disk_mb_s']:.0f} МБ/с")
    return ", ".join(parts) or None
