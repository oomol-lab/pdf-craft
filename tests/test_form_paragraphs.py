"""Independent form fields must not become one author paragraph."""
from pathlib import Path
import unittest
from xml.etree.ElementTree import parse

from pdf_craft.pdf import decode, PageLayout
from pdf_craft.extractor.chapter.chapter import SourceTextFragment, TextFlowItem
from pdf_craft.extractor.chapter.jointer import Jointer


def groups(layouts):
    return [[child.source_order for child in item.children
             if isinstance(child, SourceTextFragment)]
            for item in Jointer([(4, layouts)]).execute() if isinstance(item, TextFlowItem)]


class FormParagraphTests(unittest.TestCase):
    def test_recorded_ocr2_form_keeps_independent_header_fields(self):
        page = decode(parse(Path(__file__).parent / 'assets/analysis/ocr2_form/page_4.xml').getroot())
        paragraphs = groups(page.body_layouts)
        for first, second in ((0, 1), (1, 2), (2, 3)):
            self.assertFalse(any(first in group and second in group for group in paragraphs))
        self.assertEqual(sorted(n for group in paragraphs for n in group),
                         [item.order for item in page.body_layouts if item.ref == 'text'])

    def test_adjacent_labels_in_same_printed_row_are_not_column_continuations(self):
        layouts = [PageLayout('text', box, text, i, None) for i, (box, text) in enumerate([
            ((0, 0, 100, 20), 'Account'), ((120, 0, 220, 20), 'Date'),
            ((240, 0, 340, 20), 'Signature'),
        ])]
        self.assertEqual(groups(layouts), [[0], [1], [2]])
