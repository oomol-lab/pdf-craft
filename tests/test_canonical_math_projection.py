"""Validate final mathematical values, beyond acceptance of the fill response."""
# pylint: disable=protected-access
from copy import deepcopy
import asyncio
from io import BytesIO
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import RetryPolicy, SubmitKind
from pdf_craft.expression import ExpressionKind
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition
from pdf_craft.extractor.chapter.chapter import (
    Chapter, InlineExpression, SourceTextFragment, TextFlowItem, SourceAsset, StandaloneAsset, decode, encode,
)
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.xml_math import project_xml_math
from pdf_craft.transformer.canonical_projection import MathProjectionError
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.translation_coverage import accepted_translation
from pdf_craft.transformer.xml_translator.xml.xml_like import XMLLikeNode
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.test_anchored_content_translation import _repairing_translator, _pdf_patch_text
from tests.test_structured_translation import offline_translator


def expression(value):
    return InlineExpression(ExpressionKind.INLINE_PAREN, value)


def chapter(content):
    return Chapter(None, -1, [TextFlowItem('body', -1, [
        SourceTextFragment(2, 0, (0, 0, 90, 20), content),
    ])])


def content_of(value):
    text = value.flow_items[0]
    assert isinstance(text, TextFlowItem)
    fragment = text.children[0]
    assert isinstance(fragment, SourceTextFragment)
    return fragment.content


class CanonicalMathProjectionTests(IsolatedAsyncioTestCase):


    async def test_empty_cross_page_allocation_commits_translation_and_formula(self):
        for empty_first in (True, False):
            with self.subTest(empty_first=empty_first):
                fragments = [
                    SourceTextFragment(24, 0, (0, 0, 90, 20), ['Original lead']),
                    SourceTextFragment(25, 0, (0, 0, 90, 20), ['Original value ', expression('x')]),
                ]
                filled = ['<fragment id="1"/>', '<fragment id="2">译文 <expression>'
                          '<source-math key="s1"><math-ref key="m1"/></source-math></expression>。</fragment>']
                if not empty_first:
                    fragments.reverse()
                    filled = [filled[1].replace('id="2"', 'id="1"'), '<fragment id="2"/>']
                source = Chapter(None, -1, [TextFlowItem('body', -1, [*fragments])])
                saved = deepcopy(source)
                translator, runtime = _repairing_translator(['<xml>' + ''.join(filled) + '</xml>'])
                translator._translate_text_async = AsyncMock(return_value='译文 $x$。')
                failures = []
                target = await NarrativeXMLTransformer(translator).transform(
                    source, error_policy=lambda failure, failures=failures: failures.append(failure) or True,
                    retry_policy=RetryPolicy(fill=0))
                self.assertEqual(failures, [])
                self.assertEqual(runtime.context_value.calls, 1)
                translator._translate_text_async.assert_awaited_once()
                self.assertEqual(source, saved)
                self.assertEqual(decode(fromstring(tostring(encode(target))), allow_legacy=False), target)
                text = target.flow_items[0]
                assert isinstance(text, TextFlowItem)
                self.assertTrue(accepted_translation(text))
                self.assertEqual(len(text.children), 2)
                for index, fragment in enumerate(text.children):
                    assert isinstance(fragment, SourceTextFragment)
                    self.assertEqual(fragment.page_index, fragments[index].page_index)
                    self.assertEqual(fragment.content, [] if (index == 0) == empty_first
                                     else ['译文 ', expression('x'), '。'])
                self.assertNotIn('Original', tostring(encode(target), encoding='unicode'))

    async def translate(self, source, canonical, response):
        saved = deepcopy(source)
        translator, runtime = _repairing_translator([response])
        translator._translate_text_async = AsyncMock(return_value=canonical)
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(runtime.context_value.calls, 1)
        self.assertEqual(source, saved)
        self.assertEqual(decode(fromstring(tostring(encode(target))), allow_legacy=False), target)
        xml = tostring(encode(target), encoding='unicode')
        self.assertNotIn('math-ref', xml)
        self.assertNotIn('<expression', xml)
        self.assertNotIn('__PDF_CRAFT', xml)
        return target

    async def test_source_formula_is_indivisible_even_when_fill_puts_it_in_plain_text(self):
        target = await self.translate(chapter(['Equality: ', expression('x=y'), '.']),
            '等式 $x=y$。',
            '<xml><fragment id="1"><expression/>等式 <source-math key="s1"><math-ref key="m1"/></source-math>。</fragment></xml>')
        self.assertEqual(content_of(target), ['等式 ', expression('x=y'), '。'])
        self.assertNotIn('$', await asyncio.to_thread(_pdf_patch_text, target))


    async def test_reordered_and_repeated_formulas_do_not_follow_slot_identity(self):
        source = chapter([expression('x'), ' then ', expression('y'), ' then ', expression('x')])
        target = await self.translate(source, '$y$ then $x$ then $x$',
            '<xml><fragment id="1"><expression id="2"><source-math key="s2"><math-ref key="m1"/></source-math></expression> then '
            '<expression id="3"><source-math key="s1"><math-ref key="m2"/></source-math></expression> then '
            '<expression id="4"><source-math key="s3"><math-ref key="m3"/></source-math></expression></fragment></xml>')
        self.assertEqual([item.content for item in content_of(target) if isinstance(item, InlineExpression)],
                         ['y', 'x', 'x'])


    async def test_math_projection_failure_uses_existing_budget_without_partial_adoption(self):
        source = chapter(['Equality: ', expression('x=y'), '.'])
        response = '<xml><fragment id="1"><expression><source-math key="s1"><math-ref key="m1"/></source-math></expression>。</fragment></xml>'
        translator, runtime = _repairing_translator([response, response])
        translator._translate_text_async = AsyncMock(return_value='$x=y$。')
        calls = 0

        def flaky_projection(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise MathProjectionError('Formula projection failed.')
            return project_xml_math(*args)

        with patch('pdf_craft.transformer.structured_translation.project_xml_math', flaky_projection):
            target = await NarrativeXMLTransformer(translator).transform(
                source, error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertEqual([item.content for item in content_of(target) if isinstance(item, InlineExpression)], ['x=y'])
        self.assertIn('projection failed', runtime.context_value.messages[1][0][-1].message)

    async def test_native_epub_original_mathml_survives_serialized_xhtml(self):
        node = XMLLikeNode(BytesIO(
            b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Equality: '
            b'<math xmlns="http://www.w3.org/1998/Math/MathML" id="original"><mi>x</mi><mo>=</mo><mi>y</mi></math>.'
            b'</p></body></html>'))
        translator, runtime = _repairing_translator([
            '<xml><p id="1">等式 <expression><source-math key="s1"><math-ref key="m1"/></source-math></expression>。</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='等式 $x=y$。')
        protocol = StructuredTranslation(formulas=XMLInterrupter())
        node.element, _ = await protocol.translate(translator, TranslationTask(node.element, SubmitKind.REPLACE, None))
        result = BytesIO()
        node.save(result)
        root = fromstring(result.getvalue())
        maths = root.findall('.//{http://www.w3.org/1998/Math/MathML}math')
        self.assertEqual(len(maths), 1)
        self.assertEqual([''.join(math.itertext()) for math in maths], ['x=y'])
        self.assertEqual(maths[0].get('id'), 'original')
        self.assertNotIn('$', ''.join(root.itertext()))
        self.assertNotIn('expression', result.getvalue().decode())
        self.assertEqual(runtime.context_value.calls, 1)

    async def test_native_epub_reordered_math_preserves_the_matching_source_attributes(self):
        source = fromstring('<body><p><math id="x"><mi>x</mi></math> '
                            'then <math id="y"><mi>y</mi></math>.</p></body>')
        translator, runtime = _repairing_translator([
            '<xml><p id="1"><expression id="2"><source-math key="s2"><math-ref key="m1"/></source-math></expression> then '
            '<expression id="3"><source-math key="s1"><math-ref key="m2"/></source-math></expression>.</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='$y$ then $x$.')
        result, _ = await StructuredTranslation(formulas=XMLInterrupter()).translate(
            translator, TranslationTask(source, SubmitKind.REPLACE, None))
        self.assertEqual([math.get('id') for math in result.findall('.//math')], ['y', 'x'])
        self.assertEqual([math.find('mi').text for math in result.findall('.//math')], ['y', 'x'])
        self.assertEqual(runtime.context_value.calls, 1)

    async def test_asset_caption_preserves_original_formula_and_field_structure(self):
        source = SourceAsset(1, 'image', (0, 0, 90, 20), caption=[
            'Equality: ', expression('x=y'), '.'])
        translator, runtime = _repairing_translator([
            '<xml><caption id="1">等式 <expression><source-math key="s1"><math-ref key="m1"/></source-math></expression>。</caption></xml>'])
        translator._translate_text_async = AsyncMock(return_value='等式 $x=y$。')
        result, = await AnchoredContentXMLTransformer(translator).transform_assets([
            AnchoredContent('a', 0, -1, source, '')], error_policy=False)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual([part.content for part in result.caption if isinstance(part, InlineExpression)], ['x=y'])
        self.assertEqual([part for part in result.caption if isinstance(part, str)], ['等式 ', '。'])
        self.assertEqual(runtime.context_value.calls, 1)

    async def test_asset_mixed_cell_and_nested_blocks_preserve_owning_markup(self):
        def tag(name, children):
            definition = tag_definition(name)
            assert definition is not None
            return HTMLTag(definition, [], children)
        source = SourceAsset(1, 'table', (0, 0, 90, 20), content=[tag('table', [tag('tr', [
            tag('td', ['before ', tag('p', ['value ', expression('x=y')]), ' after']),
        ])])])
        result, = await AnchoredContentXMLTransformer(offline_translator()).transform_assets([
            AnchoredContent('a', 0, -1, source, '')], error_policy=False)
        assert result is not None
        # Decode through the actual asset pipeline before inspecting the
        # final table tree; two allocations share the outer td block.
        encoded = encode(Chapter(None, -1, [StandaloneAsset(SourceAsset(
            1, 'table', source.bbox, content=result.content))]))
        self.assertEqual([node.text for node in encoded.findall('.//td/p/inline_expr')], ['x=y'])
        self.assertEqual(len(encoded.findall('.//td/p')), 1)
        self.assertNotIn('__PDF_CRAFT', tostring(encoded, encoding='unicode'))

    async def test_source_reuse_does_not_normalize_away_canonical_tex_payload(self):
        target = await self.translate(chapter([expression(r'\text{a  b}')]), r'$\text{a  b}$',
            '<xml><fragment id="1"><expression><source-math key="s1"><math-ref key="m1"/></source-math></expression></fragment></xml>')
        self.assertEqual([item.content for item in content_of(target) if isinstance(item, InlineExpression)],
                         [r'\text{a  b}'])
