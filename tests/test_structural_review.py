"""Named structural facts must stay local, complete and source-specific."""
import unittest
import asyncio
from typing import Any
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pdf_craft import ConcurrentExecutor, FixedCapacity, JEV
from pdf_craft.jev import JEVRuntime
from pdf_craft.extractor.chapter.local_review import local_review_requests
from pdf_craft.extractor.chapter.review_evidence import supplementary_requests
from pdf_craft.extractor.chapter.structural_review import aggregate_risks, review_body_page


def text(order, value, box=(.1, .1, .9, .2), *, joined=False, role='body'):
    return dict(document_order=order, source_order=order, source_type='text', raw_text=value,
                bbox_normalized=list(box), ownership='paragraph', ocr_stream='body',
                paragraph_role=role, continues_from_previous=joined)


def state(layouts, prior=None) -> dict[str, Any]:
    return dict(target_page=dict(page_index=2, layouts=layouts),
                previous_page=None if prior is None else dict(page_index=1, paragraph_boundary=prior),
                next_page=None)


def image_state():
    value = 'An old bridge crosses the river.'
    current = [dict(document_order=0, source_order=None, source_type='image', raw_text='',
                    bbox_normalized=[0, .1, 1, .98], ownership='paragraph'),
               text(1, value, (.1, .02, .9, .08), joined=True)]
    return state(current, [text(0, 'They stopped to rest. ' + value + ' Beyond it, the road climbed towards')])


class StructuralAggregationTests(unittest.TestCase):
    def test_proofs_never_erase_unrelated_faults(self):
        base = dict(gap_1=.8, owner_1=.9, gap_2=.7)
        risks = aggregate_risks(base, dict(joined_word_complete_1=.95,
            fresh_spoken_turn_1=.9, different_dialogue_speaker_1=.85))
        self.assertAlmostEqual(risks['gap_1'], .05)
        self.assertEqual(risks['owner_1'], .9)
        self.assertEqual(risks['gap_2'], .7)
        self.assertEqual(risks['distinct_dialogue_turn_1'], .85)
        self.assertEqual(base, dict(gap_1=.8, owner_1=.9, gap_2=.7))

    def test_complete_left_word_does_not_approve_its_sentence_frame(self):
        risks = aggregate_risks(dict(gap_1_word=.8, gap_1_syntax=.9), dict(left_word_complete_1=.95))
        self.assertAlmostEqual(risks['gap_1_word'], .05)
        self.assertEqual(risks['gap_1_syntax'], .9)

    def test_all_section_facts_must_clear_the_unrounded_working_point(self):
        base = dict(owner_1=.9)
        for value in (.5199999999999999, .51):
            self.assertEqual(aggregate_risks(base, dict(section_sequence_1=.99, section_member_1=value)), base)
        risks = aggregate_risks(base, dict(section_sequence_1=.52, section_member_1=.99))
        self.assertEqual(risks['owner_1'], .48)

    def test_new_caption_fault_requires_every_fact_and_does_not_use_average(self):
        risks = aggregate_risks(dict(owner_1=.1), dict(detached_image_strip_1=.99,
            prior_narrative_role_1=.98, selected_whole_passage_1=.2))
        self.assertEqual(risks['selected_image_strip_1'], .2)


class StructuralCandidateTests(unittest.TestCase):
    def test_equal_overlapping_copies_keep_the_first_representative(self):
        requests = list(local_review_requests(state([text(0, 'Same text.'), text(1, 'Same text.')])))
        owners = next(p for p in requests if 'owner_0' in p['questions'])
        extra = list(supplementary_requests(owners['state'], {}))
        names = {name for p in extra for name in p['questions']}
        self.assertIn('redundancy_1', names)
        self.assertNotIn('redundancy_0', names)

    def test_image_replay_rejects_terminal_only_match_as_a_candidate(self):
        value = image_state()
        value['previous_page']['paragraph_boundary'][0]['raw_text'] = 'An old bridge crosses the river.'
        packets = list(local_review_requests(value))
        extra = [p for base in packets for p in supplementary_requests(base['state'], {})]
        self.assertFalse(any('detached_image_strip_1' in p['questions'] for p in extra))
        self.assertTrue(any('image_caption_role_1' in p['questions'] for p in extra))

    def test_possible_mixed_section_withholds_exception_without_dropping_source(self):
        value = state([text(0, 'Main prose.', (.1, .1, .48, .65)),
                       text(1, '## NOTES', (.55, .1, .9, .12), role='heading'),
                       text(2, 'Main prose. 1. A reference.', (.1, .14, .9, .95))])
        before = deepcopy(value)
        packets = list(local_review_requests(value))
        names = {name for p in packets for extra in supplementary_requests(p['state'], {'owner_2': .9})
                 for name in extra['questions']}
        self.assertNotIn('section_sequence_2', names)
        self.assertNotIn('section_member_2', names)
        self.assertEqual(value, before)

    def test_ignored_note_formula_cannot_conceal_the_real_body_predecessor(self):
        value = state([text(0, 'esting example.', joined=True)], [text(0, 'An inter')])
        value['previous_page']['excluded_candidates'] = [dict(document_order=1, source_order=None,
            source_type='formula', raw_text='x=1', ownership='ignored', ocr_stream='footnote')]
        packets = list(local_review_requests(value))
        check = next(p['state']['checks']['gap_0'] for p in packets if 'gap_0' in p['questions'])
        self.assertEqual(check['preceding_source_candidates'], ['p1_s0'])
        self.assertEqual(check['immediate_paragraph_hint']['text_end'], 'An inter')

    def test_formulas_without_text_identity_reset_previous_page_candidates(self):
        formula = dict(document_order=1, source_order=None, source_type='formula', raw_text='x=1',
                       ownership='paragraph')
        packets = list(local_review_requests(state([text(0, 'A new paragraph.')],
                                                  [text(0, 'We can write'), formula])))
        self.assertEqual([name for p in packets for name in p['questions']], ['owner_0'])

    def test_expanded_decision_checks_are_isolated_across_packet_boundaries(self):
        value = state([text(0, 'continuation.', joined=True), text(1, 'First paragraph.'),
                       text(2, 'Second paragraph.')], [text(0, 'A preceding')])
        packets = list(local_review_requests(value))
        word = next(p['state']['checks']['gap_2_word'] for p in packets
                    if 'gap_2_word' in p['questions'])
        syntax = next(p['state']['checks']['gap_2_syntax'] for p in packets
                      if 'gap_2_syntax' in p['questions'])
        word['current_start'] = 'mutated by a consumer'
        self.assertEqual(syntax['current_start'], 'Second paragraph.')


class StructuralExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_only_facts_can_flag_a_page_the_base_questions_passed(self):
        value = image_state()
        before = deepcopy(value)
        calls = []
        async def evaluate(requests):
            rows = []
            for index, packet in requests:
                calls.append(packet)
                scores = {name: .99 if name.startswith(('gap_', 'owner_', 'redundancy_')) else .01
                          for name in packet['questions']}
                for name in packet['questions']:
                    if name.startswith(('detached_image_strip_', 'prior_narrative_role_', 'selected_whole_passage_')):
                        scores[name] = .9
                rows.append((index, scores))
            return rows
        probability = await review_body_page(((2, p) for p in local_review_requests(value)), evaluate)
        assert probability is not None
        self.assertAlmostEqual(probability, .1)
        self.assertEqual(value, before)
        self.assertTrue(all(len(p['questions']) <= 4 for p in calls))

    async def test_failed_supplement_never_becomes_a_base_only_pass(self):
        calls = 0
        async def evaluate(requests):
            nonlocal calls
            calls += 1
            rows = [(index, {name: .99 for name in p['questions']}) for index, p in requests]
            return rows if calls == 1 else rows[:-1]
        probability = await review_body_page(((2, p) for p in local_review_requests(image_state())), evaluate)
        self.assertIsNone(probability)
        self.assertEqual(calls, 2)

    async def test_empty_page_never_creates_a_fake_review(self):
        seen = []
        async def evaluate(requests):
            seen.extend(requests)
            return []
        self.assertIsNone(await review_body_page(iter(()), evaluate))
        self.assertEqual(seen, [])

    async def test_named_transport_retains_each_score_and_existing_float_contract(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.system_one = AsyncMock(return_value=SimpleNamespace(nouls={
            'first': SimpleNamespace(noul=.95), 'second': SimpleNamespace(noul=.2)}))
        request = dict(state={}, questions={'first': {'type': 'noul'}, 'second': {'type': 'noul'}})
        with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client):
            async with JEVRuntime(JEV('unused'), ConcurrentExecutor(FixedCapacity(1))) as runtime:
                self.assertEqual(await runtime.evaluate_questions_many([(2, request)]),
                                 [(2, {'first': .95, 'second': .2})])
                self.assertEqual(await runtime.evaluate(2, request), .2)
        self.assertEqual(client.system_one.await_count, 2)

    async def test_named_batches_share_capacity_and_cancel_all_owned_work(self):
        active = 0
        peak = 0
        entered = asyncio.Event()
        blocked = asyncio.Event()
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        async def respond(**_kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await blocked.wait()
            finally:
                active -= 1
        client.system_one = AsyncMock(side_effect=respond)
        packet = dict(state={}, questions={'decision': {'type': 'noul'}})
        with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client):
            async with JEVRuntime(JEV('unused'), ConcurrentExecutor(FixedCapacity(2))) as runtime:
                task = asyncio.create_task(runtime.evaluate_questions_many((2, packet) for _ in range(8)))
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    self.assertEqual(peak, 2)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
        self.assertEqual(active, 0)
        self.assertEqual(client.system_one.await_count, 2)
