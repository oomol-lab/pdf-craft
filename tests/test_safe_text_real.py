"""Opt-in offline gate for the production PDF with polluted Subject metadata."""
# pylint: disable=protected-access
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pdf_craft import (AsyncPDFCraft, ConcurrentExecutor, DeepSeekOCRVendorConfig,
                       ExtractionOptions, FixedCapacity, PDFOptions)
from pdf_craft.common import safe_text
from pdf_craft.pdf.furniture import _native_pages
from pdf_craft.pdf.vendor_ocr import VendorOCRResponse, VendorOCRRuntime


@skipUnless(os.environ.get('PDF_CRAFT_SAFE_TEXT_REGRESSION_PDF'), 'private real-PDF gate is opt-in')
class RealPDFSafeTextTests(IsolatedAsyncioTestCase):
    async def test_real_metadata_and_native_text_enter_clean_extraction_and_epub(self):
        source = Path(os.environ['PDF_CRAFT_SAFE_TEXT_REGRESSION_PDF'])
        self.assertEqual(source.stat().st_size, 48_281_723)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         'f349729c89938bf99937442b03efeede8e51c8d5d6b177ead027fb7acc2086b4')
        info = subprocess.run(['pdfinfo', '-enc', 'UTF-8', '-isodates', str(source)],
                              check=True, capture_output=True).stdout.decode('utf-8')
        fields = {k.strip(): v.strip() for line in info.splitlines()
                  if ':' in line for k, v in [line.split(':', 1)]}
        self.assertEqual(fields['Pages'], '676')
        raw = fields['Subject']
        self.assertNotEqual(raw, safe_text(raw))
        native = subprocess.run(['pdftotext', '-f', '1', '-l', '1', '-bbox-layout', str(source), '-'],
                                check=True, capture_output=True).stdout
        with self.assertRaises(ET.ParseError):
            ET.fromstring(native)
        # Only the provider is simulated; PDF reads, normalization, initial
        # extraction/PCEX, callbacks and EPUB rendering all use production code.
        async def request(_runtime, _value, started=None):
            if started is not None:
                started.set()
            return VendorOCRResponse({}, raw_text=(
                '<|ref|>text<|/ref|><|det|>[[100, 100, 900, 900]]<|/det|>Readable\x00 body\x14.'
            ))

        async def resolve(_path):
            return 'https://images.invalid/offline-probe.png'

        received = []
        craft = AsyncPDFCraft(pdf=PDFOptions(
            ocr=DeepSeekOCRVendorConfig('https://example.invalid/v1', 'unused', 'unused'),
            ocr_image_url_resolver=resolve,
            ocr_executor=ConcurrentExecutor(FixedCapacity(1)),
        ))
        with TemporaryDirectory() as directory, patch.object(VendorOCRRuntime, '_request_deepseek', request):
            root = Path(directory)
            sections = _native_pages(source, root, native_pdf_text=native, native_pdf_text_prepared=True)
            self.assertTrue(sections[1], 'native words must survive, not disappear through ParseError fallback')
            extraction = await craft.extract_pdf(source, root / 'source.pcex', ExtractionOptions(
                page_indexes={1}, dpi=72, includes_furniture=True, on_metadata=received.append,
            ), analysing_path=root / 'analysis')
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].metadata.description, safe_text(raw))
            self.assertEqual(extraction._document_metadata()['description'], safe_text(raw))
            manifest = json.loads((root / 'analysis/extraction/manifest.json').read_text())
            self.assertEqual(manifest['document']['description'], safe_text(raw))
            checked = []
            for path in (root / 'analysis').rglob('*.xml'):
                value = path.read_text(encoding='utf-8')
                self.assertEqual(value, safe_text(value), str(path))
                ET.fromstring(value)
                checked.append(str(path.relative_to(root)))
            self.assertTrue(any('page_1.xml' in name for name in checked))
            self.assertTrue(any('chapter_' in name for name in checked))
            await craft.render_epub(extraction, root / 'result.epub')
            with ZipFile(root / 'result.epub') as archive:
                opf = ET.fromstring(archive.read('OEBPS/content.opf'))
                self.assertEqual(opf.findtext('.//{*}description'), safe_text(raw))
                body = ''.join(archive.read(name).decode('utf-8') for name in archive.namelist()
                               if name.endswith('.xhtml'))
                self.assertIn('Readable body.', body)
                for name in archive.namelist():
                    if name.endswith(('.opf', '.ncx', '.xhtml', '.xml')):
                        ET.fromstring(archive.read(name))
            print(json.dumps({'pdf_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                              'raw_subject_chars': len(raw), 'adopted_subject_chars': len(safe_text(raw)),
                              'native_lines': len(sections[1]), 'checked_xml': checked,
                              'epub_opf': 'parse_ok', 'metadata_callback': 'clean'}, ensure_ascii=True))
