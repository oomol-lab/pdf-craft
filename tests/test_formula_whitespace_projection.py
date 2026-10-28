"""Accepted separators must survive formula restoration and both PCEX writers."""
# pylint: disable=protected-access
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.document.content import ChapterWriter, ContentItem
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import (
    Chapter, InlineExpression, SourceTextFragment, TextFlowItem, decode, encode,
)
from pdf_craft.transformer.canonical_text import restore_canonical_whitespace
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_anchored_content_translation import _repairing_translator


def _expression(text):
    return InlineExpression(ExpressionKind.INLINE_PAREN, text)


class FormulaProjectionTests(IsolatedAsyncioTestCase):
    async def test_all_unambiguous_gap_placements_survive_one_fill_and_both_writers(self):
        for left, between, right in (
                ('甲 ', '', '乙'), ('甲', '', ' 乙'), ('甲 ', ' ', ' 乙'),
                ('甲', ' ', '乙'), ('甲', '', '乙')):
            with self.subTest(left=left, between=between, right=right):
                source = Chapter(None, -1, [TextFlowItem('body', -1, [
                    SourceTextFragment(1, 0, (0, 0, 100, 20), [
                        'Use ', _expression('x'), _expression('y'), ' now.',
                    ]),
                ])])
                response = ('<xml><fragment id="1">取'
                            f'<expression id="2">{left}</expression>{between}'
                            f'<expression id="3">{right}</expression>。</fragment></xml>')
                translator, runtime = _repairing_translator([response])
                translator._translate_text_async = AsyncMock(return_value='取甲 乙。')
                target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
                self.assertEqual(runtime.context_value.calls, 1)
                expected = Chapter(None, -1, [TextFlowItem('body', -1, [
                    SourceTextFragment(1, 0, (0, 0, 100, 20), [
                        '取', _expression('x'), ' ', _expression('y'), '。',
                    ]),
                ])])
                self.assertEqual(target, expected)
                self.assertEqual(decode(fromstring(tostring(encode(target))), allow_legacy=False), expected)
                with TemporaryDirectory() as directory:
                    writer = ChapterWriter(Path(directory))
                    writer.append(ContentItem('chapter_head.xml', None, -1, 0, target.flow_items[0]))
                    writer.finish()
                    self.assertEqual(decode(fromstring((Path(directory) / 'chapter_head.xml').read_bytes()),
                                            allow_legacy=False), expected)

    async def test_math_reference_padding_and_empty_source_slot_survive(self):
        source = Chapter(None, -1, [TextFlowItem('body', -1, [
            SourceTextFragment(1, 0, (0, 0, 100, 20), [
                'Use ', _expression('x ='), _expression('y'), ' now.',
            ]),
        ])])
        # Canonical math stays one reference even though the source has two IDs.
        response = ('<xml><fragment id="1">取<expression id="2"> '
                    '<math-ref key="m1"/></expression>'
                    '<expression id="3"/>。</fragment></xml>')
        translator, runtime = _repairing_translator([response])
        translator._translate_text_async = AsyncMock(return_value=r'取 \(x = y\)。')
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(runtime.context_value.calls, 1)
        item = target.flow_items[0]
        assert isinstance(item, TextFlowItem)
        fragment = item.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertEqual(fragment.content, ['取 ', _expression('x ='), _expression('y'), '。'])
        self.assertEqual(decode(fromstring(tostring(encode(target))), allow_legacy=False), target)


class ReplacementOwnershipTests(TestCase):
    def test_gap_mode_comes_from_surviving_parent_not_temporary_formula(self):
        source = fromstring('<fragment white-space="pre">取<expression>source</expression>后</fragment>')
        root = fromstring('<fragment>取<expression> 甲\n</expression>后</fragment>')
        self.assertTrue(restore_canonical_whitespace(
            [root], '取  甲\n后', [source], replaced_tags=frozenset({'expression'})))
        self.assertEqual(root.text, '取  ')
        self.assertEqual(root[0].text, '甲')
        self.assertEqual(root[0].tail, '\n后')

    def test_interior_math_spaces_remain_inside_formula(self):
        root = fromstring('<fragment>取<expression>x=y</expression>。</fragment>')
        self.assertTrue(restore_canonical_whitespace(
            [root], '取x = y。', replaced_tags=frozenset({'expression'})))
        self.assertEqual(root[0].text, 'x = y')
        self.assertEqual(root.text, '取')
        self.assertEqual(root[0].tail, '。')

    def test_ambiguous_gap_across_empty_replacement_requires_explicit_position(self):
        root = fromstring('<fragment><expression>a </expression><expression/>'
                          '<expression>b</expression></fragment>')
        before = tostring(root)
        self.assertFalse(restore_canonical_whitespace(
            [root], 'a b', replaced_tags=frozenset({'expression'})))
        self.assertEqual(tostring(root), before)
        # The empty token will restore a real formula. A separator explicitly
        # placed before or after it is meaningful; never guess between the two.
        for index in (0, 1):
            root[0].text = 'a'
            root[0].tail = root[1].tail = None
            root[index].tail = ' '
            self.assertTrue(restore_canonical_whitespace(
                [root], 'a b', replaced_tags=frozenset({'expression'})))
            self.assertEqual(root[index].tail, ' ')
        root[0].tail = root[1].tail = ' '
        before = tostring(root)
        self.assertFalse(restore_canonical_whitespace(
            [root], 'a b', replaced_tags=frozenset({'expression'})))
        self.assertEqual(tostring(root), before)

    def test_replacement_constraint_is_adapter_specific(self):
        root = fromstring('<fragment>a<expression>x </expression>b</fragment>')
        self.assertTrue(restore_canonical_whitespace([root], 'ax b'))
        self.assertEqual(root[0].text, 'x ')
        self.assertEqual(root[0].tail, 'b')
