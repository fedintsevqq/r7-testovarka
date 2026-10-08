"""Тестовый документ .docx для замеров текстового редактора (этап 5, пункт 1).

Пишется на чистой стандартной библиотеке (zipfile + WordprocessingML), без
python-docx: инструмент ставится на много ПК, и лишний пакет ради одного
файла не нужен. Документ детерминирован: один и тот же seed даёт побайтно
тот же файл (фиксированные даты в zip, свой генератор случайных чисел) —
сравнивать версии Р7 можно только на одинаковой нагрузке.

Состав: главы с заголовком «Heading 1» и разрывом страницы перед каждой,
разделы «Heading 2», абзацы обычного стиля по ~85 слов (около шести на
страницу) и одна таблица в середине. В каждом абзаце есть слово
REPLACE_WORD — его ищет тест «Поиск и замена».
"""
from __future__ import annotations

import os
import random
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path
from xml.sax.saxutils import escape

DOC_FIXTURE_PAGES = 100
DOC_FIXTURE_NAME = f"r7-test-doc-{DOC_FIXTURE_PAGES}p.docx"
DOC_FIXTURE_PATTERNS = ("r7-test-doc-*.docx",)

PARAS_PER_PAGE = 6            # абзацев по ~85 слов на страницу A4, кегль 11
WORDS_PER_PARA = (70, 100)    # длина абзаца, слов (границы включительно)
SECTIONS_PER_CHAPTER = 3
PAGES_PER_CHAPTER = 10
TABLE_ROWS, TABLE_COLS = 30, 5

REPLACE_WORD = "квартал"      # ищет и заменяет тест «Поиск и замена»
REPLACE_WITH = "период"

# Дата всех записей архива: от неё зависят байты файла, а не от часов ПК.
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)

_VOCAB = (
    "отчёт", "показатель", "выручка", "расход", "доход", "план", "факт",
    "отдел", "проект", "договор", "поставка", "склад", "клиент", "услуга",
    "бюджет", "рост", "снижение", "итог", "прогноз", "решение", "задача",
    "срок", "этап", "результат", "анализ", "данные", "таблица", "раздел",
    "компания", "сотрудник", "регион", "филиал", "объём", "цена", "сумма",
    "налог", "баланс", "актив", "капитал", "продажа", "закупка", "смета",
    "по", "в", "на", "с", "и", "для", "за", "при", "от", "до", "о",
    "текущий", "новый", "общий", "основной", "плановый", "годовой",
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/docProps/core.xml" '
    'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '</Types>')

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
    'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '</Relationships>')

_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/styles" Target="styles.xml"/>'
    '</Relationships>')

_CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
    'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    '<dc:title>R7-Testovarka: тестовый документ</dc:title>'
    '<dc:creator>R7-Testovarka</dc:creator></cp:coreProperties>')


def _style(style_id: str, name: str, size_half_pt: int, bold: bool = False,
           heading_level: int | None = None) -> str:
    """Стиль абзаца. Имена Heading 1/2 — встроенные: Р7 узнаёт их как
    заголовки (навигация, put_Style по имени)."""
    ppr = ""
    if heading_level is not None:
        ppr = (f'<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/>'
               f'<w:outlineLvl w:val="{heading_level}"/></w:pPr>')
    rpr = f'<w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size_half_pt}"/></w:rPr>'
    based = "" if style_id == "Normal" else '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
    default = ' w:default="1"' if style_id == "Normal" else ""
    return (f'<w:style w:type="paragraph"{default} w:styleId="{style_id}">'
            f'<w:name w:val="{name}"/>{based}<w:qFormat/>{ppr}{rpr}</w:style>')


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<w:styles xmlns:w="{_W}">'
    '<w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
    '<w:sz w:val="22"/><w:lang w:val="ru-RU"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/>'
    '</w:pPr></w:pPrDefault></w:docDefaults>'
    + _style("Normal", "Normal", 22)
    + _style("Heading1", "heading 1", 32, bold=True, heading_level=0)
    + _style("Heading2", "heading 2", 26, bold=True, heading_level=1)
    + '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>'
      '<w:tblPr><w:tblBorders>'
      + "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
                for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
      + '</w:tblBorders></w:tblPr></w:style>'
    + '</w:styles>')


def _run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para(text: str, style: str | None = None, page_break_before: bool = False) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    br = '<w:r><w:br w:type="page"/></w:r>' if page_break_before else ""
    return f"<w:p>{ppr}{br}{_run(text)}</w:p>"


def _sentence_text(rng: random.Random, n_words: int) -> str:
    """Абзац из n_words слов словаря; слово REPLACE_WORD — в каждом абзаце."""
    words = [rng.choice(_VOCAB) for _ in range(n_words)]
    words[rng.randrange(n_words)] = REPLACE_WORD
    out: list[str] = []
    i = 0
    while i < len(words):
        chunk = words[i:i + rng.randint(8, 14)]
        i += len(chunk)
        out.append(" ".join(chunk).capitalize() + ".")
    return " ".join(out)


def _table(rng: random.Random) -> str:
    """Таблица TABLE_ROWS × TABLE_COLS: строка заголовков и числа."""
    width = 9000 // TABLE_COLS
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for _ in range(TABLE_COLS))

    def cell(text: str) -> str:
        return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/></w:tcPr>'
                f'{_para(text)}</w:tc>')

    head = "".join(cell(f"Столбец {c + 1}") for c in range(TABLE_COLS))
    rows = [f"<w:tr>{head}</w:tr>"]
    for _ in range(TABLE_ROWS - 1):
        rows.append("<w:tr>" + "".join(cell(f"{rng.randint(0, 99999)}")
                                       for _ in range(TABLE_COLS)) + "</w:tr>")
    return (f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
            f'<w:tblW w:w="9000" w:type="dxa"/></w:tblPr>'
            f'<w:tblGrid>{grid}</w:tblGrid>{"".join(rows)}</w:tbl>')


def _body(pages: int, rng: random.Random) -> str:
    """Тело документа: главы, разделы, абзацы и одна таблица в середине."""
    chapters = max(1, round(pages / PAGES_PER_CHAPTER))
    paras_per_section = max(1, round(pages * PARAS_PER_PAGE
                                     / (chapters * SECTIONS_PER_CHAPTER)))
    parts: list[str] = []
    table_at = chapters // 2
    for ch in range(chapters):
        parts.append(_para(f"Глава {ch + 1}. Итоги работы", "Heading1",
                           page_break_before=ch > 0))
        for sec in range(SECTIONS_PER_CHAPTER):
            parts.append(_para(f"{ch + 1}.{sec + 1}. Раздел отчёта", "Heading2"))
            for _ in range(paras_per_section):
                parts.append(_para(_sentence_text(rng, rng.randint(*WORDS_PER_PARA))))
        if ch == table_at:
            parts.append(_table(rng))
            parts.append(_para("Таблица 1. Сводные показатели"))
    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" '
            'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')
    return "".join(parts) + sect


def document_xml(pages: int = DOC_FIXTURE_PAGES, seed: int = 42) -> str:
    """word/document.xml как строка — чистая функция для тестов."""
    rng = random.Random(seed)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<w:document xmlns:w="{_W}" xmlns:r="{_R}"><w:body>'
            + _body(pages, rng) + "</w:body></w:document>")


def generate_docx(path: str | os.PathLike[str], pages: int = DOC_FIXTURE_PAGES,
                  seed: int = 42) -> Path:
    """Пишет тестовый .docx примерно на pages страниц.

    Args:
        path: куда сохранить (папки создаются).
        pages: целевое число страниц A4 (оценка, точное число считает Р7).
        seed: сид генератора; тот же seed — побайтно тот же файл.

    Returns:
        Path: путь к файлу.
    """
    if pages < 1:
        raise ValueError(f"pages должно быть ≥ 1, а не {pages!r}")
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parts = (("[Content_Types].xml", _CONTENT_TYPES), ("_rels/.rels", _ROOT_RELS),
             ("docProps/core.xml", _CORE), ("word/_rels/document.xml.rels", _DOC_RELS),
             ("word/styles.xml", _STYLES), ("word/document.xml", document_xml(pages, seed)))
    tmp = out_path.with_name(out_path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text.encode("utf-8"))
    tmp.replace(out_path)      # недописанный файл не выдаёт себя за фикстуру
    return out_path


def docx_stats(path: str | os.PathLike[str]) -> dict[str, int]:
    """Состав документа по word/document.xml: абзацы, заголовки, таблицы,
    вхождения REPLACE_WORD (без учёта регистра). Нужна тестам и журналу прогона.

    Returns:
        dict: paragraphs, heading1, heading2, tables, word_hits.
    """
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    body = re.sub(r"<w:tbl>.*?</w:tbl>", "", xml, flags=re.S)
    return {
        "paragraphs": body.count("<w:p>"),
        "heading1": xml.count('w:val="Heading1"'),
        "heading2": xml.count('w:val="Heading2"'),
        "tables": xml.count("<w:tbl>"),
        "word_hits": xml.lower().count(REPLACE_WORD),    # замена — без учёта регистра
    }


def find_doc_fixture(folders: Iterable[str | os.PathLike[str]]) -> Path | None:
    """Первая найденная фикстура документа в папках по порядку: точное имя
    DOC_FIXTURE_NAME, затем любые r7-test-doc-*.docx. Lock-файлы Office
    (`~$…`) пропускаются. Returns: Path | None."""
    for raw_folder in folders:
        folder = Path(raw_folder)
        if not folder.is_dir():
            continue
        exact = folder / DOC_FIXTURE_NAME
        if exact.is_file():
            return exact
        for pattern in DOC_FIXTURE_PATTERNS:
            for p in sorted(folder.glob(pattern)):
                if p.is_file() and not p.name.startswith("~$"):
                    return p
    return None
