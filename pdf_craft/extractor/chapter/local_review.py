"""Local body-only review: one editable decision per named question."""

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from .review_questions import question

LOCAL_REVIEW_THRESHOLD = 0.52
_QUESTIONS_PER_REQUEST = 4

_JOIN_RULES: dict[str, Any] = {'instructions': ['只用提供的局部原始文字判断两个块是否构成同一自然段。不是审核整页，不评价邻页自己的字段。',
                  '一个词语或语法结构被分到两个块时应连接。明确新的独立发言、标题或编号条目应另起。不因为主题相同、问答或句号机械决定。',
                  '不校对文字，不补足未提供的OCR缺字，不假设远处内容。当前块很长时不要忽略其开头与前块末尾的直接接缝。',
                  '当present_decision=false时，必须找到前块未完成的词语或不可独立的句法成分由当前块直接补足，才判该字段错误；两个已经完整的表达分成两段是合法的。并且、因此、转折和相同话题均不强迫合段。',
                  '引出引文的段落与后面的独立引文可以分段；引文后的作者署名、出处也可以独立成段。冒号和出处依附关系不是必须合段的证据。',
                  '只判现有入向决定是否有确定的结构错误。不能从整框位置猜测没有提供的首行缩进，也不以自己的排版喜好强制拆段或合段。'],
 'conditional': '当前字段为续接：先组合前块末尾和当前开头的一两个字检查完整词语。词语的前后两部分恰好被跨页分开，保持续接就是正确的，不能被当前块后续的其他完整句误导。',
 'criteria': {'true': '当前块确为前块所属自然段的续文，本次续接是合法的。', 'false': '当前块必须另起自然段，现有续接把两个独立段落错误合并。'}}


_OWNERSHIP_STANDARD: list[str] = [
    "paragraph 是正文、标题、引文等主阅读流；页码、running header 和装饰物不应进入此流。",
    "关闭脚注指最终不输出脚注，不代表 OCR 原分类必然正确；不建立 citation 或引用体系。",
    "原 footnote 来源候选默认 ignored；若有明确证据属于正文或跨页正文续文，应判为需要修正以恢复 paragraph。",
    "原 body 中有明确证据属于脚注的完整块应 ignored，否则需要修正；不能仅凭页底、小字号或数字开头判为脚注。",
    "同时包含正文与脚注的块不能整块忽略；不确定时保留初级归属，不要求删除正文或重建缺失文字。",
    "必须逐块检查全部脚注范围，包括不带编号的后续解释、图式及跨页续文；只排除编号开头而保留无编号续段仍是错误。相邻初级页也可能误分类，不应据此放行。",
    "转下页/接上页及独立页码不是正文。正文跨页连接应跳过这些出版提示和脚注，不能把它们接到真实正文。",
    "成页或章末校释是主阅读内容，属于paragraph；页底附属于正文引用的注释才属于脚注。不能仅因编号或释义体裁就排除成页校释。",
    "关闭脚注时，正文中遗留①、上标数字、星号等原始引用字符完全合法；没有相应citation/ref也合法。只有实际把注释解释文字混入正文才是归属错误。"
]

def local_review_requests(state: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield bounded question batches with the same immutable raw neighborhood."""
    page = state["target_page"]["page_index"]
    index: dict[str, Any] = {}
    jobs: dict[str, list[tuple[str, dict[str, Any], dict[str, Any]]]] = {}
    previous: dict[str, Any] = {}
    available: list[str] = []

    def observe(page_index, layout):
        nonlocal previous, available
        if layout["source_type"] in ("formula", "equation"):
            if layout["ownership"] == "paragraph":
                previous, available = {}, []
            return
        if (layout["source_type"] != "text" or not layout.get("raw_text", "").strip()
                or layout["source_order"] is None):
            return
        key = f"p{page_index}_s{layout['source_order']}"
        index[key] = dict(page_index=page_index, source_order=layout["source_order"],
            text_end=layout["raw_text"][-200:], bbox_normalized=layout["bbox_normalized"],
            ocr_stream=layout["ocr_stream"], ownership_hint=layout["ownership"])
        available.append(key)
        if layout["ownership"] == "paragraph":
            previous = layout

    prior = state.get("previous_page")
    if prior is not None:
        found = {layout["document_order"]: layout
                 for field in ("paragraph_boundary", "citation_boundary", "excluded_candidates")
                 for layout in prior.get(field, [])}
        for _, layout in sorted(found.items()):
            observe(prior["page_index"], layout)
    for layout in state["target_page"]["layouts"]:
        raw, order = layout.get("raw_text", "").strip(), layout["source_order"]
        if layout["source_type"] == "text" and raw and order is not None:
            if layout["ownership"] == "paragraph" and available:
                key = f"gap_{order}"
                decision = layout["continues_from_previous"]
                check = dict(source_order=order, present_decision=decision,
                    current_start=layout["raw_text"][:200], preceding_source_candidates=list(available),
                    immediate_paragraph_hint=None if not previous else dict(
                        source_order=previous["source_order"], text_end=previous["raw_text"][-200:]))
                if decision:
                    prompt = dict(type="noul", instructions=[
                        f"此题只审核checks.{key}的现有操作：将当前块续接到前面的正文，作为同一自然段。完整三页证据在local_raw_evidence。",
                        "先在preceding_source_candidates对应的preceding_source_index中寻找真实正文前驱，再审核当前入向。ownership_hint和immediate_paragraph_hint不是事实，原OCR/算法可能把脚注排成paragraph；不能只拿数组最后一项与当前块比较。",
                        "若最近一项是注释、页码或页眉，应跳过它，核对前面的真实正文候选。只判断当前页的入向，不因邻页自己的归属错误单独否决本题。不得使用三页以外的内容，也不得把next_page作为入向前驱。",
                        "真实正文前驱必须是排除附属物后距离当前最近的正文。不能跳过中间一块真实正文去挑更早、看起来能接的语句；候选不是可任意拼接的语料库。若最近正文已经完整结束，当前块独立另起是合法的，即使叙述同一事件。",
                        *_JOIN_RULES["instructions"],
                        _JOIN_RULES["conditional"]], criteria=_JOIN_RULES["criteria"])
                    jobs[key] = [(key, prompt, check)]
                else:
                    jobs[key] = []
                    for part in ("word", "syntax"):
                        name = f"{key}_{part}"
                        jobs[key].append((name, question(f"gap_{part}", name), check))
            key = f"owner_{order}"
            owner = layout["ownership"]
            check = dict(source_order=order, ownership=owner, current_start=layout["raw_text"][:240])
            if owner == "paragraph":
                meaning = "保留为主阅读流paragraph"
                criteria = {"true": "当前块属于正文、标题、引文或独立校释等主阅读内容，保留是合法的。",
                            "false": "当前块明确属于应排除的脚注、页眉页码等，却被保留在主阅读流。"}
            else:
                meaning = "排除为ignored"
                criteria = {"true": "当前块应被排除，或没有充分证据恢复为主阅读内容；现有排除可接受。",
                            "false": "当前块明确属于正文、标题、引文或独立校释，却被错误排除。"}
            prompt = dict(type="noul", instructions=[
                f"此题只审核target_page中source_order={order}的现有操作：{meaning}，位置索引为checks.{key}。只审这一块归属，不审其他块或自然段入向。",
                "本次关闭脚注提取，不要求建立citation、脚注编号匹配或正文ref。原OCR分类、算法ownership都不是正确答案。",
                *_OWNERSHIP_STANDARD,
                "完整三页证据在local_raw_evidence。依据原文、已有标记和几何联合定位，不假设未提供的图像分隔线或文字。不要把别块的错误判给此块。清楚可修复的归属错误才FAIL，作者措辞、OCR错字或单纯不确定不是归属错误。"], criteria=criteria)
            jobs[key] = [(key, prompt, check)]
        observe(page, layout)
    raw_evidence = _raw_evidence(state)
    for family in ("gap", "owner"):
        keys = [key for key in jobs if key.startswith(family + "_")]
        for start in range(0, len(keys), _QUESTIONS_PER_REQUEST):
            chosen = keys[start:start + _QUESTIONS_PER_REQUEST]
            group = [job for key in chosen for job in jobs[key]]
            yield from review_batches(dict(local_raw_evidence=raw_evidence,
                                           preceding_source_index=index), group)


def _raw_evidence(value):
    if isinstance(value, dict):
        return {key: _raw_evidence(item) for key, item in value.items()
                if key not in ("continues_from_previous", "analysed_text")}
    if isinstance(value, list):
        return [_raw_evidence(item) for item in value]
    return value


def review_batches(state, jobs):
    """Copy only a bounded packet, never a full page per question up front."""
    for start in range(0, len(jobs), _QUESTIONS_PER_REQUEST):
        group = jobs[start:start + _QUESTIONS_PER_REQUEST]
        value = deepcopy(state)
        value["checks"] = deepcopy({name: check for name, _, check in group})
        yield {"state": value, "questions": deepcopy({name: prompt for name, prompt, _ in group})}
