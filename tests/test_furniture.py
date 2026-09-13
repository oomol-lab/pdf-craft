import tempfile
import unittest
from pathlib import Path

from pdf_craft.pdf.furniture import extract_furnitures


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
            (ocr / "page_1.xml").write_text("<page><body><layout det='10,40,190,190'>body</layout></body></page>", encoding="utf-8")
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
