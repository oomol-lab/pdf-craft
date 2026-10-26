"""Real-resource reproduction of the 2.6.0 refined-footnote extraction failure.

Run from the worktree root with ``python -m pdf_craft_tool.smoke.deepseek_refined
SOURCE.pdf --output pdf-craft-output/NEW-RUN``. Every run uses a new directory.
This is an opt-in, paid integration check, never part of the unit test suite.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

import pdf_craft
from pdf_craft import (
    AsyncPDFCraft, ConcurrentExecutor, FixedCapacity, ExtractionOptions,
    FootnoteOptions, FootnoteRefinement, RetryPolicy, XMLTranslator, PDFOptions,
)
from pdf_craft.pdf.handler import DefaultPDFHandler
from ..runtime import create_jev_from_env, create_llm_from_env, create_ocr_config_from_env, load_project_env

SOURCE_SHA256 = 'dd5cec9ee94ebf4fa755a9e8d6155d83156df50af0294495910f5ac2d3cde5ea'


async def run(source: Path, output: Path, profile: str):
    data = source.read_bytes()
    if len(data) != 1234259 or hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError('This gate requires the unchanged original six-page DeepSeek-OCR PDF')
    if len(PdfReader(source).pages) != 6:
        raise ValueError('Expected six source pages')
    output.mkdir(parents=True, exist_ok=False)
    load_project_env(Path.cwd())
    executor = ConcurrentExecutor(FixedCapacity(2))
    llm = create_llm_from_env(profile, cache_path=output / 'llm-cache', log_dir_path=output / 'llm-logs')
    jev = create_jev_from_env()
    craft = AsyncPDFCraft(pdf=PDFOptions(ocr=create_ocr_config_from_env('deepseek-ocr-vendor'), ocr_executor=executor))
    retry = RetryPolicy(translation=8, analysis=6, fill=12)
    failures, counts = [], Counter()

    def error_policy(context):
        # Never serialize configuration objects, keys, endpoint URLs or raw responses.
        row = {'category': context.category.value, 'stage': context.stage,
               'fallback': context.fallback, 'error_type': context.error.type,
               'code': context.error.code, 'attempts': context.error.attempts,
               'targets': [list(value.page_indexes) for value in context.targets],
               'affected': [list(value.page_indexes) for value in context.affected]}
        failures.append(row)
        (output / 'failures.json').write_text(json.dumps(failures, indent=2))
        print('RECOVERY', json.dumps(row), flush=True)
        return context.error.code != 'BALANCE_EXHAUSTED'

    def body(_value):
        counts['extraction_body'] += 1

    def note(_value):
        counts['extraction_footnote'] += 1

    def progress(event):
        print('OCR', event.kind.value, event.page_index, flush=True)

    source_extraction = await craft.extract_pdf(
        source, output / 'book.pcex',
        ExtractionOptions(includes_cover=True, includes_furniture=True,
                          extract_book_metadata=False, toc_assumed=True, toc_llm=llm,
                          footnotes=FootnoteOptions(FootnoteRefinement(jev=jev, llm=llm, executor=executor)),
                          on_body=body, on_footnote=note, on_extraction_event=progress),
        analysing_path=output / 'analysis', retry_policy=retry, error_policy=error_policy,
    )
    print('EXTRACTION_COMPLETE', flush=True)
    translator = XMLTranslator(llm, llm, 'zh-CN', None, 3, 2600, executor=executor)
    await craft.translate_pdf(source, source_extraction, output / 'translated.pdf', translator,
                              window=2, with_furniture=True, retry_policy=retry, error_policy=error_policy)
    print('TRANSLATION_COMPLETE', flush=True)
    result_pages = len(PdfReader(output / 'translated.pdf').pages)
    if result_pages != 6:
        raise ValueError(f'Output has {result_pages} pages, expected six')
    # Render all pages for actual visual inspection; page count alone is not acceptance.
    previews = output / 'previews'
    previews.mkdir()
    for label, path in [('source', source), ('translated', output / 'translated.pdf')]:
        document = DefaultPDFHandler().open(path)
        try:
            for index in range(1, 7):
                image = document.render_page(index, 100)
                try:
                    image.save(previews / f'{label}-{index}.png')
                finally:
                    image.close()
        finally:
            document.close()
    summary = {'source_sha256': SOURCE_SHA256, 'source_bytes': len(data), 'pages': result_pages,
               'parameters': {'input_format': 'pdf', 'footnotes': 'refined', 'output_format': 'pdf',
                              'rendering_mode': 'replace', 'target_language': 'zh-CN'},
               'extraction': {'includes_cover': True, 'includes_furniture': True,
                              'extract_book_metadata': False, 'toc_assumed': True, 'preview_callbacks': True},
               'retry_policy': {'translation': 8, 'analysis': 6, 'fill': 12},
               'sdk_source': str(Path(pdf_craft.__file__).resolve()), 'callback_counts': dict(counts),
               'failures': failures, 'output': str(output / 'translated.pdf'),
               'previews': str(previews), 'visual_review': 'pending: inspect all six source/output page pairs'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print('COMPLETE; VISUAL_INSPECTION_REQUIRED', str(previews), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', default='default')
    args = parser.parse_args()
    asyncio.run(run(args.source, args.output, args.profile))


if __name__ == '__main__':
    main()
