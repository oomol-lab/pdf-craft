"""PCEX fill validates the translated text before restoring source formulas."""
# pylint: disable=protected-access
import asyncio
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import (
    Chapter, InlineExpression, SourceAsset, SourceTextFragment, TextFlowItem, decode, encode,
)
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_anchored_content_translation import _repairing_translator

FIXTURE = Path(__file__).parent / 'assets/translation/fill_formula_cases.json'


def formulas(chapter):
    return [part.content for item in chapter.flow_items if isinstance(item, TextFlowItem)
            for child in item.children if isinstance(child, SourceTextFragment)
            for part in child.content if isinstance(part, InlineExpression)]


class FillFormulaLoopTests(unittest.TestCase):
    def test_three_real_paragraphs_accept_flattened_fill_then_restore_formulas(self):
        for case in json.loads(FIXTURE.read_text())['cases']:
            with self.subTest(case=case['name']):
                source = decode(fromstring(case['source_xml']), allow_legacy=False)
                original = deepcopy(source)
                translator, runtime = _repairing_translator([case['reference_fill']])
                translator._translate_text_async = AsyncMock(return_value=case['translated_text'])
                target = asyncio.run(NarrativeXMLTransformer(translator).transform(source))
                self.assertEqual(runtime.context_value.calls, 1)
                self.assertEqual(source, original)
                self.assertFalse(getattr(target.flow_items[0], '_translation_missing'))
                self.assertEqual(Counter(formulas(target)), Counter(formulas(source)))
                source_text, target_text = source.flow_items[0], target.flow_items[0]
                assert isinstance(source_text, TextFlowItem)
                assert isinstance(target_text, TextFlowItem)
                self.assertEqual(
                    [(c.page_index, c.source_order, c.bbox) for c in target_text.children
                     if isinstance(c, SourceTextFragment)],
                    [(c.page_index, c.source_order, c.bbox) for c in source_text.children
                     if isinstance(c, SourceTextFragment)],
                )
                self.assertEqual(
                    [c for c in target_text.children if isinstance(c, SourceAsset)],
                    [c for c in source_text.children if isinstance(c, SourceAsset)],
                )
                xml = tostring(encode(target), encoding='unicode')
                self.assertNotIn('<expression', xml)
                self.assertNotIn('<anchor', xml)
                self.assertNotIn('__PDF_CRAFT_CHAPTER_FORMULA', xml)
                self.assertEqual(decode(encode(target), allow_legacy=False), target)

    def test_canonical_check_retries_wrong_prose_before_formula_restoration(self):
        source = Chapter(None, -1, [TextFlowItem('body', -1, [
            SourceTextFragment(1, 0, (0, 0, 100, 20), [
                'Apply ', InlineExpression(ExpressionKind.INLINE_PAREN, r'16\times'), ' downsampling.',
            ]),
        ])])
        incorrect = '<xml><fragment id="1">进行<expression>16倍</expression>上采样。</fragment></xml>'
        corrected = incorrect.replace('上采样', '下采样')
        translator, runtime = _repairing_translator([incorrect, corrected])
        translator._translate_text_async = AsyncMock(return_value='进行16倍下采样。')
        target = asyncio.run(NarrativeXMLTransformer(translator).transform(source))
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertIn('canonical translation', runtime.context_value.messages[1][0][-1].message)
        self.assertEqual(formulas(target), [r'16\times'])
        target_text = target.flow_items[0]
        assert isinstance(target_text, TextFlowItem)
        fragment = target_text.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertEqual(fragment.content[-1], '下采样。')

    def test_expression_still_requires_ordinary_structure_and_ids(self):
        source = Chapter(None, -1, [TextFlowItem('body', -1, [
            SourceTextFragment(1, 0, (0, 0, 100, 20), [
                'Apply ', InlineExpression(ExpressionKind.INLINE_PAREN, r'16\times'), ' downsampling.',
            ]),
        ])])
        corrected = '<xml><fragment id="1">进行<expression>16倍</expression>下采样。</fragment></xml>'
        for incorrect in [corrected.replace('expression', 'span'),
                          '<xml><fragment id="1">进行16倍下采样。</fragment></xml>',
                          corrected.replace('<expression>', '<expression id="99">')]:
            with self.subTest(incorrect=incorrect):
                translator, runtime = _repairing_translator([incorrect, corrected])
                translator._translate_text_async = AsyncMock(return_value='进行16倍下采样。')
                target = asyncio.run(NarrativeXMLTransformer(translator).transform(source))
                self.assertEqual(runtime.context_value.calls, 2)
                self.assertEqual(formulas(target), [r'16\times'])
                self.assertFalse(getattr(target.flow_items[0], '_translation_missing'))
