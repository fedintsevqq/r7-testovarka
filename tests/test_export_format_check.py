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


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be", "utf-8-sig", "cp1251"])
def test_csv_text_encodings_pass(tmp_path, encoding):
    """Кодировку можно сменить в диалоге параметров CSV — любой текст годен."""
    p = tmp_path / "a.csv"
    data = "Имя;Сумма\nИванов;100\n"
    if encoding == "utf-16-be":
        p.write_bytes(b"\xfe\xff" + data.encode("utf-16-be"))
    else:
        p.write_bytes(data.encode(encoding))
    ok, detail = check(p, "csv")
    assert ok, detail


def test_unknown_extension_not_checked(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"\x00\x01")
    assert check(p, "txt") == (True, "формат не проверяется")


# ── Файл ещё держит Р7/x2t (эталонный прогон 06.10.2026, XLTX) ──────────

class _LockedOpen:
    """open() кидает PermissionError первые `locked` раз, потом — настоящий."""

    def __init__(self, locked):
        self.locked = locked
        self.calls = 0

    def __call__(self, path, *a, **k):
        self.calls += 1
        if self.calls <= self.locked:
            raise PermissionError(13, "Permission denied", str(path))
        return open(path, *a, **k)


@pytest.fixture
def clock(monkeypatch):
    """Часы двигает только sleep — тест не ждёт по-настоящему."""
    t = {"now": 0.0}
    monkeypatch.setattr(r7mod.time, "perf_counter", lambda: t["now"])
    monkeypatch.setattr(r7mod.time, "sleep", lambda s: t.__setitem__("now", t["now"] + s))
    return t


def test_locked_file_is_waited_for_then_checked(files, monkeypatch, clock):
    lock = _LockedOpen(locked=3)
    monkeypatch.setattr(r7mod.R7Testovarka, "_check_export_format_once",
                        staticmethod(_patched_once(lock)))
    ok, detail = check(files["xltx"], "xltx")
    assert ok is True and detail == "шаблон Excel"
    assert lock.calls == 4 and 0 < clock["now"] < r7mod.R7Testovarka.EXPORT_LOCK_WAIT_SEC


def test_file_locked_too_long_is_unverified_not_wrong_format(files, monkeypatch, clock):
    """Не дождались — «не проверить» (None), а не «формат не тот» (False)."""
    lock = _LockedOpen(locked=10_000)
    monkeypatch.setattr(r7mod.R7Testovarka, "_check_export_format_once",
                        staticmethod(_patched_once(lock)))
    ok, detail = check(files["xltx"], "xltx")
    assert ok is None and "занят" in detail
    assert clock["now"] >= r7mod.R7Testovarka.EXPORT_LOCK_WAIT_SEC


def test_missing_file_is_unverified(tmp_path):
    ok, detail = check(tmp_path / "нет.pdf", "pdf")
    assert ok is None and "не прочитать" in detail


def _patched_once(lock):
    """Настоящая проверка, но первое открытие файла — через lock."""
    real = r7mod.R7Testovarka._check_export_format_once

    def once(path, ext, zipfile):
        with lock(path, "rb"):
            pass
        return real(path, ext, zipfile)
    return once


DOCX_TYPES = ('<Types><Override ContentType="application/vnd.openxmlformats-'
              'officedocument.wordprocessingml.document.main+xml"/></Types>')


def test_document_formats(tmp_path):
    """Экспорт документа (этап 5): DOCX — тип wordprocessingml, ODT — mimetype."""
    docx = _zip(tmp_path / "a.docx", {"[Content_Types].xml": DOCX_TYPES})
    odt = _zip(tmp_path / "a.odt", {"mimetype": "application/vnd.oasis.opendocument.text"})
    assert check(docx, "docx") == (True, "документ Word")
    assert check(odt, "odt")[0] is True
    xlsx = _zip(tmp_path / "x.docx", {"[Content_Types].xml": XLSX_TYPES})
    ok, detail = check(xlsx, "docx")
    assert ok is False and "документа Word" in detail
    assert check(odt, "docx")[0] is False
    pdf = tmp_path / "p.docx"
    pdf.write_bytes(b"%PDF-1.7")
    assert "не zip" in check(pdf, "docx")[1]


PPTX_TYPES = ('<Types><Override ContentType="application/vnd.openxmlformats-'
              'officedocument.presentationml.presentation.main+xml"/></Types>')


def test_presentation_formats(tmp_path):
    """Экспорт презентации (этап 5): PPTX — тип presentationml, ODP — mimetype."""
    pptx = _zip(tmp_path / "a.pptx", {"[Content_Types].xml": PPTX_TYPES})
    odp = _zip(tmp_path / "a.odp", {"mimetype": "application/vnd.oasis.opendocument.presentation"})
    assert check(pptx, "pptx") == (True, "презентация PowerPoint")
    assert check(odp, "odp")[0] is True
    assert check(odp, "odt")[0] is False
    docx = _zip(tmp_path / "d.pptx", {"[Content_Types].xml": DOCX_TYPES})
    ok, detail = check(docx, "pptx")
    assert ok is False and "презентации PowerPoint" in detail
    assert check(pptx, "docx")[0] is False
    pdf = tmp_path / "p.pptx"
    pdf.write_bytes(b"%PDF-1.7")
    assert "не zip" in check(pdf, "pptx")[1]


def test_generated_fixture_passes_pptx_check(tmp_path):
    from r7.pptx_fixtures import generate_pptx
    assert check(generate_pptx(tmp_path / "f.pptx", slides=2), "pptx")[0] is True
