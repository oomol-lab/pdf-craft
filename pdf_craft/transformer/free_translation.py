# pylint: disable=unused-argument
"""Engineering allocation of an immutable, free-form group translation."""
import json
from collections import Counter

from ..llm import Message, MessageRole
from ..llm.loop import ProtocolRetry, ProtocolSuccess, RepairLoopOptions, run_repair_loop
from .._policy import current
from ..policies import RetryScope
from .allocation_text import allocation_difference, recover_allocation_text, translated_math_targets, translated_math_evidence
from .allocation_math import allocate_math
from .allocation_boundaries import expand_boundaries
from .xml_translator.xml.friendly import encode_friendly


class AllocationFillError(ValueError):
    """The engineer could not allocate the immutable artist response."""

    attempts: int = 1


def _covers(texts, raw):
    """Only allocation boundaries may consume paragraph whitespace."""
    cursor = 0
    for text in texts:
        text = text.strip(' \t\r\n')
        while cursor < len(raw) and raw[cursor] in ' \t\r\n':
            cursor += 1
        if not raw.startswith(text, cursor):
            return False
        cursor += len(text)
    return not raw[cursor:].strip(' \t\r\n')


def allocate(response, calls, raw):
    """Check engineering allocation, never accept/reject the artist's text."""
    response = response.strip()
    if response.startswith('```json\n') and response.endswith('\n```'):
        response = response[8:-4]
    elif response.startswith('```\n') and response.endswith('\n```'):
        response = response[4:-4]
    def unique_object(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError('Engineering JSON objects must not contain duplicate fields.')
        return result
    units = json.loads(response, object_pairs_hook=unique_object)
    if not isinstance(units, list):
        raise ValueError(f'Expected a JSON array containing {len(calls)} source-unit allocations; '
                         f'received {type(units).__name__}.')
    if any(not isinstance(unit, dict) or not isinstance(unit.get('id'), str) for unit in units):
        raise ValueError('Each allocation must be an object with a string source id.')
    expected = {str(index) for index in range(len(calls))}
    counts = Counter(unit['id'] for unit in units)
    if len(units) != len(calls) or set(counts) != expected:
        raise ValueError('Allocation identity mismatch: ' + json.dumps(dict(
            expected_count=len(calls), received_count=len(units),
            missing_ids=sorted(expected-counts.keys(), key=int),
            duplicate_ids=sorted(key for key, count in counts.items() if count > 1),
            unexpected_ids=sorted(counts.keys()-expected))))
    units = expand_boundaries(units, raw)
    by_id = {unit['id']: unit for unit in units}
    if any(not isinstance(unit.get('text'), str) for unit in units):
        raise ValueError('Each allocated text must be a string.')
    if not _covers([unit['text'] for unit in units], raw):
        recovered = recover_allocation_text(units, calls, raw)
        if recovered is None:
            raise ValueError(allocation_difference(units, raw))
        units = [dict(unit, text=text) for unit, text in zip(units, recovered)]
        by_id = {unit['id']: unit for unit in units}
    targets = translated_math_targets(units, raw)
    units = [by_id[str(index)] for index in range(len(calls))]
    answers, bindings = {}, []
    issues = []
    for index, (call, unit) in enumerate(zip(calls, units)):
        try:
            canonical, bound = allocate_math(unit, call.source_math, targets)
        except (ValueError, TypeError, KeyError) as error:
            issues.append(f"Unit {index}: {error}")
        else:
            answers[str(index)] = canonical
            bindings.append((call.source_math, bound))
    if issues:
        raise ValueError('\n'.join(issues)[:12000])
    # Commit identities only after the complete engineering allocation passes.
    for math, bound in bindings:
        if math is not None:
            math.bindings = bound
            math.allocated = True
    return answers


async def fill_translation_group(runtime, calls, raw):
    """The raw artist response first enters fill here and stays fixed on repair."""
    if len(calls) == 1 and not getattr(calls[0].source_math, 'values', {}):
        # Context-only display formulas do not own inline source identities.
        # There is exactly one owner for the immutable answer, including an
        # empty one. Account for additional artist math without asking a model
        # to invent an allocation or translating readonly context again.
        return allocate(json.dumps([dict(id='0', text=raw, formulas=[])]), calls, raw)
    # A unique identity allocation is an engineering operation. This does not
    # assess translation quality or change/retry the artist response.
    if all(call.source_math is None and getattr(call, 'source_text', None) is not None for call in calls):
        if _covers([call.source_text for call in calls], raw):
            return {str(index): call.source_text for index, call in enumerate(calls)}
    sources = [dict(id=str(index), source=call.messages[-1].message,
                    formula_context=call.source_math.evidence if call.source_math is not None else {},
                    formulas={key: value.latex for key, value in call.source_math.values.items()}
                    if call.source_math is not None else {},
                    formula_nodes={key: encode_friendly(value.element, preserve_whitespace=True)
                                   for key, value in call.source_math.values.items()}
                    if call.source_math is not None else {}) for index, call in enumerate(calls)]
    messages = [Message(MessageRole.SYSTEM, (await runtime.template('allocate')).render()),
                Message(MessageRole.USER, json.dumps(dict(sources=sources, translation=raw, translated_math=translated_math_evidence(raw)), ensure_ascii=False))]

    class AllocationProtocol:
        async def validate(self, response, state, attempt, max_attempts):
            try:
                return ProtocolSuccess(allocate(response, calls, raw), state)
            except (ValueError, TypeError, KeyError) as error:
                return ProtocolRetry(str(error), state)

        async def empty(self, state, attempt, max_attempts):
            return ProtocolRetry('Return the complete compact allocation JSON array using exact end anchors, not repeated full passages.', state)

        async def exhausted(self, state, attempts, response):
            error = AllocationFillError('Translation allocation fill exhausted its repair budget.')
            error.attempts = attempts
            raise error

    try:
        async with runtime.context() as context:
            return await run_repair_loop(RepairLoopOptions(
                messages=messages, request=lambda messages, index, maximum: context.request(
                    messages, retry_index=index, retry_max=maximum, use_cache=False),
                protocol=AllocationProtocol(), state=None,
                max_attempts=current().retry.attempts(RetryScope.FILL),
                failure_log_path=getattr(runtime.config, 'failure_log_path', None),
                failure_log_context={'stage': 'translation_allocation_fill'},
            ))
    except AllocationFillError:
        raise
    except Exception as error:
        raise AllocationFillError('Engineering allocation request failed.') from error
