"""Local asset-text candidates, effective adopted links and output slots.

Candidate discovery never deletes a text source. A link becomes effective only
when adopted ownership agrees; output materialization leaves that adopted page
unchanged. Review and repair expose the same candidate description.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Generator, Iterable, Literal, Mapping, cast

from ...common import ASSET_TAGS, AssetRef
from ...expression import ExpressionKind, ParsedItem, parse_latex_expressions
from ...pdf import TITLE_TAGS, PageLayout
from .chapter import SourceAsset, SourceTextFragment, TextSource, TextFlowItem, _AssetTextProjection
from .content import _parse_block_content
from .analysis_content import _analyse_content, _collect_reference_locations, analyse_text_source
from .analysis_types import (
    AnalysedLayout, AssetTextTarget, LayoutAnnotations, LayoutOwnership, PageAnalysis,
    ReferenceLocation, _AnalysedAssetSource, _AnalysedTextSource,
)


_ASSET_CAPTION_TAGS = tuple(f"{t}_caption" for t in ASSET_TAGS)

_MARKDOWN_HEAD_PATTERN = re.compile(r"^#+\s+")
_TABLE_PATTERN = re.compile(r"<table[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_TABLE_TITLE_PATTERN = re.compile(
    r"^\s*(?:table|tab\.?|表|表格)\s*[\d一二三四五六七八九十ivxlcdm]+[\s.:：、-]",
    re.IGNORECASE,
)
_TABLE_CAPTION_PATTERN = re.compile(
    r"^\s*(?:source|sources|note|notes|资料来源|来源|注|备注)\s*[:：]",
    re.IGNORECASE,
)
_FOOTNOTE_PATTERN = re.compile(
    r"^\s*(?:\d{1,2}[\).、]|[a-z][\).、]|[①-⑳]|[¹²³⁴⁵⁶⁷⁸⁹⁰]+)\s+",
    re.IGNORECASE,
)

_MAX_TABLE_TITLE_CHARS = 180
_MAX_TABLE_CAPTION_CHARS = 260


@dataclass
class _AssetHolder:
    page_index: int
    ref: AssetRef
    det: tuple[int, int, int, int]
    title: str | None
    content: str
    caption: str | None
    hash: str | None
    source: TextSource | None = None
    associated_texts: list[tuple[Literal["title", "caption"], SourceTextFragment]] = field(default_factory=list)


@dataclass
class _PendingParagraph:
    source: PageLayout
    paragraph: TextFlowItem


class AssetLayoutParser:
    """Parse a reading serial while retaining every caption's source identity."""

    def __init__(self, sources: Mapping[tuple[int, int], tuple[int, TextSource]],
                 reading_positions: Mapping[tuple[int, tuple[int, int, int, int]], int]) -> None:
        self._sources = sources
        self._reading_positions = reading_positions

    def parse(
        self, page_index: int, layouts: list[PageLayout]
    ) -> Generator[TextFlowItem | SourceAsset, None, None]:
        # layout 可能被后续处理，必须等待所有 layout 处理完毕
        for layout in list(
            self._join_asset_layouts(
                page_index=page_index,
                layouts=layouts,
            )
        ):
            if not isinstance(layout, _AssetHolder):
                yield layout
                continue

            if layout.ref == "equation":
                _normalize_equation(layout)
            if layout.ref == "table":
                _normalize_table(layout)

            intrinsic_title = _parse_block_content(layout.title)
            intrinsic_caption = _parse_block_content(
                (layout.caption or layout.content) if layout.ref == "image" else layout.caption)
            title_parts = [fragment.content for slot, fragment in layout.associated_texts if slot == 'title']
            caption_parts = [fragment.content for slot, fragment in layout.associated_texts if slot == 'caption']
            yield SourceAsset(
                page_index=page_index,
                ref="formula" if layout.ref == "equation" else layout.ref,
                bbox=layout.det,
                title=_join_asset_text([*title_parts, intrinsic_title]),
                content=[] if layout.ref == "image" else _parse_block_content(layout.content),
                caption=_join_asset_text([intrinsic_caption, *caption_parts]),
                asset_hash=layout.hash,
                reading_order=self._reading_positions.get((page_index, layout.det)),
                source=layout.source,
                text_projection=(_AssetTextProjection(intrinsic_title, intrinsic_caption,
                                 tuple(layout.associated_texts)) if layout.associated_texts else None),
            )


    def _text_paragraph(self, page_index: int, layout: PageLayout) -> TextFlowItem:
        if layout.ref in TITLE_TAGS:
            layout.text = _MARKDOWN_HEAD_PATTERN.sub("", layout.text)
        return TextFlowItem(
            role="heading" if layout.ref in TITLE_TAGS else "body", level=-1,
            children=[SourceTextFragment(
                page_index, self._sources.get((page_index, id(layout)), (layout.order, None))[0],
                layout.det, _parse_block_content(layout.text),
                self._sources.get((page_index, id(layout)), (0, None))[1])])


    def _join_asset_layouts(self, page_index: int, layouts: list[PageLayout]):
        last_asset: _AssetHolder | None = None
        pending_paragraph: _PendingParagraph | None = None

        def flush_pending_paragraph():
            nonlocal pending_paragraph
            if pending_paragraph:
                pending = pending_paragraph
                pending_paragraph = None
                return pending.paragraph
            return None

        for layout in layouts:
            if layout.ref in ASSET_TAGS:
                pending_title: SourceTextFragment | None = None
                if pending_paragraph:
                    if layout.ref == "table" and _can_join_table_title(
                        pending_paragraph.source, layout
                    ):
                        pending_title = cast(SourceTextFragment, pending_paragraph.paragraph.children[0])
                        pending_paragraph = None
                    else:
                        pending = flush_pending_paragraph()
                        if pending:
                            yield pending
                if last_asset:
                    yield last_asset
                last_asset = _AssetHolder(
                    page_index=page_index,
                    ref=layout.ref,
                    det=layout.det,
                    title=None,
                    content=layout.text,
                    caption=None,
                    hash=layout.hash,
                    source=(self._sources[(page_index, id(layout))][1]
                            if (page_index, id(layout)) in self._sources else None),
                    associated_texts=[] if pending_title is None else [('title', pending_title)],
                )
            elif layout.ref in _ASSET_CAPTION_TAGS:
                pending = flush_pending_paragraph()
                if pending:
                    yield pending
                if (last_asset and layout.ref == last_asset.ref + '_caption'
                        and _is_block_below(last_asset.det, layout.det)
                        and _is_close_to_table(last_asset.det, layout.det)
                        and _is_horizontally_related(layout.det, last_asset.det)):
                    last_asset.associated_texts.append(('caption', cast(SourceTextFragment, self._text_paragraph(page_index, layout).children[0])))
                else:
                    if last_asset:
                        yield last_asset
                        last_asset = None
                    # Preceding, isolated or unrelated captions remain independent
                    # OCR sources rather than disappearing on a failed association.
                    yield self._text_paragraph(page_index, layout)
            else:
                if (
                    last_asset
                    and last_asset.ref == "table"
                    and _can_join_table_caption(last_asset, layout)
                ):
                    last_asset.associated_texts.append(('caption', cast(SourceTextFragment, self._text_paragraph(page_index, layout).children[0])))
                    continue

                if last_asset:
                    yield last_asset
                    last_asset = None

                pending = flush_pending_paragraph()
                if pending:
                    yield pending

                paragraph = self._text_paragraph(page_index, layout)
                if _can_wait_for_table_title(layout):
                    pending_paragraph = _PendingParagraph(
                        source=layout,
                        paragraph=paragraph,
                    )
                else:
                    yield paragraph
        if last_asset:
            yield last_asset
        pending = flush_pending_paragraph()
        if pending:
            yield pending


def _normalize_equation(layout: _AssetHolder):
    if layout.ref != "equation" or not layout.content:
        return

    found_first_expression: bool = False
    expression_content: str = ""
    prefix_texts: list[str] = []
    tail_items: list[ParsedItem] = []

    for item in parse_latex_expressions(layout.content):
        if not found_first_expression and item.kind != ExpressionKind.TEXT:
            expression_content = item.content
            found_first_expression = True
        elif found_first_expression:
            tail_items.append(item)
        else:
            prefix_texts.append(item.content)

    if not found_first_expression:
        return

    if layout.title is not None:
        prefix_texts.insert(0, layout.title)

    if layout.caption is not None:
        tail_items.append(ParsedItem(kind=ExpressionKind.TEXT, content=layout.caption))

    if prefix_texts:
        layout.title = "".join(prefix_texts)

    layout.content = expression_content

    if tail_items:
        layout.caption = "".join(item.reverse() for item in tail_items)


def _normalize_table(layout: _AssetHolder):
    found_table_content: str | None = None
    head_buffer: list[str] = []
    tail_buffer: list[str] = []

    for part in (layout.title, "\n", layout.content, "\n", layout.caption):
        if not part:
            continue

        table_match = _TABLE_PATTERN.search(part)
        if not table_match:
            if found_table_content is None:
                head_buffer.append(part)
            else:
                tail_buffer.append(part)
            continue

        table_start = table_match.start()
        table_end = table_match.end()

        table_content = part[table_start:table_end]
        before = part[:table_start].rstrip()
        after = part[table_end:].lstrip()

        if before.strip():
            head_buffer.append(before)
        if after.strip():
            tail_buffer.append(after)

        found_table_content = table_content

    if not found_table_content:
        return

    head = "".join(head_buffer).strip()
    tail = "".join(tail_buffer).strip()

    layout.title = head if head else None
    layout.caption = tail if tail else None
    layout.content = found_table_content


def _join_asset_text(parts):
    result = []
    for part in parts:
        if not part:
            continue
        if result:
            if isinstance(result[-1], str) and isinstance(part[0], str):
                result[-1] += "\n" + part[0]
                result.extend(part[1:])
                continue
            result.append("\n")
        result.extend(part)
    return result


def _can_wait_for_table_title(layout: PageLayout) -> bool:
    if layout.ref in TITLE_TAGS:
        return False
    return _is_table_title_text(layout.text)


def _can_join_table_title(text_layout: PageLayout, table_layout: PageLayout) -> bool:
    if not _is_table_title_text(text_layout.text):
        return False
    if not _is_block_above(text_layout.det, table_layout.det):
        return False
    if not _is_close_to_table(text_layout.det, table_layout.det):
        return False
    return _is_horizontally_related(text_layout.det, table_layout.det)


def _can_join_table_caption(asset: _AssetHolder, text_layout: PageLayout) -> bool:
    if not _is_table_caption_text(text_layout.text):
        return False
    if not _is_block_below(asset.det, text_layout.det):
        return False
    if not _is_close_to_table(asset.det, text_layout.det):
        return False
    return _is_horizontally_related(text_layout.det, asset.det)


def _is_table_title_text(text: str) -> bool:
    normalized = _normalize_table_adjacent_text(text)
    if not normalized or len(normalized) > _MAX_TABLE_TITLE_CHARS:
        return False
    if re.search(r"\r?\n[ \t\r]*\n", text):
        return False
    return bool(_TABLE_TITLE_PATTERN.search(normalized)) or normalized.endswith((":", "："))


def _is_table_caption_text(text: str) -> bool:
    normalized = _normalize_table_adjacent_text(text)
    if not normalized or len(normalized) > _MAX_TABLE_CAPTION_CHARS:
        return False
    if re.search(r"\r?\n[ \t\r]*\n", text):
        return False
    return bool(
        _TABLE_CAPTION_PATTERN.search(normalized)
        or _FOOTNOTE_PATTERN.search(normalized)
        or normalized.lower() == "category not applicable."
    )


def _normalize_table_adjacent_text(text: str) -> str:
    return " ".join(text.strip().split())


def _is_block_above(block_det, table_det) -> bool:
    return block_det[3] <= table_det[1]


def _is_block_below(table_det, block_det) -> bool:
    return table_det[3] <= block_det[1]


def _is_close_to_table(det1, det2) -> bool:
    vertical_gap = max(det1[1], det2[1]) - min(det1[3], det2[3])
    if vertical_gap < 0:
        return False

    table_height = max(_height(det1), _height(det2))
    return vertical_gap <= max(30, int(table_height * 0.12))


def _is_horizontally_related(text_det, table_det) -> bool:
    text_width = _width(text_det)
    table_width = _width(table_det)
    if text_width <= 0 or table_width <= 0:
        return False

    overlap = max(0, min(text_det[2], table_det[2]) - max(text_det[0], table_det[0]))
    overlap_ratio = overlap / min(text_width, table_width)
    width_ratio = min(text_width, table_width) / max(text_width, table_width)
    center_distance = abs(
        (text_det[0] + text_det[2]) / 2 - (table_det[0] + table_det[2]) / 2
    )

    return overlap_ratio >= 0.6 or (
        width_ratio >= 0.75 and center_distance <= table_width * 0.2
    )


def _width(det) -> int:
    return max(0, det[2] - det[0])


def _height(det) -> int:
    return max(0, det[3] - det[1])


def active_asset_text(layouts: Iterable[AnalysedLayout]) -> dict[int, AnalysedLayout]:
    """Map active text-member identities to their same-stream local asset."""
    items = list(layouts)
    assets = {(item.page_index, item.source.ref, item.bbox): item for item in items
              if isinstance(item.source, _AnalysedAssetSource) and item.ownership != 'ignored'}
    active = {}
    for item in items:
        target = item.asset_text_target
        if (target is None or not item.annotations.keep_asset_attachment
                or item.ownership == 'ignored' or item.annotations.preserve_as_image):
            continue
        asset = assets.get((target.page_index, target.ref, target.bbox))
        if (asset is not None and (asset.ownership, asset.citation_id) ==
                (item.ownership, item.citation_id)):
            active[id(item)] = asset
    return active


def materialize_asset_text(page: PageAnalysis) -> PageAnalysis:
    """Build output slots without mutating the adopted, source-complete page."""
    active = active_asset_text(page.layouts)
    if not active:
        return page
    members: dict[int, list[AnalysedLayout]] = {}
    for item in page.layouts:
        if id(item) in active:
            members.setdefault(id(active[id(item)]), []).append(item)
    layouts = []
    for item in page.layouts:
        if id(item) in active:
            continue
        attached = members.get(id(item), [])
        if not attached:
            layouts.append(item)
            continue
        source = item.source
        assert isinstance(source, _AnalysedAssetSource)
        title, caption = list(source.title), list(source.caption)
        prefixes = []
        for member in sorted(attached, key=lambda value: value.document_order):
            assert isinstance(member.source, _AnalysedTextSource)
            target = member.source.asset_text_target
            assert target is not None
            if target.slot == 'title':
                prefixes.append(member.source.content)
            else:
                if caption:
                    caption.append('\n')
                caption.extend(member.source.content)
        for prefix in reversed(prefixes):
            title = [*prefix, *(['\n'] if title else []), *title]
        # Keep the canonical adjacent string shape, preserving structured parts.
        def compact(parts):
            result = []
            for part in parts:
                if isinstance(part, str) and result and isinstance(result[-1], str):
                    result[-1] += part
                else:
                    result.append(part)
            return tuple(result)
        projected = replace(source, title=compact(title), caption=compact(caption), associated_sources=())
        references = []
        for field_index, field_content in enumerate((projected.title, projected.content, projected.caption)):
            _collect_reference_locations(field_content, references, (field_index,))
        layouts.append(replace(item, source=projected, annotations=replace(item.annotations, references=references)))
    return replace(page, layouts=layouts)


def analyse_asset_source(source: SourceAsset, ownership: LayoutOwnership,
                         references: list[ReferenceLocation]) -> _AnalysedAssetSource:
    """Keep intrinsic slots separate from independently editable text sources."""
    projection = source.text_projection
    associated = []
    if projection is not None:
        for slot, member in projection.members:
            projected = analyse_text_source(member, ownership, [])
            associated.append(replace(projected, asset_text_target=AssetTextTarget(
                source.page_index, source.ref, source.bbox, slot)))
    return _AnalysedAssetSource(
        page_index=source.page_index,
        ref=source.ref,
        bbox=source.bbox,
        title=_analyse_content(source.title if projection is None else projection.title, references, (0,)),
        content=_analyse_content(source.content, references, (1,)),
        caption=_analyse_content(source.caption if projection is None else projection.caption, references, (2,)),
        asset_hash=source.asset_hash,
        reading_order=source.reading_order,
        origin=source.source,
        associated_sources=tuple(associated),
    )


def asset_text_packet(layout: AnalysedLayout, *, include_page: bool = False) -> dict | None:
    """Describe the candidate and requested retention, not effective ownership."""
    target = layout.asset_text_target
    if target is None:
        return None
    packet: dict = {'asset_page_index': target.page_index} if include_page else {}
    packet.update(asset_ref=target.ref, asset_bbox=list(target.bbox),
                  slot=target.slot, retained=layout.annotations.keep_asset_attachment)
    return packet


def compile_asset_attachment(original: AnalysedLayout, ownership: LayoutOwnership,
                             preserve_as_image: bool, requested: bool | None) -> bool:
    """Compile a local retention request; cross-layout agreement is checked later."""
    if requested and original.asset_text_target is None:
        raise ValueError('cannot attach a source with no original local asset target')
    if ownership == 'ignored' or preserve_as_image:
        return False
    return original.annotations.keep_asset_attachment if requested is None else requested


def detach_invalid_asset_text(layouts: Iterable[AnalysedLayout]) -> None:
    """Ownership edits implicitly detach obsolete links without a model retry."""
    items = list(layouts)
    attached = active_asset_text(items)
    for layout in items:
        if layout.annotations.keep_asset_attachment and id(layout) not in attached:
            layout.annotations.keep_asset_attachment = False


def translatable_asset(asset: SourceAsset) -> SourceAsset:
    """Only image title/caption are translatable, regardless of field presence."""
    if asset.ref == 'image':
        return replace(asset, content=[])
    return asset


def expand_asset_sources(layouts: list[AnalysedLayout]) -> list[AnalysedLayout]:
    """Expose provisional caption members as independent editable observations."""
    members = []
    for layout in layouts:
        if isinstance(layout.source, _AnalysedAssetSource):
            for member in layout.source.associated_sources:
                members.append(AnalysedLayout(member, -1, LayoutAnnotations(
                    ownership=layout.ownership, continues_from_previous=False,
                    continues_to_next=False, paragraph_role='body', paragraph_level=-1,
                    citation_id=layout.citation_id, citation_index=layout.citation_index,
                    keep_asset_attachment=True)))
    return [*layouts, *members]
