"""Предусловия живого прогона на self-hosted раннере (perf.yml).

Не test_*.py — pytest его не собирает. Падает (код 1) с понятной причиной
ДО любого действия с Р7-Офис:

* процесс в сессии 0 (раннер установлен службой) — рабочего стола нет,
  pyautogui и UI Automation там не работают;
* Р7-Офис уже запущен — CDP-порт занят, а клавиши ушли бы в чужой документ;
* нет requests/websocket-client — без CDP цифры несравнимы.

    .venv/Scripts/python.exe tests/ci_preflight.py
"""
import ctypes
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _session_id():
    sid = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
        return None
    return sid.value


def check(log=print):
    """Список проблем; пустой — можно запускать."""
    problems = []
    if os.name != "nt":
        return ["живой прогон — только на Windows"]
    sid = _session_id()
    if sid == 0:
        problems.append("процесс в сессии 0 — раннер запущен службой, рабочего стола "
                        "нет; запустите run.cmd в сессии пользователя (docs/ci-runner.md)")
    if not ctypes.windll.user32.GetDesktopWindow():
        problems.append("нет рабочего стола")

    import r7_Testovarka as m
    if not m.env.WEBDRIVER_OK:
        problems.append("requests/websocket-client не установлены — CDP недоступен")
    app = m.R7Testovarka.__new__(m.R7Testovarka)
    app._r7_pids = None
    app._cached_r7_path = None
    running = app._get_r7_processes(log_cb=lambda *_: None)
    if running:
        problems.append("Р7-Офис уже запущен (PID: "
                        + ", ".join(str(p.pid) for p in running) + ") — закройте его")
    r7_path = app._find_r7_path()
    if not r7_path:
        problems.append("Р7-Офис не найден")
    log(f"Сессия: {sid}, Python: {sys.version.split()[0]}, Р7: {r7_path}")
    return problems


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    found = check()
    for p in found:
        print(f"❌ {p}")
    if not found:
        print("✅ Стенд готов к живому прогону")
    sys.exit(1 if found else 0)
