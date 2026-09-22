"""Reversible page-oriented projection of resolved chapter layouts.

This module sits between the traditional paragraph/reference resolution and
FlowItem assembly.  It deliberately contains no inference: the first version
only records the decisions already made by the existing algorithms and can
restore those decisions without changing source text or geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
)
from .mark import Mark, NumberClass, NumberStyle


LayoutOwnership: TypeAlias = Literal["paragraph", "citation"]


@dataclass(frozen=True)
class _ReferencePlaceholder:
    """Immutable position occupied by a reference in source content."""


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


@dataclass(frozen=True)
class _AnalysedAssetSource:
    page_index: int
    ref: Literal["image", "table", "formula"]
    bbox: tuple[int, int, int, int]
    title: _AnalysedContent
    content: _AnalysedContent
    caption: _AnalysedContent
    asset_hash: str | None


_AnalysedSource: TypeAlias = _AnalysedTextSource | _AnalysedAssetSource


@dataclass
class ReferenceLocation:
    """A reference annotation at a stable path within a text layout."""

    path: tuple[int, ...]
    citation_page_index: int
    citation_index: int


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


@dataclass
class RestoredStreams:
    """The two logical streams recovered from page analyses."""

    paragraphs: list[TextFlowItem | SourceAsset]
    citations: list[Reference]


def analyse_pages(
    page_indexes: Iterable[int],
    paragraphs: Iterable[TextFlowItem | SourceAsset],
    citations: Iterable[Reference | UnindexedCitation],
) -> list[PageAnalysis]:
    """Project traditional resolution results back onto their source pages."""

    pages: dict[int, PageAnalysis] = {}
    for page_index in page_indexes:
        if page_index in pages:
            raise ValueError(f"Duplicate PageAnalysis page index: {page_index}")
        pages[page_index] = PageAnalysis(page_index=page_index)

    for item in paragraphs:
        if isinstance(item, SourceAsset):
            layout = _analyse_layout(
                source=item,
                ownership="paragraph",
                continues_from_previous=False,
                continues_to_next=False,
            )
            _get_page(pages, layout.page_index).layouts.append(layout)
            continue

        for index, fragment in enumerate(item.children):
            if not isinstance(fragment, SourceTextFragment):
                raise ValueError(
                    "PageAnalysis must be created before FlowItem asset assembly"
                )
            layout = _analyse_layout(
                source=fragment,
                ownership="paragraph",
                continues_from_previous=index > 0,
                continues_to_next=index < len(item.children) - 1,
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
        for flow_item in citation.flow_items:
            if isinstance(flow_item, TextFlowItem):
                for index, fragment in enumerate(flow_item.children):
                    if not isinstance(fragment, SourceTextFragment):
                        raise ValueError(
                            "Citation analysis cannot contain assembled anchored assets"
                        )
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
                            continues_to_next=index < len(flow_item.children) - 1,
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

    document_order = 0
    for page in pages.values():
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
    citation_annotations.sort(key=lambda citation: citation.stream_order)

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
    return RestoredStreams(paragraphs=paragraphs, citations=references)


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
            content=_analyse_content(source.content, references),
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

    for page in pages:
        for layout in page.layouts:
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
            result.append(_ReferencePlaceholder())
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
            raise ValueError("Text layout is missing paragraph role or level")
        result.append(TextFlowItem(pending_role, pending_level, pending))
        pending = []
        pending_role = None
        pending_level = None

    previous: AnalysedLayout | None = None
    for layout in layouts:
        source = layout.source
        if isinstance(source, _AnalysedAssetSource):
            if layout.continues_from_previous or layout.continues_to_next:
                raise ValueError("Asset layouts cannot carry continuation gaps")
            if previous is not None and previous.continues_to_next:
                raise ValueError("Text layout cannot continue into an asset")
            flush_pending()
            result.append(_restore_asset(layout, references))
            previous = layout
            continue

        should_continue = layout.continues_from_previous
        if previous is None:
            if should_continue:
                raise ValueError("First layout cannot continue from a predecessor")
        elif previous.continues_to_next != should_continue:
            raise ValueError("Adjacent layout continuation annotations disagree")

        if not should_continue:
            flush_pending()
            pending_role = layout.paragraph_role
            pending_level = layout.paragraph_level
        elif (
            layout.paragraph_role != pending_role
            or layout.paragraph_level != pending_level
        ):
            raise ValueError("Continued text layouts disagree on paragraph metadata")
        pending.append(_restore_fragment(layout, references))
        previous = layout

    if previous is not None and previous.continues_to_next:
        raise ValueError("Last layout cannot continue to a successor")
    flush_pending()
    return result


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
        content=_restore_content(source.content, layout.references, references),
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


def _restore_mark(mark: _CitationMark) -> str | Mark:
    if isinstance(mark, str):
        return mark
    return Mark(mark.number, mark.char, mark.clazz, mark.style)
