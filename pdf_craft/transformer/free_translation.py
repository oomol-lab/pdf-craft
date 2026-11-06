# pylint: disable=unused-argument
"""Engineering allocation of an immutable, free-form group translation."""
import json
import re
from xml.etree.ElementTree import tostring

from ..llm import Message, MessageRole
from ..llm.loop import ProtocolRetry, ProtocolSuccess, RepairLoopOptions, run_repair_loop
from .._policy import current
from ..policies import RetryScope
from .canonical_projection import MathProjectionError, formula_content
from .canonical_math import CanonicalMath


class AllocationFillError(ValueError):
    """The engineer could not allocate the immutable artist response."""

    attempts: int = 1


def _prose(value):
    return re.sub(r'[ \t\r\n]+', ' ', value).strip(' ')


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
    if not isinstance(units, list) or len(units) != len(calls):
        raise ValueError('Return exactly one allocation for each source unit.')
    if any(not isinstance(unit, dict) or not isinstance(unit.get('id'), str) for unit in units):
        raise ValueError('Each allocation must be an object with a string source id.')
    by_id = {unit['id']: unit for unit in units}
    if set(by_id) != {str(index) for index in range(len(calls))}:
        raise ValueError('Allocation identities must match the source units exactly once.')
    if any(not isinstance(unit['text'], str) for unit in units):
        raise ValueError('Each allocated text must be a string.')
    if not _covers([unit['text'] for unit in units], raw):
        raise ValueError('Allocation must preserve the complete original translation, in order, without changing words.')
    units = [by_id[str(index)] for index in range(len(calls))]
    answers, bindings = {}, []
    for index, (call, unit) in enumerate(zip(calls, units)):
        text = unit['text']
        math = call.source_math
        values = math.values if math is not None else {}
        formulas = unit.get('formulas', [])
        if not isinstance(formulas, list) or any(not isinstance(item, dict) for item in formulas):
            raise ValueError('Formula allocations must be an array of objects.')
        for item in formulas:
            if item.get('preserve') is True and ('key' in item or 'keys' in item):
                raise ValueError('Additional translated math must not claim a source identity.')
            keys = item.get('keys', [] if item.get('preserve') is True else [item.get('key')])
            if not isinstance(keys, list) or (not keys and item.get('preserve') is not True) or any(not isinstance(key, str) for key in keys):
                raise ValueError('Each formula allocation must specify nonempty source keys.')
            if any(not isinstance(item.get(field), str) for field in ('text', 'before', 'after')):
                raise ValueError('Formula text and before/after context must be strings.')
        assigned = [key for item in formulas for key in item.get('keys', [] if item.get('preserve') is True else [item.get('key')])]
        if sorted(assigned) != sorted(values):
            raise ValueError(f'Assign each source identity once. Missing: {sorted(set(values)-set(assigned))}; duplicated: {sorted({key for key in assigned if assigned.count(key)>1})}; unknown: {sorted(set(assigned)-set(values))}. Additional mathematical occurrences must use preserve=true instead of reusing a source key.')
        edits, bound = [], []
        for item in formulas:
            keys, value = item.get('keys', [] if item.get('preserve') is True else [item.get('key')]), item['text']
            before, after = item['before'], item['after']
            if not value:
                raise ValueError('A formula allocation must name nonempty translated text.')
            matches = [match for match in re.finditer(re.escape(value), text)
                       if _prose(text[:match.start()]).endswith(_prose(before))
                       and _prose(text[match.end():]).startswith(_prose(after))]
            if len(matches) != 1:
                raise ValueError(f'Formula {keys}: span {value!r} with before={before!r}, after={after!r} matches {len(matches)} occurrences. Copy the translated span and immediately adjacent context exactly; ordinary whitespace is ignored in context.')
            edits.append((matches[0].start(), matches[0].end(), [values[key] for key in keys], value))
        for match in CanonicalMath.matches(text):
            if any(start <= match.start() and end >= match.end() for start, end, *_ in edits):
                continue
            if any(start < match.end() and end > match.start() for start, end, *_ in edits):
                raise ValueError(f'Use complete mathematical delimiters in the formula span: {match.group()!r}.')
            # Additional artist math has no source identity to restore. Keeping
            # it verbatim is not a source match or a translation quality gate.
            edits.append((match.start(), match.end(), [], match.group()))
        edits.sort(key=lambda item: item[0])
        cursor, parts, projected_formulas = 0, [], []
        for start, end, group, original in edits:
            if start < cursor:
                raise ValueError('Formula allocations cannot overlap.')
            if not group:
                if len(CanonicalMath.matches(original)) != 1 or CanonicalMath.matches(original)[0].group() != original:
                    raise ValueError('A preserved additional mathematical occurrence needs its complete original delimiters.')
                latex = original
            else:
                latex = (group[0].latex if len(group) == 1 else '\\(' + ''.join(formula_content(source.latex)[0] for source in group) + '\\)')
            if len(CanonicalMath.matches(original)) == 1 and CanonicalMath.matches(original)[0].group() == original:
                latex = original
            parts.extend((text[cursor:start], latex))
            projected_formulas.append(latex)
            bound.append(group)
            cursor = end
        parts.append(text[cursor:])
        canonical = ''.join(parts)
        if math is not None and [match.group() for match in CanonicalMath.matches(canonical)] != projected_formulas:
            raise MathProjectionError('Account for all mathematical occurrences and their complete delimiters.')
        answers[str(index)] = canonical
        bindings.append((math, bound))
    # Commit identities only after the complete engineering allocation passes.
    for math, bound in bindings:
        if math is not None:
            math.bindings = bound
            math.allocated = True
    return answers


async def fill_translation_group(runtime, calls, raw):
    """The raw artist response first enters fill here and stays fixed on repair."""
    # A unique identity allocation is an engineering operation. This does not
    # assess translation quality or change/retry the artist response.
    if all(call.source_math is None and getattr(call, 'source_text', None) is not None for call in calls):
        if _covers([call.source_text for call in calls], raw):
            return {str(index): call.source_text for index, call in enumerate(calls)}
    sources = [dict(id=str(index), source=call.messages[-1].message,
                    formula_context=call.source_math.evidence if call.source_math is not None else {},
                    formulas={key: value.latex for key, value in call.source_math.values.items()}
                    if call.source_math is not None else {},
                    formula_nodes={key: tostring(value.element, encoding='unicode')
                                   for key, value in call.source_math.values.items()}
                    if call.source_math is not None else {}) for index, call in enumerate(calls)]
    messages = [Message(MessageRole.SYSTEM, (await runtime.template('allocate')).render()),
                Message(MessageRole.USER, json.dumps(dict(sources=sources, translation=raw), ensure_ascii=False))]

    class AllocationProtocol:
        async def validate(self, response, state, attempt, max_attempts):
            try:
                return ProtocolSuccess(allocate(response, calls, raw), state)
            except (ValueError, TypeError, KeyError) as error:
                return ProtocolRetry(str(error), state)

        async def empty(self, state, attempt, max_attempts):
            return ProtocolRetry('Return the complete allocation JSON array.', state)

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
