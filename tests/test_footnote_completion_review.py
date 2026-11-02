"""Completion proofs cannot remove unrelated structural risks."""
import unittest
from copy import deepcopy
from tests.test_footnote_review import source, state
from pdf_craft.extractor.chapter.footnote_review import (
    supplementary_footnote_requests, footnote_pass_scores, review_footnote_page,
)


class CompletionReviewTests(unittest.TestCase):
    def test_completed_left_quote_requests_separate_word_and_frame_proofs(self):
        value = state(source(0, 'The guide said, "Leave now!"'), source(1, 'The door opened.'))
        before = deepcopy(value)
        packets = list(supplementary_footnote_requests(value,
                            dict(gap_1_word=.55, gap_1_syntax=.54)))
        names = [name for _, packet in packets for name in packet['questions']]
        self.assertEqual(names.count('left_word_complete_1'), 1)
        self.assertEqual(names.count('left_frame_complete_1'), 1)
        self.assertEqual(value, before)
        self.assertTrue(all(len(p['questions']) <= 4 for _, p in packets))

    def test_proofs_only_relax_their_corresponding_test(self):
        passed = footnote_pass_scores(dict(gap_1_word=.2, gap_1_syntax=.3,
            gap_2_syntax=.4, reference_1=.1, left_frame_complete_1=.9))
        self.assertEqual(passed, dict(gap_1_word=.2, gap_1_syntax=.9,
                                     gap_2_syntax=.4, reference_1=.1))
        passed = footnote_pass_scores(dict(gap_1_word=.3, gap_1_syntax=.2,
                                          left_word_complete_1=.9))
        self.assertEqual(passed, dict(gap_1_word=.9, gap_1_syntax=.2))

    def test_interrupted_turn_is_not_forced_to_complete_its_abandoned_inner_sentence(self):
        value = state(source(0, '"I meant to say…"'), source(1, 'The door slammed.'))
        packets = list(supplementary_footnote_requests(value, dict(gap_1_syntax=.5)))
        spec = next(p['questions']['ended_utterance_boundary_1'] for _, p in packets
                    if 'ended_utterance_boundary_1' in p['questions'])
        self.assertIn('outer conditional', ' '.join(spec['instructions']))
        self.assertEqual(footnote_pass_scores(dict(gap_1_syntax=.5,
            ended_utterance_boundary_1=.1)), dict(gap_1_syntax=.5))

    def test_math_is_a_question_and_cannot_clear_a_word_split(self):
        value = state(source(0, r'Write \(u = a + b\);'), source(1, r'and \(v = a - b\).'))
        packets = list(supplementary_footnote_requests(value, dict(gap_1_syntax=.54)))
        self.assertTrue(any('math_unit_boundary_1' in p['questions'] for _, p in packets))
        passed = footnote_pass_scores(dict(gap_1_syntax=.54, gap_1_word=.2,
            math_unit_boundary_1=.9, math_unit_boundary_2=.99))
        self.assertEqual(passed, dict(gap_1_syntax=.9, gap_1_word=.2))

    def test_new_proofs_do_not_apply_to_an_existing_join_or_a_good_break(self):
        current = source(1, 'The door opened.')
        current['continues_from_previous'] = True
        for value, scores in (
            (state(source(0, 'He said, "Go!"'), current), dict(gap_1=.1)),
            (state(source(0, 'He said, "Go!"'), source(1, 'The door opened.')),
             dict(gap_1_word=.99, gap_1_syntax=.99)),
        ):
            names = [n for _, p in supplementary_footnote_requests(value, scores)
                     for n in p['questions']]
            self.assertFalse(any(n.startswith(('left_word_', 'left_frame_',
                                'ended_utterance_', 'math_unit_')) for n in names))

    def test_direct_reported_speech_join_gets_only_a_targeted_question(self):
        target = source(1, '"We are ready."')
        target['continues_from_previous'] = True
        value = state(source(0, 'The guide said:'), target)
        packets = list(supplementary_footnote_requests(value, dict(gap_1=.5)))
        self.assertTrue(any('reporting_join_1' in p['questions'] for _, p in packets))
        passed = footnote_pass_scores(dict(gap_1=.5, gap_2=.2, furniture_1=.9,
                                          reporting_join_1=.98))
        self.assertEqual(passed['gap_1'], .98)
        self.assertEqual(passed['gap_2'], .2)
        self.assertAlmostEqual(passed['furniture_1'], .1)
        self.assertNotIn('reporting_join_1', passed)

    def test_a_reporting_join_proof_cannot_approve_a_separate_break(self):
        self.assertEqual(footnote_pass_scores(dict(gap_1_syntax=.3,
                          reporting_join_1=.99)), dict(gap_1_syntax=.3))


    def test_lower_same_column_hint_cannot_prove_left_completion(self):
        prior = source(0, 'He said, "Leave!"')
        current = source(1, 'The gate opened.')
        prior['bbox_normalized'] = [.1, .8, .9, .9]
        current['bbox_normalized'] = [.1, .1, .9, .2]
        names = [n for _, p in supplementary_footnote_requests(state(prior, current),
                 dict(gap_1_word=.3, gap_1_syntax=.3)) for n in p['questions']]
        self.assertFalse(any(n.startswith(('left_word_', 'left_frame_')) for n in names))



class MissingCompletionReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_completion_response_cannot_publish_partial_verdict(self):
        value = state(source(0, 'The guide said, "Go!"'), source(1, 'The door opened.'))
        seen = []

        async def evaluate(requests):
            rows = []
            for index, packet in requests:
                if 'left_frame_complete_1' in packet['questions']:
                    seen.append(packet)
                    continue
                rows.append((index, {n: .54 if n == 'gap_1_syntax'
                    else .99 if n.startswith('gap_') else .01 for n in packet['questions']}))
            return rows

        self.assertIsNone(await review_footnote_page(value, evaluate))
        self.assertEqual(len(seen), 1)

    async def test_missing_math_proof_is_not_replaced_with_a_partial_pass(self):
        value = state(source(0, r'Write \(u = a + b\);'), source(1, r'and \(v = a - b\).'))
        seen = []

        async def evaluate(requests):
            rows = []
            for index, packet in requests:
                if 'math_unit_boundary_1' in packet['questions']:
                    seen.append(packet)
                    continue
                rows.append((index, {n: .54 if n == 'gap_1_syntax'
                    else .99 if n.startswith('gap_') else .01 for n in packet['questions']}))
            return rows

        self.assertIsNone(await review_footnote_page(value, evaluate))
        self.assertEqual(len(seen), 1)
