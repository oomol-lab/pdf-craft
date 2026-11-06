"""Reduced public OCR evidence through the real artist/engineer entry points."""
# pylint: disable=protected-access
import json
import re
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring

from pdf_craft.extractor.chapter.chapter import InlineExpression, decode
from pdf_craft.transformer.canonical_math import CanonicalMath
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_canonical_math_projection import content_of
from tests.test_canonical_math_projection import chapter, expression
from tests.translation_model import translator_fixture


class OnlineFormulaAllocationTests(IsolatedAsyncioTestCase):
    async def test_split_math_preserves_additional_artist_text_without_reusing_source(self):
        source = chapter(['A character ', expression(r'\chi\mod q'), '.'])
        raw = r'模 \(q\) 的特征 \(\chi\)。'
        translator, model = translator_fixture()
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return raw
            if messages[0]['content'].startswith('You are the structural fill engineer.'):
                return json.dumps([dict(id='0', text=raw, formulas=[
                    dict(key='s1', text=r'\(\chi\)', before='的特征 ', after='。')])])
            return '<xml><fragment id="1">模 <math-ref key="m1"/> 的特征 <expression><math-ref key="m2"/></expression>。</fragment></xml>'
        model.answer = answer
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(content_of(target), [r'模 \(q\) 的特征 ', expression(r'\chi\mod q'), '。'])
        self.assertEqual(len(model.wire_calls), 3)

    async def test_both_ocr_variants_restore_reordered_zero_and_one(self):
        fixture = json.loads((Path(__file__).parent / 'assets/translation/online_formula_identity_cases.json').read_text())
        for case in fixture['cases']:
            with self.subTest(case=case['name']):
                await self._run_case(case)

    async def _run_case(self, case):
        source = decode(fromstring(case['source_xml']), allow_legacy=False)
        originals = [item for item in content_of(source) if isinstance(item, InlineExpression)]
        # All source occurrences survive, while the two result equations
        # move to the opposite physical slots. Repeated chi stays distinct.
        order = list(range(len(originals)))
        one = next(i for i, item in enumerate(originals) if 'chi_{0}(n)' in item.content and item.content.rstrip().endswith('1'))
        zero = next(i for i, item in enumerate(originals) if 'chi_{0}(n)' in item.content and item.content.rstrip().endswith('0'))
        order[one], order[zero] = order[zero], order[one]
        raw = '自由译文：' + '；'.join(r'\(' + originals[index].content + r'\)' for index in order) + '。'
        translator, model = translator_fixture()
        translator._direct_fill_candidates = type(translator)._direct_fill_candidates
        allocation_attempts = []
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return raw
            if not messages[0]['content'].startswith('You are the structural fill engineer.'):
                canonical = CanonicalMath(raw)
                filled = re.sub(r'<math-ref key="m(\d+)"/>', lambda match: f'<expression id="{int(match[1])+1}">{match[0]}</expression>', canonical.text)
                return '<xml><fragment id="1">' + filled + '</fragment></xml>'
            payload = json.loads(messages[1]['content'])
            allocation_attempts.append(payload['translation'])
            formulas = []
            offset = len('自由译文：')
            for index in order:
                text = r'\(' + originals[index].content + r'\)'
                formulas.append(dict(key=f's{index+1}', text=text, before=raw[:offset], after=raw[offset+len(text):]))
                offset += len(text)+1
            # One engineering failure must not cause artist retranslation.
            return 'invalid allocation' if len(allocation_attempts) == 1 else json.dumps([
                dict(id='0', text=raw, formulas=formulas)])
        model.answer = answer
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        restored = [item for item in content_of(target) if isinstance(item, InlineExpression)]
        self.assertEqual(restored, [originals[index] for index in order])
        self.assertIn('自由译文：', content_of(target))
        self.assertEqual(allocation_attempts, [raw, raw])
        artists = [call for call in model.wire_calls if call[0].message.startswith('You are a translator.')]
        self.assertEqual(len(artists), 1)
        actual_prompt = '\n'.join(message.message for message in artists[0])
        for forbidden in ('source-math', 'JSON', 'placeholder', 's10'):
            self.assertNotIn(forbidden, actual_prompt)
