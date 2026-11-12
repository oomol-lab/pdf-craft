"""Reversible page-oriented projection of resolved chapter layouts.

This module sits between the traditional paragraph/reference resolution and
FlowItem assembly. It records decisions made by the local algorithms, orders
page observations and restores adopted streams without changing source text or
geometry. Structured-text conversion and asset association have separate owners.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from ...markdown.paragraph import HTMLTag

from .chapter import (
    DisplayFormula,
    Reference,
    Chapter,
    SourceAsset,
    SourceTextFragment,
    StandaloneAsset,
    TextFlowItem,
)
from .text_operations import propose_word_joins
from .analysis_types import (
    LayoutOwnership,
    _ReferencePlaceholder,
    _AnalysedInlineExpression,
    _AnalysedHTMLTag,
    _AnalysedContentPart,
    _AnalysedContent,
    AssetTextTarget,
    _AnalysedTextSource,
    _AnalysedAssetSource,
    _AnalysedSource,
    ReferenceLocation,
    ReferenceSpan,
    _AnalysedMark,
    _CitationMark,
    AnalysedCitation,
    UnindexedCitation,
    LayoutAnnotations,
    AnalysedLayout,
    PageAnalysis,
    RestoredStreams,
    PageStructureError,
)
from .analysis_content import (
    _project_fragment_content,
    _restore_content,
    _analyse_mark,
    analyse_text_source,
)
from .asset_text import (
    analyse_asset_source,
    expand_asset_sources,
)


# Internal facade: previous imports and local diagnostic pickles resolve here.
__all__ = [
    'LayoutOwnership',
    '_ReferencePlaceholder',
    '_AnalysedInlineExpression',
    '_AnalysedHTMLTag',
    '_AnalysedContentPart',
    '_AnalysedContent',
    'AssetTextTarget',
    '_AnalysedTextSource',
    '_AnalysedAssetSource',
    '_AnalysedSource',
    'ReferenceLocation',
    'ReferenceSpan',
    '_AnalysedMark',
    '_CitationMark',
    'AnalysedCitation',
    'UnindexedCitation',
    'LayoutAnnotations',
    'AnalysedLayout',
    'PageAnalysis',
    'RestoredStreams',
    'PageStructureError',
]


def analyse_pages(
    page_indexes: Iterable[int],
    paragraphs: Iterable[TextFlowItem | SourceAsset],
    citations: Iterable[Reference | UnindexedCitation],
    *,
    ignored_sources: Iterable[SourceTextFragment] = (),
) -> list[PageAnalysis]:
    """Project traditional resolution results back onto their source pages."""

    pages: dict[int, PageAnalysis] = {}
    for page_index in page_indexes:
        if page_index in pages:
            raise ValueError(f"Duplicate PageAnalysis page index: {page_index}")
        pages[page_index] = PageAnalysis(page_index=page_index)

    # Establish author paragraphs before projecting them back into page order.
    from .generation import _assemble_flow_items
    initial_items = list(_assemble_flow_items(paragraphs))
    from .analyse_level import analyse_chapter_internal_levels
    analyse_chapter_internal_levels(Chapter(None, -1, initial_items))
    for item in initial_items:
        if isinstance(item, (StandaloneAsset, DisplayFormula)):
            item = item.asset
        if isinstance(item, SourceAsset):
            layout = _analyse_layout(
                source=item,
                ownership="paragraph",
                continues_from_previous=False,
                continues_to_next=False,
            )
            _get_page(pages, layout.page_index).layouts.append(layout)
            continue

        text_removals = propose_word_joins(item)
        texts = [child for child in item.children if isinstance(child, SourceTextFragment)]
        for fragment in item.children:
            if isinstance(fragment, SourceAsset):
                layout = _analyse_layout(source=fragment, ownership="paragraph",
                                         continues_from_previous=False, continues_to_next=False)
                _get_page(pages, layout.page_index).layouts.append(layout)
                continue
            index = texts.index(fragment)
            layout = _analyse_layout(
                source=fragment,
                ownership="paragraph",
                continues_from_previous=index > 0,
                continues_to_next=index < len(texts) - 1,
                paragraph_role=item.role,
                paragraph_level=item.level,
                text_removals=tuple(text_removals.get(id(fragment), ())),
            )
            _get_page(pages, layout.page_index).layouts.append(layout)

    for citation_order, citation in enumerate(citations):
        citation_id = f"citation-{citation_order}"
        citation_index = (
            citation.order if isinstance(citation, Reference) else None
        )
        citation_mark = (
            _analyse_mark(citation.mark)
            if isinstance(citation, Reference)
            else None
        )
        segments: dict[int, AnalysedCitation] = {}
        _get_citation_segment(
            pages=pages,
            segments=segments,
            citation_id=citation_id,
            origin_page_index=citation.page_index,
            page_index=citation.page_index,
            citation_index=citation_index,
            citation_mark=citation_mark,
            stream_order=citation_order,
        )
        flat_items = (item.asset if isinstance(item, (StandaloneAsset, DisplayFormula)) else item
                      for item in citation.flow_items)
        for flow_item in _assemble_flow_items(flat_items):
            if isinstance(flow_item, TextFlowItem):
                text_removals = propose_word_joins(flow_item)
                texts = [child for child in flow_item.children if isinstance(child, SourceTextFragment)]
                for fragment in flow_item.children:
                    if isinstance(fragment, SourceAsset):
                        segment = _get_citation_segment(
                            pages, segments, citation_id, citation.page_index, fragment.page_index,
                            citation_index, citation_mark, citation_order)
                        _get_page(pages, fragment.page_index).layouts.append(_analyse_layout(
                            source=fragment, ownership="citation", continues_from_previous=False,
                            continues_to_next=False, citation_id=citation_id, citation_index=segment.index))
                        continue
                    index = texts.index(fragment)
                    segment = _get_citation_segment(
                        pages=pages,
                        segments=segments,
                        citation_id=citation_id,
                        origin_page_index=citation.page_index,
                        page_index=fragment.page_index,
                        citation_index=citation_index,
                        citation_mark=citation_mark,
                        stream_order=citation_order,
                    )
                    _get_page(pages, fragment.page_index).layouts.append(
                        _analyse_layout(
                            source=fragment,
                            ownership="citation",
                            continues_from_previous=index > 0,
                            continues_to_next=index < len(texts) - 1,
                            paragraph_role=flow_item.role,
                            paragraph_level=flow_item.level,
                            text_removals=tuple(text_removals.get(id(fragment), ())),
                            citation_id=citation_id,
                            citation_index=segment.index,
                        )
                    )
            else:
                source = (
                    flow_item
                    if isinstance(flow_item, SourceAsset)
                    else flow_item.asset
                )
                segment = _get_citation_segment(
                    pages=pages,
                    segments=segments,
                    citation_id=citation_id,
                    origin_page_index=citation.page_index,
                    page_index=source.page_index,
                    citation_index=citation_index,
                    citation_mark=citation_mark,
                    stream_order=citation_order,
                )
                _get_page(pages, source.page_index).layouts.append(
                    _analyse_layout(
                        source=source,
                        ownership="citation",
                        continues_from_previous=False,
                        continues_to_next=False,
                        citation_id=citation_id,
                        citation_index=segment.index,
                    )
                )

    for source in ignored_sources:
        _get_page(pages, source.page_index).layouts.append(_analyse_layout(
            source=source, ownership="ignored", continues_from_previous=False,
            continues_to_next=False,
        ))

    for page in pages.values():
        page.layouts = expand_asset_sources(page.layouts)

    for page in pages.values():
        # Reading positions were captured before splitting the OCR streams.
        # Hand-constructed/legacy fragments retain their supplied ordering.
        initial_positions = {id(layout): index for index, layout in enumerate(page.layouts)}
        def position(layout: AnalysedLayout, positions=initial_positions) -> int:
            source = layout.source
            if source.reading_order is not None:
                return source.reading_order
            return positions[id(layout)]

        page.layouts.sort(key=position)
        for index, layout in enumerate(page.layouts):
            page.layouts[index] = replace(
                layout,
                document_order=(page.page_index << 32) + index,
            )

    return list(pages.values())


def _get_page(pages: dict[int, PageAnalysis], page_index: int) -> PageAnalysis:
    if page_index not in pages:
        pages[page_index] = PageAnalysis(page_index=page_index)
    return pages[page_index]


def _get_citation_segment(
    pages: dict[int, PageAnalysis],
    segments: dict[int, AnalysedCitation],
    citation_id: str,
    origin_page_index: int,
    page_index: int,
    citation_index: int | None,
    citation_mark: _CitationMark | None,
    stream_order: int,
) -> AnalysedCitation:
    segment = segments.get(page_index)
    if segment is not None:
        return segment
    is_origin = page_index == origin_page_index
    segment = AnalysedCitation(
        citation_id=citation_id,
        page_index=page_index,
        index=citation_index if is_origin else None,
        mark=citation_mark if is_origin else None,
        stream_order=stream_order,
        origin_page_index=origin_page_index if citation_index is not None else None,
        continues_previous=not is_origin,
    )
    _get_page(pages, page_index).citations.append(segment)
    segments[page_index] = segment
    return segment


def restore_streams(pages: Iterable[PageAnalysis]) -> RestoredStreams:
    """Restore paragraph and citation streams from editable page annotations."""

    import pickle
    import sqlite3
    from tempfile import TemporaryDirectory
    from pathlib import Path
    from contextlib import closing
    from .assembly import PageAssembly
    with TemporaryDirectory(prefix='pdf-craft-restore-') as directory:
        database = Path(directory) / 'streams.sqlite'
        assembly = PageAssembly(database)
        for page in pages:
            assembly.append(page)
        assembly.finish()
        with closing(sqlite3.connect(database)) as db:
            paragraphs = [pickle.loads(row[0]) for row in db.execute(
                'SELECT value FROM bodies ORDER BY document_order,rowid')]
            citations = [pickle.loads(row[0]) for row in db.execute(
                'SELECT value FROM refs ORDER BY page,number')]
        paragraphs = [item.asset if isinstance(item, (StandaloneAsset, DisplayFormula)) else item
                      for item in paragraphs]
        # Bodies contain serialized references captured before the last note
        # fragment closed. Restore the final output entities by stable key.
        references = {item.id: item for item in citations}
        def resolve(content):
            for index, part in enumerate(content):
                if isinstance(part, Reference):
                    content[index] = references[part.id]
                elif isinstance(part, HTMLTag):
                    resolve(part.children)
        def hydrate(item):
            if isinstance(item, TextFlowItem):
                for child in item.children:
                    hydrate(child)
            elif isinstance(item, SourceTextFragment):
                resolve(item.content)
            elif isinstance(item, SourceAsset):
                for content in (item.title, item.content, item.caption):
                    resolve(content)
        for item in paragraphs:
            hydrate(item)
        return RestoredStreams(paragraphs, citations)


def _source_position_key(source) -> tuple:
    if isinstance(source, SourceTextFragment):
        return ("text", source.page_index, source.identity_order)
    if isinstance(source, _AnalysedTextSource):
        return ("text", source.page_index, source.fragment_id)
    return (source.ref, source.page_index, source.bbox)


def _analyse_layout(
    source: SourceTextFragment | SourceAsset,
    ownership: LayoutOwnership,
    continues_from_previous: bool,
    continues_to_next: bool,
    paragraph_role: str | None = None,
    paragraph_level: int | None = None,
    citation_id: str | None = None,
    citation_index: int | None = None,
    text_removals: tuple[tuple[int, int], ...] = (),
) -> AnalysedLayout:
    references: list[ReferenceLocation] = []
    analysed_source = (analyse_text_source(source, ownership, references)
                       if isinstance(source, SourceTextFragment)
                       else analyse_asset_source(source, ownership, references))
    return AnalysedLayout(
        source=analysed_source,
        document_order=-1,
        annotations=LayoutAnnotations(
            ownership=ownership,
            continues_from_previous=continues_from_previous,
            continues_to_next=continues_to_next,
            paragraph_role=paragraph_role,
            paragraph_level=paragraph_level,
            citation_id=citation_id,
            citation_index=citation_index,
            references=references,
            text_removals=text_removals,
        ),
    )


def _validate_pages(pages: list[PageAnalysis]) -> None:
    page_indexes: set[int] = set()
    document_orders: set[int] = set()
    source_identities: set[tuple[int, int]] = set()
    segments: dict[tuple[str, int], AnalysedCitation] = {}
    indexed_segments: dict[str, AnalysedCitation] = {}

    for page in pages:
        if page.page_index in page_indexes:
            raise ValueError(f"Duplicate PageAnalysis page index: {page.page_index}")
        page_indexes.add(page.page_index)

        expected_index = 1
        found_indexed = False
        for citation in page.citations:
            if citation.page_index != page.page_index:
                raise ValueError("Citation segment belongs to a different page")
            key = (citation.citation_id, citation.page_index)
            if key in segments:
                raise ValueError(f"Duplicate citation segment: {key}")
            segments[key] = citation

            if citation.index is None:
                if found_indexed:
                    raise ValueError(
                        "Unindexed citations must precede indexed citations"
                    )
                if citation.mark is not None:
                    raise ValueError("Unindexed citation cannot have a mark")
                continue

            found_indexed = True
            if citation.index != expected_index:
                raise ValueError(
                    "Page-local citation indexes must be contiguous and "
                    f"increasing from 1; expected {expected_index}, "
                    f"got {citation.index} on page {page.page_index}"
                )
            if citation.citation_id in indexed_segments:
                raise ValueError(
                    f"Citation identity has multiple indexes: {citation.citation_id}"
                )
            indexed_segments[citation.citation_id] = citation
            expected_index += 1

        for layout in page.layouts:
            if layout.page_index != page.page_index:
                raise ValueError("Analysed layout belongs to a different page")
            if layout.document_order in document_orders:
                raise ValueError(
                    f"Duplicate layout document order: {layout.document_order}"
                )
            document_orders.add(layout.document_order)
            if isinstance(layout.source, _AnalysedTextSource):
                identity = (layout.page_index, layout.source.fragment_id)
                if identity in source_identities:
                    raise ValueError(f"Duplicate fragment source identity: {identity}")
                source_identities.add(identity)

    for page in pages:
        for layout in page.layouts:
            if layout.ownership == "ignored":
                if layout.continues_from_previous or layout.continues_to_next:
                    raise ValueError("Ignored layout cannot carry continuation gaps")
                if (
                    layout.paragraph_role is not None
                    or layout.paragraph_level is not None
                    or layout.citation_id is not None
                    or layout.citation_index is not None
                    or layout.references
                ):
                    raise ValueError("Ignored layout cannot carry semantic annotations")
                continue
            if layout.ownership == "paragraph":
                if layout.citation_id is not None or layout.citation_index is not None:
                    raise ValueError(
                        "Paragraph layout cannot carry citation annotations"
                    )
                continue

            if layout.citation_id is None:
                raise ValueError("Citation layout is missing its citation identity")
            segment = segments.get((layout.citation_id, layout.page_index))
            if segment is None:
                raise ValueError(
                    "Citation layout has no page-local citation segment: "
                    f"{layout.citation_id} on page {layout.page_index}"
                )
            if layout.citation_index != segment.index:
                raise ValueError(
                    "Citation layout index disagrees with its page-local segment"
                )


def _restore_layouts(
    layouts: list[AnalysedLayout],
    references: dict[tuple[int, int], Reference],
    *, check_connections: bool = True,
) -> list[TextFlowItem | SourceAsset]:
    result: list[TextFlowItem | SourceAsset] = []
    pending: list[SourceTextFragment | SourceAsset] = []
    pending_role: str | None = None
    pending_level: int | None = None

    def flush_pending() -> None:
        nonlocal pending, pending_role, pending_level
        if not pending:
            return
        if pending_role is None or pending_level is None:
            raise PageStructureError("Text layout is missing paragraph role or level", pending[0].page_index)
        paragraph = TextFlowItem(pending_role, pending_level, pending)
        result.append(paragraph)
        pending = []
        pending_role = None
        pending_level = None

    assets: list[SourceAsset] = []
    previous: AnalysedLayout | None = None
    for layout in layouts:
        source = layout.source
        if isinstance(source, _AnalysedAssetSource):
            if layout.continues_from_previous or layout.continues_to_next:
                raise PageStructureError("Asset layouts cannot carry continuation gaps", layout.page_index, bbox=layout.bbox)
            asset = _restore_asset(layout, references)
            if asset.ref == "formula":
                if previous is not None and previous.continues_to_next:
                    raise PageStructureError("Text layout cannot continue across a display formula", layout.page_index)
                flush_pending()
                result.extend(assets)
                assets = []
                result.append(asset)
                previous = None
            else:
                assets.append(asset)
            continue

        should_continue = layout.continues_from_previous
        if previous is None:
            if should_continue:
                raise PageStructureError("First layout cannot continue from a predecessor", layout.page_index)
        elif previous.continues_to_next != should_continue:
            raise PageStructureError("Adjacent layout continuation annotations disagree", layout.page_index)

        if not should_continue:
            flush_pending()
            result.extend(assets)
            pending_role = layout.paragraph_role
            pending_level = layout.paragraph_level
        elif check_connections and (
            layout.paragraph_role != pending_role
            or layout.paragraph_level != pending_level
        ):
            raise PageStructureError("Continued text layouts disagree on paragraph metadata", layout.page_index)
        if should_continue:
            pending.extend(assets)
        assets = []
        pending.append(_restore_fragment(layout, references))
        previous = layout

    if previous is not None and previous.continues_to_next:
        raise PageStructureError("Last layout cannot continue to a successor", previous.page_index)
    flush_pending()
    result.extend(assets)
    return result


def continuation_sequences(layouts: Iterable[AnalysedLayout]) -> Iterable[list[AnalysedLayout]]:
    """Text predecessors skip image/table anchors; display formulas stop them."""
    from .asset_editing import reading_sequences
    for group in reading_sequences(layouts):
        sequence: list[AnalysedLayout] = []
        for layout in group:
            if isinstance(layout.source, _AnalysedAssetSource):
                if layout.source.ref == "formula":
                    yield sequence
                    sequence = []
            else:
                sequence.append(layout)
        yield sequence


def _restore_fragment(
    layout: AnalysedLayout,
    references: dict[tuple[int, int], Reference],
) -> SourceTextFragment:
    source = layout.source
    if not isinstance(source, _AnalysedTextSource):
        raise TypeError("Expected analysed text source")
    from .analysis_content import layout_reference_text
    edited = bool(layout.annotations.text_removals)
    return SourceTextFragment(
        page_index=source.page_index,
        source_order=source.source_order,
        bbox=source.bbox,
        content=_project_fragment_content(source, layout, references),
        source=source.origin,
        source_start=source.source_start,
        source_end=(source.source_start + len(layout_reference_text(layout))
                    if edited else source.source_end),
        source_text=(source.source_text or layout_reference_text(layout)) if edited else source.source_text,
    )


def _restore_asset(
    layout: AnalysedLayout,
    references: dict[tuple[int, int], Reference],
) -> SourceAsset:
    source = layout.source
    if not isinstance(source, _AnalysedAssetSource):
        raise TypeError("Expected analysed asset source")
    return SourceAsset(
        page_index=source.page_index,
        ref=source.ref,
        bbox=source.bbox,
        title=_restore_content(source.title, layout.references, references, (0,)),
        content=_restore_content(source.content, layout.references, references, (1,)),
        caption=_restore_content(source.caption, layout.references, references, (2,)),
        asset_hash=source.asset_hash,
        reading_order=source.reading_order,
        source=source.origin,
    )
