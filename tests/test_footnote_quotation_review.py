"""Quotation proofs repair only a matching, incorrectly rejected paragraph break."""
import unittest
from copy import deepcopy
from tests.test_footnote_review import source, state
from pdf_craft.extractor.chapter.footnote_review import (
    supplementary_footnote_requests, footnote_pass_scores,
)


class FootnoteQuotationTests(unittest.TestCase):
    def test_speech_break_at_footnote_working_point_gets_one_bounded_proof(self):
        value = state(source(0, 'The speaker said:'), source(1, '"We should leave."'))
        before = deepcopy(value)
        for score in (.54, .55):
            with self.subTest(score=score):
                packets = list(supplementary_footnote_requests(value,
                    dict(gap_1_word=.99, gap_1_syntax=score)))
                proofs = [p for _, p in packets if 'quotation_boundary_1' in p['questions']]
                self.assertEqual(len(proofs), 1)
                self.assertTrue(all(len(p['questions']) <= 4 for _, p in packets))
                self.assertEqual(proofs[0]['state']['checks']['quotation_boundary_1']['source_order'], 1)
        self.assertEqual(value, before)

    def test_proof_uses_this_modes_work_point_and_preserves_other_faults(self):
        passed = footnote_pass_scores(dict(gap_1_word=.99, gap_1_syntax=.54,
            quotation_boundary_1=.91, gap_2_syntax=.3, reference_3=.2))
        self.assertAlmostEqual(passed['gap_1_syntax'], .91)
        self.assertEqual(passed['gap_1_word'], .99)
        self.assertEqual(passed['gap_2_syntax'], .3)
        self.assertEqual(passed['reference_3'], .2)
        self.assertNotIn('quotation_boundary_1', passed)

    def test_weak_or_unmatched_proof_cannot_fail_a_page_or_clear_another_gap(self):
        original = dict(gap_1_syntax=.7, gap_2_word=.3)
        passed = footnote_pass_scores(dict(original, quotation_boundary_1=.1,
                                          quotation_boundary_3=.99))
        self.assertEqual(passed, original)

    def test_unquoted_recitation_is_a_question_not_a_colon_based_acceptance(self):
        value = state(source(0, 'They began to recite:'), source(1, 'Across the river we go.'))
        packets = list(supplementary_footnote_requests(value,
            dict(gap_1_word=.99, gap_1_syntax=.5)))
        proof = next(p for _, p in packets if 'quotation_boundary_1' in p['questions'])
        instructions = ' '.join(proof['questions']['quotation_boundary_1']['instructions'])
        self.assertIn('mathematical expression', instructions)
        self.assertIn('not proof', instructions)
        self.assertEqual(footnote_pass_scores(dict(gap_1_syntax=.5,
                         quotation_boundary_1=.1))['gap_1_syntax'], .5)

    def test_existing_join_and_unfinished_word_are_not_admitted(self):
        joined = source(1, '"A complete utterance."')
        joined['continues_from_previous'] = True
        values = [state(source(0, 'He said:'), joined),
                  state(source(0, '"We travel inter'), source(1, 'nationally."'))]
        for value in values:
            names = {name for _, p in supplementary_footnote_requests(value,
                     dict(gap_1_word=.2, gap_1_syntax=.4)) for name in p['questions']}
            self.assertNotIn('quotation_boundary_1', names)

    def test_same_gap_split_across_physical_batches_has_only_one_proof(self):
        layouts = [source(i, 'He said:' if i % 2 == 0 else '"We should leave."')
                   for i in range(8)]
        scores = {f'gap_{i}_{part}': .5 for i in range(1, 8, 2)
                  for part in ('word', 'syntax')}
        names = [name for _, packet in supplementary_footnote_requests(state(*layouts), scores)
                 for name in packet['questions'] if name.startswith('quotation_boundary_')]
        self.assertEqual(sorted(names), [f'quotation_boundary_{i}' for i in range(1, 8, 2)])


class MissingQuotationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_quotation_batch_does_not_publish_partial_verdict(self):
        from pdf_craft.extractor.chapter.footnote_review import review_footnote_page
        value = state(source(0, 'He said:'), source(1, '"We should leave."'))
        seen = []

        async def evaluate(requests):
            rows = []
            for index, packet in requests:
                if any(n.startswith('quotation_boundary_') for n in packet['questions']):
                    seen.append(packet)
                    continue
                rows.append((index, {n: .54 if n == 'gap_1_syntax'
                    else .99 if n.startswith('gap_') else .01 for n in packet['questions']}))
            return rows

        self.assertIsNone(await review_footnote_page(value, evaluate))
        self.assertEqual(len(seen), 1)
