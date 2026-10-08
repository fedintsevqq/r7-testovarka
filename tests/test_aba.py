"""Сэндвич A-B-A в Batch (этап 3 плана, п. 5): решение о дрейфе (r7/aba.py),
повтор базовой версии в _batch_worker (установка и прогон подменены — правило
6 CLAUDE.md) и предупреждение в сводке Batch."""
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import r7.runs as runs
import r7_reports
import r7_Testovarka as r7mod
from r7 import aba
from r7.batch_config import aba_default, validate_batch_config

STEADY = [1.00, 1.01, 0.99, 1.02, 1.00, 0.98]
SLOW = [1.50, 1.52, 1.49, 1.51, 1.50, 1.48]


def _op(name, rs, error=None):
    t = sorted(rs)[len(rs) // 2] if rs else 0.0
    return {"name": name, "time": 0.0 if error else t, "runs": rs,
            "run_statuses": ["ok"] * len(rs), "first_run_discarded": False, "error": error}


def _ver(ops, success=True):
    return {"file": "A.msi", "version": "v1", "success": success, "results": ops}


# ── check_drift ──────────────────────────────────────────────────────────

def test_same_base_twice_is_not_drift():
    chk = aba.check_drift(_ver([_op("ВПР", STEADY), _op("Ctrl+A", STEADY)]),
                          _ver([_op("ВПР", list(reversed(STEADY))), _op("Ctrl+A", STEADY)]))
    assert chk["drift"] is False and chk["drifted"] == [] and chk["compared"] == 2
    assert chk["warning"] is None


def test_significant_change_of_base_is_drift():
    chk = aba.check_drift(_ver([_op("ВПР", STEADY), _op("Ctrl+A", STEADY)]),
                          _ver([_op("ВПР", SLOW), _op("Ctrl+A", STEADY)]))
    assert chk["drift"] is True and chk["drifted"] == ["ВПР"]
    assert chk["warning"] == aba.DRIFT_WARNING
    row = next(r for r in chk["rows"] if r["name"] == "ВПР")
    assert row["verdict"] == "РЕГРЕССИЯ" and round(row["pct"]) == 50


def test_few_runs_use_fallback_threshold():
    # Экспорт в Batch — три повтора: критерию мало, решает порог по медианам.
    near = aba.check_drift(_ver([_op("PDF", [2.0, 2.1, 2.0])]), _ver([_op("PDF", [2.1, 2.1, 2.0])]))
    far = aba.check_drift(_ver([_op("PDF", [2.0, 2.1, 2.0])]), _ver([_op("PDF", [2.6, 2.5, 2.6])]))
    assert near["drift"] is False and far["drift"] is True
    assert "Δ медиан" in far["rows"][0]["verdict"]


def test_single_open_and_failed_ops_are_not_compared():
    a1 = _ver([{"name": "Открытие файла", "time": 9.0, "error": None},
               _op("ВПР", STEADY), _op("ODS", [], error="x2t упал")])
    a2 = _ver([{"name": "Открытие файла", "time": 14.0, "error": None},
               _op("ВПР", STEADY), _op("ODS", STEADY)])
    chk = aba.check_drift(a1, a2)
    assert [r["name"] for r in chk["rows"]] == ["ВПР"] and chk["drift"] is False


def test_failed_repeat_means_unknown():
    chk = aba.check_drift(_ver([_op("ВПР", STEADY)]), _ver([], success=False))
    assert chk["drift"] is None and "не проверен" in chk["warning"]


def test_should_repeat_base_and_config():
    assert aba.should_repeat_base(True, [1, 2]) and not aba.should_repeat_base(True, [1])
    assert not aba.should_repeat_base(False, [1, 2])
    assert aba_default(2) and not aba_default(1)


def test_validate_config_drops_aba_for_single_version(tmp_path):
    f = tmp_path / "t.xlsx"
    f.write_bytes(b"x")
    one, _ = validate_batch_config([Path("a.msi")], str(f), aba=True)
    two, _ = validate_batch_config([Path("a.msi"), Path("b.msi")], str(f), aba=True)
    assert one.aba is False and two.aba is True
    old, _ = validate_batch_config([Path("a.msi"), Path("b.msi")], str(f))
    assert old.aba is False


# ── поток Batch с повтором базовой версии ────────────────────────────────

@pytest.fixture
def batch(monkeypatch):
    a = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    a._applied_r7_window_size = None
    a._cached_cpu_count = 4
    a._webdriver_connector = None
    a.calls, a.logs, a.done, a.progress = [], [], [], []
    a._capture_environment = lambda log_cb=None: {}
    a.uninstall_current_version = lambda: a.calls.append("uninstall") or True
    a.install_fail = set()

    def install(dist):
        a.calls.append(("install", dist.name))
        return dist.name not in a.install_fail
    a.install_version = install
    a.detect_current_version = lambda: None
    a.current_version_info = {"name": "Р7-Офис", "version": "2026.3.2.3229"}
    a._clear_r7_cache = lambda: 0
    a.measured = {}            # имя дистрибутива → список итогов по очереди прогонов

    def single(test_file, label, log_cb, stop_event, pause_event):
        dist = [c for c in a.calls if isinstance(c, tuple) and c[0] == "install"][-1][1]
        a.calls.append(("run", dist))
        queue = a.measured.get(dist) or [[_op("ВПР", STEADY)]]
        ops = queue.pop(0) if len(queue) > 1 else queue[0]
        return {"open_elapsed": 8.0, "vlookup_elapsed": 1.0, "results": ops}
    a._batch_run_single_version = single
    monkeypatch.setattr(runs, "time", SimpleNamespace(
        sleep=lambda sec: time.sleep(min(sec, 0.01)), perf_counter=time.perf_counter,
        time=time.time))
    a.PAUSE_POLL_SEC = 0.01
    return a


def _run(a, versions, aba_on=True, stop_on_error=False, stop=None):
    stop = stop or threading.Event()
    a._batch_worker(versions, Path("f.xlsx"), stop_on_error, False,
                    a.logs.append, lambda t: None, lambda f, t: None, a.progress.append,
                    lambda res, err: a.done.append((res, err)), stop, threading.Event(),
                    aba=aba_on)
    return a.done[-1]


VERSIONS = [Path("R7-2026.3.1.msi"), Path("R7-2026.3.2.msi")]


def test_aba_reinstalls_base_at_end_and_marks_repeat(batch):
    res, errors = _run(batch, VERSIONS)
    installs = [c[1] for c in batch.calls if isinstance(c, tuple) and c[0] == "install"]
    assert installs == ["R7-2026.3.1.msi", "R7-2026.3.2.msi", "R7-2026.3.1.msi"]
    assert errors == 0 and len(res) == 3
    assert res[2]["aba_repeat"] and res[2]["version"].endswith(aba.REPEAT_SUFFIX)
    assert res[2]["aba"]["drift"] is False
    assert batch.progress[-1] == 3
    assert any("A-B-A" in m and "совпала" in m for m in batch.logs)


def test_aba_drift_is_flagged_and_logged(batch):
    batch.measured["R7-2026.3.1.msi"] = [[_op("ВПР", STEADY)], [_op("ВПР", SLOW)]]
    res, _ = _run(batch, VERSIONS)
    assert res[2]["aba"]["drift"] is True and res[2]["aba"]["drifted"] == ["ВПР"]
    assert any(aba.DRIFT_WARNING in m for m in batch.logs)


def test_aba_off_or_single_version_does_not_repeat(batch):
    res, _ = _run(batch, VERSIONS, aba_on=False)
    assert len(res) == 2 and not any(r.get("aba") for r in res)
    batch.calls.clear()
    res, _ = _run(batch, VERSIONS[:1], aba_on=True)
    assert len(res) == 1 and [c for c in batch.calls if c[0] == "install"] == [("install", "R7-2026.3.1.msi")]


def test_aba_skipped_after_stop_on_error(batch):
    batch.install_fail = {"R7-2026.3.2.msi"}
    res, errors = _run(batch, VERSIONS, stop_on_error=True)
    assert errors == 1 and len(res) == 2 and not any(r.get("aba") for r in res)


def test_aba_failed_repeat_is_unknown_and_counted(batch):
    batch.install_fail = {"R7-2026.3.1.msi"}
    res, errors = _run(batch, VERSIONS)
    # A1 тоже не встал — повтор выполнен, но сравнить не с чем.
    assert errors == 2 and res[2]["aba"]["drift"] is None


def test_aba_skipped_when_stopped(batch):
    stop = threading.Event()
    orig = batch._batch_run_single_version

    def single_then_stop(*a, **kw):
        out = orig(*a, **kw)
        if len([c for c in batch.calls if isinstance(c, tuple) and c[0] == "run"]) == 2:
            stop.set()
        return out
    batch._batch_run_single_version = single_then_stop
    res, _ = _run(batch, VERSIONS, stop=stop)
    assert len(res) == 2 and not any(r.get("aba") for r in res)


# ── сводка Batch ─────────────────────────────────────────────────────────

def _summary(drift):
    a1 = {"file": "A.msi", "version": "v1", "success": True, "open_elapsed": 8.0,
          "vlookup_elapsed": 1.0, "results": [_op("ВПР", STEADY)]}
    b = {**a1, "file": "B.msi", "version": "v2"}
    a2 = {**a1, "version": "v1" + aba.REPEAT_SUFFIX, "aba_repeat": True,
          "results": [_op("ВПР", SLOW if drift else STEADY)]}
    a2["aba"] = aba.check_drift(a1, a2)
    return [a1, b, a2]


def test_batch_model_without_aba_keeps_old_shape():
    model = r7_reports.batch_model([{"version": "v1", "file": "a.msi", "success": True}])
    assert model["aba"] is None


def test_batch_summary_shows_drift_warning(bare_r7):
    model = r7_reports.batch_model(_summary(drift=True))
    assert model["aba"]["tone"] == "critical" and model["aba"]["drifted"] == ["ВПР"]
    out = bare_r7._generate_batch_summary_html(_summary(drift=True))
    assert "Стенд дрейфовал за время Batch, сравнение ненадёжно" in out
    assert "повтор базовой версии (A-B-A)" in out and "дрейф" in out


def test_batch_summary_shows_stable_stand(bare_r7):
    out = bare_r7._generate_batch_summary_html(_summary(drift=False))
    assert "стенд не дрейфовал" in out and "ненадёжно" not in out


def test_old_batch_summary_renders_without_aba(bare_r7):
    out = bare_r7._generate_batch_summary_html(
        [{"version": "v1", "file": "a.msi", "success": True, "open_elapsed": 8.0}])
    assert "A-B-A" not in out and "v1" in out


# ── A-B-A для документов и презентаций ───────────────────────────────────

def test_aba_repeat_runs_in_editor_mode(batch):
    """Повтор A идёт в том же режиме редактора, что и весь Batch."""
    seen = []
    orig = batch._batch_run_single_version

    def single(*a, **k):
        seen.append(batch._run_editor)
        return orig(*a, **k)
    batch._batch_run_single_version = single
    batch._batch_worker(VERSIONS, Path("d.docx"), False, False, batch.logs.append,
                        lambda t: None, lambda f, t: None, batch.progress.append,
                        lambda res, err: batch.done.append((res, err)), threading.Event(),
                        threading.Event(), aba=True, editor="document")
    res, errors = batch.done[-1]
    assert seen == ["document"] * 3 and errors == 0
    assert res[2]["aba_repeat"] and res[2]["aba"]["drift"] is False
    assert batch._run_editor == "spreadsheet"


def test_batch_summary_shows_editor_and_hides_vlookup(bare_r7):
    rows = [{"version": "v1", "file": "a.msi", "success": True, "open_elapsed": 3.0,
             "editor": "presentation", "results": []},
            {"version": "v2", "file": "b.msi", "success": True, "open_elapsed": 3.2,
             "editor": "presentation", "results": []}]
    model = r7_reports.batch_model(rows)
    assert model["editor"] == "presentation" and model["show_vlookup"] is False
    assert model["title"] == "Сводка Batch · презентации (.pptx)"
    html = bare_r7._generate_batch_summary_html(rows)
    assert "Редактор: презентации (.pptx)" in html
    assert "ВПР, с" not in html and "vprChart" not in html


def test_batch_summary_spreadsheet_keeps_vlookup(bare_r7):
    rows = [{"version": "v1", "file": "a.msi", "success": True, "open_elapsed": 3.0,
             "vlookup_elapsed": 1.0}]
    model = r7_reports.batch_model(rows)
    assert model["editor"] == "spreadsheet" and model["title"] == "Сводка Batch"
    html = bare_r7._generate_batch_summary_html(rows)
    assert "ВПР, с" in html and "Редактор:" not in html


def test_noisy_export_within_noise_threshold_is_not_drift():
    """XLTX на стенде бимодален (5 или 11 с, CV 16 %, порог 49 %): с профилем
    шума такой разброс A1/A2 — не дрейф; без профиля — дрейф, как раньше."""
    # Живой Batch 08.10.2026: A1 и A2 на одной версии 2026.3.2.3229.
    a1 = _ver([_op("XLTX", [5.56, 7.97, 7.30])])
    a2 = _ver([_op("XLTX", [5.46, 5.41, 6.25])])
    profile = {"tests": {"XLTX": {"cv_pct": 16.43}}}
    assert aba.check_drift(a1, a2, noise_profile=profile)["drift"] is False
    assert aba.check_drift(a1, a2)["drift"] is True
    row = aba.check_drift(a1, a2, noise_profile=profile)["rows"][0]
    assert row["threshold_pct"] == 49.29
