"""Opt-in paid PDF size regression; never run by ordinary tests or CI.

Run with --output NEW-DIRECTORY. Uses real OCR/refinement/translation/fill.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from time import perf_counter

import pypdf

from .deepseek_refined import run as run_refined

SOURCE = Path(__file__).resolve().parents[2] / 'tests/assets/pdf/strategic-justice-size.pdf'
SOURCE_SHA256 = '248f733685f35263026d1d8c6afff07adfa23f0e3d9bcec8793cbe3d3884b3c4'
SOURCE_BYTES = 582298
PAGES = 3
# Fixed-fixture budgets, not a general restriction on user PDFs. Allow font
# embedding overhead, while rejecting the observed multi-megabyte page streams.
MAX_OUTPUT_BYTES = 2 * SOURCE_BYTES
MAX_SIZE_RATIO = 2.0


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
        report['automated_passed'] = report['size_check']['passed']
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
