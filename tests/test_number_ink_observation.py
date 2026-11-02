"""Short numeral ink evidence never changes the vendor's source rectangle."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from xml.etree.ElementTree import fromstring
import unittest
from PIL import Image, ImageDraw
from pdf_craft.pdf.line_geometry import number_ink_bbox
from pdf_craft.pdf.types import Page, PageLayout, encode, decode
from pdf_craft.extractor.chapter.primary import project_window
from pdf_craft.extractor.chapter.page_review import build_jev_review_states


class NumberInkObservationTests(unittest.TestCase):
    def test_fragments_of_one_glyph_can_share_a_sparse_number_observation(self):
        with Image.new('RGB', (200, 100), 'white') as image:
            draw = ImageDraw.Draw(image)
            draw.rectangle((95, 60, 103, 66), fill='black')
            draw.rectangle((95, 69, 103, 75), fill='black')
            self.assertEqual(number_ink_bbox(image, (0, 0, 200, 100), '3'), (95, 60, 104, 76))
            self.assertIsNone(number_ink_bbox(image, (0, 0, 200, 100), 'many words'))
            self.assertIsNone(number_ink_bbox(image, (0, 0, 200, 100), '2024'))

    def test_separate_lines_noise_and_invalid_or_tight_crops_remain_unknown(self):
        with Image.new('RGB', (200, 100), 'white') as image:
            draw = ImageDraw.Draw(image)
            draw.rectangle((95, 10, 103, 20), fill='black')
            draw.rectangle((95, 70, 103, 80), fill='black')
            self.assertIsNone(number_ink_bbox(image, (0, 0, 200, 100), '3'))
            self.assertIsNone(number_ink_bbox(image, (-1, 0, 200, 100), '3'))
        with Image.new('RGB', (200, 100), 'white') as image:
            draw = ImageDraw.Draw(image)
            draw.rectangle((95, 60, 103, 75), fill='black')
            draw.line((0, 95, 199, 95), fill='black')
            self.assertIsNone(number_ink_bbox(image, (0, 0, 200, 100), '3'))
            self.assertIsNone(number_ink_bbox(image, (95, 60, 104, 76), '3'))

    def test_measurement_is_cached_and_used_without_moving_source_or_losing_review_evidence(self):
        raw = Page(1, None, [PageLayout('text', (20, 10, 180, 120),
            'A substantial complete reading paragraph. ' * 12, 0, None)],
            [PageLayout('text', (0, 121, 200, 200), '3', 0, None,
                        number_ink_bbox=(95, 170, 104, 186))], 0, 0)
        before = deepcopy(raw)
        restored = decode(encode(raw))
        self.assertEqual(restored.footnotes_layouts[0].number_ink_bbox, (95, 170, 104, 186))
        page = project_window([restored], True)[0]
        assert page is not None
        note = page.layouts[-1]
        self.assertEqual(note.ownership, 'ignored')
        self.assertEqual(note.source.bbox, raw.footnotes_layouts[0].det)
        self.assertEqual(raw, before)
        _, packet = next(iter(build_jev_review_states([restored], [page], {1: (200, 200)})))
        fact = packet['target_page']['layouts'][-1]
        self.assertEqual(fact['bbox_normalized'], [0, .605, 1, 1])
        self.assertEqual(fact['number_ink_bbox_normalized'], [.475, .85, .52, .93])

    def test_regular_page_uses_unmasked_first_pass_without_retaining_a_cover_image(self):
        from doc_page_extractor.types import Layout, LayoutKind, OCRPageResult
        from doc_page_extractor.structure import build_structured_page
        from pdf_craft import DeepSeekOCRVendorConfig
        from pdf_craft.common import AssetHub
        from pdf_craft.pdf.page_extractor import PageExtractorNode
        node = PageExtractorNode(DeepSeekOCRVendorConfig('https://unused.invalid', 'unused', 'unused'))
        layout = Layout((0, 0, 200, 100), '3', kind=LayoutKind.TEXT)
        first = OCRPageResult([], 'test', structured=build_structured_page([]))
        second = OCRPageResult([layout], 'test', structured=build_structured_page([layout]))
        with Image.new('RGB', (200, 100), 'white') as image, image.copy() as masked, TemporaryDirectory() as directory:
            ImageDraw.Draw(image).rectangle((95, 60, 103, 75), fill='black')
            page = node.results2page(results=[(image, first), (masked, second)], page_index=2,
                asset_hub=AssetHub(Path(directory)), includes_footnotes=True, raw_image=None,
                plot_path=None, input_tokens=0, output_tokens=0, aborted=lambda: False)
        self.assertIsNone(page.image)
        self.assertEqual(page.footnotes_layouts[0].number_ink_bbox, (95, 60, 104, 76))

    def test_oversized_crops_are_rejected_before_allocation(self):
        image = Mock(mode='RGB', width=3000, height=2000)
        self.assertIsNone(number_ink_bbox(image, (0, 0, 3000, 2000), '3'))
        image.crop.assert_not_called()

    def test_cached_observation_outside_its_source_is_rejected(self):
        root = fromstring('<page index="1"><body/><footnotes><layout ref="text" det="0,0,100,100" '
                          'number_ink_bbox="0,0,101,50">3</layout></footnotes></page>')
        with self.assertRaisesRegex(ValueError, 'inside'):
            decode(root)
