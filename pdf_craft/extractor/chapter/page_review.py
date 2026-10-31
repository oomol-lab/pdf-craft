"""Page-level semantic review between resolution and stream restoration."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from math import sqrt
from unicodedata import east_asian_width
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ...pdf import Page
from .page_analysis import AnalysedLayout, PageAnalysis
from .local_review import LOCAL_REVIEW_THRESHOLD, local_review_requests


JEV_REVIEW_THRESHOLD = 0.45
JEV_QUESTION_NAME = "page_passes_strict_standard"

# Footnote review is separate from the body-only per-decision protocol.
_FOOTNOTE_QUESTIONS: dict[str, Any] = {
    "page_passes_strict_standard": {
        "type": "noul",
        "instructions": {
            "role": "你是 PDF 页级分析结果的严格审核器。你只做判定，不做修复。",
            "scope": [
                "只判断 target_page 是否正确；previous_page 和 next_page 仅用于验证跨页语义连续性。",
                "判断对象是 layout 的 ownership、continuation gap、citation 索引与正文 ref，不是 OCR 逐字转录质量。",
                "只有发现具体违反下列标准的证据时才判错；不能仅因页面复杂、文本很长或缺少图片而判错。",
                "只看target自身已提供的结构决定。邻页的末段未完、误分类、入向错误等都不能单独让target失败；target页末是半句话也不算错误。",
                "忽略空文字块、OCR纯标点噪声及内部编号/占位元信息；它们不是此轮自然段语义判断的依据。不要审核标题最终层级、字体排版或TOC。"
            ],
            "ownership_standard": [
                "paragraph 表示正文、标题、引文等主阅读流；citation 表示由正文引用标记引出的页脚注释。",
                "结合文字语义、位置和上下文判断归属，不能只看 bbox。页底对正文标记作解释的内容通常是 citation。",
                "页码、running header、章节栏头、装饰性短数字等 page furniture 不属于 paragraph 或 citation；若它们进入任一语义流，本页判错。",
                "OCR 原分类与初级 ownership 不是事实。逐块检查脚注全部范围，包括没有编号的后续解释、图式及跨页续文；只纠正带编号的第一块而遗漏其余部分仍应判错。",
                "相邻页可能也误分类。跨页正文必须接真正的正文，不能接脚注或转下页/接上页等出版提示。",
                "成页或章末校释是主阅读内容，属于paragraph；页底附属于正文引用的注释才属于脚注。不能仅因编号或释义体裁就排除成页校释。"
            ],
            "continuation_standard": [
                "只审核target内各块的continues_from_previous（是否接前一同流文字块）。出向标记由后块入向派生，未提供，也不是待审核字段；target页尾是否接入后页由后页负责，不能把邻页的标记错误判给target。",
                "continues_from_previous 表示同一流中相邻文字 layout 在语义上属于同一个自然段或同一条 citation，而不只是视觉上相邻。图片/表格可作为段内锚点，判断文字连接时跳过它们，资产自身 gap 为 false；独立段落公式才是硬边界。",
                "验证连接处的语法和语义：前段未完且后段自然续接才可连接；明确另起的新自然段、标题、页码、独立条目或话题重启不得连接。",
                "跨页开始或结束于半句话本身是合法的，只要 gap 与相邻页对应流的语义边界一致。",
                "paragraph 与 citation 是两条独立流；验证 paragraph gap 时对照 paragraph_boundary，验证 citation gap 时对照 citation_boundary。",
                "一个自然段可有多个完整句。句号不意味着段落结束；相反，同一句中的词语被切成两个layout时，应保持续接。跨页同理。独立引文不因前面有冒号就必须接成同一段。",
                "相邻初级页可能误归属。寻找真实正文前驱，不要机械地把邻页数组最后一项当作前驱。前页正文后面混入的脚注既不是正文续接对象，也不能遮蔽真实正文前驱；若target确实续接那段正文而入向false，target仍需修正。"
            ],
            "citation_standard": [
                "citation index 是页内索引：本页新开始的 citation 从 1 连续递增；mark 可为数字、圈号、字母、星号或其他符号。",
                "index 为 null 的片段可为已知跨页定义的续段，也可为没有确定起点的 unresolved 内容；不能擅自猜测归属或丢弃。",
                "如果本页脚注出现新的 mark，必须是本页 indexed citation，不能作为上一页 citation 的无索引延续。",
                "一条 citation 可由多个 layouts 组成；owned_layout_count 大于 1 本身完全合法。",
                "无索引 continuation 必须排在本页 indexed citations 之前；页面也可以完全没有 citation。"
            ],
            "reference_standard": [
                "正文 raw_text 中的真实引用标记应在 analysed_text 相同语义位置变成 ⟦REF⟧，references 给出其目标。",
                "每个正文 ref 必须指向存在的本页 citation index。一条定义允许重复引用；未匹配定义仍须保留，不能仅因缺少 ref 就将其丢弃。",
                "标记与 citation 必须在语义上对应；不要把普通序号、列表编号或正文符号误当成 ref。",
                "不要因为 OCR 标点、空格或个别错字而判错，除非它导致 ownership、gap、mark 或 ref 语义错误。"
            ],
            "decision_rule": "判断是否存在可具体定位到target某一块/相邻块、且可通过改变归属、入向续接或已有引用关系修复的结构错误。仅有模糊怀疑不等于错误；不以假设的原图、遗漏文字或对邻页的要求判错。",
            "boundary_examples": [
                {
                    "case": "target首块是上一页未完句的直接续文，continues_from_previous=true",
                    "verdict": "这条入向正确；跨页截断本身不是错误。"
                },
                {
                    "case": "target末块以未完句结束，next_page首块如何标注尚有问题",
                    "verdict": "不以此判target错误；后页的入向由后页负责。"
                },
                {
                    "case": "前块在一个语法词组中途截断，当前块补足这个词组，但当前continues_from_previous=false",
                    "verdict": "错误：同一句被断开，应续接。"
                },
                {
                    "case": "前块引出引文，后块是独立缩进的引文，continues_from_previous=false",
                    "verdict": "合法：引文可以是独立自然段，冒号不强迫合段。"
                },
                {
                    "case": "正文有原始引用字符，对应现存同页脚注定义",
                    "verdict": "检查已有定义和引用的实际对应；不能仅凭字符形状要求删除或重写原文。"
                },
                {
                    "case": "独立校释章节或章末注释，以编号逐项解释文献",
                    "verdict": "主阅读内容，不应被当成页底脚注排除。"
                },
                {
                    "case": "target某块明确解释正文标记①，位于独立页底注释区域，却被标paragraph",
                    "verdict": "归属错误，应改为citation。"
                },
                {
                    "case": "前块是已结束的编号条目1，当前块以新的编号条目2开始，但当前入向true",
                    "verdict": "错误：独立条目被合并，当前块应入向false。"
                }
            ]
        },
        "criteria": {
            "true": {
                "label": "PASS",
                "definition": "target没有可以具体定位、有证据支持的可修复结构错误；可以保持现有正文/排除及入向关系。"
            },
            "false": {
                "label": "FAIL",
                "definition": "target至少一处现有归属、入向续接或已有citation/reference关系明确错误。必须能指出实际来源块及其错误决定；不因潜在风险、邻页错误或OCR缺字而失败。"
            }
        }
    }
}


def text_scale_estimate(text, bbox):
    """Soft layout evidence, not font metrics or an ownership decision.

    OCR block height alone conflates font size and line count. Approximate
    occupied glyph area makes long small-print continuations comparable with
    short marked notes. Whitespace and Latin's narrower advance are accounted
    for, but formulas/leading make this only a hint to the semantic reviewer.
    """
    units = sum(1 if east_asian_width(char) in {'W', 'F'} else .5
                for char in text if not char.isspace())
    return round(sqrt(max(0, bbox[2]-bbox[0])*max(0, bbox[3]-bbox[1])/max(1, units)), 2)


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
    ) -> None:
        threshold = review_threshold(includes_footnotes) if threshold is None else threshold
        if not 0 <= threshold <= 1:
            raise ValueError("JEV review threshold must be between 0 and 1")
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
        expected: dict[int, int] = {}

        def requests():
            for index, packet in build_jev_review_requests(
                    source_pages, analyses, page_pixel_sizes, includes_footnotes=self._includes_footnotes):
                expected[index] = expected.get(index, 0) + 1
                yield index, packet

        if self._batch_evaluator is not None:
            evaluated = await self._batch_evaluator(requests())
        else:
            evaluated = []
            for page_index, request in requests():
                try:
                    probability = await self._evaluator(page_index, request)
                except Exception as error:
                    from ..._policy import recover
                    from ...policies import FailureCategory, FailureLocation
                    await recover(error, FailureCategory.ANALYSIS, 'jev_review', 'algorithm_result',
                                  targets=(FailureLocation('page', page_indexes=(page_index,)),))
                    continue
                evaluated.append((page_index, probability))
        probabilities: dict[int, list[float]] = {}
        for page_index, probability in evaluated:
            if not 0 <= probability <= 1:
                raise ValueError("JEV pass probability must be between 0 and 1")
            probabilities.setdefault(page_index, []).append(probability)
        self.results = []
        for page_index, count in expected.items():
            values = probabilities.get(page_index, [])
            # A missing batch is a failed review, never a successful partial page.
            if len(values) != count:
                continue
            probability = min(values)
            risk = 1 - probability
            self.results.append(PageReviewResult(page_index, probability, risk, risk >= self._threshold))
        return analyses


def build_jev_review_requests(
    source_pages: Iterable[Page],
    analyses: Iterable[PageAnalysis],
    page_pixel_sizes: Mapping[int, tuple[int, int]],
    *, includes_footnotes: bool = True, target_page_index: int | None = None,
) -> Iterable[tuple[int, dict[str, Any]]]:
    """Build local body-only batches or the dedicated footnote-page rubric."""

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
                    "bbox_normalized is [left, top, right, bottom] in page fractions. "
                    "text_scale_estimate is a rough OCR-pixel glyph-area scale, not actual font size; "
                    "compare small-print blocks with their neighbours, never classify by this alone."
                ),
                "target_page": target,
                "previous_page": _boundary_context(
                    packets.get(page_index - 1), "previous", includes_footnotes
                ),
                "next_page": _boundary_context(
                    packets.get(page_index + 1), "next", includes_footnotes
                ),
            }
            if includes_footnotes:
                yield page_index, {"state": state, "questions": _review_questions()}
            else:
                for request in local_review_requests(state):
                    yield page_index, request
    return generate()


def review_threshold(includes_footnotes: bool) -> float:
    """Mode-specific internal decisions, not a caller-controlled cost quota."""
    return JEV_REVIEW_THRESHOLD if includes_footnotes else LOCAL_REVIEW_THRESHOLD


def _review_questions() -> dict[str, Any]:
    """Return a private copy of the dedicated footnote-enabled rubric."""
    return deepcopy(_FOOTNOTE_QUESTIONS)


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
