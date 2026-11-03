"""A native fault proposition should not carry a second polarity wrapper."""
from unittest import IsolatedAsyncioTestCase

from pdf_craft.extractor.chapter.packed_review import _evaluate
from pdf_craft.extractor.chapter.local_review import local_review_requests
from tests.test_structural_review import state, text


class NativeBodyFaultTests(IsolatedAsyncioTestCase):
    def test_body_only_ownership_asks_a_direct_fault(self):
        value = state([text(0, 'An ordinary reading paragraph.')])
        body = next(p['questions']['owner_0'] for p in local_review_requests(
            value, concrete_joins=True, native_faults=True) if 'owner_0' in p['questions'])
        legacy = next(p['questions']['owner_0'] for p in local_review_requests(value, concrete_joins=True)
                      if 'owner_0' in p['questions'])
        self.assertIn('incorrect', body['instructions'][0])
        self.assertIn('excluded footnote', body['criteria']['true'])
        self.assertIn('ownership is acceptable', body['criteria']['false'])
        self.assertNotEqual(body['criteria'], legacy['criteria'])

    async def test_native_fault_response_is_inverted_without_generic_rewording(self):
        spec = {'instructions': ['Is this exact ownership incorrect?'],
                'criteria': {'true': 'A definite ownership error.', 'false': 'Acceptable.'}}
        request = {'state': {'checks': {'owner_0': {'source_order': 0}}},
                   'questions': {'owner_0': spec}}
        packets = []

        async def evaluate(_page, packet):
            packets.append(packet)
            self.assertEqual(packet['questions']['q0']['instructions'][0], spec['instructions'][0])
            self.assertEqual(packet['questions']['q0']['criteria'], spec['criteria'])
            self.assertNotIn("This question's proposition", str(packet))
            return {'q0': .85}

        result = await _evaluate([(2, request)], evaluate, evidence={},
                                 native_fault_names=frozenset({'owner_0'}))
        self.assertEqual(len(packets), 1)
        self.assertAlmostEqual(result['owner_0'], .15)
