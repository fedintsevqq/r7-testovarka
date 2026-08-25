"""Тесты соак-режима (этап 3, M3): цикл операций с контрольными замерами и
фоновым сбором ресурсов.

Инфраструктура, не реальный многочасовой прогон — тесты крутят несколько
итераций синхронно (ResourceSampler мокается или используется с interval=0,
без реального сна) и проверяют логику: остановку по iterations/duration_sec/
stop_event, периодичность контрольных замеров, сохранение истории,
переиспользование detect_leak/compare_runs.
"""
import json
import threading
import time
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod


# ── run_soak: базовые условия остановки ────────────────────────────────

def test_run_soak_requires_iterations_or_duration():
    with pytest.raises(ValueError):
        r7mod.run_soak(lambda: None)


def test_run_soak_stops_after_iterations_count():
    calls = []
    out = r7mod.run_soak(lambda: calls.append(1), iterations=5)
    assert len(calls) == 5
    assert out["iterations_completed"] == 5
    assert out["stopped_early"] is False


def test_run_soak_iterations_takes_priority_over_duration():
    """Если заданы оба — duration_sec игнорируется (см. докстрока)."""
    calls = []
    out = r7mod.run_soak(lambda: calls.append(1), iterations=3, duration_sec=9999)
    assert len(calls) == 3
    assert out["iterations_completed"] == 3


def test_run_soak_stops_by_duration_when_no_iterations():
    calls = []

    def op():
        calls.append(1)
        time.sleep(0.02)

    out = r7mod.run_soak(op, duration_sec=0.05)
    assert len(calls) >= 1
    assert out["elapsed_sec"] >= 0.05 or len(calls) >= 2


def test_run_soak_stops_early_on_stop_event():
    stop_event = threading.Event()
    calls = []

    def op():
        calls.append(1)
        if len(calls) == 3:
            stop_event.set()

    out = r7mod.run_soak(op, iterations=100, stop_event=stop_event)
    assert out["stopped_early"] is True
    assert out["iterations_completed"] == 3


# ── контрольные замеры ──────────────────────────────────────────────────

def test_run_soak_takes_control_measurement_every_n_iterations():
    values = iter([10.0, 20.0, 30.0])
    out = r7mod.run_soak(lambda: None, iterations=9, control_every=3,
                         control_op=lambda: next(values))
    assert [m["iteration"] for m in out["control_measurements"]] == [3, 6, 9]
    assert [m["value"] for m in out["control_measurements"]] == [10.0, 20.0, 30.0]


def test_run_soak_no_control_measurements_without_control_op():
    out = r7mod.run_soak(lambda: None, iterations=10, control_every=2)
    assert out["control_measurements"] == []


def test_run_soak_no_control_measurements_when_control_every_zero():
    out = r7mod.run_soak(lambda: None, iterations=10, control_every=0,
                         control_op=lambda: 1.0)
    assert out["control_measurements"] == []


def test_run_soak_control_measurement_records_elapsed_time():
    out = r7mod.run_soak(lambda: None, iterations=1, control_every=1,
                         control_op=lambda: 1.0)
    assert out["control_measurements"][0]["t"] >= 0.0


# ── sampler (ResourceSampler, этап 2) ───────────────────────────────────

def test_run_soak_starts_and_stops_sampler():
    sampler = Mock()
    sampler.snapshot.return_value = []
    r7mod.run_soak(lambda: None, iterations=3, sampler=sampler)
    sampler.start.assert_called_once()
    sampler.stop.assert_called_once()
    sampler.join.assert_called_once()


def test_run_soak_stops_sampler_even_if_op_raises():
    sampler = Mock()
    sampler.snapshot.return_value = []

    def boom():
        raise RuntimeError("операция упала")

    with pytest.raises(RuntimeError):
        r7mod.run_soak(boom, iterations=3, sampler=sampler)
    sampler.stop.assert_called_once()


def test_run_soak_includes_leak_verdict_when_sampler_given():
    sampler = Mock()
    now = time.time()
    sampler.snapshot.return_value = [
        {"t": now + i * 60, "heap_mb": 100 + i * 1.0, "rss_mb": None, "doc_count": 1}
        for i in range(40)
    ]
    out = r7mod.run_soak(lambda: None, iterations=1, sampler=sampler)
    assert out["leak"]["leak"] is True


def test_run_soak_no_resource_fields_without_sampler():
    out = r7mod.run_soak(lambda: None, iterations=1)
    assert "resource_samples" not in out
    assert "leak" not in out


# ── drift (soak_drift_verdict / compare_runs, этап 2 M5) ───────────────

def test_run_soak_no_drift_field_when_not_enough_control_measurements():
    out = r7mod.run_soak(lambda: None, iterations=3, control_every=1,
                         control_op=lambda: 1.0)
    assert "drift" not in out


def test_run_soak_drift_field_present_with_enough_measurements():
    values = iter([1.0] * 5 + [2.0] * 5)  # явная деградация после baseline
    out = r7mod.run_soak(lambda: None, iterations=10, control_every=1,
                         control_op=lambda: next(values))
    assert out["drift"] is not None
    assert out["drift"]["verdict"] == "РЕГРЕССИЯ"


def test_soak_drift_verdict_none_with_insufficient_data():
    measurements = [{"iteration": i, "value": 1.0} for i in range(3)]
    assert r7mod.soak_drift_verdict(measurements) is None


def test_soak_drift_verdict_ignores_non_numeric_values():
    measurements = ([{"iteration": i, "value": 1.0} for i in range(5)]
                    + [{"iteration": i, "value": None} for i in range(5, 10)])
    assert r7mod.soak_drift_verdict(measurements) is None


def test_soak_drift_verdict_uses_compare_runs_directly():
    baseline = [1.0] * 5
    rest = [1.5] * 7
    measurements = [{"value": v} for v in baseline + rest]
    expected = r7mod.compare_runs(baseline, rest)
    assert r7mod.soak_drift_verdict(measurements, baseline_count=5) == expected


# ── история в JSON ───────────────────────────────────────────────────────

def test_run_soak_saves_history_to_json(tmp_path):
    path = tmp_path / "soak_history.json"
    r7mod.run_soak(lambda: None, iterations=3, history_path=path)
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["iterations_completed"] == 3


def test_run_soak_does_not_write_file_without_history_path(tmp_path):
    r7mod.run_soak(lambda: None, iterations=2)
    assert list(tmp_path.iterdir()) == []
