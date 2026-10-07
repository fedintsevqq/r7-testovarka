"""Фикстура презентации .pptx (r7/pptx_fixtures.py): валидный zip, корректный
XML, все обязательные части, каждая связь ведёт в существующую часть, у
каждой части есть тип содержимого, состав, детерминированность и поиск."""
import posixpath
import zipfile
import xml.etree.ElementTree as ET

import pytest

from r7 import pptx_fixtures as pf

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"

REQUIRED = {"[Content_Types].xml", "_rels/.rels", "docProps/core.xml", "docProps/app.xml",
            "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels", "ppt/presProps.xml",
            "ppt/viewProps.xml", "ppt/tableStyles.xml", "ppt/theme/theme1.xml",
            "ppt/slideMasters/slideMaster1.xml",
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            "ppt/slideLayouts/slideLayout1.xml", "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            "ppt/slides/slide1.xml", "ppt/slides/_rels/slide1.xml.rels"}


@pytest.fixture(scope="module")
def pptx(tmp_path_factory):
    return pf.generate_pptx(tmp_path_factory.mktemp("pptx") / pf.PPTX_FIXTURE_NAME)


@pytest.fixture(scope="module")
def parts(pptx):
    with zipfile.ZipFile(pptx) as z:
        assert z.testzip() is None
        return {n: z.read(n) for n in z.namelist()}


def _rels_of(parts, part):
    """Связи части: {rId: (тип, абсолютная цель)}."""
    folder, name = posixpath.split(part)
    rels_name = posixpath.join(folder, "_rels", name + ".rels")
    if rels_name not in parts:
        return {}
    out = {}
    for rel in ET.fromstring(parts[rels_name]).findall(f"{PR}Relationship"):
        target = posixpath.normpath(posixpath.join(folder, rel.get("Target")))
        out[rel.get("Id")] = (rel.get("Type").rsplit("/", 1)[-1], target)
    return out


def test_required_parts_and_well_formed_xml(parts):
    assert REQUIRED <= set(parts)
    for data in parts.values():
        ET.fromstring(data)                     # не падает — XML корректен


def test_every_relationship_target_exists(parts):
    rels_files = [n for n in parts if n.endswith(".rels")]
    # корень, presentation.xml, мастер, макеты, слайды
    assert len(rels_files) == 3 + len(pf.LAYOUTS) + pf.PPTX_FIXTURE_SLIDES
    for rels in rels_files:
        folder = posixpath.dirname(posixpath.dirname(rels))
        source = posixpath.join(folder, posixpath.basename(rels)[:-len(".rels")])
        assert rels == "_rels/.rels" or source in parts, rels
        for _rid, (_type, target) in _rels_of(parts, source).items():
            assert target in parts, (rels, target)


def test_every_part_has_content_type(parts):
    types = ET.fromstring(parts["[Content_Types].xml"])
    overrides = {o.get("PartName").lstrip("/"): o.get("ContentType")
                 for o in types.findall(f"{CT}Override")}
    defaults = {d.get("Extension") for d in types.findall(f"{CT}Default")}
    for name in overrides:
        assert name in parts, name              # нет лишних Override
    for name in parts:
        if name == "[Content_Types].xml":
            continue
        assert name in overrides or name.rsplit(".", 1)[-1] in defaults, name
    assert overrides["ppt/presentation.xml"].endswith("presentationml.presentation.main+xml")
    slides = [n for n, ct in overrides.items() if ct.endswith("presentationml.slide+xml")]
    assert len(slides) == pf.PPTX_FIXTURE_SLIDES


def test_presentation_lists_master_and_slides_in_order(parts):
    pres = ET.fromstring(parts["ppt/presentation.xml"])
    rels = _rels_of(parts, "ppt/presentation.xml")
    masters = pres.findall(f"{P}sldMasterIdLst/{P}sldMasterId")
    assert [rels[m.get(f"{R}id")] for m in masters] == [
        ("slideMaster", "ppt/slideMasters/slideMaster1.xml")]
    sld = pres.findall(f"{P}sldIdLst/{P}sldId")
    ids = [int(s.get("id")) for s in sld]
    assert len(ids) == len(set(ids)) == pf.PPTX_FIXTURE_SLIDES and min(ids) >= 256
    assert [rels[s.get(f"{R}id")][1] for s in sld] == [
        f"ppt/slides/slide{i}.xml" for i in range(1, pf.PPTX_FIXTURE_SLIDES + 1)]
    kinds = {t for t, _ in rels.values()}
    assert {"presProps", "viewProps", "theme", "tableStyles"} <= kinds
    size = pres.find(f"{P}sldSz")
    assert (int(size.get("cx")), int(size.get("cy"))) == (pf.SLIDE_CX, pf.SLIDE_CY)


def test_master_layouts_and_theme_are_linked(parts):
    master = ET.fromstring(parts["ppt/slideMasters/slideMaster1.xml"])
    rels = _rels_of(parts, "ppt/slideMasters/slideMaster1.xml")
    layout_ids = master.findall(f"{P}sldLayoutIdLst/{P}sldLayoutId")
    assert [rels[x.get(f"{R}id")][1] for x in layout_ids] == [
        f"ppt/slideLayouts/slideLayout{i}.xml" for i in range(1, len(pf.LAYOUTS) + 1)]
    assert all(int(x.get("id")) >= 2147483648 for x in layout_ids)
    assert ("theme", "ppt/theme/theme1.xml") in rels.values()
    assert master.find(f"{P}clrMap") is not None
    for i in range(1, len(pf.LAYOUTS) + 1):
        assert list(_rels_of(parts, f"ppt/slideLayouts/slideLayout{i}.xml").values()) == [
            ("slideMaster", "ppt/slideMasters/slideMaster1.xml")]
    theme = ET.fromstring(parts["ppt/theme/theme1.xml"])
    assert theme.get("name") == pf.THEME_NAME
    fmt = theme.find(f"{A}themeElements/{A}fmtScheme")
    for lst in ("fillStyleLst", "lnStyleLst", "effectStyleLst", "bgFillStyleLst"):
        assert len(list(fmt.find(f"{A}{lst}"))) == 3, lst


def test_slides_content_matches_stats(pptx, parts):
    stats = pf.pptx_stats(pptx)
    n = pf.PPTX_FIXTURE_SLIDES
    tables = sum(1 for i in range(1, n + 1) if pf.slide_kind(i) == "table")
    texts = sum(1 for i in range(1, n + 1) if pf.slide_kind(i) == "text")
    assert stats == {"slides": n, "tables": tables, "shapes": 2 * texts,
                     "layouts": len(pf.LAYOUTS), "theme": pf.THEME_NAME}
    assert tables == n // pf.TABLE_EVERY
    first = _rels_of(parts, "ppt/slides/slide1.xml")
    assert list(first.values()) == [("slideLayout", "ppt/slideLayouts/slideLayout1.xml")]
    table_slide = ET.fromstring(parts[f"ppt/slides/slide{pf.TABLE_EVERY}.xml"])
    tbl = table_slide.find(f".//{A}tbl")
    assert len(tbl.findall(f"{A}tr")) == pf.TABLE_ROWS
    assert len(tbl.findall(f"{A}tblGrid/{A}gridCol")) == pf.TABLE_COLS
    text_slide = ET.fromstring(parts["ppt/slides/slide2.xml"])
    shapes = text_slide.findall(f"{P}cSld/{P}spTree/{P}sp")
    assert len(shapes) == 4                     # заголовок, список, две фигуры
    ids = [int(e.get("id")) for e in text_slide.iter(f"{P}cNvPr")]
    assert len(ids) == len(set(ids))            # id фигур слайда уникальны


def test_same_seed_same_bytes_other_seed_differs(tmp_path):
    a = pf.generate_pptx(tmp_path / "a.pptx", slides=7, seed=7).read_bytes()
    b = pf.generate_pptx(tmp_path / "b.pptx", slides=7, seed=7).read_bytes()
    c = pf.generate_pptx(tmp_path / "c.pptx", slides=7, seed=8).read_bytes()
    assert a == b and a != c


def test_slides_count_and_reject_zero(tmp_path):
    assert pf.pptx_stats(pf.generate_pptx(tmp_path / "one.pptx", slides=1))["slides"] == 1
    assert pf.pptx_stats(pf.generate_pptx(tmp_path / "s.pptx", slides=12))["slides"] == 12
    with pytest.raises(ValueError):
        pf.generate_pptx(tmp_path / "z.pptx", slides=0)
    assert not list(tmp_path.glob("*.tmp"))


def test_find_pptx_fixture_prefers_exact_name_and_skips_locks(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    assert pf.find_pptx_fixture([first, second, tmp_path / "нет"]) is None
    (first / "~$r7-test-slides-5.pptx").write_bytes(b"lock")
    (second / "r7-test-slides-5.pptx").write_bytes(b"x")
    assert pf.find_pptx_fixture([first, second]) == second / "r7-test-slides-5.pptx"
    (second / pf.PPTX_FIXTURE_NAME).write_bytes(b"x")
    assert pf.find_pptx_fixture([second]) == second / pf.PPTX_FIXTURE_NAME
