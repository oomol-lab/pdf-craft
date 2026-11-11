"""The formula beside a connective can finish its row before a new paragraph."""
from copy import deepcopy
import unittest
from pdf_craft.pdf import Page, PageLayout
from pdf_craft.extractor.chapter.paragraph_geometry import completed_equation_rows
from pdf_craft.extractor.chapter.primary import project_window


def row_layouts():
    return [PageLayout('text', (100, 100, 900, 250), 'A complete introduction ends here.', 0, None),
            PageLayout('equation', (450, 280, 800, 480), r'\[u = v;\] \[v = w.\]', 1, None),
            PageLayout('text', (100, 400, 250, 440), 'whence', 2, None),
            PageLayout('text', (180, 550, 800, 590), 'A separate narrative begins here.', 3, None),
            PageLayout('text', (100, 650, 900, 850), 'A complete wider paragraph follows. ' * 4, 4, None)]


class CompletedEquationRowTests(unittest.TestCase):
    def test_complete_spatial_row_supports_the_next_observed_indented_paragraph(self):
        layouts = row_layouts()
        self.assertEqual(completed_equation_rows(layouts), {layouts[2].det: 480})
        raw = Page(1, None, layouts, [], 0, 0)
        before = deepcopy(raw)
        for footnotes in (False, True):
            result = project_window([raw], footnotes)[0]
            assert result is not None
            target = next(l for l in result.layouts if l.source_order == 3)
            self.assertFalse(target.continues_from_previous)
        self.assertEqual(raw, before)

    def test_damaged_prefix_does_not_hide_the_last_completed_formula(self):
        layouts = row_layouts()
        layouts[1].text = r'\[broken \[v = w.\]'
        self.assertEqual(completed_equation_rows(layouts), {layouts[2].det: 480})
        layouts[1].text = r'\[broken \[v = w,\]'
        self.assertFalse(completed_equation_rows(layouts))

    def test_missing_end_punctuation_or_non_connective_cannot_prove_completion(self):
        layouts = row_layouts()
        layouts[1].text = r'\[u = v;\] \[v = w,\]'
        self.assertFalse(completed_equation_rows(layouts))
        layouts = row_layouts()
        layouts[2].text = 'An unrelated caption'
        self.assertFalse(completed_equation_rows(layouts))

    def test_formula_must_be_beside_this_line_and_finish_close_below_it(self):
        for box in ((450, 500, 800, 700), (450, 280, 800, 700), (0, 280, 80, 480)):
            layouts = row_layouts()
            layouts[1].det = box
            self.assertFalse(completed_equation_rows(layouts))

    def test_row_alone_does_not_force_a_break_without_following_indent(self):
        layouts = row_layouts()
        layouts[3].det = (100, 550, 800, 590)
        result = project_window([Page(1, None, layouts, [], 0, 0)], True)[0]
        assert result is not None
        self.assertTrue(next(l for l in result.layouts if l.source_order == 3).continues_from_previous)
