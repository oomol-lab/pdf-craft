"""A separately printed numeric display may follow an unfinished introduction."""
from copy import deepcopy
import unittest
from tests.test_footnote_review import source, state
from pdf_craft.extractor.chapter.footnote_review import supplementary_footnote_requests, footnote_pass_scores


def display_state():
    before = source(0, 'The computation approaches the following limiting value')
    number = source(1, '1.23456...')
    after = source(2, 'This is a displayed result in the calculation.')
    before['bbox_normalized'] = [.1, .1, .9, .35]
    number['bbox_normalized'] = [.45, .4, .55, .44]
    after['bbox_normalized'] = [.1, .49, .7, .56]
    return state(before, number, after)


def probes(value):
    return [p for _, p in supplementary_footnote_requests(value, {'gap_1_syntax': .3})
            if 'displayed_number_1' in p['questions']]


class DisplayedNumberReviewTests(unittest.TestCase):
    def test_measured_display_geometry_is_exposed_without_altering_evidence(self):
        value = display_state()
        before = deepcopy(value)
        packets = probes(value)
        self.assertEqual(len(packets), 1)
        check = packets[0]['state']['checks']['displayed_number_1']
        self.assertEqual(check['measured_display_geometry']['target_bbox'], [.45, .4, .55, .44])
        self.assertEqual(value, before)

    def test_proof_only_addresses_the_same_syntax_break(self):
        result = footnote_pass_scores(dict(gap_1_syntax=.3, gap_1_word=.2, gap_2=.1,
                                            reference_1=.4, displayed_number_1=.9))
        self.assertEqual(result, dict(gap_1_syntax=.9, gap_1_word=.2, gap_2=.1, reference_1=.4))

    def test_ordinary_numeric_labels_and_inline_or_obstructed_placement_are_not_admitted(self):
        for text in ('2024', '1.', '3', 'The value is 1.23456', r'\(u = 12\) degrees'):
            value = display_state()
            value['target_page']['layouts'][1]['raw_text'] = text
            self.assertFalse(probes(value))
        for box in ([.1, .4, .2, .44], [.45, .34, .55, .38], [.45, .45, .55, .49]):
            value = display_state()
            value['target_page']['layouts'][1]['bbox_normalized'] = box
            self.assertFalse(probes(value))

    def test_missing_surrounding_body_or_an_existing_join_keeps_its_current_review(self):
        value = display_state()
        value['target_page']['layouts'][2]['ocr_stream'] = 'footnote'
        self.assertFalse(probes(value))
        value = display_state()
        value['target_page']['layouts'][1]['continues_from_previous'] = True
        self.assertFalse(probes(value))
