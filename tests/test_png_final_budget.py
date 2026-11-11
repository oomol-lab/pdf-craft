"""Late PNG fallback preserves real pixels with a deterministic byte budget."""
# pylint: disable=protected-access,cell-var-from-loop
from io import BytesIO
from pathlib import Path
import random
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image

from pdf_craft.pdf import png
from tests.test_ocr_image_size import noisy


class FinalPNGBudgetTests(TestCase):
    def test_stage_four_reduces_more_than_early_corrections(self):
        early = png._next_resolution([(300, 100001)], 100000)
        late = png._next_resolution([(400, 130000), (350, 110000), (300, 100001)], 100000)
        self.assertGreater(early, 290)
        self.assertLessEqual(late, 225)
        # A locally measured shallow slope predicts a larger correction than
        # the square model, but early attempts still protect at least 80% DPI.
        measured = png._next_resolution([(205, 15388907), (164, 13200000)], 10000000)
        self.assertEqual(measured, 131)
        self.assertLess(png._next_resolution([(205, 15388907), (164, 13200000), (131, 11000000)], 10000000), 100)

    def test_final_encoding_is_decodable_and_bound_includes_rows_and_blocks(self):
        for size, channels in [((1, 70000), 3), ((70000, 1), 4), ((512, 512), 3)]:
            with self.subTest(size=size), Image.frombytes(
                    'RGBA' if channels == 4 else 'RGB', size,
                    random.Random(92).randbytes(size[0] * size[1] * channels)) as image:
                data = png._stored_png(image, lambda: False)
                self.assertEqual(len(data), png._png_bound(size, channels))
                with Image.open(BytesIO(data)) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.mode, image.mode)
                    self.assertEqual(decoded.tobytes(), image.tobytes())

    def test_fifth_candidate_preserves_alpha_and_whole_image_without_extra_encoding(self):
        sources = []
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'page.png'
            def render(_dpi):
                image = noisy((180, 240)).convert('RGBA')
                image.putalpha(64)
                sources.append(image)
                return image
            save = Image.Image.save
            saves = []
            def encode(image, *args, **kwargs):
                saves.append(image.size)
                return save(image, *args, **kwargs)
            with patch.object(Image.Image, 'save', encode), patch.object(png, '_stored_png', wraps=png._stored_png) as final:
                image = png.bounded_png(render, 300, 12000, path)
            try:
                self.assertEqual(len(sources), 5)
                self.assertEqual(len(saves), 4)
                self.assertEqual(final.call_count, 1)
                self.assertLessEqual(path.stat().st_size, 12000)
                self.assertAlmostEqual(image.width / image.height, 180 / 240, delta=0.02)
                with Image.open(path) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.tobytes(), image.tobytes())
                    self.assertEqual(decoded.getchannel('A').getextrema(), (64, 64))
                # Compare the entire downscaled source, not just a corner or an
                # empty placeholder with the right dimensions.
                with noisy((180, 240)) as original, original.convert('RGBA') as expected:
                    expected.putalpha(64)
                    with expected.resize(image.size) as resized:
                        self.assertEqual(image.tobytes(), resized.tobytes())
                for source in sources:
                    with self.assertRaises(ValueError):
                        source.getpixel((0, 0))
            finally:
                image.close()

    def test_final_conversion_keeps_palette_transparency_and_16bit_gray(self):
        with Image.new('P', (30, 40)) as palette:
            palette.putpalette([0, 80, 160] * 256)
            palette.info['transparency'] = 0
            palette.info['icc_profile'] = b'x' * 20000
            with png._final_image(palette, 1000) as image:
                self.assertEqual(image.mode, 'RGBA')
                self.assertEqual(image.getchannel('A').getextrema(), (0, 0))
                self.assertEqual(image.info, {})
                self.assertLessEqual(len(png._stored_png(image, lambda: False)), 1000)
        with Image.new('I;16', (10, 10)) as source:
            source.putpixel((0, 0), 60000)
            source.info['transparency'] = 60000
            with png._final_image(source, 1000) as image:
                data = png._stored_png(image, lambda: False)
                with Image.open(BytesIO(data)) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.getpixel((0, 0)), 60000)
                    self.assertEqual(decoded.info.get('transparency'), 60000)

    def test_thin_page_final_dimensions_include_per_row_filter_bytes(self):
        calls = []
        def render(_dpi):
            calls.append(_dpi)
            return noisy((1, 30000))
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'thin.png'
            image = png.bounded_png(render, 300, 10000, path)
            try:
                self.assertEqual(len(calls), 5)
                self.assertEqual(image.width, 1)
                self.assertGreater(image.height, 1)
                self.assertLessEqual(path.stat().st_size, 10000)
                with Image.open(path) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.size, image.size)
            finally:
                image.close()

    def test_error_and_cancellation_in_final_encoder_close_all_candidates(self):
        for error in (OSError('write failed'), RuntimeError('cancelled')):
            sources = []
            def render(_dpi):
                image = noisy((100, 100))
                sources.append(image)
                return image
            with patch.object(png, '_stored_png', side_effect=error):
                with self.assertRaises(type(error)):
                    png.bounded_png(render, 300, 1000)
            self.assertEqual(len(sources), 5)
            for source in sources:
                with self.assertRaises(ValueError):
                    source.getpixel((0, 0))
