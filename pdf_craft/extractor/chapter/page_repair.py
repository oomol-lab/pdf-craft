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
from .mark import transform2mark
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
    restore_streams,
    continuation_sequences,
)
from .asset_text import (
    active_asset_text, asset_text_packet, compile_asset_attachment, detach_invalid_asset_text,
)
from .review_templates import review_text
from .source_ranges import SourceRange, compile_source_ranges
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
    ownership: Literal["paragraph", "citation", "ignored"]
    continues_from_previous: bool
    continues_to_next: bool | None = None
    paragraph_role: Literal["body", "heading"] | None
    paragraph_level: int | None = Field(ge=-1)
    citation_id: str | None
    citation_index: int | None = Field(default=None, ge=1)
    preserve_as_image: bool = False
    keep_asset_attachment: bool | None = None


class CitationPredecessor(_StrictModel):
    page_index: int = Field(ge=1)
    source_order: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class RepairedCitation(_StrictModel):
    citation_id: str
    index: int | None = Field(default=None, ge=1)
    mark: str | None
    stream_order: int = Field(ge=0)
    continues_previous: bool = False
    predecessor: CitationPredecessor | None = None


class RepairedPage(_StrictModel):
    page_index: int = Field(ge=1)
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
                    all_pages=repaired,
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
        del completed  # Accepted candidates are committed to the tentative graph in parse.
        try:
            _compile_page_connections(repaired)
            restore_streams(repaired)
        except Exception as error:
            from ..._policy import recover
            from ...policies import FailureCategory
            from ..._failure_sources import analysis_location
            await recover(error, FailureCategory.ANALYSIS, 'footnote_commit', 'algorithm_result',
                          targets=tuple(analysis_location(page) for page in repaired))
            # A fallback must preserve the original graph, not compile it again.
            restore_streams(original)
            return original
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
    all_pages: list[PageAnalysis] | None = None,
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
    def compile_candidate(data, _index, _maximum):
        if not includes_footnotes:
            response = cast(RepairedPage, data)
            if response.citations or response.references or any(
                layout.ownership == 'citation' or layout.citation_id is not None
                or layout.citation_index is not None for layout in response.layouts
            ):
                raise ValueError("[FOOTNOTES_DISABLED] Page refinement cannot create citations or references")
        context = all_pages or [page for page in (previous_page, target_page, next_page)
                                if page is not None]
        available = {citation.citation_id: citation for page in context
                     if page.page_index < target_page.page_index
                     for citation in page.citations if citation.index is not None}
        candidate = _compile_repaired_page(target_page, cast(RepairedPage, data), available)
        _validate_reading_boundary(candidate, next_page)
        shared_ids = {citation.citation_id for page in context
                      if page.page_index != target_page.page_index
                      for citation in page.citations}
        _validate_shared_citations(target_page, candidate, shared_ids)
        if all_pages is not None:
            transaction = [_clone_page(candidate if page.page_index == candidate.page_index else page)
                           for page in all_pages]
            _compile_page_connections(transaction)
            restore_streams(transaction)
            all_pages[:] = transaction
            return next(page for page in transaction if page.page_index == candidate.page_index)
        return candidate

    return await request_guaranteed_json(GuaranteedOptions(
        messages=messages, request=request, schema=RepairedPage,
        parse=compile_candidate,
    ))


def _validate_reading_boundary(candidate, following):
    """Reject a main-flow detour through a clearly bracketed note band.

    This is deliberately narrow: an open prose enclosure resumes at the next
    page's first text, and the candidate itself has already excluded/identified
    marked notes between that enclosure and its supposed later body. The
    conflicting ownership remains an LLM decision, never a geometry rewrite.
    """
    if following is None or following.page_index != candidate.page_index + 1:
        return
    next_text = next((layout_reference_text(item) for item in following.layouts
                      if item.ownership == 'paragraph' and item.source_order is not None), '')
    if not next_text:
        return
    ordered = sorted(candidate.layouts, key=lambda item: item.document_order)
    pairs = {'(': ')', '（': '）'}
    for index, layout in enumerate(ordered):
        if layout.ownership != 'paragraph' or layout.source_order is None:
            continue
        text = layout_reference_text(layout)
        for opening, closing in pairs.items():
            if text.count(opening) <= text.count(closing):
                continue
            prefix = next_text.split(closing, 1)[0]
            if closing not in next_text or opening in prefix:
                continue
            tail = ordered[index+1:]
            notes = [item for item in tail if item.source_order is not None
                     and item.ownership != 'paragraph'
                     and any(transform2mark(char) is not None for char in layout_reference_text(item)[:5])]
            if not notes:
                continue
            note_start = min(item.document_order for item in notes)
            note_top = min(item.bbox[1] for item in notes)
            left = min(item.bbox[0] for item in notes)
            right = max(item.bbox[2] for item in notes)
            detours = [item for item in tail if item.ownership == 'paragraph'
                       and item.source_order is not None and item.document_order > note_start
                       and item.bbox[1] >= note_top
                       and min(item.bbox[2], right) - max(item.bbox[0], left) >= .5*(item.bbox[2]-item.bbox[0])
                       and closing not in layout_reference_text(item)]
            if detours:
                names = ', '.join(_layout_id(item) for item in detours)
                raise ValueError(
                    f'[READING_FLOW] {_layout_id(layout)} 的未闭合括号在下一页正文开头续接。'
                    f'已确认的编号注释区之后 {names} 却另列为正文；请重新检查它们是否是最后一条脚注的'
                    '无编号续文/图式，而不是仅因无编号就当正文。不要改原文或拼接无关段落。')


def _validate_shared_citations(
    source: PageAnalysis, candidate: PageAnalysis, shared_ids: set[str],
) -> None:
    """A page-local edit cannot detach existing cross-page source members.

    Keeping a segment record alone does not keep its content: ownership is
    stored on layouts. Protect both contracts before committing the graph.
    Additional members and paragraph boundaries remain editable.
    """
    # A window-local unresolved alias is an algorithmic guess, not a known
    # cross-page definition. Protecting it would freeze classification errors.
    shared_ids = shared_ids & {citation.citation_id for citation in source.citations
                               if citation.index is not None or citation.origin_page_index is not None}
    proposed = {citation.citation_id: citation for citation in candidate.citations}
    for original in source.citations:
        if original.citation_id in shared_ids:
            replacement = proposed.get(original.citation_id)
            if replacement is None or (
                replacement.index, replacement.stream_order
            ) != (original.index, original.stream_order):
                raise ValueError("[CROSS_PAGE_IDENTITY] Preserve shared definition origins and segments")
    for original in source.layouts:
        if original.ownership == "citation" and original.citation_id in shared_ids:
            replacements = [layout for layout in candidate.layouts
                            if (layout.source_order == original.source_order if original.source_order is not None
                                else _layout_id(layout) == _layout_id(original))]
            # An OCR box can contain other notes too. Preserve membership of
            # this source in the established note, not exclusive ownership of
            # every character in its original coarse box.
            if not any(layout.ownership == 'citation' and layout.citation_id == original.citation_id
                       for layout in replacements):
                raise ValueError('[CROSS_PAGE_MEMBERSHIP] Preserve established cross-page source ownership')



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
    del previous_pass_probability, next_pass_probability
    target = _page_packet(target_page)
    has_asset_text = any(layout.asset_text_target is not None
                         for layout in target_page.layouts)
    if has_asset_text:
        target['asset_text_note'] = review_text('evidence', kind='asset_text')
    output_contract = RepairedPage.model_json_schema()
    if not has_asset_text:
        output_contract['$defs']['RepairedLayout']['properties'].pop('keep_asset_attachment')
    if target_pass_probability is not None:
        target['jev_p_pass'] = target_pass_probability
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
        "analysis": _page_packet(page),
    }


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
    source: PageAnalysis, response: RepairedPage,
    available_citations: Mapping[str, AnalysedCitation] | None = None,
) -> PageAnalysis:
    source, response = compile_source_ranges(source, response, _layout_id, _resolve_anchor)
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

    citations = _compile_citations(source, response.citations, issues, available_citations or {})
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
    _validate_layout_sequences(repaired_layouts, citations_by_id, issues)
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
    return repaired


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
        attached = active_asset_text(layout for _, layout in sequence)
        previous: tuple[str, AnalysedLayout] | None = None
        for layout_id, layout in sequence:
            if id(layout) in attached:
                if layout.continues_from_previous or layout.continues_to_next:
                    issues.append(f'[GAP_ASSET_TEXT] {layout_id}: detach before joining an asset label into prose')
                continue
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
                    issues.append(
                        "[GAP_MISMATCH] Adjacent layouts in the same "
                        f"{ownership} stream"
                        f"{'' if citation_id is None else f' {citation_id!r}'} "
                        f"disagree: layouts[{previous_id}]."
                        f"continues_to_next={previous_layout.continues_to_next}, "
                        f"but layouts[{layout_id}].continues_from_previous="
                        f"{layout.continues_from_previous}. Make these two "
                        "fields equal."
                    )
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


def _compile_citations(
    source: PageAnalysis,
    proposed: list[RepairedCitation],
    issues: list[str],
    available: Mapping[str, AnalysedCitation],
) -> list[AnalysedCitation]:
    existing = {citation.citation_id: citation for citation in source.citations}
    result: list[AnalysedCitation] = []
    seen_ids: set[str] = set()
    expected_index = 1
    found_indexed = False
    for position, item in enumerate(proposed):
        prefix = f"citations[{position}]"
        if item.continues_previous and item.index is not None:
            issues.append(f'[CITATION_ORIGIN] {prefix}: an indexed origin cannot continue a previous note')
        if item.citation_id in seen_ids:
            issues.append(
                f"[CITATION_ID_DUPLICATE] {prefix}.citation_id is duplicated: "
                f"{item.citation_id!r}."
            )
        seen_ids.add(item.citation_id)
        if item.index is None:
            if found_indexed:
                issues.append(
                    f"[CITATION_ORDER] {prefix} is unindexed but appears after "
                    "an indexed citation."
                )
            if item.mark is not None:
                issues.append(
                    f"[CITATION_MARK] {prefix}.mark must be null when index is null."
                )
        else:
            found_indexed = True
            if item.index != expected_index:
                issues.append(
                    f"[CITATION_INDEX] {prefix}.index must be {expected_index}; "
                    f"got {item.index}."
                )
            expected_index += 1
            if item.mark == "":
                issues.append(
                    f"[CITATION_MARK] {prefix}.mark cannot be empty for an "
                    "indexed citation."
                )
        old = existing.get(item.citation_id)
        predecessor = available.get(item.citation_id) if item.index is None else None
        if old is None and predecessor is not None:
            old = replace(predecessor, index=None, mark=None)
        if item.predecessor is not None and (item.index is not None or not item.continues_previous
                                             or item.predecessor.page_index >= source.page_index
                                             or item.predecessor.start >= item.predecessor.end):
            issues.append(f'[CITATION_PREDECESSOR] {prefix}: only an incoming note may name an earlier source range')
        if old is None and item.predecessor is None and item.citation_id != f"new-{source.page_index}-{item.index}":
            issues.append(
                f"[CITATION_ID] {prefix}: retain existing identity or use "
                f"new-{source.page_index}-{item.index} for a new local definition.")
        if old is not None and (old.index is None) != (item.index is None):
            issues.append(f"[CITATION_ORIGIN] {prefix}: cannot change a cross-page origin.")
        mark = old.mark if old is not None and _mark_text(old.mark) == item.mark else item.mark
        result.append(AnalysedCitation(
            citation_id=item.citation_id,
            page_index=source.page_index,
            index=item.index,
            mark=mark,
            stream_order=item.stream_order,
            origin_page_index=(old.origin_page_index if old is not None else source.page_index if item.index is not None else None),
            continues_previous=item.continues_previous,
            predecessor=((item.predecessor.page_index, item.predecessor.source_order,
                          item.predecessor.start, item.predecessor.end) if item.predecessor is not None else None),
        ))
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


def _compile_page_connections(pages: list[PageAnalysis]) -> None:
    """Compile successor projections from each layout's single incoming edge.

    This never chooses between conflicting edits or changes an incoming edge.
    Every candidate and the complete transaction are assembled and validated
    before publication. The old last-repaired-page-wins reconciliation is gone.
    """
    groups: dict[tuple[str, str | None], list[AnalysedLayout]] = {}
    for page in pages:
        for layout in page.layouts:
            if layout.ownership != "ignored":
                key = (layout.ownership, layout.citation_id)
                groups.setdefault(key, []).append(layout)
    for layouts in groups.values():
        layouts.sort(key=lambda layout: layout.document_order)
        for layout in layouts:
            if layout.source_order is None and (layout.continues_from_previous or layout.continues_to_next):
                raise ValueError("Asset layouts cannot carry continuation gaps")
        for sequence in continuation_sequences(layouts):
            if sequence and sequence[0].continues_from_previous:
                raise ValueError("[CONTINUATION_ORIGIN] First text has no predecessor")
            for index, layout in enumerate(sequence):
                layout.annotations.continues_to_next = (
                    sequence[index + 1].continues_from_previous if index + 1 < len(sequence) else False)
