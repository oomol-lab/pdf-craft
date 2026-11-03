"""Body break checks use direct fault propositions and restore pass scores."""
from unittest import IsolatedAsyncioTestCase

from pdf_craft.extractor.chapter.local_review import local_review_requests
from pdf_craft.extractor.chapter.packed_review import packed_review
from tests.test_structural_review import state, text


class NativeBodyBreakTests(IsolatedAsyncioTestCase):
    def test_body_only_breaks_ask_for_concrete_faults(self):
        value = state([text(0, 'A fresh thought.')], [text(0, 'A completed thought.')])
        body = {name: spec for p in local_review_requests(value, concrete_joins=True, native_faults=True)
                for name, spec in p['questions'].items()}
        legacy = {name: spec for p in local_review_requests(value, concrete_joins=True)
                  for name, spec in p['questions'].items()}
        self.assertIn('split one lexical word', body['gap_0_word']['instructions'][0])
        self.assertIn('sever a mandatory', body['gap_0_syntax']['instructions'][0])
        self.assertNotEqual(body['gap_0_word']['criteria'], legacy['gap_0_word']['criteria'])
        self.assertNotEqual(body['gap_0_syntax']['criteria'], legacy['gap_0_syntax']['criteria'])

    async def test_native_break_and_ownership_scores_are_restored_together(self):
        value = state([text(0, 'A fresh thought.')], [text(0, 'A completed thought.')])
        calls = []

        async def evaluate(_page, packet):
            calls.append(packet)
            return dict.fromkeys(packet['questions'], .1)

        result = await packed_review(value, evaluate, includes_footnotes=False)
        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(result['scores']['gap_0_word'], .9)
        self.assertAlmostEqual(result['scores']['gap_0_syntax'], .9)
        self.assertAlmostEqual(result['scores']['owner_0'], .9)
        self.assertAlmostEqual(result['pass_probability'], .9)
