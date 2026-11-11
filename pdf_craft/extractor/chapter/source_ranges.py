"""Compile source ranges into ordered semantic fragments before validation.

Offsets always refer to the immutable source's reference-text view, before
prefix stripping, reference replacement or dehyphenation. Geometry belongs to
that source; slicing never invents smaller rectangles.
"""
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .analysis_types import (
    PageAnalysis, AnalysedLayout, LayoutAnnotations, _AnalysedTextSource, _AnalysedAssetSource, _AnalysedHTMLTag,
)
from .analysis_content import layout_reference_text, _visible_content_text, _materialize_reference_text
from .text_locator import locate_text
from .asset_editing import editable_asset_sources


@dataclass(frozen=True)
class RepairSources:
    """Codec catalog with no initial decisions or citation definitions."""
    page_index: int
    layouts: tuple[AnalysedLayout, ...]


def repair_sources(page: PageAnalysis | RepairSources) -> RepairSources:
    """Transport original content/coordinates, without initial semantic decisions.

    This catalog is a codec input, not an acceptance snapshot. Old reference
    placeholders become their literal text; citation origins and ownership are
    discarded. The validator never receives this catalog.
    """
    if isinstance(page, RepairSources):
        return page
    page = editable_asset_sources(page)
    layouts = []
    for layout in page.layouts:
        source = layout.source
        if isinstance(source, _AnalysedTextSource):
            source = replace(source, content=_materialize_reference_text(source.content),
                             citation_prefix=0, citation_prefix_parts=0)
        elif isinstance(source, _AnalysedAssetSource):
            source = replace(source, title=_materialize_reference_text(source.title),
                             content=_materialize_reference_text(source.content),
                             caption=_materialize_reference_text(source.caption))
        layouts.append(replace(layout, source=source, annotations=LayoutAnnotations(
            'ignored', False, False, keep_asset_attachment=layout.asset_text_target is not None)))
    return RepairSources(page.page_index, tuple(layouts))


class SourceBoundary(BaseModel):
    model_config = ConfigDict(extra='forbid')
    left: str
    right: str


class SourceRange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: Literal['source_start'] | SourceBoundary
    end: Literal['source_end'] | SourceBoundary


def _boundary(text, value):
    if value == 'source_start':
        return 0
    if value == 'source_end':
        return len(text)
    matches = locate_text(text, value.left, '', value.right)
    if len(matches) != 1:
        raise ValueError('[SOURCE_RANGE] Boundary must identify exactly one original position')
    return matches[0][0]


def slice_content(content, start, end):
    """Retain rich wrappers while rejecting cuts through atomic expressions."""
    materialized = _materialize_reference_text(content)
    total = len(_visible_content_text(materialized))
    if start == 0 and end == total:
        return materialized
    result, offset = [], 0
    for part in materialized:
        length = len(part) if isinstance(part, str) else len(_visible_content_text((part,)))
        lo, hi = max(start, offset), min(end, offset + length)
        if length == 0 and start <= offset and (offset < end or offset == end == total):
            result.append(part)
        if lo < hi:
            if isinstance(part, str):
                result.append(part[lo-offset:hi-offset])
            elif isinstance(part, _AnalysedHTMLTag):
                result.append(replace(part, children=slice_content(part.children, lo-offset, hi-offset)))
            elif lo == offset and hi == offset + length:
                result.append(part)
            else:
                raise ValueError('[SOURCE_RANGE] A source cut cannot split an atomic expression')
        offset += length
    return tuple(result)


def compile_source_ranges(source, response, layout_id, resolve_anchor):
    """Resolve all ownership ranges and reference annotations atomically.

    The response array is the semantic order. Each entry directly references
    an original source and optionally a range; there is no second partition
    table for the model to keep aligned.
    """
    originals = {layout_id(item): item for item in source.layouts}
    consumed = defaultdict(list)
    expanded, proposals, source_members = [], [], defaultdict(list)
    base_order = min((item.document_order for item in source.layouts), default=0)
    for number, proposal in enumerate(response.layouts):
        original = originals.get(proposal.layout_id)
        if original is None:
            raise ValueError(f'[IMMUTABLE_LAYOUTS] Unknown source {proposal.layout_id}')
        ident = proposal.layout_id
        if isinstance(original.source, _AnalysedTextSource):
            text = layout_reference_text(original)
            bounds = proposal.source_range
            start, end = (0, len(text)) if bounds is None else (_boundary(text, bounds.start), _boundary(text, bounds.end))
            if not (0 <= start <= end <= len(text)) or start == end and text:
                raise ValueError(f'[SOURCE_RANGE] Invalid range in {ident}')
            consumed[ident].append((start, end))
            sliced = bounds is not None and (start, end) != (0, len(text))
            if sliced and proposal.preserve_as_image:
                raise ValueError('[SOURCE_RANGE] A partial text range cannot become a whole-source artwork')
            unique = f'{ident}@{start}:{end}' if sliced else ident
            record = replace(original.source, content=slice_content(original.source.content, start, end),
                             source_start=original.source.source_start + start,
                             source_end=original.source.source_start + end if bounds is not None else original.source.source_end,
                             source_text=(original.source.source_text if bounds is None else original.source.source_text or text),
                             citation_prefix=0, citation_prefix_parts=0)
            item = replace(original, source=record, document_order=base_order + number, source_id=unique)
            source_members[ident].append((start, end, unique, item))
        else:
            if proposal.source_range is not None:
                raise ValueError('[SOURCE_RANGE] Assets cannot be sliced')
            consumed[ident].append((0, 1))
            unique = ident
            item = replace(original, document_order=base_order + number, source_id=unique)
        expanded.append(item)
        proposals.append(proposal.model_copy(update={'layout_id': unique, 'source_range': None}))
    if set(consumed) != set(originals):
        raise ValueError('[IMMUTABLE_LAYOUTS] Return every source, including ignored content')
    for ident, intervals in consumed.items():
        original = originals[ident]
        end = len(layout_reference_text(original)) if isinstance(original.source, _AnalysedTextSource) else 1
        cursor = 0
        for left, right in sorted(intervals):
            if left != cursor:
                raise ValueError(f'[SOURCE_RANGE] Overlap or missing content in {ident} at {cursor}')
            cursor = right
        if cursor != end or len(intervals) != len(set(intervals)):
            raise ValueError(f'[SOURCE_RANGE] Incomplete or duplicate ownership in {ident}')
    references = []
    for ref in response.references:
        original = originals.get(ref.layout_id)
        if original is None:
            raise ValueError('[REF_LAYOUT_UNKNOWN] Reference must name an original text source')
        text = layout_reference_text(original)
        resolved, error = resolve_anchor(text, ref.anchor, 'reference.anchor', ref.layout_id)
        if error:
            raise ValueError(error)
        start, end = resolved
        members = [member for member in source_members[ref.layout_id] if member[0] <= start and end <= member[1]]
        if len(members) != 1:
            raise ValueError('[SOURCE_RANGE] Reference must belong to one semantic fragment')
        left, _, ident, item = members[0]
        part_text = layout_reference_text(item)
        anchor = ref.anchor.model_copy(update={'before': part_text[:start-left],
                                               'mark': part_text[start-left:end-left],
                                               'after': part_text[end-left:]})
        references.append(ref.model_copy(update={'layout_id': ident, 'anchor': anchor}))
    return (PageAnalysis(source.page_index, expanded, []),
            response.model_copy(update={'layouts': proposals, 'references': references}))
