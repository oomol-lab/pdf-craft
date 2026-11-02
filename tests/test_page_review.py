import json
from math import nextafter, inf
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft.extractor.chapter.generation import (
    _resolve_pages,
    prepare_chapter_analysis,
)
from pdf_craft.extractor.chapter.page_analysis import analyse_pages
from pdf_craft.extractor.chapter.page_review import (
    JEV_REVIEW_THRESHOLD,
    JevReviewProcessor,
    build_jev_review_requests,
    build_jev_review_states,
    review_threshold,
)
from pdf_craft.extractor.toc import TocInfo
from pdf_craft.pdf import decode
from pdf_craft_tool.cli import _parser
from pdf_craft_tool.jev import PinnedPageSelection


class PageReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_cutoff_and_adjacent_float_probabilities_route_distinctly(self):
        pages = [decode(fromstring("<page index='1'><body>"
            "<layout ref='text' det='1,1,9,9'>Body.</layout></body></page>"))]
        paragraphs, citations = _resolve_pages(pages)
        analyses = analyse_pages([1], paragraphs, citations)
        async def unused(_index, _packet):
            raise AssertionError('Use the supplied complete review probability')
        async def unused_many(_requests):
            raise AssertionError('Use the supplied complete review probability')
        for footnotes in (False, True):
            threshold = review_threshold(footnotes)
            cutoff = 1 - threshold
            function = 'review_footnote_page' if footnotes else 'review_body_page'
            for probability, expected in ((nextafter(cutoff, inf), False),
                                          (cutoff, True), (nextafter(cutoff, -inf), True)):
                with self.subTest(footnotes=footnotes, probability=probability), patch(
                        'pdf_craft.extractor.chapter.page_review.' + function,
                        return_value=probability):
                    reviewer = JevReviewProcessor(unused, includes_footnotes=footnotes,
                                                 question_batch_evaluator=unused_many)
                    await reviewer(pages, analyses, {1: (10, 10)})
                    result = reviewer.results[0]
                    self.assertEqual(result.requires_review, expected)
                    if probability == cutoff:
                        self.assertEqual(result.risk, threshold)

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

        requests = list(build_jev_review_requests(
            [page], analyses, {1: (100, 100)},
        ))

        self.assertGreater(len(requests), 1)
        page_index, _ = requests[0]
        self.assertEqual(page_index, 1)
        self.assertTrue(all(len(p["questions"]) <= 4 for _, p in requests))
        target = next(iter(build_jev_review_states([page], analyses, {1: (100, 100)})))[1]["target_page"]
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
            return 0.71

        reviewer = JevReviewProcessor(evaluate)

        returned = await reviewer(pages, analyses, {1: (10, 10)})

        self.assertIs(returned, analyses)
        self.assertAlmostEqual(reviewer.results[0].risk, 0.71)
        self.assertTrue(reviewer.results[0].requires_review)

    async def test_review_uses_unrounded_probability_and_includes_the_cutoff(self):
        pages = [decode(fromstring(
            f"<page index='{index}'><body>"
            "<layout ref='text' det='1,1,9,9'>A paragraph.</layout>"
            "</body></page>"
        )) for index in (1, 2, 3)]
        paragraphs, citations = _resolve_pages(pages)
        analyses = analyse_pages([1, 2, 3], paragraphs, citations)

        async def evaluate(index, _request):
            return (1 - {1: 0.56, 2: 0.55, 3: 0.54}[index]) if next(iter(_request["questions"])).startswith(("footnote_role_", "furniture_")) else .99

        reviewer = JevReviewProcessor(evaluate)
        await reviewer(pages, analyses, {index: (10, 10) for index in (1, 2, 3)})
        self.assertEqual([result.requires_review for result in reviewer.results], [False, True, True])
        self.assertEqual(reviewer.results[1].risk, JEV_REVIEW_THRESHOLD)

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

    def test_pinned_selection_uses_recorded_threshold_without_replaying_questions(self):
        baseline_path = (
            Path(__file__).parent
            / "assets/analysis/citation_large_jev_baseline.json"
        )
        evaluator = PinnedPageSelection(baseline_path, "strict-rubric")

        self.assertEqual(evaluator.run_name, "strict-rubric")
        self.assertEqual(evaluator.page_indexes, [2, 4, 6, 12, 20, 26])
        self.assertFalse(callable(evaluator))

    def test_citation_large_baseline_replays_its_recorded_threshold(self):
        baseline_path = (
            Path(__file__).parent
            / "assets/analysis/citation_large_jev_baseline.json"
        )
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        expected = set(baseline["confirmed_review_pages"])
        self.assertEqual(baseline["threshold"], 0.70)
        for run in baseline["runs"]:
            with self.subTest(run=run["name"]):
                selected = {
                    page_index
                    for page_index, probability in enumerate(
                        run["pass_probabilities"], 1
                    )
                    if 1 - probability >= baseline["threshold"]
                }
                self.assertEqual(selected, expected)


if __name__ == "__main__":
    unittest.main()
