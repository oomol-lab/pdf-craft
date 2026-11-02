"""Local body-only review: one editable decision per named question."""

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from .review_questions import question
from .review_templates import review_question

LOCAL_REVIEW_THRESHOLD = 0.52
_QUESTIONS_PER_REQUEST = 4

def local_review_requests(state: dict[str, Any], *, concrete_joins: bool = False) -> Iterator[dict[str, Any]]:
    """Yield bounded questions; body-only review binds concrete incoming sources.

    Footnote review keeps its separate ownership and edge-fact protocol.
    """
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
                    prompt = review_question('shared/incoming_join', key=key)
                    if concrete_joins:
                        check['selected_predecessor'] = next((index[key] for key in reversed(available)
                            if index[key]['ownership_hint'] == 'paragraph'), None)
                        prompt = review_question('body/concrete_join', key=key)
                    jobs[key] = [(key, prompt, check)]
                else:
                    jobs[key] = []
                    for part in ("word", "syntax"):
                        name = f"{key}_{part}"
                        jobs[key].append((name, question(f"gap_{part}", name), check))
            key = f"owner_{order}"
            owner = layout["ownership"]
            check = dict(source_order=order, ownership=owner, current_start=layout["raw_text"][:240])
            prompt = review_question('body/ownership', order=order, key=key, retained=owner == 'paragraph')
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
