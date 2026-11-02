"""Printed definition syntax does not require a body reference to survive OCR."""
from copy import deepcopy
import unittest
from pdf_craft.pdf import Page, PageLayout
from pdf_craft.extractor.chapter.primary import project_window
from pdf_craft.extractor.chapter.reference import extract_head_mark


class UnpairedNoteDefinitionTests(unittest.TestCase):
    def test_punctuated_mark_opens_definition_without_a_body_reference(self):
        raw = Page(1, None, [PageLayout('text', (100, 100, 900, 500),
            'A complete main passage without any surviving reference.', 0, None)],
            [PageLayout('text', (100, 650, 900, 700), '4. An explanatory note.', 0, None),
             PageLayout('text', (100, 710, 900, 760), '(5) Another explanatory note.', 1, None)], 0, 0)
        before = deepcopy(raw)
        page = project_window([raw], True)[0]
        assert page is not None
        self.assertEqual([c.index for c in page.citations], [1, 2])
        self.assertEqual([c.mark for c in page.citations], ['4', '5'])
        notes = [l for l in page.layouts if l.ownership == 'citation']
        self.assertTrue(all(l.source.origin is not None for l in notes))
        self.assertEqual([l.source.origin.text for l in notes if l.source.origin is not None],
                         [l.text for l in raw.footnotes_layouts])
        self.assertEqual(raw, before)

    def test_ordinary_bare_numbers_dates_and_decimals_are_not_definition_syntax(self):
        for text in ('4 ordinary quantities', '2024. A year', '4.5 units', '(5)years', '0. Invalid'):
            with self.subTest(text=text):
                mark, content = extract_head_mark([text])
                self.assertIsNone(mark)
                self.assertEqual(content, [text])

    def test_bare_number_still_requires_a_matching_reference(self):
        self.assertEqual(extract_head_mark(['4 ordinary words'], numeric_marks={'4'}),
                         ('4', ['ordinary words']))
        for text in ('4. A note', '4) A note', '(4) A note'):
            self.assertEqual(extract_head_mark([text]), ('4', ['A note']))
