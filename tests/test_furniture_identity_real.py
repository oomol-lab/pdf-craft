"""Opt-in, offline generator/validator gate for service #276's original pages."""
# pylint: disable=protected-access
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless

from pdf_craft.common import read_xml, save_xml
from pdf_craft.document.package import _validate_furnitures
from pdf_craft.pdf.furniture import _native_pages, extract_furnitures
from pdf_craft.pdf.poppler import PopplerDocument


@skipUnless(os.environ.get('PDF_CRAFT_FURNITURE_REGRESSION_PDF'), 'private furniture PDF not configured')
class RealFurnitureIdentityTests(TestCase):
    def test_original_three_pages_keep_colliding_sections_and_validate(self):
        source = Path(os.environ['PDF_CRAFT_FURNITURE_REGRESSION_PDF'])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(digest, '878c6c87a829c12918682a8fffd1d65cca99429ea8ee4d741cab95b397af3744')
        document = PopplerDocument(source)
        self.assertEqual(document.pages_count, 3)
        sizes = {index: (round(document.geometry(index).size[0] * 300 / 72),
                         round(document.geometry(index).size[1] * 300 / 72))
                 for index in range(1, 4)}
        with TemporaryDirectory() as folder:
            root = Path(folder)
            ocr = root / 'empty-occupancy'
            ocr.mkdir()
            native = _native_pages(source, ocr)
            generated = extract_furnitures(source, ocr)
            path = root / 'furnitures.xml'
            save_xml(generated, path)
            _validate_furnitures(path, sizes, set())
            adopted = read_xml(path)
            collisions = []
            for index, sections in native.items():
                actual = adopted.findall(f'pages/page[@index="{index}"]/section')
                self.assertEqual(len(actual), len(sections))
                self.assertEqual(len({s.get('id') for s in actual}), len(actual))
                self.assertTrue(all(s.get('id') for s in actual))
                self.assertEqual(Counter(s.get('det') for s in actual),
                                 Counter(','.join(map(str, s.det)) for s in sections))
                by_box = defaultdict(list)
                for section in sections:
                    by_box[section.det].append(section.content)
                for box, texts in by_box.items():
                    if len(set(texts)) > 1:
                        det = ','.join(map(str, box))
                        # The actual colliding sections are literal fragments,
                        # not pattern references; neither text may be lost.
                        self.assertEqual(Counter(s.text for s in actual if s.get('det') == det), Counter(texts))
                        collisions.append({'subset_page': index, 'sections': len(texts)})
            self.assertEqual(len(collisions), 3)
            self.assertEqual([c['subset_page'] for c in collisions], [1, 2, 3])
            print('REAL_FURNITURE_IDENTITY_GATE', json.dumps({
                'sha256': digest,
                'collisions_preserved': collisions, 'validation': 'passed',
                'scope': 'real native generation with empty OCR occupancy; not historical 672-page replay',
            }))
