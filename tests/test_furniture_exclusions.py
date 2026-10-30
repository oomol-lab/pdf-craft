"""Adopted exclusion is not unclaimed native text for furniture to recover."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from xml.etree.ElementTree import fromstring

from reportlab.pdfgen.canvas import Canvas

from pdf_craft.common import save_xml
from pdf_craft.extractor.commit import project_adopted
from pdf_craft.pdf.furniture import extract_furnitures


class FurnitureExclusionTests(TestCase):
    def test_hidden_search_layer_does_not_become_visible_furniture(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root/'source.pdf'
            canvas = Canvas(str(pdf), pagesize=(200, 200))
            canvas.setFont('Helvetica', 10)
            canvas.drawString(10, 180, 'Visible header')
            hidden = canvas.beginText(10, 40)
            hidden.setTextRenderMode(3)
            hidden.textLine('Invisible old OCR note')
            canvas.drawText(hidden)
            canvas.save()
            ocr = root/'ocr'
            ocr.mkdir()
            save_xml(fromstring('<page index="1"><body/><footnotes/></page>'), ocr/'page_1.xml')
            (ocr/'page_pixel_sizes.json').write_text(json.dumps({'1': [200, 200]}))
            text = ''.join(extract_furnitures(pdf, ocr).itertext())
            self.assertIn('Visible header', text)
            self.assertNotIn('Invisible old OCR note', text)

    def test_excluded_text_stays_out_while_unclaimed_native_header_survives(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, chapters = root/'ocr', root/'chapters'
            evidence.mkdir(); chapters.mkdir()
            pdf = root/'source.pdf'
            canvas = Canvas(str(pdf), pagesize=(200, 200))
            canvas.setFont('Helvetica', 10)
            canvas.drawString(10, 180, 'Native running header')
            canvas.drawString(10, 100, 'Adopted body')
            canvas.drawString(10, 40, 'Explicitly excluded note')
            canvas.drawString(10, 10, 'Disabled footnote candidate')
            canvas.save()
            save_xml(fromstring('<page index="1"><body>'
                                '<layout ref="text" det="5,80,195,110">Adopted body</layout>'
                                '<layout ref="text" det="5,145,195,175">Explicitly excluded note</layout>'
                                '</body><footnotes><layout ref="text" det="5,175,195,198">'
                                'Disabled footnote candidate</layout></footnotes></page>'), evidence/'page_1.xml')
            (evidence/'page_pixel_sizes.json').write_text(json.dumps({'1': [200, 200]}))
            save_xml(fromstring('<chapter><flow><text role="body"><fragment page_index="1" '
                                'source_order="0" bbox="5,80,195,110">Adopted body</fragment>'
                                '</text></flow></chapter>'), chapters/'chapter_head.xml')
            project_adopted(chapters, evidence, root/'projection', [])
            result = extract_furnitures(pdf, root/'projection')
            text = ''.join(result.itertext())
            self.assertIn('Native running header', text)
            self.assertNotIn('Adopted body', text)
            self.assertNotIn('Explicitly excluded note', text)
            self.assertNotIn('Disabled footnote candidate', text)
            self.assertIn('Explicitly excluded note', (evidence/'page_1.xml').read_text())
