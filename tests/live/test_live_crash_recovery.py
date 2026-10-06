"""Живой сценарий восстановления после сбоя (run_crash_recovery.py).

Проверяет честность инструмента, а не поведение продукта: сбой настоящий
(убит сам Р7, а не лаунчер), диалог «Обнаружен файл блокировки…» найден и
нажат, переподключение прошло, проверка восстановления выполнена, а после
прогона — ни процессов Р7, ни lock-файлов, ни новых записей в recover.
Сколько правок восстановлено, не фиксируется: на 2026.3.2 — 0 (06.10.2026),
другая сборка вправе восстанавливать.

    set R7_LIVE=1
    .venv/Scripts/python.exe -m pytest -m live tests/live/test_live_crash_recovery.py -v

Р7 должен быть закрыт: сценарий откажется запускаться при открытом.
"""
import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("R7_LIVE") != "1",
                       reason="живой набор: задайте R7_LIVE=1 и закройте Р7-Офис"),
]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _recover_entries():
    base = os.environ.get("LOCALAPPDATA")
    rec = Path(base) / "R7-Office" / "Editors" / "data" / "recover" if base else None
    return {p.name for p in rec.iterdir()} if rec and rec.is_dir() else set()


def test_crash_recovery_scenario_is_honest(tmp_path, monkeypatch):
    import r7_Testovarka as r7mod
    import run_crash_recovery as cli

    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app._r7_pids = None
    app._cached_r7_path = None
    if not app._find_r7_path():
        pytest.skip("Р7-Офис не установлен")
    if app._get_r7_processes(log_cb=lambda _m: None):
        pytest.skip("Р7-Офис уже запущен")

    doc = tmp_path / "crash_live_10k.xlsx"
    app._generate_fixture(doc, rows=10_000, profile="flat")
    monkeypatch.chdir(tmp_path)              # Reports/ — во временной папке
    recover_before = _recover_entries()

    cli.main(["--file", str(doc), "--ops", "3", "--timeout", "30"])

    report = json.loads(next((tmp_path / "Reports").glob("crash_recovery_*.json"))
                        .read_text(encoding="utf-8"))
    sc = report["scenario"]
    assert sc["connected_before_crash"] and sc["edits_applied"] == 3
    assert sc["r7_processes_killed"] > 0, "убит только лаунчер — сбоя не было"
    assert sc["process_died_cleanly"] is True
    assert report["recovery_dialog"]["dialog_seen"] is True
    assert report["recovery_dialog"]["clicked"] is True
    assert report["recovery_dialog"]["button_text"] == "Продолжить редактирование"
    assert sc["connected_after_crash"] is True
    assert sc["recovered_count"] is not None
    assert report["leftover_r7_pids"] == []

    # После прогона — чисто (правило 10 и уборка следов сбоя).
    app._r7_pids = None
    assert not app._get_r7_processes(log_cb=lambda _m: None, fresh=True)
    assert not (tmp_path / f"~${doc.name}").exists()
    assert not (tmp_path / f".~lock.{doc.name}#").exists()
    assert _recover_entries() <= recover_before
