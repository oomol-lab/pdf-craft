"""Reduced public OCR evidence through the real artist/engineer entry points."""
# pylint: disable=protected-access
import json
import re
from types import SimpleNamespace
from xml.etree.ElementTree import Element
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft.extractor.chapter.chapter import InlineExpression, decode
from pdf_craft.transformer.canonical_math import CanonicalMath
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_canonical_math_projection import content_of
from tests.test_canonical_math_projection import chapter, expression
from tests.translation_model import translator_fixture
from pdf_craft import SubmitKind
from pdf_craft.pipeline.pdf.pipeline import _to_pdf_patch_content
from pdf_craft.transformer.free_translation import fill_translation_group
from pdf_craft.transformer.source_math import SourceMathTranslation
from pdf_craft.transformer.canonical_projection import SourceMath
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask


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

    async def test_source_formula_restoration_retains_artist_math_residuals(self):
        # Reduced from the real gundam nontrivial-zero paragraph: OCR left
        # the upper endpoint outside its formula node, the artist did not.
        for before, after in [('', '1'), ('a+', ''), ('a+', '1')]:
            with self.subTest(before=before, after=after):
                await self._run_math_residual_case(before, after)
                await self._run_math_residual_case(before, after, plain=True)

    async def _run_math_residual_case(self, before, after, plain=False):
        source = chapter(['A zero satisfies ', expression(r'0<\Re(s)<'), ' 1.'])
        value = before + r'0<\Re(s)<' + after
        raw = '零点满足 ' + (value if plain else r'\(' + value + r'\)') + '。'
        translator, model = translator_fixture()
        allocations = []
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return raw
            if messages[0]['content'].startswith('You are the structural fill engineer.'):
                allocations.append(json.loads(messages[1]['content'])['translation'])
                formula: dict = (dict(key='s1', text=value, before='', after='') if plain
                                 else dict(key='s1', target='t1'))
                if len(allocations) > 1:
                    parts = ([dict(text=before)] if before else [])
                    parts += [dict(keys=['s1'], text=r'0<\Re(s)<')]
                    parts += [dict(text=after)] if after else []
                    formula['parts'] = parts
                return json.dumps([dict(id='0', text=raw, formulas=[formula])])
            return '<xml><fragment id="1">零点满足 <expression><math-ref key="m1"/></expression>。</fragment></xml>'
        model.answer = answer
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        content = content_of(target)
        expected = ([before] if before else []) + [r'0<\Re(s)<'] + ([after] if after else [])
        self.assertEqual([part.content for part in content if isinstance(part, InlineExpression)], expected)
        patch_text, formulas = _to_pdf_patch_content(content)
        self.assertNotIn(r'\(', patch_text)
        self.assertNotIn(r'\)', patch_text)
        self.assertEqual([formula.latex for formula in formulas], expected)
        self.assertEqual(allocations, [raw, raw])
        self.assertEqual(len(model.wire_calls), 4)

    async def test_complete_partition_ids_are_adopted_without_an_empty_math_node(self):
        source = chapter([expression(r'\chi(n)='), ' ', expression(r"\chi'(n)")])
        raw = r"\(\chi(n)= \chi'(n)\)"
        translator, model = translator_fixture()
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return raw
            if messages[0]['content'].startswith('You are the structural fill engineer.'):
                # This is the real rejected response shape: all source keys
                # are explicit in parts, while the outer shorthand names s1.
                return json.dumps([dict(id='0', text=raw, formulas=[dict(key='s1', target='t1', parts=[
                    dict(keys=['s1'], text=r'\chi(n)='), dict(text=' '),
                    dict(keys=['s2'], text=r"\chi'(n)")])])])
            return '<xml><fragment id="1"><expression id="2"><math-ref key="m1"/></expression><expression id="3"/></fragment></xml>'
        model.answer = answer
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(content_of(target), [expression(r'\chi(n)='), ' ', expression(r"\chi'(n)")])
        patch_text, formulas = _to_pdf_patch_content(content_of(target))
        self.assertEqual([formula.latex for formula in formulas], [r'\chi(n)=', r"\chi'(n)"])
        self.assertNotIn('\\(', patch_text)
        self.assertEqual(len(model.wire_calls), 3)

    async def test_native_epub_mathml_restoration_preserves_endpoint_outside_source_node(self):
        source = fromstring('<body><p>A zero satisfies <math id="original"><mi>x</mi><mo>=</mo></math>1.</p></body>')
        raw = r'零点满足 \(x=1\)。'
        translator, model = translator_fixture()
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return raw
            if messages[0]['content'].startswith('You are the structural fill engineer.'):
                return json.dumps([dict(id='0', text=raw, formulas=[dict(key='s1', target='t1', parts=[
                    dict(keys=['s1'], text='x='), dict(text='1')])])])
            return '<xml><p id="1">零点满足 <expression><math-ref key="m1"/></expression>。</p></xml>'
        model.answer = answer
        target, _ = await StructuredTranslation(formulas=XMLInterrupter()).translate(
            translator, TranslationTask(source, SubmitKind.REPLACE, None))
        restored = target.findall('.//math')
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored[0].get('id'), 'original')
        self.assertEqual(''.join(restored[0].itertext()), 'x=')
        self.assertIsNone(restored[1].get('id'))
        self.assertEqual(restored[1].find('semantics/mtext').text, '1')
        self.assertEqual(restored[1].find('semantics/annotation').text, '1')
        self.assertEqual(restored[1].tail, '。')
        self.assertEqual(len(model.wire_calls), 3)

    async def test_context_only_formula_has_unique_owner_even_for_an_empty_answer(self):
        for raw in ('', 'unchanged prose', r'Extra \(x+1\)'):
            with self.subTest(raw=raw):
                math = SourceMathTranslation([SourceMath(r'\(x\)', Element('formula'), context=True)])
                # A runtime without template/request methods proves no model is
                # asked to allocate an empty source identity table. The normal
                # allocation validator still records unbound artist math.
                result = await fill_translation_group(object(), [SimpleNamespace(source_math=math)], raw)
                self.assertEqual(result, {'0': raw})
                self.assertTrue(math.allocated)
                self.assertEqual(math.bindings, [[]] if 'Extra' in raw else [])

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

    async def test_splitter_overlap_is_background_not_retranslated_formula_sources(self):
        from pdf_craft.expression import ExpressionKind
        from pdf_craft.extractor.chapter.chapter import Chapter, SourceTextFragment, TextFlowItem
        source = Chapter(None, -1, [TextFlowItem('body', -1, [
            SourceTextFragment(1, index, (0, index*20, 100, index*20+20),
                               [f'Part {index} explains the formula ',
                                InlineExpression(ExpressionKind.INLINE_PAREN, f'x_{index}'),
                                ' and continues the argument. ']) for index in range(8)])])
        translator, model = translator_fixture(max_group_score=97)
        original = model.answer
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return messages[1]['content'].split('Text to translate:\n', 1)[1].strip('\n')
            return original(messages)
        model.answer = answer
        from pdf_craft.transformer.xml_translator.xml_translator.stream_mapper import XMLStreamMapper
        truncate = XMLStreamMapper._truncate_and_transform_group
        def overlap(mapper, group):
            head, body, tail = truncate(mapper, group)
            # The observed production failure has this same readonly overlap
            # shape. Keep the real splitter/runtime/fill and inject only the
            # neighboring evidence slot to exercise it deterministically.
            return head, body, [body[-1], *tail] if body else tail
        with patch.object(XMLStreamMapper, '_truncate_and_transform_group', overlap):
            target = await NarrativeXMLTransformer(translator, window=2).transform(source, error_policy=False)
        artists = [call for call in model.wire_calls if call[0].message.startswith('You are a translator.')]
        self.assertTrue(any('Read-only background' in call[1].message for call in artists))
        body = ''.join(call[1].message.split('Text to translate:\n', 1)[1] for call in artists)
        for index in range(8):
            self.assertEqual(body.count(f'Part {index}'), 1)
            self.assertEqual(body.count(f'x_{index}'), 1)
        restored = [part.content for item in target.flow_items if isinstance(item, TextFlowItem) for fragment in item.children
                    for part in fragment.content if isinstance(part, InlineExpression)]
        self.assertEqual(restored, [f'x_{index}' for index in range(8)])
        sources = [json.loads(call[1].message)['sources'] for call in model.wire_calls
                   if call[0].message.startswith('You are the structural fill engineer.')]
        self.assertEqual(sum(len(unit['formulas']) for batch in sources for unit in batch), 8)
