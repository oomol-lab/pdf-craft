"""Repair smoke checks final facts, independent of where they were corrected."""
import unittest
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from pdf_craft.extractor.chapter.generation import prepare_chapter_analysis
from pdf_craft.extractor.chapter.page_repair import build_page_repair_messages
from pdf_craft.extractor.toc import TocInfo
from pdf_craft_tool.smoke.page_repair import _check_final_state


class PageRepairSmokeTests(unittest.TestCase):
    def setUp(self):
        self.analysis = prepare_chapter_analysis(
            Path(__file__).parent / 'assets/analysis/citation_large_ocr', TocInfo([], []))

    def paragraph_fixture(self, answer, page_index=20):
        from pdf_craft.extractor.chapter.page_repair import _compile_repaired_page
        from pdf_craft.extractor.chapter.repair_plan import RepairPlan, materialize_plan
        expected = json.loads((Path(__file__).parent / 'assets/analysis/citation_large_page_repair_expected.json').read_text())
        target = next(item for item in expected['pages'] if item['page_index'] == page_index)
        source = next(item for item in self.analysis.pages if item.page_index == page_index)
        joins = {identity: index > 0 for group in answer for index, identity in enumerate(group)}
        if page_index == 20:
            joins['layout-85899345921'] = True
        def destination(item):
            identity = f'layout-{item.document_order}'
            if identity in joins:
                return dict(kind='body', join=joins[identity])
            if page_index == 20 or item.source_order == 4:
                return dict(kind='ignored')
            if item.source_order == 1:
                return dict(kind='body', heading_level=2)
            return dict(kind='body', join=item.source_order in (0, 3))
        plan = RepairPlan.model_validate(dict(layouts=[dict(
            layout_id=f'layout-{item.document_order}',
            destination=destination(item))
            for item in source.layouts]))
        page = _compile_repaired_page(source, materialize_plan(plan, page_index))
        return source, page, {'pages': [target]}

    def test_all_reviewed_paragraph_answers_preserve_delivery(self):
        fixture = json.loads((Path(__file__).parent / 'assets/analysis/citation_large_page_repair_expected.json').read_text())
        target = next(item for item in fixture['pages'] if item['page_index'] == 20)
        self.assertEqual(len(target['paragraph_answers']), 8)
        for answer in target['paragraph_answers']:
            with self.subTest(answer=answer):
                source, page, expected = self.paragraph_fixture(answer)
                self.assertEqual(_check_final_state([page], expected, source_pages=[source]), [])

    def test_alternative_answers_reject_content_order_and_ownership_damage(self):
        ids = [f'layout-{(20 << 32) + i}' for i in range(1, 7)]
        source, good, expected = self.paragraph_fixture([[ids[0]], ids[1:4], ids[4:]])
        for damage in ('drop', 'duplicate', 'reorder', 'text', 'ignored', 'heading', 'cross_boundary'):
            with self.subTest(damage=damage):
                page = deepcopy(good)
                if damage == 'drop':
                    page.layouts.pop(3)
                elif damage == 'duplicate':
                    page.layouts.insert(3, deepcopy(page.layouts[3]))
                elif damage == 'reorder':
                    page.layouts[2], page.layouts[3] = page.layouts[3], page.layouts[2]
                    for index, item in enumerate(page.layouts):
                        page.layouts[index] = replace(item, document_order=(20 << 32) + index)
                elif damage == 'text':
                    item = page.layouts[3]
                    page.layouts[3] = replace(item, source=replace(item.source, content=('LOST ORIGINAL CONTENT',)))
                elif damage == 'ignored':
                    page.layouts[3].annotations.ownership = 'ignored'
                elif damage == 'heading':
                    page.layouts[2].annotations.paragraph_role = 'heading'
                else:
                    page.layouts[5].annotations.continues_from_previous = True
                    page.layouts[4].annotations.continues_to_next = True
                self.assertTrue(_check_final_state([page], expected, source_pages=[source]))

    def test_question_and_explanation_allow_only_reviewed_span_answers(self):
        fixture = json.loads((Path(__file__).parent / 'assets/analysis/citation_large_page_repair_expected.json').read_text())
        target = next(item for item in fixture['pages'] if item['page_index'] == 12)
        self.assertEqual(len(target['paragraph_answers']), 2)
        for answer in target['paragraph_answers']:
            with self.subTest(answer=answer):
                source, page, expected = self.paragraph_fixture(answer, page_index=12)
                self.assertEqual(_check_final_state([page], expected, source_pages=[source]), [])

    def test_reviewed_span_rejects_damage_and_merging_into_preceding_quote(self):
        ids = ['layout-51539607557', 'layout-51539607558']
        source, good, expected = self.paragraph_fixture([ids], page_index=12)
        for damage in ('drop', 'duplicate', 'reorder', 'text', 'ignored', 'heading', 'context'):
            with self.subTest(damage=damage):
                page = deepcopy(good)
                if damage == 'drop':
                    page.layouts.pop(6)
                elif damage == 'duplicate':
                    page.layouts.append(deepcopy(page.layouts[6]))
                elif damage == 'reorder':
                    page.layouts[5], page.layouts[6] = page.layouts[6], page.layouts[5]
                    for index, item in enumerate(page.layouts):
                        page.layouts[index] = replace(item, document_order=(12 << 32) + index)
                elif damage == 'text':
                    item = page.layouts[6]
                    page.layouts[6] = replace(item, source=replace(item.source, content=('CHANGED',)))
                elif damage == 'ignored':
                    page.layouts[6].annotations.ownership = 'ignored'
                elif damage == 'heading':
                    page.layouts[6].annotations.paragraph_role = 'heading'
                    page.layouts[6].annotations.continues_from_previous = False
                    page.layouts[5].annotations.continues_to_next = False
                else:
                    page.layouts[5].annotations.continues_from_previous = True
                    page.layouts[3].annotations.continues_to_next = True
                self.assertTrue(_check_final_state([page], expected, source_pages=[source]))

    def test_paragraph_oracle_requires_source_and_complete_answer_contract(self):
        ids = [f'layout-{(20 << 32) + i}' for i in range(1, 7)]
        source, page, expected = self.paragraph_fixture([[ids[0]], ids[1:4], ids[4:]])
        with self.assertRaises(ValueError):
            _check_final_state([page], expected)
        expected['pages'][0]['paragraph_answers'] = [[[ids[0]]]]
        with self.assertRaises(ValueError):
            _check_final_state([page], expected, source_pages=[source])

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
