"""Метка «без подтверждения» в HTML-отчёте прогона (схема 8)."""
from pathlib import Path

import r7_Testovarka as r7mod


def _result(**extra):
    r = {"name": "Вставка большого массива (Ctrl+V)", "time": 1.0, "error": None,
         "runs": [1.0, 0.001, 1.0, 1.0], "run_statuses": ["ok", "unverified", "ok", "ok"],
         "min": 0.001, "max": 1.0, "mad": 0.0, "n_runs": 3, "n_timeouts": 0,
         "ram": None, "cpu": None}
    r.update(extra)
    return r


def _html(results, tmp_path):
    app = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    app.current_version_info = {"name": "Р7", "version": "2026.3.2"}
    f = tmp_path / "f.xlsx"
    f.write_bytes(b"x")
    return app._generate_html_report(results, Path(f), 9.0, "Р7 2026.3.2",
                                     [], [], None, None, None, None)


def test_unverified_badge_shown(tmp_path):
    assert "без подтверждения×1" in _html([_result(n_unverified=1)], tmp_path)


def test_no_badge_for_old_files_without_field(tmp_path):
    assert "без подтверждения" not in _html([_result()], tmp_path)
