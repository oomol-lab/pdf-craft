"""Short outer footer codes stay traceable without becoming note definitions."""
from copy import deepcopy
import unittest

from pdf_craft.pdf import Page, PageLayout
from pdf_craft.extractor.chapter.primary import PrimaryPages, project_window


def raw_page(code='ZZYY'):
    text = 'A complete reading paragraph with several instructions and explanations. '
    return Page(1, None, [
        PageLayout('text', (200, 100, 900, 350), text * 2, 0, None),
        PageLayout('text', (200, 370, 900, 700), text * 2, 1, None),
    ], [PageLayout('text', (50, 950, 130, 970), code, 0, None)], 0, 0)


def projected(raw):
    page = project_window([None, raw, None], True)[1]
    assert page is not None
    return page


class MarginalNoteCodeTests(unittest.TestCase):
    def test_outer_code_is_ignored_in_both_initial_paths_without_editing_raw(self):
        raw = raw_page()
        before = deepcopy(raw)
        for page in (projected(raw), PrimaryPages(True).append(raw)):
            with self.subTest(path=type(page).__name__):
                code = next(item for item in page.layouts if item.source_order == 2)
                self.assertEqual(code.ownership, 'ignored')
                self.assertFalse(page.citations)
                self.assertIsNotNone(code.source.origin)
                assert code.source.origin is not None
                self.assertEqual(code.source.origin.text, 'ZZYY')
                self.assertEqual(code.source.origin.stream, 'footnote')
                self.assertEqual(code.source.bbox, raw.footnotes_layouts[0].det)
        self.assertEqual(raw, before)

    def test_a_code_alone_is_insufficient(self):
        for box in [(220, 950, 300, 970), (50, 740, 130, 760),
                    (50, 950, 500, 970), (50, 950, 130, 950)]:
            with self.subTest(box=box):
                raw = raw_page()
                raw.footnotes_layouts[0].det = box
                self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')

    def test_reference_markers_keep_short_notes_for_review(self):
        for marker in ('①', '¹', '<sup>1</sup>', '<sup>a</sup>', '*', '†',
                       r'\(^{a}\)', ' Another sentence.1'):
            with self.subTest(marker=marker):
                raw = raw_page()
                raw.body_layouts[0].text += marker
                self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')

    def test_code_mentioned_in_body_or_paired_with_real_notes_is_ambiguous(self):
        raw = raw_page()
        raw.body_layouts[0].text += ' ZZYY'
        self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')
        raw = raw_page()
        raw.footnotes_layouts.append(PageLayout(
            'text', (200, 800, 900, 850), 'An unmarked note continuation.', 1, None))
        self.assertEqual(projected(raw).layouts[-2].ownership, 'citation')

    def test_caption_or_symbol_or_roman_number_is_not_a_footer_code(self):
        for text in ('A photograph caption', 'IV', 'Z', 'ZZYY text', '1234'):
            with self.subTest(text=text):
                raw = raw_page(text)
                self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')
        raw = raw_page()
        raw.body_layouts = [PageLayout('image', (200, 100, 900, 700), '', 0, None)]
        self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')

    def test_a_lower_asset_prevents_the_blank_margin_inference(self):
        raw = raw_page()
        raw.body_layouts.append(PageLayout('image', (200, 720, 900, 960), '', 2, None))
        self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')

    def test_sparse_body_and_multiple_codes_do_not_establish_footer_roles(self):
        raw = raw_page()
        raw.body_layouts = raw.body_layouts[:1]
        self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')
        raw = raw_page()
        for item in raw.body_layouts:
            item.text = 'A short line.'
        self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')
        raw = raw_page()
        raw.footnotes_layouts.append(PageLayout(
            'text', (920, 950, 1000, 970), 'YYZZ', 1, None))
        self.assertTrue(all(item.ownership == 'citation' for item in projected(raw).layouts[-2:]))
