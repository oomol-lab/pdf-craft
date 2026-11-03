"""Preserve source-specific decisions while packing each review round once."""

from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
import json
from typing import Any

from ...jev import jev_page_request_budget
from .local_review import local_review_requests
from .structural_review import _decisions, aggregate_risks
from .body_review_facts import body_supplementary_requests, apply_body_facts
from .footnote_review import (
    footnote_review_requests,
    supplementary_footnote_requests,
    footnote_pass_scores,
    raw_roles,
)
from .review_templates import review_question, review_text
from .predecessor_review import predecessor_requests, predecessor_hypotheses
from .packed_facts import reference_check_applies, apply_new_note_boundaries

Evaluator = Callable[[int, dict[str, Any]], Awaitable[dict[str, float]]]


def _confirmed_role_pass_score(original: float, confirmation: float) -> float:
    """Relax a role fault conservatively when its recheck disagrees.

    This is a routing score, not an independence/calibration claim. A generic
    acceptance must not wholly erase a stronger source-specific rejection.
    Keep the result within the original score and the old max-proof bound.
    """
    if confirmation < 0.8:
        return original
    support = original * confirmation
    total = support + (1 - original) * (1 - confirmation)
    consensus = support / total if total else original
    return max(original, min(confirmation, consensus))


def pack_review_requests(
    requests: Iterable[tuple[int, dict[str, Any]]], *, evidence: dict[str, Any]
) -> dict[str, Any] | None:
    """Share one complete evidence view and verbatim reusable policy paragraphs."""
    from .review_encoding import compact_review_evidence, quote_encoded_literals

    requests = list(requests)
    shared = compact_review_evidence(evidence)
    value = {
        "local_raw_evidence": shared,
        "checks": {},
        "encoding_note": review_text("packed_text_encoding"),
    }
    instructions = {}
    instruction_ids = {}
    specifications = {}
    page = None
    for index, packet in requests:
        if page is not None and index != page:
            raise ValueError("One packed review must target one page")
        page = index
        if "preceding_source_index" in packet["state"]:
            value["preceding_source_index"] = deepcopy(
                packet["state"]["preceding_source_index"]
            )
        for name, spec in packet["questions"].items():
            if name in specifications:
                raise ValueError("Duplicate packed question identity: " + name)
            value["checks"][name] = deepcopy(packet["state"]["checks"][name])
            ids = []
            for paragraph in spec["instructions"][1:]:
                if paragraph not in instruction_ids:
                    key = f"p{len(instructions)}"
                    instruction_ids[paragraph] = key
                    instructions[key] = paragraph
                ids.append(instruction_ids[paragraph])
            specifications[name] = spec
    if not specifications:
        return None
    value["instruction_texts"] = instructions
    dense = len(specifications) > 64
    if dense:
        shared_checks = {}
        seen = {}

        def compact(item):
            if isinstance(item, dict):
                key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if len(key) > 80:
                    if key in seen:
                        return {"shared_check": seen[key]}
                    identifier = f"c{len(seen)}"
                    seen[key] = identifier
                    shared_checks[identifier] = {k: compact(v) for k, v in item.items()}
                    return {"shared_check": identifier}
                return {k: compact(v) for k, v in item.items()}
            if isinstance(item, list):
                return [compact(v) for v in item]
            return item

        value["checks"] = {
            name: compact(check) for name, check in value["checks"].items()
        }
        value["shared_checks"] = shared_checks
    value["transport_contract"] = review_text("packed_contract")
    questions = {}
    for name, spec in specifications.items():
        ids = [instruction_ids[p] for p in spec["instructions"][1:]]
        questions[name] = review_question(
            "packed_question",
            name=name,
            shared=True,
            lead=spec["instructions"][0],
            instruction_ids=ids,
            true_criterion=spec["criteria"]["true"],
            false_criterion=spec["criteria"]["false"],
        )
    return {"state": quote_encoded_literals(value), "questions": questions}


def _localize_checks(packet):
    """Keep a large per-question check out of every question's shared prefix.

    Every check is expanded exactly, then moved beside its own question. Full
    raw page evidence and common rules stay shared. This is transport placement,
    not a change to the available sources or an additional provider request.
    """
    state = packet['state']
    if len(json.dumps(state, ensure_ascii=False)) <= 60_000:
        return packet
    shared = state.get('shared_checks', {})
    def expand(value):
        if isinstance(value, dict):
            if set(value) == {'shared_check'}:
                return expand(shared[value['shared_check']])
            return {key: expand(child) for key, child in value.items()}
        if isinstance(value, list):
            return [expand(child) for child in value]
        return value
    questions = {}
    for name, spec in packet['questions'].items():
        check = expand(state['checks'][name])
        context = review_text('packed_check_context', name=name,
            check_json=json.dumps(check, ensure_ascii=False, separators=(',', ':')))
        questions[name] = dict(spec, instructions=[*spec['instructions'], context])
    state = {key: value for key, value in state.items() if key not in ('checks', 'shared_checks')}
    return dict(packet, state=state, questions=questions)


async def _evaluate(
    requests, evaluate, *, evidence, fault_names=frozenset(), hypotheses=None,
    native_fault_names=frozenset(),
):
    """Pack once, restoring native or wrapped fault scores to pass scores."""
    requests = list(requests)
    if fault_names:
        requests = [
            (
                index,
                dict(
                    packet,
                    questions={
                        name: review_question(
                            "fault_orientation",
                            original_instructions=spec["instructions"],
                            fault_criterion=spec["criteria"]["false"],
                            acceptable_criterion=spec["criteria"]["true"],
                            incoming=name.startswith(("gap_", "note_gap_")),
                            word=name.endswith("_word"),
                            syntax=name.endswith("_syntax"),
                            note=name.startswith("note_gap_"),
                        )
                        if name in fault_names
                        else spec
                        for name, spec in packet["questions"].items()
                    },
                ),
            )
            for index, packet in requests
        ]
    packet = pack_review_requests(requests, evidence=evidence)
    if packet is None:
        return {}
    if hypotheses:
        packet["state"]["incoming_predecessor_hypotheses"] = hypotheses
    packet = _localize_checks(packet)
    page = requests[0][0]
    # Provider answer keys need no business semantics. Keep targets and policies
    # intact, then restore their descriptive identities before aggregation.
    aliases = {f"q{i}": name for i, name in enumerate(packet["questions"])}
    packet = dict(
        packet,
        questions={alias: packet["questions"][name] for alias, name in aliases.items()},
    )
    scores = await evaluate(page, packet)
    if set(scores) != set(packet["questions"]) or any(
        not 0 <= v <= 1 for v in scores.values()
    ):
        raise ValueError("Incomplete or invalid packed JEV scores")
    return {
        name: 1 - scores[alias] if name in fault_names or name in native_fault_names else scores[alias]
        for alias, name in aliases.items()
    }


def _body_followups(state, checks, risks):
    for batch in _decisions(checks):
        for packet in body_supplementary_requests(dict(state, checks=batch), risks):
            # These generic ownership exceptions are already stated in the base
            # ownership rubric. Do not speculate about them for every source
            # before a one-call review knows whether ownership is even suspect.
            names = [
                name
                for name in packet["questions"]
                if not name.startswith(
                    ("attribution_", "section_sequence_", "section_member_")
                )
            ]
            if names:
                yield dict(
                    packet,
                    questions={name: packet["questions"][name] for name in names},
                    state=dict(
                        packet["state"],
                        checks={
                            name: packet["state"]["checks"][name] for name in names
                        },
                    ),
                )


def _role_evidence(state):
    """Preserve original adjacent assets in the detached raw-role view."""
    sources = dict(state)
    for side in ("previous_page", "next_page"):
        page = state.get(side)
        if page:
            sources[side] = dict(page, asset_text_sources=[
                *page.get("assets", ()), *page.get("asset_text_sources", ())])
    evidence = raw_roles(sources)
    if "coordinate_note" in state:
        evidence["coordinate_note"] = state["coordinate_note"]
    return evidence


async def _packed_review(
    state: dict[str, Any], evaluate: Evaluator, *, includes_footnotes: bool
) -> dict[str, Any]:
    """One complete body call, or at most two complete note-enabled rounds."""
    page = state["target_page"]["page_index"]
    calls = 0

    async def limited(index, packet):
        nonlocal calls
        if calls >= (2 if includes_footnotes else 1):
            raise RuntimeError("JEV page request budget exceeded")
        calls += 1
        return await evaluate(index, packet)

    if includes_footnotes:
        base = []
        for index, packet in footnote_review_requests(state):
            names = [name for name in packet['questions']
                     if not (name.startswith('reference_') and not name.startswith('reference_position_'))
                     or reference_check_applies(state, packet['state']['checks'][name])]
            if names:
                base.append((index, dict(packet, questions={name: packet['questions'][name] for name in names},
                    state=dict(packet['state'], checks={name: packet['state']['checks'][name] for name in names}))))
        role_prefixes = ("footnote_role_", "main_reading_", "furniture_")
        roles = []
        others = []
        for index, packet in base:
            for wanted, target in ((True, roles), (False, others)):
                names = [
                    name
                    for name in packet["questions"]
                    if name.startswith(role_prefixes) == wanted
                ]
                if names:
                    target.append(
                        (
                            index,
                            dict(
                                packet,
                                questions={
                                    name: packet["questions"][name] for name in names
                                },
                                state=dict(
                                    packet["state"],
                                    checks={
                                        name: packet["state"]["checks"][name]
                                        for name in names
                                    },
                                ),
                            ),
                        )
                    )
        predecessors = predecessor_requests(others)
        role_evidence = _role_evidence(state)
        first = await _evaluate(
            roles + predecessors, limited, evidence=role_evidence
        )
        hypotheses = predecessor_hypotheses(predecessors, first)
        scores = {
            name: value
            for name, value in first.items()
            if not name.startswith("predecessor_candidate_")
        }
        unknown = list(
            dict.fromkeys(name for _, packet in others for name in packet["questions"])
        )
        potential = {}
        for value in (0.0, 1.0):
            trial = dict(scores, **{name: value for name in unknown})
            for index, packet in supplementary_footnote_requests(state, trial):
                for name in packet["questions"]:
                    potential.setdefault(
                        name,
                        (
                            index,
                            dict(
                                packet,
                                questions={name: packet["questions"][name]},
                                state=dict(
                                    packet["state"],
                                    checks={name: packet["state"]["checks"][name]},
                                ),
                            ),
                        ),
                    )
        confirmations = []
        for layout in state["target_page"]["layouts"]:
            order = layout["source_order"]
            if order is None:
                continue
            suspects = {
                name: value
                for name, value in scores.items()
                if name.startswith(role_prefixes)
                and name.rsplit("_", 1)[1] == str(order)
                and value >= 0.25
            }
            if not suspects:
                continue
            name = f"confirmed_role_{order}"
            spec = review_question("role_confirmation", name=name)
            check = {"source_order": order, "proposed_ownership": layout["ownership"]}
            confirmations.append(
                (page, {"state": {"checks": {name: check}}, "questions": {name: spec}})
            )
        second = others + list(potential.values()) + confirmations
        fault_names = frozenset(
            name for name in unknown if not name.startswith("new_note_")
        )
        later = await _evaluate(
            second,
            limited,
            evidence=state,
            fault_names=fault_names,
            hypotheses=hypotheses,
        )
        scores.update({name: later[name] for name in unknown})
        selected = list(
            dict.fromkeys(
                name
                for _, packet in supplementary_footnote_requests(state, scores)
                for name in packet["questions"]
            )
        )
        if not set(selected) <= later.keys():
            raise ValueError("A note fact was not admitted by the two-call plan")
        facts = {name: later[name] for name in selected}
        all_scores = dict(scores, **facts)
        verdicts = footnote_pass_scores(all_scores)
        for _, packet in confirmations:
            for name in packet["questions"]:
                order = name.rsplit("_", 1)[1]
                for original in verdicts:
                    if (
                        original.startswith(role_prefixes)
                        and original.rsplit("_", 1)[1] == order
                    ):
                        verdicts[original] = _confirmed_role_pass_score(
                            verdicts[original], later[name]
                        )
        verdicts = apply_new_note_boundaries(state, verdicts)
        probability = None if not verdicts else min(verdicts.values())
        return {"pass_probability": probability, "scores": all_scores, "calls": calls}
    base = list(local_review_requests(state, concrete_joins=True, native_faults=True))
    if not base:
        return {"pass_probability": None, "scores": {}, "calls": 0}
    shared = base[0]["state"]
    checks = {
        name: value
        for packet in base
        for name, value in packet["state"]["checks"].items()
    }
    all_risks = {name: 1.0 for packet in base for name in packet["questions"]}
    potential = list(_body_followups(shared, checks, all_risks))
    packets = [(page, packet) for packet in base + potential]
    scores = await _evaluate(packets, limited, evidence=state,
        native_fault_names=frozenset(name for name in all_risks
            if name.startswith('owner_') or name.endswith(('_word', '_syntax'))))
    risks = {name: 1 - scores[name] for packet in base for name in packet["questions"]}
    # All admitted facts were already evaluated in this one request. The legacy
    # follow-up trigger chooses which requests to pay for; it must not discard
    # positive evidence that has already been returned for its exact source.
    facts = {name: scores[name] for packet in potential for name in packet["questions"]}
    resolved = apply_body_facts(aggregate_risks(risks, facts), facts)
    probability = 1 - max(resolved.values())
    return {"pass_probability": probability, "scores": scores, "calls": calls}


async def packed_review(
    state: dict[str, Any], evaluate: Evaluator, *, includes_footnotes: bool
) -> dict[str, Any]:
    """Enforce the physical page allowance through the official JEV runtime."""
    with jev_page_request_budget(2 if includes_footnotes else 1):
        return await _packed_review(
            state, evaluate, includes_footnotes=includes_footnotes
        )
