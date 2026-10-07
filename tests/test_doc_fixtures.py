"""Фикстура документа .docx (r7/doc_fixtures.py): валидный zip, корректный
XML, состав, детерминированность и поиск фикстуры."""
import zipfile
import xml.etree.ElementTree as ET

import pytest

from r7 import doc_fixtures as df

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture(scope="module")
def docx(tmp_path_factory):
    return df.generate_docx(tmp_path_factory.mktemp("doc") / df.DOC_FIXTURE_NAME)


def test_zip_has_all_parts_and_xml_is_well_formed(docx):
    with zipfile.ZipFile(docx) as z:
        assert z.testzip() is None
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml",
                "word/styles.xml", "word/_rels/document.xml.rels"} <= names
        for name in names:
            if name.endswith((".xml", ".rels")):
                ET.fromstring(z.read(name))          # не падает — XML корректен
        types = z.read("[Content_Types].xml").decode("utf-8")
    assert "wordprocessingml.document.main+xml" in types


def test_body_structure_matches_stats(docx):
    with zipfile.ZipFile(docx) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(f"{W}body")
    paras = body.findall(f"{W}p")
    styles = [p.find(f"{W}pPr/{W}pStyle") for p in paras]
    h1 = sum(1 for s in styles if s is not None and s.get(f"{W}val") == "Heading1")
    h2 = sum(1 for s in styles if s is not None and s.get(f"{W}val") == "Heading2")
    stats = df.docx_stats(docx)
    assert stats["paragraphs"] == len(paras)
    assert (stats["heading1"], stats["heading2"]) == (h1, h2)
    assert h1 == df.DOC_FIXTURE_PAGES // df.PAGES_PER_CHAPTER
    assert h2 == h1 * df.SECTIONS_PER_CHAPTER
    assert stats["tables"] == 1 and len(body.findall(f"{W}tbl")) == 1
    rows = body.find(f"{W}tbl").findall(f"{W}tr")
    assert len(rows) == df.TABLE_ROWS
    # Около PARAS_PER_PAGE абзацев текста на страницу — порядок ~100 страниц.
    text_paras = len(paras) - h1 - h2 - 1
    assert 0.9 * df.DOC_FIXTURE_PAGES * df.PARAS_PER_PAGE <= text_paras \
        <= 1.1 * df.DOC_FIXTURE_PAGES * df.PARAS_PER_PAGE
    # Слово для «Поиска и замены» — в каждом абзаце текста.
    assert stats["word_hits"] >= text_paras


def test_page_breaks_before_every_chapter_but_first(docx):
    with zipfile.ZipFile(docx) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert xml.count('<w:br w:type="page"/>') == df.DOC_FIXTURE_PAGES // df.PAGES_PER_CHAPTER - 1


def test_styles_declare_builtin_headings(docx):
    with zipfile.ZipFile(docx) as z:
        styles = ET.fromstring(z.read("word/styles.xml"))
    names = {s.get(f"{W}styleId"): s.find(f"{W}name").get(f"{W}val")
             for s in styles.findall(f"{W}style")}
    assert names["Heading1"] == "heading 1" and names["Heading2"] == "heading 2"
    assert names["Normal"] == "Normal"


def test_same_seed_same_bytes_other_seed_differs(tmp_path):
    a = df.generate_docx(tmp_path / "a.docx", pages=12, seed=7).read_bytes()
    b = df.generate_docx(tmp_path / "b.docx", pages=12, seed=7).read_bytes()
    c = df.generate_docx(tmp_path / "c.docx", pages=12, seed=8).read_bytes()
    assert a == b and a != c


def test_pages_scale_document_and_reject_zero(tmp_path):
    small = df.docx_stats(df.generate_docx(tmp_path / "s.docx", pages=10))
    big = df.docx_stats(df.generate_docx(tmp_path / "b.docx", pages=40))
    assert big["paragraphs"] > 3 * small["paragraphs"]
    with pytest.raises(ValueError):
        df.generate_docx(tmp_path / "z.docx", pages=0)
    assert not list(tmp_path.glob("*.tmp"))


def test_find_doc_fixture_prefers_exact_name_and_skips_locks(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    assert df.find_doc_fixture([first, second, tmp_path / "нет"]) is None
    (first / "~$r7-test-doc-5p.docx").write_bytes(b"lock")
    (second / "r7-test-doc-5p.docx").write_bytes(b"x")
    assert df.find_doc_fixture([first, second]) == second / "r7-test-doc-5p.docx"
    (second / df.DOC_FIXTURE_NAME).write_bytes(b"x")
    assert df.find_doc_fixture([second]) == second / df.DOC_FIXTURE_NAME
