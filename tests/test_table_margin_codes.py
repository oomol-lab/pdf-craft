"""Remote table-footer codes are distinct from nearby captions or note keys."""
from copy import deepcopy
import unittest
from pdf_craft.pdf import Page, PageLayout
from tests.test_marginal_note_codes import projected


def table_page(*, rows=4, code='ZZYY', data='Ordinary table content and explanations'):
    html = '<table>' + ''.join(f'<tr><td>{data}</td><td>{i}</td></tr>' for i in range(rows)) + '</table>'
    return Page(1, None, [PageLayout('table', (100, 100, 900, 600), html, 0, 'fixture')],
                [PageLayout('text', (100, 950, 180, 970), code, 0, None)], 0, 0)


class TableMarginCodeTests(unittest.TestCase):
    def test_substantial_table_and_remote_code_keep_source_identity(self):
        raw = table_page()
        before = deepcopy(raw)
        page = projected(raw)
        self.assertFalse(page.citations)
        self.assertEqual(page.layouts[-1].ownership, 'ignored')
        self.assertEqual(page.layouts[-1].source.bbox, raw.footnotes_layouts[0].det)
        assert page.layouts[-1].source.origin is not None
        self.assertEqual(page.layouts[-1].source.origin.text, 'ZZYY')
        self.assertEqual(raw, before)

    def test_near_caption_or_sparse_table_cannot_establish_a_footer(self):
        raw = table_page()
        raw.footnotes_layouts[0].det = (100, 640, 180, 660)
        self.assertTrue(projected(raw).citations)
        self.assertTrue(projected(table_page(rows=2)).citations)

    def test_code_mentioned_in_table_or_real_note_typography_prevents_exclusion(self):
        for data in ('A value called ZZYY', 'A note<sup>a</sup>', r'An explanation \(^{a}\)'):
            with self.subTest(data=data):
                self.assertTrue(projected(table_page(data=data)).citations)
        raw = table_page()
        raw.footnotes_layouts.append(PageLayout('text', (100, 800, 900, 850),
                                                'An actual note.', 1, None))
        self.assertTrue(projected(raw).citations)

    def test_roman_labels_and_image_pages_do_not_match(self):
        self.assertTrue(projected(table_page(code='IV')).citations)
        raw = table_page()
        raw.body_layouts[0].ref = 'image'
        self.assertTrue(projected(raw).citations)

    def test_two_rows_require_stronger_blank_gap_and_table_scale(self):
        raw = table_page(rows=2, data="Substantial ordinary table content and explanations")
        self.assertTrue(projected(raw).citations)
        raw.footnotes_layouts[0].det = (100, 1250, 180, 1270)
        self.assertFalse(projected(raw).citations)
        raw.body_layouts[0].det = (100, 100, 900, 400)
        self.assertTrue(projected(raw).citations)
