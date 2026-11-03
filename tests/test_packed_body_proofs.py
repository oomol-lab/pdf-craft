"""A collected proof must not be dropped by the old follow-up admission gate."""
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pdf_craft.extractor.chapter.body_review_facts import apply_body_facts
from pdf_craft.extractor.chapter.structural_review import aggregate_risks
from pdf_craft.extractor.chapter.packed_review import packed_review
from pdf_craft.extractor.chapter.review_templates import review_question


class PackedBodyProofTests(IsolatedAsyncioTestCase):
    def test_collected_word_proof_reduces_only_its_existing_source_fault(self):
        risks = {'gap_0_word': .4, 'owner_1': .8}
        before = deepcopy(risks)
        actual = aggregate_risks(risks, {'left_word_complete_0': .95,
                                       'left_word_complete_9': .99})
        self.assertAlmostEqual(actual['gap_0_word'], .05)
        self.assertEqual(actual['owner_1'], .8)
        self.assertNotIn('gap_9_word', actual)
        self.assertEqual(risks, before)

    def test_collected_frame_proof_still_requires_both_complete_and_main_reading(self):
        risks = {'gap_0_syntax': .4, 'owner_1': .8}
        proofs = {'closed_predecessor_frame_0': .95,
                  'closed_predecessor_main_reading_0': .98}
        actual = apply_body_facts(risks, proofs)
        self.assertAlmostEqual(actual['gap_0_syntax'], .05)
        self.assertEqual(actual['owner_1'], .8)
        weak = dict(proofs, closed_predecessor_main_reading_0=.1)
        self.assertEqual(apply_body_facts(risks, weak), risks)

    async def test_owner_fault_orientation_restores_public_pass_score(self):
        value = {'target_page': {'page_index': 2, 'layouts': []}}
        check = {'source_order': 0, 'ownership': 'paragraph'}
        base = {'state': {'checks': {'owner_0': check}},
                'questions': {'owner_0': review_question(
                    'body_base', kind='ownership_fault', order=0, key='owner_0', retained=True)}}
        calls = []

        async def evaluate(_page, packet):
            calls.append(packet)
            spec = packet['questions']['q0']
            self.assertIn('clearly belongs to an excluded footnote', spec['criteria']['true'])
            return {'q0': .9}

        with patch('pdf_craft.extractor.chapter.packed_review.local_review_requests',
                   return_value=iter([base])), patch(
                       'pdf_craft.extractor.chapter.packed_review._body_followups', return_value=iter([])):
            result = await packed_review(value, evaluate, includes_footnotes=False)
        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(result['scores']['owner_0'], .1)
        self.assertAlmostEqual(result['pass_probability'], .1)

    async def test_one_call_review_uses_a_proof_already_paid_for(self):
        state = {'target_page': {'page_index': 2, 'layouts': []}}
        check = {'source_order': 0, 'present_decision': False}
        base = {'state': {'checks': {'gap_0_word': check}},
                'questions': {'gap_0_word': review_question('body_base', kind='gap_word', name='gap_0_word', native_faults=True)}}
        proof = {'state': {'checks': {'left_word_complete_0': check}},
                 'questions': {'left_word_complete_0': review_question(
                     'body_followup', kind='left_word_complete', name='left_word_complete_0')}}
        calls = []

        def followups(_state, _checks, risks):
            if risks.get('gap_0_word', 0) >= .52:
                yield proof

        async def evaluate(_page, packet):
            calls.append(packet)
            self.assertEqual(len(packet['questions']), 2)
            return {'q0': .6, 'q1': .95}

        with patch('pdf_craft.extractor.chapter.packed_review.local_review_requests',
                   return_value=iter([base])), patch(
                       'pdf_craft.extractor.chapter.packed_review._body_followups', followups):
            result = await packed_review(state, evaluate, includes_footnotes=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['calls'], 1)
        self.assertAlmostEqual(result['pass_probability'], .95)
