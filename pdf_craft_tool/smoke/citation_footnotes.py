"""Opt-in paid citation.pdf regression: OCR/JEV/LLM and English PDF writeback.

Run with --output NEW-DIRECTORY. Not included in default tests or CI.
"""
# pylint: disable=protected-access
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
from zipfile import ZipFile
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from math import floor, ceil

from pdf_craft.pdf import ocr
from pdf_craft import transform
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


def check_preserved_ink(source, translated):
    """Require every source ink pixel to survive within one raster pixel.

    This checks preserved artwork, not translated text. The one-pixel spatial
    tolerance allows antialiasing/rounding; disappearing strokes still fail.
    """
    if source.size != translated.size:
        raise ValueError('formula comparison requires identical raster dimensions')
    original = source.convert('L').point(lambda value: 255 if value < 160 else 0)
    actual = translated.convert('L').point(lambda value: 255 if value < 160 else 0)
    with original, actual, actual.filter(ImageFilter.MaxFilter(3)) as expanded:
        with ImageChops.subtract(original, expanded) as missing:
            total = original.histogram()[255]
            lost = missing.histogram()[255]
        with ImageChops.difference(source.convert('RGB'), translated.convert('RGB')) as difference:
            changed = difference.getbbox()
    return {'source_ink_pixels': total, 'missing_ink_pixels': lost,
            'pixel_difference_bbox': changed, 'passed': total > 0 and lost == 0}


def check_formula_preview(output):
    """Check the pinned citation page-3 display formula in the final PDF render."""
    previews = output / 'previews'
    with Image.open(previews/'source-3.png') as source, Image.open(previews/'translated-3.png') as translated:
        if source.size != translated.size:
            raise ValueError('source and translated page dimensions differ')
        # Pinned fixture geometry, not an LLM result that might omit the formula.
        left, top, right, bottom = 876, 2065, 1066, 2182
        width, height = source.size
        box = (floor(left*width/1942), floor(top*height/2845),
               ceil(right*width/1942), ceil(bottom*height/2845))
        with source.crop(box) as before, translated.crop(box) as after:
            result = check_preserved_ink(before, after)
            for name, image in (('source', before), ('translated', after)):
                with image.resize((image.width*6, image.height*6), Image.Resampling.NEAREST) as detail:
                    detail.save(previews/f'{name}-3-formula-detail.png')
    return {'page': 3, 'preview_bbox': box, **result}


def check_furniture_exclusions(output, *, require_exclusions=True):
    """Compare the actual adopted exclusions with published PCEX furniture.

    This observes production geometry without deciding which text the model
    should exclude. Manual inspection separately checks the semantic decision.
    """
    ignored = {}
    for path in (output/'adopted-pages').glob('page_*.xml'):
        root = ET.parse(path).getroot()
        ignored[int(root.attrib['index'])] = [tuple(map(int, layout.attrib['det'].split(',')))
                                             for layout in root.iter('layout')
                                             if layout.get('ref') == 'ignored']
    collisions = []
    with ZipFile(output/'book.pcex') as archive:
        furniture = ET.fromstring(archive.read('furnitures.xml'))
    for page in furniture.findall('./pages/page'):
        index = int(page.attrib['index'])
        for section in page.findall('section'):
            x1, y1, x2, y2 = map(int, section.attrib['det'].split(','))
            area = max(1, (x2-x1)*(y2-y1))
            for left, top, right, bottom in ignored.get(index, []):
                covered = max(0, min(x2, right)-max(x1, left))*max(0, min(y2, bottom)-max(y1, top))
                if covered / area >= .95:
                    collisions.append({'page': index, 'furniture_bbox': [x1,y1,x2,y2],
                                       'excluded_bbox': [left,top,right,bottom]})
    count = sum(map(len, ignored.values()))
    return {'excluded_regions': count, 'collisions': collisions,
            'exercised': count > 0,
            'passed': len(ignored) == 3 and (count > 0 or not require_exclusions) and not collisions}


def check_reading_flows(output, footnotes):
    """Pinned fixture semantics, independent of JEV's score or process exit."""
    from unicodedata import normalize
    def text(element):
        return ''.join(normalize('NFKC', ''.join(element.itertext())).split())
    body, notes, furniture = '', '', ''
    joined = set()
    definitions = 0
    first_page_paragraphs = []
    with ZipFile(output/'book.pcex') as archive:
        for name in archive.namelist():
            if name.startswith('chapters/') and name.endswith('.xml'):
                root = ET.fromstring(archive.read(name))
                flow = root.find('flow')
                references = root.find('references')
                if flow is not None:
                    body += text(flow)
                    for item in flow.findall('text'):
                        fragments = list(item.iter('fragment'))
                        if fragments and fragments[0].attrib.get('page_index') == '1':
                            first_page_paragraphs.append([fragment.attrib.get('source_order')
                                                          for fragment in fragments
                                                          if fragment.attrib.get('page_index') == '1'])
                        pages = {int(f.attrib['page_index']) for f in item.iter('fragment')}
                        if {1,2} <= pages:
                            joined.add('1-2')
                        if {2,3} <= pages:
                            joined.add('2-3')
                if references is not None:
                    notes += text(references)
                    definitions += len(references)
        furniture = text(ET.fromstring(archive.read('furnitures.xml')))
    phrases = ('我不会在本书', '对拉康来说', '我们可以说', '试着泛化', '米勒', '主人话语')
    leaked = [phrase for phrase in phrases if phrase in body or phrase in furniture]
    missing = [phrase for phrase in phrases if footnotes and phrase not in notes]
    return {'body_note_leaks': leaked, 'missing_note_content': missing,
            'first_page_paragraphs': first_page_paragraphs,
            'cross_page_body_paragraphs': sorted(joined), 'definitions': definitions,
            'passed': not leaked and not missing and joined == {'1-2','2-3'}
                      and first_page_paragraphs == [['0'], ['1'], ['2']]
                      and (definitions >= 5 if footnotes else definitions == 0)}


async def run(source: Path, output: Path, profile: str, *, footnotes=True):
    if output.exists():
        raise ValueError('Output must be a new directory (no reuse of cached OCR)')
    records = []
    historical_masks = list(ocr._redact_rectangles(HISTORICAL_SIZE, HISTORICAL_BOXES))
    historical = check_masks(HISTORICAL_SIZE, HISTORICAL_BOXES, historical_masks)
    original_prepare = ocr._prepare_second_stage
    pipeline_completed = False
    formula = {"passed": False}
    exclusions = {"passed": False}
    reading = {"passed": False}
    original_furniture = transform.write_furnitures

    def furniture(pdf, pages, destination, **kwargs):
        # Copy only the finalized input, without changing production behavior.
        shutil.copytree(pages, output/'adopted-pages')
        return original_furniture(pdf, pages, destination, **kwargs)

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
        with patch.object(ocr, '_prepare_second_stage', prepare), patch.object(transform, 'write_furnitures', furniture):
            await run_refined(
                source, output, profile, source_sha256=SOURCE_SHA256, source_bytes=477248,
                pages=3, target_language='en', strict=True, generate_plot=True, footnotes=footnotes,
            )
        pipeline_completed = True
        formula = check_formula_preview(output)
        exclusions = check_furniture_exclusions(output, require_exclusions=not footnotes)
        reading = check_reading_flows(output, footnotes)
    finally:
        # Preserve diagnostics even if extraction/refinement/translation raises.
        geometry_passed = historical['passed'] and (
            (len(records) == 3 and {row['page'] for row in records} == {1, 2, 3}
             and all(row['passed'] for row in records)) if footnotes else not records)
        report = {'source_sha256': SOURCE_SHA256, 'historical_geometry': historical,
                  'pages': records, 'pipeline_completed': pipeline_completed,
                  'display_formula': formula, 'footnotes': footnotes, 'furniture_exclusions': exclusions,
                  'reading_flows': reading,
                  'automated_passed': pipeline_completed and geometry_passed and formula['passed'] and exclusions['passed'] and reading['passed'],
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
    parser.add_argument('--footnotes', choices=('on', 'off', 'both'), default='on')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('--output must be a new directory (no reuse of cached OCR)')
    async def execute():
        if args.footnotes == 'both':
            args.output.mkdir(parents=True)
            for enabled in (True, False):
                await run(args.source, args.output/('on' if enabled else 'off'), args.profile, footnotes=enabled)
        else:
            await run(args.source, args.output, args.profile, footnotes=args.footnotes == 'on')
    asyncio.run(execute())


if __name__ == '__main__':
    main()
