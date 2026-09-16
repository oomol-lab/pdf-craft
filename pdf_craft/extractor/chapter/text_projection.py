"""Read-only text projections for PCEX v3 :class:`TextFlowItem` values.

PCEX deliberately keeps each source fragment and each embedded asset in its
original reading order.  Consumers must not flatten that stored structure:
the fragment boundaries retain source geometry, while an embedded asset is a
meaningful position for reflow renderers.  They *can*, however, choose the
appropriate read-only text view for their output medium.
"""

from collections.abc import Callable, Generator, Iterable

from .chapter import BlockMember, SourceAsset, SourceTextFragment, TextFlowItem
from ...markdown.paragraph import HTMLTag


TextRun = tuple[SourceTextFragment, ...]
RenderedFlowPart = TextRun | SourceAsset
ContentItem = str | BlockMember | HTMLTag[BlockMember]


def iter_continuous_fragments(item: TextFlowItem) -> Generator[SourceTextFragment, None, None]:
    """Yield all text fragments as one logical paragraph.

    Embedded images and tables are deliberately transparent in this view.  It
    is for consumers such as PDF text-layer filling and translation context
    that do not render an anchored asset inside the text stream.
    """
    for child in item.children:
        if isinstance(child, SourceTextFragment):
            yield child


def iter_continuous_content(item: TextFlowItem) -> Generator[ContentItem, None, None]:
    """Yield rich text content in its continuous logical order.

    The yielded values are the original content nodes.  This helper never
    mutates, trims, synthesizes whitespace, or rewrites a fragment boundary.
    OCR dehyphenation and punctuation correction remain extraction concerns.
    """
    for fragment in iter_continuous_fragments(item):
        yield from fragment.content


def iter_rendered_flow_parts(
    item: TextFlowItem,
    asset_is_rendered: Callable[[SourceAsset], bool],
) -> Generator[RenderedFlowPart, None, None]:
    """Yield text runs separated only by assets that will actually render.

    Markdown and EPUB cannot always retain an embedded asset inside a physical
    paragraph.  A successful asset output therefore becomes a boundary; an
    omitted or unavailable asset remains transparent so it cannot create a
    spurious text split.  Runs never cross a ``TextFlowItem`` boundary because
    callers invoke this function for one item at a time.
    """
    run: list[SourceTextFragment] = []
    for child in item.children:
        if isinstance(child, SourceTextFragment):
            run.append(child)
            continue
        if not asset_is_rendered(child):
            continue
        if run:
            yield tuple(run)
            run = []
        yield child
    if run:
        yield tuple(run)


def iter_run_content(run: Iterable[SourceTextFragment]) -> Generator[ContentItem, None, None]:
    """Yield rich content from one already-selected physical text run."""
    for fragment in run:
        yield from fragment.content
