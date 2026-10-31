"""Capture page-wide source identity and reading positions before analysis."""
from copy import deepcopy
from collections.abc import Iterable, Iterator, Mapping

from ...pdf import Page
from .chapter import TextSource, SourceTextFragment
from .jointer import _parse_block_content, _is_empty_text_layout


def capture_sources(pages: list[Page]) -> dict[tuple[int, int], tuple[int, TextSource]]:
    """Insert misclassified regions into the existing OCR reading sequence.

    Preserve the body stream's column order. A region joins a column only when
    it overlaps that column horizontally; this is deliberately not a global
    y sort, which would interleave two independent columns.
    """
    sources = {}
    for page in pages:
        ordered = list(page.body_layouts)
        for note in page.footnotes_layouts:
            # A block spanning all columns separates reading bands. Preserve
            # each band's established column order, rather than inserting it
            # halfway through the left column or sorting the page by y.
            if not ordered:
                ordered.append(note)
                continue
            left = min(item.det[0] for item in page.body_layouts or ordered)
            right = max(item.det[2] for item in page.body_layouts or ordered)
            tolerance = (right - left) * .1
            spanning = note.det[0] <= left + tolerance and note.det[2] >= right - tolerance
            above = [item for item in ordered if item.det[3] <= note.det[1]]
            below = [item for item in ordered if item.det[1] >= note.det[3]]
            if spanning and len(above) + len(below) == len(ordered):
                ordered = [*above, note, *below]
                continue
            width = max(1, note.det[2] - note.det[0])
            # Full-width anchors bound the reading band, but must not count
            # as members of every column. Otherwise a footer becomes the
            # left column's successor, after the entire right column.
            start, end = 0, len(ordered)
            if page.body_layouts:
                for index, item in enumerate(ordered):
                    if item.det[0] <= left + tolerance and item.det[2] >= right - tolerance:
                        if item.det[3] <= note.det[1]:
                            start = max(start, index + 1)
                        elif item.det[1] >= note.det[3]:
                            end = min(end, index)
            candidates = [(index, item) for index, item in enumerate(ordered)
                          if start <= index < end and
                          max(0, min(item.det[2], note.det[2]) -
                              max(item.det[0], note.det[0])) >= width * .5]
            following = next((index for index, item in candidates if item.det[1] > note.det[1]), None)
            # A column's tail is immediately after its last member, not at
            # the end of the page/band. Equal-height members remain stable.
            insertion = (following if following is not None else
                         candidates[-1][0] + 1 if candidates else end)
            ordered.insert(insertion, note)
        positions = {id(item): index for index, item in enumerate(ordered)}
        for stream, layouts, offset in (
            ("body", page.body_layouts, 0),
            ("footnote", page.footnotes_layouts, len(page.body_layouts)),
        ):
            for item in layouts:
                sources[(page.index, id(item))] = (offset + item.order, TextSource(
                    stream, item.order, positions[id(item)], item.ref, item.text,
                    tuple(deepcopy(_parse_block_content(item.text))),
                    first_line_inset=item.first_line_inset,
                ))
    return sources


def empty_sources(
    pages: Iterable[Page],
    sources: Mapping[tuple[int, int], tuple[int, TextSource]],
) -> Iterator[SourceTextFragment]:
    """Retain blank OCR identities and geometry without semantic paragraphs."""
    for page in pages:
        for layout in [*page.body_layouts, *page.footnotes_layouts]:
            if _is_empty_text_layout(layout):
                order, origin = sources[(page.index, id(layout))]
                yield SourceTextFragment(page.index, order, layout.det, [], origin)
