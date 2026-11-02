"""A page break before inline math is not itself a natural paragraph break."""
from copy import deepcopy
import unittest
from pdf_craft.pdf import Page, PageLayout
from pdf_craft.extractor.chapter.primary import project_window
from pdf_craft.extractor.chapter.mergeable import check_mergeable
from pdf_craft.extractor.chapter.chapter import InlineExpression
from pdf_craft.expression import ExpressionKind


def page(index, text, *, kind='text'):
    return Page(index, None, [PageLayout(kind, (100, 100, 900, 300), text, 0, None)], [], 0, 0)


class LeadingMathPageContinuationTests(unittest.TestCase):
    def test_inline_formula_can_complete_the_previous_page_sentence(self):
        for text in (r'\(u = 12\) .', r'$u = 12$ .'):
            for footnotes in (False, True):
                with self.subTest(text=text, footnotes=footnotes):
                    raw = [page(1, 'Continue this calculation up to'), page(2, text)]
                    before = deepcopy(raw)
                    result = project_window(raw, footnotes)[1]
                    assert result is not None
                    self.assertTrue(result.layouts[0].continues_from_previous)
                    self.assertEqual(raw, before)

    def test_same_page_and_display_formulas_are_not_automatically_joined(self):
        formula = InlineExpression(ExpressionKind.INLINE_PAREN, 'u = 12')
        self.assertFalse(check_mergeable(['An earlier independent item'], [formula, ' begins another item.']))
        result = project_window([page(1, 'An unfinished introduction'),
                                 page(2, r'\[u = 12\]', kind='equation')], True)[1]
        assert result is not None
        self.assertFalse(result.layouts[0].continues_from_previous)

    def test_finished_sentence_or_numbered_marker_still_separates_pages(self):
        for left, right in (('The previous sentence is complete.', r'\(u = 12\) starts a new idea.'),
                            ('An unfinished description', r'\((1)\) A numbered item.'),
                            ('An unfinished description', r'\(2\) A possible item label.')):
            with self.subTest(left=left, right=right):
                result = project_window([page(1, left), page(2, right)], True)[1]
                assert result is not None
                self.assertFalse(result.layouts[0].continues_from_previous)
