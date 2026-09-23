import json
import unittest
from xml.etree.ElementTree import fromstring

from pdf_craft.extractor.chapter.generation import _resolve_pages
from pdf_craft.extractor.chapter.chapter import (
    Reference,
    SourceTextFragment,
    TextFlowItem,
)
from pdf_craft.extractor.chapter.mark import transform2mark
from pdf_craft.extractor.chapter.page_analysis import analyse_pages, restore_streams
from pdf_craft.extractor.chapter.page_repair import (
    JevLlmRepairProcessor,
    _reconcile_cross_page_gaps,
    build_page_repair_messages,
    repair_page_with_llm,
)
from pdf_craft.pdf import decode


def _fragment(page_index, order, *content):
    return SourceTextFragment(
        page_index=page_index,
        source_order=order,
        bbox=(0, 0, 100, 20),
        content=list(content),
    )


def _response_from_message(message):
    target = json.loads(message.message)["target_page"]
    layout_fields = {
        "layout_id", "ownership", "continues_from_previous",
        "continues_to_next", "paragraph_role", "paragraph_level",
        "citation_id", "citation_index",
    }
    return {
        "page_index": target["page_index"],
        "layouts": [
            {key: value for key, value in layout.items() if key in layout_fields}
            for layout in target["layouts"]
        ],
        "citations": target["citations"],
        "references": target["references"],
    }


def _page_with_reference(text_before="Body", text_after=" end"):
    mark = transform2mark("①")
    assert mark is not None
    citation = Reference(
        1,
        1,
        mark,
        [TextFlowItem("body", -1, [_fragment(1, 1, "Note.")])],
    )
    paragraph = TextFlowItem(
        "body",
        -1,
        [_fragment(1, 0, text_before, citation, text_after)],
    )
    return analyse_pages([1], [paragraph], [citation])[0]


class PageRepairTests(unittest.TestCase):
    def test_each_citation_id_has_independent_flow_endpoints(self):
        mark = transform2mark("①")
        assert mark is not None
        citations = [
            Reference(
                page_index,
                1,
                mark,
                [TextFlowItem("body", -1, [
                    _fragment(page_index, page_index, f"Note {page_index}.")
                ])],
            )
            for page_index in (1, 2)
        ]
        pages = analyse_pages([1, 2], [], citations)
        for page in pages:
            page.layouts[0].annotations.continues_from_previous = True
            page.layouts[0].annotations.continues_to_next = True

        _reconcile_cross_page_gaps(pages, {})

        for page in pages:
            self.assertFalse(page.layouts[0].continues_from_previous)
            self.assertFalse(page.layouts[0].continues_to_next)

    def test_ignored_layout_remains_in_page_but_not_restored_stream(self):
        page = analyse_pages([1], [TextFlowItem(
            "body", -1, [_fragment(1, 0, "207")]
        )], [])[0]

        def request(messages, _index, _maximum):
            response = _response_from_message(messages[1])
            response["layouts"][0].update({
                "ownership": "ignored",
                "continues_from_previous": False,
                "continues_to_next": False,
                "paragraph_role": None,
                "paragraph_level": None,
                "citation_id": None,
                "citation_index": None,
            })
            return json.dumps(response)

        repaired = repair_page_with_llm(
            previous_page=None,
            target_page=page,
            next_page=None,
            previous_pass_probability=None,
            next_pass_probability=None,
            request=request,
        )

        self.assertEqual(repaired.layouts[0].ownership, "ignored")
        self.assertEqual(restore_streams([repaired]).paragraphs, [])

    def test_jev_routes_one_page_and_neighbor_scores_only(self):
        source_pages = [
            decode(fromstring(
                f"<page index='{index}'><body><layout ref='text' "
                f"det='1,1,99,20'>Page {index}.</layout></body>"
                "<footnotes></footnotes></page>"
            ))
            for index in (1, 2, 3)
        ]
        paragraphs, citations = _resolve_pages(source_pages)
        analyses = analyse_pages((1, 2, 3), paragraphs, citations)
        probabilities = {1: 0.9, 2: 0.2, 3: 0.8}
        llm_payloads = []

        def request(messages, _index, _maximum):
            llm_payloads.append(json.loads(messages[1].message))
            response = _response_from_message(messages[1])
            response["layouts"][0]["continues_from_previous"] = True
            return json.dumps(response)

        processor = JevLlmRepairProcessor(
            lambda page_index, _request: probabilities[page_index],
            request,
        )
        repaired = processor(
            source_pages,
            analyses,
            {1: (100, 100), 2: (100, 100), 3: (100, 100)},
        )

        self.assertEqual(len(llm_payloads), 1)
        payload = llm_payloads[0]
        self.assertEqual(payload["target_page"]["page_index"], 2)
        self.assertNotIn("jev_p_pass", payload["target_page"])
        self.assertEqual(payload["previous_page"]["jev_p_pass"], 0.9)
        self.assertEqual(payload["next_page"]["jev_p_pass"], 0.8)
        self.assertEqual([page.page_index for page in repaired], [1, 2, 3])
        self.assertTrue(repaired[0].layouts[-1].continues_to_next)
        self.assertTrue(repaired[1].layouts[0].continues_from_previous)

    def test_neighbor_scores_are_disclosed_but_target_score_is_absent(self):
        previous = analyse_pages([1], [TextFlowItem(
            "body", -1, [_fragment(1, 0, "Previous")]
        )], [])[0]
        target = _page_with_reference()
        target.page_index = 2
        following = analyse_pages([3], [TextFlowItem(
            "body", -1, [_fragment(3, 0, "Next")]
        )], [])[0]

        messages = build_page_repair_messages(
            previous_page=previous,
            target_page=target,
            next_page=following,
            previous_pass_probability=0.91,
            next_pass_probability=0.34,
        )
        payload = json.loads(messages[1].message)

        self.assertEqual(payload["previous_page"]["jev_p_pass"], 0.91)
        self.assertEqual(payload["next_page"]["jev_p_pass"], 0.34)
        self.assertNotIn("jev_p_pass", payload["target_page"])
        citation_layout = next(
            layout for layout in payload["target_page"]["layouts"]
            if layout["ownership"] == "citation"
        )
        self.assertEqual(citation_layout["detached_citation_mark"], "①")

    def test_complete_noop_json_round_trips_through_guaranteed_loop(self):
        page = _page_with_reference()

        def request(messages, _index, _maximum):
            return json.dumps(_response_from_message(messages[1]))

        repaired = repair_page_with_llm(
            previous_page=None,
            target_page=page,
            next_page=None,
            previous_pass_probability=None,
            next_pass_probability=None,
            request=request,
        )

        self.assertEqual(restore_streams([repaired]), restore_streams([page]))

    def test_ambiguous_anchor_feedback_retries_without_cascade_error(self):
        page = _page_with_reference("Alpha① middle Alpha", "")
        calls = []

        def request(messages, index, _maximum):
            calls.append(messages)
            response = _response_from_message(messages[1])
            reference = response["references"][0]
            reference["anchor"] = (
                {"before": "", "mark": "①", "after": ""}
                if index == 0
                else {"before": "middle Alpha", "mark": "①", "after": ""}
            )
            return json.dumps(response)

        repaired = repair_page_with_llm(
            previous_page=None,
            target_page=page,
            next_page=None,
            previous_pass_probability=None,
            next_pass_probability=None,
            request=request,
            max_retries=2,
        )

        feedback = calls[1][-1].message
        self.assertIn("REF_ANCHOR_AMBIGUOUS", feedback)
        self.assertNotIn("REF_CITATION_BIJECTION", feedback)
        self.assertEqual(len(restore_streams([repaired]).citations), 1)

    def test_unique_fuzzy_context_transmits_position_without_exact_quote(self):
        page = _page_with_reference("Alpha① middle Alpha", "")

        def request(messages, _index, _maximum):
            response = _response_from_message(messages[1])
            response["references"][0]["anchor"] = {
                "before": "midle Alpha",
                "mark": "①",
                "after": "",
            }
            return json.dumps(response)

        repaired = repair_page_with_llm(
            previous_page=None,
            target_page=page,
            next_page=None,
            previous_pass_probability=None,
            next_pass_probability=None,
            request=request,
        )

        restored = restore_streams([repaired])
        paragraph = restored.paragraphs[0]
        assert isinstance(paragraph, TextFlowItem)
        references = [
            part for part in paragraph.children[0].content
            if isinstance(part, Reference)
        ]
        self.assertEqual(len(references), 1)


if __name__ == "__main__":
    unittest.main()
