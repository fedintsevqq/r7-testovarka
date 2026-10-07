"""Какой прогон сейчас идёт — одно состояние на всё приложение.

Вкладка «Производительность», Batch и тест своего файла шлют клавиши в Р7,
сценарии вкладки «Сценарии» запускают и убивают процесс Р7 сами, поэтому
одновременно может идти только один из них. Прежде это держали два
флага (_perf_running, _batch_running), которые каждый вход проверял сам, а
тест своего файла не проверял вовсе — его можно было запустить поверх
идущего прогона (этап 4 плана, 07.10.2026).

RunState не знает про Tk: try_start возвращает причину отказа (заголовок и
текст), а показывает её интерфейс. Захват атомарен — двойной щелчок по
«Запустить» не начнёт два прогона.
"""
import threading

PERF, BATCH, CUSTOM, INSTALL, SCENARIO = "perf", "batch", "custom", "install", "scenario"

# Отказ: (что хотят запустить, что уже идёт) → (заголовок, текст).
_BOTH_USE_KEYS = "Оба режима управляют клавиатурой Р7-Офис и не могут работать одновременно. "
REFUSALS = {
    (PERF, PERF): ("Тест уже выполняется",
                   "Дождитесь завершения текущего прогона или нажмите «Остановить»."),
    (PERF, BATCH): ("Выполняется Batch-режим",
                    _BOTH_USE_KEYS + "Дождитесь завершения Batch-режима."),
    (PERF, CUSTOM): ("Выполняется тест своего файла",
                     _BOTH_USE_KEYS + "Дождитесь завершения теста своего файла."),
    (BATCH, BATCH): ("Batch уже выполняется",
                     "Дождитесь завершения текущего Batch-прогона."),
    (BATCH, PERF): ("Выполняется тест производительности",
                    _BOTH_USE_KEYS + "Дождитесь завершения теста или нажмите «Остановить» "
                    "на вкладке «Производительность»."),
    (BATCH, CUSTOM): ("Выполняется тест своего файла",
                      _BOTH_USE_KEYS + "Дождитесь завершения теста своего файла."),
    (CUSTOM, CUSTOM): ("Тест уже выполняется",
                       "Дождитесь завершения текущего теста своего файла."),
    (CUSTOM, PERF): ("Выполняется тест производительности",
                     _BOTH_USE_KEYS + "Дождитесь завершения теста или нажмите «Остановить» "
                     "на вкладке «Производительность»."),
    (CUSTOM, BATCH): ("Выполняется Batch-режим",
                      _BOTH_USE_KEYS + "Дождитесь завершения Batch-режима."),
}
# Установка/удаление версии и любой прогон взаимно исключены: удаление Р7
# посреди замера роняет прогон, а прогон посреди установки меряет не ту версию.
_INSTALL_BUSY = ("Идёт установка версии",
                 "Дождитесь завершения установки или удаления Р7-Офис.")
_RUN_BUSY = {PERF: "Выполняется тест производительности", BATCH: "Выполняется Batch-режим",
             CUSTOM: "Выполняется тест своего файла", SCENARIO: "Выполняется сценарий"}
# Сценарии (soak, многодокументный, восстановление после сбоя) запускают и
# закрывают Р7 сами — с любым прогоном они делят процесс Р7 и CDP-порт.
_SCENARIO_SHARES_R7 = ("Сценарий и прогон работают с одним процессом Р7-Офис и одним "
                       "CDP-портом, одновременно они идти не могут. ")
REFUSALS[(SCENARIO, SCENARIO)] = ("Сценарий уже выполняется",
                                  "Дождитесь завершения текущего сценария или нажмите «Остановить» "
                                  "на вкладке «Сценарии».")
for _k in (PERF, BATCH, CUSTOM):
    REFUSALS[(_k, SCENARIO)] = (_RUN_BUSY[SCENARIO],
                                _SCENARIO_SHARES_R7 + "Дождитесь завершения сценария.")
    REFUSALS[(SCENARIO, _k)] = (_RUN_BUSY[_k], _SCENARIO_SHARES_R7 + "Дождитесь завершения прогона.")
for _k in (PERF, BATCH, CUSTOM, SCENARIO):
    REFUSALS[(_k, INSTALL)] = _INSTALL_BUSY
    REFUSALS[(INSTALL, _k)] = (_RUN_BUSY[_k], "Установка и удаление Р7-Офис недоступны, пока "
                                             "идёт прогон: он работает с установленной версией.")
REFUSALS[(INSTALL, INSTALL)] = _INSTALL_BUSY
del _k


class RunState:
    """Идущий прогон: None или один из PERF, BATCH, CUSTOM, INSTALL, SCENARIO."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active: str | None = None

    def refusal(self, kind: str) -> tuple[str, str] | None:
        """(заголовок, текст), если запустить kind сейчас нельзя, иначе None."""
        active = self.active
        return None if active is None else REFUSALS[(kind, active)]

    def try_start(self, kind: str) -> tuple[str, str] | None:
        """Захватывает состояние под kind. Возвращает None при успехе, иначе
        причину отказа (как refusal) — тогда состояние не меняется."""
        if (kind, kind) not in REFUSALS:
            raise ValueError(f"неизвестный вид прогона: {kind!r}")
        with self._lock:
            if self.active is not None:
                return REFUSALS[(kind, self.active)]
            self.active = kind
            return None

    def finish(self, kind: str) -> None:
        """Освобождает состояние, если его держит kind (повторный вызов и
        чужой kind ничего не делают — finally может сработать дважды)."""
        with self._lock:
            if self.active == kind:
                self.active = None

    def is_running(self, kind: str) -> bool:
        return self.active == kind


def missing_packages(pyautogui_ok: bool, pyperclip_ok: bool, excel_ok: bool,
                     win32_ok: bool) -> list[str]:
    """Пакеты, без которых прогон невозможен, — для сообщения об ошибке."""
    return [name for name, ok in (("pyautogui", pyautogui_ok), ("pyperclip", pyperclip_ok),
                                  ("openpyxl", excel_ok), ("pywin32", win32_ok)) if not ok]


class RunStateMixin:
    """Доступ к RunState из R7Testovarka и прежние флаги _perf_running /
    _batch_running поверх него (их читают интерфейс и тесты)."""

    @property
    def run_state(self) -> RunState:
        st = self.__dict__.get("_run_state")
        if st is None:                     # объект без __init__ (тесты) — создаём по месту
            st = self.__dict__["_run_state"] = RunState()
        return st

    # staticmethod: в теле класса зовётся как обычная функция (Python 3.10+).
    @staticmethod
    def _flag(kind: str) -> property:
        def get(self: "RunStateMixin") -> bool:
            return self.run_state.is_running(kind)

        def set_(self: "RunStateMixin", value: bool) -> None:
            st = self.run_state
            if value:
                st.active = kind
            else:
                st.finish(kind)
        return property(get, set_)

    _perf_running = _flag(PERF)
    _batch_running = _flag(BATCH)
    _custom_running = _flag(CUSTOM)
    _scenario_running = _flag(SCENARIO)
    del _flag
