"""Experimental fault propositions must not alter existing pass consumers."""
from unittest import TestCase

from pdf_craft.extractor.chapter.local_review import local_review_requests
from tests.test_structural_review import state, text


class ReviewPolarityIsolationTests(TestCase):
    def test_existing_concrete_body_calls_remain_pass_oriented(self):
        value = state([text(0, 'A new thought.')], [text(0, 'A complete thought.')])
        specs = {name: spec for p in local_review_requests(value, concrete_joins=True)
                 for name, spec in p['questions'].items()}
        self.assertIn('Whole words are preserved', specs['gap_0_word']['criteria']['true'])
        self.assertIn('No unfinished mandatory', specs['gap_0_syntax']['criteria']['true'])
        self.assertIn('ownership is acceptable', specs['owner_0']['criteria']['true'])

    def test_native_faults_are_an_explicit_body_only_option(self):
        value = state([text(0, 'A new thought.')], [text(0, 'A complete thought.')])
        specs = {name: spec for p in local_review_requests(
            value, concrete_joins=True, native_faults=True) for name, spec in p['questions'].items()}
        self.assertIn('divides that word', specs['gap_0_word']['criteria']['true'])
        self.assertIn('severs that specific dependency', specs['gap_0_syntax']['criteria']['true'])
        self.assertIn('excluded footnote', specs['owner_0']['criteria']['true'])
        with self.assertRaises(ValueError):
            list(local_review_requests(value, native_faults=True))
