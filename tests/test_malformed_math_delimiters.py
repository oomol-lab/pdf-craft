"""Free artist prose must not consume later complete formula occurrences."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase, IsolatedAsyncioTestCase
from xml.etree.ElementTree import Element

from pdf_craft.transformer.canonical_math import CanonicalMath
from pdf_craft.transformer.allocation_text import translated_math_targets, translated_math_evidence
from pdf_craft.transformer.canonical_projection import SourceMath
from pdf_craft.transformer.source_math import SourceMathTranslation
from pdf_craft.transformer.free_translation import allocate
from pdf_craft.expression import ExpressionKind, to_markdown_string
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.extractor.chapter.chapter import SourceTextFragment, TextFlowItem, Chapter
from tests.test_canonical_math_projection import expression, content_of
from tests.translation_model import translator_fixture

DELIMITERS = ((r'\[', r'\]'), (r'\(', r'\)'), ('$', '$'), ('$$', '$$'),
              (r'\\[', r'\\]'), (r'\\(', r'\\)'))
TEX_BODIES = (
    r'\begin{matrix}a&b\\(c+d)&e\end{matrix}',
    r'\begin {aligned}a&=b\\[2mm](c+d)&=e\\*[3pt]f&=g\end {aligned}',
    r'f(x)=\begin{cases}1&\text{if $x>0$}\\0&\text{otherwise}\end{cases}',
    r'\text{if \(x>0\), cost \$3, \{literal\}}+y',
    r'\shortstack{first\\(c+d)\\{group} and $x>0$}+y',
    r'\hbox\bgroup if $x>0$ and \(y<0\)\egroup+z',
    r'\text{if $x % fake { and \(bad'+'\n'+r'>0$}+y',
)


class MalformedMathDelimitersTests(TestCase):
    def test_row_commands_and_braced_math_keep_the_outer_span_and_identity(self):
        for opening, closing in DELIMITERS:
            for body in TEX_BODIES:
                with self.subTest(opening=opening, body=body):
                    raw = opening+body+closing
                    prefix = r'Reference \(unfinished.'+'\n\n'
                    text = prefix+raw
                    matches = CanonicalMath.matches(text)
                    self.assertEqual([m.group() for m in matches], [raw])
                    self.assertEqual(matches[0].span(), (len(prefix), len(text)))
                    self.assertEqual(translated_math_evidence(text)[0]['text'], raw)
                    source = Element('formula', name='complete-outer')
                    math = SourceMathTranslation([SourceMath(to_markdown_string(ExpressionKind.INLINE_PAREN, body), source)])
                    units = [dict(id='0', text=prefix, formulas=[]),
                             dict(id='1', text=raw, formulas=[dict(key='s1', target='t1')])]
                    self.assertEqual(translated_math_targets(units, text)['t1'][0], '1')
                    self.assertEqual(allocate(json.dumps(units), [SimpleNamespace(source_math=None),
                        SimpleNamespace(source_math=math)], text), {'0': prefix, '1': raw})
                    units[1]['formulas'] = [dict(key='s1', text=raw, before='', after='')]
                    self.assertEqual(allocate(json.dumps(units), [SimpleNamespace(source_math=None),
                        SimpleNamespace(source_math=math)], text), {'0': prefix, '1': raw})
                    self.assertEqual(math.bindings, [[math.values['s1']]])
                    self.assertIs(math.values['s1'].element, source)
                    table = CanonicalMath(text)
                    self.assertEqual(table.values, {'m1': raw})
                    root = Element('xml')
                    root.text = prefix
                    root.append(Element('math-ref', key='m1'))
                    self.assertIsNone(table.expand(root))
                    self.assertEqual(''.join(root.itertext()), text)

    def test_abandoned_groups_and_mixed_escape_widths_recover_complete_children(self):
        for prefix in (r'\(bad {unclosed ', r'\(bad ', r'\\(bad ', r'\\\\(bad '):
            for width in (1, 2, 4):
                value = '\\'*width+'(a'+'\\'*width+')'
                with self.subTest(prefix=prefix, width=width):
                    self.assertEqual([m.group() for m in CanonicalMath.matches(prefix+value)], [value])

    def test_deep_groups_and_many_abandoned_openers_do_not_use_recursion(self):
        raw = r'\('+r'\text{$'*1000+'x'+'$}'*1000+r'\)'
        self.assertEqual([m.group() for m in CanonicalMath.matches(raw)], [raw])
        text = r'\(unfinished '*10000+r'\(a\)'
        self.assertEqual([m.group() for m in CanonicalMath.matches(text)], [r'\(a\)'])

    def test_commented_openers_and_literal_percentages_do_not_split_formulas(self):
        for opening, closing in DELIMITERS:
            with self.subTest(opening=opening):
                comment = r'\(not a formula { \begin{matrix}' if closing.startswith('$') else '$not a formula {'
                raw = opening+'a % '+comment+'\n+b'+closing
                self.assertEqual([m.group() for m in CanonicalMath.matches(raw)], [raw])
                percentage = opening+'96%'+closing
                value = opening+'x'+closing
                self.assertEqual([m.group() for m in CanonicalMath.matches(percentage+' then '+value)],
                                 [percentage, value])

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

    def test_tex_row_spacing_keeps_complete_formula_in_all_delimiters(self):
        for opening, closing in ((r'\[', r'\]'), (r'\(', r'\)'), ('$', '$'), ('$$', '$$')):
            for spacing in ('[2mm]', '*[3pt]', r' [\dimexpr 2\baselineskip-1pt\relax]', '\n[0.5em]'):
                with self.subTest(opening=opening, spacing=spacing):
                    raw = opening+r'\begin{aligned}a&=b\\'+spacing+r'c&=d\end{aligned}'+closing
                    self.assertEqual([m.group() for m in CanonicalMath.matches(raw)], [raw])
                    table = CanonicalMath(raw)
                    self.assertEqual(table.values, {'m1': raw})
                    root = Element('xml')
                    root.append(Element('math-ref', key='m1'))
                    self.assertIsNone(table.expand(root))
                    self.assertEqual(''.join(root.itertext()), raw)

    def test_row_spacing_and_abandoned_opener_preserve_real_allocation(self):
        for raw in (r'\[\begin{aligned}a&=b\\[2mm]c&=d\end{aligned}\]',
                    r'\(\begin{matrix}a & b \\[3pt] c & d\end{matrix}\)'):
            with self.subTest(raw=raw):
                prefix = r'Reference \(unfinished.'+'\n\n'
                text = prefix+raw
                source = Element('formula', source='row-spaced-math')
                math = SourceMathTranslation([SourceMath(raw, source)])
                units = [dict(id='0', text=prefix, formulas=[]),
                         dict(id='1', text=raw, formulas=[dict(key='s1', target='t1')])]
                self.assertEqual([item['text'] for item in translated_math_evidence(text)], [raw])
                self.assertEqual(translated_math_targets(units, text)['t1'][0], '1')
                self.assertEqual(allocate(json.dumps(units), [SimpleNamespace(source_math=None),
                    SimpleNamespace(source_math=math)], text), {'0': prefix, '1': raw})
                self.assertEqual(math.bindings, [[math.values['s1']]])
                self.assertIs(math.values['s1'].element, source)


class MalformedMathPipelineTests(IsolatedAsyncioTestCase):
    async def test_complete_outer_tex_survives_artist_allocation_and_original_restoration(self):
        for opening, closing in DELIMITERS:
            for body in TEX_BODIES:
                with self.subTest(opening=opening, body=body):
                    await self._assert_outer_tex_roundtrip(opening, closing, body)

    async def _assert_outer_tex_roundtrip(self, opening, closing, body):
        source = Chapter(None, -1, [TextFlowItem('body', -1, [SourceTextFragment(
            1, 0, (0, 0, 100, 30), ['原文 ', expression(body), '。'])])])
        saved = deepcopy(source)
        raw = 'Value '+opening+body+closing+'.'
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
                self.assertEqual([item['text'] for item in payload['translated_math']], [opening+body+closing])
                return json.dumps([dict(id='0', text=raw, formulas=[dict(key='s1', target='t1')])])
            return '<xml><fragment id="1">Value <expression><math-ref key="m1"/></expression>.</fragment></xml>'
        model.answer = answer
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(content_of(target), ['Value ', expression(body), '.'])
        self.assertEqual(source, saved)
        self.assertEqual(calls, ['artist', 'allocate'])

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
