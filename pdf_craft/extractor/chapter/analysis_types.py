"""Source-complete page records and editable decisions, without inference."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from ...expression import ExpressionKind
from .chapter import (
    Reference,
    SourceAsset,
    TextFlowItem,
    TextSource,
)
from .mark import NumberClass, NumberStyle


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
class AssetAttachment:
    """Submitted local asset identity and display slot, independent of geometry."""

    asset_id: str
    slot: Literal['title', 'content', 'caption']


@dataclass(frozen=True)
class AssetTextTarget:
    """A local, provisional attachment; source identity remains independent."""

    page_index: int
    ref: str
    bbox: tuple[int, int, int, int]
    slot: Literal['title', 'caption']


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
    asset_text_target: AssetTextTarget | None = None
    source_start: int = 0
    source_end: int | None = None
    source_text: str | None = None

    @property
    def fragment_id(self) -> int:
        n = self.source_order + self.source_start
        return (self.source_order if self.source_start == 0
                else -(n * (n + 1) // 2 + self.source_start + 1))


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
    associated_sources: tuple[_AnalysedTextSource, ...] = ()


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
    extract_from_expression: bool = False


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
    keep_asset_attachment: bool = False
    asset_attachment: AssetAttachment | None = None
    independent_stream: str | None = None


@dataclass(frozen=True)
class AnalysedLayout:
    """An immutable source layout decorated with editable resolution facts."""

    source: _AnalysedSource
    document_order: int
    annotations: LayoutAnnotations
    source_id: str | None = field(default=None, compare=False)

    @property
    def asset_text_target(self) -> AssetTextTarget | None:
        return self.source.asset_text_target if isinstance(self.source, _AnalysedTextSource) else None

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


class PageStructureError(ValueError):
    """Invalid source relationships with a conservative page recovery target."""

    def __init__(self, message: str, page_index: int, *, bbox: tuple[int, int, int, int] | None = None) -> None:
        super().__init__(message)
        self.page_index = page_index
        self.bbox = bbox

    def __reduce__(self):
        return type(self), (str(self), self.page_index), {"bbox": self.bbox}

