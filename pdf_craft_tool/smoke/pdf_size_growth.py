"""Opt-in paid PDF size regression; never run by ordinary tests or CI.

Run with --output NEW-DIRECTORY. Uses real OCR/refinement/translation/fill.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter
import xml.etree.ElementTree as ET

import pypdf
from PIL import Image, ImageChops

from pdf_craft.pdf.poppler import PopplerDocument
from pdf_craft.runtime import run_subprocess_sync

from .deepseek_refined import run as run_refined

SOURCE = Path(__file__).resolve().parents[2] / 'tests/assets/pdf/strategic-justice-size.pdf'
SOURCE_SHA256 = '248f733685f35263026d1d8c6afff07adfa23f0e3d9bcec8793cbe3d3884b3c4'
SOURCE_BYTES = 582298
PAGES = 3
# Fixed-fixture budgets, not a general restriction on user PDFs. Allow font
# embedding overhead, while rejecting the observed multi-megabyte page streams.
MAX_OUTPUT_BYTES = SOURCE_BYTES * 3 // 2
MAX_SIZE_RATIO = 1.5


def check_output(source: Path, target: Path) -> dict:
    """Check size without allowing empty, missing, or untranslated output to pass."""
    source_bytes = source.stat().st_size
    output_bytes = target.stat().st_size
    with target.open('rb') as stream:
        reader = pypdf.PdfReader(stream)
        cjk_counts = [sum('\u4e00' <= char <= '\u9fff' for char in (page.extract_text() or ''))
                      for page in reader.pages]
    ratio = output_bytes / source_bytes
    checks = {
        'page_count': len(cjk_counts) == PAGES,
        'translated_text_on_every_page': len(cjk_counts) == PAGES and all(cjk_counts),
        'output_size': 0 < output_bytes <= MAX_OUTPUT_BYTES,
        'size_ratio': ratio <= MAX_SIZE_RATIO,
    }
    return {'source_bytes': source_bytes, 'output_bytes': output_bytes,
            'size_ratio': ratio, 'pages': len(cjk_counts), 'cjk_characters': cjk_counts,
            'max_output_bytes': MAX_OUTPUT_BYTES, 'max_size_ratio': MAX_SIZE_RATIO,
            'checks': checks, 'passed': all(checks.values())}


def check_artwork(output: Path) -> bool:
    """Pinned fixture: the untranslated chart must survive at 100 DPI.

    This catches blank/lost source content even when translated text and the
    size budget pass. The crop excludes translated captions and body text.
    """
    crop = (220, 100, 405, 245)
    with Image.open(output / 'previews/source-1.png') as source, \
         Image.open(output / 'previews/translated-1.png') as target:
        original = source.convert('RGB').crop(crop)
        rendered = target.convert('RGB').crop(crop)
    return ImageChops.difference(original, rendered).getbbox() is None


def check_footnote_layout(target: Path) -> dict:
    """Pinned page 2: allow tiny source overlap, reject amplified ink overlap.

    Poppler measures the written PDF, independently of the planner's guards.
    Restrict to Chinese text lines in the footnote band so separate vector
    formula runs at the same baseline are not mistaken for paragraphs.
    """
    stdout, _ = run_subprocess_sync(
        PopplerDocument(target).command('pdftotext'),
        '-f', '2', '-l', '2', '-bbox-layout', str(target), '-',
    )
    root = ET.fromstring(stdout)
    lines = []
    band_text = []
    for element in root.iter('{http://www.w3.org/1999/xhtml}line'):
        text = ''.join(element.itertext()).strip()
        box = tuple(float(element.attrib[key]) for key in ('xMin', 'yMin', 'xMax', 'yMax'))
        if 440 <= box[1] <= 610:
            band_text.append(text)
            if any('\u4e00' <= char <= '\u9fff' for char in text):
                lines.append((box, text))
    overlaps = []
    for index, (first, first_text) in enumerate(lines):
        for second, second_text in lines[index + 1:]:
            width = min(first[2], second[2]) - max(first[0], second[0])
            height = min(first[3], second[3]) - max(first[1], second[1])
            if width > 1 and height > 2:
                overlaps.append({'height_pt': height, 'first': first_text, 'second': second_text})
    text = ' '.join(band_text)
    markers = {str(number): bool(re.search(rf'\b{number}\b', text)) for number in (43, 44, 45)}
    cjk_count = sum('\u4e00' <= char <= '\u9fff' for char in text)
    return {'line_count': len(lines), 'cjk_characters': cjk_count, 'note_markers': markers,
            'overlaps': overlaps, 'max_overlap_pt': 2,
            'passed': len(lines) >= 6 and cjk_count >= 100 and all(markers.values()) and not overlaps}


async def run(source: Path, output: Path, profile: str):
    """Always preserve the acceptance report, including pipeline failures."""
    if output.exists():
        raise ValueError('Output must be a new directory (no reuse of cached OCR)')
    started = perf_counter()
    report = {'source_sha256': SOURCE_SHA256, 'pypdf_version': pypdf.__version__,
              'pipeline_completed': False, 'automated_passed': False,
              'visual_review': 'pending: compare all three source/translated page pairs'}
    try:
        await run_refined(
            source, output, profile, source_sha256=SOURCE_SHA256,
            source_bytes=SOURCE_BYTES, pages=PAGES, target_language='zh-CN', strict=True,
        )
        report['pipeline_completed'] = True
        report['size_check'] = check_output(source, output / 'translated.pdf')
        report['source_artwork_preserved'] = check_artwork(output)
        report['footnote_layout'] = check_footnote_layout(output / 'translated.pdf')
        report['automated_passed'] = (report['size_check']['passed'] and report['source_artwork_preserved']
                                      and report['footnote_layout']['passed'])
    finally:
        report['elapsed_seconds'] = round(perf_counter() - started, 3)
        if output.is_dir():
            (output / 'size-checks.json').write_text(json.dumps(report, indent=2))
    if not report['automated_passed']:
        raise ValueError('PDF size regression failed; inspect size-checks.json')
    print('AUTOMATED_CHECKS_PASSED; VISUAL_INSPECTION_REQUIRED', output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', default='default')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('--output must be a new directory (no reuse of cached OCR)')
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != SOURCE_SHA256:
        parser.error('--source must match the pinned three-page fixture')
    asyncio.run(run(args.source, args.output, args.profile))


if __name__ == '__main__':
    main()
