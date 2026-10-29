"""Conservative removal of vector fills hidden by the existing opaque eraser."""
from unittest import TestCase

from pypdf import PdfWriter
from pypdf.generic import ContentStream, FloatObject, NameObject

from pdf_craft.pipeline.pdf.covered_paths import remove_covered_fills
from pdf_craft.pipeline.pdf.eraser import EraseRectangle
from pdf_craft.pipeline.pdf.geometry import PageRectangle


MASK = (EraseRectangle(1, PageRectangle(20, 20, 100, 100), (255, 255, 255)),)


def make_page(operations):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    content = ContentStream(None, writer)
    content.operations = [([FloatObject(value) for value in operands], operator)
                          for operands, operator in operations]
    page[NameObject('/Contents')] = writer._add_object(content)  # pylint: disable=protected-access
    return page


def page_data(page):
    content = page.get_contents()
    assert content is not None
    return content.get_data()


class CoveredFillTests(TestCase):
    def test_only_fully_covered_fills_are_removed(self):
        page = make_page([
            ([30, 100, 10, 10], b're'), ([], b'f'),
            ([10, 100, 30, 10], b're'), ([], b'f'),
            ([30, 100, 10, 10], b're'), ([], b'S'),
            ([30, 100, 10, 10], b're'), ([], b'B'),
        ])
        self.assertEqual(remove_covered_fills(page, MASK, 200), 1)
        content = page.get_contents()
        assert content is not None
        self.assertEqual([op for _, op in content.operations], [b're', b'f', b're', b'S', b're', b'B'])

    def test_curve_control_points_and_antialias_margin_are_preserved(self):
        page = make_page([
            ([30, 100], b'm'), ([200, 100, 200, 110, 30, 110], b'c'), ([], b'f'),
            ([20.5, 100, 10, 10], b're'), ([], b'f'),
        ])
        before = page_data(page)
        self.assertEqual(remove_covered_fills(page, MASK, 200), 0)
        self.assertEqual(page_data(page), before)

    def test_nested_transforms_are_applied_and_restored(self):
        page = make_page([
            ([], b'q'), ([2, 0, 0, 2, 10, 0], b'cm'),
            ([], b'q'), ([1, 0, 0, 1, 5, 0], b'cm'),
            ([5, 50, 5, 5], b're'), ([], b'f'), ([], b'Q'), ([], b'Q'),
            ([5, 50, 5, 5], b're'), ([], b'f'),
        ])
        self.assertEqual(remove_covered_fills(page, MASK, 200), 1)
        self.assertEqual(remove_covered_fills(page, MASK, 200), 0)

    def test_clipping_path_is_never_removed_even_when_filled(self):
        page = make_page([
            ([30, 100, 10, 10], b're'), ([], b'W'), ([], b'f'),
            ([30, 100, 10, 10], b're'), ([], b'W*'), ([], b'n'),
        ])
        before = page_data(page)
        self.assertEqual(remove_covered_fills(page, MASK, 200), 0)
        self.assertEqual(page_data(page), before)

    def test_unsupported_interleaved_state_and_malformed_restore_are_conservative(self):
        page = make_page([([30, 100, 10, 10], b're'), ([1], b'w'), ([], b'f')])
        self.assertEqual(remove_covered_fills(page, MASK, 200), 0)
        page = make_page([([30, 100, 10, 10], b're'), ([], b'f'), ([], b'Q')])
        before = page_data(page)
        self.assertEqual(remove_covered_fills(page, MASK, 200), 0)
        self.assertEqual(page_data(page), before)
