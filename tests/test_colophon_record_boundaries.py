"""Explicit publication records need a local ISBN and both record conventions."""
from copy import deepcopy
import unittest
from pdf_craft.pdf import Page, PageLayout
from pdf_craft.extractor.chapter.paragraph_geometry import colophon_record_starts
from pdf_craft.extractor.chapter.primary import project_window


def fields():
    return [PageLayout('text', (100, 100, 900, 140), '开本: 800×1100 印张: 6', 0, None),
            PageLayout('text', (200, 170, 850, 210), '2021年5月第2版 2021年5月第3次印刷', 1, None),
            PageLayout('text', (300, 240, 560, 280), '印数: 1-10 000册', 2, None),
            PageLayout('text', (100, 700, 600, 740), 'ISBN 978-3-16-148410-0', 3, None)]


class ColophonRecordBoundaryTests(unittest.TestCase):
    def test_both_record_rows_are_separate_without_changing_sources(self):
        layouts = fields()
        self.assertEqual(colophon_record_starts(layouts), {layouts[1].det, layouts[2].det})
        raw = Page(1, None, layouts, [], 0, 0)
        before = deepcopy(raw)
        for footnotes in (False, True):
            result = project_window([raw], footnotes)[0]
            assert result is not None
            for order in (1, 2):
                layout = next(l for l in result.layouts if l.source_order == order)
                self.assertFalse(layout.continues_from_previous)
        self.assertEqual(raw, before)

    def test_missing_isbn_or_one_record_family_is_not_a_colophon(self):
        for missing in (1, 2, 3):
            self.assertFalse(colophon_record_starts([v for i, v in enumerate(fields()) if i != missing]))
        layouts = fields()
        layouts[3].text = 'A story mentions ISBN 978-3-16-148410-0 in passing.'
        self.assertFalse(colophon_record_starts(layouts))

    def test_multiline_and_ordinary_prose_are_not_structured_record_rows(self):
        layouts = fields()
        layouts[1].det = (200, 170, 850, 400)
        self.assertFalse(colophon_record_starts(layouts))
        layouts = fields()
        layouts[2].text = '这本书的印数: 1000册，读者讨论了很久。'
        self.assertFalse(colophon_record_starts(layouts))

    def test_traditional_print_count_and_isbn10_share_the_same_rule(self):
        layouts = fields()
        layouts[2].text = '印數：1—10 000冊'
        layouts[3].text = 'ISBN-10: 0-306-40615-2'
        self.assertEqual(colophon_record_starts(layouts), {layouts[1].det, layouts[2].det})
