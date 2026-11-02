"""A fragmented ruled form stays artwork; prose and structured tables survive."""
from pathlib import Path
import tempfile
import unittest
from xml.etree.ElementTree import parse

from PIL import Image, ImageDraw

from pdf_craft.common import AssetHub
from pdf_craft.pdf import PageLayout, decode
from pdf_craft.pdf.ruled_forms import preserve_ruled_forms, ruled_regions


def grid():
    image = Image.new('RGB', (800, 1000), 'white')
    draw = ImageDraw.Draw(image)
    for x in (100, 300, 500, 700):
        draw.line((x, 200, x, 800), fill='black', width=2)
    for y in (200, 400, 600, 800):
        draw.line((100, y, 700, y), fill='black', width=2)
    return image


def fields():
    return [PageLayout('text', box, 'Field', i, None) for i, box in enumerate([
        (110, 220, 220, 240), (310, 220, 420, 240),
        (110, 420, 220, 440), (310, 420, 420, 440),
    ])]


class RuledFormTests(unittest.TestCase):
    def test_preserves_grid_as_an_asset_and_keeps_outside_prose(self):
        outside = PageLayout('text', (100, 50, 700, 100), 'Ordinary prose.', 4, None)
        with grid() as image, tempfile.TemporaryDirectory() as directory:
            body, notes = preserve_ruled_forms(image, fields() + [outside], [], AssetHub(Path(directory)))
            self.assertEqual([item.ref for item in body], ['image', 'text'])
            self.assertEqual(body[1].text, outside.text)
            self.assertEqual(notes, [])
            with Image.open(Path(directory) / f'{body[0].hash}.png') as crop:
                self.assertEqual(crop.getpixel((300 - body[0].det[0], 500 - body[0].det[1])), (0, 0, 0))

    def test_does_not_reclassify_a_structured_table(self):
        table = PageLayout('table', (100, 200, 700, 800), '<table>cells</table>', 0, 'existing')
        with grid() as image, tempfile.TemporaryDirectory() as directory:
            body, _ = preserve_ruled_forms(image, [table], [], AssetHub(Path(directory)))
            self.assertEqual(body, [table])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_does_not_hide_crossing_prose_or_requirements_outside_grid(self):
        crossing = PageLayout('text', (0, 100, 780, 500), 'A paragraph crossing the grid.', 4, None)
        with grid() as image, tempfile.TemporaryDirectory() as directory:
            source = fields() + [crossing]
            body, _ = preserve_ruled_forms(image, source, [], AssetHub(Path(directory)))
            self.assertEqual(body, source)

    def test_horizontal_rules_without_cells_are_not_forms(self):
        with Image.new('RGB', (800, 1000), 'white') as image:
            draw = ImageDraw.Draw(image)
            for y in (200, 400, 600, 800):
                draw.line((100, y, 700, y), fill='black', width=2)
            self.assertEqual(ruled_regions(image), [])

    def test_real_ocr2_form_is_preserved_without_matching_its_words(self):
        from pdf2image import convert_from_path
        root = Path(__file__).parent / 'assets'
        page = decode(parse(root / 'analysis/ocr2_form/page_4.xml').getroot())
        images = convert_from_path(root / 'pdf/table&formula.pdf', dpi=300, first_page=4, last_page=4)
        with images[0] as image, tempfile.TemporaryDirectory() as directory:
            body, notes = preserve_ruled_forms(image, page.body_layouts, page.footnotes_layouts, AssetHub(Path(directory)))
            self.assertEqual([item.ref for item in body], ['image', 'image'])
            self.assertEqual(notes, [])
