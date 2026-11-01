"""Complete named-score review and source-specific risk aggregation for one page."""
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from .local_review import LOCAL_REVIEW_THRESHOLD
from .review_evidence import supplementary_requests

NamedScores = dict[str, float]
NamedBatchEvaluator = Callable[
    [Iterable[tuple[int, dict[str, Any]]]], Awaitable[list[tuple[int, NamedScores]]],
]


def _decisions(checks):
    """Recover the original four-decision batches without copying raw DOMs."""
    logical = {}
    for name, check in checks.items():
        key = name.rsplit('_', 1)[0] if name.endswith(('_word', '_syntax')) else name
        logical[key] = check
    for family in ('gap_', 'owner_'):
        keys = [key for key in logical if key.startswith(family)]
        for start in range(0, len(keys), 4):
            yield {key: logical[key] for key in keys[start:start + 4]}


async def _collect(requests, evaluate):
    expected = []
    def generated():
        for index, packet in requests:
            expected.append((index, frozenset(packet['questions'])))
            yield index, packet
    results = await evaluate(generated())
    if len(results) != len(expected):
        return None
    scores = {}
    for (index, values), (target, names) in zip(results, expected):
        if index != target or set(values) != names or any(name in scores for name in values):
            raise ValueError('JEV named scores do not match the requested decisions')
        if not values or any(not 0 <= value <= 1 for value in values.values()):
            raise ValueError('JEV named scores must be between 0 and 1')
        scores.update(values)
    return scores


async def review_body_page(
    requests: Iterable[tuple[int, dict[str, Any]]], evaluate: NamedBatchEvaluator,
) -> float | None:
    """Return a complete pass score, or no verdict after an incomplete review.

    Only this page's base scores control follow-up admission. Other pages keep
    progressing through the shared executor while these local facts are checked.
    """
    state: dict[str, Any] = {}
    checks = {}
    target = None
    def base_requests():
        nonlocal target
        for index, packet in requests:
            if target is not None and index != target:
                raise ValueError('A local structural review must target exactly one page')
            target = index
            if not state:
                state.update(packet['state'])
            checks.update(packet['state']['checks'])
            yield index, packet
    base = await _collect(base_requests(), evaluate)
    if base is None or not base:
        return None
    assert target is not None
    page_index = target
    risks = {name: 1 - value for name, value in base.items()}
    def followups():
        for batch in _decisions(checks):
            local = dict(state, checks=batch)
            for packet in supplementary_requests(local, risks):
                yield page_index, packet
    facts = await _collect(followups(), evaluate)
    if facts is None:
        return None
    return 1 - max(aggregate_risks(risks, facts).values())


def aggregate_risks(base: NamedScores, facts: NamedScores) -> NamedScores:
    """A proof can reduce only its own original fault, never unrelated risks."""
    risks = dict(base)
    for name, value in facts.items():
        order = name.rsplit('_', 1)[1]
        if name.startswith('redundancy_'):
            risks[name] = 1 - value
        elif name.startswith('detached_image_strip_'):
            risks['selected_image_strip_' + order] = min(value,
                facts['prior_narrative_role_' + order], facts['selected_whole_passage_' + order])
        elif name.startswith('image_caption_role_'):
            risks['caption_start_' + order] = min(value, facts['caption_starts_here_' + order])
        elif name.startswith('fresh_spoken_turn_'):
            risks['distinct_dialogue_turn_' + order] = min(value, facts['different_dialogue_speaker_' + order])
        else:
            proof = value
            if name.startswith('section_sequence_'):
                proof = min(value, facts['section_member_' + order])
                targets = ['owner_' + order]
            elif name.startswith('attribution_'):
                targets = ['owner_' + order]
            elif name.startswith('left_word_complete_'):
                targets = [f'gap_{order}_word']
            elif name.startswith('left_frame_complete_'):
                targets = [f'gap_{order}_syntax']
            elif name.startswith('quotation_boundary_'):
                targets = [f'gap_{order}_word', f'gap_{order}_syntax']
            elif name.startswith('joined_word_complete_'):
                targets = ['gap_' + order]
            else:
                continue
            if proof >= LOCAL_REVIEW_THRESHOLD:
                for key in targets:
                    if risks.get(key, 0) >= LOCAL_REVIEW_THRESHOLD:
                        risks[key] = min(risks[key], 1 - proof)
    return risks
