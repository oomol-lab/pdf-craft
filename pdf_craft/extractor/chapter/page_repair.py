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
from .page_analysis import (
    AnalysedCitation,
    AnalysedLayout,
    LayoutAnnotations,
    PageAnalysis,
    ReferenceSpan,
    layout_reference_spans,
    layout_reference_text,
    replace_layout_references,
    project_definition_mark,
    restore_streams,
    continuation_sequences,
)
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


class RepairedLayout(_StrictModel):
    layout_id: str
    ownership: Literal["paragraph", "citation", "ignored"]
    continues_from_previous: bool
    continues_to_next: bool | None = None
    paragraph_role: Literal["body", "heading"] | None
    paragraph_level: int | None = Field(ge=-1)
    citation_id: str | None
    citation_index: int | None = Field(default=None, ge=1)
    preserve_as_image: bool = False


class RepairedCitation(_StrictModel):
    citation_id: str
    index: int | None = Field(default=None, ge=1)
    mark: str | None
    stream_order: int = Field(ge=0)
    continues_previous: bool = False


class RepairedPage(_StrictModel):
    page_index: int = Field(ge=1)
    layouts: list[RepairedLayout]
    citations: list[RepairedCitation]
    references: list[RepairedReference]


_SOURCE_CONTRACT = """不可变事实：目标页 page_index、layout 数量、layout_id、document_order、bbox、source_type 和 text。你不能增删、拆分、合并或重排 layout，也不能改动文字。"""

_ASSET_CONTRACT = """图片/表格可以嵌在同一个自然段内部，它们的位置不可移动，自身的两个 continuation 必须为 false。判断文字 continuation 时跳过这些锚点，连接同一归属流中的前一文字 layout；只有独立段落公式阻断这种连接。"""

_NEIGHBOR_CONTRACT = """相邻页仅提供不可变的初级内容上下文，不提供审核评分。跨出局部窗口的语义身份及跨纯图片/表格页的正文前驱，可能尚未绑定；不要猜测窗口外的身份或仅因看不到前驱而修改连接。这些关系由后续按序组段绑定，不是本次请求必须补齐的字段。若目标页带有 jev_p_pass，它只表示较弱审核器对当前页的估计，不是事实或约束；请独立检查，如果原结果正确可以原样返回。"""

_OWNERSHIP_RULES = """归属优先于续接：
- OCR 的 body/footnote 分类及初级 ownership 都可能出错。必须逐块检查，包括没有编号的页底续文；不要只修带编号的第一块。
- 脚注可有多个自然段、公式和跨页续文。紧随脚注的解释、引文、图式或续页段没有新编号，并不使它变成正文。根据完整阅读顺序、内容和几何识别整个范围。
- 编号是注释的起点，不是每个块必须重复的类型标签。同一小字阅读带中，解释或继续展开上一条脚注的无编号块仍属于该脚注；没有编号、换段或继续讨论同一主题都不是回归正文的证据。只有明确返回主阅读流的证据才结束这条注释。
- 跨页正文应连接上一页真正正文的末尾，不能连接页码、续页提示或脚注。先在心中分别梳理正文流与页底注释流，再判断每条连接。
- 单独的页码、“转下页/接上页”等出版提示属于 ignored，不是正文或脚注文字；不要将它和相邻真实内容拼成一句。
- 相邻初级结果同样可能误分类。只将其作为证据，不能因为相邻页暂将脚注续文标为正文，就让目标页沿用错误归属。"""

_READING_EXAMPLE = """阅读示例（不是按位置硬编码）：正文末块为“这个结论还需要”，下一页正文开头为“进一步讨论。”，
两者之间本页有“① 关于某术语的注解。”以及“换一种说法，这个术语也可以……”等小字块。
后一个无编号块如果延续注解，应仍属于①，不可因换了自然段就改为正文。下一页下方的“（接上页）……”
也要检查注释续接。关闭脚注时整个已确认注释范围 ignored；开启时所有这些块属于同一 citation，
自然段各自保留。text_scale_estimate 是由块面积和文字量推算的软线索，用于比较小字区域，
不是实际字号；需结合编号、内容、正文续接和几何，不能单凭这个数值删除内容。"""

PAGE_REPAIR_SYSTEM_PROMPT = f"""你负责检查并修正 PDF OCR 页的语义结构，只返回目标页完整 JSON。
{_SOURCE_CONTRACT}

先根据原文、阅读顺序和上下文独立划分正文、脚注、出版提示，再与初级结果比较。
初级结果仅是待审核的算法猜测，不是已经正确的模板。只改有证据的错误，但必须检查所有块。
{_OWNERSHIP_RULES}
{_READING_EXAMPLE}

正文是主阅读流；脚注是解释正文标记的独立阅读流，即使 OCR 把整片页脚标为 body 也要纠正。
编号脚注后的无编号块要先检查是否属于该脚注的后续自然段。若主阅读流在前面的未完句处跨页，
其后插入的编号注释及相邻解释不能被当成主阅读流的新正文。结合邻页真正正文的开头判定。
页脚跨页时通常在下一页正文下方续写，不能与下一页顶部主阅读流互接。

citations 描述本页脚注：新开始的每条注释分别建立 index=1,2,...、实际 mark 和
citation_id=new-页码-index；同一注释的多个块共享一个 ID，不同编号绝不能合为一个无索引注释。
已确定起点的跨页定义保持其 ID、起点和成员；但 unresolved 初级别名不是已确认定义，可以删除或拆分。
真正的续页注释 index/mark 为 null，排在本页新注释前；前驱在邻页尚未绑定时保留局部 unresolved ID，
若首块确实续接前页同一段则 continues_from_previous=true，最终在有序组段时绑定已采用的起点。
同一注释在下一页另起自然段时，citation.continues_previous=true，但 layout.continues_from_previous=false；
注释身份的延续与自然段的延续不是同一件事。图式之后新起解释段不能和图式标签拼成一句。
正文标记只连接本页已建立的有索引定义：references 给出 layout_id、citation_index、anchor。
anchor.before/mark/after 连续取自该 layout.text，mark 是实际引用字符，前后文须足以唯一定位。
没有可匹配标记的定义也要保留，不得为了省事将所有定义改成无索引。
独立的新注释即使 OCR 漏掉编号/星号，也应创建本页新 index，mark 可为 null，不要假装成前页的无索引续段。
印刷标记属于来源文字，不能改写；本地按 citation.mark 统一投影。不得在正文额外制造符号。

先定归属，再定自然段：同一个脚注内可以有多个段落、引文和图式，它们共享 ID 但不一定相连。
初级 continues_from_previous=false 的段落边界默认保留。只有前段明显未完、后段直接补齐同一个句子，
且没有独立新段证据时，才改为 true；前段已完整结束、后段从完整新句开始时必须保留 false。
论述同一主题、承接上文、同一个脚注 ID，都不构成合并自然段的理由。“或者/因此”等引导的新段也不能顺手并入前段。
图式后的解释可以是同一注释的新自然段；“接上页”只说明排版续页，不自动说明同一个自然段。
正文、脚注各自按阅读顺序判断；后继的 continues_from_previous 是唯一连接真源，省略 continues_to_next。
只连接语法、语义上确实同一自然段的块；主题相同不够，完整新句、独立引文和缩进留白支持另起一段。
不要将页码/续页提示接入任一流。跨页半句允许续接，不能因此把后续完整新段一并合并。
{_ASSET_CONTRACT}
{_NEIGHBOR_CONTRACT}

paragraph 的 role/level 为 body/-1 或有证据的 heading/层级。citation 文本同样需 role/level。
OCR 若把非线性的图式/分式示意图误当文字块，且这些标签不能组成线性句子，设置 preserve_as_image=true，
仍保留其归属和原始身份。程序将保留原图，不把二维标签挤成一行译文。普通正文、编号注释和行内公式不可用此选项。
ignored 不进入任何流：两个 gap=false，role/level/citation_id/citation_index 均为 null，无 ref。
返回所有原 layout，保持其身份、文字与顺序；citations 中不能有无成员定义。
遵守 output_contract，不解释、不加 Markdown 围栏。校验冲突时重新检查语义，而非拼接无关内容过关。
最终检查：若正文在脚注区上方以未完句截断，并与下一页正文首块续接，则这期间脚注区的其余解释块、图式
不能另充正文。注释有多个自然段很正常，所有未被新编号切开的同区解释块应共享上一条注释 ID。
"""


class JevLlmRepairProcessor:
    """Use JEV only for admission, then repair selected pages once with LLM."""

    def __init__(
        self,
        evaluator: PageEvaluator,
        request: PageRepairRequest,
        *,
        threshold: float = JEV_REVIEW_THRESHOLD,
        batch_evaluator: PageBatchEvaluator | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = None,
    ) -> None:
        self._retry_policy = retry_policy or RetryPolicy()
        self._error_policy = error_policy
        self._reviewer = JevReviewProcessor(
            evaluator, threshold, batch_evaluator=batch_evaluator,
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
    proposed_layouts = {_layout_id(layout): layout for layout in candidate.layouts}
    for original in source.layouts:
        if original.ownership == "citation" and original.citation_id in shared_ids:
            replacement = proposed_layouts[_layout_id(original)]
            if replacement.ownership != "citation" or replacement.citation_id != original.citation_id:
                raise ValueError(
                    f"[CROSS_PAGE_MEMBERSHIP] Preserve source member {_layout_id(original)} "
                    f"of shared definition {original.citation_id}; a single-page edit "
                    "cannot ignore or reassign it."
                )



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
    if target_pass_probability is not None:
        target['jev_p_pass'] = target_pass_probability
    payload = {
        "previous_page": _context_packet(previous_page),
        "target_page": target,
        "next_page": _context_packet(next_page),
        "output_contract": RepairedPage.model_json_schema(),
    }
    system_prompt = PAGE_REPAIR_SYSTEM_PROMPT if includes_footnotes else f"""
你负责恢复目标页的主阅读流，只返回完整 JSON。本次关闭脚注输出，不建立脚注编号或引用体系。
{_SOURCE_CONTRACT}
先独立识别正文与注释的完整范围，再对照初级算法归属；不能照抄初级 ownership。
{_OWNERSHIP_RULES}
{_READING_EXAMPLE}

输出规则：正文 paragraph；明确的注释、页码、出版提示、running header 为 ignored。
原 footnote 分类不是事实：若实际上属于正文，则恢复 paragraph。原 body 同样可能包含脚注，需排除。
脚注编号仅表示注释起点，其后的无编号解释、引文、图式或续页内容仍可能属于同一注释。
若正文在页脚区之前截断，并在下一页首块继续，期间脚注区中的同级解释不可另外当成正文。
应先用相邻页开头确认真正正文如何续接，再决定主阅读流范围；不要让脚注内容插入主阅读流。

只有明确的内容、邻文和几何证据才能排除块，不能仅因页底、小字或编号而误删正文。
混合正文与注释的块不能整块 ignored，不做块内删改。无法确定时保留初级归属。
paragraph 的 role/level 为 body/-1 或有依据的 heading/层级；同一自然段才能 continues_from_previous=true。
初级 false 的自然段边界默认保留；只有明确的未完句直接续接才能改为 true。
前段完整结束、后段从完整新句开始时必须保留 false，主题相关不构成合段理由。
完整新句、独立引文或话题重启保留另起段落。省略 continues_to_next，由本地计算。
{_ASSET_CONTRACT}
{_NEIGHBOR_CONTRACT}
ignored 的 gap=false，role/level/citation_id/citation_index 为 null；不能建立引用关系。
所有 layouts 的 citation_id/citation_index 必须为 null，citations/references 必须为空。
保留全部原始 layout 身份、文字和顺序，遵守 output_contract，不输出解释或 Markdown 围栏。
最终检查：明确属于注释区域的无编号续文也应 ignored，不能只排除编号开头的第一块。
"""
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
    return {
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


def _compile_repaired_page(
    source: PageAnalysis, response: RepairedPage,
    available_citations: Mapping[str, AnalysedCitation] | None = None,
) -> PageAnalysis:
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
                                   else original.continues_to_next if proposed.ownership != "ignored" else False),
                paragraph_role=proposed.paragraph_role,
                paragraph_level=proposed.paragraph_level,
                citation_id=proposed.citation_id,
                citation_index=proposed.citation_index,
                references=[],
                preserve_as_image=proposed.preserve_as_image,
            ),
        )
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
        spans_by_layout.setdefault(reference.layout_id, []).append(
            ReferenceSpan(
                start=start,
                end=end,
                citation_page_index=source.page_index,
                citation_index=reference.citation_index,
                mark_text=text[start:end],
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
        previous: tuple[str, AnalysedLayout] | None = None
        for layout_id, layout in sequence:
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
        if old is None and item.citation_id != f"new-{source.page_index}-{item.index}":
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
            stream_order=old.stream_order if old is not None else item.stream_order,
            origin_page_index=(old.origin_page_index if old is not None else source.page_index),
            continues_previous=item.continues_previous,
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
