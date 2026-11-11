"""Preflight raster budgets and measured PNG feedback through real Poppler."""
# pylint: disable=protected-access
import json
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image
from doc_page_extractor.extraction_context import ExtractionAbortedError
from pypdf import PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen.canvas import Canvas

from pdf_craft import DeepSeekOCRLocalConfig
from pdf_craft.error import PDFError
from pdf_craft.pdf.handler import DefaultPDFHandler
from pdf_craft.pdf.ocr import OCR
from pdf_craft.pdf.page_ref import PageRef
from pdf_craft.pdf.png import PNGSizeError
from pdf_craft.pdf.poppler import MAX_PAGE_PIXELS, PopplerDocument, _render_dpi
from pdf_craft.pdf.types import Page
from pdf_craft.pipeline.pdf.eraser import EraseOptions, RectangularEraser
from pdf_craft.pipeline.pdf.models import PDFReplacementRegion


class PixelBudgetTests(TestCase):
    def test_large_page_and_lower_requests(self):
        self.assertEqual(_render_dpi((2776, 3952), 300), 194)
        self.assertEqual(_render_dpi((3952, 2776), 300), 194)
        self.assertEqual(_render_dpi((2776, 3952), 100), 100)
        self.assertEqual(_render_dpi((612, 792), 300), 300)
        self.assertEqual(Image.MAX_IMAGE_PIXELS, 89_478_485)

    def test_threshold_rounding_and_minimum_dpi(self):
        with patch('pdf_craft.pdf.poppler.MAX_PAGE_PIXELS', 10_000):
            # Including the pdfinfo precision allowance, these are 100x100.
            self.assertEqual(_render_dpi((99.98, 99.98), 72), 72)
            self.assertEqual(_render_dpi((99.98, 99.98), 73), 72)
            self.assertEqual(_render_dpi((100.001, 99.98), 72), 71)
            self.assertEqual(_render_dpi((7199.98, 7199.98), 300), 1)
            with self.assertRaisesRegex(ValueError, 'even at 1 DPI'):
                _render_dpi((7200.01, 7200.01), 300)

    def test_extreme_aspect_ratios_are_bounded_after_pixel_rounding(self):
        for size in ((0.01, 100_000_000), (100_000_000, 0.01), (10.123, 98765.432)):
            with self.subTest(size=size):
                dpi = _render_dpi(size, 300)
                pixels = [ceil((value + 0.02) * dpi / 72) for value in size]
                self.assertTrue(all(value >= 1 for value in pixels))
                self.assertLessEqual(pixels[0] * pixels[1], MAX_PAGE_PIXELS)
                self.assertLessEqual(dpi, 300)
        for size in ((0, 10), (-1, 10), (float('inf'), 1), (float('nan'), 1)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                _render_dpi(size, 300)

    def test_impossible_page_fails_before_raster_process(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            with PdfWriter() as writer:
                writer.add_blank_page(1e10, 1e10)
                writer.write(source)
            document = PopplerDocument(source)
            document.geometry(1)
            with patch.object(document, '_run', wraps=document._run) as run:
                with self.assertRaisesRegex(ValueError, 'even at 1 DPI'):
                    document.render_page(1, 300)
                run.assert_not_called()

    def test_real_media_box_rotation_and_fractional_dimensions(self):
        with TemporaryDirectory() as folder, patch('pdf_craft.pdf.poppler.MAX_PAGE_PIXELS', 80_000):
            source = Path(folder) / 'source.pdf'
            for rotation in (0, 90, 180, 270):
                with self.subTest(rotation=rotation), PdfWriter() as writer:
                    page = writer.add_blank_page(200.124, 300.376)
                    page.mediabox = RectangleObject((10.123, 20.123, 210.247, 320.499))
                    page.cropbox = RectangleObject((20, 30, 190, 290))
                    page.rotate(rotation)
                    writer.write(source)
                    document = PopplerDocument(source)
                    dpi = document.render_dpi(1, 300)
                    size = (200.124, 300.376)
                    if rotation % 180:
                        size = size[::-1]
                    with document.render_page(1, 300) as image:
                        self.assertEqual(image.size, tuple(ceil(value * dpi / 72) for value in size))
                        self.assertLessEqual(image.width * image.height, 80_000)


class PixelAndPNGBudgetTests(TestCase):
    def test_high_level_feedback_reduces_effective_dpi_and_releases_images(self):
        with TemporaryDirectory() as folder, patch('pdf_craft.pdf.poppler.MAX_PAGE_PIXELS', 80_000):
            root = Path(folder)
            source, target = root / 'source.pdf', root / 'page.png'
            canvas = Canvas(str(source), pagesize=(200, 300))
            canvas.drawString(20, 200, 'Synthetic page')
            canvas.save()
            document = DefaultPDFHandler().open(source)
            images, resolutions = [], []
            native_render = document.render_page

            def render(page_index, dpi):
                resolutions.append(dpi)
                image = native_render(page_index, dpi)
                images.append(image)
                return image

            with native_render(1, 300) as initial:
                initial.save(target)
                limit = target.stat().st_size * 3 // 4
            with patch.object(document, 'render_page', side_effect=render):
                result = PageRef(document, 1).render(300, limit, png_path=target)
            with result:
                measured_size = result.size
                self.assertLessEqual(target.stat().st_size, limit)
                self.assertLessEqual(len(resolutions), 5)
                self.assertGreater(len(resolutions), 1)
                self.assertLess(resolutions[0], 300)
                self.assertTrue(all(a > b for a, b in zip(resolutions, resolutions[1:])))
                self.assertTrue(all(a.width > b.width for a, b in zip(images, images[1:])))
                with Image.open(target) as stored:
                    self.assertEqual(stored.size, result.size)
            result.close()
            for image in images:
                with self.assertRaises(ValueError):
                    image.getpixel((0, 0))

            ocr = OCR(DeepSeekOCRLocalConfig(), DefaultPDFHandler())
            def recognize(**kwargs):
                self.assertEqual(kwargs['image'].size, measured_size)
                return Page(1, None, [], [], 0, 0)
            with patch.object(ocr._extractor, 'image2page', side_effect=recognize) as extract:
                list(ocr.recognize(pdf_path=source, asset_path=root / 'assets',
                                   ocr_path=root / 'ocr', max_page_image_file_size=limit))
                extract.assert_called_once()
            sizes = json.loads((root / 'ocr/page_pixel_sizes.json').read_text())
            self.assertEqual(sizes['1'], list(measured_size))
            recovered = ocr.recover_page_image(source, root / 'assets', 1, 300)
            self.assertEqual(recovered.body_layouts[0].det, (0, 0, *measured_size))
            self.assertEqual(ocr.last_page_pixel_sizes[1], measured_size)
            # Erasure maps stored OCR pixels onto the independently capped raster.
            width, height = measured_size
            region = PDFReplacementRegion(1, (0, 0, width, height), measured_size, 300)
            with native_render(1, 300) as raster:
                masks = RectangularEraser(EraseOptions(padding=0)).plan(
                    [region], {1: (200, 300)}, {1: raster})
            self.assertAlmostEqual(masks[0].rectangle.width, 200)
            self.assertAlmostEqual(masks[0].rectangle.height, 300)

    def test_cancellation_and_exhaustion_stay_bounded(self):
        with TemporaryDirectory() as folder, patch('pdf_craft.pdf.poppler.MAX_PAGE_PIXELS', 80_000):
            source = Path(folder) / 'source.pdf'
            with PdfWriter() as writer:
                writer.add_blank_page(200, 300)
                writer.write(source)
            document = DefaultPDFHandler().open(source)
            with patch.object(document, 'render_dpi') as preflight:
                with self.assertRaises(ExtractionAbortedError):
                    PageRef(document, 1).render(300, aborted=lambda: True)
                preflight.assert_not_called()
            with patch.object(document, 'render_page', wraps=document.render_page) as render:
                with self.assertRaises(PDFError) as raised:
                    PageRef(document, 1).render(300, 1)
                self.assertIsInstance(raised.exception.__cause__, PNGSizeError)
                self.assertLessEqual(render.call_count, 5)
