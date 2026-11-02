"""A word match cannot authorize choosing an older convenient predecessor."""
from copy import deepcopy
import unittest
from tests.test_footnote_review import source, state
from pdf_craft.extractor.chapter.footnote_review import (
    supplementary_footnote_requests, footnote_pass_scores, review_footnote_page,
)


class PairedWordSeamTests(unittest.TestCase):
    def test_two_independent_facts_share_immutable_literal_evidence(self):
        current = source(1, '節,再繼續。')
        current['continues_from_previous'] = True
        value = state(source(0, '他仔細檢查了每一個細'), current)
        before = deepcopy(value)
        packet = next(p for _, p in supplementary_footnote_requests(value, dict(gap_1=.5))
                      if 'seam_word_1' in p['questions'])
        self.assertIn('seam_predecessor_1', packet['questions'])
        self.assertEqual(packet['state']['checks']['seam_word_1']['short_current_prefix'], '節')
        self.assertEqual(value, before)

    def test_both_facts_are_required_and_only_the_same_join_can_change(self):
        for prior, word, expected in ((.99, .1, .4), (.1, .99, .4), (.9, .95, .9)):
            passed = footnote_pass_scores(dict(gap_1=.4, gap_2=.2, reference_1=.1,
                seam_predecessor_1=prior, seam_word_1=word))
            self.assertEqual(passed, dict(gap_1=expected, gap_2=.2, reference_1=.1))

    def test_selector_cannot_search_back_for_a_matching_word_across_a_retained_caption(self):
        current = source(2, '節,尚未完。')
        current['continues_from_previous'] = True
        value = state(source(0, '他仔細檢查了每一個細'), source(1, '一個獨立的圖片署名'), current)
        packet = next(p for _, p in supplementary_footnote_requests(value, dict(gap_2=.5))
                      if 'seam_word_2' in p['questions'])
        self.assertEqual(packet['state']['checks']['seam_word_2']['proposed_left_source'],
                         dict(page_index=2, source_order=1))

    def test_existing_good_join_and_new_paragraph_do_not_request_word_proofs(self):
        current = source(1, '節,再繼續。')
        for joined, score in ((True, .99), (False, .1)):
            current['continues_from_previous'] = joined
            names = [n for _, p in supplementary_footnote_requests(
                state(source(0, '每一個細'), current), dict(gap_1=score)) for n in p['questions']]
            self.assertFalse(any(n.startswith('seam_') for n in names))


class MissingPairedWordTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_pair_does_not_publish_a_partial_verdict(self):
        current = source(1, '節,再繼續。')
        current['continues_from_previous'] = True
        seen = []

        async def evaluate(requests):
            rows = []
            for index, packet in requests:
                if 'seam_word_1' in packet['questions']:
                    seen.append(packet)
                    continue
                rows.append((index, {n: .5 if n.startswith('gap_') else .01
                                     for n in packet['questions']}))
            return rows

        self.assertIsNone(await review_footnote_page(state(source(0, '每一個細'), current), evaluate))
        self.assertEqual(len(seen), 1)
