"""A grammatical completion must use the real predecessor, never a convenient one."""
from copy import deepcopy
import unittest
from tests.test_footnote_review import state, source
from pdf_craft.extractor.chapter.footnote_review import supplementary_footnote_requests, footnote_pass_scores, review_footnote_page


class RequiredJoinCompletionTests(unittest.TestCase):
    def test_existing_rejected_join_offers_two_independent_facts(self):
        target = source(1, '村庄。')
        target['continues_from_previous'] = True
        value = state(source(0, '他看到了一个安静的'), target)
        before = deepcopy(value)
        packets = [p for _, p in supplementary_footnote_requests(value, {'gap_1': .4})
                   if 'dependency_completion_1' in p['questions']]
        self.assertEqual(len(packets), 1)
        self.assertIn('dependency_predecessor_1', packets[0]['questions'])
        self.assertEqual(value, before)

    def test_neither_fact_alone_can_override_any_other_error(self):
        for predecessor, completion, expected in ((.9, .1, .4), (.1, .9, .4), (.9, .85, .85)):
            result = footnote_pass_scores(dict(gap_1=.4, gap_2=.2, reference_1=.1,
                dependency_predecessor_1=predecessor, dependency_completion_1=completion))
            self.assertEqual(result, dict(gap_1=expected, gap_2=.2, reference_1=.1))

    def test_passed_join_and_break_do_not_add_a_completion_probe(self):
        for joined, score in ((False, .1), (True, .95)):
            target = source(1, '村庄。')
            target['continues_from_previous'] = joined
            names = [k for _, p in supplementary_footnote_requests(
                state(source(0, '他看到了一个安静的'), target), {'gap_1': score}) for k in p['questions']]
            self.assertFalse(any(k.startswith('dependency_') for k in names))


class MissingDependencyProofTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_completion_pair_cannot_publish_a_pass(self):
        target = source(1, '村庄。')
        target['continues_from_previous'] = True
        async def evaluate(requests):
            return [(index, {key: .4 if key == 'gap_1' else .99
                             for key in packet['questions'] if key != 'dependency_completion_1'})
                    for index, packet in requests]
        with self.assertRaisesRegex(ValueError, 'named scores'):
            await review_footnote_page(state(source(0, '他看到了一个安静的'), target), evaluate)

    async def test_missing_completion_batch_returns_no_verdict(self):
        target = source(1, '村庄。')
        target['continues_from_previous'] = True
        seen = []
        async def evaluate(requests):
            rows = []
            for index, packet in requests:
                if 'dependency_completion_1' in packet['questions']:
                    seen.append(packet)
                    continue
                rows.append((index, {key: .4 if key == 'gap_1' else .99
                                     for key in packet['questions']}))
            return rows
        self.assertIsNone(await review_footnote_page(
            state(source(0, '他看到了一个安静的'), target), evaluate))
        self.assertEqual(len(seen), 1)
