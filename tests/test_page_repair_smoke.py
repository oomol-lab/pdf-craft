"""Repair smoke checks final facts, independent of where they were corrected."""
import unittest
import json
from pathlib import Path

from pdf_craft.extractor.chapter.generation import prepare_chapter_analysis
from pdf_craft.extractor.chapter.page_repair import build_page_repair_messages
from pdf_craft.extractor.toc import TocInfo
from pdf_craft_tool.smoke.page_repair import _check_final_state


class PageRepairSmokeTests(unittest.TestCase):
    def setUp(self):
        self.analysis = prepare_chapter_analysis(
            Path(__file__).parent / 'assets/analysis/citation_large_ocr', TocInfo([], []))

    def test_model_seam_evidence_uses_immediate_sides_not_a_later_page_wrap(self):
        page = next(item for item in self.analysis.pages if item.page_index == 12)
        messages = build_page_repair_messages(previous_page=None, target_page=page,
            next_page=None, previous_pass_probability=None, next_pass_probability=None)
        target = json.loads(messages[1].message)['target_page']
        seam = next(item for item in target['source_text_seams']
                    if item['left_id'] == 'layout-51539607557')
        layouts = {item['layout_id']: item for item in target['layouts']}
        self.assertEqual(seam['right_id'], 'layout-51539607558')
        self.assertEqual(seam['left_end'], layouts[seam['left_id']]['text'][-120:])
        self.assertEqual(seam['right_start'], layouts[seam['right_id']]['text'][:120])
        self.assertTrue(seam['left_end'].endswith('呢?'))
        self.assertNotIn('某一种治疗', seam['left_end'])

    def test_already_correct_target_does_not_require_an_llm_change(self):
        expected = {'pages': [{'page_index': 20, 'layouts': {
            'layout-85899345922': {'ownership': 'paragraph', 'continues_from_previous': False},
        }}]}
        self.assertEqual(_check_final_state(self.analysis.pages, expected), [])

    def test_incorrect_final_target_is_rejected(self):
        expected = {'pages': [{'page_index': 20, 'layouts': {
            'layout-85899345922': {'ownership': 'ignored'},
        }}]}
        self.assertTrue(_check_final_state(self.analysis.pages, expected))

    def test_missing_page_and_layout_are_rejected(self):
        for expected in ({'pages': [{'page_index': 999, 'layouts': {}}]},
                         {'pages': [{'page_index': 20, 'layouts': {'layout-999': {}}}]}):
            self.assertTrue(_check_final_state(self.analysis.pages, expected))
