"""Открытие с таймаутом и закрытие Р7 без проверки (аудит 06.10.2026)."""
import pytest

import r7_Testovarka as r7mod


def test_terminate_without_psutil_is_not_success(bare_r7, monkeypatch):
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", False)
    log = []
    assert bare_r7._terminate_r7_processes(log_cb=log.append) is False
    assert any("psutil" in m for m in log)


@pytest.mark.parametrize("worker, wrapper", [
    ("_spreadsheet_worker", "def run_test_with_runs"),
    ("_batch_run_single_version", "def measure("),
])
def test_edit_tests_skipped_when_document_not_loaded(worker, wrapper):
    """Тесты правки на недогруженном документе недостоверны: обёртка замера
    в обоих воркерах (зеркально) выходит при data_ready=False."""
    import inspect
    src = inspect.getsource(getattr(r7mod.R7Testovarka, worker))
    body = src[src.index(wrapper):]
    body = body[:body.index("_measure_op_repeated(")]
    assert "not data_ready" in body
    # Причина пропуска — в записи «Открытия файла»: у вкладки её собирает
    # _open_result (поведение — tests/test_run_phases.py), у Batch — сам воркер.
    owner = r7mod.R7Testovarka._open_result if worker == "_spreadsheet_worker" else None
    assert "_OPEN_NOT_READY" in (inspect.getsource(owner) if owner else src)


@pytest.mark.parametrize("procs, psutil_ok, gone", [
    ([], True, True),
    ([object()], True, False),
    ([], False, False),        # проверить нечем — пусть finally закроет аварийно
])
def test_r7_gone(bare_r7, monkeypatch, procs, psutil_ok, gone):
    monkeypatch.setattr(r7mod.env, "PSUTIL_OK", psutil_ok)
    bare_r7._get_r7_processes = lambda log_cb=None, fresh=False: procs
    assert bare_r7._r7_gone() is gone
