"""Recover damaged OCR wrappers without interpreting or rewriting formula bodies."""
import unittest

from pdf_craft.expression import ExpressionKind, parse_latex_expressions


class ExpressionRecoveryTests(unittest.TestCase):
    def test_restart_leaves_broken_prefix_literal_and_recovers_next_formula(self):
        for opener, closer, kind in (
            (r'\(', r'\)', ExpressionKind.INLINE_PAREN),
            (r'\[', r'\]', ExpressionKind.DISPLAY_BRACKET),
        ):
            with self.subTest(opener=opener):
                prefix = '23 A report ' + opener + '20 million. 24 Another note '
                items = list(parse_latex_expressions(prefix + opener + r'25\%' + closer + ' ends.'))
                self.assertEqual([(x.kind, x.content) for x in items], [
                    (ExpressionKind.TEXT, prefix), (kind, r'25\%'),
                    (ExpressionKind.TEXT, ' ends.')])
                # The existing plain-text escape contract, not delimiter repair.
                reparsed = list(parse_latex_expressions(''.join(x.reverse() for x in items)))
                self.assertEqual(reparsed, items)

    def test_conflicting_asymmetric_openers_and_closers_recover_locally(self):
        for broken, valid, kind in (
            (r'\(broken ', r'\[x\]', ExpressionKind.DISPLAY_BRACKET),
            (r'\[broken ', r'\(x\)', ExpressionKind.INLINE_PAREN),
            (r'\(broken \] prose ', r'\(x\)', ExpressionKind.INLINE_PAREN),
            (r'\[broken \) prose ', r'\[x\]', ExpressionKind.DISPLAY_BRACKET),
        ):
            with self.subTest(broken=broken):
                items = list(parse_latex_expressions(broken + valid))
                self.assertEqual([(x.kind, x.content) for x in items],
                                 [(ExpressionKind.TEXT, broken), (kind, 'x')])

    def test_escaped_and_grouped_delimiters_are_not_outer_restarts(self):
        bodies = [r'x + \\(y\\)', r'\text{label \(a\) and \[b\]} + x',
                  r'\text{price $5} + x', r'\{x\} + \frac{a}{b}']
        for opener, closer in ((r'\(', r'\)'), (r'\[', r'\]'), ('$', '$'), ('$$', '$$')):
            for body in bodies:
                with self.subTest(opener=opener, body=body):
                    items = list(parse_latex_expressions(opener + body + closer))
                    self.assertEqual(len(items), 1)
                    self.assertNotEqual(items[0].kind, ExpressionKind.TEXT)
                    self.assertEqual(items[0].content, body)

    def test_complex_tex_is_not_limited_to_comparison_math_subset(self):
        for body in (r'\begin{matrix}a & b \\ c & d\end{matrix}',
                     r'\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}'):
            text = '\\[\n' + body + '\n\\]'
            items = list(parse_latex_expressions(text))
            self.assertEqual([(x.kind, x.content) for x in items],
                             [(ExpressionKind.DISPLAY_BRACKET, '\n' + body + '\n')])
            self.assertEqual(''.join(x.reverse() for x in items), text)

    def test_adjacent_symmetric_formulas_keep_closing_semantics(self):
        for text, bodies in ((r'$a$$b$', ['a', 'b']),
                             (r'$$a$$$$b$$', ['a', 'b']),
                             (r'\(a\)\(b\)', ['a', 'b']),
                             (r'\[a\]\[b\]', ['a', 'b'])):
            with self.subTest(text=text):
                items = list(parse_latex_expressions(text))
                self.assertEqual([x.content for x in items], bodies)
                self.assertTrue(all(x.kind != ExpressionKind.TEXT for x in items))

    def test_escaped_closer_and_newline_rules_remain_distinct(self):
        for text, body in ((r'\(a \\) b\)', r'a \\) b'),
                           (r'\[a \\] b\]', r'a \\] b'),
                           (r'$a\$b$', r'a\$b')):
            with self.subTest(text=text):
                self.assertEqual([x.content for x in parse_latex_expressions(text)], [body])
        items = list(parse_latex_expressions('\\(broken\nprose \\(x\\)'))
        self.assertEqual([x.content for x in items], ['\\(broken\nprose ', 'x'])

    def test_many_broken_starts_do_not_hide_final_formula(self):
        prefix = r'\(broken ' * 10000
        items = list(parse_latex_expressions(prefix + r'\(x\)'))
        self.assertEqual([(x.kind, x.content) for x in items],
                         [(ExpressionKind.TEXT, prefix), (ExpressionKind.INLINE_PAREN, 'x')])

    def test_backslash_does_not_escape_inline_newline_restriction(self):
        for opener, closer in ((r'\(', r'\)'), ('$', '$')):
            text = opener + 'a\\\n' + 'b' + closer
            self.assertTrue(all(x.kind == ExpressionKind.TEXT
                                for x in parse_latex_expressions(text)))

    def test_equation_asset_keeps_damaged_prefix_and_real_formula_separate(self):
        # pylint: disable=import-outside-toplevel,protected-access
        from pdf_craft.extractor.chapter.asset_text import _AssetHolder, _normalize_equation
        asset = _AssetHolder(1, 'equation', (0, 0, 100, 100), None,
                             r'Label \(broken \(\int_0^1 x dx\) tail.', None, None)
        _normalize_equation(asset)
        self.assertEqual(asset.title, r'Label \(broken ')
        self.assertEqual(asset.content, r'\int_0^1 x dx')
        self.assertEqual(asset.caption, ' tail.')
