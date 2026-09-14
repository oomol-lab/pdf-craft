import tempfile
import unittest
from pathlib import Path

from pdf_craft.pdf.furniture import FurnitureSection, _discover_patterns, _similar, extract_furnitures


class FurnitureTests(unittest.TestCase):
    def test_native_text_is_kept_when_ocr_has_no_matching_box(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "source.pdf"
            from pypdf import PdfWriter
            from pypdf.generic import DecodedStreamObject, NameObject
            writer = PdfWriter()
            page = writer.add_blank_page(200, 200)
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 20 170 Td (RUNNING HEAD) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)  # pylint: disable=protected-access
            with pdf.open("wb") as output:
                writer.write(output)
            ocr = root / "ocr"
            ocr.mkdir()
            (ocr / "page_1.xml").write_text("<page><body><layout det='10,0,190,20'>body</layout></body></page>", encoding="utf-8")
            output = extract_furnitures(pdf, ocr)
            section = output.find("pages/page/section")
            assert section is not None
            self.assertTrue(section.text)

    def test_ocr_covered_native_text_is_filtered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "source.pdf"
            from pypdf import PdfWriter
            from pypdf.generic import DecodedStreamObject, NameObject
            writer = PdfWriter()
            page = writer.add_blank_page(200, 200)
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 20 170 Td (BODY) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)  # pylint: disable=protected-access
            with pdf.open("wb") as output:
                writer.write(output)
            ocr = root / "ocr"
            ocr.mkdir()
            (ocr / "page_1.xml").write_text("<page><body><layout det='0,0,1000,1000'>body</layout></body></page>", encoding="utf-8")
            output = extract_furnitures(pdf, ocr)
            self.assertIsNone(output.find("pages/page/section"))

    def test_three_page_track_creates_one_universal_position(self):
        pages = {
            index: [FurnitureSection(index, (10, 10, 100, 30), "Header")]
            for index in range(1, 5)
        }
        patterns = _discover_patterns(pages)
        universal = next(pattern for pattern in patterns if pattern.kind == "universal")
        self.assertEqual(universal.positions[0].content, "Header")
        self.assertEqual([s.page_index for s in universal.positions[0].sections], [1, 2, 3, 4])

    def test_internal_fragment_topology_is_part_of_matching(self):
        left = FurnitureSection(1, (0, 0, 100, 40), "A B", fragments=(
            (0.0, 0.0, 0.45, 0.5, "A"), (0.55, 0.0, 0.45, 0.5, "B")))
        right = FurnitureSection(2, (2, 1, 102, 41), "A B", fragments=(
            (0.0, 0.0, 0.45, 0.5, "A"), (0.55, 0.0, 0.45, 0.5, "B")))
        different = FurnitureSection(2, (2, 1, 102, 41), "A B", fragments=(
            (0.0, 0.0, 0.45, 0.5, "B"), (0.55, 0.0, 0.45, 0.5, "A")))
        self.assertTrue(_similar(left, right))
        self.assertFalse(_similar(left, different))

    def test_canonical_content_tie_uses_first_match(self):
        pages = {
            1: [FurnitureSection(1, (10, 10, 100, 30), "first", fragments=((0, 0, 1, 1, "x"),))],
            2: [FurnitureSection(2, (10, 10, 100, 30), "second", fragments=((0, 0, 1, 1, "x"),))],
            3: [FurnitureSection(3, (10, 10, 100, 30), "first", fragments=((0, 0, 1, 1, "x"),))],
        }
        pattern = next(p for p in _discover_patterns(pages) if p.kind == "universal")
        self.assertEqual(pattern.positions[0].content, "first")
