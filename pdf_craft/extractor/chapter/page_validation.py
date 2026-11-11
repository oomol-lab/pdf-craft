"""Accept only a complete current-page result; no input/context capabilities."""
from typing import Mapping
from collections import defaultdict

from .analysis_types import AnalysedLayout, AnalysedCitation, PageStructureError
from .asset_editing import reading_sequences, validate_asset_operations
from .analysis_content import layout_reference_text
from .review_templates import review_text


def validate_page(page):
    """Validate a COMPLETE current-page result; no input/neighbor capability.

    Origin records are opaque provenance for downstream output, never evidence
    used to approve a decision. Cross-page connections are operations, not claims
    to be re-proven against another page.
    """
    from .page_analysis import _validate_pages
    try:
        _validate_pages([page])
    except ValueError as error:
        raise PageStructureError(str(error), page.page_index) from error
    issues = []
    identities = [_layout_id(item) for item in page.layouts]
    if len(identities) != len(set(identities)):
        issues.append('[IMMUTABLE_LAYOUTS] Duplicate current fragment identity')
    _validate_ranges(page, issues)
    validate_asset_operations(page, issues)
    orders = [item.stream_order for item in page.citations]
    if len(orders) != len(set(orders)):
        issues.append('[CITATION_ORDER] Note stream positions must be unique within the current page')
    ordered = sorted(page.citations, key=lambda item: item.stream_order)
    for position, citation in enumerate(ordered):
        if citation.continues_previous and (position != 0 or citation.index is not None):
            issues.append('[CITATION_ORDER] Only the first unindexed note may continue the output cursor')
    citations = {item.citation_id: item for item in page.citations}
    _validate_layout_sequences({_layout_id(item): item for item in page.layouts}, citations, issues)
    defined = {item.index for item in page.citations if item.index is not None}
    for layout in page.layouts:
        if layout.ownership != 'ignored' and layout.source_order is not None and (
                layout.paragraph_role not in ('body', 'heading') or layout.paragraph_level is None):
            issues.append('[PARAGRAPH_METADATA] Retained text needs a role and level')
        for ref in layout.references:
            if ref.citation_page_index != page.page_index or ref.citation_index not in defined:
                issues.append('[REF_CITATION_UNKNOWN] Reference needs a current-page definition')
    if issues:
        raise PageStructureError('\n'.join(issues), page.page_index)


def _validate_layout_sequences(
    layouts: Mapping[str, AnalysedLayout],
    citations: Mapping[str, AnalysedCitation],
    issues: list[str],
) -> None:
    owned_citation_ids: set[str] = set()
    groups: list[tuple[str, str | None]] = [("paragraph", None)]
    groups.extend(("citation", citation_id) for citation_id in citations)
    for ownership, citation_id in groups:
        sequence = sorted(
            (
                (layout_id, layout)
                for layout_id, layout in layouts.items()
                if layout.ownership == ownership
                and (ownership == "paragraph" or layout.citation_id == citation_id)
            ),
            key=lambda item: item[1].document_order,
        )
        for layout_id, layout in sequence:
            if layout.citation_id is not None:
                owned_citation_ids.add(layout.citation_id)
        for group in reading_sequences(layout for _, layout in sequence):
            previous = None
            for layout in group:
                layout_id = _layout_id(layout)
                if layout.citation_id is not None:
                    owned_citation_ids.add(layout.citation_id)
                if layout.source_order is None and (
                    layout.continues_from_previous or layout.continues_to_next
                ):
                    issues.append(
                        f"[GAP_ASSET] layouts[{layout_id}] is an asset and cannot "
                        "carry continuation flags."
                    )
                if layout.source_order is None:
                    if getattr(layout.source, "ref", None) == "formula":
                        if previous is not None:
                            _, preceding = previous
                            if preceding.continues_to_next:
                                issues.append("[GAP_FORMULA] Text cannot continue across a display formula.")
                        previous = None
                    continue
                if previous is not None:
                    previous_id, previous_layout = previous
                    if (
                        previous_layout.continues_to_next
                        != layout.continues_from_previous
                    ):
                        issues.append(review_text(
                            'page_repair_gap_feedback', ownership=ownership, citation_id=citation_id,
                            previous_id=previous_id, current_id=layout_id,
                            previous=previous_layout, current=layout,
                            ending=repr(layout_reference_text(previous_layout)[-160:]),
                            opening=repr(layout_reference_text(layout)[:160]),
                        ))
                    if layout.continues_from_previous and (
                        layout.paragraph_role != previous_layout.paragraph_role
                        or layout.paragraph_level != previous_layout.paragraph_level
                    ):
                        issues.append(
                            "[GAP_PARAGRAPH_METADATA] Continued layouts "
                            f"{previous_id} and {layout_id} must use the same "
                            "paragraph_role and paragraph_level."
                        )
                previous = (layout_id, layout)
    for citation_id in sorted(set(citations) - owned_citation_ids):
        issues.append(
            f"[CITATION_WITHOUT_LAYOUT] citations[{citation_id!r}] has no "
            "citation layout on the target page."
        )


def _layout_id(layout):
    return layout.source_id or f'layout-{layout.document_order}'


def _validate_ranges(page, issues):
    """Check only source contracts carried by the completed result itself."""
    groups = defaultdict(list)
    for layout in page.layouts:
        if layout.source_order is not None:
            groups[layout.source_order].append(layout.source)
    for sources in groups.values():
        known = {source.source_text for source in sources if source.source_text is not None}
        if len(known) > 1:
            issues.append('[SOURCE_RANGE] Fragments disagree on their own source contract')
            continue
        total = len(next(iter(known))) if known else None
        cursor = 0
        for source in sorted(sources, key=lambda item: item.source_start):
            start, end = source.source_start, source.source_end
            if start < cursor or start < 0 or end is not None and end < start:
                issues.append('[SOURCE_RANGE] Overlapping or invalid current ranges')
            if total is not None and (start != cursor or end is None or end > total):
                issues.append('[SOURCE_RANGE] Current ranges must cover their declared source exactly once')
            cursor = end if end is not None else cursor
        if total is not None and cursor != total:
            issues.append('[SOURCE_RANGE] Current source coverage is incomplete')
