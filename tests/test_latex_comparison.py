"""LaTeX spelling is a comparison view, never a model/output protocol."""
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft import RetryPolicy, SubmitKind
from pdf_craft.formula.comparison import normalize_latex
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.fill_text import comparison_text, text_difference
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.xml_translator.xml.friendly import decode_friendly
from tests.fill_fixtures import chapter, expression, model_replies
from pdf_craft.extractor.chapter.chapter import encode
from pdf_craft.transformer.chapter_formula_interrupter import ChapterFormulaInterrupter
from tests.translation_model import translator_fixture


class LatexComparisonTests(TestCase):
    def test_supported_spellings_and_unicode(self):
        for left, right in [
            (r'$\frac{1}{2}$', r'\(\frac{1}{ 2}\)'),
            (r'$$\frac{1}{2}$$', r'\[\frac{1}{2}\]'),
            (r'\frac{1}{2}', r'$\frac{1}{2}$'),
            (r'$\alpha + \beta = \infty$', 'α + β = ∞'),
            (r'$x^{2}$', 'x^2'),
            (r'$x_{2}$', 'x_2'),
            (r'$x^{2}$.', 'x^2.'),
            (r'$x$ + y', 'x + y'),
            (r'$x$ $2$', 'x2'),
            (r'94\%', '94%'),
            ('e\u0301\t' + r'\(\alpha\)' + '\u200b', 'éα'),
        ]:
            with self.subTest(left=left):
                self.assertEqual(comparison_text(left), comparison_text(right))

    def test_plain_prose_unknown_and_malformed_syntax_remain_literal(self):
        for value in [
            'costs $20 and $30.', 'costs $20 $30.', 'A lone $20.',
            r'C:\Users\Person', r'\unknown{a}', r'$\unknown{a}$',
            r'$\frac{1}$', r'\frac{1}', r'\(x', r'$\alpha{',
            r'\begin{unknown}a\end{unknown}', r'\input{private.tex}',
            r'ordinary \ with a backslash', r'\today', r'\maketitle',
        ]:
            with self.subTest(value=value):
                self.assertEqual(normalize_latex(value), value)

    def test_currency_padding_does_not_turn_missing_dollars_into_equivalence(self):
        for padding in ('', ' ', '\t', '\n', '\r\n', '\u00a0', '\u2003', '\u3000',
                        '\x08', '\u200b', '\u2060', '\ufeff', ' \u200b\t\u3000'):
            for amount in ('30', '.30', '-30', '+30', '−30', '(30)', '− .30', '٫٣٠'):
                for separator in (' / ', ', '):
                    raw = f'Prices: $20{separator}${padding}{amount}.'
                    missing = raw.replace('$', '')
                    with self.subTest(padding=padding, amount=amount, separator=separator):
                        self.assertEqual(normalize_latex(raw), raw)
                        self.assertNotEqual(comparison_text(raw), comparison_text(missing))
                        difference = text_difference(raw, missing)
                        assert difference is not None
                        self.assertIn('Missing 2, added 0', difference)
                        self.assertIn("'$'", difference)

    def test_postfixed_currency_lists_and_ranges_keep_their_dollar_signs(self):
        for padding in ('', ' ', '\t', '\n', '\r\n', '\u00a0', '\u2003', '\u3000',
                        '\x08', '\u200b', '\u2060', '\ufeff', ' \u200b\t\u3000'):
            for amount in ('30', '.30', '-30', '+30', '−30', '(30)', '− .30', '٫٣٠'):
                for separator in (' / ', '–', ', '):
                    raw = f'Prices: 20{padding}${separator}{amount}{padding}$.'
                    with self.subTest(padding=padding, amount=amount, separator=separator):
                        self.assertEqual(normalize_latex(raw), raw)
                        difference = text_difference(raw, raw.replace('$', ''))
                        assert difference is not None
                        self.assertIn('Missing 2, added 0', difference)
                        self.assertIn("'$'", difference)

    def test_failures_preserve_construct_and_other_valid_text(self):
        self.assertEqual(normalize_latex(r'$\unknown{a}$ then $\alpha$'), r'$\unknown{a}$ then α')
        for target in ('LatexWalker', 'LatexNodes2Text', '_render'):
            with self.subTest(target=target), patch(
                    f'pdf_craft.formula.comparison.{target}', side_effect=ValueError('unavailable')):
                self.assertEqual(normalize_latex(r'Before $\alpha$ after.'), r'Before $\alpha$ after.')
                self.assertEqual(comparison_text(r'Before $\alpha$ after.'), r'Before$\alpha$after.')

    def test_real_content_and_math_grouping_stay_distinct(self):
        for left, right in [
            (r'$\frac{1}{2+3}$', r'$\frac{1}{2}+3$'),
            (r'$\frac{1}{2}$', r'$\frac{1}{3}$'),
            (r'$x^{2}$', r'$x_{2}$'),
            (r'$\unknown{a}$', '$a$'),
            (r'$\sin x$', r'$\sinx$'),
            ('1.5', '15'), ('-1', '1'), ('A', 'a'), ('x²', 'x2'),
            ('a\u200db', 'ab'), ('ё', 'е'),
        ]:
            with self.subTest(left=left):
                self.assertNotEqual(comparison_text(left), comparison_text(right))

    def test_literal_tex_glyphs_do_not_collide_with_structural_syntax(self):
        for literal, structural in [
            (r'$\frac{\{1\}}{2}$', r'$\frac{{1}}{2}$'),
            (r'$A_{\{1\}}$', r'$A_{{1}}$'),
            (r'$x_\{2\}$', r'$x_{2}$'),
            (r'$\{1\}$', r'${1}$'),
            (r'$x\_2$', r'$x_2$'),
            (r'$x\textunderscore2$', r'$x_2$'),
            (r'$x\textasciicircum{2}$', r'$x^{2}$'),
            (r'$\begin{matrix}1\&2\end{matrix}$', r'$\begin{matrix}1&2\end{matrix}$'),
            (r'$94\%$', '$94%\n$'),
        ]:
            with self.subTest(literal=literal):
                self.assertNotEqual(comparison_text(literal), comparison_text(structural))
                self.assertIsNotNone(text_difference(literal, structural))

    def test_diff_uses_same_complete_string_rules_across_fields(self):
        block = fromstring(r'<p id="1">Result $\frac{1}<b>{ 2}</b>$.</p>')
        self.assertIsNone(text_difference(r'Result \(\frac{1}{2}\).', ''.join(block.itertext()), [block]))
        difference = text_difference(r'Result $\alpha+1$.', 'Result α+2.', [
            fromstring('<p id="1">Result α+2.</p>')])
        assert difference is not None
        self.assertIn("expected[", difference)
        self.assertIn("'1'", difference)
        self.assertIn("'2'", difference)


class LatexFillTests(IsolatedAsyncioTestCase):
    async def test_both_sides_normalize_without_altering_request_or_delivery(self):
        raw = r'Value $\alpha+1$ and \(\frac{1}{ 2}\).'
        filled = r'Value α+1 and $\frac{1}{2}$.'
        for epub in (False, True):
            with self.subTest(epub=epub):
                translator, model = translator_fixture()
                tag = 'p' if epub else 'fragment'
                model_replies(model, raw, [f'<xml><{tag} id="1">Value <expression>α+1</expression> and $\\frac{{1}}{{2}}$.</{tag}></xml>'])
                if epub:
                    source = fromstring('<body><p>Original <math><mi>x</mi></math>.</p></body>')
                    target, _ = await StructuredTranslation(formulas=XMLInterrupter()).translate(
                        translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False)
                    delivered = XMLInterrupter().visible_text(target)
                else:
                    target = await NarrativeXMLTransformer(translator).transform(
                        chapter(['Original ', expression('x'), '.']), error_policy=False)
                    fragment = encode(target).find('.//fragment')
                    assert fragment is not None
                    delivered = ChapterFormulaInterrupter().visible_text(fragment)
                self.assertEqual(delivered, filled)
                self.assertEqual(len(model.wire_calls), 2)  # One artist, one fill.
                payload = next(decode_friendly(
                    model.wire_calls[1][1].message.split('Translated text:', 1)[1], tags='translation'))
                self.assertEqual(''.join(payload.itertext()), raw)
                self.assertEqual(list(payload), [])

    async def test_changed_value_repairs_in_same_loop_with_normalized_diff(self):
        translator, model = translator_fixture()
        raw = r'The value is $\alpha+1$.'
        bad = '<xml><p id="1">The value is α+2.</p><p id="2"/></xml>'
        good = '<xml><p id="1">The value is α+1.</p><p id="2"/></xml>'
        model_replies(model, raw, [bad, good])
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p>Original.</p><p>Next.</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(''.join(result.itertext()), 'The value is α+1.')
        self.assertEqual(len(model.wire_calls), 3)
        feedback = model.wire_calls[-1][-1].message
        self.assertIn("'1'", feedback)
        self.assertIn("'2'", feedback)
        self.assertNotIn('Missing 8', feedback)

    async def test_currency_deletion_requires_repair_with_space_and_unicode_padding(self):
        for padding in ('', ' ', '\t', '\n', '\u00a0', '\u3000', '\u200b', ' \u200b\t\u3000'):
            for amount in ('30', '.30', '-30', '+.30', '−30', '(30)'):
                with self.subTest(padding=padding, amount=amount):
                    translator, model = translator_fixture()
                    raw = f'Prices: $20 / ${padding}{amount}.'
                    missing = raw.replace('$', '')
                    bad = f'<xml><p id="1">{missing}</p><p id="2"/></xml>'
                    good = f'<xml><p id="1">{raw}</p><p id="2"/></xml>'
                    model_replies(model, raw, [bad, good])
                    result, _ = await StructuredTranslation().translate(translator, TranslationTask(
                        fromstring('<body><p>Original.</p><p>Next.</p></body>'), SubmitKind.REPLACE, None),
                        error_policy=False, retry_policy=RetryPolicy(fill=1))
                    delivered = ''.join(result.itertext())
                    self.assertEqual(delivered.count('$'), 2)
                    self.assertEqual(comparison_text(delivered), comparison_text(raw))
                    self.assertEqual(len(model.wire_calls), 3)  # Artist once, bad fill, repaired fill.
                    feedback = model.wire_calls[-1][-1].message
                    self.assertIn('Missing 2, added 0', feedback)
                    self.assertIn("'$'", feedback)

    async def test_postfixed_currency_deletion_is_repaired_before_adoption(self):
        for padding in ('', ' ', '\t', '\n', '\u00a0', '\u3000', '\u200b', ' \u200b\t\u3000'):
            for amount in ('30', '.30', '-30', '(30)'):
                for separator in (' / ', '–', ', '):
                    with self.subTest(padding=padding, amount=amount, separator=separator):
                        translator, model = translator_fixture()
                        raw = f'Prices: 20{padding}${separator}{amount}{padding}$.'
                        missing = raw.replace('$', '')
                        bad = f'<xml><p id="1">{missing}</p><p id="2"/></xml>'
                        good = f'<xml><p id="1">{raw}</p><p id="2"/></xml>'
                        model_replies(model, raw, [bad, good])
                        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
                            fromstring('<body><p>Original.</p><p>Next.</p></body>'), SubmitKind.REPLACE, None),
                            error_policy=False, retry_policy=RetryPolicy(fill=1))
                        delivered = ''.join(result.itertext())
                        self.assertEqual(delivered.count('$'), 2)
                        self.assertEqual(comparison_text(delivered), comparison_text(raw))
                        self.assertEqual(len(model.wire_calls), 3)
                        feedback = model.wire_calls[-1][-1].message
                        self.assertIn('Missing 2, added 0', feedback)
                        self.assertIn("'$'", feedback)

    async def test_visible_braces_cannot_be_removed_by_fill(self):
        for literal, structural in [
            (r'$\frac{\{1\}}{2}$', r'$\frac{{1}}{2}$'),
            (r'$A_{\{1\}}$', r'$A_{{1}}$'),
            (r'$x_\{2\}$', r'$x_{2}$'),
        ]:
            with self.subTest(literal=literal):
                translator, model = translator_fixture()
                raw = f'Value {literal}.'
                bad = f'<xml><p id="1">Value {structural}.</p><p id="2"/></xml>'
                good = f'<xml><p id="1">{raw}</p><p id="2"/></xml>'
                model_replies(model, raw, [bad, good])
                result, _ = await StructuredTranslation().translate(translator, TranslationTask(
                    fromstring('<body><p>Original.</p><p>Next.</p></body>'), SubmitKind.REPLACE, None),
                    error_policy=False, retry_policy=RetryPolicy(fill=1))
                self.assertEqual(''.join(result.itertext()), raw)
                self.assertEqual(len(model.wire_calls), 3)  # Artist once, bad fill, repaired fill.
                feedback = model.wire_calls[-1][-1].message
                self.assertIn('Visible text differs', feedback)
                self.assertIn('missing:', feedback)
                self.assertIn('expected[', feedback)
                self.assertIn(r'\\', feedback.split('LITERAL CONTENT REPAIR', 1)[0])

    async def test_conversion_failure_does_not_add_requests_or_modify_content(self):
        translator, model = translator_fixture()
        raw = r'Keep $\unknown{a}$ and $\alpha$.'
        model_replies(model, raw, [f'<xml><p id="1">{raw}</p><p id="2"/></xml>'])
        with patch('pdf_craft.formula.comparison.LatexWalker', side_effect=RuntimeError('parser failed')):
            result, _ = await StructuredTranslation().translate(translator, TranslationTask(
                fromstring('<body><p>Original.</p><p>Next.</p></body>'), SubmitKind.REPLACE, None), error_policy=False)
        self.assertEqual(''.join(result.itertext()), raw)
        self.assertEqual(len(model.wire_calls), 2)
