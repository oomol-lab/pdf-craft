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
from pdf_craft._failure_sources import furniture_section_locations, page_sources
from pdf_craft._policy import current, locations, recover, source_locations
from pdf_craft.policies import FailureCategory, FailureLocation
from .furniture import Box, FurniturePosition, FurnitureSection, FurnitureTransformer
from .progress import translation_unit
from ..progress import UnitKind
from ..common.words import count_text_words
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


async def translate_furnitures_in_workspace_async(
    *,
    chapters_path: Path,
    toc_path: Path,
    furnitures_path: Path,
    translation_path: Path,
    transformer: FurnitureTransformer,
    pages_path: Path | None = None,
) -> None:
    """Native-async furniture translation with filesystem work off-loop."""
    from ..document.package import _read_pages
    from ..document.access import page_geometries

    pages_path = pages_path if pages_path is not None else chapters_path.parent / "pages.xml"
    pages = {}
    if await IO_DOMAIN.run(pages_path.exists):
        dpi, sizes = await IO_DOMAIN.run(_read_pages, pages_path)
        pages = page_geometries(sizes, dpi)
    with page_sources(pages):
        await _translate_furnitures_async(
            chapters_path, toc_path, furnitures_path, translation_path, transformer,
        )


async def _translate_furnitures_async(
    chapters_path: Path, toc_path: Path, furnitures_path: Path,
    translation_path: Path, transformer: FurnitureTransformer,
) -> None:
    def prepare():
        if not furnitures_path.exists():
            return None
        return read_xml(furnitures_path), _translated_titles(chapters_path, toc_path)

    prepared = await IO_DOMAIN.run(prepare)
    if prepared is None:
        return
    root, titles = prepared
    position_coverage: list[tuple[str, str, str]] = []
    section_coverage: list[FurnitureSectionCoverage] = []

    for pattern in root.findall("patterns/pattern"):
        pattern_id = pattern.get("id", "")
        kind = pattern.get("kind", "")
        for position in pattern.findall("position"):
            position_id = position.get("id", "")
            occurrence_pages = tuple(dict.fromkeys(int(page.get('index', '0')) for page in root.findall('pages/page')
                if any(association.get('pattern_id') == pattern_id and association.get('position_id') == position_id
                       for association in page.findall('section/association'))))
            target = FailureLocation('template', source=current().source, member_path='furnitures.xml',
                                     unit_id=f'{pattern_id}:{position_id}', page_indexes=occurrence_pages,
                                     original=position.text)
            impact = furniture_section_locations(tuple(
                _as_section(page.get('index', '0'), section.get('det', ''), section.text or '', section.get("id"))
                for page in root.findall('pages/page') for section in page.findall('section')
                if section.get('det') and any(association.get('pattern_id') == pattern_id
                    and association.get('position_id') == position_id for association in section.findall('association'))))
            with locations((target,), affected=impact):
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
                section_coverage.append(FurnitureSectionCoverage(
                    int(page_index), det, _reconcile_toc_section(section, titles), section.get("id"),
                ))
                continue
            ordinary.append((section, _as_section(page_index, det, section.text or "", section.get("id"))))
        section_coverage.extend(await _translate_page_sections_async(
            page_index, ordinary, transformer,
        ))

    def finish():
        save_xml(root, furnitures_path)
        write_furniture_coverage(
            translation_path,
            (FurniturePositionCoverage(*entry) for entry in position_coverage),
            section_coverage,
        )

    await IO_DOMAIN.run(finish)



@translation_unit(UnitKind.FURNITURE, lambda position, *args, **kwargs:
    (count_text_words(position.get('folio_prefix', '')) + count_text_words(position.get('folio_suffix', '')))
    if position.get('folio_style') is not None else
    (count_text_words(position.text or '') if position.get('toc_id') is None else 0))
async def _translate_position_async(
    position: Element,
    pattern_id: str,
    position_id: str,
    kind: str,
    titles: dict[int, str],
    transformer: FurnitureTransformer,
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
        except Exception as error:
            await recover(error, FailureCategory.FILL, 'furniture', 'omit_translation',
                          targets=source_locations() or (FailureLocation('template', member_path='furnitures.xml',
                                    unit_id=f'{pattern_id}:{position_id}'),))
            return "preserved"
        if result is None:
            return "preserved"
        if (
            not isinstance(result, str)
            or not result.strip()
            or result.count(_FOLIO_MARKER) != 1
            or not result.replace(_FOLIO_MARKER, "").strip()
        ):
            await recover(ValueError("invalid translated folio decoration"),
                          FailureCategory.FILL, 'furniture', 'omit_translation',
                          targets=source_locations() or (FailureLocation('template', member_path='furnitures.xml',
                                    unit_id=f'{pattern_id}:{position_id}'),))
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
    except Exception as error:
        await recover(error, FailureCategory.FILL, 'furniture', 'omit_translation',
                      targets=source_locations() or (FailureLocation('template', member_path='furnitures.xml',
                                unit_id=f'{pattern_id}:{position_id}', original=position.text),))
        return "preserved"
    if result is None:
        return "preserved"
    if not _usable_target(result):
        await recover(ValueError("invalid translated furniture text"),
                      FailureCategory.FILL, 'furniture', 'omit_translation',
                      targets=source_locations() or (FailureLocation('template', member_path='furnitures.xml',
                                unit_id=f'{pattern_id}:{position_id}', original=position.text),))
        return "preserved"
    position.text = result
    return "translated"


@translation_unit(UnitKind.FURNITURE, lambda page_index, entries, transformer:
    sum(count_text_words(section.content) for _, section in entries))
async def _translate_page_sections_async(
    page_index: str,
    entries: list[tuple[Element, FurnitureSection]],
    transformer: FurnitureTransformer,
) -> list[FurnitureSectionCoverage]:
    if not entries:
        return []
    targets = furniture_section_locations([section for _, section in entries])
    try:
        translated = await transformer.transform_sections(
            int(page_index), [section for _, section in entries],
        )
        if len(translated) != len(entries):
            raise ValueError("translated furniture section count does not match source")
    except Exception as error:
        await recover(error, FailureCategory.FILL, 'furniture_sections', 'omit_translation',
                      targets=targets)
        translated = (None,) * len(entries)
    coverage = []
    for (element, _section), target, location in zip(entries, translated, targets, strict=True):
        if target is not None and not _usable_target(target):
            await recover(ValueError("invalid translated furniture section text"),
                          FailureCategory.FILL, 'furniture_sections', 'omit_translation', targets=(location,))
        det = element.get("det", "")
        if _usable_target(target):
            element.text = target
            coverage.append(FurnitureSectionCoverage(int(page_index), det, "translated", element.get("id")))
        else:
            coverage.append(FurnitureSectionCoverage(int(page_index), det, "preserved", element.get("id")))
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


def _as_section(page_index: str, det: str, content: str, section_id: str | None = None) -> FurnitureSection:
    values = tuple(int(value) for value in det.split(","))
    if len(values) != 4:
        raise ValueError(f"invalid furniture section bbox: {det}")
    box: Box = (values[0], values[1], values[2], values[3])
    return FurnitureSection(int(page_index), box, content, section_id)


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
