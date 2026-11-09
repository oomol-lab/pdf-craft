"""Reduced online OCR cases exercise XML-only engineering and semantic source IDs."""
from unittest import IsolatedAsyncioTestCase
from pdf_craft.extractor.chapter.chapter import InlineExpression
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_canonical_math_projection import chapter, expression, content_of
from tests.test_elastic_formula_restore import model_replies
from tests.translation_model import translator_fixture


class OnlineFormulaXMLTests(IsolatedAsyncioTestCase):
    async def test_split_math_preserves_additional_artist_text_without_reusing_source(self):
        translator, model = translator_fixture()
        raw = r'模 \(q\) 的特征 \(\chi\)。'
        model_replies(model, raw, ['<xml><fragment id="1">模 <math-ref key="m1"/> 的特征 <expression><source-math key="s1"><math-ref key="m2"/></source-math></expression>。</fragment></xml>'])
        target = await NarrativeXMLTransformer(translator).transform(chapter(['A character ', expression(r'\chi\mod q'), '.']), error_policy=False)
        self.assertEqual(content_of(target), [r'模 \(q\) 的特征 ', expression(r'\chi'), '。'])
        self.assertEqual(len(model.wire_calls), 2)

    async def test_residual_math_before_and_after_source_span_survives(self):
        for before, after in [('', '1'), ('a+', ''), ('a+', '1')]:
            for plain in (False, True):
                with self.subTest(before=before, after=after, plain=plain):
                    value = before + r'0<\Re(s)<' + after
                    raw = '零点满足 ' + (value if plain else r'\('+value+r'\)') + '。'
                    inside = before + r'<source-math key="s1">0&lt;\Re(s)&lt;</source-math>' + after
                    translated = inside if plain else r'\(' + inside + r'\)'
                    translator, model = translator_fixture()
                    model_replies(model, raw, [f'<xml><fragment id="1">零点满足 <expression>{translated}</expression>。</fragment></xml>'])
                    target = await NarrativeXMLTransformer(translator).transform(chapter(['A zero satisfies ', expression(r'0<\Re(s)<'), ' 1.']), error_policy=False)
                    actual = [part.content if isinstance(part, InlineExpression) else str(part) for part in content_of(target)]
                    self.assertEqual(''.join(actual), '零点满足 '+value+'。')
                    self.assertIn(expression(r'0<\Re(s)<'), content_of(target))

    async def test_zero_one_reordering_follows_explicit_keys_not_slots(self):
        translator, model = translator_fixture()
        raw = r'否则 \(x=0\)，条件成立 \(x=1\)。'
        model_replies(model, raw, ['<xml><fragment id="1">否则 <expression id="2"><source-math key="s2"><math-ref key="m1"/></source-math></expression>，条件成立 <expression id="3"><source-math key="s1"><math-ref key="m2"/></source-math></expression>。</fragment></xml>'])
        target = await NarrativeXMLTransformer(translator).transform(chapter([expression('x=1'), ' if true, otherwise ', expression('x=0'), '.']), error_policy=False)
        self.assertEqual(content_of(target), ['否则 ', expression('x=0'), '，条件成立 ', expression('x=1'), '。'])
        self.assertEqual(len(model.wire_calls), 2)
