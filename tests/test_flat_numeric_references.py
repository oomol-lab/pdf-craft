"""Recover flattened reference typography only from paired local evidence."""
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3

from tests.test_numeric_footnote_marks import project
from pdf_craft.extractor.chapter.stream import resolve_to_disk


class FlattenedNumericReferenceTests(unittest.TestCase):
    def test_one_sentence_end_marker_and_matching_note_are_resolved(self):
        for body, number in (('A statement ends.4 This sentence follows.', '4'),
                             ('An independent claim ends.7', '7'),
                             ('“This is a quoted statement.”1 But the discussion continues.', '1')):
            with self.subTest(body=body):
                raw, page = project(body, number + '. An explanatory note.')
                assert page is not None
                self.assertEqual(page.citations[0].index, 1)
                self.assertEqual(page.citations[0].mark, number)
                self.assertEqual(len(page.layouts[0].references), 1)
                self.assertEqual(raw.body_layouts[0].text, body)

    def test_decimals_equations_abbreviations_and_code_are_not_footnote_evidence(self):
        for body in ('The value is 3.4.', 'See Fig.4 and Eq.4 for details.',
                     'The release is version.4.', 'A token `statement.4` is literal code.',
                     'The value is \\(x^{4}\\).', 'An expression.4 lower case continuation.'):
            with self.subTest(body=body):
                _, page = project(body, '4. An ambiguous note candidate.')
                assert page is not None
                self.assertIsNone(page.citations[0].index)
                self.assertFalse(page.layouts[0].references)

    def test_ambiguous_repeated_flat_candidates_do_not_choose_a_location(self):
        _, page = project('A statement ends.4 Another statement ends.4', '4. An explanatory note.')
        assert page is not None
        self.assertFalse(page.layouts[0].references)

    def test_an_explicit_reference_does_not_justify_replacing_other_flat_numbers(self):
        _, page = project('A statement⁴ ends. Another statement ends.4', '4. An explanatory note.')
        assert page is not None
        self.assertEqual(len(page.layouts[0].references), 1)
        self.assertTrue(any(isinstance(part, str) and 'ends.4' in part
                            for part in page.layouts[0].source.content))

    def test_unmatched_definition_and_disabled_footnotes_do_not_rewrite_prose(self):
        for note, enabled in (('5. A different note.', True), ('4. An explanatory note.', False)):
            with self.subTest(note=note, enabled=enabled):
                raw, page = project('A statement ends.4', note, enabled)
                before = deepcopy(raw)
                assert page is not None
                self.assertFalse(page.layouts[0].references)
                self.assertEqual(raw, before)

    def test_traditional_stream_and_local_projection_share_recovery(self):
        raw, _ = project('A statement ends.4 This follows.', '4. An explanatory note.')
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'stream.sqlite'
            emitted = []
            resolve_to_disk(lambda: iter([raw]), database, on_footnote=emitted.append)
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(connection.execute('SELECT page,number FROM refs').fetchall(), [(1, 1)])
            finally:
                connection.close()
            self.assertEqual(len(emitted), 1)
