"""Free artist prose must not consume later complete formula occurrences."""
import json
from types import SimpleNamespace
from unittest import TestCase, IsolatedAsyncioTestCase
from xml.etree.ElementTree import Element

from pdf_craft.transformer.canonical_math import CanonicalMath
from pdf_craft.transformer.allocation_text import translated_math_targets, translated_math_evidence
from pdf_craft.transformer.canonical_projection import SourceMath
from pdf_craft.transformer.source_math import SourceMathTranslation
from pdf_craft.transformer.free_translation import allocate
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.extractor.chapter.chapter import SourceTextFragment, TextFlowItem, Chapter
from tests.test_canonical_math_projection import expression, content_of
from tests.translation_model import translator_fixture


class MalformedMathDelimitersTests(TestCase):
    def test_unclosed_reference_keeps_later_formula_and_unit_target(self):
        first, second = r'Reference \(Fink, 1997).'+'\n\n', r'Object \(a\).'
        raw = first+second
        self.assertEqual([m.group() for m in CanonicalMath.matches(raw)], [r'\(a\)'])
        units = [dict(id='0', text=first, formulas=[]), dict(id='1', text=second,
                    formulas=[dict(key='s1', target='t1')])]
        self.assertEqual(translated_math_evidence(raw)[0]['text'], r'\(a\)')
        self.assertEqual(translated_math_targets(units, raw)['t1'][0], '1')
        source = Element('formula', source='actual-a')
        math = SourceMathTranslation([SourceMath(r'\(a\)', source)])
        answers = allocate(json.dumps(units), [SimpleNamespace(source_math=None),
                                               SimpleNamespace(source_math=math)], raw)
        self.assertEqual(answers, {'0': first, '1': second})
        binding = math.bindings[0][0]
        self.assertIsInstance(binding, SourceMath)
        assert isinstance(binding, SourceMath)
        self.assertIs(binding.element, source)
        table = CanonicalMath(raw)
        root = Element('xml')
        node = Element('math-ref', key='m1')
        root.text = first+'Object '
        node.tail = '.'
        root.append(node)
        self.assertIsNone(table.expand(root))
        self.assertEqual(''.join(root.itertext()), raw)

    def test_nested_openers_recover_complete_inner_span_without_dropping_prose(self):
        for opening in (r'\(', r'\['):
            for complete in (r'\(a\)', r'\[a+b\]', '$a$', '$$a+b$$'):
                with self.subTest(opening=opening, complete=complete):
                    text = opening+'reference (ordinary brackets)\n\n'+complete
                    self.assertEqual([m.group() for m in CanonicalMath.matches(text)], [complete])
                    table = CanonicalMath(text)
                    self.assertTrue(table.text.startswith(opening+'reference'))

    def test_valid_multiline_repeated_and_reordered_math_remains_atomic(self):
        for value in ('\\(a\n+b\\)', '\\[a\n\n+b\\]', '$a\n+b$', '$$a\n\n+b$$'):
            with self.subTest(value=value):
                text = '(ordinary) '+value+' then '+r'\(y\)'+value
                self.assertEqual([m.group() for m in CanonicalMath.matches(text)], [value, r'\(y\)', value])
        self.assertFalse(CanonicalMath.matches(r'Price \$3 and ordinary (x) [y].'))


class MalformedMathPipelineTests(IsolatedAsyncioTestCase):
    async def test_real_artist_fill_entry_keeps_unclosed_prose_and_source_identity(self):
        source = Chapter(None, -1, [TextFlowItem('body', -1, [SourceTextFragment(
            1, 0, (0, 0, 100, 30), ['参考文献。对象 ', expression('a'), '。'])])])
        raw = r'Reference \(Fink, 1997). Object \(a\).'
        translator, model = translator_fixture()
        calls = []
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                calls.append('artist')
                return raw
            if messages[0]['content'].startswith('You are the structural fill engineer.'):
                calls.append('allocate')
                payload = json.loads(messages[1]['content'])
                self.assertEqual(payload['translation'], raw)
                self.assertEqual(payload['translated_math'][0]['text'], r'\(a\)')
                return json.dumps([dict(id='0', text=raw, formulas=[dict(key='s1', target='t1')])])
            return r'<xml><fragment id="1">Reference \(Fink, 1997). Object <expression><math-ref key="m1"/></expression>.</fragment></xml>'
        model.answer = answer
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(content_of(target), [r'Reference \(Fink, 1997). Object ', expression('a'), '.'])
        self.assertEqual(calls, ['artist', 'allocate'])
