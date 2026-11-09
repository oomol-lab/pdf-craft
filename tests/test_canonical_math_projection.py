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
from pdf_craft.transformer.canonical_projection import MathProjectionError, SourceMath, project_canonical_math
from pdf_craft.transformer.free_translation import AllocationFillError
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.translation_coverage import accepted_translation
from pdf_craft.transformer.xml_translator.xml.xml_like import XMLLikeNode
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.xml_translator.segment import TextPosition, combine_text_segments, search_text_segments
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
    def test_empty_mappings_survive_on_either_side_of_math(self):
        for empty_first in (True, False):
            for bound in (True, False):
                with self.subTest(empty_first=empty_first, bound=bound):
                    empty = fromstring('<p role="empty"/>')
                    formula = fromstring('<p>译文 $x$。</p>')
                    mappings = [(empty, []), (formula, list(search_text_segments(formula)))]
                    if not empty_first:
                        mappings.reverse()
                    source = fromstring('<inline_expr kind="inline_dollar">x</inline_expr>')
                    bindings: list[list[SourceMath | str]] = [[SourceMath('$x$', source)]] if bound else [[]]
                    projected = project_canonical_math([None, *mappings], '译文 $x$。', bindings)
                    assert projected is not None
                    self.assertIsNone(projected[0])
                    for mapping, original in zip(projected[1:], mappings):
                        assert mapping is not None
                        self.assertIs(mapping[0], original[0])
                        root, = [root for root, _ in combine_text_segments(mapping[1])]
                        if original[0] is empty:
                            self.assertEqual(root.tag, 'p')
                            self.assertEqual(root.attrib, {'role': 'empty'})
                            self.assertEqual(''.join(root.itertext()), '')
                            self.assertEqual(len(root), 0)
                            self.assertEqual(mapping[1][0].position, TextPosition.TEXT)
                        elif bound:
                            self.assertEqual(tostring(root, encoding='unicode'),
                                '<p>译文 <inline_expr kind="inline_dollar">x</inline_expr>。</p>')
                        else:
                            self.assertEqual(''.join(root.itertext()), '译文 $x$。')

    def test_consumed_tail_field_becomes_empty_root_text(self):
        first = fromstring('<p>$</p>')
        second = fromstring('<p><div/>x$</p>')
        fields = [field for field in search_text_segments(second) if field.position == TextPosition.TAIL]
        self.assertEqual([field.position for field in fields], [TextPosition.TAIL])
        source = fromstring('<inline_expr>x</inline_expr>')
        projected = project_canonical_math([
            (first, list(search_text_segments(first))), (second, fields),
        ], '$x$', [[SourceMath('$x$', source)]])
        assert projected is not None and projected[0] is not None and projected[1] is not None
        root, = [root for root, _ in combine_text_segments(projected[0][1])]
        self.assertEqual(tostring(root, encoding='unicode'), '<p><inline_expr>x</inline_expr></p>')
        self.assertEqual(projected[1][1][0].text, '')
        self.assertEqual(projected[1][1][0].position, TextPosition.TEXT)

    async def test_empty_cross_page_allocation_commits_translation_and_formula(self):
        for empty_first in (True, False):
            with self.subTest(empty_first=empty_first):
                fragments = [
                    SourceTextFragment(24, 0, (0, 0, 90, 20), ['Original lead']),
                    SourceTextFragment(25, 0, (0, 0, 90, 20), ['Original value ', expression('x')]),
                ]
                filled = ['<fragment id="1"/>', '<fragment id="2">译文 <expression>'
                          '<math-ref key="m1"/></expression>。</fragment>']
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
            '<xml><fragment id="1"><expression/>等式 <math-ref key="m1"/>。</fragment></xml>')
        self.assertEqual(content_of(target), ['等式 ', expression('x=y'), '。'])
        self.assertNotIn('$', await asyncio.to_thread(_pdf_patch_text, target))

    async def test_incomplete_engineering_correspondence_exhausts_fill(self):
        source = chapter(['A character ', expression(r'\chi\mod q'), ', ',
                          expression(r'\chi_0(n)=1'), ' or ', expression(r'\chi_0(n)=0'), '.'])
        for canonical in (
            r'$\chi$ 是模 $q$ 的特征；当条件成立时 $\chi_0(n)=1$，否则 $\chi_0(n)=0$。',
            r'$\chi\mod q$；当条件成立时 $\chi_0(n)=0$，否则 $\chi_0(n)=0$。',
        ):
            with self.subTest(canonical=canonical):
                translator, runtime = _repairing_translator([])
                translator._translate_text_async = AsyncMock(return_value=canonical)
                with self.assertRaises(AllocationFillError):
                    await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
                self.assertEqual(runtime.context_value.calls, 0)

    async def test_reordered_and_repeated_formulas_do_not_follow_slot_identity(self):
        source = chapter([expression('x'), ' then ', expression('y'), ' then ', expression('x')])
        target = await self.translate(source, '$y$ then $x$ then $x$',
            '<xml><fragment id="1"><expression id="2"><math-ref key="m1"/></expression> then '
            '<expression id="3"><math-ref key="m2"/></expression> then '
            '<expression id="4"><math-ref key="m3"/></expression></fragment></xml>')
        self.assertEqual([item.content for item in content_of(target) if isinstance(item, InlineExpression)],
                         ['y', 'x', 'x'])

    async def test_math_delimiters_crossing_inline_formatting_are_one_formula(self):
        source = chapter(['Value ', expression('x=y'), '.'])
        target = await self.translate(source, '$x=y$.',
            '<xml><fragment id="1"><expression>$x</expression>=y$.</fragment></xml>')
        self.assertEqual(content_of(target), [expression('x=y'), '.'])

    async def test_math_projection_failure_uses_existing_budget_without_partial_adoption(self):
        source = chapter(['Equality: ', expression('x=y'), '.'])
        response = '<xml><fragment id="1"><expression><math-ref key="m1"/></expression>。</fragment></xml>'
        translator, runtime = _repairing_translator([response, response])
        translator._translate_text_async = AsyncMock(return_value='$x=y$。')
        calls = 0

        def flaky_projection(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise MathProjectionError('Formula projection failed.')
            return project_canonical_math(*args)

        with patch('pdf_craft.transformer.structured_translation.project_canonical_math', flaky_projection):
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
            '<xml><p id="1">等式 <expression><math-ref key="m1"/></expression>。</p></xml>'])
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
            '<xml><p id="1"><expression id="2"><math-ref key="m1"/></expression> then '
            '<expression id="3"><math-ref key="m2"/></expression>.</p></xml>'])
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
            '<xml><caption id="1">等式 <expression><math-ref key="m1"/></expression>。</caption></xml>'])
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
            '<xml><fragment id="1"><expression><math-ref key="m1"/></expression></fragment></xml>')
        self.assertEqual([item.content for item in content_of(target) if isinstance(item, InlineExpression)],
                         [r'\text{a  b}'])
