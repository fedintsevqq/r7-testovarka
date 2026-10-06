"""Проверка формата файла экспорта (аудит проглоченных ошибок 06.10.2026).

«Файл записан» значило только «размер > 0 и не растёт»: если выбор типа в
диалоге «Сохранить как» промахнулся, файл другого формата проходил как OK.
"""
import zipfile

import pytest

import r7_Testovarka as r7mod

check = r7mod.R7Testovarka._check_export_format

XLSX_TYPES = ('<Types><Override ContentType="application/vnd.openxmlformats-'
              'officedocument.spreadsheetml.sheet.main+xml"/></Types>')
XLTX_TYPES = ('<Types><Override ContentType="application/vnd.openxmlformats-'
              'officedocument.spreadsheetml.template.main+xml"/></Types>')


def _zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


@pytest.fixture
def files(tmp_path):
    return {
        "pdf": (tmp_path / "a.pdf").write_bytes(b"%PDF-1.7\n...") and tmp_path / "a.pdf",
        "ods": _zip(tmp_path / "a.ods",
                    {"mimetype": "application/vnd.oasis.opendocument.spreadsheet"}),
        "odt": _zip(tmp_path / "t.ods",
                    {"mimetype": "application/vnd.oasis.opendocument.text"}),
        "xltx": _zip(tmp_path / "a.xltx", {"[Content_Types].xml": XLTX_TYPES}),
        "xlsx": _zip(tmp_path / "a_really_xlsx.xltx", {"[Content_Types].xml": XLSX_TYPES}),
        "csv": (tmp_path / "a.csv").write_text("a;b\n1;2\n", encoding="utf-8")
               and tmp_path / "a.csv",
    }


@pytest.mark.parametrize("ext, key", [("pdf", "pdf"), ("ods", "ods"), ("xltx", "xltx"),
                                      ("csv", "csv")])
def test_right_format_passes(files, ext, key):
    ok, detail = check(files[key], ext)
    assert ok, detail


@pytest.mark.parametrize("ext, key, why", [
    ("pdf", "ods", "%PDF"),
    ("ods", "pdf", "не zip"),
    ("ods", "odt", "opendocument.text"),
    ("xltx", "xlsx", "обычная книга"),      # тип не переключился: сохранилась xlsx
    ("xltx", "ods", "нет типа"),
    ("csv", "xlsx", "двоичный"),
    ("csv", "pdf", "двоичный"),
])
def test_wrong_format_detected(files, ext, key, why):
    ok, detail = check(files[key], ext)
    assert not ok
    assert why in detail


def test_csv_with_nul_bytes_is_not_text(tmp_path):
    p = tmp_path / "a.csv"
    p.write_bytes(b"a\x00b\x00")
    assert check(p, "csv")[0] is False


def test_broken_zip(tmp_path):
    p = tmp_path / "a.ods"
    p.write_bytes(b"PK\x03\x04 truncated")
    ok, detail = check(p, "ods")
    assert not ok and "битый zip" in detail


def test_missing_file(tmp_path):
    ok, detail = check(tmp_path / "нет.pdf", "pdf")
    assert not ok and "не прочитать" in detail
