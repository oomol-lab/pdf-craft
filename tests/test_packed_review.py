"""Page-level transport limits and complete, source-preserving review packets."""

# pylint: disable=protected-access
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from pdf_craft import (
    ConcurrentExecutor,
    FixedCapacity,
    JEV,
    RateLimitedError,
    RetryPolicy,
    OperationError,
)
from pdf_craft.jev import JEVRuntime, JEVRequestBudgetExceeded, jev_page_request_budget
from pdf_craft.extractor.chapter.packed_review import (
    _confirmed_role_pass_score,
    _evaluate,
    _localize_checks,
    _role_evidence,
    packed_review,
    pack_review_requests,
)
from pdf_craft.extractor.chapter.page_review import build_jev_review_states
from pdf_craft.extractor.chapter.review_encoding import compact_review_evidence
from pdf_craft.extractor.chapter.review_templates import review_question
from tests.test_local_jev_review import inputs


def page_state(count=12):
    raw, analysed, sizes = inputs(count)
    return next(
        value
        for _, value in build_jev_review_states(
            raw, analysed, sizes, target_page_index=2
        )
    )


class PackedReviewTests(IsolatedAsyncioTestCase):
    async def test_predecessor_candidates_keep_complete_excluded_neighbor_evidence(self):
        state = page_state(1)
        prior = state['previous_page']
        candidate = deepcopy(prior['paragraph_boundary'][-1])
        candidate.update(
            document_order=candidate['document_order'] + 1,
            source_order=99,
            raw_text='An independently retained illustration caption. ' * 8,
            ownership='ignored',
        )
        prior['excluded_candidates'] = [candidate]
        original = deepcopy(state)
        packets = []

        async def evaluate(_index, packet):
            packets.append(packet)
            return {name: 0.5 for name in packet['questions']}

        await packed_review(state, evaluate, includes_footnotes=True)
        self.assertEqual(len(packets), 2)
        first = packets[0]['state']
        candidates = [check['candidate'] for name, check in first['checks'].items()
                      if name.startswith('predecessor_candidate_')]
        self.assertTrue(any(item['source_order'] == 99 for item in candidates))
        layouts = first['local_raw_evidence']['previous_page']['layouts']
        actual = next(item for item in layouts if item['source_order'] == 99)
        self.assertEqual(actual['raw_text'], compact_review_evidence(candidate)['raw_text'])
        self.assertEqual(actual['document_order'], candidate['document_order'])
        self.assertNotIn('ownership', actual)
        self.assertEqual(state, original)

    def test_role_view_preserves_adjacent_assets_and_drops_proposed_ownership(self):
        state = page_state(1)
        for i, side in enumerate(("previous_page", "next_page")):
            asset = dict(document_order=10**12 + i, source_order=None,
                         source_type="image", text_scale_estimate=None,
                         bbox_normalized=[.1, .1, .9, .8], raw_text="",
                         ownership="citation")
            state[side]["assets"] = [asset]
        original = deepcopy(state)
        evidence = _role_evidence(state)
        for side in ("previous_page", "next_page"):
            source = state[side]["assets"][0]
            actual = next(item for item in evidence[side]["layouts"]
                          if item["document_order"] == source["document_order"])
            self.assertEqual(actual["bbox_normalized"], source["bbox_normalized"])
            self.assertEqual(actual["source_type"], source["source_type"])
            self.assertEqual(actual["source_order"], source["source_order"])
            self.assertNotIn("ownership", actual)
        self.assertEqual(evidence["coordinate_note"], state["coordinate_note"])
        self.assertEqual(state, original)

    def test_large_checks_move_intact_beside_their_own_questions(self):
        checks = {'a': {'source_order': 0, 'text': 'First source. ' * 3000},
                  'b': {'source_order': 1, 'text': 'Second source. ' * 3000}}
        source = {'state': {'checks': checks, 'local_raw_evidence': {'page': 2}},
                  'questions': {key: {'instructions': ['Audit only this source.'],
                                      'criteria': {'true': 'Valid', 'false': 'Invalid'}} for key in checks}}
        original = deepcopy(source)
        packet = _localize_checks(source)
        self.assertNotIn('checks', packet['state'])
        self.assertEqual(packet['state']['local_raw_evidence'], {'page': 2})
        for name, question in packet['questions'].items():
            self.assertEqual(json.loads(question['instructions'][-1].split('\n\n')[-1]), checks[name])
            self.assertEqual(question['criteria'], original['questions'][name]['criteria'])
        self.assertEqual(source, original)

    def test_repeated_evidence_uses_opaque_lossless_transport(self):
        state = page_state(1)
        text = "Original repeated source. " * 600
        state['target_page']['layouts'][0]['raw_text'] = text
        original = deepcopy(state)
        name = 'owner_0'
        request = {'state': {'checks': {name: {'source_order': 0}}},
                   'questions': {name: review_question(
                       'body_base', kind='ownership', order=0, key=name, retained=True)}}
        packet = pack_review_requests([(2, request)], evidence=state)
        assert packet is not None
        encoded = packet['state']['local_raw_evidence']['target_page']['layouts'][0]['raw_text']
        self.assertEqual(set(encoded), {'literal_parts_json'})
        self.assertEqual(json.loads(encoded['literal_parts_json']),
                         compact_review_evidence(state)['target_page']['layouts'][0]['raw_text']['literal_parts'])
        self.assertEqual(state, original)

    def test_role_proof_cannot_erase_conflicting_evidence_or_create_a_new_fault(self):
        disputed = _confirmed_role_pass_score(0.3, 0.8)
        self.assertGreater(disputed, 0.3)
        self.assertLess(disputed, 0.67)
        self.assertEqual(_confirmed_role_pass_score(0.8, 0.9), 0.9)
        self.assertEqual(_confirmed_role_pass_score(0.9, 0.2), 0.9)
        self.assertEqual(_confirmed_role_pass_score(0.58, 0.63), 0.58)
        self.assertEqual(_confirmed_role_pass_score(0.0, 1.0), 0.0)
        self.assertEqual(_confirmed_role_pass_score(1.0, 0.0), 1.0)

    async def test_context_candidate_rejections_are_not_page_faults(self):
        state = page_state()
        original = deepcopy(state)
        packets = []

        async def evaluate(index, packet):
            self.assertEqual(index, 2)
            packets.append(packet)
            values = {}
            for alias, name in zip(packet["questions"], packet["state"]["checks"]):
                if name.startswith("predecessor_candidate_"):
                    value = 0.0
                elif name.startswith(
                    ("footnote_role_", "furniture_", "main_reading_", "new_note_")
                ):
                    value = 0.01
                elif packet["questions"][alias]["criteria"]["true"].startswith(
                    "A single word"
                ):
                    value = 0.01
                elif packet["questions"][alias]["criteria"]["true"].startswith(
                    "The target supplies"
                ):
                    value = 0.01
                else:
                    value = 0.99
                values[alias] = value
            return values

        result = await packed_review(state, evaluate, includes_footnotes=True)
        self.assertEqual(len(packets), 2)
        self.assertTrue(
            any(
                n.startswith("predecessor_candidate_")
                for n in packets[0]["state"]["checks"]
            )
        )
        self.assertNotIn("predecessor_candidate_0", result["scores"])
        self.assertGreater(result["pass_probability"], 0.9)
        self.assertEqual(state, original)

    async def test_fault_polarity_restores_the_original_pass_score(self):
        name = "owner_0"
        original = review_question(
            "body_base", kind="ownership", order=0, key=name, retained=True
        )
        request = {
            "questions": {name: original},
            "state": {"checks": {name: {"source_order": 0}}},
        }

        async def evaluate(index, packet):
            self.assertEqual(index, 2)
            actual = packet["questions"]["q0"]
            self.assertEqual(actual["criteria"]["true"], original["criteria"]["false"])
            self.assertEqual(actual["criteria"]["false"], original["criteria"]["true"])
            self.assertEqual(actual["instructions"][0], original["instructions"][0])
            self.assertEqual(set(packet["state"]["checks"]), {name})
            return {"q0": 0.2}

        scores = await _evaluate(
            [(2, request)], evaluate, evidence=page_state(), fault_names=frozenset({name})
        )
        self.assertEqual(scores, {name: 0.8})

    async def test_short_answer_keys_restore_exact_decision_identity(self):
        name = "owner_0"
        original = review_question(
            "body_base", kind="ownership", order=0, key=name, retained=True
        )
        check = {"source_order": 0, "ownership": "paragraph"}
        request = {"questions": {name: original}, "state": {"checks": {name: check}}}

        async def evaluate(index, packet):
            self.assertEqual(index, 2)
            self.assertEqual(set(packet["questions"]), {"q0"})
            self.assertEqual(packet["state"]["checks"], {name: check})
            self.assertEqual(
                packet["questions"]["q0"]["instructions"][0],
                original["instructions"][0],
            )
            self.assertEqual(
                packet["questions"]["q0"]["criteria"], original["criteria"]
            )
            return {"q0": 0.73}

        self.assertEqual(
            await _evaluate([(2, request)], evaluate, evidence=page_state()),
            {name: 0.73},
        )

    async def test_real_page_plans_stay_in_one_or_two_calls_and_preserve_sources(self):
        for footnotes in (False, True):
            with self.subTest(footnotes=footnotes):
                state = page_state()
                original = deepcopy(state)
                packets = []

                # Bind the fresh collector for this iteration, not the next one.
                async def evaluate(index, packet, packets=packets):  # pylint: disable=dangerous-default-value
                    self.assertEqual(index, 2)
                    packets.append(packet)
                    return {
                        name: 0.99 if name.startswith(("owner_", "gap_")) else 0.01
                        for name in packet["questions"]
                    }

                result = await packed_review(
                    state, evaluate, includes_footnotes=footnotes
                )
                self.assertEqual(len(packets), 2 if footnotes else 1)
                self.assertEqual(result["calls"], len(packets))
                self.assertIsNotNone(result["pass_probability"])
                self.assertEqual(state, original)
                if footnotes:
                    self.assertEqual(
                        packets[0]["state"]["local_raw_evidence"]["coordinate_note"],
                        state["coordinate_note"],
                    )
                # The final decision view contains every original source and field.
                self.assertEqual(
                    packets[-1]["state"]["local_raw_evidence"],
                    compact_review_evidence(state),
                )

    async def test_incomplete_scores_do_not_trigger_unbounded_repair_or_partial_approval(
        self,
    ):
        calls = []

        async def incomplete(_index, packet):
            calls.append(packet)
            return {}

        for mode in (False, True):
            calls.clear()
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                await packed_review(page_state(), incomplete, includes_footnotes=mode)
            self.assertEqual(len(calls), 1)

    async def test_blank_page_never_fakes_a_provider_approval(self):
        evaluate = AsyncMock(side_effect=AssertionError("No semantic evidence"))
        result = await packed_review(page_state(0), evaluate, includes_footnotes=False)
        self.assertIsNone(result["pass_probability"])
        evaluate.assert_not_awaited()

    def test_dense_check_sharing_is_lossless_and_keeps_question_identities(self):
        state = page_state(1)
        check = {
            "source_order": 0,
            "raw_text": "A retained source. " * 20,
            "selected_predecessor": {
                "page_index": 1,
                "source_order": 0,
                "text_end": "before " * 40,
            },
        }
        questions = {
            f"owner_{i}": review_question(
                "body_base", kind="ownership", order=0, key=f"owner_{i}", retained=True
            )
            for i in range(70)
        }
        source = {
            "state": {"checks": {name: deepcopy(check) for name in questions}},
            "questions": questions,
        }
        packet = pack_review_requests([(2, source)], evidence=state)
        assert packet is not None
        self.assertEqual(set(packet["questions"]), set(questions))

        def expand(value):
            if isinstance(value, dict):
                if set(value) == {"shared_check"}:
                    return expand(
                        packet["state"]["shared_checks"][value["shared_check"]]
                    )
                return {key: expand(child) for key, child in value.items()}
            if isinstance(value, list):
                return [expand(child) for child in value]
            return value

        self.assertEqual(expand(packet["state"]["checks"]), source["state"]["checks"])
        self.assertEqual(
            packet["state"]["local_raw_evidence"], compact_review_evidence(state)
        )

    async def test_retry_cannot_exceed_the_physical_one_request_budget(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.system_one = AsyncMock(
            side_effect=[
                RateLimitedError(retry_after=0),
                SimpleNamespace(nouls={"x": SimpleNamespace(noul=0.99)}),
            ]
        )
        with patch("pdf_craft.jev.AsyncTypeSafeClient", return_value=client):
            async with JEVRuntime(
                JEV("test"),
                ConcurrentExecutor(FixedCapacity(1)),
                retry_policy=RetryPolicy(analysis=3),
            ) as runtime:
                with jev_page_request_budget(1):
                    with self.assertRaises(OperationError) as caught:
                        await runtime.evaluate(
                            2, {"state": {}, "questions": {"x": {"type": "noul"}}}
                        )
        self.assertIsInstance(caught.exception.__cause__, JEVRequestBudgetExceeded)
        self.assertEqual(client.system_one.await_count, 1)

    async def test_two_call_budget_is_separate_for_each_concurrent_page(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.system_one = AsyncMock(
            return_value=SimpleNamespace(nouls={"x": SimpleNamespace(noul=0.99)})
        )
        with patch("pdf_craft.jev.AsyncTypeSafeClient", return_value=client):
            async with JEVRuntime(
                JEV("test"), ConcurrentExecutor(FixedCapacity(4))
            ) as runtime:

                async def page(index):
                    with jev_page_request_budget(2):
                        for _ in range(2):
                            await runtime.evaluate(
                                index,
                                {"state": {}, "questions": {"x": {"type": "noul"}}},
                            )
                        with self.assertRaises(OperationError) as caught:
                            await runtime.evaluate(
                                index,
                                {"state": {}, "questions": {"x": {"type": "noul"}}},
                            )
                        self.assertIsInstance(
                            caught.exception.__cause__, JEVRequestBudgetExceeded
                        )

                await asyncio.gather(page(1), page(2))
        self.assertEqual(client.system_one.await_count, 4)
