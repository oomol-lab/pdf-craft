"""Stacked numerals provide orientation evidence even in a single OCR body box."""
from copy import deepcopy
import unittest
from pdf_craft.pdf import Page, PageLayout
from tests.test_marginal_note_codes import projected


def stacked_page():
    text = '這是正文的完整敘述，並不是頁外的注釋。' * 30
    return Page(1, None, [PageLayout('text', (100, 100, 900, 1300), text, 0, None)],
                [PageLayout('text', (40, 900, 80, 980), '二五', 0, None)], 0, 0)


class StackedMarginFolioTests(unittest.TestCase):
    def test_stacked_side_numerals_do_not_need_two_separate_vertical_body_boxes(self):
        raw = stacked_page()
        before = deepcopy(raw)
        page = projected(raw)
        self.assertFalse(page.citations)
        self.assertEqual(page.layouts[-1].ownership, 'ignored')
        self.assertEqual(page.layouts[-1].source.bbox, raw.footnotes_layouts[0].det)
        assert page.layouts[-1].source.origin is not None
        self.assertEqual(page.layouts[-1].source.origin.text, '二五')
        self.assertEqual(raw, before)

    def test_horizontal_number_or_top_label_or_reference_is_not_a_stacked_folio(self):
        for box in ((10, 900, 90, 940), (40, 200, 80, 280), (100, 900, 140, 980)):
            with self.subTest(box=box):
                raw = stacked_page()
                raw.footnotes_layouts[0].det = box
                self.assertTrue(projected(raw).citations)
        for mark in ('二五', '㉕'):
            raw = stacked_page()
            raw.body_layouts[0].text += mark
            self.assertTrue(projected(raw).citations)

    def test_other_notes_and_non_cjk_body_keep_the_ambiguity(self):
        raw = stacked_page()
        raw.footnotes_layouts.append(PageLayout('text', (100, 1350, 900, 1400),
                                                'An actual note.', 1, None))
        self.assertTrue(projected(raw).citations)
        raw = stacked_page()
        raw.body_layouts[0].text = 'An ordinary horizontal reading paragraph. ' * 30
        self.assertTrue(projected(raw).citations)
