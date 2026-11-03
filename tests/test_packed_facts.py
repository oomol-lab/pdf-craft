"""Deterministic proof must stay source-bound and leave uncertain checks live."""
from copy import deepcopy
import unittest

from pdf_craft.extractor.chapter.packed_facts import (
    reference_check_applies, apply_new_note_boundaries,
)


class PackedFactsTests(unittest.TestCase):
    def test_absent_mapping_without_a_possible_printed_target_is_not_a_question(self):
        state = {'target_page': {'layouts': [{'source_order': 1, 'raw_text': 'Unmapped marker⁵.'}]}}
        check = {'source_order': 1, 'references': [], 'literal_edits': [],
                 'target_definitions': [{'mark': '4'}]}
        original = deepcopy(state)
        self.assertFalse(reference_check_applies(state, check))
        self.assertEqual(state, original)
        for raw in ('Genuine marker⁴.', 'Potential quantity14.', r'Escaped \(^{4}\)',
                    'Entity&#52;', 'Other numeral٤.'):
            state['target_page']['layouts'][0]['raw_text'] = raw
            self.assertTrue(reference_check_applies(state, check), raw)

    def test_existing_mapping_edits_and_unsupported_marks_remain_reviewable(self):
        state = {'target_page': {'layouts': [{'source_order': 1, 'raw_text': 'Plain source.'}]}}
        base = {'source_order': 1, 'references': [], 'literal_edits': [], 'target_definitions': []}
        self.assertFalse(reference_check_applies(state, base))
        for override in ({'references': [{'citation_index': 1}]},
                         {'literal_edits': [{'original': '4', 'analysed': 'REF'}]},
                         {'target_definitions': [{'mark': '*'}]},
                         {'target_definitions': [{'mark': None}]}):
            self.assertTrue(reference_check_applies(state, dict(base, **override)))

    def test_new_definition_supports_only_its_first_explicitly_marked_break(self):
        definition = {'citation_id': 'a', 'index': 1, 'mark': '2'}
        first = {'source_order': 0, 'ownership': 'citation', 'citation_id': 'a',
                 'citation_index': 1, 'raw_text': '2. A new note.', 'continues_from_previous': False}
        later = dict(first, source_order=1, raw_text='2. A later numbered paragraph.')
        state = {'target_page': {'layouts': [first, later], 'citations': [definition]}}
        values = {'note_gap_0': .5, 'note_gap_1': .4, 'definition_0': .9,
                  'main_reading_0': .95, 'furniture_0': .98}
        original = deepcopy(state)
        result = apply_new_note_boundaries(state, values)
        self.assertEqual(result['note_gap_0'], .9)
        self.assertEqual(result['note_gap_1'], .4)
        self.assertEqual(values['note_gap_0'], .5)
        self.assertEqual(state, original)
        for override in ({'definition_0': .2}, {'main_reading_0': .2}):
            self.assertEqual(apply_new_note_boundaries(state, dict(values, **override))['note_gap_0'], .5)
        first['raw_text'] = '3. A different mark.'
        self.assertEqual(apply_new_note_boundaries(state, values)['note_gap_0'], .5)
        first['raw_text'], first['continues_from_previous'] = '2. A new note.', True
        self.assertEqual(apply_new_note_boundaries(state, values)['note_gap_0'], .5)
