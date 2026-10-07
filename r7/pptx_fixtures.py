"""Тестовая презентация .pptx для замеров редактора презентаций (этап 5, пункт 1).

Как r7/doc_fixtures.py: только стандартная библиотека (zipfile +
PresentationML), без python-pptx, и детерминированно — один seed даёт
побайтно тот же файл (фиксированные даты в zip, свой генератор случайных
чисел).

Состав: слайд-мастер с заголовком и текстом, два макета («Титульный слайд»
и «Заголовок и объект»), своя тема «R7 Testovarka» (имя не совпадает ни с
одной встроенной темой Р7 — «Смена темы» всегда меняет тему). Первый слайд
— титульный, каждый TABLE_EVERY-й — заголовок и таблица, остальные —
заголовок, маркированный список и две фигуры. Все части, без которых
PowerPoint и Р7 считают пакет битым, на месте: presentation.xml, presProps,
viewProps, tableStyles, мастер, макеты, тема, слайды со связями,
[Content_Types].xml и docProps.
"""
import random
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from r7.doc_fixtures import _VOCAB

PPTX_FIXTURE_SLIDES = 50
PPTX_FIXTURE_NAME = f"r7-test-slides-{PPTX_FIXTURE_SLIDES}.pptx"
PPTX_FIXTURE_PATTERNS = ("r7-test-slides-*.pptx",)

THEME_NAME = "R7 Testovarka"
TABLE_EVERY = 5                    # каждый 5-й слайд — таблица
TABLE_ROWS, TABLE_COLS = 6, 4
BULLETS_PER_SLIDE = (4, 6)         # пунктов списка (границы включительно)
WORDS_PER_BULLET = (5, 10)
SHAPE_GEOMS = ("rect", "ellipse", "roundRect", "triangle", "rightArrow")

SLIDE_CX, SLIDE_CY = 12192000, 6858000      # 16:9, EMU

_ZIP_DATE = (2026, 1, 1, 0, 0, 0)

_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS = f'xmlns:a="{_NS_A}" xmlns:r="{_NS_R}" xmlns:p="{_NS_P}"'
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_CT_PML = "application/vnd.openxmlformats-officedocument.presentationml."

LAYOUTS = (                         # (тип макета, имя) — порядок = номер файла
    ("title", "Титульный слайд"),
    ("obj", "Заголовок и объект"),
)


def _rels(items):
    """Связи части: [(тип, цель)] → XML, Id = rId1…"""
    body = "".join(
        f'<Relationship Id="rId{i}" Type="{_REL_OFFICE if not t.startswith("http") else ""}{t}" '
        f'Target="{target}"/>' for i, (t, target) in enumerate(items, 1))
    return f'{_XML}<Relationships xmlns="{_REL_NS}">{body}</Relationships>'


def _content_types(slides):
    over = [("/ppt/presentation.xml", _CT_PML + "presentation.main+xml"),
            ("/ppt/presProps.xml", _CT_PML + "presProps+xml"),
            ("/ppt/viewProps.xml", _CT_PML + "viewProps+xml"),
            ("/ppt/tableStyles.xml", _CT_PML + "tableStyles+xml"),
            ("/ppt/slideMasters/slideMaster1.xml", _CT_PML + "slideMaster+xml"),
            ("/ppt/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
            ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
            ("/docProps/app.xml",
             "application/vnd.openxmlformats-officedocument.extended-properties+xml")]
    over += [(f"/ppt/slideLayouts/slideLayout{i}.xml", _CT_PML + "slideLayout+xml")
             for i in range(1, len(LAYOUTS) + 1)]
    over += [(f"/ppt/slides/slide{i}.xml", _CT_PML + "slide+xml")
             for i in range(1, slides + 1)]
    body = "".join(f'<Override PartName="{p}" ContentType="{ct}"/>' for p, ct in over)
    return (f'{_XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'{body}</Types>')


_ROOT_RELS = _rels([
    ("officeDocument", "ppt/presentation.xml"),
    ("http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
     "docProps/core.xml"),
    ("extended-properties", "docProps/app.xml"),
])

_CORE = (
    f'{_XML}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
    'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    '<dc:title>R7-Testovarka: тестовая презентация</dc:title>'
    '<dc:creator>R7-Testovarka</dc:creator></cp:coreProperties>')


def _app(slides):
    return (f'{_XML}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
            'extended-properties"><Application>R7-Testovarka</Application>'
            f'<Slides>{slides}</Slides></Properties>')


# ── тема ─────────────────────────────────────────────────────────────────

def _theme():
    colors = (("accent1", "4472C4"), ("accent2", "ED7D31"), ("accent3", "A5A5A5"),
              ("accent4", "FFC000"), ("accent5", "5B9BD5"), ("accent6", "70AD47"),
              ("hlink", "0563C1"), ("folHlink", "954F72"))
    scheme = ('<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
              '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
              '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
              + "".join(f'<a:{n}><a:srgbClr val="{v}"/></a:{n}>' for n, v in colors))
    font = '<a:latin typeface="{0}"/><a:ea typeface=""/><a:cs typeface=""/>'
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = f'<a:ln w="{{0}}" cap="flat" cmpd="sng" algn="ctr">{fill}<a:prstDash val="solid"/></a:ln>'
    return (f'{_XML}<a:theme xmlns:a="{_NS_A}" name="{THEME_NAME}"><a:themeElements>'
            f'<a:clrScheme name="{THEME_NAME}">{scheme}</a:clrScheme>'
            f'<a:fontScheme name="{THEME_NAME}">'
            f'<a:majorFont>{font.format("Arial")}</a:majorFont>'
            f'<a:minorFont>{font.format("Arial")}</a:minorFont></a:fontScheme>'
            f'<a:fmtScheme name="{THEME_NAME}">'
            f'<a:fillStyleLst>{fill * 3}</a:fillStyleLst>'
            '<a:lnStyleLst>' + "".join(line.format(w) for w in (6350, 12700, 19050))
            + '</a:lnStyleLst>'
            '<a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3
            + '</a:effectStyleLst>'
            f'<a:bgFillStyleLst>{fill * 3}</a:bgFillStyleLst></a:fmtScheme>'
            '</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>')


# ── фигуры ───────────────────────────────────────────────────────────────

def _xfrm(x, y, cx, cy):
    return f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'


def _run(text, size=None):
    sz = f' sz="{size}"' if size else ""
    return f'<a:r><a:rPr lang="ru-RU"{sz} dirty="0"/><a:t>{escape(text)}</a:t></a:r>'


def _txbody(paras, anchor=None):
    body_pr = f'<a:bodyPr anchor="{anchor}"/>' if anchor else "<a:bodyPr/>"
    ps = "".join(f"<a:p>{_run(p)}</a:p>" if p else '<a:p><a:endParaRPr lang="ru-RU"/></a:p>'
                 for p in paras)
    return f"<p:txBody>{body_pr}<a:lstStyle/>{ps}</p:txBody>"


def _placeholder(shape_id, name, ph, paras, xfrm=""):
    """Заглушка макета/слайда: ph — атрибуты <p:ph>, xfrm — размер (у мастера)."""
    sp_pr = f'<p:spPr>{xfrm}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>' \
        if xfrm else "<p:spPr/>"
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{escape(name)}"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            f'<p:nvPr><p:ph {ph}/></p:nvPr></p:nvSpPr>{sp_pr}{_txbody(paras)}</p:sp>')


def _shape(shape_id, geom, x, y, cx, cy, accent, text):
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="Фигура {shape_id}"/>'
            '<p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr>{_xfrm(x, y, cx, cy)}<a:prstGeom prst="{geom}"><a:avLst/></a:prstGeom>'
            f'<a:solidFill><a:schemeClr val="{accent}"/></a:solidFill></p:spPr>'
            f'{_txbody([text], anchor="ctr")}</p:sp>')


def _table(shape_id, rng):
    col_w = 2400000
    rows = [[f"Показатель {c + 1}" for c in range(TABLE_COLS)]]
    rows += [[f"{rng.randint(0, 99999)}" for _ in range(TABLE_COLS)]
             for _ in range(TABLE_ROWS - 1)]
    grid = "".join(f'<a:gridCol w="{col_w}"/>' for _ in range(TABLE_COLS))
    trs = "".join(
        '<a:tr h="420000">' + "".join(
            f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p>{_run(v, 1600)}</a:p></a:txBody>'
            '<a:tcPr/></a:tc>' for v in row) + "</a:tr>"
        for row in rows)
    return (f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{shape_id}" '
            f'name="Таблица {shape_id}"/><p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/>'
            '</p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>'
            f'<p:xfrm><a:off x="1296000" y="1800000"/>'
            f'<a:ext cx="{col_w * TABLE_COLS}" cy="{420000 * TABLE_ROWS}"/></p:xfrm>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            f'<a:tbl><a:tblPr firstRow="1" bandRow="1"/><a:tblGrid>{grid}</a:tblGrid>{trs}'
            '</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')


_GROUP_HEAD = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
               '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
               '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


def _sp_tree(shapes):
    return f"<p:spTree>{_GROUP_HEAD}{''.join(shapes)}</p:spTree>"


# ── мастер и макеты ──────────────────────────────────────────────────────

_TITLE_BOX = _xfrm(838200, 365125, 10515600, 1325563)
_BODY_BOX = _xfrm(838200, 1825625, 10515600, 4351338)

_CLR_MAP = ('bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
            'hlink="hlink" folHlink="folHlink"')


def _master():
    tx = '<a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
    layouts = "".join(f'<p:sldLayoutId id="{2147483649 + i}" r:id="rId{i + 1}"/>'
                      for i in range(len(LAYOUTS)))
    return (f'{_XML}<p:sldMaster {_NS}><p:cSld>'
            '<p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
            + _sp_tree([_placeholder(2, "Заголовок 1", 'type="title"',
                                     ["Образец заголовка"], _TITLE_BOX),
                        _placeholder(3, "Текст 2", 'type="body" idx="1"',
                                     ["Образец текста"], _BODY_BOX)])
            + f'</p:cSld><p:clrMap {_CLR_MAP}/><p:sldLayoutIdLst>{layouts}</p:sldLayoutIdLst>'
            '<p:txStyles>'
            f'<p:titleStyle><a:lvl1pPr algn="l"><a:defRPr sz="4000" b="1">{tx}'
            '<a:latin typeface="+mj-lt"/></a:defRPr></a:lvl1pPr></p:titleStyle>'
            '<p:bodyStyle><a:lvl1pPr marL="228600" indent="-228600"><a:buFont typeface="Arial"/>'
            f'<a:buChar char="•"/><a:defRPr sz="2400">{tx}<a:latin typeface="+mn-lt"/>'
            '</a:defRPr></a:lvl1pPr></p:bodyStyle>'
            f'<p:otherStyle><a:lvl1pPr><a:defRPr sz="1800">{tx}</a:defRPr></a:lvl1pPr>'
            '</p:otherStyle></p:txStyles></p:sldMaster>')


def _layout(kind, name):
    if kind == "title":
        shapes = [_placeholder(2, "Заголовок 1", 'type="ctrTitle"', [""]),
                  _placeholder(3, "Подзаголовок 2", 'type="subTitle" idx="1"', [""])]
    else:
        shapes = [_placeholder(2, "Заголовок 1", 'type="title"', [""]),
                  _placeholder(3, "Объект 2", 'idx="1"', [""])]
    return (f'{_XML}<p:sldLayout {_NS} type="{kind}" preserve="1">'
            f'<p:cSld name="{escape(name)}">{_sp_tree(shapes)}</p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')


# ── слайды ───────────────────────────────────────────────────────────────

def _words(rng, lo, hi):
    return " ".join(rng.choice(_VOCAB) for _ in range(rng.randint(lo, hi))).capitalize()


def slide_kind(number):
    """Вид слайда по номеру (с 1): title — титульный, table — с таблицей,
    text — список и фигуры."""
    if number == 1:
        return "title"
    return "table" if number % TABLE_EVERY == 0 else "text"


def _slide(number, rng):
    kind = slide_kind(number)
    if kind == "title":
        shapes = [_placeholder(2, "Заголовок 1", 'type="ctrTitle"',
                               ["Итоги года: тестовая презентация"]),
                  _placeholder(3, "Подзаголовок 2", 'type="subTitle" idx="1"',
                               ["R7-Testovarka, замеры редактора презентаций"])]
    elif kind == "table":
        shapes = [_placeholder(2, "Заголовок 1", 'type="title"',
                               [f"Слайд {number}. Сводные показатели"]),
                  _table(3, rng)]
    else:
        bullets = [_words(rng, *WORDS_PER_BULLET) for _ in range(rng.randint(*BULLETS_PER_SLIDE))]
        shapes = [_placeholder(2, "Заголовок 1", 'type="title"',
                               [f"Слайд {number}. {_words(rng, 2, 4)}"]),
                  _placeholder(3, "Объект 2", 'idx="1"', bullets)]
        for k in range(2):
            geom = SHAPE_GEOMS[rng.randrange(len(SHAPE_GEOMS))]
            shapes.append(_shape(4 + k, geom, 8400000 + k * 1700000, 5300000, 1500000, 900000,
                                 f"accent{rng.randint(1, 6)}", f"{rng.randint(1, 99)} %"))
    return (f'{_XML}<p:sld {_NS}><p:cSld>{_sp_tree(shapes)}</p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')


def _presentation(slides):
    sld_ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>'
                      for i in range(1, slides + 1))
    return (f'{_XML}<p:presentation {_NS} saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            f'<p:sldIdLst>{sld_ids}</p:sldIdLst>'
            f'<p:sldSz cx="{SLIDE_CX}" cy="{SLIDE_CY}"/><p:notesSz cx="6858000" cy="9144000"/>'
            '</p:presentation>')


def _presentation_rels(slides):
    items = [("slideMaster", "slideMasters/slideMaster1.xml")]
    items += [("slide", f"slides/slide{i}.xml") for i in range(1, slides + 1)]
    items += [("presProps", "presProps.xml"), ("viewProps", "viewProps.xml"),
              ("theme", "theme/theme1.xml"), ("tableStyles", "tableStyles.xml")]
    return _rels(items)


_PRES_PROPS = f'{_XML}<p:presentationPr {_NS}/>'
_VIEW_PROPS = (f'{_XML}<p:viewPr {_NS}><p:normalViewPr><p:restoredLeft sz="15620"/>'
               '<p:restoredTop sz="94660"/></p:normalViewPr>'
               '<p:gridSpacing cx="72008" cy="72008"/></p:viewPr>')
_TABLE_STYLES = (f'{_XML}<a:tblStyleLst xmlns:a="{_NS_A}" '
                 'def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>')


def pptx_parts(slides=PPTX_FIXTURE_SLIDES, seed=42):
    """Все части пакета [(имя в zip, текст)] — чистая функция для тестов."""
    rng = random.Random(seed)
    parts = [("[Content_Types].xml", _content_types(slides)), ("_rels/.rels", _ROOT_RELS),
             ("docProps/core.xml", _CORE), ("docProps/app.xml", _app(slides)),
             ("ppt/presentation.xml", _presentation(slides)),
             ("ppt/_rels/presentation.xml.rels", _presentation_rels(slides)),
             ("ppt/presProps.xml", _PRES_PROPS), ("ppt/viewProps.xml", _VIEW_PROPS),
             ("ppt/tableStyles.xml", _TABLE_STYLES), ("ppt/theme/theme1.xml", _theme()),
             ("ppt/slideMasters/slideMaster1.xml", _master()),
             ("ppt/slideMasters/_rels/slideMaster1.xml.rels",
              _rels([("slideLayout", f"../slideLayouts/slideLayout{i}.xml")
                     for i in range(1, len(LAYOUTS) + 1)] + [("theme", "../theme/theme1.xml")]))]
    for i, (kind, name) in enumerate(LAYOUTS, 1):
        parts.append((f"ppt/slideLayouts/slideLayout{i}.xml", _layout(kind, name)))
        parts.append((f"ppt/slideLayouts/_rels/slideLayout{i}.xml.rels",
                      _rels([("slideMaster", "../slideMasters/slideMaster1.xml")])))
    for n in range(1, slides + 1):
        layout = 1 if slide_kind(n) == "title" else 2
        parts.append((f"ppt/slides/slide{n}.xml", _slide(n, rng)))
        parts.append((f"ppt/slides/_rels/slide{n}.xml.rels",
                      _rels([("slideLayout", f"../slideLayouts/slideLayout{layout}.xml")])))
    return parts


def generate_pptx(path, slides=PPTX_FIXTURE_SLIDES, seed=42):
    """Пишет тестовую .pptx на slides слайдов.

    Args:
        path: куда сохранить (папки создаются).
        slides: число слайдов, ≥ 1.
        seed: сид генератора; тот же seed — побайтно тот же файл.

    Returns:
        Path: путь к файлу.
    """
    if slides < 1:
        raise ValueError(f"slides должно быть ≥ 1, а не {slides!r}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in pptx_parts(slides, seed):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text.encode("utf-8"))
    tmp.replace(path)          # недописанный файл не выдаёт себя за фикстуру
    return path


def pptx_stats(path):
    """Состав презентации: слайды, таблицы, фигуры (не заглушки), макеты, тема.
    Нужна тестам и журналу живой проверки.

    Returns:
        dict: slides, tables, shapes, layouts, theme.
    """
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        slides = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        xml = "".join(z.read(n).decode("utf-8") for n in slides)
        theme = re.search(r'<a:theme [^>]*name="([^"]*)"',
                          z.read("ppt/theme/theme1.xml").decode("utf-8"))
    return {
        "slides": len(slides),
        "tables": xml.count("<a:tbl>"),
        "shapes": len(re.findall(r'<p:cNvPr id="\d+" name="Фигура', xml)),
        "layouts": sum(1 for n in names if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)),
        "theme": theme.group(1) if theme else None,
    }


def find_pptx_fixture(folders):
    """Первая найденная фикстура презентации в папках по порядку: точное имя
    PPTX_FIXTURE_NAME, затем любые r7-test-slides-*.pptx. Lock-файлы Office
    (`~$…`) пропускаются. Returns: Path | None."""
    for folder in folders:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        exact = folder / PPTX_FIXTURE_NAME
        if exact.is_file():
            return exact
        for pattern in PPTX_FIXTURE_PATTERNS:
            for p in sorted(folder.glob(pattern)):
                if p.is_file() and not p.name.startswith("~$"):
                    return p
    return None
