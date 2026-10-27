"""Opt-in paid citation.pdf regression: OCR/JEV/LLM and English PDF writeback.

Run with --output NEW-DIRECTORY. Not included in default tests or CI.
"""
# pylint: disable=protected-access
import argparse
import asyncio
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

from pdf_craft.pdf import ocr
from .deepseek_refined import run as run_refined

SOURCE = Path(__file__).resolve().parents[2] / 'tests/assets/pdf/citation.pdf'
SOURCE_SHA256 = 'e42e3288d80f500009535ce3e47fab6a31e0e4644ead323917a2ea150de3f56e'
HISTORICAL_SIZE = (1943, 2845)
HISTORICAL_BOXES = ((225, 2248, 1745, 2529), (301, 2532, 762, 2629))


def mask_difference(size, boxes, masks):
    """Compare pixel unions using Pillow's actual inclusive erase boundaries."""
    width, height = size
    cutoff = round(height * (2 / 3))
    with Image.new('1', size) as expected, Image.new('1', size) as actual:
        draw = ImageDraw.Draw(expected)
        draw.rectangle((0, 0, width, cutoff), fill=1)
        for x1, _, x2, y2 in boxes:
            if y2 > cutoff:
                draw.rectangle((x1, cutoff, x2, y2), fill=1)
        draw = ImageDraw.Draw(actual)
        for rectangle in masks:
            draw.rectangle(rectangle, fill=1)
        with ImageChops.subtract(expected, actual) as missing, ImageChops.subtract(actual, expected) as extra:
            return {'missing_bbox': missing.getbbox(), 'extra_bbox': extra.getbbox()}


def check_masks(size, boxes, masks):
    difference = mask_difference(size, boxes, masks)
    return {**difference, 'passed': not any(difference.values())}


async def run(source: Path, output: Path, profile: str):
    if output.exists():
        raise ValueError('Output must be a new directory (no reuse of cached OCR)')
    records = []
    historical_masks = list(ocr._redact_rectangles(HISTORICAL_SIZE, HISTORICAL_BOXES))
    historical = check_masks(HISTORICAL_SIZE, HISTORICAL_BOXES, historical_masks)
    original_prepare = ocr._prepare_second_stage
    pipeline_completed = False

    def prepare(item, first_result, directory, aborted):
        # Observe production preparation; do not replace its geometry or inputs.
        result = original_prepare(item, first_result, directory, aborted)
        evidence = output / 'ocr-evidence'
        evidence.mkdir(exist_ok=True)
        shutil.copyfile(item.request_path, evidence / f'page-{item.page_index}-stage-1.png')
        shutil.copyfile(result.request_path, evidence / f'page-{item.page_index}-stage-2.png')
        boxes = [layout.det for layout in first_result.layouts]
        with Image.open(item.request_path) as image:
            size = image.size
        masks = result.erased_rectangles[len(item.erased_rectangles):]
        record = {'page': item.page_index, 'size': size, 'first_stage_boxes': boxes,
                  'masks': masks, **check_masks(size, boxes, masks)}
        records.append(record)
        (evidence / f'page-{item.page_index}.json').write_text(json.dumps(record, indent=2))
        return result

    try:
        with patch.object(ocr, '_prepare_second_stage', prepare):
            await run_refined(
                source, output, profile, source_sha256=SOURCE_SHA256, source_bytes=477248,
                pages=3, target_language='en', strict=True, generate_plot=True,
            )
        pipeline_completed = True
    finally:
        # Preserve diagnostics even if extraction/refinement/translation raises.
        geometry_passed = (historical['passed'] and len(records) == 3
                           and {row['page'] for row in records} == {1, 2, 3}
                           and all(row['passed'] for row in records))
        report = {'source_sha256': SOURCE_SHA256, 'historical_geometry': historical,
                  'pages': records, 'pipeline_completed': pipeline_completed,
                  'automated_passed': pipeline_completed and geometry_passed,
                  'visual_review': 'pending: compare all source/translated pages, especially page-1 footnotes'}
        if output.is_dir():
            (output / 'citation-checks.json').write_text(json.dumps(report, indent=2))
    if not report['automated_passed']:
        raise ValueError('Citation regression failed; inspect citation-checks.json')
    print('AUTOMATED_CHECKS_PASSED; VISUAL_INSPECTION_REQUIRED', output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', default='default')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('--output must be a new directory (no reuse of cached OCR)')
    asyncio.run(run(args.source, args.output, args.profile))


if __name__ == '__main__':
    main()
