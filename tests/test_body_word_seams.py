"""A visible numbered-item boundary cannot split an alphabetic word."""
from copy import deepcopy
from unittest import TestCase

from pdf_craft.extractor.chapter.local_review import local_review_requests
from tests.test_structural_review import state, text


def names(value, *, body=True):
    return {name for p in local_review_requests(value, concrete_joins=body, native_faults=body) for name in p['questions']}


class BodyWordSeamTests(TestCase):
    def test_indexed_item_skips_only_inapplicable_word_test(self):
        value = state([text(0, '(24) Work on the next example.')],
                      [text(0, 'The preceding statement is complete.')])
        before = deepcopy(value)
        questions = names(value)
        self.assertNotIn('gap_0_word', questions)
        self.assertIn('gap_0_syntax', questions)
        self.assertIn('owner_0', questions)
        self.assertEqual(value, before)
        self.assertIn('gap_0_word', names(value, body=False))
        legacy = {name for p in local_review_requests(value, concrete_joins=True) for name in p['questions']}
        self.assertIn('gap_0_word', legacy)

    def test_incoming_join_to_a_new_item_still_requires_review(self):
        value = state([text(0, '(24) A separate exercise.', joined=True)],
                      [text(0, 'The preceding exercise ends.')])
        self.assertIn('gap_0', names(value))

    def test_unlabelled_lexical_or_numeric_continuations_remain_reviewable(self):
        for left, right in [('inter', 'national travel'), ('14', '92 was a year'),
                            ('A comment', '(24)continued')]:
            value = state([text(0, right)], [text(0, left)])
            self.assertIn('gap_0_word', names(value))
