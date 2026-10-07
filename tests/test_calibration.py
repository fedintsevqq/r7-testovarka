"""Калибровка стенда (r7.calibration): числа при успехе, None при сбое и
таймауте, временный файл убирается."""
import os

from r7 import calibration


def test_cpu_index_returns_median_ms():
    v = calibration.cpu_index(iterations=20_000, repeats=3)
    assert isinstance(v, float) and v > 0


def test_cpu_index_none_on_timeout(monkeypatch):
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(calibration.time, "perf_counter", lambda: next(ticks))
    assert calibration.cpu_index(iterations=10, repeats=3, timeout_sec=1.0) is None


def test_cpu_index_none_on_error(monkeypatch):
    def boom(n):
        raise RuntimeError("x")
    monkeypatch.setattr(calibration, "_cpu_workload", boom)
    assert calibration.cpu_index(iterations=10) is None


def test_disk_index_writes_reads_and_cleans_up(tmp_path):
    v = calibration.disk_index(tmp_path, size_mb=2)
    assert isinstance(v, float) and v > 0
    assert list(tmp_path.iterdir()) == []          # временный файл удалён


def test_disk_index_none_when_folder_unusable(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert calibration.disk_index(blocker / "sub", size_mb=1) is None   # mkdir под файлом


def test_disk_index_none_on_timeout_and_removes_file(tmp_path, monkeypatch):
    ticks = iter([0.0, 0.0, 500.0, 500.0, 500.0, 500.0, 500.0])
    monkeypatch.setattr(calibration.time, "perf_counter", lambda: next(ticks))
    assert calibration.disk_index(tmp_path, size_mb=2, timeout_sec=1.0) is None
    assert list(tmp_path.iterdir()) == []


def test_calibrate_and_format(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "cpu_index", lambda **k: 312.4)
    monkeypatch.setattr(calibration, "disk_index", lambda folder, **k: 410.6)
    cal = calibration.calibrate(tmp_path)
    assert cal == {"cpu_ms": 312.4, "disk_mb_s": 410.6}
    assert calibration.format_calibration(cal) == "CPU 312 мс, диск 411 МБ/с"
    assert calibration.format_calibration({"cpu_ms": None, "disk_mb_s": 5.0}) == "диск 5 МБ/с"
    assert calibration.format_calibration({}) is None and calibration.format_calibration(None) is None


def test_workload_is_reproducible():
    assert calibration._cpu_workload(1000) == calibration._cpu_workload(1000)
    assert os.getpid()  # защита от «неиспользуемого» импорта в будущем рефакторинге
