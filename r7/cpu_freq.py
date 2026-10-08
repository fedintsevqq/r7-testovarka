"""Частота CPU во время замера: % номинальной, для пометки троттлинга.

psutil.cpu_freq() на Windows отдаёт CurrentMhz из CallNtPowerInformation, а
он на большинстве машин застывает на номинале (стенд 07.10.2026:
current=max=4700 при любой нагрузке) — троттлинг так не увидеть. Диспетчер
задач берёт частоту из счётчика PDH «\\Processor Information(_Total)\\%
Processor Performance» — отношения APERF/MPERF, то есть реальной частоты к
номинальной за интервал между двумя опросами. Его и читаем: 100 — номинал,
выше — турбо, заметно ниже — троттлинг или экономичный план питания.

Опрос — ~0.03 мс (замерено), счётчик английский (PdhAddEnglishCounter):
на русской Windows локализованные имена счётчиков другие. Нет PDH —
запасной путь psutil (честно помечен source="psutil": он может показывать
номинал всегда). Ничего нет — None, замер идёт без частоты. Вызовы PDH —
в r7/env.py (граница Windows-кода).
"""
from __future__ import annotations

import threading
from typing import Any

from r7 import env

PDH_COUNTER = r"\Processor Information(_Total)\% Processor Performance"


class CpuFreqProbe:
    """Источник «частота CPU, % номинальной». У каждого экземпляра свой
    PDH-запрос и свой интервал: наблюдатель операции и семплер прогона
    не сбивают друг другу окно. Потокобезопасен."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._query: Any = None
        self._counter: Any = None
        self._opened = False
        self.source: str | None = None          # "pdh" | "psutil" | None

    def _open(self) -> None:
        """Открывает PDH-запрос один раз. Первый CollectQueryData только
        запоминает базу: счётчик — отношение за интервал между опросами."""
        self._opened = True
        try:
            opened = env.pdh_open_counter(PDH_COUNTER)
        except Exception:  # счётчика нет (урезанная Windows) — пробуем psutil
            opened = None
            self._query = self._counter = None
        if opened is not None:
            self._query, self._counter = opened
            self.source = "pdh"
            return
        if env.PSUTIL_OK:
            self.source = "psutil"

    def sample(self) -> float | None:
        """% номинальной частоты за интервал с прошлого вызова (PDH) или
        мгновенно (psutil). None — источника нет или опрос не удался.

        Returns:
            float | None
        """
        with self._lock:
            if not self._opened:
                self._open()
            if self.source == "pdh":
                return self._sample_pdh()
            if self.source == "psutil":
                return self._sample_psutil()
            return None

    def _sample_pdh(self) -> float | None:
        try:
            value = env.pdh_read_double(self._query, self._counter)
        except Exception:  # первый интервал или сбой счётчика — точки нет
            return None
        return _valid_pct(value)

    @staticmethod
    def _sample_psutil() -> float | None:
        try:
            f = env.psutil.cpu_freq()
        except Exception:  # psutil не знает частоту на этой машине
            return None
        if not f or not f.max:
            return None
        return _valid_pct(f.current / f.max * 100.0)

    def close(self) -> None:
        """Закрывает PDH-запрос. Повторный вызов безопасен."""
        with self._lock:
            if self._query is not None:
                try:
                    env.pdh_close_query(self._query)
                except Exception:  # запрос уже закрыт — освобождать нечего
                    pass
            self._query = self._counter = None
            self._opened = False
            self.source = None


def _valid_pct(value: object) -> float | None:
    """Число в разумных пределах (0–1000 %) или None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not (0.0 < value < 1000.0):
        return None
    return round(float(value), 1)

