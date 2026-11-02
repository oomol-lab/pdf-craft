"""Local footnote review using bounded questions and immutable OCR evidence."""

from copy import deepcopy
from .footnote_quotation import quotation_requests, apply_quotation_proofs
from .footnote_completion import COMPLETION_TARGETS, completion_requests, apply_completion_proofs
from .reference import extract_head_mark
from .jointer import _parse_block_content
from .local_review import local_review_requests, review_batches
from .review_questions import question
from .review_evidence import _duplicates, _selected_strips, _image_starts
from .review_encoding import (
    compact_review_evidence as compact,
    TEXT_ENCODING_NOTE as _ENCODING,
    literal_reference_edits,
)
from .structural_review import _collect, aggregate_risks

FOOTNOTE_REVIEW_THRESHOLD = 0.45

_COMMON = "只审核checks指明的target当前决定。相邻页是只读上下文，不因邻页自身出错否决target。不校对错字、公式计算、作者观点，不补不存在的文字，不审核最终标题层级。只有能具体定位、可改变归属/续接/现有引用关系修复的错误才FAIL。模糊怀疑不算错误。"
_DEF = [
    _COMMON,
    "本页新开始的脚注定义应有从1连续的页内index和相应印刷mark。实际mark可以是数字、圈号、字母、星号，不是内部ID。新mark不可作为前条无索引延续。",
    "跨页或无法确定起点的注释允许index=null；不要猜测缺失起点或强制匹配。一个注释可以有多个自然段/layout，无须给每段新index。不同的新注释不能仅因相邻而合成同一无索引定义。",
    "只评价给定注释定义及成员，不把正文错字或邻页误归属作为本页定义错误。页码误入citation归ownership问题，不借此制造虚假的注释索引。",
]
_REF = [
    _COMMON,
    "只检查本块已有引用的字面对应，不检查段落续接、原文是否完整、注释是否解释得通。raw_text是原文，analysed_text中的每个⟦REF⟧是替换掉的印刷引用符号。按出现顺序对应references，path是内部XML路径，不是字数位置或脚注序号。",
    "逐个对比被替换位置的原始符号与references目标definition.mark。definition.index是内部编号，与印刷mark可以不同。Markdown反斜杠转义和行内数学包装不是印刷符号的一部分。原文的其他语句、公式和普通数字不是引用对应错误。",
    "本块没有⟦REF⟧且references为空不代表出错，不要求所有正文块都引用脚注。仅当本块原文明显有与同页定义mark一致的真实脚注引用，却没有建立对应，才判漏引用。作者年份、书目引用、章末注释、与现有mark不对应的数字不强行匹配，缺失定义不凭空补。",
    "只在有具体字面证据的错目标、错替换位置、漏映射时判错；不能因为当前块末尾未完、邻块出错、定义没有正文引用、同一定义被多次引用或解释文字不匹配主题而否决。",
]


def prompt(instructions):
    return dict(
        type="noul",
        instructions=instructions,
        criteria=dict(
            true="指定的现有结构决定合法或证据不足以要求修正。",
            false="指定决定有明确、可定位、可修复的结构错误。",
        ),
    )


def role_fact(kind, *, from_citation=False):
    scope = "Decide ONLY the source identified by this check, using its complete original text and the supplied three-page context. Current OCR stream and ownership are hypotheses, not evidence of correctness. Do not judge adjacent pages, author accuracy, missing OCR or final heading levels."
    meanings = {
        "footnote_role": (
            "Does the local printed structure establish that this ENTIRE source belongs to a separate ancillary footnote flow supporting another main passage?",
            "Look for an actual local reference/definition or a clear continuation of note prose. A low position, small print, a numbered item or the word Note alone is insufficient. Chapter-end or full-page commentary and classroom instructions belong to the main reading flow. A mixed body/note source must not be removed wholesale.",
        ),
        "main_reading": (
            "Is this source unmistakably part of the main reading sequence, rather than an ancillary footnote?",
            "Section explanations, continuing narrative, instructions and chapter-end commentary are main reading. Geometric headings above this source are supplied independently of the possibly wrong document_order; determine whether this is their main explanatory passage, not just text on the same topic. Follow the actual prose and nearby headings, including across columns. Do not decide from its original footnote stream or current citation label. Real unnumbered footnote continuations are not main reading merely because their prose is grammatical.",
        ),
        "furniture": (
            "Is this entire source unmistakably recurring page furniture, or a standalone continued-page printing direction, rather than reading content?",
            "Require an isolated current-page number, a running book/chapter label or language footer in the page apparatus, or a printing-only continued-page direction. Ordinary section/exercise headings, in-body navigation/link lists, captions, attributions and See-page cross-references are reading content, not running page furniture. A note beginning with a number is not a page number. Merely mentioning a page or section does not satisfy this question.",
        ),
    }
    ask, rule = meanings[kind]
    if kind == "main_reading" and from_citation:
        ask, rule = (
            "Is this WHOLE source retained reading content outside the ancillary footnote stream?",
            "Reading content includes narrative, section explanations, instructions, chapter-end commentary and separately placed figure captions or labels. A full-page image with its own adjacent description does not establish a footnote apparatus. Page numbers, running headers and publishing directions are not reading content. Follow the supplied original text and printed column organization, not the current citation label. In vertical writing, a narrow fragment continuing or repeating a main-text column can still be main reading: cropping, an incomplete sentence or a second OCR pass does not make it a footnote. Require a concrete relationship to the main passage or figure, not merely similar subject matter. Genuine detached footnotes and their unmarked continuations remain footnotes even when they are grammatical explanations, quotations or short text. Read the supplied neighboring pages before reclassifying a long continuation: a detached note can fill a column or page, and a printing-only continued-page direction is not a new section heading. A continuation of an established marked note does not become autonomous main commentary merely because it is long, and it need not repeat its note marker on every block.",
        )
    extra = []
    if kind == "footnote_role":
        extra = [
            "Judge the printed role of the WHOLE source, not whether its sentences explain, correct or comment on something. Parentheses, editorial remarks, quotations, definitions and scholarly citations are also ordinary main reading.",
            "An inline parenthesis stays with its surrounding narrative paragraph. A source mixing main narrative with an author aside is not wholly a footnote.",
            "An autonomous commentary or endnote section is main reading even when it consists of short numbered annotations and continues without repeating a heading. Conversely, a genuine detached note can have multiple paragraphs or fill a page: positive local evidence of its note origin or continuation still establishes that role.",
            "Follow the printed reading sequence across columns. A later-column continuation or long quotation inside an autonomous commentary section remains part of that main section; its words being a quotation do not establish a detached footnote flow.",
            "Within an actually detached note area, the target need not repeat its own marker: a source between a genuine note opening and the next opening can continue that note across natural-paragraph breaks. Grammatical, independent-looking prose can still be a note continuation. First establish the ancillary stream; numbered entries alone do not establish it.",
        ]
    return dict(
        type="noul",
        instructions=[
            scope,
            ask,
            rule,
            *extra,
            "Insufficient or ambiguous evidence means false. Pure punctuation/empty OCR noise is outside this judgement.",
        ],
        criteria=dict(
            true="There is concrete positive evidence for the stated role of this specific source.",
            false="That role is not established; do not infer it from neighbouring sources or a vague suspicion.",
        ),
    )


def bound(packet):
    for name, spec in packet["questions"].items():
        spec["instructions"] = [
            f"本题唯一审核对象是 state.checks.{name}。按该记录的source_order在local_raw_evidence.target_page.layouts定位；若是definition则按该记录的definition定位。其他checks是并行问题，不得用它们的错误否决本题。",
            *spec["instructions"],
        ]
    return packet


def raw_roles(state):
    out = {}
    fields = (
        "document_order",
        "source_order",
        "source_type",
        "text_scale_estimate",
        "bbox_normalized",
        "raw_text",
    )
    for side in ("target_page", "previous_page", "next_page"):
        page = state.get(side)
        if page is None:
            out[side] = None
            continue
        layouts = page.get(
            "layouts",
            page.get("paragraph_boundary", []) + page.get("citation_boundary", []),
        )
        unique = {l["document_order"]: l for l in layouts}
        out[side] = dict(
            page_index=page["page_index"],
            layouts=[{k: l[k] for k in fields} for _, l in sorted(unique.items())],
        )
    return compact(out)


def footnote_review_requests(state):
    page = state["target_page"]
    p = page["page_index"]
    for packet in local_review_requests(state):
        names = [n for n in packet["questions"] if n.startswith("gap_")]
        if names:
            packet["questions"] = {n: packet["questions"][n] for n in names}
            packet["state"]["checks"] = {n: packet["state"]["checks"][n] for n in names}
            packet["state"] = compact(packet["state"])
            packet["state"]["encoding_note"] = _ENCODING
            yield (p, packet)
    evidence = compact(deepcopy(state))
    base = dict(local_raw_evidence=evidence, encoding_note=_ENCODING)
    owners = []
    gaps = []
    refs = []
    has_note_evidence = any(d["index"] is not None for d in page["citations"]) or any(
        l["ownership"] == "citation" and any(c.isalnum() for c in l["raw_text"])
        for l in page["layouts"]
    )
    for l in page["layouts"]:
        if (
            l["source_type"] != "text"
            or not any((c.isalnum() for c in l["raw_text"]))
            or l["source_order"] is None
        ):
            continue
        order = l["source_order"]
        check = dict(
            source_order=order,
            ownership=l["ownership"],
            current_start=l["raw_text"][:240],
            current_end=l["raw_text"][-120:],
            bbox_normalized=l["bbox_normalized"],
        )
        box = l["bbox_normalized"]
        headings = [
            h
            for h in page["layouts"]
            if h["source_type"] == "text"
            and (h.get("paragraph_role") == "heading" or h["raw_text"].startswith("#"))
            and (h["bbox_normalized"][3] <= box[1])
            and (
                min(h["bbox_normalized"][2], box[2])
                > max(h["bbox_normalized"][0], box[0])
            )
        ]
        headings.sort(key=lambda h, current=box: current[1] - h["bbox_normalized"][3])
        check["geometric_headings_above"] = [
            dict(
                source_order=h["source_order"],
                text=h["raw_text"],
                bbox_normalized=h["bbox_normalized"],
                vertical_gap=round(box[1] - h["bbox_normalized"][3], 3),
            )
            for h in headings[:2]
        ]
        for kind in (
            ("footnote_role", "furniture")
            if l["ownership"] == "paragraph"
            else ("main_reading", "furniture")
            if l["ownership"] == "citation"
            else ("main_reading", "footnote_role")
        ):
            owners.append(
                (
                    f"{kind}_{order}",
                    role_fact(kind, from_citation=l["ownership"] == "citation"),
                    check,
                )
            )
        if l["ownership"] == "citation":
            gaps.append(
                (
                    f"note_gap_{order}",
                    prompt(
                        [
                            _COMMON,
                            "审核当前脚注文字块的continues_from_previous。它表示是否与真实脚注前驱属于同一个自然段。正文和脚注是独立流；页码、正文或出版提示不能作脚注前驱。",
                            "同一脚注可包含多个自然段，所以同一citation ID不强迫连接。两个不同的新注释应断开。一个词或未完成句法被前后块切开应连接；独立新段落应断开。独立公式是硬边界，image/table本身不强制断段。",
                        ]
                    ),
                    dict(
                        check,
                        present_decision=l["continues_from_previous"],
                        citation_id=l["citation_id"],
                    ),
                )
            )
        if (
            l["ownership"] == "paragraph"
            and (page["citations"] or l["references"])
            and (has_note_evidence or l["references"])
        ):
            refs.append((f"reference_{order}", prompt(_REF), check))
    for jobs in (owners, gaps, refs):
        current = base
        if jobs is owners:
            jobs = [
                (name, spec, {k: v for k, v in check.items() if k != "ownership"})
                for name, spec, check in jobs
            ]
            current = dict(local_raw_evidence=raw_roles(state), encoding_note=_ENCODING)
        for packet in review_batches(current, jobs):
            packet = bound(packet)
            if jobs is refs:
                yield from _reference_requests(state, packet)
            else:
                yield p, packet
    definitions = [
        (f"definition_{i}", prompt(_DEF), dict(definition=c))
        for i, c in enumerate(page["citations"])
        if c.get("index") is not None
    ]
    for packet in review_batches(base, definitions):
        yield (p, bound(packet))
    starts = []
    seen = set()
    for layout in page["layouts"]:
        cid = layout["citation_id"]
        if (
            layout["ownership"] != "citation"
            or layout["source_type"] != "text"
            or layout["source_order"] is None
            or (not any((c.isalnum() for c in layout["raw_text"])))
        ):
            continue
        first = cid not in seen
        seen.add(cid)
        if first and layout["citation_index"] is not None:
            continue
        order = layout["source_order"]
        name = f"new_note_{order}"
        spec = dict(
            type="noul",
            instructions=[
                f"Inspect only the current source in checks.{name}. Does its ORIGINAL text clearly start a distinct new footnote here, rather than continue an earlier note?",
                "A genuine opening has an actual printed note marker (number, circled character, letter or star) followed by the note explanation. Markdown/LaTeX escaping around a literal marker does not erase that marker. A corresponding body mark may be absent from the OCR; do not require it to recognise an explicitly marked new definition.",
                "Do not trust existing index=null or mark=null as evidence of continuation: these are the algorithm claims being checked. Equally do not invent a missing printed marker or start from the meaning of the prose alone. A footer page number, list item in main commentary, unmarked continuation or pure noise is not a new footnote opening.",
                "A footnote may have multiple natural paragraphs without a new definition. Inspect the actual mark and its role, not merely a sentence beginning or indentation.",
            ],
            criteria=dict(
                true="This source clearly opens a new real footnote with an explicit note marker.",
                false="A distinct new marked footnote start is not established.",
            ),
        )
        starts.append(
            (
                name,
                spec,
                dict(
                    source_order=order,
                    current_start=layout["raw_text"][:240],
                    current_end=layout["raw_text"][-120:],
                    existing_citation_id=cid,
                    existing_index=layout["citation_index"],
                ),
            )
        )
    for packet in review_batches(base, starts):
        yield (p, bound(packet))


def _reference_requests(state, packet):
    """Expose literal edits and audit reference identity/placement separately."""
    page = state["target_page"]
    layouts = {l["source_order"]: l for l in page["layouts"]}
    jobs = []
    for name, spec in packet["questions"].items():
        check = packet["state"]["checks"][name]
        source = layouts[check["source_order"]]
        edits = (
            literal_reference_edits(source["raw_text"], source["analysed_text"])
            if source["references"]
            else []
        )
        if edits is not None:
            check["literal_edits"] = edits
        check["references"] = deepcopy(source["references"])
        check["target_definitions"] = [
            dict(
                citation_page=page["page_index"],
                citation_index=d["index"],
                mark=d["mark"],
            )
            for d in page["citations"]
            if d["index"] is not None
        ]
        spec["instructions"] += [
            "literal_edits列出原文到分析文字的字面差异：original是删除文字，analysed是插入文字，before/after保留原位置上下文。references按⟦REF⟧出现顺序列出其目标；target_definitions给出同页定义的印刷mark。此清单只是待审核的原始事实，不是正确性结论。只审核本块引用的身份与原位置是否保留，空白、转义或数学包装不影响同一个印刷标记。"
        ]
        jobs.append((name, spec, check))
        if source["references"] and edits is not None:
            location = f"reference_position_{name.rsplit('_', 1)[1]}"
            jobs.append((location, question("reference_position", location), check))
    base = {k: v for k, v in packet["state"].items() if k != "checks"}
    for current in review_batches(base, jobs):
        yield page["page_index"], current


def supplementary_footnote_requests(state, scores):
    page = state["target_page"]
    p = page["page_index"]
    jobs = []
    headings = []
    for side in ("previous_page", "target_page"):
        value = state.get(side)
        if value is None:
            continue
        layouts = value.get(
            "layouts",
            value.get("paragraph_boundary", []) + value.get("citation_boundary", []),
        )
        for l in layouts:
            if l["source_type"] == "text" and (
                l.get("paragraph_role") == "heading" or l["raw_text"].startswith("#")
            ):
                headings.append(
                    dict(
                        page_index=value["page_index"],
                        source_order=l["source_order"],
                        raw_text=l["raw_text"],
                        bbox_normalized=l["bbox_normalized"],
                    )
                )
    for layout in page["layouts"]:
        order = layout["source_order"]
        if (
            order is None
            or scores.get(f"footnote_role_{order}", 0) < FOOTNOTE_REVIEW_THRESHOLD
            or (not headings)
        ):
            continue
        for kind in ("section_sequence", "section_member"):
            name = f"{kind}_{order}"
            spec = question(kind, name)
            if kind == "section_sequence":
                spec = question(kind, name)
                spec["instructions"] += [
                    "The matching section heading may be on the PREVIOUS supplied page. A current page continuing that established section does not need to repeat its heading or start a second section. Judge whether the heading established a main reading section, not whether that section starts on the current page.",
                    "An independent bibliography/reference section is also main reading. This applies to vertical Chinese annotations as well as horizontal text: follow the actual column order, not an assumed top-to-bottom prose-plus-footer layout.",
                ]
            jobs.append(
                (
                    name,
                    spec,
                    dict(
                        source_order=order,
                        ownership=layout["ownership"],
                        preceding_or_current_heading_candidates=headings,
                    ),
                )
            )
    for packet in review_batches(
        dict(local_raw_evidence=raw_roles(state), encoding_note=_ENCODING), jobs
    ):
        yield (p, packet)
    checks = {
        f"owner_{l['source_order']}": dict(
            source_order=l["source_order"], ownership="paragraph"
        )
        for l in page["layouts"]
        if l["source_type"] == "text"
        and l["ownership"] == "paragraph"
        and (l["source_order"] is not None)
    }
    original = dict(local_raw_evidence=state, checks=checks)
    for packet in review_batches(
        dict(local_raw_evidence=compact(state), encoding_note=_ENCODING),
        list(_duplicates(original, {})),
    ):
        yield (p, packet)
    for packet in local_review_requests(state):
        if not any((name.startswith("gap_") for name in packet["questions"])):
            continue
        for family in (_selected_strips, _image_starts):
            for value in review_batches(
                packet["state"], list(family(packet["state"], {}))
            ):
                value["state"] = compact(value["state"])
                value["state"]["encoding_note"] = _ENCODING
                yield (p, value)

    yield from _main_section_requests(state)
    yield from _retained_reading_requests(state, scores)
    yield from quotation_requests(state, scores, FOOTNOTE_REVIEW_THRESHOLD)
    yield from completion_requests(state, scores, FOOTNOTE_REVIEW_THRESHOLD)


def _retained_reading_requests(state, scores):
    """Verify main-reading placement without changing other role decisions."""
    jobs = []
    for layout in state["target_page"]["layouts"]:
        order = layout["source_order"]
        if (
            layout["source_type"] != "text"
            or layout["ownership"] != "paragraph"
            or order is None
            or scores.get(f"furniture_{order}", 0) < FOOTNOTE_REVIEW_THRESHOLD
        ):
            continue
        name = f"retained_reading_position_{order}"
        jobs.append(
            (
                name,
                question("retained_reading_position", name),
                dict(source_order=order),
            )
        )
    for packet in review_batches(
        dict(local_raw_evidence=raw_roles(state), encoding_note=_ENCODING), jobs
    ):
        yield state["target_page"]["page_index"], packet


def _main_section_requests(state):
    """Check raw heading candidates for unindexed sources in the note stream.

    Candidate selection establishes no heading or reading-role verdict. The
    section start and whole-source membership must both be established by JEV.
    """
    page = state["target_page"]
    headings = []
    for side in ("previous_page", "target_page"):
        part = state.get(side)
        if not part:
            continue
        layouts = part.get(
            "layouts",
            part.get("paragraph_boundary", []) + part.get("citation_boundary", []),
        )
        for item in layouts:
            text = item["raw_text"].strip()
            if (
                item["source_type"] == "text"
                and item["source_order"] is not None
                and 1 <= len(text) <= 64
                and any(c.isalpha() for c in text)
            ):
                headings.append(
                    dict(
                        page_index=part["page_index"],
                        source_order=item["source_order"],
                        raw_text=text,
                        bbox_normalized=item["bbox_normalized"],
                        document_order=item["document_order"],
                    )
                )
    jobs = []
    for item in page["layouts"]:
        if (
            item["source_type"] != "text"
            or item["ownership"] != "citation"
            or item["source_order"] is None
            or not str(item.get("citation_id", "")).startswith("unresolved-")
            or not any(c.isalpha() for c in item["raw_text"])
        ):
            continue
        if extract_head_mark(_parse_block_content(item["raw_text"]))[0] is not None:
            continue
        candidates = [
            h for h in headings if h["document_order"] < item["document_order"]
        ]
        candidates = sorted(
            candidates, key=lambda x: x["document_order"], reverse=True
        )[:3]
        if not candidates:
            continue
        for kind in ("section_sequence", "section_member"):
            name = f"main_{kind}_{item['source_order']}"
            spec = question(kind, name)
            spec["instructions"] += [
                "preceding_or_current_heading_candidates are unclassified short RAW text candidates, not established headings. Confirm their actual heading function independently; an OCR text tag or absent heading tag is not proof either way. A short vertical section label can identify a main commentary section without a Markdown heading marker. Do not treat an ordinary sentence, page number or inline note label as a section heading."
            ]
            jobs.append(
                (
                    name,
                    spec,
                    dict(
                        source_order=item["source_order"],
                        preceding_or_current_heading_candidates=candidates,
                    ),
                )
            )
    for packet in review_batches(
        dict(local_raw_evidence=raw_roles(state), encoding_note=_ENCODING), jobs
    ):
        yield page["page_index"], packet


def footnote_pass_scores(scores):
    completion_proofs = {k: v for k, v in scores.items() if k.startswith(tuple(COMPLETION_TARGETS))}
    scores = {k: v for k, v in scores.items() if k not in completion_proofs}
    quotation_proofs = {k: v for k, v in scores.items() if k.startswith("quotation_boundary_")}
    scores = {k: v for k, v in scores.items() if k not in quotation_proofs}
    reading_proofs = {
        k: v for k, v in scores.items() if k.startswith("retained_reading_position_")
    }
    scores = {k: v for k, v in scores.items() if k not in reading_proofs}
    main_proofs = {
        k: v
        for k, v in scores.items()
        if k.startswith(("main_section_sequence_", "main_section_member_"))
    }
    scores = {k: v for k, v in scores.items() if k not in main_proofs}
    proof_prefixes = (
        "section_sequence_",
        "section_member_",
        "detached_image_strip_",
        "prior_narrative_role_",
        "selected_whole_passage_",
        "image_caption_role_",
        "caption_starts_here_",
    )
    values = {
        key: 1 - value
        if key.startswith(
            ("furniture_", "main_reading_", "footnote_role_", "new_note_")
        )
        else value
        for key, value in scores.items()
        if not key.startswith(proof_prefixes)
    }
    for name, score in scores.items():
        if not name.startswith("section_sequence_"):
            continue
        order = name.rsplit("_", 1)[1]
        proof = min(score, scores[f"section_member_{order}"])
        if proof >= FOOTNOTE_REVIEW_THRESHOLD and f"footnote_role_{order}" in values:
            values[f"footnote_role_{order}"] = max(
                values[f"footnote_role_{order}"], proof
            )
    facts = {k: v for k, v in scores.items() if k.startswith(proof_prefixes[2:])}
    for name, risk in aggregate_risks({}, facts).items():
        values[name] = 1 - risk
    for name, score in main_proofs.items():
        if name.startswith("main_section_sequence_"):
            order = name.rsplit("_", 1)[1]
            proof = min(score, main_proofs[f"main_section_member_{order}"])
            key = f"main_reading_{order}"
            if key in values:
                values[key] = min(values[key], 1 - proof)
    for name, proof in reading_proofs.items():
        key = f"furniture_{name.rsplit('_', 1)[1]}"
        if key in values:
            values[key] = max(values[key], proof)
    apply_quotation_proofs(values, quotation_proofs, FOOTNOTE_REVIEW_THRESHOLD)
    apply_completion_proofs(values, completion_proofs, FOOTNOTE_REVIEW_THRESHOLD)
    return values


async def review_footnote_page(state, evaluate):
    """Return a complete pass score, never a partial or fabricated verdict.

    The neighborhood is fixed before review. Source-specific supplementary
    questions may refine that source's evidence but create no other-page wait.
    """
    scores = await _collect(footnote_review_requests(state), evaluate)
    if not scores:
        return None
    facts = await _collect(supplementary_footnote_requests(state, scores), evaluate)
    if facts is None:
        return None
    return min(footnote_pass_scores(dict(scores, **facts)).values())
