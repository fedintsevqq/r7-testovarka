"""Решения окна «Сравнить версии» без Tk (r7.compare_files, этап 4 плана)."""
import json
import os

import pytest

from r7.compare_files import (build_datasets, fmt_report_ts, read_report_meta, scan_reports,
                              validate_comparison)


def _report(folder, name, version, ts="20261006_224450", mtime=None):
    p = folder / name
    p.write_text(json.dumps({"version": version, "timestamp": ts, "results": []},
                            ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_fmt_report_ts():
    assert fmt_report_ts("20261006_224450") == "06.10.2026 22:44"
    assert fmt_report_ts("кривое") == "кривое" and fmt_report_ts(None) == ""


def test_read_report_meta_uses_custom_name(tmp_path):
    p = _report(tmp_path, "performance_full_a.json", "2026.3.2")
    meta = read_report_meta(p, {str(p): "до рефакторинга"})
    assert meta["version"] == "2026.3.2" and meta["display_name"] == "до рефакторинга"
    assert meta["ts"] == "06.10.2026 22:44" and meta["data"]["results"] == []


def test_read_report_meta_rejects_non_object(tmp_path):
    p = tmp_path / "performance_full_x.json"
    p.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        read_report_meta(p)


def test_scan_newest_first_and_keeps_broken_files(tmp_path):
    _report(tmp_path, "performance_full_old.json", "v1", mtime=1_000)
    _report(tmp_path, "performance_full_new.json", "v2", mtime=2_000)
    broken = tmp_path / "performance_full_bad.json"
    broken.write_text("{ не json", encoding="utf-8")
    os.utime(broken, (1_500, 1_500))
    (tmp_path / "comparison_1.html").write_text("x", encoding="utf-8")
    metas = scan_reports(tmp_path)
    assert [m["version"] for m in metas] == ["v2", "performance_full_bad", "v1"]
    assert metas[1]["data"] is None


@pytest.mark.parametrize("keys, base, title", [
    (["a"], "a", "Мало файлов"),
    (["a", "b", "c"], "a", "Много файлов"),
    (["a", "b"], None, "Базовая версия"),
    (["a", "b"], "c", "Базовая версия"),
])
def test_validate_comparison_refusals(keys, base, title):
    assert validate_comparison(keys, base, max_files=2)[0] == title


def test_validate_comparison_ok():
    assert validate_comparison(["a", "b"], "b", max_files=8) is None


def test_build_datasets_in_selection_order_with_labels(tmp_path):
    a = read_report_meta(_report(tmp_path, "performance_full_a.json", "v1"))
    b = read_report_meta(_report(tmp_path, "performance_full_b.json", "v2"))
    b["display_name"] = "новая"
    ds = build_datasets([b["key"], a["key"]], {a["key"]: a, b["key"]: b})
    assert [d["version"] for d in ds] == ["новая", "v1"]
    assert ds[0]["path"] == b["key"]


def test_build_datasets_reads_unloaded_file_or_names_it(tmp_path):
    p = _report(tmp_path, "performance_full_a.json", "v1")
    meta = {"path": p, "key": str(p), "version": "v1", "data": None}
    assert build_datasets([str(p)], {str(p): meta})[0]["data"]["version"] == "v1"
    p.write_text("{ сломан", encoding="utf-8")
    with pytest.raises(ValueError, match="performance_full_a.json"):
        build_datasets([str(p)], {str(p): meta})
