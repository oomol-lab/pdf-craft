"""Unstyled OCR definitions need explicit body-reference evidence."""
import unittest
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree.ElementTree import fromstring

from pdf_craft.extractor.refinement import _project_window
from pdf_craft.pdf import decode
from pdf_craft.extractor.chapter.stream import resolve_to_disk


def project(body, note, enabled=True):
    raw = decode(fromstring('<page index="1"><body><layout ref="text" '
        f'det="0,0,100,30">{body}</layout></body><footnotes>'
        f'<layout ref="text" det="0,80,100,95">{note}</layout>'
        '</footnotes></page>'))
    return raw, _project_window([None, raw, None], enabled)[1]


class NumericFootnoteMarkTests(unittest.TestCase):
    def test_traditional_stream_uses_the_same_explicit_numeric_evidence(self):
        for body in ('A statement⁴.', r'A statement\(^{4}\).', 'A statement&lt;sup&gt;4&lt;/sup&gt;.'):
            with self.subTest(body=body), TemporaryDirectory() as directory:
                raw, _ = project(body, '4 An explanatory note.')
                emitted = []
                database = Path(directory) / 'stream.sqlite'
                resolve_to_disk(lambda raw=raw: iter([raw]), database, on_footnote=emitted.append)
                self.assertEqual(len(emitted), 1)
                connection = sqlite3.connect(database)
                try:
                    self.assertEqual(connection.execute('SELECT page,number FROM refs').fetchall(), [(1, 1)])
                finally:
                    connection.close()

    def test_explicit_body_superscripts_disambiguate_plain_note_heads(self):
        for body in ('A statement⁴.', r'A statement\(^{4}\).', 'A statement&lt;sup&gt;4&lt;/sup&gt;.'):
            for prefix in ('4 ', '4. ', '4) '):
                with self.subTest(body=body, prefix=prefix):
                    raw, page = project(body, prefix + 'An explanatory note.')
                    assert page is not None
                    self.assertEqual(page.citations[0].index, 1)
                    self.assertEqual(page.citations[0].mark, '4')
                    self.assertEqual(len(page.layouts[0].references), 1)
                    self.assertEqual(raw.footnotes_layouts[0].text, prefix + 'An explanatory note.')

    def test_multiple_superscript_digits_form_one_reference(self):
        _, page = project('A statement¹².', '12 An explanatory note.')
        assert page is not None
        self.assertEqual(page.citations[0].mark, '12')
        self.assertEqual(len(page.layouts[0].references), 1)

    def test_plain_body_numbers_and_math_constants_are_not_reference_evidence(self):
        for body in ('There are 4 options.', r'The value is \(4\).', r'It is \(x^{4}\).'):
            with self.subTest(body=body):
                _, page = project(body, '4 An explanation with an ambiguous opening.')
                assert page is not None
                self.assertIsNone(page.citations[0].index)
                self.assertFalse(page.layouts[0].references)

    def test_an_established_note_does_not_consume_a_plain_math_constant(self):
        _, page = project(r'The claim⁴ has value \(4\), namely 4.', '4 An explanatory note.')
        assert page is not None
        self.assertEqual(len(page.layouts[0].references), 1)
        self.assertTrue(any(getattr(part, 'content', None) == '4'
                            for part in page.layouts[0].source.content))

    def test_unmatched_or_isolated_numeric_sources_stay_unresolved(self):
        for note in ('5 A different note.', '4', '4.5 is a decimal value.', '2024 was an important year.'):
            with self.subTest(note=note):
                _, page = project('A statement⁴.', note)
                assert page is not None
                self.assertIsNone(page.citations[0].index)
                self.assertFalse(page.layouts[0].references)

    def test_disabled_footnotes_do_not_create_definitions_or_refs(self):
        _, page = project('A statement⁴.', '4 An explanatory note.', False)
        assert page is not None
        self.assertFalse(page.citations)
        self.assertFalse(any(layout.references for layout in page.layouts))
