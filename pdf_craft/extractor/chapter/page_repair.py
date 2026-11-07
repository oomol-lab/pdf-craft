# pylint: disable=unused-argument
"""LLM repair of one reversible page with deterministic integrity checks."""

from __future__ import annotations

from pdf_craft.usage import UsageCollector, UsageScope
from ...concurrency import fatal_error_boundary, settle_tasks
from ...policies import RetryPolicy, ErrorPolicyInput
from ..._policy import OMITTED, policy_operation

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from ...llm import Message, MessageRole
from ...llm.guaranteed import GuaranteedOptions, request_guaranteed_json
from ...pdf import Page
from .analysis_types import (
    AnalysedCitation,
    AnalysedLayout,
    LayoutAnnotations,
    PageAnalysis,
    ReferenceSpan,
)
from .analysis_content import (
    layout_reference_spans,
    layout_reference_text,
    replace_layout_references,
    project_definition_mark,
)
from .page_analysis import (
    continuation_sequences,
)
from .asset_text import (
    asset_text_packet, compile_asset_attachment, detach_invalid_asset_text,
)
from .review_templates import review_text
from .source_ranges import SourceRange, RepairSources, compile_source_ranges, repair_sources
from .page_validation import validate_page
from .page_review import (
    text_scale_estimate,
    JEV_REVIEW_THRESHOLD,
    JevReviewProcessor,
    PageEvaluator,
    PageReviewResult,
    PageBatchEvaluator,
)


PageRepairRequest = Callable[[list[Message], int, int], Awaitable[str]]
_MIN_FUZZY_SCORE = 0.82
_MIN_FUZZY_GAP = 0.12


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RefAnchor(_StrictModel):
    before: str
    mark: str
    after: str


class RepairedReference(_StrictModel):
    layout_id: str
    citation_index: int = Field(ge=1)
    anchor: RefAnchor
    extract_from_expression: bool = False


class RepairedLayout(_StrictModel):
    layout_id: str
    source_range: SourceRange | None = None
    ownership_evidence: str = Field(min_length=1)
    ownership: Literal["paragraph", "citation", "ignored"]
    paragraph_evidence: str = Field(min_length=1)
    continues_from_previous: bool
    continues_to_next: bool | None = None
    paragraph_role: Literal["body", "heading"] | None
    paragraph_level: int | None = Field(ge=-1)
    citation_id: str | None
    citation_index: int | None = Field(default=None, ge=1)
    preserve_as_image: bool = False
    keep_asset_attachment: bool | None = None


class RepairedCitation(_StrictModel):
    citation_id: str
    stream_order: int = Field(ge=0)
    continues_previous: bool = False
    index: int | None = Field(default=None, ge=1)
    mark: str | None


class RepairedPage(_StrictModel):
    page_index: int = Field(ge=1)
    stream_assessment: str = ""
    layouts: list[RepairedLayout]
    citations: list[RepairedCitation]
    references: list[RepairedReference]


PAGE_REPAIR_SYSTEM_PROMPT = review_text('page_repair', includes_footnotes=True)


class JevLlmRepairProcessor:
    """Use JEV only for admission, then repair selected pages once with LLM."""

    def __init__(
        self,
        evaluator: PageEvaluator,
        request: PageRepairRequest,
        *,
        threshold: float = JEV_REVIEW_THRESHOLD,
        batch_evaluator: PageBatchEvaluator | None = None,
        question_batch_evaluator=None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = None,
    ) -> None:
        self._retry_policy = retry_policy or RetryPolicy()
        self._error_policy = error_policy
        self._reviewer = JevReviewProcessor(
            evaluator, threshold, batch_evaluator=batch_evaluator,
            question_batch_evaluator=question_batch_evaluator,
        )
        self._request = request

    @property
    def results(self) -> list[PageReviewResult]:
        return self._reviewer.results

    @policy_operation
    async def __call__(
        self,
        source_pages: list[Page],
        analyses: list[PageAnalysis],
        page_pixel_sizes: Mapping[int, tuple[int, int]],
        *, request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> list[PageAnalysis]:
        await self._reviewer(source_pages, analyses, page_pixel_sizes)
        reviews = {result.page_index: result for result in self.results}
        return await _repair_pages(
            analyses=analyses,
            page_indexes=[
                result.page_index
                for result in self.results
                if result.requires_review
            ],
            reviews=reviews,
            request=self._request,
        )


class AllPageLlmRepairProcessor:
    """Repair selected or all pages with LLM without running or disclosing JEV."""

    def __init__(
        self,
        request: PageRepairRequest,
        *,
        page_indexes: Iterable[int] | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = None,
    ) -> None:
        self._retry_policy = retry_policy or RetryPolicy()
        self._error_policy = error_policy
        self._request = request
        self._configured_page_indexes = (
            None if page_indexes is None else tuple(page_indexes)
        )
        self.page_indexes: list[int] = []

    @policy_operation
    async def __call__(
        self,
        source_pages: list[Page],
        analyses: list[PageAnalysis],
        page_pixel_sizes: Mapping[int, tuple[int, int]],
        *, request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> list[PageAnalysis]:
        del source_pages, page_pixel_sizes
        available = {page.page_index for page in analyses}
        configured = self._configured_page_indexes
        if configured is None:
            self.page_indexes = [page.page_index for page in analyses]
        else:
            missing = sorted(set(configured) - available)
            if missing:
                raise ValueError(f"LLM repair pages do not exist: {missing}")
            selected = set(configured)
            self.page_indexes = [
                page.page_index for page in analyses
                if page.page_index in selected
            ]
        return await _repair_pages(
            analyses=analyses,
            page_indexes=self.page_indexes,
            reviews={},
            request=self._request,
        )


@fatal_error_boundary
async def _repair_pages(
    *,
    analyses: list[PageAnalysis],
    page_indexes: list[int],
    reviews: Mapping[int, PageReviewResult],
    request: PageRepairRequest,
) -> list[PageAnalysis]:
    with UsageScope() as candidate_usage:
        original = [_clone_page(page) for page in analyses]
        repaired = [_clone_page(page) for page in analyses]
        positions = {page.page_index: index for index, page in enumerate(original)}

        @fatal_error_boundary
        async def repair(page_index: int) -> tuple[int, PageAnalysis]:
            from ..progress import page_stage
            await page_stage(page_index, 6)
            position = positions[page_index]
            target = original[position]
            previous = original[position - 1] if position > 0 else None
            following = original[position + 1] if position + 1 < len(original) else None
            from ..._policy import recover
            from ...policies import FailureCategory
            try:
                result = await repair_page_with_llm(
                    previous_page=previous,
                    target_page=target,
                    next_page=following,
                    previous_pass_probability=None,
                    next_pass_probability=None,
                    target_pass_probability=(reviews[page_index].pass_probability if page_index in reviews else None),
                    request=request,
                )
            except Exception as error:
                from ..._failure_sources import analysis_location
                await recover(error, FailureCategory.ANALYSIS, 'footnote_repair', 'algorithm_result',
                              targets=(analysis_location(target),),
                              context_locations=tuple(analysis_location(page) for page in (previous, following) if page is not None))
                result = target
            return page_index, result

        tasks = [asyncio.create_task(repair(page_index)) for page_index in page_indexes]
        try:
            completed = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await settle_tasks(tasks)
            raise
        for page_index, result in completed:
            repaired[positions[page_index]] = result
        candidate_usage.accept()
        return repaired


@policy_operation
async def repair_page_with_llm(
    *,
    previous_page: PageAnalysis | None,
    target_page: PageAnalysis,
    next_page: PageAnalysis | None,
    previous_pass_probability: float | None,
    next_pass_probability: float | None,
    target_pass_probability: float | None = None,
    request: PageRepairRequest,
    retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
    request_headers: Mapping[str, str] | None = None,
    usage: UsageCollector | None = None,
    includes_footnotes: bool = True,
) -> PageAnalysis:
    """Repair exactly ``target_page`` using full text-only neighbor context."""

    messages = build_page_repair_messages(
        previous_page=previous_page,
        target_page=target_page,
        next_page=next_page,
        previous_pass_probability=previous_pass_probability,
        next_pass_probability=next_pass_probability,
        target_pass_probability=target_pass_probability,
        includes_footnotes=includes_footnotes,
    )
    sources = repair_sources(target_page)

    def compile_candidate(data, _index, _maximum):
        if not includes_footnotes:
            response = cast(RepairedPage, data)
            if response.citations or response.references or any(
                layout.ownership == 'citation' or layout.citation_id is not None
                or layout.citation_index is not None for layout in response.layouts
            ):
                raise ValueError("[FOOTNOTES_DISABLED] Page refinement cannot create citations or references")
        return _compile_repaired_page(sources, cast(RepairedPage, data))

    return await request_guaranteed_json(GuaranteedOptions(
        messages=messages, request=request, schema=RepairedPage,
        parse=compile_candidate,
    ))


def build_page_repair_messages(
    *,
    previous_page: PageAnalysis | None,
    target_page: PageAnalysis,
    next_page: PageAnalysis | None,
    previous_pass_probability: float | None,
    next_pass_probability: float | None,
    target_pass_probability: float | None = None,
    includes_footnotes: bool = True,
) -> list[Message]:
    del previous_pass_probability, next_pass_probability, target_pass_probability
    target = _repair_packet(target_page)
    has_asset_text = any(layout.asset_text_target is not None
                         for layout in target_page.layouts)
    if has_asset_text:
        target['asset_text_note'] = review_text('evidence', kind='asset_text')
    output_contract = RepairedPage.model_json_schema()
    if not has_asset_text:
        output_contract['$defs']['RepairedLayout']['properties'].pop('keep_asset_attachment')
    payload = {
        "previous_page": _context_packet(previous_page),
        "target_page": target,
        "next_page": _context_packet(next_page),
        "output_contract": output_contract,
    }
    system_prompt = review_text('page_repair', includes_footnotes=includes_footnotes)
    return [
        Message(MessageRole.SYSTEM, system_prompt),
        Message(
            MessageRole.USER,
            json.dumps(payload, ensure_ascii=False, indent=2),
        ),
    ]


def _context_packet(
    page: PageAnalysis | None,
) -> dict | None:
    if page is None:
        return None
    return {
        "analysis": _repair_packet(page),
    }


def _repair_packet(page):
    """Keep fallible decisions visibly separate from the source observations."""
    packet = _page_packet(page)
    fields = ('ownership', 'continues_from_previous', 'continues_to_next',
              'paragraph_role', 'paragraph_level', 'citation_id', 'citation_index')
    for layout in packet['layouts']:
        layout['initial_proposal'] = {key: layout.pop(key) for key in fields}
    text = [layout for layout in packet['layouts'] if layout['source_type'] == 'text']
    # Raw adjacency evidence, not approved paragraph/ownership decisions.
    # The model may reorder sources; no validator consumes this catalog.
    packet['source_text_seams'] = [dict(
        left_id=left['layout_id'], left_end=left['text'][-120:],
        right_id=right['layout_id'], right_start=right['text'][:120],
    ) for left, right in zip(text, text[1:])]
    return packet


def _page_packet(page: PageAnalysis) -> dict:
    citation_marks = {
        citation.citation_id: _mark_text(citation.mark)
        for citation in page.citations
        if citation.index is not None
    }
    mark_layouts: dict[str, str] = {}
    for layout in sorted(page.layouts, key=lambda item: item.document_order):
        if (
            layout.ownership == "citation"
            and layout.citation_id in citation_marks
            and layout.citation_id not in mark_layouts
        ):
            mark_layouts[layout.citation_id] = _layout_id(layout)
    previous_by_stream: dict[tuple[str, str | None], AnalysedLayout] = {}
    layout_packets = []
    for layout in page.layouts:
        stream_key = _initial_stream_key(layout)
        previous = previous_by_stream.get(stream_key) if stream_key and layout.source_order is not None else None
        layout_packets.append(_layout_packet(
            layout,
            citation_marks[layout.citation_id]
            if (
                layout.citation_id is not None
                and mark_layouts.get(layout.citation_id) == _layout_id(layout)
            )
            else None,
            previous,
        ))
        if stream_key is not None:
            if layout.source_order is not None:
                previous_by_stream[stream_key] = layout
            elif getattr(layout.source, "ref", None) == "formula":
                previous_by_stream.pop(stream_key, None)
    return {
        "page_index": page.page_index,
        "citation_states": {item.citation_id: item.state for item in page.citations},
        "layouts": layout_packets,
        "citations": [
            {
                "citation_id": citation.citation_id,
                "index": citation.index,
                "mark": _mark_text(citation.mark),
                "stream_order": citation.stream_order,
                "continues_previous": citation.continues_previous,
            }
            for citation in page.citations
        ],
        "references": [
            {
                "layout_id": _layout_id(layout),
                "citation_index": span.citation_index,
                "anchor": _anchor_for_span(
                    layout_reference_text(layout), span.start, span.end
                ),
            }
            for layout in page.layouts
            for span in layout_reference_spans(layout)
        ],
    }


def _layout_packet(
    layout: AnalysedLayout,
    definition_mark: str | None,
    previous_in_initial_stream: AnalysedLayout | None,
) -> dict:
    try:
        text = layout_reference_text(layout)
    except ValueError:
        text = ""
    packet = {
        "layout_id": _layout_id(layout),
        "document_order": layout.document_order,
        "source_order": layout.source_order,
        "ocr_stream": getattr(getattr(layout.source, "origin", None), "stream", None),
        "ocr_order": getattr(getattr(layout.source, "origin", None), "stream_order", None),
        "source_type": getattr(layout.source, "ref", "text"),
        "bbox": list(layout.bbox),
        "first_line_extra_indent_vs_later_lines_in_same_box": getattr(getattr(layout.source, "origin", None), "first_line_inset", None),
        "printed_lines": _printed_lines_packet(layout),
        "text": text,
        "text_scale_estimate": text_scale_estimate(text, layout.bbox) if layout.source_order is not None else None,
        "definition_mark": definition_mark,
        "initial_stream_boundary_from_previous": (
            _boundary_geometry(previous_in_initial_stream, layout)
            if previous_in_initial_stream is not None else None
        ),
        "ownership": layout.ownership,
        "continues_from_previous": layout.continues_from_previous,
        "continues_to_next": layout.continues_to_next,
        "paragraph_role": layout.paragraph_role,
        "paragraph_level": layout.paragraph_level,
        "citation_id": layout.citation_id,
        "citation_index": layout.citation_index,
    }

    association = asset_text_packet(layout)
    if association is not None:
        packet['asset_text_association'] = association
    return packet


def _printed_lines_packet(layout):
    origin = getattr(layout.source, 'origin', None)
    count = getattr(origin, 'printed_line_count', None)
    height = getattr(origin, 'printed_line_height', None)
    return dict(count=count, median_ink_height=height) if count is not None and height is not None else None


def _initial_stream_key(layout: AnalysedLayout) -> tuple[str, str | None] | None:
    if layout.annotations.keep_asset_attachment:
        return None
    if layout.ownership == "paragraph":
        return ("paragraph", None)
    if layout.ownership == "citation":
        return ("citation", layout.citation_id)
    return None


def _boundary_geometry(
    previous: AnalysedLayout,
    current: AnalysedLayout,
) -> dict[str, int | str]:
    return {
        "previous_layout_id": _layout_id(previous),
        "vertical_gap": current.bbox[1] - previous.bbox[3],
        "left_delta": current.bbox[0] - previous.bbox[0],
        "right_inset_delta": previous.bbox[2] - current.bbox[2],
    }


def _anchor_for_span(text: str, start: int, end: int, context: int = 24) -> dict:
    return {
        "before": text[max(0, start - context):start],
        "mark": text[start:end],
        "after": text[end:min(len(text), end + context)],
    }


def _compile_repaired_page(
    source: PageAnalysis | RepairSources, response: RepairedPage,
) -> PageAnalysis:
    source, response = compile_source_ranges(repair_sources(source), response, _layout_id, _resolve_anchor)
    issues: list[str] = []
    if response.page_index != source.page_index:
        issues.append(
            f"[IMMUTABLE_PAGE] page_index must remain {source.page_index}; "
            f"got {response.page_index}."
        )

    source_layouts = {_layout_id(layout): layout for layout in source.layouts}
    response_layouts = {layout.layout_id: layout for layout in response.layouts}
    if len(response_layouts) != len(response.layouts):
        issues.append("[IMMUTABLE_LAYOUTS] layouts contains duplicate layout_id values.")
    missing = sorted(set(source_layouts) - set(response_layouts))
    extra = sorted(set(response_layouts) - set(source_layouts))
    if missing or extra:
        issues.append(
            "[IMMUTABLE_LAYOUTS] Return every original layout exactly once. "
            f"missing={missing}, unknown={extra}."
        )
    if issues:
        raise ValueError("\n".join(issues))

    citations = _compile_citations(response.page_index, response.citations, issues)
    citations_by_id = {citation.citation_id: citation for citation in citations}
    repaired_layouts: dict[str, AnalysedLayout] = {}
    for layout_id, original in source_layouts.items():
        proposed = response_layouts[layout_id]
        try:
            keep_attachment = compile_asset_attachment(
                original, proposed.ownership, proposed.preserve_as_image, proposed.keep_asset_attachment)
        except ValueError as error:
            issues.append(f'[ASSET_TEXT] {layout_id}: {error}')
            keep_attachment = False
        if proposed.preserve_as_image and (original.source_order is None or proposed.ownership == 'ignored'):
            issues.append(f'[GRAPHIC_SOURCE] {layout_id}: only retained OCR text can be preserved as artwork')
        if proposed.ownership != "ignored" and original.source_order is not None:
            if proposed.paragraph_role is None or proposed.paragraph_level is None:
                issues.append(f"[PARAGRAPH_METADATA] {layout_id} requires role and level.")
        if proposed.ownership == "ignored":
            if (
                proposed.continues_from_previous
                or proposed.continues_to_next
                or proposed.paragraph_role is not None
                or proposed.paragraph_level is not None
                or proposed.citation_id is not None
                or proposed.citation_index is not None
            ):
                issues.append(
                    f"[LAYOUT_IGNORED] layouts[{layout_id}] is ignored, so both "
                    "continuation flags must be false and all paragraph/citation "
                    "fields must be null."
                )
        elif proposed.ownership == "paragraph":
            if proposed.citation_id is not None or proposed.citation_index is not None:
                issues.append(
                    f"[LAYOUT_OWNERSHIP] layouts[{layout_id}] is paragraph, so "
                    "citation_id and citation_index must both be null."
                )
        else:
            citation = citations_by_id.get(proposed.citation_id or "")
            if citation is None:
                issues.append(
                    f"[LAYOUT_CITATION] layouts[{layout_id}] references unknown "
                    f"citation_id={proposed.citation_id!r}."
                )
            elif citation.index != proposed.citation_index:
                issues.append(
                    f"[LAYOUT_CITATION] layouts[{layout_id}].citation_index "
                    f"must equal citations[{citation.citation_id}].index "
                    f"({citation.index!r})."
                )
        repaired_layouts[layout_id] = replace(
            project_definition_mark(original, None),
            annotations=LayoutAnnotations(
                ownership=proposed.ownership,
                continues_from_previous=proposed.continues_from_previous,
                continues_to_next=(proposed.continues_to_next
                                   if proposed.continues_to_next is not None
                                   else False),
                paragraph_role=proposed.paragraph_role,
                paragraph_level=proposed.paragraph_level,
                citation_id=proposed.citation_id,
                citation_index=proposed.citation_index,
                references=[],
                preserve_as_image=proposed.preserve_as_image,
                keep_asset_attachment=keep_attachment,
            ),
        )
    detach_invalid_asset_text(repaired_layouts.values())
    for ownership, citation_id in [("paragraph", None), *[("citation", ident) for ident in citations_by_id]]:
        sequence = sorted([layout for layout in repaired_layouts.values()
                           if layout.ownership == ownership and
                           (ownership == "paragraph" or layout.citation_id == citation_id)],
                          key=lambda layout: layout.document_order)
        for text_sequence in continuation_sequences(sequence):
            for left, right in zip(text_sequence, text_sequence[1:]):
                if response_layouts[_layout_id(left)].continues_to_next is None:
                    left.annotations.continues_to_next = right.continues_from_previous
        if sequence and ownership == "citation":
            first = sequence[0]
            repaired_layouts[_layout_id(first)] = project_definition_mark(
                first, _mark_text(citations_by_id[citation_id].mark))
    if issues:
        raise ValueError("\n".join(issues))

    spans_by_layout: dict[str, list[ReferenceSpan]] = {}
    indexed_citations = {
        citation.index: citation
        for citation in citations
        if citation.index is not None
    }
    for index, reference in enumerate(response.references):
        prefix = f"references[{index}]"
        layout = repaired_layouts.get(reference.layout_id)
        if layout is None:
            issues.append(
                f"[REF_LAYOUT_UNKNOWN] {prefix}.layout_id={reference.layout_id!r} "
                "does not identify a target-page layout."
            )
            continue
        if layout.ownership != "paragraph":
            issues.append(
                f"[REF_LAYOUT_OWNERSHIP] {prefix} points to {reference.layout_id}, "
                "which is not a paragraph layout."
            )
            continue
        citation = indexed_citations.get(reference.citation_index)
        if citation is None:
            issues.append(
                f"[REF_CITATION_UNKNOWN] {prefix}.citation_index="
                f"{reference.citation_index} has no indexed citation on page "
                f"{source.page_index}."
            )
            continue
        text = layout_reference_text(layout)
        resolved, error = _resolve_anchor(
            text, reference.anchor, f"{prefix}.anchor", reference.layout_id
        )
        if error is not None:
            issues.append(error)
            continue
        assert resolved is not None
        start, end = resolved
        if reference.extract_from_expression:
            printed = (_mark_text(citation.mark) or '').strip()
            selected = text[start:end]
            if not printed or selected != '^{' + printed + '}':
                issues.append(f'[REF_EXPRESSION] {prefix}: extracted superscript must match the printed note marker')
                continue
        spans_by_layout.setdefault(reference.layout_id, []).append(
            ReferenceSpan(
                start=start,
                end=end,
                citation_page_index=source.page_index,
                citation_index=reference.citation_index,
                mark_text=text[start:end],
                extract_from_expression=reference.extract_from_expression,
            )
        )

    if issues:
        raise ValueError("\n".join(issues))

    compiled_layouts: list[AnalysedLayout] = []
    for original in source.layouts:
        layout_id = _layout_id(original)
        layout = repaired_layouts[layout_id]
        if layout.source_order is not None:
            layout = replace_layout_references(
                layout, spans_by_layout.get(layout_id, []) if layout.ownership == "paragraph" else []
            )
        compiled_layouts.append(layout)

    repaired = PageAnalysis(
        page_index=source.page_index,
        layouts=compiled_layouts,
        citations=citations,
    )
    _materialize_local_definitions(repaired)
    validate_page(repaired)
    return repaired


def _materialize_local_definitions(page):
    """Give explicitly independent unmarked notes their output-local numbers.

    This operates on the complete submitted result, never on initial origins.
    Store the decision in that result so batch and live assembly are identical.
    """
    next_index = max((citation.index or 0 for citation in page.citations), default=0) + 1
    for citation in page.citations:
        if citation.index is None and not citation.continues_previous:
            citation.index = next_index
            citation.origin_page_index = page.page_index
            next_index += 1
            for layout in page.layouts:
                if layout.citation_id == citation.citation_id:
                    layout.annotations.citation_index = citation.index
    page.citations.sort(key=lambda citation: citation.index or 0)


def _compile_citations(page_index, proposed, issues):
    """Compile only the submitted local definitions and connection operations."""
    result = []
    seen_ids = set()
    expected_index = 1
    found_indexed = False
    for position, item in enumerate(sorted(proposed, key=lambda item: item.stream_order)):
        prefix = f"citations[{position}]"
        if item.continues_previous and position != 0:
            issues.append(f'[CITATION_ORDER] {prefix}: only the first note may append to the output cursor')
        if item.continues_previous and item.index is not None:
            issues.append(f'[CITATION_ORIGIN] {prefix}: choose the actual note operation first. '
                          'For a continuing note, retain continues_previous=true and set index=null, mark=null; '
                          'do not copy the earlier printed marker into a new local definition. '
                          'Use continues_previous=false with a local index only for an independent new note, '
                          'not merely to resolve this field conflict.')
        if item.citation_id in seen_ids:
            issues.append(f'[CITATION_ID_DUPLICATE] {prefix}: duplicate local identity')
        seen_ids.add(item.citation_id)
        if item.index is None:
            if found_indexed:
                issues.append(f'[CITATION_ORDER] {prefix}: unindexed notes must precede indexed notes')
            if item.mark is not None:
                issues.append(f'[CITATION_MARK] {prefix}: an incoming note cannot have a printed mark')
        else:
            found_indexed = True
            if item.index != expected_index:
                issues.append(f'[CITATION_INDEX] {prefix}: expected {expected_index}')
            expected_index += 1
            if item.mark == '':
                issues.append(f'[CITATION_MARK] {prefix}: an empty mark is invalid')
        result.append(AnalysedCitation(
            item.citation_id, page_index, item.index, item.mark, item.stream_order,
            page_index if item.index is not None else None, item.continues_previous))
    return result


def _resolve_anchor(
    text: str,
    anchor: RefAnchor,
    path: str,
    layout_id: str,
) -> tuple[tuple[int, int] | None, str | None]:
    if anchor.mark == "" and anchor.before == "" and anchor.after == "":
        return None, (
            f"[REF_ANCHOR_EMPTY] {path} for {layout_id} cannot use three empty "
            "strings. Copy adjacent layout text into before and/or after."
        )
    positions = _mark_positions(text, anchor.mark)
    exact = [
        position for position in positions
        if text[:position].endswith(anchor.before)
        and text[position + len(anchor.mark):].startswith(anchor.after)
    ]
    if len(exact) == 1:
        start = exact[0]
        return (start, start + len(anchor.mark)), None
    if len(exact) > 1:
        return None, _anchor_error(
            "REF_ANCHOR_AMBIGUOUS", path, layout_id, anchor, text, exact,
            "Expand before and/or after with more exact adjacent text.",
        )

    ranked = sorted(
        ((_anchor_score(text, position, anchor), position) for position in positions),
        reverse=True,
    )
    if ranked:
        best_score, best_position = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        if best_score >= _MIN_FUZZY_SCORE and best_score - second_score >= _MIN_FUZZY_GAP:
            return (
                best_position,
                best_position + len(anchor.mark),
            ), None
        code = "REF_ANCHOR_AMBIGUOUS" if best_score >= _MIN_FUZZY_SCORE else "REF_ANCHOR_NOT_FOUND"
        action = (
            "Expand or correct before/after so one occurrence is clearly best."
            if code == "REF_ANCHOR_AMBIGUOUS"
            else "Copy mark and adjacent text from the supplied layout exactly."
        )
        return None, _anchor_error(
            code, path, layout_id, anchor, text,
            [position for _score, position in ranked[:3]], action,
            scores=[score for score, _position in ranked[:3]],
        )
    return None, _anchor_error(
        "REF_ANCHOR_NOT_FOUND", path, layout_id, anchor, text, [],
        "The mark does not occur in this layout. Correct mark or choose the right layout_id.",
    )


def _mark_positions(text: str, mark: str) -> list[int]:
    if mark == "":
        return list(range(len(text) + 1))
    positions: list[int] = []
    start = 0
    while True:
        found = text.find(mark, start)
        if found < 0:
            return positions
        positions.append(found)
        start = found + 1


def _anchor_score(text: str, position: int, anchor: RefAnchor) -> float:
    scores: list[float] = []
    if anchor.before:
        scores.append(_best_suffix_score(text[:position], anchor.before))
    if anchor.after:
        scores.append(_best_prefix_score(
            text[position + len(anchor.mark):], anchor.after
        ))
    return sum(scores) / len(scores) if scores else 1.0


def _best_suffix_score(source: str, expected: str) -> float:
    return _best_boundary_score(source, expected, from_end=True)


def _best_prefix_score(source: str, expected: str) -> float:
    return _best_boundary_score(source, expected, from_end=False)


def _best_boundary_score(source: str, expected: str, *, from_end: bool) -> float:
    drift = max(2, min(12, len(expected) // 5))
    scores: list[float] = []
    for length in range(max(1, len(expected) - drift), len(expected) + drift + 1):
        candidate = source[-length:] if from_end else source[:length]
        if candidate:
            scores.append(SequenceMatcher(None, expected, candidate).ratio())
    return max(scores, default=0.0)


def _anchor_error(
    code: str,
    path: str,
    layout_id: str,
    anchor: RefAnchor,
    text: str,
    positions: list[int],
    action: str,
    scores: list[float] | None = None,
) -> str:
    candidates: list[str] = []
    for index, position in enumerate(positions[:3]):
        end = position + len(anchor.mark)
        excerpt = (
            text[max(0, position - 24):position]
            + "⟦" + text[position:end] + "⟧"
            + text[end:min(len(text), end + 24)]
        )
        score = "" if scores is None else f" score={scores[index]:.3f}"
        candidates.append(f"candidate {index + 1}{score}: {excerpt!r}")
    detail = "\n".join(candidates) if candidates else "candidates: none"
    return (
        f"[{code}] {path} in {layout_id} did not identify exactly one position.\n"
        f"submitted={anchor.model_dump_json()}\n{detail}\nAction: {action}"
    )


def _layout_id(layout: AnalysedLayout) -> str:
    return layout.source_id or f"layout-{layout.document_order}"


def _mark_text(mark) -> str | None:
    if mark is None:
        return None
    return str(getattr(mark, "char", mark))


def _neighbor_probability(
    page: PageAnalysis | None,
    reviews: Mapping[int, PageReviewResult],
) -> float | None:
    if page is None:
        return None
    result = reviews.get(page.page_index)
    return result.pass_probability if result is not None else None


def _clone_page(page: PageAnalysis) -> PageAnalysis:
    return PageAnalysis(
        page_index=page.page_index,
        layouts=[
            replace(
                layout,
                annotations=replace(
                    layout.annotations,
                    references=[replace(reference) for reference in layout.references],
                ),
            )
            for layout in page.layouts
        ],
        citations=[replace(citation) for citation in page.citations],
    )
