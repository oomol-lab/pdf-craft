"""Canonical formula boundaries must not be inferred from source expression IDs."""
# pylint: disable=protected-access
import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import Chapter, InlineExpression, SourceTextFragment, TextFlowItem
from pdf_craft.transformer.canonical_math import CanonicalMath
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_anchored_content_translation import _repairing_translator


def source():
    return Chapter(None, -1, [TextFlowItem('body', -1, [
        SourceTextFragment(1, 0, (0, 0, 100, 20), [
            'Take ', InlineExpression(ExpressionKind.INLINE_PAREN, 'x ='),
            InlineExpression(ExpressionKind.INLINE_PAREN, 'y'), '.',
        ]),
    ])])


class CanonicalMathAllocationTests(TestCase):
    def test_merged_canonical_math_is_one_reference_with_all_source_ids_retained(self):
        canonical = r'取 \(x = y\)。'
        response = ('<xml><fragment id="1">取 '
                    '<expression id="2"><math-ref key="m1"/></expression>'
                    '<expression id="3"/>。</fragment></xml>')
        translator, runtime = _repairing_translator([response])
        translator._translate_text_async = AsyncMock(return_value=canonical)
        target = asyncio.run(NarrativeXMLTransformer(translator).transform(source(), error_policy=False))
        self.assertEqual(runtime.context_value.calls, 1)
        request = runtime.context_value.messages[0][0][1].message
        self.assertIn('NOT independent math boundaries', request)
        self.assertIn('ONE math-ref token', request)
        self.assertIn('permitted transport payload', runtime.context_value.messages[0][0][0].message)
        item = target.flow_items[0]
        assert isinstance(item, TextFlowItem)
        fragment = item.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertEqual(fragment.content, [
            '取 ', InlineExpression(ExpressionKind.INLINE_PAREN, 'x ='),
            InlineExpression(ExpressionKind.INLINE_PAREN, 'y'), '。',
        ])

    def test_separately_rewrapped_math_is_rejected_before_reference_repair(self):
        canonical = r'取 \(x = y\)。'
        wrong = (r'<xml><fragment id="1">取 <expression id="2">\(x =\)</expression>'
                 r'<expression id="3">\(y\)</expression>。</fragment></xml>')
        corrected = ('<xml><fragment id="1">取 '
                     '<expression id="2"><math-ref key="m1"/></expression>'
                     '<expression id="3"/>。</fragment></xml>')
        wrapped_reference = corrected.replace('<math-ref key="m1"/>', r'\(<math-ref key="m1"/>\)')
        for candidate in (wrong, wrapped_reference):
            with self.subTest(candidate=candidate):
                translator, runtime = _repairing_translator([candidate, corrected])
                translator._translate_text_async = AsyncMock(return_value=canonical)
                asyncio.run(NarrativeXMLTransformer(translator).transform(source(), error_policy=False))
                self.assertEqual(runtime.context_value.calls, 2)
                self.assertIn('differs from the canonical translation', runtime.context_value.messages[1][0][-1].message)
                self.assertIn('NEVER retype', runtime.context_value.messages[1][0][-1].message)

    def test_missing_source_expression_id_still_fails_even_with_correct_math_reference(self):
        response = ('<xml><fragment id="1">取 '
                    '<expression id="2"><math-ref key="m1"/></expression>'
                    '<expression id="3"/>。</fragment></xml>')
        translator, runtime = _repairing_translator([response.replace('<expression id="3"/>', ''), response])
        translator._translate_text_async = AsyncMock(return_value=r'取 \(x = y\)。')
        asyncio.run(NarrativeXMLTransformer(translator).transform(source(), error_policy=False))
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertIn('Missing expected inline elements', runtime.context_value.messages[1][0][-1].message)

    def test_exact_delimiters_are_expanded_and_references_never_survive(self):
        for text in (r'\(x = y\)', r'\\(x = y\\)', '$x=y$', '$$x=y$$', r'\[x=y\]'):
            with self.subTest(text=text):
                math = CanonicalMath(text)
                root = fromstring('<xml><fragment><math-ref key="m1"/></fragment></xml>')
                self.assertIsNone(math.expand(root))
                self.assertEqual(root[0].text, text)
                self.assertNotIn(b'math-ref', tostring(root))

    def test_invalid_reference_tables_do_not_mutate_the_candidate(self):
        math = CanonicalMath(r'\(x\) and \(y\)')
        for body in ('<math-ref key="unknown"/>', '<math-ref key="m1"/>',
                     '<math-ref key="m1"/><math-ref key="m1"/>',
                     '<math-ref key="m1">changed</math-ref><math-ref key="m2"/>',
                     '<math-ref key="m1" extra="x"/><math-ref key="m2"/>'):
            with self.subTest(body=body):
                root = fromstring('<xml><fragment>' + body + '</fragment></xml>')
                before = tostring(root)
                self.assertIsNotNone(math.expand(root))
                self.assertEqual(tostring(root), before)

    def test_reference_expansion_does_not_hide_added_or_removed_delimiters(self):
        canonical = r'\(x = y\)'
        math = CanonicalMath(canonical)
        root = fromstring(r'<xml><fragment>\(<math-ref key="m1"/>\)</fragment></xml>')
        self.assertIsNone(math.expand(root))
        self.assertNotEqual(''.join(root[0].itertext()), canonical)
