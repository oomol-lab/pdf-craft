"""Translate pcex page furniture without treating it as document flow."""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree.ElementTree import Element

from pdf_craft.common import read_xml, save_xml
from pdf_craft.extractor.chapter.chapter import (
    SourceTextFragment,
    InlineExpression,
    TextFlowItem,
    decode as decode_chapter,
)
from pdf_craft.extractor.toc import decode as decode_toc, iter_toc
from pdf_craft.markdown.paragraph import flatten
from pdf_craft.runtime import IO_DOMAIN
from .furniture import Box, FurniturePosition, FurnitureSection, FurnitureTransformer
from .furniture_xml import FurnitureXMLTransformer
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
_FOLIO_MARKER = "__PDF_CRAFT_FOLIO__"


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


async def translate_furnitures_in_workspace_async(
    *,
    chapters_path: Path,
    toc_path: Path,
    furnitures_path: Path,
    translation_path: Path,
    transformer: FurnitureXMLTransformer,
) -> None:
    """Native-async furniture translation with filesystem work off-loop."""
    def prepare():
        if not furnitures_path.exists():
            return None
        return read_xml(furnitures_path), _translated_titles(chapters_path, toc_path)

    prepared = await IO_DOMAIN.run(prepare)
    if prepared is None:
        return
    root, titles = prepared
    position_coverage: list[tuple[str, str, str]] = []
    section_coverage: list[tuple[str, str, str]] = []

    for pattern in root.findall("patterns/pattern"):
        pattern_id = pattern.get("id", "")
        kind = pattern.get("kind", "")
        for position in pattern.findall("position"):
            position_id = position.get("id", "")
            translated = await _translate_position_async(
                position, pattern_id, position_id, kind, titles, transformer,
            )
            position_coverage.append((pattern_id, position_id, translated))

    for page in root.findall("pages/page"):
        page_index = page.get("index", "")
        ordinary: list[tuple[Element, FurnitureSection]] = []
        for section in page.findall("section"):
            if len(section):
                continue
            det = section.get("det", "")
            if section.get("toc_id") is not None:
                section_coverage.append((
                    page_index, det, _reconcile_toc_section(section, titles),
                ))
                continue
            ordinary.append((section, _as_section(page_index, det, section.text or "")))
        section_coverage.extend(await _translate_page_sections_async(
            page_index, ordinary, transformer,
        ))

    def finish():
        save_xml(root, furnitures_path)
        write_furniture_coverage(
            translation_path,
            (FurniturePositionCoverage(*entry) for entry in position_coverage),
            (
                FurnitureSectionCoverage(int(page_index), det, state)
                for page_index, det, state in section_coverage
            ),
        )

    await IO_DOMAIN.run(finish)


async def _translate_position_async(
    position: Element,
    pattern_id: str,
    position_id: str,
    kind: str,
    titles: dict[int, str],
    transformer: FurnitureXMLTransformer,
) -> str:
    if position.get("folio_style") is not None:
        prefix = position.get("folio_prefix", "")
        suffix = position.get("folio_suffix", "")
        if not prefix and not suffix:
            return "preserved"
        try:
            result = await transformer.transform_position(FurniturePosition(
                int(pattern_id), int(position_id), kind,
                prefix + _FOLIO_MARKER + suffix,
            ))
        except Exception:
            return "preserved"
        if (
            not isinstance(result, str)
            or not result.strip()
            or result.count(_FOLIO_MARKER) != 1
            or not result.replace(_FOLIO_MARKER, "").strip()
        ):
            return "preserved"
        translated_prefix, translated_suffix = result.split(_FOLIO_MARKER)
        _set_optional_attribute(position, "folio_prefix", translated_prefix)
        _set_optional_attribute(position, "folio_suffix", translated_suffix)
        return "translated"

    toc_id = _integer_attribute(position, "toc_id")
    if toc_id is not None:
        title = titles.get(toc_id)
        if title is None:
            return "preserved"
        position.text = title
        return "translated"
    try:
        result = await transformer.transform_position(FurniturePosition(
            int(pattern_id), int(position_id), kind, position.text or "",
        ))
    except Exception:
        return "preserved"
    if not _usable_target(result):
        return "preserved"
    position.text = result
    return "translated"


async def _translate_page_sections_async(
    page_index: str,
    entries: list[tuple[Element, FurnitureSection]],
    transformer: FurnitureXMLTransformer,
) -> list[tuple[str, str, str]]:
    if not entries:
        return []
    try:
        translated = await transformer.transform_sections(
            int(page_index), [section for _, section in entries],
        )
    except Exception:
        translated = ()
    if len(translated) != len(entries):
        translated = (None,) * len(entries)
    coverage = []
    for (element, _section), target in zip(entries, translated, strict=True):
        det = element.get("det", "")
        if _usable_target(target):
            element.text = target
            coverage.append((page_index, det, "translated"))
        else:
            coverage.append((page_index, det, "preserved"))
    return coverage


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
    if position.get("folio_style") is not None:
        return _translate_folio_position(
            position, pattern_id, position_id, kind, transformer
        )
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


def _translate_folio_position(
    position: Element,
    pattern_id: str,
    position_id: str,
    kind: str,
    transformer: FurnitureTransformer,
) -> str:
    """Translate fixed folio decoration without ever submitting its value.

    The marker is preserved exactly once by a usable translation. It lets the
    target language place fixed prefix/suffix text on either side of the
    number while the PDF patcher still reconstructs the actual value from
    each physical section's page index.
    """
    prefix = position.get("folio_prefix", "")
    suffix = position.get("folio_suffix", "")
    if not prefix and not suffix:
        return "preserved"
    source = prefix + _FOLIO_MARKER + suffix
    try:
        result = transformer.transform_position(
            FurniturePosition(
                pattern_id=int(pattern_id),
                position_id=int(position_id),
                kind=kind,
                content=source,
            )
        )
    except Exception:  # A failed decoration must leave its source folio intact.
        return "preserved"
    if (
        not isinstance(result, str)
        or not result.strip()
        or result.count(_FOLIO_MARKER) != 1
        or not result.replace(_FOLIO_MARKER, "").strip()
    ):
        return "preserved"
    translated_prefix, translated_suffix = result.split(_FOLIO_MARKER)
    _set_optional_attribute(position, "folio_prefix", translated_prefix)
    _set_optional_attribute(position, "folio_suffix", translated_suffix)
    return "translated"


def _set_optional_attribute(element: Element, name: str, value: str) -> None:
    if value:
        element.set(name, value)
    else:
        element.attrib.pop(name, None)


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
        for item in chapter.flow_items:
            if not isinstance(item, TextFlowItem):
                continue
            for fragment in item.children:
                if isinstance(fragment, SourceTextFragment):
                    result[(fragment.page_index, fragment.source_order)] = _block_text(fragment)
    return result


def _block_text(block: SourceTextFragment) -> str:
    values: list[str] = []
    for value in flatten(block.content):
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, InlineExpression):
            values.append(value.content)
    return "".join(values).strip()
