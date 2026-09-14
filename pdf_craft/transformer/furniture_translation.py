"""Translate pcex page furniture without treating it as document flow."""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree.ElementTree import Element

from pdf_craft.common import read_xml, save_xml
from pdf_craft.extractor.chapter.chapter import (
    BlockLayout,
    InlineExpression,
    ParagraphLayout,
    decode as decode_chapter,
)
from pdf_craft.extractor.toc import decode as decode_toc, iter_toc
from pdf_craft.markdown.paragraph import flatten
from .furniture import Box, FurniturePosition, FurnitureSection, FurnitureTransformer
from .translation_coverage import (
    FurniturePositionCoverage, FurnitureSectionCoverage, write_furniture_coverage,
)


_LEADER_TRAILER = re.compile(
    r"(?P<trailer>\s*[.…⋯·•._][\s.…⋯·•._,:;\-–—()\[\]]*\d+(?:[\s.…⋯·•._,:;\-–—()\[\]]*\d+)*\s*)$"
)
_TOC_NUMBER_PREFIX = re.compile(
    r"^(?P<prefix>\s*(?:"
    r"(?:\d+(?:[.．]\d+)+(?:[.．)])?|\d+[.．)])"  # 1. / 1.2 / 1)
    r"|[IVXLCDM]+[.．)]"  # I. / IV)
    r"|[A-Za-z][.．)]"  # A. / b)
    r"|[一二三四五六七八九十百千]+[、.．]"  # 一、 / 十.
    r")\s+)"
)


def translate_furnitures_in_workspace(
    *,
    chapters_path: Path,
    toc_path: Path,
    furnitures_path: Path,
    translation_path: Path,
    transformer: FurnitureTransformer,
) -> None:
    """Translate furniture and write only furniture coverage into a sidecar.

    ``chapters_path`` is already NarrativeFlow-translated.  The still-source
    furniture XML plus its ``toc_id`` is enough to resolve printed TOC rows;
    no original extraction is needed.
    """
    if not furnitures_path.exists():
        return
    root = read_xml(furnitures_path)
    titles = _translated_titles(chapters_path, toc_path)
    position_coverage: list[tuple[str, str, str]] = []
    section_coverage: list[tuple[str, str, str]] = []

    for pattern in root.findall("patterns/pattern"):
        pattern_id = pattern.get("id", "")
        kind = pattern.get("kind", "")
        for position in pattern.findall("position"):
            position_id = position.get("id", "")
            translated = _translate_position(position, pattern_id, position_id, kind, titles, transformer)
            position_coverage.append((pattern_id, position_id, translated))

    for page in root.findall("pages/page"):
        page_index = page.get("index", "")
        ordinary: list[tuple[Element, FurnitureSection]] = []
        for section in page.findall("section"):
            if len(section):
                # A physical associated section is represented by its pattern
                # position.  It has no own text or coverage identity.
                continue
            det = section.get("det", "")
            toc_id = section.get("toc_id")
            if toc_id is not None:
                state = _reconcile_toc_section(section, titles)
                section_coverage.append((page_index, det, state))
                continue
            ordinary.append((section, _as_section(page_index, det, section.text or "")))

        section_coverage.extend(
            _translate_page_sections(page_index, ordinary, transformer)
        )

    save_xml(root, furnitures_path)
    write_furniture_coverage(
        translation_path,
        (FurniturePositionCoverage(*entry) for entry in position_coverage),
        (FurnitureSectionCoverage(int(page_index), det, state) for page_index, det, state in section_coverage),
    )


def _translated_titles(chapters_path: Path, toc_path: Path) -> dict[int, str]:
    if not toc_path.exists():
        return {}
    toc = decode_toc(read_xml(toc_path))
    references = {
        item.id: (item.page_index, item.order)
        for item in iter_toc(toc.content)
    }
    texts = _text_by_reference(chapters_path)
    return {
        toc_id: texts[reference]
        for toc_id, reference in references.items()
        if reference in texts and texts[reference].strip()
    }


def _translate_position(
    position: Element,
    pattern_id: str,
    position_id: str,
    kind: str,
    titles: dict[int, str],
    transformer: FurnitureTransformer,
) -> str:
    toc_id = _integer_attribute(position, "toc_id")
    if toc_id is not None:
        title = titles.get(toc_id)
        if title is None:
            return "preserved"
        position.text = title
        return "translated"

    content = position.text or ""
    try:
        result = transformer.transform_position(
            FurniturePosition(
                pattern_id=int(pattern_id),
                position_id=int(position_id),
                kind=kind,
                content=content,
            )
        )
    except Exception:  # Isolated furniture must never discard a pcex translation.
        return "preserved"
    if not _usable_target(result):
        return "preserved"
    position.text = result
    return "translated"


def _reconcile_toc_section(section: Element, titles: dict[int, str]) -> str:
    toc_id = _integer_attribute(section, "toc_id")
    title = titles.get(toc_id) if toc_id is not None else None
    content = section.text or ""
    if title is None or not content.strip():
        return "preserved"
    title_field, trailer = _split_toc_title_field(content)
    if trailer is not None:
        section.text = _replace_toc_title_field(title_field, title) + trailer
        return "translated"
    # A section with toc_id was bound at extraction only when it was exactly a
    # headline or a headline plus a recognized page trailer.  If no leader is
    # present, accepting the entire section is safe only when it has no final
    # standalone page number to confuse with a title number.
    if not re.search(r"\s\d+\s*$", content):
        section.text = _replace_toc_title_field(content, title)
        return "translated"
    return "preserved"


def _split_toc_title_field(content: str) -> tuple[str, str | None]:
    matched = _LEADER_TRAILER.search(content)
    if matched is None:
        return content, None
    return content[:matched.start()], matched.group("trailer")


def _replace_toc_title_field(source: str, translated_title: str) -> str:
    """Keep a recognized outline number while replacing only the title field."""
    matched = _TOC_NUMBER_PREFIX.match(source)
    if matched is None:
        return translated_title
    prefix = matched.group("prefix")
    # Narrative headlines sometimes include the same structural number.  The
    # printed TOC must show it once, in its original visual form.
    if translated_title.startswith(prefix):
        translated_title = translated_title[len(prefix):]
    return prefix + translated_title.lstrip()


def _translate_page_sections(
    page_index: str,
    entries: list[tuple[Element, FurnitureSection]],
    transformer: FurnitureTransformer,
) -> list[tuple[str, str, str]]:
    if not entries:
        return []
    sections = [section for _, section in entries]
    try:
        translated = transformer.transform_sections(int(page_index), sections)
    except Exception:  # The failed page's fragments are preserved independently.
        translated = ()
    if len(translated) != len(entries):
        translated = (None,) * len(entries)

    coverage: list[tuple[str, str, str]] = []
    for (element, _section), target in zip(entries, translated):
        det = element.get("det", "")
        if _usable_target(target):
            element.text = target
            coverage.append((page_index, det, "translated"))
        else:
            coverage.append((page_index, det, "preserved"))
    return coverage


def _as_section(page_index: str, det: str, content: str) -> FurnitureSection:
    values = tuple(int(value) for value in det.split(","))
    if len(values) != 4:
        raise ValueError(f"invalid furniture section bbox: {det}")
    box: Box = (values[0], values[1], values[2], values[3])
    return FurnitureSection(int(page_index), box, content)


def _integer_attribute(element: Element, name: str) -> int | None:
    raw = element.get(name)
    return int(raw) if raw is not None and raw.isdigit() else None


def _usable_target(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _text_by_reference(chapters_path: Path) -> dict[tuple[int, int], str]:
    result: dict[tuple[int, int], str] = {}
    for path in sorted(chapters_path.glob("chapter_*.xml")):
        chapter = decode_chapter(read_xml(path))
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
