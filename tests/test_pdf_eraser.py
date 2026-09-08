import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw

from pdf_craft.pipeline.pdf import EraseOptions, PDFReplacementRegion, RectangularEraser


class TestRectangularEraser(unittest.TestCase):
    def test_samples_overlapping_regions_from_one_unmodified_page_image(self):
        """Planning and drawing one mask must not affect another mask's sample."""
        beige = (234, 220, 183)
        charcoal = (4, 7, 9)
        page_image = Image.new("RGB", (100, 100), beige)
        # The regions overlap at (40, 40)-(50, 50). The first still has a
        # beige majority after padding; the second has a charcoal majority.
        ImageDraw.Draw(page_image).rectangle((40, 40, 99, 99), fill=charcoal)
        first = PDFReplacementRegion(1, (0, 0, 50, 50), (100, 100))
        second = PDFReplacementRegion(1, (40, 40, 90, 90), (100, 100))
        eraser = RectangularEraser(EraseOptions(padding=5))

        planned = eraser.plan(
            (first, second), {1: (200, 200)}, {1: page_image},
        )

        self.assertEqual([erase.color for erase in planned], [beige, charcoal])
        # First padding clips at the page edge; second keeps its full padding.
        self.assertEqual(
            (planned[0].rectangle.x, planned[0].rectangle.top,
             planned[0].rectangle.width, planned[0].rectangle.height),
            (0, 0, 110, 110),
        )
        self.assertEqual(
            (planned[1].rectangle.x, planned[1].rectangle.top,
             planned[1].rectangle.width, planned[1].rectangle.height),
            (70, 70, 120, 120),
        )

        overlay = Mock()
        eraser.draw(overlay, (planned[0],), page_height=200)
        self.assertEqual(page_image.getpixel((10, 10)), beige)
        self.assertEqual(page_image.getpixel((45, 45)), charcoal)
        # Re-planning the overlapping second box after drawing the first mask
        # yields the same source-page color: the overlay never contaminates
        # source pixels used by subsequent estimates.
        replanned_second = eraser.plan(
            (second,), {1: (200, 200)}, {1: page_image},
        )[0]
        self.assertEqual(replanned_second.color, charcoal)
        self.assertEqual(replanned_second.rectangle, planned[1].rectangle)

    def test_plans_padded_beige_mask_from_the_unmodified_page_image(self):
        page_image = Image.new("RGB", (100, 200), (234, 220, 183))
        drawing = ImageDraw.Draw(page_image)
        # Simulate multiple dark source lines. They remain a minority of the
        # padded rectangle, so the background RGB should retain its beige hue.
        drawing.rectangle((12, 25, 45, 28), fill=(20, 20, 20))
        drawing.rectangle((12, 36, 45, 39), fill=(20, 20, 20))
        region = PDFReplacementRegion(2, (10, 20, 50, 70), (100, 200))

        planned = RectangularEraser(EraseOptions(padding=3)).plan(
            (region,), {2: (300, 400)}, {2: page_image},
        )

        self.assertEqual(len(planned), 1)
        erase = planned[0]
        self.assertEqual(erase.page_index, 2)
        self.assertEqual(erase.color, (234, 220, 183))
        self.assertEqual(erase.rectangle.x, 21)
        self.assertEqual(erase.rectangle.top, 34)
        self.assertEqual(erase.rectangle.width, 138)
        self.assertEqual(erase.rectangle.height, 112)
        self.assertEqual(page_image.getpixel((20, 26)), (20, 20, 20))

    def test_uses_black_background_for_light_text(self):
        page_image = Image.new("RGB", (100, 100), (4, 7, 9))
        drawing = ImageDraw.Draw(page_image)
        drawing.rectangle((25, 30, 74, 34), fill=(245, 245, 245))
        region = PDFReplacementRegion(1, (20, 20, 80, 80), (100, 100))

        erase = RectangularEraser().plan((region,), {1: (100, 100)}, {1: page_image})[0]

        self.assertEqual(erase.color, (4, 7, 9))

    def test_clips_padding_at_page_edges_and_rejects_negative_padding(self):
        page_image = Image.new("RGB", (100, 100), (210, 200, 170))
        region = PDFReplacementRegion(1, (0, 0, 10, 10), (100, 100))

        erase = RectangularEraser(EraseOptions(padding=20)).plan(
            (region,), {1: (100, 100)}, {1: page_image},
        )[0]

        self.assertEqual(erase.rectangle.x, 0)
        self.assertEqual(erase.rectangle.top, 0)
        self.assertEqual(erase.rectangle.width, 30)
        self.assertEqual(erase.rectangle.height, 30)
        with self.assertRaisesRegex(ValueError, "padding"):
            EraseOptions(padding=-1)

    def test_noise_uses_a_stable_background_color_not_one_random_pixel(self):
        page_image = Image.new("RGB", (20, 20), (231, 214, 170))
        pixels = page_image.load()
        assert pixels is not None
        for y in range(20):
            for x in range(20):
                if (x + y) % 3 == 0:
                    pixels[x, y] = (232, 215, 171)
                elif (x + y) % 3 == 1:
                    pixels[x, y] = (230, 213, 169)
        region = PDFReplacementRegion(1, (0, 0, 20, 20), (20, 20))

        erase = RectangularEraser().plan((region,), {1: (20, 20)}, {1: page_image})[0]

        self.assertEqual(erase.color, (231, 214, 170))
