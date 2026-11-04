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

    def test_same_page_illustration_caption_is_checked_as_the_selected_predecessor(self):
        image = dict(text(-1, '', (.1, .1, .9, .68)), source_order=None, source_type='image')
        value = state([image, text(0, 'A credited illustration excerpt.', (.1, .7, .9, .78)),
                       text(1, 'An independent narrative starts.', (.1, .81, .9, .94), joined=True)])
        before = deepcopy(value)
        packets = [p for base in local_review_requests(value, concrete_joins=True)
                   for p in body_supplementary_requests(base['state'], {})]
        packet = next(p for p in packets if 'prior_illustration_unit_1' in p['questions'])
        check = packet['state']['checks']['prior_illustration_unit_1']
        self.assertEqual(check['selected_predecessor']['page_index'], 2)
        self.assertEqual(check['selected_predecessor']['source_order'], 0)
        self.assertTrue(check['measured_adjacency'][0]['source_is_below_image'])
        self.assertEqual(value, before)

    def test_unnumbered_condition_and_first_textual_consequence_get_direct_checks(self):
        value = state([text(0, 'If the rate is constant;'),
                       text(1, 'one integration gives the distance.')])
        packets = [p for base in local_review_requests(value, concrete_joins=True)
                   for p in body_supplementary_requests(base['state'], {})]
        names = {name for p in packets for name in p['questions']}
        self.assertIn('conditional_frame_open_1', names)
        self.assertIn('conditional_main_clause_1', names)

    def test_standalone_formula_keeps_its_hard_boundary_after_a_condition(self):
        formula = dict(text(1, 'z = q', (.2, .3, .8, .4)), source_order=None,
                       source_type='formula')
        value = state([text(0, 'If the rate is constant;'), formula,
                       text(2, 'one integration gives the distance.')])
        packets = [p for base in local_review_requests(value, concrete_joins=True)
                   for p in body_supplementary_requests(base['state'], {})]
        self.assertFalse(any(name.startswith('conditional_frame_open_')
                             for p in packets for name in p['questions']))

    def test_conditional_fault_needs_both_an_open_frame_and_its_actual_completion(self):
        base = {'gap_1_syntax': .1, 'owner_0': .8}
        facts = {'conditional_frame_open_1': .95, 'conditional_main_clause_1': .9}
        result = apply_body_facts(base, facts)
        self.assertEqual(result['split_conditional_clause_1'], .9)
        self.assertEqual(result['owner_0'], .8)
        weak = dict(facts, conditional_main_clause_1=.1)
        self.assertEqual(apply_body_facts(base, weak)['split_conditional_clause_1'], .1)

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


class ClosedFrameProofTests(unittest.TestCase):
    def test_closed_left_proof_affects_only_its_syntax_boundary(self):
        original = {'gap_1_word': .9, 'gap_1_syntax': .8, 'owner_1': .9, 'gap_2_syntax': .7}
        result = apply_body_facts(original, {'closed_predecessor_frame_1': .95, 'closed_predecessor_main_reading_1': .98})
        self.assertAlmostEqual(result['gap_1_syntax'], .05)
        self.assertEqual(result['gap_1_word'], .9)
        self.assertEqual(result['owner_1'], .9)
        self.assertEqual(result['gap_2_syntax'], .7)
        self.assertEqual(original['gap_1_syntax'], .8)

    def test_complete_misclassified_note_cannot_certify_a_main_paragraph_break(self):
        original = {'gap_1_syntax': .8}
        result = apply_body_facts(original, {'closed_predecessor_frame_1': .95,
                                           'closed_predecessor_main_reading_1': .1})
        self.assertEqual(result, original)

    def test_unconfirmed_left_closure_cannot_erase_a_missing_conditional_command(self):
        original = {'gap_1_syntax': .8}
        self.assertEqual(apply_body_facts(original, {'closed_predecessor_frame_1': .2}), original)

    def test_left_frame_question_is_bound_to_the_actual_predecessor(self):
        value = state([text(0, 'The previous sentence is complete.'),
                       text(1, 'A separate description.')])
        packets = [p for base in local_review_requests(value)
                   for p in body_supplementary_requests(base['state'], {'gap_1_syntax': .8})]
        packet = next(p for p in packets if 'closed_predecessor_frame_1' in p['questions'])
        self.assertEqual(packet['state']['checks']['closed_predecessor_frame_1']
                         ['selected_predecessor']['source_order'], 0)

    def test_neighbor_or_lower_printed_source_cannot_supply_a_closure_proof(self):
        cases = [state([text(0, 'A main continuation.')], [text(0, 'A completed note.')]),
                 state([text(0, 'A completed note.', (.1, .9, .9, .95)),
                        text(1, 'A main continuation.', (.1, .1, .9, .2))])]
        for value in cases:
            packets = [p for base in local_review_requests(value)
                       for p in body_supplementary_requests(base['state'],
                           {'gap_0_syntax': .9, 'gap_1_syntax': .9})]
            self.assertFalse(any(k.startswith('closed_predecessor_frame_')
                                 for p in packets for k in p['questions']))


class NonreadingTraceTests(unittest.TestCase):
    def test_punctuation_only_page_gets_an_independent_trace_question(self):
        value = state([text(0, '.', (.01, .01, .99, .99), joined=True)])
        packets = [p for base in local_review_requests(value)
                   for p in body_supplementary_requests(base['state'], {})]
        self.assertTrue(any('nonreading_page_trace_0' in p['questions'] for p in packets))
        result = apply_body_facts({'owner_0': .1, 'gap_0': .1}, {'nonreading_page_trace_0': .9})
        self.assertEqual(result['nonreading_page_trace_0'], .9)

    def test_ordinary_text_on_page_withholds_the_trace_shortcut(self):
        value = state([text(0, '.'), text(1, 'An ordinary paragraph.')])
        packets = [p for base in local_review_requests(value)
                   for p in body_supplementary_requests(base['state'], {})]
        self.assertFalse(any(k.startswith('nonreading_page_trace_') for p in packets for k in p['questions']))


class ConcreteBodySourceTests(unittest.TestCase):
    def test_join_binds_the_actual_source_even_when_it_is_a_misclassified_note(self):
        value = state([text(0, 'the rest of the main clause.', joined=True)],
            [text(0, 'An unfinished main clause'), text(1, '1. A detached explanatory note.')])
        packets = list(local_review_requests(value, concrete_joins=True))
        packet = next(p for p in packets if 'gap_0' in p['questions'])
        prior = packet['state']['checks']['gap_0']['selected_predecessor']
        self.assertEqual(prior['source_order'], 1)
        self.assertEqual(prior['page_index'], 1)
        self.assertNotIn('若最近一项是注释、页码或页眉，应跳过它', str(packet['questions']))

    def test_apparatus_fact_can_reject_an_accepted_ownership(self):
        value = state([text(0, '37 Chapter 4 Operating the device', (.05, .94, .4, .96))])
        original = deepcopy(value)
        packets = [p for base in local_review_requests(value)
                   for p in body_supplementary_requests(base['state'], {})]
        packet = next(p for p in packets if 'publisher_apparatus_0' in p['questions'])
        self.assertEqual(packet['state']['checks']['publisher_apparatus_0']['source']['raw_text'],
                         value['target_page']['layouts'][0]['raw_text'])
        self.assertNotIn('ownership', packet['state']['checks']['publisher_apparatus_0']['source'])
        self.assertEqual(value, original)
        result = apply_body_facts({'owner_0': .05, 'gap_1': .9}, {'publisher_apparatus_0': .94})
        self.assertEqual(result['publisher_apparatus_0'], .94)
        self.assertEqual(result['gap_1'], .9)

    def test_middle_page_prose_and_already_excluded_footer_need_no_apparatus_fact(self):
        for excluded in (False, True):
            value = state([text(0, 'A real paragraph.' if not excluded else '37 Chapter 4',
                                (.1, .4, .8, .6) if not excluded else (.05, .94, .4, .96))])
            if excluded:
                value['target_page']['layouts'][0]['ownership'] = 'ignored'
            packets = [p for base in local_review_requests(value)
                       for p in body_supplementary_requests(base['state'], {})]
            self.assertFalse(any('publisher_apparatus_0' in p['questions'] for p in packets))


class BodyReviewFactRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_apparatus_verdict_is_independent_of_base_ownership_acceptance(self):
        value = state([text(0, '37 Chapter 4 Operating the device', (.05, .94, .4, .96))])
        async def evaluate(requests):
            return [(index, {name: .9 for name in packet['questions']}) for index, packet in requests]
        probability = await review_body_page(((2, p) for p in local_review_requests(value)), evaluate)
        assert probability is not None
        self.assertAlmostEqual(probability, .1)

    async def test_missing_apparatus_fact_is_an_incomplete_review(self):
        value = state([text(0, '37 Chapter 4 Operating the device', (.05, .94, .4, .96))])
        async def evaluate(requests):
            rows = list(requests)
            if any('publisher_apparatus_0' in packet['questions'] for _, packet in rows):
                return []
            return [(index, {name: .9 for name in packet['questions']}) for index, packet in rows]
        probability = await review_body_page(((2, p) for p in local_review_requests(value)), evaluate)
        self.assertIsNone(probability)

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
