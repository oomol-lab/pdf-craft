"""Repair smoke checks final facts, independent of where they were corrected."""
import unittest
from pathlib import Path

from pdf_craft.extractor.chapter.generation import prepare_chapter_analysis
from pdf_craft.extractor.toc import TocInfo
from pdf_craft_tool.smoke.page_repair import _check_final_state


class PageRepairSmokeTests(unittest.TestCase):
    def setUp(self):
        self.analysis = prepare_chapter_analysis(
            Path(__file__).parent / 'assets/analysis/citation_large_ocr', TocInfo([], []))

    def test_already_correct_target_does_not_require_an_llm_change(self):
        expected = {'pages': [{'page_index': 20, 'layouts': {
            'layout-118': {'ownership': 'paragraph', 'continues_from_previous': False},
        }}]}
        self.assertEqual(_check_final_state(self.analysis.pages, expected), [])

    def test_incorrect_final_target_is_rejected(self):
        expected = {'pages': [{'page_index': 20, 'layouts': {
            'layout-118': {'ownership': 'ignored'},
        }}]}
        self.assertTrue(_check_final_state(self.analysis.pages, expected))

    def test_missing_page_and_layout_are_rejected(self):
        for expected in ({'pages': [{'page_index': 999, 'layouts': {}}]},
                         {'pages': [{'page_index': 20, 'layouts': {'layout-999': {}}}]}):
            self.assertTrue(_check_final_state(self.analysis.pages, expected))
