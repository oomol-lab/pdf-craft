"""Literal reference evidence stays bounded and never rewrites OCR sources."""
from copy import deepcopy
from typing import Any
import unittest

from pdf_craft.extractor.chapter.review_encoding import literal_reference_edits
from pdf_craft.extractor.chapter.footnote_review import footnote_review_requests, review_footnote_page
from tests.test_footnote_review import source, state


class ReferenceEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_literal_replacement_and_displacement_are_distinguishable(self):
        raw = 'A marked sentence① ends.'
        correct = literal_reference_edits(raw, 'A marked sentence⟦REF⟧ ends.')
        assert correct is not None
        self.assertEqual([(e['original'], e['analysed']) for e in correct], [('①', '⟦REF⟧')])
        moved = literal_reference_edits(raw, '⟦REF⟧A marked sentence ends.')
        assert moved is not None
        self.assertIn(('', '⟦REF⟧'), [(e['original'], e['analysed']) for e in moved])
        self.assertIn(('①', ''), [(e['original'], e['analysed']) for e in moved])

    def test_long_unchanged_text_and_local_edit_do_not_need_full_diff(self):
        prefix, suffix = 'a' * 100_000, 'b' * 100_000
        self.assertEqual(literal_reference_edits(prefix, prefix), [])
        edits = literal_reference_edits(prefix + '①' + suffix, prefix + '⟦REF⟧' + suffix)
        assert edits is not None
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]['original'], '①')
        self.assertLess(len(str(edits)), 200)
        self.assertIsNone(literal_reference_edits('a' * 5000, 'b' * 5000))

    def test_unrepresentable_diff_does_not_create_a_position_verdict(self):
        value = self.marked_state()
        layout = value['target_page']['layouts'][0]
        layout['raw_text'] = 'a' * 5000
        layout['analysed_text'] = 'b' * 5000 + '⟦REF⟧'
        names = {n for _, p in footnote_review_requests(value) for n in p['questions']}
        self.assertIn('reference_0', names)
        self.assertNotIn('reference_position_0', names)

    def test_punctuation_only_note_does_not_make_all_body_sources_reference_checks(self):
        value = state(source(0, 'A body sentence.'), source(1, '.', 'citation'))
        value['target_page']['citations'] = [dict(citation_id='unresolved', index=None, mark=None)]
        names = {n for _, p in footnote_review_requests(value) for n in p['questions']}
        self.assertFalse(any(n.startswith('reference_') for n in names))
        value['target_page']['layouts'][0]['references'] = [dict(citation_page=2, citation_index=1, path=[1])]
        names = {n for _, p in footnote_review_requests(value) for n in p['questions']}
        self.assertIn('reference_0', names)  # A dangling existing reference still needs review.

    def test_reference_evidence_keeps_source_and_distinct_printed_mark(self):
        value = self.marked_state()
        before = deepcopy(value)
        packets = list(footnote_review_requests(value))
        packet = next(p for _, p in packets if 'reference_position_0' in p['questions'])
        check = packet['state']['checks']['reference_0']
        self.assertEqual(check['references'][0]['citation_index'], 1)
        self.assertEqual(check['target_definitions'][0]['mark'], '①')
        self.assertEqual(value, before)
        self.assertTrue(all(len(p['questions']) <= 4 for _, p in packets))

    async def test_missing_position_response_is_not_a_partial_pass(self):
        async def evaluate(requests):
            return [(i, {n: .99 if n.startswith(('gap_', 'reference_')) else .01
                         for n in p['questions'] if not n.startswith('reference_position_')})
                    for i, p in requests]
        with self.assertRaisesRegex(ValueError, 'named scores'):
            await review_footnote_page(self.marked_state(), evaluate)

    @staticmethod
    def marked_state():
        body: dict[str, Any] = source(0, 'A marked sentence① ends.')
        body['analysed_text'] = 'A marked sentence⟦REF⟧ ends.'
        body['references'] = [dict(path=[1], citation_page=2, citation_index=1)]
        value = state(body, source(1, '① A footnote.', 'citation'))
        value['target_page']['citations'] = [dict(citation_id='unresolved', index=1, mark='①')]
        return value
