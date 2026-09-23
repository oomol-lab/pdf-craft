"""LLM repair of one reversible page with deterministic integrity checks."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from ...llm import Message, MessageRole
from ...llm.guaranteed import GuaranteedOptions, request_guaranteed_json
from ...pdf import Page
from .page_analysis import (
    AnalysedCitation,
    AnalysedLayout,
    LayoutAnnotations,
    PageAnalysis,
    ReferenceSpan,
    layout_reference_spans,
    layout_reference_text,
    replace_layout_references,
)
from .page_review import (
    JEV_REVIEW_THRESHOLD,
    JevReviewProcessor,
    PageEvaluator,
    PageReviewResult,
)


PageRepairRequest = Callable[[list[Message], int, int], str]
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


class RepairedLayout(_StrictModel):
    layout_id: str
    ownership: Literal["paragraph", "citation", "ignored"]
    continues_from_previous: bool
    continues_to_next: bool
    paragraph_role: Literal["body", "heading"] | None
    paragraph_level: int | None
    citation_id: str | None
    citation_index: int | None = Field(default=None, ge=1)


class RepairedCitation(_StrictModel):
    citation_id: str
    index: int | None = Field(default=None, ge=1)
    mark: str | None
    stream_order: int = Field(ge=0)


class RepairedPage(_StrictModel):
    page_index: int = Field(ge=1)
    layouts: list[RepairedLayout]
    citations: list[RepairedCitation]
    references: list[RepairedReference]


PAGE_REPAIR_SYSTEM_PROMPT = """\
你负责修复 PDF OCR 页的语义分析结果。输入包含上一页、目标页和下一页的完整文本结构；只允许修改目标页。

不可变事实：目标页 page_index、layout 数量、layout_id、document_order、bbox、source_type 和 text。你不能增删、拆分、合并或重排 layout，也不能改动文字。

可编辑语义：layout 的 paragraph/citation/ignored 归属、前后 continuation、paragraph role/level、citation 分组与页内索引，以及正文 ref 的位置。页码、running header 和装饰物必须标为 ignored；ignored layout 仍保留在页面中，但不会进入任何恢复流。

相邻页只是历史现场，可能错误。相邻页的 jev_p_pass 是较弱审核器对其现有标注可靠性的估计，不是事实或约束。目标页分数被刻意隐藏，请独立检查；如果原结果正确，可以原样返回。

修改纪律：
- 现有标注是一套已闭环的候选结果，不是让你从头重做的空白草稿。默认保留每个字段；只在文本语义、几何位置或引用对应给出具体证据时修改。
- 发现页码或 running header 时，将该 layout 改为 ignored，但不要顺手改写与它无关的 citation、ref 或段落边界。
- 判断 gap 时，在心中直接拼接前一 layout 末尾和后一 layout 开头。只有拼接后语法与意义显然是同一自然段或同一条 citation 时才设为 true；空间相邻、冒号、缩进或排版分块单独都不足以决定连接。结合 bbox 判断：同缩进且行距正常的冒号引导句与后续文字可能是同一段；而前文说“阅读下面的段落”之类引导语，后面内容明显整体缩进且增加垂直留白时，后者是独立块引文，gap 应为 false。
- 对现有 false 边界采用很高的合并门槛。两个 layout 主题相关、后者解释前者、处在同一引文中，或普通行距接近，都不表示它们是同一自然段；完整句之后开始的新段必须保持 false。只有存在明确的 OCR/跨栏/跨页人工断裂证据，而且前后文字拼接后才形成一个原本无法各自成立的句子或同一脚注段落时，才把 false 改成 true。
- 特别检查页首的跨页尾句：如果某 layout 的 continues_from_previous 已为 true，它只是承接上一页并以句号等终止标点结束的短尾部，而下一 layout 从完整新句开始，则二者之间的既有 false 必须保留。不能因为同缩进、普通行距或主题连续，把上一段的页首尾句并入下一段。
- initial_stream_boundary_from_previous 是根据现有归属预先计算的几何提示：vertical_gap 是上下间距，left_delta 为正表示当前块左侧向内缩，right_inset_delta 为正表示当前块右侧也向内缩。它只是证据，归属有误时可以忽略；但同时出现明显左右缩进和较大留白时，应认真检查当前块是否为独立块引文。
- 同一流内两个相邻 layout 之间的边界是一个原子决定。修改边界时必须同时设置前者 continues_to_next 和后者 continues_from_previous，且两值相等；不要只改一侧再依赖校验循环提醒。
- 已有 citation_id、citation 记录与 ref 对应是强证据。citation 开头的 mark 已从 layout.text 拆入 citations[].mark，并在原所在 layout 上以 detached_citation_mark 提示。判断语义时要把它视为 layout.text 原本的前缀；不得因为 text 里看不到 mark 就把该 layout 改成 paragraph。
- citation 成员关系与 gap 是两个正交维度：多个 layout 可以都属于同一 citation_id，却因为是脚注内的不同自然段而将相邻 gap 设为 false。常见脚注会依次包含以冒号结尾的引导句、缩进引文和后续评论；它们可以是三个自然段，但仍全部属于同一 citation。不要为了拆段而把引导句改成 paragraph。
- 修好已有具体错误后就停止。页面被送来不意味着每种结构都有错，不得为了显示做过审查而制造额外修改。
- 校验错误只说明结果存在矛盾，不代表必须修改错误消息中的某一侧。回到文本语义判断哪个 ownership 或 gap 才是根因，不要为了过校验而拼接不相干的文字。

ref 规则：
- 每个 ref 返回 layout_id、citation_index 和 anchor。
- anchor.before、anchor.mark、anchor.after 表示同一 layout.text 中连续相邻的三段；mark 是 ref 覆盖的实际字符，before/after 只是定位上下文。
- 复制足以唯一定位的相邻原文，不要返回字符 offset。上下文允许短，但必须让程序无歧义地确定位置。
- OCR 完全漏掉标记时 mark 可以为空，表示 before 与 after 之间的零宽位置；此时上下文必须足以唯一定位。
- ref 只属于 paragraph 文本 layout。每个有索引 citation 恰好对应一个 ref，每个 ref 指向本页存在的有索引 citation。

ignored 规则：ignored layout 的两个 continuation 必须为 false，paragraph_role、paragraph_level、citation_id、citation_index 必须全部为 null，也不能承载 ref。

citation 规则：
- 本页新开始的 citation 使用从 1 连续递增的 index，并具有非空 mark。
- 上一页 citation 的续页片段 index 和 mark 都为 null，且必须排在有索引 citation 之前。
- citation layout 的 citation_id/index 必须与 citations 中相应项一致；paragraph layout 不得携带 citation_id/index。

返回前逐项自检：
1. 所有 ignored layout 的 gap 均为 false，所有语义字段均为 null。
2. 删去 ignored 后，按 document_order 查看相邻 paragraph：前者 continues_to_next 必须等于后者 continues_from_previous。不要把被忽略页码曾经携带的 gap 原样留给下一 layout。
3. 对每个 citation_id 独立做同样的相邻 gap 检查，不要把不同 citation 串成一条流。
4. 有索引 citation 与 references 按 citation_index 一一对应，每个 anchor 都只能在指定 layout_id 内定位到一处。

返回一个完整目标页 JSON，字段严格遵守给定 output_contract。不要解释，不要使用 Markdown 围栏。每次收到校验错误后，都重新返回完整目标页 JSON。"""


class JevLlmRepairProcessor:
    """Use JEV only for admission, then repair selected pages once with LLM."""

    def __init__(
        self,
        evaluator: PageEvaluator,
        request: PageRepairRequest,
        *,
        threshold: float = JEV_REVIEW_THRESHOLD,
        max_retries: int = 4,
    ) -> None:
        self._reviewer = JevReviewProcessor(evaluator, threshold)
        self._request = request
        self._max_retries = max_retries

    @property
    def results(self) -> list[PageReviewResult]:
        return self._reviewer.results

    def __call__(
        self,
        source_pages: list[Page],
        analyses: list[PageAnalysis],
        page_pixel_sizes: Mapping[int, tuple[int, int]],
    ) -> list[PageAnalysis]:
        self._reviewer(source_pages, analyses, page_pixel_sizes)
        reviews = {result.page_index: result for result in self.results}
        return _repair_pages(
            analyses=analyses,
            page_indexes=[
                result.page_index
                for result in self.results
                if result.requires_review
            ],
            reviews=reviews,
            request=self._request,
            max_retries=self._max_retries,
        )


class AllPageLlmRepairProcessor:
    """Repair selected or all pages with LLM without running or disclosing JEV."""

    def __init__(
        self,
        request: PageRepairRequest,
        *,
        page_indexes: Iterable[int] | None = None,
        max_retries: int = 4,
    ) -> None:
        self._request = request
        self._configured_page_indexes = (
            None if page_indexes is None else tuple(page_indexes)
        )
        self._max_retries = max_retries
        self.page_indexes: list[int] = []

    def __call__(
        self,
        source_pages: list[Page],
        analyses: list[PageAnalysis],
        page_pixel_sizes: Mapping[int, tuple[int, int]],
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
        return _repair_pages(
            analyses=analyses,
            page_indexes=self.page_indexes,
            reviews={},
            request=self._request,
            max_retries=self._max_retries,
        )


def _repair_pages(
    *,
    analyses: list[PageAnalysis],
    page_indexes: list[int],
    reviews: Mapping[int, PageReviewResult],
    request: PageRepairRequest,
    max_retries: int,
) -> list[PageAnalysis]:
    repaired = [_clone_page(page) for page in analyses]
    positions = {page.page_index: index for index, page in enumerate(repaired)}
    repair_order: dict[int, int] = {}
    for page_index in page_indexes:
        position = positions[page_index]
        target = repaired[position]
        previous = repaired[position - 1] if position > 0 else None
        following = repaired[position + 1] if position + 1 < len(repaired) else None
        repaired[position] = repair_page_with_llm(
            previous_page=previous,
            target_page=target,
            next_page=following,
            previous_pass_probability=_neighbor_probability(previous, reviews),
            next_pass_probability=_neighbor_probability(following, reviews),
            request=request,
            max_retries=max_retries,
        )
        repair_order[page_index] = len(repair_order)
    _reconcile_cross_page_gaps(repaired, repair_order)
    return repaired


def repair_page_with_llm(
    *,
    previous_page: PageAnalysis | None,
    target_page: PageAnalysis,
    next_page: PageAnalysis | None,
    previous_pass_probability: float | None,
    next_pass_probability: float | None,
    request: PageRepairRequest,
    max_retries: int = 4,
) -> PageAnalysis:
    """Repair exactly ``target_page`` using full text-only neighbor context."""

    messages = build_page_repair_messages(
        previous_page=previous_page,
        target_page=target_page,
        next_page=next_page,
        previous_pass_probability=previous_pass_probability,
        next_pass_probability=next_pass_probability,
    )
    return request_guaranteed_json(GuaranteedOptions(
        messages=messages,
        request=request,
        schema=RepairedPage,
        parse=lambda data, _index, _maximum: _compile_repaired_page(
            target_page, cast(RepairedPage, data)
        ),
        max_retries=max_retries,
    ))


def build_page_repair_messages(
    *,
    previous_page: PageAnalysis | None,
    target_page: PageAnalysis,
    next_page: PageAnalysis | None,
    previous_pass_probability: float | None,
    next_pass_probability: float | None,
) -> list[Message]:
    payload = {
        "previous_page": _context_packet(
            previous_page, previous_pass_probability
        ),
        "target_page": _page_packet(target_page),
        "next_page": _context_packet(next_page, next_pass_probability),
        "output_contract": RepairedPage.model_json_schema(),
    }
    return [
        Message(MessageRole.SYSTEM, PAGE_REPAIR_SYSTEM_PROMPT),
        Message(
            MessageRole.USER,
            json.dumps(payload, ensure_ascii=False, indent=2),
        ),
    ]


def _context_packet(
    page: PageAnalysis | None,
    pass_probability: float | None,
) -> dict | None:
    if page is None:
        return None
    return {
        "jev_p_pass": pass_probability,
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
        previous = previous_by_stream.get(stream_key) if stream_key else None
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
            previous_by_stream[stream_key] = layout
    return {
        "page_index": page.page_index,
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
    detached_citation_mark: str | None,
    previous_in_initial_stream: AnalysedLayout | None,
) -> dict:
    try:
        text = layout_reference_text(layout)
    except ValueError:
        text = ""
    return {
        "layout_id": _layout_id(layout),
        "document_order": layout.document_order,
        "source_order": layout.source_order,
        "source_type": getattr(layout.source, "ref", "text"),
        "bbox": list(layout.bbox),
        "text": text,
        "detached_citation_mark": detached_citation_mark,
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


def _initial_stream_key(layout: AnalysedLayout) -> tuple[str, str | None] | None:
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


def _compile_repaired_page(source: PageAnalysis, response: RepairedPage) -> PageAnalysis:
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

    citations = _compile_citations(source, response.citations, issues)
    citations_by_id = {citation.citation_id: citation for citation in citations}
    repaired_layouts: dict[str, AnalysedLayout] = {}
    for layout_id, original in source_layouts.items():
        proposed = response_layouts[layout_id]
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
            original,
            annotations=LayoutAnnotations(
                ownership=proposed.ownership,
                continues_from_previous=proposed.continues_from_previous,
                continues_to_next=proposed.continues_to_next,
                paragraph_role=proposed.paragraph_role,
                paragraph_level=proposed.paragraph_level,
                citation_id=proposed.citation_id,
                citation_index=proposed.citation_index,
                references=[],
            ),
        )
    _validate_layout_sequences(repaired_layouts, citations_by_id, issues)
    if issues:
        raise ValueError("\n".join(issues))

    spans_by_layout: dict[str, list[ReferenceSpan]] = {}
    reference_indexes: list[int] = []
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
        spans_by_layout.setdefault(reference.layout_id, []).append(
            ReferenceSpan(
                start=start,
                end=end,
                citation_page_index=source.page_index,
                citation_index=reference.citation_index,
                mark_text=text[start:end],
            )
        )
        reference_indexes.append(reference.citation_index)

    # Do not emit derived bijection errors while any anchor is unresolved.
    if issues:
        raise ValueError("\n".join(issues))

    expected_indexes = sorted(indexed_citations)
    actual_indexes = sorted(reference_indexes)
    if actual_indexes != expected_indexes:
        issues.append(
            "[REF_CITATION_BIJECTION] Indexed citations and refs must correspond "
            f"one-to-one. expected={expected_indexes}, actual={actual_indexes}."
        )
    if issues:
        raise ValueError("\n".join(issues))

    compiled_layouts: list[AnalysedLayout] = []
    for original in source.layouts:
        layout_id = _layout_id(original)
        layout = repaired_layouts[layout_id]
        if layout.ownership == "paragraph":
            layout = replace_layout_references(
                layout, spans_by_layout.get(layout_id, [])
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
        previous: tuple[str, AnalysedLayout] | None = None
        for layout_id, layout in sequence:
            if layout.source_order is None and (
                layout.continues_from_previous or layout.continues_to_next
            ):
                issues.append(
                    f"[GAP_ASSET] layouts[{layout_id}] is an asset and cannot "
                    "carry continuation flags."
                )
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
            if layout.citation_id is not None:
                owned_citation_ids.add(layout.citation_id)
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
) -> list[AnalysedCitation]:
    existing = {citation.citation_id: citation for citation in source.citations}
    result: list[AnalysedCitation] = []
    seen_ids: set[str] = set()
    expected_index = 1
    found_indexed = False
    for position, item in enumerate(proposed):
        prefix = f"citations[{position}]"
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
            if item.mark is None or item.mark == "":
                issues.append(
                    f"[CITATION_MARK] {prefix}.mark must be non-empty for an "
                    "indexed citation."
                )
        old = existing.get(item.citation_id)
        mark = old.mark if old is not None and _mark_text(old.mark) == item.mark else item.mark
        result.append(AnalysedCitation(
            citation_id=item.citation_id,
            page_index=source.page_index,
            index=item.index,
            mark=mark,
            stream_order=item.stream_order,
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
    return f"layout-{layout.document_order}"


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


def _reconcile_cross_page_gaps(
    pages: list[PageAnalysis],
    repair_order: Mapping[int, int],
) -> None:
    citation_ids = sorted({
        layout.citation_id
        for page in pages
        for layout in page.layouts
        if layout.ownership == "citation" and layout.citation_id is not None
    })
    groups: list[tuple[str, str | None]] = [("paragraph", None)]
    groups.extend(("citation", citation_id) for citation_id in citation_ids)
    for ownership, citation_id in groups:
        layouts = sorted(
            (
                layout
                for page in pages
                for layout in page.layouts
                if layout.ownership == ownership
                and (ownership == "paragraph" or layout.citation_id == citation_id)
            ),
            key=lambda layout: layout.document_order,
        )
        if layouts:
            layouts[0].annotations.continues_from_previous = False
            layouts[-1].annotations.continues_to_next = False
        for left, right in zip(layouts, layouts[1:]):
            if left.continues_to_next == right.continues_from_previous:
                continue
            if left.page_index == right.page_index:
                raise ValueError(
                    "LLM repair left an inconsistent same-page gap between "
                    f"{_layout_id(left)} and {_layout_id(right)}"
                )
            left_order = repair_order.get(left.page_index, -1)
            right_order = repair_order.get(right.page_index, -1)
            if left_order < 0 and right_order < 0:
                raise ValueError(
                    "Traditional page analysis contains an inconsistent gap "
                    f"between {_layout_id(left)} and {_layout_id(right)}"
                )
            value = (
                right.continues_from_previous
                if right_order > left_order
                else left.continues_to_next
            )
            left.annotations.continues_to_next = value
            right.annotations.continues_from_previous = value
