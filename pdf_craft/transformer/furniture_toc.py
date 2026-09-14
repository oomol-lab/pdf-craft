"""Reconcile furniture bound to a TOC identity after chapter translation."""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element

from pdf_craft.common import read_xml, save_xml
from pdf_craft.extractor.chapter.chapter import (
    BlockLayout,
    InlineExpression,
    ParagraphLayout,
    decode,
)
from pdf_craft.extractor.toc import decode as decode_toc, iter_toc
from pdf_craft.markdown.paragraph import flatten


def reconcile_furniture_toc(
    source_chapters: Path,
    translated_chapters: Path,
    toc_path: Path,
    furnitures_path: Path,
) -> None:
    """Apply the translated formal headline to every bound furniture item.

    ``toc.xml`` has no duplicated title string.  It only identifies the source
    block for a formal headline, so the before/after chapter files are the
    authoritative pair used to update furniture.  Unbound furniture is never
    translated here.
    """
    if not toc_path.exists() or not furnitures_path.exists():
        return
    toc = decode_toc(read_xml(toc_path))
    refs = {
        item.id: (item.page_index, item.order)
        for item in iter_toc(toc.content)
    }
    source_text = _text_by_reference(source_chapters)
    translated_text = _text_by_reference(translated_chapters)
    titles = {
        toc_id: (source_text[ref], translated_text[ref])
        for toc_id, ref in refs.items()
        if ref in source_text and ref in translated_text
    }
    if not titles:
        return

    root = read_xml(furnitures_path)
    changed = False
    for position in root.findall("patterns/pattern/position"):
        title = _title_for(position, titles)
        if title is None:
            continue
        _source, translated = title
        if position.text != translated:
            position.text = translated
            changed = True

    for section in root.findall("pages/page/section"):
        title = _title_for(section, titles)
        if title is None or section.text is None:
            continue
        source, translated = title
        if source and source in section.text:
            section.text = section.text.replace(source, translated, 1)
            changed = True

    if changed:
        save_xml(root, furnitures_path)


def _title_for(element: Element, titles: dict[int, tuple[str, str]]) -> tuple[str, str] | None:
    raw = element.get("toc_id")
    if raw is None or not raw.isdigit():
        return None
    return titles.get(int(raw))


def _text_by_reference(chapters_path: Path) -> dict[tuple[int, int], str]:
    result: dict[tuple[int, int], str] = {}
    for path in sorted(chapters_path.glob("chapter_*.xml")):
        chapter = decode(read_xml(path))
        for layout in chapter.layouts:
            if not isinstance(layout, ParagraphLayout):
                continue
            for block in layout.blocks:
                result[(block.page_index, block.order)] = _block_text(block)
    return result


def _block_text(block: BlockLayout) -> str:
    values: list[str] = []
    for value in flatten(block.content):
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, InlineExpression):
            values.append(value.content)
    return "".join(values).strip()
