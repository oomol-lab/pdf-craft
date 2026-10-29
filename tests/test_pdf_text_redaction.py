"""Character removal must preserve unmasked text positions and source graphics."""
import asyncio
from pathlib import Path
from io import BytesIO
from typing import Any
import unittest
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar, LTContainer
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject,
    NameObject, NumberObject,
)
from PIL import Image
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from pdf_craft.pipeline.pdf.original_base import OriginalVisualBaseCompiler
from pdf_craft.pipeline.pdf.eraser import EraseRectangle
from pdf_craft.pipeline.pdf.geometry import PageRectangle
from pdf_craft.pipeline.pdf.text_redaction import remove_masked_text


def _pdf(draw):
    data = BytesIO()
    canvas = Canvas(data, pagesize=(200, 200))
    draw(canvas)
    canvas.save()
    data.seek(0)
    writer = PdfWriter()
    page: Any = writer.add_page(PdfReader(data).pages[0])
    return writer, page


def _chars(writer):
    data = BytesIO()
    writer.write(data)
    data.seek(0)
    result = []

    def visit(node):
        if isinstance(node, LTChar):
            result.append((node.get_text(), node.bbox))
        elif isinstance(node, LTContainer):
            for child in node:
                visit(child)
    for page in extract_pages(data):
        visit(page)
    return result


def _erase(writer, page, left, bottom, right, top):
    return remove_masked_text(writer, page, (
        EraseRectangle(1, PageRectangle(left, 200-top, right-left, top-bottom), (255, 255, 255)),
    ), 200)


class PDFTextRedactionTests(unittest.TestCase):
    def test_extgstate_font_size_controls_character_removal(self):
        writer, page = _pdf(lambda c: c.drawString(20, 100, 'AB'))
        resources = page['/Resources']
        font = resources['/Font'].raw_get('/F1')
        resources[NameObject('/ExtGState')] = DictionaryObject({
            NameObject('/GS'): DictionaryObject({
                NameObject('/Font'): ArrayObject([font, NumberObject(40)]),
            }),
        })
        content = ContentStream(None, writer)
        content.set_data(b'BT /F1 12 Tf /GS gs 1 0 0 1 20 100 Tm (AB) Tj ET')
        page.replace_contents(content)
        # Helvetica 40pt: A ends at x=46.68; this mask intersects B only.
        self.assertEqual(_erase(writer, page, 50, 95, 60, 125), 1)
        self.assertEqual(page.extract_text().strip(), 'A')

    @unittest.skipUnless(shutil.which('pdftoppm'), 'requires Poppler')
    def test_hidden_full_page_stamp_stays_invisible(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        appearance = DecodedStreamObject()
        appearance.set_data(b'1 0 0 rg 0 0 200 200 re f')
        rectangle = ArrayObject([NumberObject(v) for v in (0, 0, 200, 200)])
        appearance.update({
            NameObject('/Type'): NameObject('/XObject'),
            NameObject('/Subtype'): NameObject('/Form'),
            NameObject('/BBox'): rectangle,
            NameObject('/Resources'): DictionaryObject(),
        })
        annotation = DictionaryObject({
            NameObject('/Type'): NameObject('/Annot'),
            NameObject('/Subtype'): NameObject('/Stamp'),
            NameObject('/Rect'): rectangle,
            NameObject('/F'): NumberObject(2),  # Hidden, regardless of print/view.
            NameObject('/AP'): DictionaryObject({NameObject('/N'): writer._add_object(appearance)}),
        })
        page[NameObject('/Annots')] = ArrayObject([writer._add_object(annotation)])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source.pdf', root / 'target.pdf'
            writer.write(source)
            asyncio.run(OriginalVisualBaseCompiler().compile(source, target))
            pixels = []
            for pdf in (source, target):
                prefix = pdf.with_suffix('')
                subprocess.run(['pdftoppm', '-singlefile', '-scale-to', '100', '-png',
                                str(pdf), str(prefix)], check=True, capture_output=True)
                with Image.open(prefix.with_suffix('.png')) as image:
                    pixels.append(image.convert('RGB').getpixel((50, 50)))
            self.assertEqual(pixels[0], (255, 255, 255))
            self.assertEqual(pixels[1], pixels[0])

    def test_original_base_preserves_display_list(self):
        def draw(canvas):
            canvas.rect(10, 10, 100, 100, fill=True)
            canvas.drawString(20, 100, 'KEEP')
        writer, page = _pdf(draw)
        before = page.get_contents().operations
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source.pdf', Path(directory) / 'target.pdf'
            writer.write(source)
            asyncio.run(OriginalVisualBaseCompiler().compile(source, target))
            result = next(iter(PdfReader(target).pages))
            self.assertEqual(result.extract_text().strip(), 'KEEP')
            content = result.get_contents()
            assert content is not None
            self.assertEqual(content.operations[1:-1], before)

    def test_touching_character_deleted_without_moving_neighbors(self):
        writer, page = _pdf(lambda c: c.drawString(20, 100, 'ABCDE'))
        before = _chars(writer)
        # Zero-width mask at A's right edge touches A and B, not C.
        right = before[0][1][2]
        self.assertEqual(_erase(writer, page, right, 90, right, 115), 2)
        self.assertEqual(page.extract_text().strip(), 'CDE')
        after = _chars(writer)
        for old, new in zip(before[2:], after):
            self.assertEqual(old[0], new[0])
            for expected, actual in zip(old[1], new[1]):
                self.assertAlmostEqual(expected, actual, places=5)

    def test_invisible_ocr_is_removed(self):
        def draw(canvas):
            text = canvas.beginText(20, 100)
            text.setTextRenderMode(3)
            text.textOut('HIDDEN')
            canvas.drawText(text)
            canvas.drawString(20, 150, 'KEEP')
        writer, page = _pdf(draw)
        self.assertEqual(_erase(writer, page, 0, 90, 190, 115), 6)
        self.assertEqual(page.extract_text().strip(), 'KEEP')

    def test_shared_nested_form_is_edited_per_invocation(self):
        def draw(canvas):
            canvas.beginForm('inner')
            canvas.drawString(20, 20, 'FORM')
            canvas.endForm()
            canvas.beginForm('outer')
            canvas.doForm('inner')
            canvas.endForm()
            canvas.doForm('outer')
            canvas.translate(0, 100)
            canvas.doForm('outer')
        writer, page = _pdf(draw)
        self.assertEqual(_erase(writer, page, 0, 10, 100, 35), 4)
        self.assertEqual(page.extract_text().count('FORM'), 1)
        self.assertGreater(_chars(writer)[0][1][1], 100)

    @unittest.skipUnless(shutil.which("pdftotext"), "requires Poppler")
    def test_spacing_and_transformed_text_stay_in_place(self):
        def draw(canvas):
            canvas.translate(10, 10)
            canvas.rotate(15)
            text = canvas.beginText(20, 100)
            text.setCharSpace(2)
            text.setWordSpace(20)
            text.setHorizScale(80)
            text.textOut('A BCD')
            canvas.drawText(text)
        writer, page = _pdf(draw)
        def words():
            # Poppler independently checks positions. pdfminer inserts a spurious
            # Tc before strings preceded by a TJ number, so it is not an oracle
            # for this particular positioning boundary.
            with tempfile.TemporaryDirectory() as directory:
                source = f'{directory}/input.pdf'
                writer.write(source)
                xml = subprocess.check_output(['pdftotext', '-bbox', source, '-'])
                root = ET.fromstring(xml)
                return {node.text: tuple(float(node.attrib[k]) for k in ('xMin', 'yMin', 'xMax', 'yMax'))
                        for node in root.iter() if node.tag.endswith('}word')}
        before = words()
        box = _chars(writer)[0][1]
        _erase(writer, page, *box)
        after = words()
        self.assertIn('BCD', after)
        for actual, expected in zip(after['BCD'], before['BCD']):
            self.assertAlmostEqual(actual, expected, places=4)

    def test_actual_text_removed_with_colliding_glyphs(self):
        writer, page = _pdf(lambda c: c.drawString(20, 100, 'SECRET'))
        content = page.get_contents()
        content.set_data(b'/Span << /ActualText (SECRET) /Alt (SECRET) >> BDC\n' + content.get_data() + b'\nEMC')
        page.replace_contents(content)
        self.assertEqual(_erase(writer, page, 0, 90, 190, 115), 6)
        self.assertNotIn(b'SECRET', page.get_contents().get_data())
        self.assertNotIn(b'/ActualText', page.get_contents().get_data())

    def test_graphics_operators_remain_unchanged(self):
        def draw(canvas):
            canvas.rect(10, 10, 180, 180, fill=True)
            canvas.drawString(20, 100, 'DELETE')
        writer, page = _pdf(draw)
        original = page.get_contents().operations
        _erase(writer, page, 0, 90, 190, 115)
        remaining = page.get_contents().operations
        for operation in original:
            if operation[1] in (b're', b'f*', b'B*'):
                self.assertIn(operation, remaining)

    def test_two_byte_cid_font_retains_original_codes(self):
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        def draw(canvas):
            canvas.setFont('STSong-Light', 12)
            canvas.drawString(20, 100, '中文测试')
        writer, page = _pdf(draw)
        before = _chars(writer)
        self.assertEqual(_erase(writer, page, 21, 95, 22, 110), 1)
        after = _chars(writer)
        self.assertEqual(''.join(char for char, _ in after), '文测试')
        for old, new in zip(before[1:], after):
            for expected, actual in zip(old[1], new[1]):
                self.assertAlmostEqual(expected, actual, places=5)

    def test_quote_operators_keep_line_and_spacing_effects(self):
        writer, page = _pdf(lambda c: c.drawString(20, 100, 'BASE'))
        content = ContentStream(None, writer)
        content.set_data(b'BT /F1 12 Tf 20 TL 1 0 0 1 20 140 Tm '
                         b'(KEEP) Tj (DELETE) \' 4 2 (REMOVE) " (LAST) \' ET')
        page.replace_contents(content)
        self.assertEqual(_erase(writer, page, 0, 95, 190, 130), 12)
        operations = page.get_contents().operations
        self.assertIn(([4], b'Tw'), operations)
        self.assertIn(([2], b'Tc'), operations)
        self.assertIn('KEEP', page.extract_text())
        self.assertIn('LAST', page.extract_text())
        self.assertNotIn('DELETE', page.extract_text())
        self.assertNotIn('REMOVE', page.extract_text())

    def test_clipping_text_fails_explicitly(self):
        writer, page = _pdf(lambda c: c.drawString(20, 100, 'CLIP'))
        content = ContentStream(None, writer)
        content.set_data(page.get_contents().get_data().replace(b'(CLIP)', b'7 Tr (CLIP)'))
        page.replace_contents(content)
        with self.assertRaisesRegex(ValueError, 'clipping'):
            _erase(writer, page, 0, 90, 190, 115)
