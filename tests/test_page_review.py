import json
from pathlib import Path
import tempfile
import unittest
from xml.etree.ElementTree import fromstring

from pdf_craft.extractor.chapter.generation import (
    _resolve_pages,
    prepare_chapter_analysis,
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
from pdf_craft_tool.jev import PinnedJevEvaluator


class PageReviewTests(unittest.IsolatedAsyncioTestCase):
    def test_cli_exposes_opt_in_official_jev_review(self):
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

    def test_cli_accepts_pinned_jev_baseline(self):
        args = _parser().parse_args([
            "analysis", "repair-jev-llm", "analysis/ocr",
            "--output", "repair-output",
            "--jev-baseline", "baseline.json",
            "--jev-run", "first-run",
        ])
        self.assertEqual(args.jev_baseline, Path("baseline.json"))
        self.assertEqual(args.jev_run, "first-run")

    def test_cli_can_skip_jev_and_repair_all_pages(self):
        args = _parser().parse_args([
            "analysis", "repair-jev-llm", "analysis/ocr",
            "--output", "repair-output",
            "--all-pages",
        ])
        self.assertTrue(args.all_pages)

    def test_cli_can_skip_jev_and_repair_selected_pages(self):
        args = _parser().parse_args([
            "analysis", "repair-jev-llm", "analysis/ocr",
            "--output", "repair-output",
            "--llm-pages", "2,4,25",
        ])
        self.assertEqual(args.llm_pages, "2,4,25")

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

    async def test_risk_threshold_routes_low_pass_probability(self):
        pages = [decode(fromstring(
            "<page index='1'><body><layout ref='text' det='1,1,9,9'>Body</layout>"
            "</body><footnotes></footnotes></page>"
        ))]
        paragraphs, citations = _resolve_pages(pages)
        analyses = analyse_pages([1], paragraphs, citations)
        async def evaluate(_page, _request):
            return 0.29

        reviewer = JevReviewProcessor(evaluate)

        returned = await reviewer(pages, analyses, {1: (10, 10)})

        self.assertIs(returned, analyses)
        self.assertAlmostEqual(reviewer.results[0].risk, 0.71)
        self.assertTrue(reviewer.results[0].requires_review)

    def test_chapter_analysis_exposes_projection_before_restoration(self):
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
            analysis = prepare_chapter_analysis(pages_path, TocInfo([], []))

        self.assertEqual(len(analysis.source_pages), 1)
        self.assertEqual(len(analysis.pages), 1)
        self.assertEqual(analysis.page_pixel_sizes[1], (10, 10))

    async def test_pinned_evaluator_replays_first_named_run(self):
        baseline_path = (
            Path(__file__).parent
            / "assets/analysis/citation_large_jev_baseline.json"
        )
        evaluator = PinnedJevEvaluator(baseline_path, "strict-rubric")

        self.assertEqual(evaluator.run_name, "strict-rubric")
        self.assertEqual(await evaluator(1, {}), 0.45)
        self.assertEqual(await evaluator(26, {}), 0.16)

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
