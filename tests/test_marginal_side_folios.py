"""Side folios require isolated geometry and comparable, non-raised text scale."""
from copy import deepcopy
import unittest
from pdf_craft.pdf import Page, PageLayout
from pdf_craft.extractor.chapter.primary import PrimaryPages
from tests.test_marginal_note_codes import projected


def side_page():
    text = '這是一段完整正文，並不是頁外注釋。' * 30
    return Page(1, None, [
        PageLayout('text', (100, 100, 900, 400), text, 0, None),
        PageLayout('text', (100, 420, 900, 720), text, 1, None),
    ], [PageLayout('text', (950, 250, 970, 270), '45', 0, None)], 0, 0)


class MarginalSideFolioTests(unittest.TestCase):
    def test_an_isolated_mid_margin_folio_keeps_its_original_source(self):
        raw = side_page()
        before = deepcopy(raw)
        for page in (projected(raw), PrimaryPages(True).append(raw)):
            ignored = next(l for l in page.layouts if l.source_order == 2)
            self.assertEqual(ignored.ownership, 'ignored')
            self.assertEqual(ignored.source.bbox, raw.footnotes_layouts[0].det)
            assert ignored.source.origin is not None
            self.assertEqual(ignored.source.origin.text, '45')
            self.assertFalse(page.citations)
        self.assertEqual(raw, before)

    def test_superscript_size_or_list_item_alignment_is_not_folio_evidence(self):
        for box in ((950, 250, 959, 258), (950, 100, 970, 120), (910, 250, 930, 270)):
            with self.subTest(box=box):
                raw = side_page()
                raw.footnotes_layouts[0].det = box
                self.assertEqual(next(l for l in projected(raw).layouts if l.source_order == 2).ownership,
                                 'citation')

    def test_a_matching_body_number_or_another_note_prevents_exclusion(self):
        raw = side_page()
        raw.body_layouts[0].text += '45'
        self.assertTrue(projected(raw).citations)
        raw = side_page()
        raw.footnotes_layouts.append(PageLayout('text', (100, 800, 900, 850),
                                                'An actual note.', 1, None))
        self.assertTrue(projected(raw).citations)

    def test_asset_labels_sparse_prose_and_multiple_numbers_are_not_folios(self):
        raw = side_page()
        raw.body_layouts.append(PageLayout('image', (920, 230, 990, 300), '', 2, None))
        self.assertTrue(projected(raw).citations)
        raw = side_page()
        raw.body_layouts = raw.body_layouts[:1]
        self.assertTrue(projected(raw).citations)
        raw = side_page()
        raw.footnotes_layouts.append(PageLayout('text', (950, 500, 970, 520), '46', 1, None))
        self.assertTrue(projected(raw).citations)
