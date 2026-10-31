"""Reversible page-oriented projection of resolved chapter layouts.

This module sits between the traditional paragraph/reference resolution and
FlowItem assembly.  It deliberately contains no inference: the first version
only records the decisions already made by the existing algorithms and can
restore those decisions without changing source text or geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Iterable, Literal, TypeAlias

from ...expression import ExpressionKind
from ...markdown.paragraph import HTMLTag, tag_definition
from .chapter import (
    DisplayFormula,
    InlineExpression,
    Reference,
    SourceAsset,
    SourceTextFragment,
    StandaloneAsset,
    TextFlowItem,
    TextSource,
)
from .mark import Mark, NumberClass, NumberStyle
from .jointer import normalize_source_projection


LayoutOwnership: TypeAlias = Literal["paragraph", "citation", "ignored"]


@dataclass(frozen=True)
class _ReferencePlaceholder:
    """Immutable position occupied by a reference in source content."""

    text: str
    citation_page_index: int
    citation_index: int


@dataclass(frozen=True)
class _AnalysedInlineExpression:
    kind: ExpressionKind
    content: str


@dataclass(frozen=True)
class _AnalysedHTMLTag:
    name: str
    attributes: tuple[tuple[str, str], ...]
    children: tuple[_AnalysedContentPart, ...]


_AnalysedContentPart: TypeAlias = (
    str | _ReferencePlaceholder | _AnalysedInlineExpression | _AnalysedHTMLTag
)
_AnalysedContent: TypeAlias = tuple[_AnalysedContentPart, ...]


@dataclass(frozen=True)
class _AnalysedTextSource:
    page_index: int
    source_order: int
    bbox: tuple[int, int, int, int]
    content: _AnalysedContent
    reading_order: int | None = None
    citation_prefix: int = 0
    origin: TextSource | None = None
    citation_prefix_parts: int = 0


@dataclass(frozen=True)
class _AnalysedAssetSource:
    page_index: int
    ref: Literal["image", "table", "formula"]
    bbox: tuple[int, int, int, int]
    title: _AnalysedContent
    content: _AnalysedContent
    caption: _AnalysedContent
    asset_hash: str | None
    reading_order: int | None = None
    origin: TextSource | None = None


_AnalysedSource: TypeAlias = _AnalysedTextSource | _AnalysedAssetSource


@dataclass
class ReferenceLocation:
    """A reference annotation at a stable path within a text layout."""

    path: tuple[int, ...]
    citation_page_index: int
    citation_index: int


@dataclass(frozen=True)
class ReferenceSpan:
    """A validated visible-text range compiled into a reference placeholder."""

    start: int
    end: int
    citation_page_index: int
    citation_index: int
    mark_text: str


@dataclass(frozen=True)
class _AnalysedMark:
    number: int
    char: str
    clazz: NumberClass
    style: NumberStyle


_CitationMark: TypeAlias = str | _AnalysedMark


@dataclass
class AnalysedCitation:
    """One page-local segment of a logical citation."""

    citation_id: str
    page_index: int
    index: int | None
    mark: _CitationMark | None
    stream_order: int
    origin_page_index: int | None = None
    continues_previous: bool = False

    @property
    def state(self) -> Literal["definition", "continuation", "unresolved"]:
        if self.index is not None:
            return "definition"
        return "continuation" if self.origin_page_index is not None else "unresolved"


@dataclass(frozen=True)
class UnindexedCitation:
    """Footnote content retained even though the legacy split omitted it."""

    page_index: int
    flow_items: tuple[SourceAsset | TextFlowItem, ...]


@dataclass
class LayoutAnnotations:
    """The editable decisions attached to one immutable source layout."""

    ownership: LayoutOwnership
    continues_from_previous: bool
    continues_to_next: bool
    paragraph_role: str | None = None
    paragraph_level: int | None = None
    citation_id: str | None = None
    citation_index: int | None = None
    references: list[ReferenceLocation] = field(default_factory=list)
    preserve_as_image: bool = False


@dataclass(frozen=True)
class AnalysedLayout:
    """An immutable source layout decorated with editable resolution facts."""

    source: _AnalysedSource
    document_order: int
    annotations: LayoutAnnotations

    @property
    def page_index(self) -> int:
        return self.source.page_index

    @property
    def source_order(self) -> int | None:
        if isinstance(self.source, _AnalysedTextSource):
            return self.source.source_order
        return None

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.source.bbox

    @property
    def ownership(self) -> LayoutOwnership:
        return self.annotations.ownership

    @property
    def continues_from_previous(self) -> bool:
        return self.annotations.continues_from_previous

    @property
    def continues_to_next(self) -> bool:
        return self.annotations.continues_to_next

    @property
    def paragraph_role(self) -> str | None:
        return self.annotations.paragraph_role

    @property
    def paragraph_level(self) -> int | None:
        return self.annotations.paragraph_level

    @property
    def citation_id(self) -> str | None:
        return self.annotations.citation_id

    @property
    def citation_index(self) -> int | None:
        return self.annotations.citation_index

    @property
    def references(self) -> list[ReferenceLocation]:
        return self.annotations.references


@dataclass
class PageAnalysis:
    """Internal, in-memory analysis of one OCR page."""

    page_index: int
    layouts: list[AnalysedLayout] = field(default_factory=list)
    citations: list[AnalysedCitation] = field(default_factory=list)


def layout_reference_text(layout: AnalysedLayout) -> str:
    """Return the exact visible text surface used to locate paragraph refs."""

    source = layout.source
    if not isinstance(source, _AnalysedTextSource):
        raise ValueError("References can only be located in text layouts")
    return _visible_content_text(source.content)


def layout_reference_spans(layout: AnalysedLayout) -> list[ReferenceSpan]:
    """Return existing ref locations on the same visible-text surface."""

    source = layout.source
    if not isinstance(source, _AnalysedTextSource):
        return []
    spans: list[ReferenceSpan] = []
    offset = 0

    def visit(parts: _AnalysedContent) -> None:
        nonlocal offset
        for part in parts:
            if isinstance(part, str):
                offset += len(part)
            elif isinstance(part, _ReferencePlaceholder):
                start = offset
                offset += len(part.text)
                spans.append(ReferenceSpan(
                    start=start,
                    end=offset,
                    citation_page_index=part.citation_page_index,
                    citation_index=part.citation_index,
                    mark_text=part.text,
                ))
            elif isinstance(part, _AnalysedInlineExpression):
                offset += len(part.content)
            else:
                visit(part.children)

    visit(source.content)
    return spans


def replace_layout_references(
    layout: AnalysedLayout,
    spans: Iterable[ReferenceSpan],
) -> AnalysedLayout:
    """Compile validated visible ranges back into immutable content paths."""

    source = layout.source
    if not isinstance(source, _AnalysedTextSource):
        raise ValueError("References can only be placed in text layouts")

    materialized = _materialize_reference_text(source.content)
    visible_text = _visible_content_text(materialized)
    ordered = sorted(spans, key=lambda span: (span.start, span.end))
    previous_end = -1
    for span in ordered:
        if span.start < 0 or span.end < span.start or span.end > len(visible_text):
            raise ValueError(
                f"Reference span is outside layout text: {span.start}..{span.end}"
            )
        if span.start < previous_end:
            raise ValueError("Reference spans cannot overlap")
        if visible_text[span.start:span.end] != span.mark_text:
            raise ValueError(
                "Reference span mark does not match layout text: "
                f"{visible_text[span.start:span.end]!r} != {span.mark_text!r}"
            )
        previous_end = span.end

    leaves = _content_text_leaves(materialized)
    replacements: dict[tuple[int, ...], list[tuple[int, int, ReferenceSpan]]] = {}
    for span in ordered:
        matched = _leaf_for_span(leaves, span.start, span.end)
        if matched is None:
            raise ValueError(
                "Reference span crosses a structured content boundary: "
                f"{span.start}..{span.end}"
            )
        path, leaf_start, _leaf_end = matched
        replacements.setdefault(path, []).append(
            (span.start - leaf_start, span.end - leaf_start, span)
        )

    rebuilt = _replace_content_text_leaves(materialized, replacements)
    references: list[ReferenceLocation] = []
    _collect_reference_locations(rebuilt, references)
    return replace(
        layout,
        source=replace(source, content=rebuilt),
        annotations=replace(layout.annotations, references=references),
    )


@dataclass
class RestoredStreams:
    """The two logical streams recovered from page analyses."""

    paragraphs: list[TextFlowItem | SourceAsset]
    citations: list[Reference]


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
    for item in _assemble_flow_items(paragraphs):
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

    document_order = 0
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
                document_order=document_order,
            )
            document_order += 1

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
    )
    _get_page(pages, page_index).citations.append(segment)
    segments[page_index] = segment
    return segment


def restore_streams(pages: Iterable[PageAnalysis]) -> RestoredStreams:
    """Restore paragraph and citation streams from editable page annotations."""

    page_list = list(pages)
    _validate_pages(page_list)
    citation_annotations = [
        citation for page in page_list for citation in page.citations
        if citation.index is not None
    ]
    citation_annotations.sort(key=lambda citation: (citation.page_index, citation.index or 0))

    citation_layouts = [
        layout
        for page in page_list
        for layout in page.layouts
        if layout.ownership == "citation"
    ]
    citation_layouts.sort(key=lambda layout: layout.document_order)

    references: list[Reference] = []
    reference_map: dict[tuple[int, int], Reference] = {}
    for annotation in citation_annotations:
        if annotation.index is None or annotation.mark is None:
            raise ValueError("Indexed citation is missing its index or mark")
        key = (annotation.page_index, annotation.index)
        if key in reference_map:
            raise ValueError(f"Duplicate citation index: {key}")
        owned_layouts = [
            layout
            for layout in citation_layouts
            if layout.citation_id == annotation.citation_id
        ]
        raw_items = _restore_layouts(owned_layouts, {})
        flow_items = []
        for item in raw_items:
            if isinstance(item, SourceAsset):
                flow_items.append(
                    DisplayFormula(item)
                    if item.ref == "formula"
                    else StandaloneAsset(item)
                )
            else:
                flow_items.append(item)
        reference = Reference(
            page_index=annotation.page_index,
            order=annotation.index,
            mark=_restore_mark(annotation.mark),
            flow_items=flow_items,
        )
        references.append(reference)
        reference_map[key] = reference

    paragraph_layouts = [
        layout
        for page in page_list
        for layout in page.layouts
        if layout.ownership == "paragraph"
    ]
    paragraph_layouts.sort(key=lambda layout: layout.document_order)
    paragraphs = _restore_layouts(paragraph_layouts, reference_map)
    # Unresolved source is explicit (origin_page_index=None), and is preserved
    # in narrative rather than silently discarded or guessed onto a definition.
    indexed_ids = {citation.citation_id for citation in citation_annotations}
    for citation_id in dict.fromkeys(layout.citation_id for layout in citation_layouts):
        if citation_id not in indexed_ids:
            paragraphs.extend(_restore_layouts([
                layout for layout in citation_layouts if layout.citation_id == citation_id
            ], {}))
    positions = {_source_position_key(layout.source): layout.document_order
                 for page in page_list for layout in page.layouts}
    paragraphs.sort(key=lambda item: positions[_source_position_key(
        item.children[0] if isinstance(item, TextFlowItem) else item)])
    return RestoredStreams(paragraphs=paragraphs, citations=references)


def _source_position_key(source) -> tuple:
    if isinstance(source, (SourceTextFragment, _AnalysedTextSource)):
        return ("text", source.page_index, source.source_order)
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
) -> AnalysedLayout:
    references: list[ReferenceLocation] = []
    if isinstance(source, SourceTextFragment):
        analysed_source: _AnalysedSource = _AnalysedTextSource(
            page_index=source.page_index,
            source_order=source.source_order,
            bbox=source.bbox,
            content=_analyse_content(
                list(source.source.content)
                if ownership == "citation" and source.source is not None
                else source.content, references),
            reading_order=source.source.reading_order if source.source else None,
            origin=source.source,
            citation_prefix=_citation_prefix(source) if ownership == "citation" else 0,
        )
    else:
        analysed_source = _AnalysedAssetSource(
            page_index=source.page_index,
            ref=source.ref,
            bbox=source.bbox,
            title=_analyse_content(source.title, references, (0,)),
            content=_analyse_content(source.content, references, (1,)),
            caption=_analyse_content(source.caption, references, (2,)),
            asset_hash=source.asset_hash,
            reading_order=source.reading_order,
            origin=source.source,
        )
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
            if citation.mark is None:
                raise ValueError("Indexed citation must have a mark")
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
            if layout.source_order is not None:
                identity = (layout.page_index, layout.source_order)
                if identity in source_identities:
                    raise ValueError(f"Duplicate fragment source identity: {identity}")
                source_identities.add(identity)

    for segment in segments.values():
        if segment.origin_page_index is not None:
            origin = indexed_segments.get(segment.citation_id)
            if origin is None or origin.page_index != segment.origin_page_index:
                raise ValueError(f"Citation continuation has no origin: {segment.citation_id}")

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


def _analyse_content(
    content: list,
    references: list[ReferenceLocation],
    prefix: tuple[int, ...] = (),
) -> _AnalysedContent:
    result: list[_AnalysedContentPart] = []
    for index, part in enumerate(content):
        path = (*prefix, index)
        if isinstance(part, Reference):
            references.append(
                ReferenceLocation(
                    path=path,
                    citation_page_index=part.page_index,
                    citation_index=part.order,
                )
            )
            result.append(_ReferencePlaceholder(
                text=str(getattr(part.mark, "char", part.mark)),
                citation_page_index=part.page_index,
                citation_index=part.order,
            ))
        elif isinstance(part, InlineExpression):
            result.append(_AnalysedInlineExpression(part.kind, part.content))
        elif isinstance(part, HTMLTag):
            result.append(
                _AnalysedHTMLTag(
                    name=part.definition.name,
                    attributes=tuple(part.attributes),
                    children=_analyse_content(part.children, references, path),
                )
            )
        elif isinstance(part, str):
            result.append(part)
        else:
            raise TypeError(f"Unsupported source content member: {type(part).__name__}")
    return tuple(result)


def _visible_content_text(content: _AnalysedContent) -> str:
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, _ReferencePlaceholder):
            parts.append(part.text)
        elif isinstance(part, _AnalysedInlineExpression):
            parts.append(part.content)
        else:
            parts.append(_visible_content_text(part.children))
    return "".join(parts)


def _materialize_reference_text(content: _AnalysedContent) -> _AnalysedContent:
    result: list[_AnalysedContentPart] = []
    for part in content:
        if isinstance(part, _ReferencePlaceholder):
            result.append(part.text)
        elif isinstance(part, _AnalysedHTMLTag):
            result.append(replace(
                part,
                children=_materialize_reference_text(part.children),
            ))
        else:
            result.append(part)
    return tuple(result)


def _content_text_leaves(
    content: _AnalysedContent,
) -> list[tuple[tuple[int, ...], int, int]]:
    leaves: list[tuple[tuple[int, ...], int, int]] = []
    offset = 0

    def visit(parts: _AnalysedContent, prefix: tuple[int, ...]) -> None:
        nonlocal offset
        for index, part in enumerate(parts):
            path = (*prefix, index)
            if isinstance(part, str):
                leaves.append((path, offset, offset + len(part)))
                offset += len(part)
            elif isinstance(part, _ReferencePlaceholder):
                offset += len(part.text)
            elif isinstance(part, _AnalysedInlineExpression):
                leaves.append((path, offset, offset + len(part.content)))
                offset += len(part.content)
            else:
                visit(part.children, path)

    visit(content, ())
    return leaves


def _leaf_for_span(
    leaves: list[tuple[tuple[int, ...], int, int]],
    start: int,
    end: int,
) -> tuple[tuple[int, ...], int, int] | None:
    if start != end:
        return next((leaf for leaf in leaves if leaf[1] <= start and end <= leaf[2]), None)
    candidates = [leaf for leaf in leaves if leaf[1] <= start <= leaf[2]]
    if not candidates:
        return None
    # At a structured boundary, attach a zero-width ref to the text on its
    # left. This preserves the natural reading position without crossing tags.
    return candidates[-1]


def _replace_content_text_leaves(
    content: _AnalysedContent,
    replacements: dict[tuple[int, ...], list[tuple[int, int, ReferenceSpan]]],
    prefix: tuple[int, ...] = (),
) -> _AnalysedContent:
    result: list[_AnalysedContentPart] = []
    for index, part in enumerate(content):
        path = (*prefix, index)
        if isinstance(part, str) and path in replacements:
            cursor = 0
            for start, end, span in sorted(
                replacements[path], key=lambda item: (item[0], item[1])
            ):
                if start > cursor:
                    result.append(part[cursor:start])
                result.append(_ReferencePlaceholder(
                    text=span.mark_text,
                    citation_page_index=span.citation_page_index,
                    citation_index=span.citation_index,
                ))
                cursor = end
            if cursor < len(part):
                result.append(part[cursor:])
        elif isinstance(part, _AnalysedInlineExpression) and path in replacements:
            spans = replacements[path]
            if len(spans) != 1:
                raise ValueError('Reference must cover the complete atomic OCR expression')
            start, end, span = spans[0]
            placeholder = _ReferencePlaceholder(span.mark_text, span.citation_page_index, span.citation_index)
            if (start, end) == (0, len(part.content)):
                result.append(placeholder)
            elif start == end == 0:
                result.extend((placeholder, part))
            elif start == end == len(part.content):
                result.extend((part, placeholder))
            else:
                raise ValueError('Reference must cover the complete atomic OCR expression')
        elif isinstance(part, _AnalysedHTMLTag):
            result.append(replace(
                part,
                children=_replace_content_text_leaves(
                    part.children, replacements, path
                ),
            ))
        else:
            result.append(part)
    return tuple(result)


def _collect_reference_locations(
    content: _AnalysedContent,
    references: list[ReferenceLocation],
    prefix: tuple[int, ...] = (),
) -> None:
    for index, part in enumerate(content):
        path = (*prefix, index)
        if isinstance(part, _ReferencePlaceholder):
            references.append(ReferenceLocation(
                path=path,
                citation_page_index=part.citation_page_index,
                citation_index=part.citation_index,
            ))
        elif isinstance(part, _AnalysedHTMLTag):
            _collect_reference_locations(part.children, references, path)


class PageStructureError(ValueError):
    """Invalid source relationships with a conservative page recovery target."""

    def __init__(self, message: str, page_index: int, *, bbox: tuple[int, int, int, int] | None = None) -> None:
        super().__init__(message)
        self.page_index = page_index
        self.bbox = bbox

    def __reduce__(self):
        return type(self), (str(self), self.page_index), {"bbox": self.bbox}


def _restore_layouts(
    layouts: list[AnalysedLayout],
    references: dict[tuple[int, int], Reference],
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
        normalize_source_projection(paragraph)
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
        elif (
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
    sequence: list[AnalysedLayout] = []
    for layout in layouts:
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
    return SourceTextFragment(
        page_index=source.page_index,
        source_order=source.source_order,
        bbox=source.bbox,
        content=_project_fragment_content(source, layout, references),
        source=source.origin,
    )


def project_definition_mark(layout: AnalysedLayout, mark: str | None) -> AnalysedLayout:
    """Remove a printed prefix only when projecting a definition, reversibly."""
    source = layout.source
    if not isinstance(source, _AnalysedTextSource):
        return layout
    prefix = 0
    parts = 0
    if mark and source.content and isinstance(source.content[0], _AnalysedInlineExpression) and source.content[0].content.strip() == mark:
        parts = 1
    remaining = source.content[parts:]
    if mark and remaining and isinstance(remaining[0], str):
        first = remaining[0]
        start = len(first) - len(first.lstrip())
        if parts:
            prefix = start
        elif first[start:].startswith(mark):
            end = start + len(mark)
            prefix = end + len(first[end:]) - len(first[end:].lstrip())
    return replace(layout, source=replace(source, citation_prefix=prefix, citation_prefix_parts=parts))


def _citation_prefix(fragment: SourceTextFragment) -> int:
    if fragment.source is None:
        return 0
    original = fragment.source.content
    current = fragment.content
    if original and current and isinstance(original[0], str) and isinstance(current[0], str):
        if original[0].endswith(current[0]):
            return len(original[0]) - len(current[0])
    return 0


def _project_fragment_content(source, layout, references) -> list:
    content = _restore_content(source.content, layout.references, references)
    if layout.ownership == 'citation' and source.citation_prefix_parts:
        content = content[source.citation_prefix_parts:]
    if layout.ownership == "citation" and source.citation_prefix and content:
        content[0] = content[0][source.citation_prefix:]
        if not content[0]:
            content.pop(0)
    if layout.ownership == 'citation' and content and isinstance(content[0], str):
        # Publication cues locate printed continuation, not author prose. Once
        # note identity is restored they must not migrate through translation
        # into an earlier physical page of the same paragraph.
        content[0] = re.sub(r'^\s*[（(]\s*(?:接上[页頁]|续上[页頁])\s*[）)]\s*', '', content[0])
    return content


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


def _restore_content(
    content: _AnalysedContent,
    annotations: list[ReferenceLocation],
    references: dict[tuple[int, int], Reference],
    prefix: tuple[int, ...] = (),
) -> list:
    annotations_by_path: dict[tuple[int, ...], ReferenceLocation] = {}
    for annotation in annotations:
        if annotation.path in annotations_by_path:
            raise ValueError(f"Duplicate reference location: {annotation.path}")
        annotations_by_path[annotation.path] = annotation

    used_paths: set[tuple[int, ...]] = set()

    def restore_parts(
        parts: _AnalysedContent, current_prefix: tuple[int, ...]
    ) -> list:
        result: list = []
        for index, part in enumerate(parts):
            path = (*current_prefix, index)
            if isinstance(part, _ReferencePlaceholder):
                annotation = annotations_by_path.get(path)
                if annotation is None:
                    raise ValueError(f"Reference placeholder has no annotation: {path}")
                key = (annotation.citation_page_index, annotation.citation_index)
                if key not in references:
                    raise ValueError(f"Reference points to unknown citation: {key}")
                result.append(references[key])
                used_paths.add(path)
            elif isinstance(part, _AnalysedInlineExpression):
                result.append(InlineExpression(part.kind, part.content))
            elif isinstance(part, _AnalysedHTMLTag):
                definition = tag_definition(part.name)
                if definition is None:
                    raise ValueError(f"Unknown analysed HTML tag: {part.name}")
                result.append(
                    HTMLTag(
                        definition=definition,
                        attributes=list(part.attributes),
                        children=restore_parts(part.children, path),
                    )
                )
            else:
                result.append(part)
        return result

    restored = restore_parts(content, prefix)
    expected_paths = {
        path for path in annotations_by_path if path[: len(prefix)] == prefix
    }
    if used_paths != expected_paths:
        unused = expected_paths - used_paths
        raise ValueError(f"Reference annotations have no placeholder: {unused}")
    return restored


def _analyse_mark(mark: str | Mark) -> _CitationMark:
    if isinstance(mark, str):
        return mark
    return _AnalysedMark(mark.number, mark.char, mark.clazz, mark.style)


def _restore_mark(mark: _CitationMark | None) -> str | Mark:
    if mark is None:
        return ''  # OCR may identify a note's content but miss its printed mark.
    if isinstance(mark, str):
        return mark
    return Mark(mark.number, mark.char, mark.clazz, mark.style)
