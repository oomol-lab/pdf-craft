"""Whitespace is sentence content between inline formula/formatting slots."""
# pylint: disable=protected-access
import asyncio
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring

from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import Chapter, InlineExpression, SourceTextFragment, TextFlowItem
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.canonical_text import restore_canonical_whitespace
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.xml_translator.xml.friendly import decode_friendly
from tests.test_anchored_content_translation import _repairing_translator


class InlineWhitespaceTests(TestCase):
    def test_recorded_table_formula_candidate_restores_exact_canonical_padding(self):
        fixture = json.loads((Path(__file__).parent / 'assets/translation/table_formula_padding.json').read_text())
        root = next(decode_friendly(fixture['fill_response'], tags='xml'))
        identities = [(node.tag, dict(node.attrib)) for node in root.iter()]
        self.assertTrue(restore_canonical_whitespace(list(root), fixture['canonical']))
        self.assertEqual(''.join(segment.text for fragment in root
                                 for segment in search_text_segments(fragment, preserve_whitespace=True)),
                         fixture['canonical'])
        self.assertEqual([(node.tag, dict(node.attrib)) for node in root.iter()], identities)

    def test_unrepresented_formula_requires_an_empty_slot_then_restores_source(self):
        source = Chapter(None, -1, [TextFlowItem('body', -1, [
            SourceTextFragment(1, 0, (0, 0, 100, 20), [
                'Use ', InlineExpression(ExpressionKind.INLINE_PAREN, 'x'),
                InlineExpression(ExpressionKind.INLINE_PAREN, 'y'), ' now.',
            ]),
        ])])
        corrected = ('<xml><fragment id="1">取<expression id="2">甲</expression>'
                     '<expression id="3"/>。</fragment></xml>')
        missing = corrected.replace('<expression id="3"/>', '')
        translator, runtime = _repairing_translator([missing, corrected])
        translator._translate_text_async = AsyncMock(return_value='取甲。')
        target = asyncio.run(NarrativeXMLTransformer(translator).transform(source, error_policy=False))
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertIn('empty contents', runtime.context_value.messages[0][0][1].message)
        text = target.flow_items[0]
        assert isinstance(text, TextFlowItem)
        fragment = text.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertEqual(fragment.content, [
            '取', InlineExpression(ExpressionKind.INLINE_PAREN, 'x'),
            InlineExpression(ExpressionKind.INLINE_PAREN, 'y'), '。',
        ])

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
        self.assertEqual(fragment.content, [
            '取', InlineExpression(ExpressionKind.INLINE_PAREN, 'x'), ' ',
            InlineExpression(ExpressionKind.INLINE_PAREN, 'y'), '。',
        ])

    def test_added_formula_padding_is_restored_from_canonical_without_another_request(self):
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
        self.assertEqual(runtime.context_value.calls, 1)

    def test_formula_padding_repair_does_not_accept_other_changes(self):
        for original, canonical in (
                ('<fragment><expression>x</expression>wrong</fragment>', 'xright'),
                ('<fragment><expression>x</expression>word.</fragment>', 'xword!'),
        ):
            root = fromstring(original)
            from xml.etree.ElementTree import tostring
            before = tostring(root)
            self.assertFalse(restore_canonical_whitespace([root], canonical))
            self.assertEqual(tostring(root), before)

    def test_padding_is_owned_outside_restored_formula_slots(self):
        root = fromstring('<fragment>取<expression>甲</expression><expression>乙</expression>。</fragment>')
        self.assertTrue(restore_canonical_whitespace([root], '取甲 乙。'))
        self.assertEqual(root[0].tail, ' ')
        self.assertEqual(root[0].text, '甲')
        first = fromstring('<fragment><expression>x</expression></fragment>')
        second = fromstring('<fragment> word</fragment>')
        self.assertTrue(restore_canonical_whitespace([first, second], 'xword'))

    def test_empty_final_formula_padding_does_not_add_canonical_characters(self):
        root = fromstring('<fragment>而当 <expression id="25"/></fragment>')
        self.assertTrue(restore_canonical_whitespace([root], '而当'))
        self.assertEqual(root.text, '而当')
        self.assertEqual(root[0].get('id'), '25')
        self.assertIsNone(root[0].text)

    def test_canonical_edge_padding_stays_outside_formula_content(self):
        for canonical in (' x', 'x '):
            root = fromstring('<fragment><expression>x</expression></fragment>')
            self.assertTrue(restore_canonical_whitespace([root], canonical))
            self.assertEqual(''.join(root.itertext()), canonical.strip())
            self.assertEqual(root[0].text, 'x')
