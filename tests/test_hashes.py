"""Проверка дистрибутивов по хэшам без окна (r7.hashes)."""
import csv
import hashlib
import json

import pytest

from r7 import hashes


@pytest.fixture
def dist(tmp_path):
    f = tmp_path / "R7-2026.3.2.msi"
    f.write_bytes(b"r7" * 100_000)
    return f


def _h(data):
    return hashlib.md5(data).hexdigest(), hashlib.sha256(data).hexdigest()


def test_file_hashes_match_hashlib(dist):
    md5_val, sha_val, size = hashes.file_hashes(dist, chunk=4096)
    assert (md5_val, sha_val) == _h(dist.read_bytes()) and size == pytest.approx(0.19, abs=0.01)


def test_hash_row_statuses(dist):
    md5_val, sha_val = _h(dist.read_bytes())
    assert hashes.hash_row(dist, {})["tag"] == hashes.NO_REF
    ok = hashes.hash_row(dist, {dist.name: {"md5": md5_val.upper(), "sha256": sha_val}})
    assert ok["tag"] == hashes.OK and ok["status"] == "✅ Совпадает"
    bad = hashes.hash_row(dist, {dist.name: {"md5": "0" * 32, "sha256": sha_val}})
    assert bad["tag"] == hashes.FAIL


def test_unreadable_file_row_keeps_error(tmp_path):
    row = hashes.hash_row(tmp_path / "нет.msi", {})
    assert row["md5"] == "ОШИБКА" and row["tag"] == hashes.FAIL
    assert hashes.status_against(row, {"нет.msi": {"md5": "x"}}) == (row["status"], row["tag"])


def test_summary():
    rows = [{"tag": hashes.OK}, {"tag": hashes.FAIL}, {"tag": hashes.NO_REF}, {"tag": hashes.OK}]
    assert hashes.summary(rows) == (4, 2, 1)


def test_reference_roundtrip(tmp_path):
    ref = tmp_path / "Distributives" / "hashes.json"
    assert hashes.load_reference(ref) == ({}, None)
    assert hashes.set_reference(ref, "a.msi", "1" * 32, "2" * 64) is None
    assert hashes.set_reference(ref, "b.msi", "3" * 32, "4" * 64) is None
    data, err = hashes.load_reference(ref)
    assert err is None and set(data) == {"a.msi", "b.msi"}
    assert hashes.delete_reference(ref, "a.msi") == (True, None)
    assert hashes.delete_reference(ref, "a.msi") == (False, None)
    assert set(hashes.load_reference(ref)[0]) == {"b.msi"}


@pytest.mark.parametrize("content", ["{ битый json", "[1, 2]"])
def test_corrupt_reference_is_never_overwritten(tmp_path, content):
    """Прежде битый hashes.json читался как пустой, и «Сохранить эталон»
    перезаписывал файл одной записью — остальные эталоны пропадали."""
    ref = tmp_path / "hashes.json"
    ref.write_text(content, encoding="utf-8")
    data, err = hashes.load_reference(ref)
    assert data == {} and err
    assert "не сохранён" in hashes.set_reference(ref, "a.msi", "1" * 32, "2" * 64)
    assert hashes.delete_reference(ref, "a.msi")[1]
    assert ref.read_text(encoding="utf-8") == content          # файл не тронут


@pytest.mark.parametrize("value, n, ok", [
    ("  ABCDEF" + "0" * 26 + " ", 32, True),
    ("abc", 32, False),
    ("g" * 32, 32, False),
    ("", 64, False),
    (None, 64, False),
])
def test_validate_hex(value, n, ok):
    clean, err = hashes.validate_hex(value, n, "MD5")
    assert (clean is not None) == ok and (err is None) == ok
    if ok:
        assert clean == clean.strip().lower()


def test_write_csv_has_bom_and_rows(tmp_path):
    out = tmp_path / "r.csv"
    hashes.write_csv(out, [{"name": "a.msi", "size": "1.00", "md5": "m", "sha256": "s",
                            "status": "✅ Совпадает"}])
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0][0] == "Имя файла" and rows[1] == ["a.msi", "1.00", "m", "s", "✅ Совпадает"]


def test_json_on_disk_is_readable(tmp_path):
    ref = tmp_path / "hashes.json"
    hashes.set_reference(ref, "Р7.msi", "1" * 32, "2" * 64)
    assert json.loads(ref.read_text(encoding="utf-8"))["Р7.msi"]["md5"] == "1" * 32
