"""Local footnote review using bounded questions and immutable OCR evidence."""

from copy import deepcopy
from .footnote_quotation import quotation_requests, apply_quotation_proofs
from .footnote_completion import (
    COMPLETION_TARGETS, PAIRED_WORD_PREFIXES, DEPENDENCY_PREFIXES, completion_requests,
    apply_completion_proofs, apply_paired_word_proofs, apply_dependency_proofs,
)
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
from .review_templates import review_question, review_instructions, review_text

FOOTNOTE_REVIEW_THRESHOLD = 0.45

def role_fact(kind, *, from_citation=False):
    path = 'caption_reading' if kind == 'main_reading' and from_citation else kind
    return review_question('footnote/' + path)


def bound(packet):
    for name, spec in packet["questions"].items():
        spec["instructions"] = [review_text('footnote/check_scope', name=name), *spec['instructions']]
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
            layouts=[dict({k: l[k] for k in fields}, **(
                {'number_ink_bbox_normalized': l['number_ink_bbox_normalized']}
                if 'number_ink_bbox_normalized' in l else {})) for _, l in sorted(unique.items())],
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
                    review_question('footnote/note_gap'),
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
            refs.append((f"reference_{order}", review_question('footnote/reference'), check))
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
        (f"definition_{i}", review_question('footnote/definition'), dict(definition=c))
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
        spec = review_question('footnote/new_note', name=name)
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
        spec["instructions"] += review_instructions('footnote/reference_location')
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
                spec["instructions"] += review_instructions('footnote/unmarked_note')
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
            spec["instructions"] += review_instructions('footnote/main_section')
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
    dependencies = {k: v for k, v in scores.items() if k.startswith(DEPENDENCY_PREFIXES)}
    scores = {k: v for k, v in scores.items() if k not in dependencies}
    word_proofs = {k: v for k, v in scores.items() if k.startswith(PAIRED_WORD_PREFIXES)}
    scores = {k: v for k, v in scores.items() if k not in word_proofs}
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
    apply_paired_word_proofs(values, word_proofs, FOOTNOTE_REVIEW_THRESHOLD)
    apply_dependency_proofs(values, dependencies, FOOTNOTE_REVIEW_THRESHOLD)
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
