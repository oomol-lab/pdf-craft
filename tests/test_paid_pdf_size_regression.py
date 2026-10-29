"""Offline acceptance-oracle tests; no paid model requests."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

from pdf_craft_tool.smoke.pdf_size_growth import (
    MAX_OUTPUT_BYTES, SOURCE, SOURCE_BYTES, SOURCE_SHA256, check_artwork, check_output, run,
)


def translated_pdf(path, pages=3):
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    canvas = Canvas(str(path))
    for _ in range(pages):
        canvas.setFont('STSong-Light', 12)
        canvas.drawString(30, 100, '中文翻译验收')
        canvas.showPage()
    canvas.save()


class SizeOracleTests(TestCase):
    def test_missing_artwork_is_rejected_despite_small_translated_output(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            previews = output / 'previews'
            previews.mkdir()
            translated_pdf(output / 'translated.pdf')
            self.assertTrue(check_output(SOURCE, output / 'translated.pdf')['passed'])
            original = Image.new('RGB', (613, 925), 'white')
            ImageDraw.Draw(original).rectangle((250, 130, 350, 200), outline='black')
            original.save(previews / 'source-1.png')
            original.save(previews / 'translated-1.png')
            self.assertTrue(check_artwork(output))
            Image.new('RGB', original.size, 'white').save(previews / 'translated-1.png')
            self.assertFalse(check_artwork(output))

    def test_fixture_contains_only_three_pages_without_hidden_page_objects(self):
        self.assertEqual(SOURCE.stat().st_size, SOURCE_BYTES)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA256)
        reader = PdfReader(SOURCE)
        self.assertEqual(len(reader.pages), 3)
        writer = PdfWriter(clone_from=reader)
        self.assertEqual(sum(isinstance(obj, dict) and obj.get('/Type') == '/Page'
                             for obj in writer._objects), 3)  # pylint: disable=protected-access

    def test_small_translated_output_passes_but_missing_pages_fail(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'out.pdf'
            translated_pdf(target)
            self.assertTrue(check_output(SOURCE, target)['passed'])
            translated_pdf(target, pages=2)
            self.assertFalse(check_output(SOURCE, target)['passed'])

    def test_copying_english_input_cannot_pass_by_being_small(self):
        result = check_output(SOURCE, SOURCE)
        self.assertTrue(result['checks']['output_size'])
        self.assertFalse(result['checks']['translated_text_on_every_page'])
        self.assertFalse(result['passed'])

    def test_exact_150_percent_passes_but_one_extra_byte_fails(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'out.pdf'
            translated_pdf(target)
            writer = PdfWriter(clone_from=target)
            padding = 0
            for _ in range(5):
                writer.add_metadata({'/SizeRegressionPadding': 'x' * padding})
                writer.write(target)
                delta = MAX_OUTPUT_BYTES - target.stat().st_size
                if delta == 0:
                    break
                padding += delta
            self.assertEqual(MAX_OUTPUT_BYTES, 873447)
            self.assertEqual(target.stat().st_size, MAX_OUTPUT_BYTES)
            self.assertTrue(check_output(SOURCE, target)['passed'])
            with target.open('ab') as stream:
                stream.write(b'\n')
            result = check_output(SOURCE, target)
            self.assertTrue(result['checks']['translated_text_on_every_page'])
            self.assertFalse(result['checks']['size_ratio'])
            self.assertFalse(result['checks']['output_size'])
            self.assertFalse(result['passed'])

    def test_large_valid_translated_pdf_fails_size_budget(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'out.pdf'
            translated_pdf(target)
            writer = PdfWriter(clone_from=target)
            writer.add_metadata({'/SizeRegressionPadding': 'x' * (MAX_OUTPUT_BYTES * 2)})
            writer.write(target)
            result = check_output(SOURCE, target)
            self.assertTrue(result['checks']['translated_text_on_every_page'])
            self.assertFalse(result['checks']['output_size'])
            self.assertFalse(result['checks']['size_ratio'])
            self.assertFalse(result['passed'])


class SizeGateTests(IsolatedAsyncioTestCase):
    async def test_pipeline_failure_is_recorded_and_propagated(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / 'run'

            async def fail(*_args, **_kwargs):
                output.mkdir()
                raise RuntimeError('pipeline failed')

            with patch('pdf_craft_tool.smoke.pdf_size_growth.run_refined', side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, 'pipeline failed'):
                    await run(SOURCE, output, 'default')
            report = json.loads((output / 'size-checks.json').read_text())
            self.assertFalse(report['pipeline_completed'])
            self.assertFalse(report['automated_passed'])

    async def test_existing_run_is_not_overwritten_or_charged_again(self):
        with TemporaryDirectory() as directory:
            with patch('pdf_craft_tool.smoke.pdf_size_growth.run_refined', new_callable=AsyncMock) as paid:
                with self.assertRaisesRegex(ValueError, 'new directory'):
                    await run(SOURCE, Path(directory), 'default')
                paid.assert_not_awaited()
