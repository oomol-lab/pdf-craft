"""Whitespace is sentence content between inline formula/formatting slots."""
# pylint: disable=protected-access
import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring

from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import Chapter, InlineExpression, SourceTextFragment, TextFlowItem
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from tests.test_anchored_content_translation import _repairing_translator


class InlineWhitespaceTests(TestCase):
    def test_inline_gap_survives_segmentation_but_block_indentation_does_not(self):
        element = fromstring('<root>\n <p><em>one</em> <strong>two</strong></p>\n <p>end</p>\n</root>')
        self.assertEqual(''.join(s.text for s in search_text_segments(element)), 'one twoend')

    def test_adjacent_formula_gap_is_checked_and_survives_adoption(self):
        source = Chapter(None, -1, [TextFlowItem('body', -1, [
            SourceTextFragment(1, 0, (0, 0, 100, 20), [
                'Use ', InlineExpression(ExpressionKind.INLINE_PAREN, 'x'),
                InlineExpression(ExpressionKind.INLINE_PAREN, 'y'), ' now.',
            ]),
        ])])
        response = ('<xml><fragment id="1">取<expression id="2">甲</expression> '
                    '<expression id="3">乙</expression>。</fragment></xml>')
        translator, runtime = _repairing_translator([response, response])
        translator._translate_text_async = AsyncMock(return_value='取甲 乙。')
        target = asyncio.run(NarrativeXMLTransformer(translator).transform(source, error_policy=False))
        self.assertEqual(runtime.context_value.calls, 1)
        text = target.flow_items[0]
        assert isinstance(text, TextFlowItem)
        fragment = text.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertIn(' ', fragment.content)

    def test_added_inline_gap_still_requires_a_repair(self):
        source = Chapter(None, -1, [TextFlowItem('body', -1, [
            SourceTextFragment(1, 0, (0, 0, 100, 20), [
                'Use ', InlineExpression(ExpressionKind.INLINE_PAREN, 'x'),
                InlineExpression(ExpressionKind.INLINE_PAREN, 'y'), ' now.',
            ]),
        ])])
        response = ('<xml><fragment id="1">取<expression id="2">甲</expression> '
                    '<expression id="3">乙</expression>。</fragment></xml>')
        translator, runtime = _repairing_translator([response, response.replace('</expression> ', '</expression>')])
        translator._translate_text_async = AsyncMock(return_value='取甲乙。')
        asyncio.run(NarrativeXMLTransformer(translator).transform(source, error_policy=False))
        self.assertEqual(runtime.context_value.calls, 2)
