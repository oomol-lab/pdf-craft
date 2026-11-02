"""A paired chapter footer and remote code do not form main reading content."""
from copy import deepcopy
import unittest
from pdf_craft.pdf import Page, PageLayout
from pdf_craft.extractor.chapter.primary import project_window, PrimaryPages
from pdf_craft.extractor.chapter.generation import _resolve_pages


def projected(raw, footnotes=True):
    page = project_window([raw], footnotes)[0]
    assert page is not None
    return page


def apparatus_page():
    return Page(1, None, [PageLayout('text', (100, 3000, 600, 3050),
        '24 Chapter 3 Printing settings', 0, None)],
        [PageLayout('text', (2000, 3005, 2120, 3050), 'ZZYY', 0, None)], 0, 0)


class ApparatusOnlyPageTests(unittest.TestCase):
    def test_both_streams_are_retained_as_ignored_without_a_paragraph(self):
        raw = apparatus_page()
        before = deepcopy(raw)
        for page in (projected(raw), PrimaryPages().append(raw)):
            assert page is not None
            self.assertFalse(page.citations)
            self.assertEqual([l.ownership for l in page.layouts], ['ignored', 'ignored'])
            self.assertTrue(all(l.source.origin is not None for l in page.layouts))
            self.assertEqual({l.source.origin.text for l in page.layouts if l.source.origin is not None},
                             {raw.body_layouts[0].text, raw.footnotes_layouts[0].text})
        paragraphs, notes = _resolve_pages([raw])
        self.assertFalse(paragraphs)
        self.assertFalse(notes)
        self.assertEqual(raw, before)

    def test_title_without_a_remote_matching_band_cannot_be_discarded(self):
        for box in ((610, 3005, 730, 3050), (2000, 2600, 2120, 2645)):
            raw = apparatus_page()
            raw.footnotes_layouts[0].det = box
            self.assertTrue(any(l.ownership == 'paragraph' for l in projected(raw).layouts))
        raw = apparatus_page()
        raw.footnotes_layouts = []
        self.assertTrue(projected(raw).layouts)
        raw = apparatus_page()
        raw.body_layouts[0].det = (100, 100, 600, 150)
        self.assertTrue(any(l.ownership == 'paragraph' for l in projected(raw).layouts))

    def test_other_reading_content_or_note_prose_prevents_the_hypothesis(self):
        for stream in ('body_layouts', 'footnotes_layouts'):
            raw = apparatus_page()
            getattr(raw, stream).append(PageLayout('text', (100, 2000, 900, 2200),
                                                   'Actual reading content.', 1, None))
            self.assertTrue(projected(raw).citations or
                            any(l.ownership == 'paragraph' for l in projected(raw).layouts))

    def test_disabled_footnotes_does_not_infer_from_unavailable_second_pass(self):
        raw = apparatus_page()
        raw.footnotes_layouts = []
        page = projected(raw, False)
        self.assertEqual(page.layouts[0].ownership, 'paragraph')
