"""Opt-in offline XML replay of four exact e81368d paid-smoke artist responses.

Old JSON supplies manually retained reference boundaries ONLY in this fixture
builder. The production model substitute returns XML; no allocator is imported.
"""
# pylint: disable=protected-access
import json
import os
import re
from functools import lru_cache, partial
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, skipUnless

from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment
from pdf_craft.transformer.canonical_math import CanonicalMath
from pdf_craft.transformer.canonical_projection import formula_content
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.common.whitespace import normalize_whitespace
from tests.test_canonical_math_projection import expression
from tests.test_service_364_replay import xml_oracle
from tests.translation_model import translator_fixture

FIXTURES = os.environ.get('PDF_CRAFT_PAID_FILL_FIXTURES')
CASES = ('68ea16f37b0a422bbb7a4d09e2843b44', '3e4d3dba890b492ea95c57c4e6be4851',
         '97ced5c0c8cc4ff4a5652b665bb4dd18', 'baf3e76ba47243c28f11984ffd57f5d7')


def reference_texts(raw, units):
    """Uniquely solve saved first-answer ends jointly to prepare a test oracle.

    This deliberately does not implement the old after protocol or accept its
    JSON in production. It verifies the retained material has one reference
    partition before constructing the XML answer tested below.
    """
    math = [match.span() for match in CanonicalMath.matches(raw)]
    @lru_cache(maxsize=None)
    def solve(index: int, cursor: int) -> list[tuple[int, ...]]:
        if index == len(units):
            return [()] if not raw[cursor:].strip() else []
        unit = units[index]
        if unit.get('text') == '':
            ends = [cursor]
        else:
            anchor = unit['end']
            ends = [match.start()+len(anchor) for match in re.finditer(re.escape(anchor), raw)
                    if match.start()+len(anchor) > cursor]
        answers = []
        for end in ends:
            if any(start < end < finish for start, finish in math):
                continue
            for tail in solve(index+1, end):
                answers.append((end, *tail))
                if len(answers) == 2:
                    return answers
        return answers
    solutions = solve(0, 0)
    assert len(solutions) == 1, 'Reference XML requires a unique complete retained partition.'
    ends: list[int] = list(solutions[0])
    ends[-1] = len(raw)
    texts, cursor = [], 0
    for end in ends:
        texts.append(raw[cursor:end])
        cursor = end
    return texts


def replay_answer(raw, texts, payload, units, messages):
    if messages[0]['content'].startswith('You are a translator.'):
        return raw
    return xml_oracle(messages[1]['content'], texts, payload['sources'], units)


@skipUnless(FIXTURES, 'Set PDF_CRAFT_PAID_FILL_FIXTURES to the retained e81368d paid output')
class PaidFillReplayTests(IsolatedAsyncioTestCase):
    async def test_four_failed_allocation_groups_use_one_xml_engineering_stage(self):
        assert FIXTURES is not None
        for identifier in CASES:
            with self.subTest(failure_log_id=identifier):
                path = next(Path(FIXTURES).rglob(identifier)) / 'attempts.jsonl'
                row = json.loads(path.read_text().splitlines()[0])
                payload = json.loads(row['messages'][1]['content'])
                units = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', row['response'].strip()))
                self.assertEqual([unit['id'] for unit in units], [source['id'] for source in payload['sources']])
                raw = payload['translation']
                texts = reference_texts(raw, units)
                occurrences = CanonicalMath.matches(raw)
                offset = 0
                for unit, text in zip(units, texts):
                    for formula in unit['formulas']:
                        occurrence = occurrences[int(formula['target'][1:])-1]
                        start, end = occurrence.start()-offset, occurrence.end()-offset
                        self.assertTrue(0 <= start < end <= len(text))
                        formula.update(text=text[start:end], before=text[:start], after=text[end:])
                    offset += len(text)
                items = []
                for index, source in enumerate(payload['sources']):
                    rest = source['source'].split('Text to translate:\n', 1)[-1]
                    content = []
                    for value in source['formulas'].values():
                        before, rest = rest.split(value, 1)
                        content.extend([before, expression(formula_content(value)[0])])
                    content.append(rest)
                    items.append(TextFlowItem('body', index, [SourceTextFragment(1, index, (0, index*30, 100, index*30+25), content)]))
                translator, model = translator_fixture(max_group_score=100000)
                model.answer = partial(replay_answer, raw, texts, payload, units)
                target = await NarrativeXMLTransformer(translator).transform(Chapter(None, -1, items), error_policy=False)
                self.assertEqual(len(target.flow_items), len(items))
                self.assertTrue(all(not getattr(item, '_translation_missing') for item in target.flow_items))
                self.assertEqual(len(model.wire_calls), 2, 'One artist and one complete XML reply; no JSON allocation')
                from pdf_craft.extractor.chapter.chapter import encode
                # Flatten rendered formula notation through the adapter, not raw
                # PCEX itertext (whose rich math deliberately restores notation).
                self.assertEqual(len(list(encode(target).iter('inline_expr'))), sum(len(source['formulas']) for source in payload['sources']))
                for item, expected in zip(target.flow_items, texts):
                    assert isinstance(item, TextFlowItem)
                    if not any(source['formulas'] for source in payload['sources']):
                        actual = ''.join(part for fragment in item.children for part in fragment.content if isinstance(part, str))
                        self.assertEqual(normalize_whitespace(actual, trim=True), normalize_whitespace(expected, trim=True))
