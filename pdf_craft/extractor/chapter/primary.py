"""Immutable local OCR analysis, independent of refined neighbors and final TOC."""
# pylint: disable=protected-access
from dataclasses import replace
from copy import deepcopy

from .generation import _resolve_pages
from .page_analysis import analyse_pages


def primary_page(previous, current, following, previous_analysis=None):
    """Resolve only a bounded raw neighborhood and project the target page.

    Carry citation origin identities across overlapping neighborhoods. Sources
    stay unchanged; global document order is derived from page identity rather
    than from the completion order of asynchronous work.
    """
    pages = deepcopy([page for page in (previous, current, following) if page is not None])
    paragraphs, citations = _resolve_pages(pages)
    analyses = analyse_pages((page.index for page in pages), paragraphs, citations)
    target = next(page for page in analyses if page.page_index == current.index)
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
                           else f'unresolved-{current.index}-{citation.stream_order}')
    previous_citations = {citation.citation_id: citation for citation in previous_analysis.citations} if previous_analysis else {}
    for citation in target.citations:
        citation.citation_id = aliases[citation.citation_id]
        predecessor = previous_citations.get(citation.citation_id)
        if citation.index is None and predecessor is not None:
            citation.origin_page_index = predecessor.origin_page_index
    for index, layout in enumerate(target.layouts):
        if layout.citation_id is not None:
            layout.annotations.citation_id = aliases[layout.citation_id]
        target.layouts[index] = replace(layout, document_order=(current.index << 32) + index)
    return target
