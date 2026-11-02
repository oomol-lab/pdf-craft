"""Page-level semantic review between resolution and stream restoration."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ...pdf import Page
from .page_analysis import AnalysedLayout, PageAnalysis, _AnalysedAssetSource
from .paragraph_geometry import text_scale_estimate
from .local_review import LOCAL_REVIEW_THRESHOLD, local_review_requests
from .structural_review import NamedBatchEvaluator, review_body_page
from .footnote_review import (FOOTNOTE_REVIEW_THRESHOLD, footnote_review_requests, review_footnote_page)

from .review_templates import review_template

JEV_REVIEW_THRESHOLD = FOOTNOTE_REVIEW_THRESHOLD



@dataclass(frozen=True)
class PageReviewResult:
    """One JEV page decision interpreted by the routing policy."""

    page_index: int
    pass_probability: float
    risk: float
    requires_review: bool


PageEvaluator = Callable[[int, dict[str, Any]], Awaitable[float]]
PageBatchEvaluator = Callable[
    [Iterable[tuple[int, dict[str, Any]]]],
    Awaitable[list[tuple[int, float]]],
]


class PageAnalysisProcessor(Protocol):
    """Optional stage allowed to inspect or replace the reversible pages."""

    async def __call__(
        self,
        source_pages: list[Page],
        analyses: list[PageAnalysis],
        page_pixel_sizes: Mapping[int, tuple[int, int]],
    ) -> list[PageAnalysis]: ...


class JevReviewProcessor:
    """Aggregate complete JEV reviews while leaving PageAnalysis unchanged."""

    def __init__(
        self,
        evaluator: PageEvaluator,
        threshold: float | None = None,
        batch_evaluator: PageBatchEvaluator | None = None,
        includes_footnotes: bool = True,
        question_batch_evaluator: NamedBatchEvaluator | None = None,
    ) -> None:
        threshold = review_threshold(includes_footnotes) if threshold is None else threshold
        if not 0 <= threshold <= 1:
            raise ValueError("JEV review threshold must be between 0 and 1")
        self._question_batch_evaluator = question_batch_evaluator
        self._evaluator = evaluator
        self._includes_footnotes = includes_footnotes
        self._batch_evaluator = batch_evaluator
        self._threshold = threshold
        self.results: list[PageReviewResult] = []

    async def __call__(
        self,
        source_pages: list[Page],
        analyses: list[PageAnalysis],
        page_pixel_sizes: Mapping[int, tuple[int, int]],
    ) -> list[PageAnalysis]:
        from ..progress import page_stage
        for page in analyses:
            await page_stage(page.page_index, 5)
        evaluate = self._question_batch_evaluator
        if evaluate is None:
            if not self._includes_footnotes:
                raise ValueError("Body-only review requires a named-score batch evaluator")
            evaluate = self._evaluate_single_questions
        self.results = []
        for _, state in build_jev_review_states(source_pages, analyses, page_pixel_sizes,
                                                includes_footnotes=self._includes_footnotes):
            if self._includes_footnotes:
                probability = await review_footnote_page(state, evaluate)
            else:
                index = state['target_page']['page_index']
                probability = await review_body_page(
                    ((index, packet) for packet in local_review_requests(state, concrete_joins=True)), evaluate)
            if probability is not None:
                cutoff = 1 - self._threshold
                # Compare on the score's own scale: taking the complement can
                # turn the exact cutoff into a slightly lower float risk.
                risk = self._threshold if probability == cutoff else 1 - probability
                self.results.append(PageReviewResult(state['target_page']['page_index'],
                    probability, risk, probability <= cutoff))
        return analyses

    async def _evaluate_single_questions(self, requests):
        """Adapt scalar diagnostic evaluators without losing question polarity."""
        evaluated = []
        for index, packet in requests:
            scores = {}
            for name, prompt in packet['questions'].items():
                single = dict(packet, questions={name: prompt})
                try:
                    if self._batch_evaluator is not None:
                        values = await self._batch_evaluator([(index, single)])
                        if len(values) != 1 or values[0][0] != index:
                            return evaluated
                        scores[name] = values[0][1]
                    else:
                        scores[name] = await self._evaluator(index, single)
                except Exception as error:
                    from ..._policy import recover
                    from ...policies import FailureCategory, FailureLocation
                    await recover(error, FailureCategory.ANALYSIS, 'jev_review', 'algorithm_result',
                                  targets=(FailureLocation('page', page_indexes=(index,)),))
                    return evaluated
            evaluated.append((index, scores))
        return evaluated


def build_jev_review_requests(
    source_pages: Iterable[Page], analyses: Iterable[PageAnalysis],
    page_pixel_sizes: Mapping[int, tuple[int, int]], *,
    includes_footnotes: bool = True, target_page_index: int | None = None,
) -> Iterable[tuple[int, dict[str, Any]]]:
    """Build bounded named-question packets for either extraction mode."""
    for index, state in build_jev_review_states(source_pages, analyses, page_pixel_sizes,
            includes_footnotes=includes_footnotes, target_page_index=target_page_index):
        if includes_footnotes:
            yield from footnote_review_requests(state)
        else:
            for packet in local_review_requests(state, concrete_joins=True):
                yield index, packet


def build_jev_review_states(
    source_pages: Iterable[Page], analyses: Iterable[PageAnalysis],
    page_pixel_sizes: Mapping[int, tuple[int, int]], *,
    includes_footnotes: bool = True, target_page_index: int | None = None,
) -> Iterable[tuple[int, dict[str, Any]]]:
    """Keep the same immutable initial OCR neighborhood for each local review."""
    page_by_index = {page.index: page for page in source_pages}
    packets: dict[int, dict[str, Any]] = {}
    for analysis in analyses:
        page = page_by_index.get(analysis.page_index)
        if page is None:
            raise ValueError(f"Missing OCR page for analysis {analysis.page_index}")
        size = page_pixel_sizes.get(analysis.page_index)
        if size is None:
            raise ValueError(
                f"Missing pixel size for analysis page {analysis.page_index}"
            )
        packets[analysis.page_index] = _page_packet(analysis, page, size)

    def generate():
        for page_index, target in packets.items():
            if target_page_index is not None and page_index != target_page_index:
                continue
            state = {
                "format": "pdf-craft PageReviewPacket v2",
                "coordinate_note": (
                    review_template('shared_review', 'coordinate_note')
                ),
                "target_page": target,
                "previous_page": _boundary_context(
                    packets.get(page_index - 1), "previous", includes_footnotes
                ),
                "next_page": _boundary_context(
                    packets.get(page_index + 1), "next", includes_footnotes
                ),
            }
            yield page_index, state
    return generate()


def review_threshold(includes_footnotes: bool) -> float:
    """Mode-specific internal decisions, not a caller-controlled cost quota."""
    return JEV_REVIEW_THRESHOLD if includes_footnotes else LOCAL_REVIEW_THRESHOLD


def load_page_pixel_sizes(path: Path) -> dict[int, tuple[int, int]]:
    """Read OCR page geometry used to normalize review coordinates."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[int, tuple[int, int]] = {}
    for page_index, size in raw.items():
        if (
            not isinstance(size, list)
            or len(size) != 2
            or not all(isinstance(value, int) and value > 0 for value in size)
        ):
            raise ValueError(f"Invalid page pixel size for page {page_index}")
        result[int(page_index)] = (size[0], size[1])
    return result


def _page_packet(
    analysis: PageAnalysis,
    page: Page,
    size: tuple[int, int],
) -> dict[str, Any]:
    layouts = [_layout_packet(layout, page, size) for layout in analysis.layouts]
    citation_layout_counts: dict[str, int] = {}
    for layout in layouts:
        citation_id = layout["citation_id"]
        if citation_id is not None:
            citation_layout_counts[citation_id] = (
                citation_layout_counts.get(citation_id, 0) + 1
            )
    return {
        "page_index": page.index,
        "pixel_size": list(size),
        "ocr_body_layout_count": len(page.body_layouts),
        "ocr_footnote_layout_count": len(page.footnotes_layouts),
        "citations": [
            {
                "citation_id": citation.citation_id,
                "index": citation.index,
                "mark": _mark_text(citation.mark),
                "stream_order": citation.stream_order,
                "owned_layout_count_on_page": citation_layout_counts.get(
                    citation.citation_id, 0
                ),
            }
            for citation in analysis.citations
        ],
        "layouts": layouts,
    }


def _layout_packet(
    layout: AnalysedLayout,
    page: Page,
    size: tuple[int, int],
) -> dict[str, Any]:
    source = layout.source
    annotations = layout.annotations
    source_order = getattr(source, "source_order", None)
    if source_order is not None:
        raw_layouts = (
            page.body_layouts
            if annotations.ownership == "paragraph"
            else page.footnotes_layouts
        )
        origin = getattr(source, "origin", None)
        raw_text = origin.text if origin is not None else (
            raw_layouts[source_order].text
            if source_order < len(raw_layouts)
            else _content_text(source.content)
        )
        analysed_text = _content_text(source.content)
        source_type = "text"
    else:
        assert isinstance(source, _AnalysedAssetSource)
        fields = (source.title, source.caption) if source.ref == 'image' else (source.content,)
        raw_text = "\n".join(_content_text(field) for field in fields if field)
        analysed_text = raw_text
        source_type = getattr(source, "ref")
    width, height = size
    bbox = source.bbox
    result = {
        "document_order": layout.document_order,
        "source_order": source_order,
        "source_type": source_type,
        "ocr_stream": getattr(getattr(source, "origin", None), "stream", None),
        "text_scale_estimate": text_scale_estimate(raw_text, bbox) if source_type == 'text' else None,
        "bbox_normalized": [
            round(bbox[0] / width, 3),
            round(bbox[1] / height, 3),
            round(bbox[2] / width, 3),
            round(bbox[3] / height, 3),
        ],
        "raw_text": _review_text(raw_text),
        "analysed_text": _review_text(analysed_text),
        "ownership": annotations.ownership,
        "continues_from_previous": annotations.continues_from_previous,
        "paragraph_role": annotations.paragraph_role,
        "paragraph_level": annotations.paragraph_level,
        "citation_id": annotations.citation_id,
        "citation_index": annotations.citation_index,
        "references": [
            {
                "path": list(reference.path),
                "citation_page": reference.citation_page_index,
                "citation_index": reference.citation_index,
            }
            for reference in annotations.references
        ],
    }

    origin = getattr(source, "origin", None)
    if origin is not None:
        raw_sources = page.body_layouts if origin.stream == 'body' else page.footnotes_layouts
        observation = next((item.number_ink_bbox for item in raw_sources
                            if item.order == origin.stream_order), None)
        if observation is not None:
            result['number_ink_bbox_normalized'] = [
                round(value / (width if index % 2 == 0 else height), 3)
                for index, value in enumerate(observation)]
    return result


def _boundary_context(
    packet: dict[str, Any] | None,
    _side: str,
    includes_footnotes: bool = True,
) -> dict[str, Any] | None:
    if packet is None:
        return None
    paragraphs = [
        layout for layout in packet["layouts"]
        if layout["ownership"] == "paragraph" and layout.get("source_type") not in {"image", "table"}
    ]
    citations = [
        layout for layout in packet["layouts"]
        if layout["ownership"] == "citation" and layout.get("source_type") not in {"image", "table"}
    ]
    # A misplaced note may occupy the apparent tail: retain the real body too.
    candidates = {} if includes_footnotes else {'excluded_candidates': [
        layout for layout in packet['layouts']
        if layout['ownership'] == 'ignored' and layout['ocr_stream'] == 'footnote'
    ]}
    return {
        "page_index": packet["page_index"],
        "ocr_body_layout_count": packet["ocr_body_layout_count"],
        "ocr_footnote_layout_count": packet["ocr_footnote_layout_count"],
        "citations": packet["citations"],
        "paragraph_boundary": paragraphs,
        "citation_boundary": citations,
        "assets": [layout for layout in packet['layouts'] if layout['source_type'] in {'image', 'table'}],
        **candidates,
    }


def _content_text(content: Iterable[Any]) -> str:
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif type(part).__name__ == "_ReferencePlaceholder":
            parts.append("⟦REF⟧")
        elif hasattr(part, "children"):
            parts.append(_content_text(part.children))
        elif hasattr(part, "content"):
            parts.append(str(part.content))
    return "".join(parts)


def _review_text(text: str) -> str:
    """Keep ownership and paragraph evidence in the middle of a layout too."""
    return re.sub(r"\s+", " ", text).strip()


def _mark_text(mark: Any) -> str | None:
    if mark is None:
        return None
    return str(getattr(mark, "char", mark))
