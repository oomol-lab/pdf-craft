"""Page-level semantic review between resolution and stream restoration."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ...pdf import Page
from .page_analysis import AnalysedLayout, PageAnalysis


JEV_REVIEW_THRESHOLD = 0.70
JEV_QUESTION_NAME = "page_passes_strict_standard"

JEV_QUESTION: dict[str, Any] = {
    JEV_QUESTION_NAME: {
        "type": "noul",
        "instructions": {
            "role": "你是 PDF 页级分析结果的严格审核器。你只做判定，不做修复。",
            "scope": [
                "只判断 target_page 是否正确；previous_page 和 next_page 仅用于验证跨页语义连续性。",
                "判断对象是 layout 的 ownership、continuation gap、citation 索引与正文 ref，不是 OCR 逐字转录质量。",
                "只有发现具体违反下列标准的证据时才判错；不能仅因页面复杂、文本很长或缺少图片而判错。",
            ],
            "ownership_standard": [
                "paragraph 表示正文、标题、引文等主阅读流；citation 表示由正文引用标记引出的页脚注释。",
                "结合文字语义、位置和上下文判断归属，不能只看 bbox。页底对正文标记作解释的内容通常是 citation。",
                "页码、running header、章节栏头、装饰性短数字等 page furniture 不属于 paragraph 或 citation；若它们进入任一语义流，本页判错。",
            ],
            "continuation_standard": [
                "continues_from_previous / continues_to_next 表示同一流中相邻文字 layout 在语义上属于同一个自然段或同一条 citation，而不只是视觉上相邻。图片/表格可作为段内锚点，判断文字连接时跳过它们，资产自身 gap 为 false；独立段落公式才是硬边界。",
                "验证连接处的语法和语义：前段未完且后段自然续接才可连接；完整句、标题、页码、独立条目或话题重启不得连接。",
                "跨页开始或结束于半句话本身是合法的，只要 gap 与相邻页对应流的语义边界一致。",
                "paragraph 与 citation 是两条独立流；验证 paragraph gap 时对照 paragraph_boundary，验证 citation gap 时对照 citation_boundary。",
            ],
            "citation_standard": [
                "citation index 是页内索引：本页新开始的 citation 从 1 连续递增；mark 可为数字、圈号、字母、星号或其他符号。",
                "index 为 null 的片段可为已知跨页定义的续段，也可为没有确定起点的 unresolved 内容；不能擅自猜测归属或丢弃。",
                "如果本页脚注出现新的 mark，必须是本页 indexed citation，不能作为上一页 citation 的无索引延续。",
                "一条 citation 可由多个 layouts 组成；owned_layout_count 大于 1 本身完全合法。",
                "无索引 continuation 必须排在本页 indexed citations 之前；页面也可以完全没有 citation。",
            ],
            "reference_standard": [
                "正文 raw_text 中的真实引用标记应在 analysed_text 相同语义位置变成 ⟦REF⟧，references 给出其目标。",
                "每个正文 ref 必须指向存在的本页 citation index。一条定义允许重复引用；未匹配定义仍须保留，不能仅因缺少 ref 就将其丢弃。",
                "标记与 citation 必须在语义上对应；不要把普通序号、列表编号或正文符号误当成 ref。",
                "不要因为 OCR 标点、空格或个别错字而判错，除非它导致 ownership、gap、mark 或 ref 语义错误。",
            ],
            "decision_rule": (
                "逐项审核 target_page 的每个 layout 及 citation/ref 关系。全部满足才返回 true；"
                "只要有一个明确错误就返回 false。概率应表达依据这些严格标准，本页无需任何修正的可信度。"
            ),
        },
        "criteria": {
            "true": {
                "label": "PASS",
                "definition": (
                    "target_page 的 ownership、语义 continuation、页内 citation/index、"
                    "正文 ref 和 furniture 排除均符合 rubric，可以原样通过。"
                ),
            },
            "false": {
                "label": "FAIL",
                "definition": (
                    "target_page 至少存在一个有证据支持的 ownership、语义 continuation、"
                    "citation/index、正文 ref 或 furniture 错误，不能原样通过。"
                ),
            },
        },
    }
}


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
    """Score every page with JEV while leaving PageAnalysis unchanged."""

    def __init__(
        self,
        evaluator: PageEvaluator,
        threshold: float = JEV_REVIEW_THRESHOLD,
        batch_evaluator: PageBatchEvaluator | None = None,
    ) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("JEV review threshold must be between 0 and 1")
        self._evaluator = evaluator
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
        requests = build_jev_review_requests(
            source_pages, analyses, page_pixel_sizes
        )
        if self._batch_evaluator is not None:
            evaluated = await self._batch_evaluator(iter(requests))
        else:
            # Compatibility for custom evaluators. Official JEV runtimes use
            # the lazy executor-backed batch evaluator above.
            evaluated = []
            for page_index, request in requests:
                try:
                    probability = await self._evaluator(page_index, request)
                except Exception as error:
                    from ..._policy import recover
                    from ...policies import FailureCategory, FailureLocation
                    await recover(error, FailureCategory.ANALYSIS, 'jev_review', 'algorithm_result',
                                  targets=(FailureLocation('page', page_indexes=(page_index,)),))
                    continue
                evaluated.append((page_index, probability))
        self.results = []
        for page_index, pass_probability in evaluated:
            if not 0 <= pass_probability <= 1:
                raise ValueError("JEV pass probability must be between 0 and 1")
            risk = round(1 - pass_probability, 12)
            self.results.append(PageReviewResult(
                page_index=page_index,
                pass_probability=pass_probability,
                risk=risk,
                requires_review=risk >= self._threshold,
            ))
        return analyses


def build_jev_review_requests(
    source_pages: Iterable[Page],
    analyses: Iterable[PageAnalysis],
    page_pixel_sizes: Mapping[int, tuple[int, int]],
    *, includes_footnotes: bool = True,
) -> Iterable[tuple[int, dict[str, Any]]]:
    """Build the selected PageReviewPacket v2 prompt for every page."""

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
            yield page_index, {
                "state": {
                    "format": "pdf-craft PageReviewPacket v2",
                    "coordinate_note": (
                        "bbox_normalized is [left, top, right, bottom] in page fractions."
                    ),
                    "target_page": target,
                    "previous_page": _boundary_context(
                        packets.get(page_index - 1), "previous", includes_footnotes
                    ),
                    "next_page": _boundary_context(
                        packets.get(page_index + 1), "next", includes_footnotes
                    ),
                },
                "questions": _review_questions(includes_footnotes),
            }
    return generate()


def _review_questions(includes_footnotes: bool) -> dict[str, Any]:
    if includes_footnotes:
        return JEV_QUESTION
    questions = deepcopy(JEV_QUESTION)
    question = questions[JEV_QUESTION_NAME]
    instructions = question['instructions']
    instructions['scope'] = [
        "只判断 target_page；相邻初级页仅用于验证正文语义连续性，不评估 OCR 逐字转录质量。",
        "本次关闭脚注提取。不要检查、要求或重建 citation、脚注索引及正文引用关系。",
        "只有具体语义错误才判错，不能因标点、空格、页复杂或缺少图片而判错。",
    ]
    instructions['ownership_standard'] = [
        "paragraph 是正文、标题、引文等主阅读流；页码、running header 和装饰物不应进入此流。",
        "关闭脚注指最终不输出脚注，不代表 OCR 原分类必然正确；不建立 citation 或引用体系。",
        "原 footnote 来源候选默认 ignored；若有明确证据属于正文或跨页正文续文，应判为需要修正以恢复 paragraph。",
        "原 body 中有明确证据属于脚注的完整块应 ignored，否则需要修正；不能仅凭页底、小字号或数字开头判为脚注。",
        "同时包含正文与脚注的块不能整块忽略；不确定时保留初级归属，不要求删除正文或重建缺失文字。",
    ]
    instructions['continuation_standard'] = [
        "continuation 表示同一自然段的语义续接，不能仅因位置相邻而连接。",
        "完整句、标题、独立条目或话题重启不得连接；页首半句话可合法承接前页。",
        "图片/表格是透明段内锚点，资产自身 gap 为 false；独立段落公式是硬边界。",
        "相邻页 paragraph_boundary 及 excluded_candidates 是初级证据；后者保留默认排除的 footnote 来源，可用于辨认跨页正文误分类，不是已经精校的结果。",
    ]
    del instructions['citation_standard'], instructions['reference_standard']
    instructions['decision_rule'] = "审核全部候选的正文/排除归属、正文续接及 furniture 排除；有明确错误才返回 false。"
    question['criteria']['true']['definition'] = "正文归属、语义续接及 furniture 排除正确，可原样通过。"
    question['criteria']['false']['definition'] = "正文归属、语义续接或 furniture 排除存在明确错误。"
    return questions


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
        raw_text = _content_text(source.content)
        analysed_text = raw_text
        source_type = getattr(source, "ref")
    width, height = size
    bbox = source.bbox
    return {
        "document_order": layout.document_order,
        "source_order": source_order,
        "source_type": source_type,
        "ocr_stream": getattr(getattr(source, "origin", None), "stream", None),
        "bbox_normalized": [
            round(bbox[0] / width, 3),
            round(bbox[1] / height, 3),
            round(bbox[2] / width, 3),
            round(bbox[3] / height, 3),
        ],
        "raw_text": _excerpt(raw_text),
        "analysed_text": _excerpt(analysed_text),
        "ownership": annotations.ownership,
        "continues_from_previous": annotations.continues_from_previous,
        "continues_to_next": annotations.continues_to_next,
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


def _boundary_context(
    packet: dict[str, Any] | None,
    side: str,
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
    boundary = slice(-2, None) if side == "previous" else slice(None, 2)
    candidates = {} if includes_footnotes else {'excluded_candidates': [
        layout for layout in packet['layouts']
        if layout['ownership'] == 'ignored' and layout['ocr_stream'] == 'footnote'
    ][boundary]}
    return {
        "page_index": packet["page_index"],
        "ocr_body_layout_count": packet["ocr_body_layout_count"],
        "ocr_footnote_layout_count": packet["ocr_footnote_layout_count"],
        "citations": packet["citations"],
        "paragraph_boundary": paragraphs[boundary],
        "citation_boundary": citations[boundary],
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


def _excerpt(text: str, limit: int = 240) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:160] + " … " + normalized[-70:]


def _mark_text(mark: Any) -> str | None:
    if mark is None:
        return None
    return str(getattr(mark, "char", mark))
