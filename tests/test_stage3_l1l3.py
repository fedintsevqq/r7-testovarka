"""Тесты L1–L3 (нагрузочный стенд, этап 3):

- L1: _split_open_timing — раздельные холодный/тёплый старт, общая
  арифметика для трёх мест открытия файла (вынесена из тройной инлайн-копии
  по итогам code review).
- L3: _fix_r7_window_geometry — фиксированная геометрия окна Р7 вместо
  maximize(); _get_dpi_scale_pct — множитель масштабирования Windows.
- L2: TEST_DEFINITIONS содержит новые форматы экспорта (ods/csv/xltx рядом
  с pdf), _cleanup_x2t_temp_pdfs подчищает все четыре расширения.

win32gui/win32con/win32api — реальный установленный pywin32, функции
подменяются через monkeypatch (тот же приём, что в test_close_and_dialogs.py).
"""
from unittest.mock import Mock

import r7_Testovarka as r7mod


# ── _split_open_timing (L1) ──────────────────────────────────────────────

def test_split_open_timing_basic_arithmetic():
    result = r7mod.R7Testovarka._split_open_timing(
        open_start=100.0, window_appeared_ts=101.5,
        ready_ts=105.0, setup_elapsed=0.5)

    assert result["cold_start_ms"] == 1500.0
    # (105.0 - 101.5 - 0.5) * 1000 = 3000.0
    assert result["warm_start_ms"] == 3000.0
    assert result["total_open_ms"] == 4500.0


def test_split_open_timing_matches_open_elapsed_invariant():
    open_start, window_ts, ready_ts, setup_elapsed = 10.0, 12.3, 20.7, 0.4
    result = r7mod.R7Testovarka._split_open_timing(
        open_start, window_ts, ready_ts, setup_elapsed)

    open_elapsed_ms = (ready_ts - open_start - setup_elapsed) * 1000
    # Округление каждого слагаемого отдельно может разойтись с округлением
    # суммы не больше чем на 0.1 мс (см. docstring _split_open_timing).
    assert abs(result["total_open_ms"] - open_elapsed_ms) < 0.15


def test_split_open_timing_none_when_window_not_found():
    # Регрессия (найдено code review): _worker_run_test не прерывается на
    # таймауте ожидания окна, в отличие от двух других мест — если окно так
    # и не нашлось, window_appeared_ts на самом деле момент сдачи ожидания,
    # а не появления окна, и правдоподобно выглядящее число было бы тихо
    # неверным.
    result = r7mod.R7Testovarka._split_open_timing(
        open_start=100.0, window_appeared_ts=160.0,
        ready_ts=161.0, setup_elapsed=0.0, window_found=False)

    assert result == {"cold_start_ms": None, "warm_start_ms": None,
                       "total_open_ms": None}


# ── _get_dpi_scale_pct (L3) ──────────────────────────────────────────────

def test_get_dpi_scale_pct_returns_int(monkeypatch):
    monkeypatch.setattr(
        r7mod.ctypes.windll.shcore, "GetScaleFactorForDevice", lambda idx: 100
    )
    assert r7mod.R7Testovarka._get_dpi_scale_pct() == 100


def test_get_dpi_scale_pct_none_on_error(monkeypatch):
    def _boom(idx):
        raise OSError("no shcore on this OS")

    monkeypatch.setattr(r7mod.ctypes.windll.shcore, "GetScaleFactorForDevice", _boom)
    assert r7mod.R7Testovarka._get_dpi_scale_pct() is None


# ── _fix_r7_window_geometry (L3) ─────────────────────────────────────────

def test_fix_geometry_none_when_win32_unavailable(bare_r7, log, monkeypatch):
    monkeypatch.setattr(r7mod, "WIN32_OK", False)
    assert bare_r7._fix_r7_window_geometry(hwnd=123, log_cb=log) is None
    assert bare_r7._applied_r7_window_size is None


def test_fix_geometry_none_when_hwnd_missing(bare_r7, log):
    assert bare_r7._fix_r7_window_geometry(hwnd=None, log_cb=log) is None


def test_fix_geometry_applies_target_size_on_large_screen(bare_r7, log, monkeypatch):
    monkeypatch.setattr("win32api.GetSystemMetrics", lambda idx: 2560)
    move = Mock()
    monkeypatch.setattr("win32gui.MoveWindow", move)
    monkeypatch.setattr("win32gui.ShowWindow", Mock())

    result = bare_r7._fix_r7_window_geometry(hwnd=123, log_cb=log)

    assert result == {"width": r7mod.R7Testovarka.R7_WINDOW_W,
                       "height": r7mod.R7Testovarka.R7_WINDOW_H}
    assert bare_r7._applied_r7_window_size == result
    move.assert_called_once_with(
        123, 0, 0, r7mod.R7Testovarka.R7_WINDOW_W, r7mod.R7Testovarka.R7_WINDOW_H, True
    )
    assert not any("меньше цели" in m for m in log.messages)


def test_fix_geometry_clamps_to_small_screen_and_warns(bare_r7, log, monkeypatch):
    # Ноутбучный монитор: 1366×768 — уже целевой ширины (1920) и высоты (1080).
    monkeypatch.setattr("win32api.GetSystemMetrics",
                         lambda idx: 1366 if idx == r7mod.win32con.SM_CXSCREEN else 768)
    move = Mock()
    monkeypatch.setattr("win32gui.MoveWindow", move)
    monkeypatch.setattr("win32gui.ShowWindow", Mock())

    result = bare_r7._fix_r7_window_geometry(hwnd=123, log_cb=log)

    assert result == {"width": 1366, "height": 768}
    move.assert_called_once_with(123, 0, 0, 1366, 768, True)
    assert any("меньше цели" in m for m in log.messages)


def test_fix_geometry_returns_none_and_warns_on_win32_error(bare_r7, log, monkeypatch):
    monkeypatch.setattr("win32api.GetSystemMetrics",
                         Mock(side_effect=OSError("no display")))

    result = bare_r7._fix_r7_window_geometry(hwnd=123, log_cb=log)

    assert result is None
    assert bare_r7._applied_r7_window_size is None
    assert any("Не удалось зафиксировать размер окна" in m for m in log.messages)


# ── TEST_DEFINITIONS (L2) ────────────────────────────────────────────────

def test_test_definitions_includes_all_export_formats():
    names = r7mod.R7Testovarka.TEST_DEFINITIONS
    for fmt in ("PDF", "ODS", "CSV", "XLTX"):
        assert f"Сохранение в {fmt} (конвертация x2t)" in names
    # Список не должен разрастись дублями при повторном редактировании.
    assert len(names) == len(set(names))


# ── _cleanup_x2t_temp_pdfs (L2) ──────────────────────────────────────────

def test_cleanup_removes_all_export_extensions(bare_r7, log, tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path))
    kept = tmp_path / "unrelated.pdf"
    kept.write_text("keep me")
    leftovers = []
    for ext in ("pdf", "ods", "csv", "xltx"):
        f = tmp_path / f"temp_export_x2t_123.{ext}"
        f.write_text("leftover")
        leftovers.append(f)

    bare_r7._cleanup_x2t_temp_pdfs(log_cb=log)

    assert all(not f.exists() for f in leftovers)
    assert kept.exists()


def test_cleanup_safe_when_nothing_to_remove(bare_r7, log, tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path))
    bare_r7._cleanup_x2t_temp_pdfs(log_cb=log)
    assert log.messages == []


# ── _build_system_info wiring (L3) ───────────────────────────────────────

def test_build_system_info_reports_applied_window_size(bare_r7, monkeypatch):
    monkeypatch.setattr(r7mod, "PSUTIL_OK", False)
    monkeypatch.setattr(r7mod.ctypes.windll.shcore, "GetScaleFactorForDevice",
                         lambda idx: 100)
    bare_r7._cached_cpu_count = None
    bare_r7._applied_r7_window_size = {"width": 1920, "height": 1080}

    info = bare_r7._build_system_info()

    assert info["window_size"] == {"width": 1920, "height": 1080}
    assert info["dpi_scale_pct"] == 100
