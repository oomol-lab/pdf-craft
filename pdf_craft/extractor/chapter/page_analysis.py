"""Reversible page-oriented projection of resolved chapter layouts.

This module sits between the traditional paragraph/reference resolution and
FlowItem assembly.  It deliberately contains no inference: the first version
only records the decisions already made by the existing algorithms and can
restore those decisions without changing source text or geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    """Page-local citation identity, including citations with no body."""

    page_index: int
    index: int
    mark: _CitationMark
    stream_order: int


@dataclass
class LayoutAnnotations:
    """The editable decisions attached to one immutable source layout."""

    ownership: LayoutOwnership
    continues_from_previous: bool
    continues_to_next: bool
    paragraph_role: str | None = None
    paragraph_level: int | None = None
    citation_page_index: int | None = None
    citation_index: int | None = None
    references: list[ReferenceLocation] = field(default_factory=list)


@dataclass(frozen=True)
class AnalysedLayout:
    """An immutable source layout decorated with editable resolution facts."""

    source: _AnalysedSource
    stream_order: int
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
    def citation_page_index(self) -> int | None:
        return self.annotations.citation_page_index

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
    citations: Iterable[Reference],
) -> list[PageAnalysis]:
    """Project traditional resolution results back onto their source pages."""

    pages: dict[int, PageAnalysis] = {}
    for page_index in page_indexes:
        if page_index in pages:
            raise ValueError(f"Duplicate PageAnalysis page index: {page_index}")
        pages[page_index] = PageAnalysis(page_index=page_index)

    def get_page(page_index: int) -> PageAnalysis:
        if page_index not in pages:
            pages[page_index] = PageAnalysis(page_index=page_index)
        return pages[page_index]

    paragraph_order = 0
    for item in paragraphs:
        if isinstance(item, SourceAsset):
            layout = _analyse_layout(
                source=item,
                ownership="paragraph",
                stream_order=paragraph_order,
                continues_from_previous=False,
                continues_to_next=False,
            )
            get_page(layout.page_index).layouts.append(layout)
            paragraph_order += 1
            continue

        for index, fragment in enumerate(item.children):
            if not isinstance(fragment, SourceTextFragment):
                raise ValueError(
                    "PageAnalysis must be created before FlowItem asset assembly"
                )
            layout = _analyse_layout(
                source=fragment,
                ownership="paragraph",
                stream_order=paragraph_order,
                continues_from_previous=index > 0,
                continues_to_next=index < len(item.children) - 1,
                paragraph_role=item.role,
                paragraph_level=item.level,
            )
            get_page(layout.page_index).layouts.append(layout)
            paragraph_order += 1

    citation_layout_order = 0
    for citation_order, citation in enumerate(citations):
        page = get_page(citation.page_index)
        page.citations.append(
            AnalysedCitation(
                page_index=citation.page_index,
                index=citation.order,
                mark=_analyse_mark(citation.mark),
                stream_order=citation_order,
            )
        )
        for flow_item in citation.flow_items:
            if isinstance(flow_item, TextFlowItem):
                for index, fragment in enumerate(flow_item.children):
                    if not isinstance(fragment, SourceTextFragment):
                        raise ValueError(
                            "Citation analysis cannot contain assembled anchored assets"
                        )
                    layout = _analyse_layout(
                        source=fragment,
                        ownership="citation",
                        stream_order=citation_layout_order,
                        continues_from_previous=index > 0,
                        continues_to_next=index < len(flow_item.children) - 1,
                        paragraph_role=flow_item.role,
                        paragraph_level=flow_item.level,
                        citation_page_index=citation.page_index,
                        citation_index=citation.order,
                    )
                    get_page(layout.page_index).layouts.append(layout)
                    citation_layout_order += 1
            else:
                source = flow_item.asset
                layout = _analyse_layout(
                    source=source,
                    ownership="citation",
                    stream_order=citation_layout_order,
                    continues_from_previous=False,
                    continues_to_next=False,
                    citation_page_index=citation.page_index,
                    citation_index=citation.order,
                )
                get_page(layout.page_index).layouts.append(layout)
                citation_layout_order += 1

    return list(pages.values())


def restore_streams(pages: Iterable[PageAnalysis]) -> RestoredStreams:
    """Restore paragraph and citation streams from editable page annotations."""

    page_list = list(pages)
    citation_annotations = [
        citation for page in page_list for citation in page.citations
    ]
    citation_annotations.sort(key=lambda citation: citation.stream_order)

    citation_layouts = [
        layout
        for page in page_list
        for layout in page.layouts
        if layout.ownership == "citation"
    ]
    citation_layouts.sort(key=lambda layout: layout.stream_order)

    references: list[Reference] = []
    reference_map: dict[tuple[int, int], Reference] = {}
    for annotation in citation_annotations:
        key = (annotation.page_index, annotation.index)
        if key in reference_map:
            raise ValueError(f"Duplicate citation index: {key}")
        owned_layouts = [
            layout
            for layout in citation_layouts
            if (layout.citation_page_index, layout.citation_index) == key
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

    known_citations = set(reference_map)
    orphaned = {
        (layout.citation_page_index, layout.citation_index)
        for layout in citation_layouts
        if (layout.citation_page_index, layout.citation_index) not in known_citations
    }
    if orphaned:
        raise ValueError(f"Citation layouts have no citation annotation: {orphaned}")

    paragraph_layouts = [
        layout
        for page in page_list
        for layout in page.layouts
        if layout.ownership == "paragraph"
    ]
    paragraph_layouts.sort(key=lambda layout: layout.stream_order)
    paragraphs = _restore_layouts(paragraph_layouts, reference_map)
    return RestoredStreams(paragraphs=paragraphs, citations=references)


def _analyse_layout(
    source: SourceTextFragment | SourceAsset,
    ownership: LayoutOwnership,
    stream_order: int,
    continues_from_previous: bool,
    continues_to_next: bool,
    paragraph_role: str | None = None,
    paragraph_level: int | None = None,
    citation_page_index: int | None = None,
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
        stream_order=stream_order,
        annotations=LayoutAnnotations(
            ownership=ownership,
            continues_from_previous=continues_from_previous,
            continues_to_next=continues_to_next,
            paragraph_role=paragraph_role,
            paragraph_level=paragraph_level,
            citation_page_index=citation_page_index,
            citation_index=citation_index,
            references=references,
        ),
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
