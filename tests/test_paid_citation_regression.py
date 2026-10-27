"""Offline checks for the opt-in paid gate; never submit model requests."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from itertools import permutations

from pdf_craft.pdf.ocr import _redact_rectangles

from pdf_craft_tool.smoke.citation_footnotes import (
    HISTORICAL_BOXES, HISTORICAL_SIZE, SOURCE, SOURCE_SHA256, check_masks, run,
)


class CitationMaskOracleTests(TestCase):
    def test_production_masks_cover_wide_nested_and_crossing_boxes_in_any_order(self):
        for boxes in (
                HISTORICAL_BOXES,
                ((2, 10, 18, 25), (5, 23, 10, 28), (12, 22, 16, 27)),
                ((1, 21, 10, 26), (8, 21, 19, 28), (4, 22, 15, 24)),
                ((1, 0, 19, 20), (4, 0, 12, 19)),
        ):
            size = HISTORICAL_SIZE if boxes == HISTORICAL_BOXES else (20, 30)
            for ordered in permutations(boxes):
                with self.subTest(boxes=ordered):
                    masks = list(_redact_rectangles(size, iter(ordered)))
                    self.assertTrue(check_masks(size, boxes, masks)['passed'])

    def test_pinned_source_is_the_reported_three_page_pdf(self):
        self.assertEqual(SOURCE.stat().st_size, 477248)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA256)

    def test_detects_historical_right_side_leak(self):
        masks = [(0, 0, 1943, 1897), (225, 1897, 301, 2529), (301, 1897, 762, 2629)]
        result = check_masks(HISTORICAL_SIZE, HISTORICAL_BOXES, masks)
        self.assertFalse(result['passed'])
        self.assertEqual(result['missing_bbox'], (763, 1898, 1746, 2530))
        self.assertIsNone(result['extra_bbox'])

    def test_accepts_union_but_rejects_erasing_unrecognized_footer(self):
        masks = [(0, 0, 1943, 1897), (225, 1897, 1745, 2529), (301, 1897, 762, 2629)]
        self.assertTrue(check_masks(HISTORICAL_SIZE, HISTORICAL_BOXES, masks)['passed'])
        result = check_masks(HISTORICAL_SIZE, HISTORICAL_BOXES, [(0, 0, 1943, 2845)])
        self.assertFalse(result['passed'])
        self.assertIsNotNone(result['extra_bbox'])


class CitationGateTests(IsolatedAsyncioTestCase):
    async def test_existing_output_is_rejected_without_calls_or_overwriting(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            report = output / 'citation-checks.json'
            report.write_text('previous run')
            with patch('pdf_craft_tool.smoke.citation_footnotes.run_refined', new_callable=AsyncMock) as paid:
                with self.assertRaisesRegex(ValueError, 'new directory'):
                    await run(SOURCE, output, 'default')
                paid.assert_not_awaited()
            self.assertEqual(report.read_text(), 'previous run')

    async def test_pipeline_error_is_preserved_and_cannot_report_success(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / 'new-run'
            async def failed(*_args, **_kwargs):
                output.mkdir()
                raise RuntimeError('refinement rejected')
            with patch('pdf_craft_tool.smoke.citation_footnotes.run_refined', side_effect=failed):
                with self.assertRaisesRegex(RuntimeError, 'refinement rejected'):
                    await run(SOURCE, output, 'default')
            import json
            report = json.loads((output / 'citation-checks.json').read_text())
            self.assertFalse(report['automated_passed'])
            self.assertFalse(report['pipeline_completed'])
