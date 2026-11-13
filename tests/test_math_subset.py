"""The math optimization is bounded, transactional, and never owns translation."""
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft import SubmitKind, RetryPolicy
from pdf_craft.common.whitespace import is_whitespace
from pdf_craft.formula.comparison import normalize_latex, plain_formula, same_formula
from pdf_craft.formula.math_subset import MathParser, MAX_LENGTH, MAX_DEPTH
from pdf_craft.transformer.fill_text import comparison_text, text_difference
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.chapter_formula_interrupter import ChapterFormulaInterrupter
from pdf_craft.extractor.chapter.chapter import encode, decode
from tests.fill_fixtures import chapter, expression, model_replies
from tests.translation_model import translator_fixture


class MathSubsetTests(TestCase):
    def test_one_sided_and_split_wrappers_only_affect_comparison(self):
        for value in (r'\(20.0', r'20.0\)', '$20.0', '20.0$', r'\[20.0', '20.0$$'):
            with self.subTest(value=value):
                self.assertEqual(normalize_latex(value), '20.0')
                self.assertIsNone(text_difference(value, '20.0'))
        whole = r'\(\chi(n)=\chi^{\prime}(n)\)'
        split = r'\(\chi(n)=\)\(\chi^{\prime}(n)\)'
        for gap in ('', ' ', '\u3000', '\x08\u200b\ufeff'):
            with self.subTest(gap=gap):
                fragmented = split.replace(r'\)\(', '\\)' + gap + '\\(')
                self.assertIsNone(text_difference(whole, fragmented))
        self.assertEqual(normalize_latex(whole), 'χ(n)=χ^{′}(n)')
        self.assertIsNotNone(text_difference(whole, split.replace('(n)', '(m)', 1)))
        self.assertIsNotNone(text_difference(whole, split.replace('=', '+')))
        self.assertIsNotNone(text_difference(r'\(f(x)\)', r'\(fx\)'))

    def test_recognized_math_not_generic_tex_controls_delimiter_removal(self):
        self.assertEqual(normalize_latex(r'\(\gcd(a,b)\)'), r'\gcd{}(a,b)')
        self.assertEqual(normalize_latex(r'\(O^{*}(R)\)'), 'O^{*}(R)')
        self.assertEqual(normalize_latex(r'\(\mathbb{R}^{+}\)'), r'\mathbb{R}^{+}')
        for value in (r'\(\section{Title}', r'$\unknown{x}', '$ordinary prose',
                      r'\(\frac{1}', r'\(x^{', '$。word $20$'):
            with self.subTest(value=value):
                self.assertEqual(normalize_latex(value), value)

    def test_normalized_wrappers_are_equivalent_to_bare_text(self):
        for value in ('$ 20 $', '$$20$$', r'\(20\)', r'\[ 20 \]',
                      '$$ $ 20 $ $$', r'\($20$\)', '$$$20$$$'):
            with self.subTest(value=value):
                self.assertEqual(normalize_latex(value), '20')
                self.assertEqual(comparison_text(value), comparison_text('20'))
        self.assertEqual(normalize_latex(r'$\frac{1}{ 2}$'), r'\frac{1}{2}')

    def test_wrapper_whitespace_is_the_shared_unicode_policy(self):
        for gap in (' ', '\t', '\r\n', '\u00a0', '\u3000', '\x08', '\u200b', '\u2060', '\ufeff'):
            with self.subTest(gap=gap):
                self.assertTrue(all(is_whitespace(c) for c in gap))
                self.assertEqual(normalize_latex(f'$${gap}${gap}20{gap}${gap}$$').strip(), '20')
        self.assertNotEqual(comparison_text('$2\u200d0$'), comparison_text('$20$'))

    def test_prose_is_not_mathematics_even_when_tex_accepts_it(self):
        for text in ('前面一个 $。后面是 $x+1$。', '$这是一段中文$，后面是 $20$。',
                     '$ordinary prose$ then $x$', r'$\section{Heading}$',
                     'The dollar signs ($, $).', 'Percentage: 20% of all. Next paragraph.'):
            with self.subTest(text=text):
                self.assertEqual(normalize_latex(text), text)

    def test_unknown_construct_rolls_back_earlier_optional_conversion(self):
        text = r'Before $\alpha$ then $\unknown{x}$ after.'
        self.assertEqual(normalize_latex(text), text)
        original = MathParser.expression
        calls = []
        def fail_later(parser, *args, **kwargs):
            calls.append(None)
            if len(calls) == 2:
                raise RuntimeError('optional parser failed')
            return original(parser, *args, **kwargs)
        with patch.object(MathParser, 'expression', fail_later):
            self.assertEqual(normalize_latex(r'$\alpha$ then $\beta$'), r'$\alpha$ then $\beta$')

    def test_incomplete_operator_and_bounded_nesting_are_literal(self):
        for text in ('$' + '{'*(MAX_DEPTH+2) + 'x' + '}'*(MAX_DEPTH+2) + '$',
                     '$' + '1'*(MAX_LENGTH+10) + '$', r'$\frac{1}$'):
            with self.subTest(length=len(text)):
                self.assertEqual(normalize_latex(text), text)
        self.assertEqual(normalize_latex('cash $20 $30.').count('$'), 0)

    def test_base_equivalence_is_never_rejected_by_optional_interpretation(self):
        left = '$前缀 $$x$$\n$y$ 计算\n$$z$$'
        right = '$前缀 $$x$$$y$ 计算$$z$$'
        self.assertIsNone(text_difference(left, right))
        # Cross-fragment/code whitespace still follows the agreed whole-text view.
        self.assertIsNone(text_difference('a\u3000b\n c', 'ab c'))
        self.assertIsNotNone(text_difference(left, right.replace('x', 'q')))

    def test_source_reuse_does_not_confuse_grouping_or_changed_values(self):
        for source, target in [('$20$', '$$ 20 $$'), ('20', r'\(20\)'),
                               (r'$\frac{1}{2}$', r'\(\frac{1}{ 2}\)')]:
            with self.subTest(source=source):
                self.assertTrue(same_formula(source, target))
        for source, target in [('$4$', '$$40$$'), (r'$\frac{1}{2+3}$', r'$\frac{1}{2}+3$'),
                               (r'$\unknown{x}$', '$x$')]:
            with self.subTest(source=source):
                self.assertFalse(same_formula(source, target))
        with patch('pdf_craft.formula.comparison.LatexNodes2Text', side_effect=RuntimeError('failed')):
            self.assertIsNone(plain_formula('$20$'))
            self.assertFalse(same_formula('$20$', '$20$'))


class MathSubsetFillTests(IsolatedAsyncioTestCase):
    async def test_split_source_formulas_accept_one_translation_without_repair(self):
        artist = r'Relation \(\chi(n)=\chi^{\prime}(n)\).'
        filled = r'Relation \(\chi(n)=\)\(\chi^{\prime}(n)\).'
        for epub in (False, True):
            with self.subTest(epub=epub):
                translator, model = translator_fixture()
                tag = 'p' if epub else 'fragment'
                answer = (f'<xml><{tag} id="1">Relation '
                          r'<expression signature="1">\(\chi(n)=\)</expression>'
                          r'<expression signature="2">\(\chi^{\prime}(n)\)</expression>'
                          f'.</{tag}></xml>')
                model_replies(model, artist, [answer])
                if epub:
                    adapter = XMLInterrupter()
                    source = fromstring('<body><p>Relation <math><mi>x</mi></math>'
                                        '<math><mi>y</mi></math>.</p></body>')
                    target, _ = await StructuredTranslation(formulas=adapter).translate(
                        translator, TranslationTask(source, SubmitKind.REPLACE, None),
                        error_policy=False, retry_policy=RetryPolicy(fill=0))
                    delivered = adapter.visible_text(target)
                else:
                    target = await NarrativeXMLTransformer(translator).transform(
                        chapter(['Relation ', expression('x'), expression('y'), '.']),
                        error_policy=False, retry_policy=RetryPolicy(fill=0))
                    persisted = encode(target)
                    self.assertEqual(decode(persisted), target)
                    fragment = persisted.find('.//fragment')
                    assert fragment is not None
                    delivered = ChapterFormulaInterrupter().visible_text(fragment)
                # The normalization is NOT written into the delivered expression.
                self.assertEqual(delivered, filled)
                self.assertIsNone(text_difference(artist, delivered))
                self.assertEqual(len(model.wire_calls), 2)

    async def test_bare_scalar_survives_source_reuse_in_both_formats(self):
        for epub in (False, True):
            for source, translated in [('20', '20'), ('4', '40')]:
                with self.subTest(epub=epub, source=source):
                    translator, model = translator_fixture()
                    tag = 'p' if epub else 'fragment'
                    artist = f'{translated} 亿'
                    model_replies(model, artist, [
                        f'<xml><{tag} id="1"><expression>{translated}</expression> 亿</{tag}></xml>'])
                    if epub:
                        adapter = XMLInterrupter()
                        result, _ = await StructuredTranslation(formulas=adapter).translate(
                            translator, TranslationTask(fromstring(
                                f'<body><p><math><mn>{source}</mn></math> billion.</p></body>'),
                                SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=0))
                        delivered = adapter.visible_text(result)
                    else:
                        value = await NarrativeXMLTransformer(translator).transform(
                            chapter([expression(source), ' billion.']),
                            error_policy=False, retry_policy=RetryPolicy(fill=0))
                        persisted = encode(value)
                        self.assertEqual(decode(persisted), value)
                        fragment = persisted.find('.//fragment')
                        assert fragment is not None
                        delivered = ChapterFormulaInterrupter().visible_text(fragment)
                    self.assertEqual(delivered, artist)
                    self.assertEqual(len(model.wire_calls), 2)  # No repair of program-introduced delimiters.

    async def test_correct_answer_with_lone_dollar_needs_one_fill(self):
        artist = '$� 开头\n\n$$x$$\n$y$ 计算\n$$z$$'
        delivered = artist.replace('\n', '')
        translator, model = translator_fixture()
        model_replies(model, artist, [f'<xml><p id="1">{delivered}</p><p id="2"/></xml>'])
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p>Original.</p><p>Next.</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=0))
        self.assertEqual(''.join(result.itertext()), delivered)
        self.assertEqual(len(model.wire_calls), 2)

    async def test_source_scalar_reuse_and_changed_values_in_both_formats(self):
        for epub in (False, True):
            for source, translated in [('20', '20'), ('4', '40')]:
                with self.subTest(epub=epub, source=source):
                    translator, model = translator_fixture()
                    tag = 'p' if epub else 'fragment'
                    artist = f'$$ {translated} $$ 亿'
                    model_replies(model, artist, [f'<xml><{tag} id="1"><expression>${translated}$</expression> 亿</{tag}></xml>'])
                    if epub:
                        adapter = XMLInterrupter()
                        result, _ = await StructuredTranslation(formulas=adapter).translate(translator, TranslationTask(
                            fromstring(f'<body><p><math><mn>{source}</mn></math> billion.</p></body>'),
                            SubmitKind.REPLACE, None), error_policy=False)
                        self.assertEqual(result.findtext('.//mn'), source if source == translated else None)
                        self.assertEqual(comparison_text(adapter.visible_text(result)), comparison_text(artist))
                        if source != translated:
                            self.assertEqual(result.findtext('.//mtext'), '$40$')
                    else:
                        value = await NarrativeXMLTransformer(translator).transform(
                            chapter([expression(source), ' billion.']), error_policy=False)
                        formula = encode(value).find('.//inline_expr')
                        assert formula is not None
                        self.assertEqual(formula.text, translated)
                        if source == translated:
                            self.assertEqual(formula.get('kind'), '$')
                    self.assertEqual(len(model.wire_calls), 2)
