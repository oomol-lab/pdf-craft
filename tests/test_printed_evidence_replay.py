"""Recover physical observations without changing historical OCR or targets."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
from xml.etree import ElementTree

from PIL import Image, ImageDraw
from pdf_craft_tool.smoke.printed_evidence import recover_printed_evidence


class PrintedEvidenceReplayTests(TestCase):
    def test_raw_evidence_is_measured_with_the_production_observer(self):
        for obstruction in (False, True):
            with self.subTest(obstruction=obstruction), TemporaryDirectory() as directory:
                root = Path(directory)
                source = root/'source'
                source.mkdir()
                original = ('<page index="1" input_tokens="3"><body>'
                            '<layout ref="text" det="0,0,100,70">unchanged source</layout>'
                            + ('<layout ref="image" det="0,0,80,20"/>' if obstruction else '')
                            + '</body><footnotes><layout ref="text" det="0,0,100,70">note</layout></footnotes></page>')
                (source/'page_1.xml').write_text(original)
                pdf = root/'original.pdf'
                pdf.write_bytes(b'original PDF identity')
                image = Image.new('RGB', (100,70), 'white')
                draw = ImageDraw.Draw(image)
                for left, top in ((25,4),(5,24),(5,44)):
                    draw.rectangle((left,top,95,top+10), fill='black')
                document = Mock()
                document.render_page.return_value = image
                with patch('pdf_craft_tool.smoke.printed_evidence.DefaultPDFHandler') as handler:
                    handler.return_value.open.return_value = document
                    output = recover_printed_evidence(source, root/'observed'/'ocr', pdf, 300)
                self.assertEqual((source/'page_1.xml').read_text(), original)
                observed = ElementTree.parse(output/'page_1.xml').getroot()
                block = observed.find('body/layout')
                assert block is not None
                self.assertEqual(block.text, 'unchanged source')
                self.assertEqual(block.get('det'), '0,0,100,70')
                self.assertEqual(observed.get('input_tokens'), '3')
                note = observed.find('footnotes/layout')
                assert note is not None
                self.assertIsNone(note.get('first_line_inset'))
                for item in (block, note):
                    self.assertEqual(item.get('printed_line_count'), '3')
                    self.assertEqual(item.get('printed_line_height'), '11.0')
                    self.assertEqual(item.get('printed_line_lefts'), '25,5,5')
                if obstruction:
                    self.assertIsNone(block.get('first_line_inset'))
                else:
                    self.assertGreater(float(block.attrib['first_line_inset']), 1)
                ledger = json.loads((output.parent/'printed-evidence.json').read_text())
                self.assertEqual(ledger['dpi'], 300)
                self.assertEqual(ledger['observations'][0]['bbox'], [0,0,100,70])
                self.assertEqual(ledger['observations'][0]['first_line_inset'],
                                 float(block.attrib['first_line_inset']) if not obstruction else None)
                document.render_page.assert_called_once_with(1,300)
                document.close.assert_called_once_with()
