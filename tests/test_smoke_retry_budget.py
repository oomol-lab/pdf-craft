"""Configured smoke budgets reach real public conversion and repair loops."""
# pylint: disable=protected-access
import json
from contextlib import ExitStack
from typing import cast
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from pdf_craft import PDFCraft, PDFCraftExtraction
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLFillError
from pdf_craft.common import save_xml
from pdf_craft.document.stream_view import content_view
from pdf_craft.metering import OCRTokensMetering
from pdf_craft.ocr_config import OCRConfig
from pdf_craft.extractor.chapter.chapter import Chapter, encode
from pdf_craft.policies import RetryScope
from pdf_craft._policy import current
from pdf_craft_tool.smoke.runner import SmokeAsset, SmokeRun, _translate_package, _run_pdf
from tests.extraction_helpers import make_extraction
from tests.translation_model import paragraph, translator_fixture


class SmokeRetryBudgetTests(TestCase):
    def test_public_extraction_smoke_survives_four_failures_and_stops_at_budget(self):
        for failures in (4, 20):
            with self.subTest(failures=failures):
                self._run_budget_case(failures)

    def test_public_pdf_smoke_uses_the_same_bounded_budget(self):
        for failures in (4, 20):
            with self.subTest(failures=failures):
                self._run_budget_case(failures, pdf=True)

    def _run_budget_case(self, failures, *, pdf=False):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root/'source', page_pixel_sizes={1: (100, 100), 2: (100, 100)})
            save_xml(encode(Chapter(None, -1, [paragraph(1), paragraph(2)])),
                     root/'source/chapters/chapter_head.xml')
            translator, model = translator_fixture()
            original = model.answer
            attempts, limits, xml_limits = [], [], []
            def answer(messages):
                if messages[0]['content'].startswith('You are a translator.'):
                    return original(messages).replace('Paragraph', 'Translated')
                attempts.append(len(attempts)+1)
                limits.append(current().retry.attempts(RetryScope.FILL))
                xml_limits.append(current().retry.attempts(RetryScope.FILL))
                if len(attempts) <= failures:
                    return 'not XML'
                return original(messages).replace('Paragraph', 'Translated')
            model.answer = answer
            run = SmokeRun('pdf/citation.pdf', 'markdown', translation={
                'target_language': 'English', 'retry_policy': {'fill': 12}})
            craft, received = PDFCraft(), []
            async def patch_binary(_source, source_extraction, output, *, translator, **_kwargs):
                # Consume the real PCEX translation stream; replace only the
                # unrelated binary renderer and OCR for this budget regression.
                async with content_view(source_extraction, translator=translator,
                                        with_anchored=False, deferred={}) as (_, _, _, items):
                    async for item in items:
                        received.append(str(item.value))
                from pypdf import PdfWriter
                writer = PdfWriter()
                for _ in range(3):
                    writer.add_blank_page(width=100, height=100)
                writer.write(output)
            with ExitStack() as stack:
                stack.enter_context(patch('pdf_craft_tool.smoke.runner.model_request_options', return_value={}))
                if pdf:
                    (root/'output').mkdir()
                    run = SmokeRun('pdf/citation.pdf', 'pdf-patch', translation=run.translation)
                    stack.enter_context(patch('pdf_craft_tool.smoke.runner.PDFCraft', return_value=craft))
                    stack.enter_context(patch.object(craft, 'extract_pdf_with_metering',
                                                   return_value=(extraction, OCRTokensMetering(0, 0))))
                    stack.enter_context(patch('pdf_craft_tool.smoke.runner._xml_translation_transformer',
                                              return_value=translator))
                    stack.enter_context(patch('pdf_craft.craft._validate_extraction_for_pdf'))
                    stack.enter_context(patch('pdf_craft.pipeline.pdf.content.patch_content', patch_binary))
                def execute():
                    if pdf:
                        return _run_pdf(run, SmokeAsset('pdf/citation.pdf', 'pdf', Path('tests/assets/pdf/citation.pdf')),
                                        root, cast(OCRConfig, None))
                    return _translate_package(craft, extraction, root, translator, run)
                if failures == 4:
                    target = execute()
                    if pdf:
                        assert isinstance(target, tuple)
                        self.assertEqual(target[0], 'passed', target[1])
                        texts = ''.join(received)
                    else:
                        assert isinstance(target, PDFCraftExtraction)
                        with target._materialize() as paths:
                            texts = ''.join(p.read_text() for p in paths.translations.glob('*/chapters/*.xml'))
                    self.assertIn('Translated 1.', texts)
                    self.assertIn('Translated 2.', texts)
                    self.assertTrue(xml_limits)
                    self.assertEqual(set(xml_limits), {13})
                else:
                    with self.assertRaises(XMLFillError):
                        execute()
            self.assertEqual(len(attempts), 5 if failures == 4 else 13)
            self.assertEqual(set(limits), {13})
            self.assertEqual(sum(call[0].message.startswith('You are a translator.')
                                 for call in model.wire_calls), 1)

    def test_five_case_config_preserves_scope_and_backend(self):
        from pdf_craft_tool.smoke.runner import expand_matrix
        config = json.loads(Path('tests/smoke/translation_fill_regression.json').read_text(encoding='utf-8'))
        runs = expand_matrix(config, Path('tests/assets'))
        self.assertEqual(len(runs), 5)
        self.assertEqual([run.backend for run in runs[:4]], ['deepseek-ocr-vendor']*4)
        self.assertEqual([run.route for run in runs], ['markdown', 'markdown', 'epub', 'pdf-patch', 'page-repair'])
        self.assertEqual(runs[0].page_indexes, (1, 2, 3))
        self.assertEqual([run.ocr_size for run in runs[:4]], ['tiny', 'gundam', 'gundam', 'gundam'])
        for run in runs[:4]:
            assert run.translation is not None
            self.assertEqual(run.translation['retry_policy']['fill'], 12)
