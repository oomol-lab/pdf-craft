"""Immutable local OCR analysis, independent of refined neighbors and final TOC."""
# pylint: disable=protected-access
from dataclasses import replace
from copy import deepcopy

from .generation import _resolve_jointers
from .jointer import Jointer
from .page_analysis import analyse_pages
from .source import capture_sources


class PrimaryPages:
    """Initial projections with a bounded semantic, rather than physical, tail.

    Keep the last body-bearing page across transparent image/table pages. Their
    assets are already projected/spooled, so retaining the intervening pages is
    unnecessary. Empty pages and display formulas retain the joiner's boundary
    semantics. Footnote identity resolution keeps its separate adjacent input.
    """
    def __init__(self):
        self.previous = None
        self.previous_analysis = None
        self.body_tail = None

    def append(self, current):
        # Deduplicate the at-most-two retained raw pages before capturing source
        # identities. Filtering body/footnote contexts must not change offsets.
        pages = deepcopy({page.index: page for page in (self.body_tail, self.previous, current)
                          if page is not None})
        sources = capture_sources(list(pages.values()))
        body = Jointer(((page.index, pages[page.index].body_layouts)
                        for page in (self.body_tail, current) if page is not None), sources)
        notes = Jointer(((page.index, pages[page.index].footnotes_layouts)
                         for page in (self.previous, current) if page is not None), sources)
        paragraphs, citations = _resolve_jointers(body, notes)
        analyses = analyse_pages(pages, paragraphs, citations)
        target = next(page for page in analyses if page.page_index == current.index)
        _canonicalize(target, analyses, self.previous_analysis)
        if not current.body_layouts:
            self.body_tail = None
        elif any(layout.source_order is not None or getattr(layout.source, 'ref', None) == 'formula'
                 for layout in target.layouts if layout.ownership == 'paragraph'):
            self.body_tail = current
        self.previous, self.previous_analysis = current, target
        return target


def _canonicalize(target, analyses, previous_analysis):
    """Keep citation origins stable across overlapping local projections."""
    aliases = {}
    if previous_analysis is not None:
        local_previous = next(page for page in analyses if page.page_index == previous_analysis.page_index)
        previous_ids = {layout.source_order: layout.citation_id for layout in previous_analysis.layouts
                        if layout.citation_id is not None}
        for layout in local_previous.layouts:
            if layout.citation_id is not None and layout.source_order in previous_ids:
                aliases[layout.citation_id] = previous_ids[layout.source_order]
    origins = {citation.citation_id: citation for page in analyses for citation in page.citations
               if citation.index is not None}
    for citation in target.citations:
        origin = origins.get(citation.citation_id)
        aliases.setdefault(citation.citation_id,
                           f'citation-{origin.page_index}-{origin.index}' if origin is not None
                           else f'unresolved-{target.page_index}-{citation.stream_order}')
    previous_citations = {citation.citation_id: citation for citation in previous_analysis.citations} if previous_analysis else {}
    for citation in target.citations:
        citation.citation_id = aliases[citation.citation_id]
        predecessor = previous_citations.get(citation.citation_id)
        if citation.index is None and predecessor is not None:
            citation.origin_page_index = predecessor.origin_page_index
    for index, layout in enumerate(target.layouts):
        if layout.citation_id is not None:
            layout.annotations.citation_id = aliases[layout.citation_id]
        target.layouts[index] = replace(layout, document_order=(target.page_index << 32) + index)
