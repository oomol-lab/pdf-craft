"""Capture page-wide source identity and reading positions before analysis."""
from copy import deepcopy

from ...pdf import Page
from .chapter import TextSource
from .jointer import _parse_block_content


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
            if ordered:
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
            candidates = [item for item in ordered if
                          max(0, min(item.det[2], note.det[2]) -
                              max(item.det[0], note.det[0])) >= width * .5]
            following = next((item for item in candidates if item.det[1] > note.det[1]), None)
            ordered.insert(ordered.index(following) if following else len(ordered), note)
        positions = {id(item): index for index, item in enumerate(ordered)}
        for stream, layouts, offset in (
            ("body", page.body_layouts, 0),
            ("footnote", page.footnotes_layouts, len(page.body_layouts)),
        ):
            for item in layouts:
                sources[(page.index, id(item))] = (offset + item.order, TextSource(
                    stream, item.order, positions[id(item)], item.ref, item.text,
                    tuple(deepcopy(_parse_block_content(item.text))),
                ))
    return sources
