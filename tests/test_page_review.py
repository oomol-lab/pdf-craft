import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft.extractor.chapter.generation import (
    _extract_body_layouts,
    _resolve_pages,
)
from pdf_craft.extractor.chapter.page_analysis import analyse_pages
from pdf_craft.extractor.chapter.page_review import (
    JEV_QUESTION_NAME,
    JEV_REVIEW_THRESHOLD,
    JevReviewProcessor,
    build_jev_review_requests,
)
from pdf_craft.extractor.toc import TocInfo
from pdf_craft.pdf import decode
from pdf_craft_tool.cli import _parser
from pdf_craft_tool.jev import OoJevEvaluator


class PageReviewTests(unittest.TestCase):
    def test_cli_exposes_opt_in_oo_jev_review(self):
        args = _parser().parse_args([
            "analysis", "review-jev", "analysis/ocr",
            "--output", "review-output",
        ])
        self.assertEqual(args.ocr_path, Path("analysis/ocr"))
        self.assertEqual(args.output, Path("review-output"))
        self.assertEqual(args.threshold, JEV_REVIEW_THRESHOLD)

    def test_cli_exposes_jev_llm_page_repair(self):
        args = _parser().parse_args([
            "analysis", "repair-jev-llm", "analysis/ocr",
            "--output", "repair-output",
            "--llm-profile", "repair-test",
        ])
        self.assertEqual(args.ocr_path, Path("analysis/ocr"))
        self.assertEqual(args.output, Path("repair-output"))
        self.assertEqual(args.llm_profile, "repair-test")
        self.assertEqual(args.threshold, JEV_REVIEW_THRESHOLD)

    def test_selected_prompt_builds_semantic_page_packet(self):
        page = decode(fromstring("""<page index='1'><body>
            <layout ref='text' det='10,10,90,40'>Body①</layout>
            </body><footnotes>
            <layout ref='text' det='10,80,90,95'>① Note.</layout>
            </footnotes></page>"""))
        paragraphs, citations = _resolve_pages([page])
        analyses = analyse_pages([1], paragraphs, citations)

        requests = build_jev_review_requests([page], analyses, {1: (100, 100)})

        self.assertEqual(len(requests), 1)
        page_index, request = requests[0]
        self.assertEqual(page_index, 1)
        self.assertIn(JEV_QUESTION_NAME, request["questions"])
        target = request["state"]["target_page"]
        self.assertEqual(target["pixel_size"], [100, 100])
        self.assertEqual(target["layouts"][0]["raw_text"], "Body①")
        self.assertEqual(target["layouts"][0]["analysed_text"], "Body⟦REF⟧")
        self.assertEqual(target["layouts"][0]["bbox_normalized"], [0.1, 0.1, 0.9, 0.4])
        self.assertEqual(target["citations"][0]["owned_layout_count_on_page"], 1)

    def test_risk_threshold_routes_low_pass_probability(self):
        pages = [decode(fromstring(
            "<page index='1'><body><layout ref='text' det='1,1,9,9'>Body</layout>"
            "</body><footnotes></footnotes></page>"
        ))]
        paragraphs, citations = _resolve_pages(pages)
        analyses = analyse_pages([1], paragraphs, citations)
        reviewer = JevReviewProcessor(lambda _page, _request: 0.29)

        returned = reviewer(pages, analyses, {1: (10, 10)})

        self.assertIs(returned, analyses)
        self.assertAlmostEqual(reviewer.results[0].risk, 0.71)
        self.assertTrue(reviewer.results[0].requires_review)

    def test_generation_hook_runs_between_projection_and_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            pages_path = Path(directory)
            (pages_path / "page_1.xml").write_text(
                "<page index='1'><body><layout ref='text' det='1,1,9,9'>Body</layout>"
                "</body><footnotes></footnotes></page>",
                encoding="utf-8",
            )
            (pages_path / "page_pixel_sizes.json").write_text(
                '{"1": [10, 10]}', encoding="utf-8"
            )
            calls: list[tuple[int, int]] = []

            def processor(source_pages, analyses, page_pixel_sizes):
                calls.append((len(source_pages), page_pixel_sizes[1][0]))
                return analyses

            layouts = list(_extract_body_layouts(
                pages_path, TocInfo([], []), processor
            ))

        self.assertEqual(calls, [(1, 10)])
        self.assertEqual(len(layouts), 1)

    def test_oo_adapter_uses_connector_contract_and_reads_probability(self):
        captured_request: dict = {}

        def run(command, **_kwargs):
            request_path = Path(command[command.index("--data") + 1][1:])
            captured_request.update(json.loads(request_path.read_text(encoding="utf-8")))
            response = {
                "data": {
                    "answers": {
                        JEV_QUESTION_NAME: {"type": "noul", "noul": 0.81}
                    }
                }
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(response), "")

        with patch("pdf_craft_tool.jev.subprocess.run", side_effect=run) as execute:
            probability = OoJevEvaluator()(3, {"state": {}, "questions": {}})

        self.assertEqual(probability, 0.81)
        self.assertEqual(captured_request["state"], {})
        command = execute.call_args.args[0]
        self.assertEqual(command[:6], [
            "oo", "connector", "run", "jev", "--action", "evaluate"
        ])
        self.assertIn("--json", command)

    def test_oo_adapter_reuses_matching_saved_evaluation(self):
        request = {"state": {"page": 1}, "questions": {}}
        response = {
            "data": {
                "answers": {
                    JEV_QUESTION_NAME: {"type": "noul", "noul": 0.73}
                }
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "page_001-request.json").write_text(
                json.dumps(request), encoding="utf-8"
            )
            (output / "page_001-response.json").write_text(
                json.dumps(response), encoding="utf-8"
            )
            evaluator = OoJevEvaluator(output, reuse_existing=True)
            with patch("pdf_craft_tool.jev.subprocess.run") as execute:
                probability = evaluator(1, request)

        self.assertEqual(probability, 0.73)
        execute.assert_not_called()

    def test_citation_large_baseline_keeps_zero_false_negatives(self):
        baseline_path = (
            Path(__file__).parent
            / "assets/analysis/citation_large_jev_baseline.json"
        )
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        expected = set(baseline["confirmed_review_pages"])
        self.assertEqual(baseline["threshold"], JEV_REVIEW_THRESHOLD)
        for run in baseline["runs"]:
            with self.subTest(run=run["name"]):
                selected = {
                    page_index
                    for page_index, probability in enumerate(
                        run["pass_probabilities"], 1
                    )
                    if 1 - probability >= JEV_REVIEW_THRESHOLD
                }
                self.assertEqual(selected, expected)


if __name__ == "__main__":
    unittest.main()
