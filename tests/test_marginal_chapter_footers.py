"""Chapter-footer hypotheses preserve raw sources and reject note evidence."""
from copy import deepcopy
import unittest
from tests.test_marginal_note_codes import raw_page, projected
from pdf_craft.pdf import PageLayout
from pdf_craft.extractor.chapter.primary import PrimaryPages


def chapter_page(text='42 Chapter 7 General Principles'):
    raw = raw_page(text)
    raw.footnotes_layouts[0].det = (50, 950, 400, 970)
    return raw


class MarginalChapterFooterTests(unittest.TestCase):
    def test_footer_grammar_is_not_tied_to_a_book_or_language(self):
        for label in ('42 Chapter 7 General Principles', '18 Chapitre II Analyse',
                      '21 Kapitel 3 Grundlagen', '12 Capítulo 4 Conceptos', '9 Capitolo V Metodi'):
            raw = chapter_page(label)
            before = deepcopy(raw)
            for page in (projected(raw), PrimaryPages(True).append(raw)):
                self.assertEqual(page.layouts[-1].ownership, 'ignored')
                self.assertFalse(page.citations)
                self.assertEqual(page.layouts[-1].source.bbox, raw.footnotes_layouts[0].det)
                assert page.layouts[-1].source.origin is not None
                self.assertEqual(page.layouts[-1].source.origin.text, label)
            self.assertEqual(raw, before)

    def test_real_reference_typography_disables_the_inference(self):
        for marker in ('①', '¹', '<sup>a</sup>', '*', '†', r'\(^{a}\)', ' Another claim.1'):
            with self.subTest(marker=marker):
                raw = chapter_page()
                raw.body_layouts[0].text += marker
                self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')

    def test_mathematical_powers_are_not_bare_reference_marks(self):
        raw = chapter_page()
        raw.body_layouts[0].text += r' The expression is \(x^{2} + 1\).'
        self.assertEqual(projected(raw).layouts[-1].ownership, 'ignored')

    def test_an_in_body_chapter_title_or_short_margin_is_not_a_footer(self):
        for box in ((220, 950, 570, 970), (50, 710, 400, 730), (50, 650, 400, 670)):
            with self.subTest(box=box):
                raw = chapter_page()
                raw.footnotes_layouts[0].det = box
                self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')

    def test_other_note_content_or_a_low_asset_prevents_exclusion(self):
        raw = chapter_page()
        raw.footnotes_layouts.append(PageLayout('text', (200, 800, 900, 850),
                                                'A real note continuation.', 1, None))
        self.assertEqual(projected(raw).layouts[-2].ownership, 'citation')
        raw = chapter_page()
        raw.body_layouts.append(PageLayout('image', (200, 720, 900, 960), '', 2, None))
        self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')

    def test_syntax_alone_is_insufficient_on_sparse_pages(self):
        raw = chapter_page()
        raw.body_layouts = raw.body_layouts[:1]
        self.assertEqual(projected(raw).layouts[-1].ownership, 'citation')

    def test_years_prose_and_explicit_cross_references_do_not_match_the_grammar(self):
        for text in ('2024 Chapter 7 General Principles', '42 See Chapter 7 General Principles',
                     '42 Chapter 7 explains the following.', '42 General Principles',
                     '42 Chapter 7 This is an excessively long explanatory note title'):
            with self.subTest(text=text):
                self.assertEqual(projected(chapter_page(text)).layouts[-1].ownership, 'citation')
