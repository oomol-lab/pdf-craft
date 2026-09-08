import unittest

from pdf_craft.pipeline.pdf import PDFReplacementRegion, RectangularEraser


class TestRectangularEraser(unittest.TestCase):
    def test_plans_visual_masks_without_any_text_input(self):
        region = PDFReplacementRegion(2, (10, 20, 50, 70), (100, 200))

        planned = RectangularEraser().plan((region,), {2: (300, 400)})

        self.assertEqual(len(planned), 1)
        erase = planned[0]
        self.assertEqual(erase.page_index, 2)
        self.assertEqual(erase.rectangle.x, 30)
        self.assertEqual(erase.rectangle.top, 40)
        self.assertEqual(erase.rectangle.width, 120)
        self.assertEqual(erase.rectangle.height, 100)
