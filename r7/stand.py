"""Управление стендом на время прогона: план питания Windows.

На время прогона (вкладка, Batch, свой файл; CLI идёт через вкладку) план
питания переключается на «Высокая производительность» (SCHEME_MIN) и в
finally возвращается прежний — как _suspend_autosave/_restore_autosave. В
сбалансированном плане частота CPU плавает вслед за нагрузкой, и короткие
операции попадали то на разогнанное ядро, то на сброшенное. Прав
администратора powercfg /setactive не требует. Любой сбой — строка в журнал,
прогон идёт дальше.

Приоритет и привязку процессов Р7 к ядрам здесь НЕ трогаем: это меняет сам
объект замера (plan-to-20, этап 3, п. 6).
"""
from __future__ import annotations

import functools
import inspect
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from typing import Any, TypeVar, cast

from r7 import settings

# GUID встроенной схемы «Высокая производительность» (alias SCHEME_MIN).
HIGH_PERFORMANCE_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
# Переключаем только со встроенных «Сбалансированная» и «Экономия энергии»:
# они сбрасывают частоту и дают троттлинг. Свой план (OEM, «Максимальная
# производительность», игровой) владелец стенда выбрал сам; его подмена
# меняет отпечаток машины, и все прошлые отчёты стали бы «другим стендом».
SWITCH_FROM_GUIDS = frozenset({
    "381b4222-f694-41f0-9685-ff5bb260df2e",   # Сбалансированная
    "a1841308-3541-4fab-bc81-f71556f20b4a",   # Экономия энергии
})

POWERCFG_TIMEOUT_SEC = 5.0

LogCb = Callable[[str], object]
# powercfg с аргументами → (код возврата, вывод); подменяется в тестах.
RunPowercfg = Callable[[list[str]], tuple[int, str]]
_F = TypeVar("_F", bound=Callable[..., Any])

_GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def powercfg_path() -> str:
    """powercfg.exe из System32 — не из PATH (как msiexec в versions)."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "System32", "powercfg.exe")


def run_powercfg(args: Sequence[str], timeout: float = POWERCFG_TIMEOUT_SEC) -> tuple[int, str]:
    """Запускает powercfg без shell, с таймаутом и kill.

    Returns:
        tuple[int, str]: (код возврата, вывод). Вывод — в кодировке консоли
        (cp866 на русской Windows).

    Raises:
        OSError: powercfg не запустился; subprocess.TimeoutExpired — завис
        (процесс к этому моменту убит).
    """
    proc = subprocess.Popen([powercfg_path(), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        out, _err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    return proc.returncode, (out or b"").decode("cp866", errors="replace")


def parse_scheme_line(line: str) -> tuple[str, str | None] | None:
    """«GUID схемы питания: 381b…  (Сбалансированная)» → (guid, имя).

    Имя может само содержать скобки («GameTurbo (High Performance)»),
    поэтому берётся всё между первой «(» после GUID и последней «)».

    Returns:
        tuple[str, str | None] | None: None — GUID в строке нет.
    """
    m = _GUID_RE.search(line or "")
    if not m:
        return None
    rest = line[m.end():]
    name = None
    if "(" in rest:
        name = rest.split("(", 1)[1].rsplit(")", 1)[0].strip() or None
    return m.group(0).lower(), name


def get_active_scheme(run: RunPowercfg | None = None) -> tuple[str, str | None] | None:
    """Активная схема питания.

    Returns:
        tuple[str, str | None] | None: (guid, имя); None — не прочиталась.
    """
    code, out = (run or run_powercfg)(["/getactivescheme"])
    if code != 0:
        return None
    return parse_scheme_line(out.strip())


def list_schemes(run: RunPowercfg | None = None) -> dict[str, str | None]:
    """Все схемы питания: {guid: имя}. Пусто — список не прочитался."""
    code, out = (run or run_powercfg)(["/list"])
    if code != 0:
        return {}
    schemes: dict[str, str | None] = {}
    for line in out.splitlines():
        parsed = parse_scheme_line(line)
        if parsed:
            schemes[parsed[0]] = parsed[1]
    return schemes


def set_active_scheme(guid: str, run: RunPowercfg | None = None) -> bool:
    """powercfg /setactive <guid>. True — код возврата 0."""
    code, _out = (run or run_powercfg)(["/setactive", guid])
    return code == 0


def _plan_label(guid: str, name: str | None) -> str:
    return name or guid


def power_plan_during_run(method: _F) -> _F:
    """Декоратор воркера прогона: план «Высокая производительность» на время
    вызова, прежний — в finally. Вложенные воркеры (Batch внутри себя) план
    не переключают второй раз — счётчик глубины в _power_plan_depth."""
    sig = inspect.signature(method)

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        log_cb = None
        try:
            log_cb = sig.bind_partial(self, *args, **kwargs).arguments.get("log_cb")
        except TypeError:  # сигнатура не сошлась — пусть ошибку покажет сам вызов
            log_cb = None
        self._engage_power_plan(log_cb)
        try:
            return method(self, *args, **kwargs)
        finally:
            self._restore_power_plan(log_cb)
    return cast(_F, wrapper)


class StandMixin:
    """План питания на время прогона и порог троттлинга — часть R7Testovarka."""

    # Частота CPU за окно повтора ниже этой доли номинальной (r7.cpu_freq,
    # % Processor Performance) — повтор помечается «троттлинг». В медиану
    # входит как обычно: пометка объясняет выброс, а не прячет его.
    CPU_THROTTLE_PCT = 80.0

    # Состояние прогона; до первого _engage_power_plan атрибутов нет —
    # читаются через getattr с умолчанием.
    _power_plan_depth: int
    _power_plan_state: dict[str, Any] | None

    def _log_stand(self, log_cb: LogCb | None) -> LogCb:
        return log_cb or getattr(self, "add_test_log", None) or (lambda _m: None)

    def _engage_power_plan(self, log_cb: LogCb | None = None) -> None:
        """Включает «Высокую производительность», запоминает прежний план.

        Состояние — self._power_plan_state: {"before", "during", "before_guid",
        "switched"}; его читает _capture_environment (power_plan_before,
        power_plan_during). Повторный вызов внутри прогона только считает
        глубину. Никогда не бросает.
        """
        depth = getattr(self, "_power_plan_depth", 0)
        self._power_plan_depth = depth + 1
        if depth:
            return
        log = self._log_stand(log_cb)
        self._power_plan_state = None
        try:
            enabled = settings.get("manage_power_plan")
        except Exception:  # настройки не прочитались — умолчание: управлять
            enabled = True
        try:
            self._power_plan_state = self._switch_power_plan(log, enabled is not False)
        except Exception as e:  # powercfg нет, завис, упал — прогон идёт как есть
            log(f"⚠️ План питания: не удалось переключить ({type(e).__name__}: {e}) — "
                f"прогон идёт в текущем плане")

    def _switch_power_plan(self, log: LogCb, enabled: bool) -> dict[str, Any] | None:
        """Чтение и переключение плана; исключения ловит _engage_power_plan."""
        active = get_active_scheme()
        if active is None:
            log("⚠️ План питания: активный план не прочитался — не переключаю")
            return None
        guid, name = active
        state: dict[str, Any] = {"before": _plan_label(guid, name), "during": _plan_label(guid, name),
                 "before_guid": guid, "switched": False}
        if not enabled:
            log(f"🔋 План питания «{state['before']}» — управление выключено "
                f"(manage_power_plan в r7_settings.json)")
            return state
        if guid == HIGH_PERFORMANCE_GUID:
            log(f"🔋 План питания уже «{state['before']}»")
            return state
        if guid.lower() not in SWITCH_FROM_GUIDS:
            log(f"🔋 План питания «{state['before']}» — свой план стенда, не переключаю")
            return state
        schemes = list_schemes()
        if HIGH_PERFORMANCE_GUID not in schemes:
            log(f"⚠️ План питания: схемы «Высокая производительность» в системе нет "
                f"(powercfg /list) — прогон идёт в плане «{state['before']}»")
            return state
        if not set_active_scheme(HIGH_PERFORMANCE_GUID):
            log(f"⚠️ План питания: powercfg /setactive не прошёл — прогон идёт в "
                f"плане «{state['before']}»")
            return state
        state["during"] = _plan_label(HIGH_PERFORMANCE_GUID, schemes.get(HIGH_PERFORMANCE_GUID))
        state["switched"] = True
        log(f"🔋 План питания на время прогона: «{state['during']}» "
            f"(был «{state['before']}», вернётся после прогона)")
        return state

    def _restore_power_plan(self, log_cb: LogCb | None = None) -> None:
        """Возвращает план, бывший до прогона. Повторный вызов и вызов без
        переключения ничего не делают. Никогда не бросает."""
        depth = max(0, getattr(self, "_power_plan_depth", 0) - 1)
        self._power_plan_depth = depth
        if depth:
            return
        state = getattr(self, "_power_plan_state", None)
        # Прогон закончен: следующий (или сценарий без управления планом) не
        # должен унаследовать «было/стало» этого прогона.
        self._power_plan_state = None
        if not state or not state.get("switched"):
            return
        log = self._log_stand(log_cb)
        try:
            ok = set_active_scheme(state["before_guid"])
        except Exception as e:  # powercfg завис или пропал — сказать, что вернуть руками
            ok = False
            log(f"⚠️ План питания: возврат упал ({type(e).__name__}: {e})")
        if ok:
            log(f"🔋 План питания возвращён: «{state['before']}»")
        else:
            log(f"❌ План питания «{state['before']}» не вернулся — включите его вручную: "
                f"Панель управления → Электропитание")

    def _power_plan_environment(self) -> dict[str, str | None]:
        """Поля окружения: power_plan_before / power_plan_during. Без
        управления планом (прогон старым путём) — оба None."""
        state = getattr(self, "_power_plan_state", None) or {}
        return {"power_plan_before": state.get("before"),
                "power_plan_during": state.get("during")}
