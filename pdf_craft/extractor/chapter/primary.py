"""Immutable local OCR analysis, independent of refined neighbors and final TOC."""
# pylint: disable=protected-access
from dataclasses import replace
from copy import deepcopy

from .generation import _resolve_jointers
from .jointer import Jointer
from .page_analysis import analyse_pages
from .source import capture_sources


def project_page(page, includes_footnotes):
    """Select analysis capabilities without changing persistent OCR evidence."""
    return page if includes_footnotes else replace(page, footnotes_layouts=[])


class PrimaryPages:
    """Initial projections with a bounded semantic, rather than physical, tail.

    Keep the last body-bearing page across transparent image/table pages. Their
    assets are already projected/spooled, so retaining the intervening pages is
    unnecessary. Empty pages and display formulas retain the joiner's boundary
    semantics. Footnote identity resolution keeps its separate adjacent input.
    """
    def __init__(self, includes_footnotes=True):
        self.includes_footnotes = includes_footnotes
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
                         for page in (self.previous, current) if page is not None and self.includes_footnotes), sources)
        paragraphs, citations = _resolve_jointers(body, notes)
        if not self.includes_footnotes:
            # Treat the original note stream as source candidates, not as
            # definitions. Do not parse note indices or match body references.
            paragraphs.extend(Jointer(((current.index, pages[current.index].footnotes_layouts),), sources).execute())
        analyses = analyse_pages(pages, paragraphs, citations)
        target = next(page for page in analyses if page.page_index == current.index)
        _canonicalize(target, analyses, self.previous_analysis)
        if not self.includes_footnotes:
            target = _body_candidates(target)
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


def bind_local_result(candidate, local, previous, canonical, canonical_previous):
    """Resolve window-local citation aliases only at the ordered boundary.

    Initial projections are deterministic within an OCR neighborhood. A note
    originating before that neighborhood still has a local unresolved alias;
    source membership identifies it without depending on a refined neighbor.
    The ordered initial stream, not an LLM outcome, supplies its canonical ID.
    """
    result = deepcopy(candidate)
    _canonicalize(result, [page for page in (previous, result) if page is not None], canonical_previous)
    canonical_by_order = {layout.document_order: layout for layout in canonical.layouts}
    aliases = {}
    for layout in local.layouts:
        target = canonical_by_order[layout.document_order]
        if layout.citation_id is not None and target.citation_id is not None:
            aliases[layout.citation_id] = target.citation_id
    # _canonicalize may already have resolved the alias using the preceding
    # initial page. Bind unresolved origins by immutable target membership too.
    canonical_citations = {citation.citation_id: citation for citation in canonical.citations}
    for citation in result.citations:
        citation.citation_id = aliases.get(citation.citation_id, citation.citation_id)
        original = canonical_citations.get(citation.citation_id)
        if citation.index is None and original is not None:
            citation.origin_page_index = original.origin_page_index
    for layout in result.layouts:
        if layout.citation_id is not None:
            layout.annotations.citation_id = aliases.get(layout.citation_id, layout.citation_id)
    # A transparent neighboring asset page contains no text predecessor for the
    # model to judge. Restore that deferred incoming edge from the ordered raw
    # stream, without overwriting any visible, locally editable boundary.
    if previous is not None and previous.layouts and all(
            item.source_order is None and getattr(item.source, 'ref', None) != 'formula'
            for item in previous.layouts if item.ownership == 'paragraph'):
        local_by_order = {layout.document_order: layout for layout in local.layouts}
        for layout in result.layouts:
            if layout.ownership == 'paragraph' and layout.source_order is not None:
                source = local_by_order[layout.document_order]
                if source.ownership == 'paragraph' and layout.continues_from_previous == source.continues_from_previous:
                    layout.annotations.continues_from_previous = canonical_by_order[layout.document_order].continues_from_previous
                break
    return result


def _body_candidates(page):
    """Retain all OCR identities, but do not adopt note candidates as content.

    The original stream is evidence, not a verdict. A body-only repair may
    promote an ignored note to paragraph; an unreviewed note stays excluded.
    Printed marks remain text rather than active reference placeholders.
    """
    layouts = []
    for layout in page.layouts:
        annotations = replace(layout.annotations, citation_id=None, citation_index=None, references=[])
        if getattr(getattr(layout.source, 'origin', None), 'stream', None) == 'footnote':
            annotations = replace(annotations, ownership='ignored', continues_from_previous=False,
                                  continues_to_next=False, paragraph_role=None, paragraph_level=None)
        layouts.append(replace(layout, annotations=annotations))
    return replace(page, layouts=layouts, citations=[])
