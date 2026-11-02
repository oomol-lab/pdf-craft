"""Body-only proofs check the selected edge without changing neighbor decisions."""
import unittest
from copy import deepcopy

from pdf_craft.extractor.chapter.body_review_facts import body_supplementary_requests, apply_body_facts
from pdf_craft.extractor.chapter.local_review import local_review_requests
from pdf_craft.extractor.chapter.structural_review import review_body_page
from tests.test_structural_review import text, state


def illustration_join():
    value = state([text(0, 'The story continues.', joined=True)],
                  [text(0, 'An excerpt with a credit.', (.1, .8, .9, .88))])
    value['previous_page']['assets'] = [dict(document_order=-1, source_order=None, source_type='image', raw_text='', bbox_normalized=[0, .1, 1, .78])]
    return value


class BodyReviewFactTests(unittest.TestCase):
    def test_illustration_checks_bind_the_actual_selected_predecessor(self):
        value = illustration_join()
        value['previous_page']['paragraph_boundary'].append(text(1, 'Nearest actual predecessor.'))
        before = deepcopy(value)
        packets = [p for base in local_review_requests(value)
                   for p in body_supplementary_requests(base['state'], {})]
        selected = [p for p in packets if 'prior_illustration_unit_0' in p['questions']]
        self.assertEqual(len(selected), 1)
        check = selected[0]['state']['checks']['prior_illustration_unit_0']
        self.assertEqual(check['selected_predecessor']['source_order'], 1)
        self.assertEqual(check['selected_predecessor']['raw_text'], 'Nearest actual predecessor.')
        self.assertIn('measured_adjacency', check)
        self.assertEqual(value, before)

    def test_role_questions_hide_hypotheses_but_retain_every_original_source(self):
        value = illustration_join()
        original = deepcopy(value)
        packets = [p for base in local_review_requests(value)
                   for p in body_supplementary_requests(base['state'], {})]
        packet = next(p for p in packets if 'prior_illustration_unit_0' in p['questions'])
        evidence = packet['state']['local_raw_evidence']
        self.assertEqual(len(evidence['previous_page']['layouts']), 2)
        self.assertEqual(evidence['target_page']['layouts'][0]['raw_text'], 'The story continues.')
        for page in (evidence['target_page'], evidence['previous_page']):
            self.assertTrue(all('ownership' not in x and 'ocr_stream' not in x for x in page['layouts']))
        self.assertNotIn('preceding_source_index', packet['state'])
        self.assertEqual(value, original)

    def test_same_page_predecessor_does_not_trigger_cross_page_illustration_checks(self):
        value = illustration_join()
        value['target_page']['layouts'].insert(0, text(1, 'A nearer same-page paragraph.'))
        packets = [p for base in local_review_requests(value)
                   for p in body_supplementary_requests(base['state'], {})]
        self.assertFalse(any('prior_illustration_unit_0' in p['questions'] for p in packets))

    def test_independent_illustration_fault_cannot_be_erased_by_a_word_proof(self):
        result = apply_body_facts({'gap_0': .05, 'owner_1': .9},
            {'prior_illustration_unit_0': .92, 'target_leaves_illustration_0': .84})
        self.assertEqual(result['joined_illustration_switch_0'], .84)
        self.assertEqual(result['owner_1'], .9)

    def test_two_facts_must_both_support_the_illustration_fault(self):
        result = apply_body_facts({'gap_0': .1},
            {'prior_illustration_unit_0': .95, 'target_leaves_illustration_0': .1})
        self.assertEqual(result['joined_illustration_switch_0'], .1)

    def test_example_proof_affects_only_its_join_and_preserves_other_faults(self):
        result = apply_body_facts({'gap_0': .8, 'gap_1': .7, 'owner_0': .9},
                                  {'same_example_setup_0': .95})
        self.assertAlmostEqual(result['gap_0'], .05)
        self.assertEqual(result['gap_1'], .7)
        self.assertEqual(result['owner_0'], .9)

    def test_example_proof_requires_an_actual_example_introduction(self):
        for prior, admitted in [('A mathematical example.', True), ('An unrelated theorem.', False)]:
            value = state([text(0, 'Let z equal a constant.', joined=True)], [text(0, prior)])
            packets = [p for base in local_review_requests(value)
                       for p in body_supplementary_requests(base['state'], {'gap_0': .9})]
            self.assertEqual(any('same_example_setup_0' in p['questions'] for p in packets), admitted)


    def test_numbered_condition_and_its_command_are_checked_independently(self):
        value = state([text(0, 'find the value of q.')], [text(0, '(7) If q + r = 12,')])
        packets = [p for base in local_review_requests(value)
                   for p in body_supplementary_requests(base['state'], {})]
        names = {k for p in packets for k in p['questions']}
        self.assertIn('one_numbered_instruction_0', names)
        self.assertIn('conditional_command_required_0', names)

    def test_displayed_rows_and_new_items_do_not_create_instruction_fault_candidates(self):
        for prior, target in [('If the function equals q;', 'once differentiating gives r;'),
                              ('(7) If q + r = 12,', '(8) Find the value of q.')]:
            value = state([text(0, target)], [text(0, prior)])
            packets = [p for base in local_review_requests(value)
                       for p in body_supplementary_requests(base['state'], {})]
            self.assertFalse(any('one_numbered_instruction_0' in p['questions'] for p in packets))

    def test_numbered_instruction_fault_requires_both_facts(self):
        result = apply_body_facts({'gap_0_syntax': .1},
            {'one_numbered_instruction_0': .95, 'conditional_command_required_0': .2})
        self.assertEqual(result['split_numbered_instruction_0'], .2)


class BodyReviewFactRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepted_base_still_checks_the_concrete_illustration_edge(self):
        async def evaluate(requests):
            return [(index, {name: .9 for name in packet['questions']}) for index, packet in requests]
        probability = await review_body_page(
            ((2, packet) for packet in local_review_requests(illustration_join())), evaluate)
        assert probability is not None
        self.assertAlmostEqual(probability, .1)

    async def test_accepted_base_still_checks_explicit_conditional_instruction(self):
        value = state([text(0, 'find the value of q.')], [text(0, '(7) If q + r = 12,')])
        async def evaluate(requests):
            return [(index, {name: .9 for name in packet['questions']}) for index, packet in requests]
        probability = await review_body_page(((2, p) for p in local_review_requests(value)), evaluate)
        assert probability is not None
        self.assertAlmostEqual(probability, .1)

    async def test_missing_illustration_response_cannot_publish_a_partial_pass(self):
        async def evaluate(requests):
            rows = list(requests)
            if any('prior_illustration_unit_0' in packet['questions'] for _, packet in rows):
                return []
            return [(index, {name: .9 for name in packet['questions']}) for index, packet in rows]
        probability = await review_body_page(
            ((2, packet) for packet in local_review_requests(illustration_join())), evaluate)
        self.assertIsNone(probability)
