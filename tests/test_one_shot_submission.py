"""One-shot translations must compose display views, not duplicate PCEX sources."""
# pylint: disable=protected-access
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from PIL import Image

from pdf_craft import AsyncPDFCraft, PDFCraft, SubmitKind, RenderMode
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, Reference, SourceAsset, SourceTextFragment, TextFlowItem, encode,
)
from pdf_craft.extractor.toc.types import Toc, TocInfo, encode as encode_toc
from pdf_craft.metering import OCRTokensMetering
from pdf_craft.transformer.xml_translator.segment import search_inline_segments, search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator.submitter import submit
from tests.extraction_helpers import make_extraction
from tests.test_inline_reference_translation import _identity_mappings

WORDS = {'Heading': 'Titre', 'First': 'Premier', 'Second': 'Deuxieme',
         'Next': 'Suivant', 'Note': 'Annotation', 'Caption': 'Legende'}


class DeterministicTranslator:
    """Only the network response is replaced; real template and submit code runs."""
    target_language = 'en'

    async def translate_element(self, task, **_kwargs):
        mappings = _identity_mappings(search_inline_segments(search_text_segments(task.element)))
        for _parent, segments in mappings:
            for segment in segments:
                for source, target in WORDS.items():
                    segment.text = segment.text.replace(source, target)
        return submit(task.element, task.action, mappings), task.payload


def source_fixture(root):
    extraction = make_extraction(root, with_toc=True, language='en')
    image_hash = 'a' * 64
    Image.new('RGB', (5, 5), 'white').save(root/'assets'/f'{image_hash}.png')
    note = Reference(1, 1, '1', [TextFlowItem('body', 0, [
        SourceTextFragment(1, 5, (0, 80, 90, 95), ['Note'])])])
    chapter = Chapter(1, 0, [
        TextFlowItem('heading', 1, [SourceTextFragment(1, 0, (0, 0, 90, 10), ['Heading'])]),
        TextFlowItem('body', 0, [
            SourceTextFragment(1, 1, (0, 12, 90, 20), ['First', note]),
            SourceTextFragment(1, 2, (0, 22, 90, 30), ['Second']),
            SourceAsset(1, 'image', (0, 32, 90, 50), caption=['Caption'], asset_hash=image_hash),
            SourceTextFragment(1, 3, (0, 52, 90, 60), ['Next']),
        ]),
        TextFlowItem('body', 0, [SourceTextFragment(1, 4, (0, 62, 90, 70), ['Same'])]),
    ])
    save_xml(encode(chapter), root/'chapters/chapter_1.xml')
    save_xml(encode_toc(TocInfo([Toc(1, 1, 0, 0, [])], [])), root/'toc.xml')
    return extraction._validate(require_toc=True)


def epub_documents(path):
    with ZipFile(path) as archive:
        return [ET.fromstring(archive.read(name)) for name in archive.namelist()
                if name.endswith('.xhtml')]


def blocks(path):
    return [''.join(node.itertext()).strip() for document in epub_documents(path)
            for node in document.iter() if node.tag.rsplit('}', 1)[-1] == 'p']


class OneShotSubmissionTests(unittest.TestCase):
    def convert(self, root, kind, mode, *, sync=False):
        extraction = source_fixture(root/'source')
        output = root/f'book.{kind}'
        metering = OCRTokensMetering(0, 0)
        with patch.object(AsyncPDFCraft, '_extract_to_workspace',
                          AsyncMock(return_value=(extraction, metering))):
            craft = PDFCraft() if sync else AsyncPDFCraft()
            call = getattr(craft, f'convert_pdf_to_{"epub" if kind == "epub" else "markdown"}')
            result = call('unused.pdf', output, translator=DeterministicTranslator(), submit=mode)
            if not sync:
                asyncio.run(result)
        extraction._validate()
        original = ET.parse(root/'source/chapters/chapter_1.xml').getroot()
        self.assertEqual(original.findtext('./flow/text/fragment'), 'Heading')
        return output

    def test_append_block_public_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.convert(Path(directory), 'md', SubmitKind.APPEND_BLOCK)
            text = output.read_text()
            self.assertIn('Premier', text)
            self.assertIn('First', text)

    def test_append_block_public_epub(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.convert(Path(directory), 'epub', SubmitKind.APPEND_BLOCK, sync=True)
            paragraphs = blocks(output)
            self.assertTrue(any('First' in p for p in paragraphs))
            self.assertTrue(any('Premier' in p for p in paragraphs))
            self.assertFalse(any('First' in p and 'Premier' in p for p in paragraphs))

    def test_three_modes_both_facades_and_formats(self):
        for sync in (False, True):
            for kind in ('md', 'epub'):
                for mode in SubmitKind:
                    with self.subTest(sync=sync, kind=kind, mode=mode), tempfile.TemporaryDirectory() as d:
                        output = self.convert(Path(d), kind, mode, sync=sync)
                        if kind == 'epub':
                            paragraphs = blocks(output)
                            text = '\n'.join(''.join(doc.itertext()) for doc in epub_documents(output))
                        else:
                            text = output.read_text()
                            paragraphs = text.split('\n\n')
                        for word in ('Titre', 'Premier', 'Deuxieme', 'Suivant', 'Annotation', 'Legende', 'Same'):
                            self.assertIn(word, text)
                        if mode == SubmitKind.REPLACE:
                            self.assertNotIn('First', text)
                            self.assertNotIn('Note', text)
                        else:
                            self.assertIn('First', text)
                            self.assertIn('Note', text)
                            self.assertEqual(any('First' in p and 'Premier' in p for p in paragraphs),
                                             mode == SubmitKind.APPEND_TEXT)
                            if mode == SubmitKind.APPEND_BLOCK:
                                self.assertLess(text.index('Second'), text.index('Premier'))

    def test_stored_layers_keep_source_identities_and_allow_all_render_styles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_fixture(root/'source')
            craft = AsyncPDFCraft()
            translated = asyncio.run(craft.translate_extraction(
                source, root/'translated.pcex', DeterministicTranslator(), translation_id='fresh'))
            reopened = PDFCraft().open_extraction(root/'translated.pcex')
            PDFCraft().export_extraction(reopened, root/'reexported.pcex')
            with source._materialize() as a, translated._materialize() as b:
                original = (a.chapters/'chapter_1.xml').read_bytes()
                self.assertEqual(original, (b.chapters/'chapter_1.xml').read_bytes())
                x = ET.fromstring(original)
                y = ET.parse(b.translations/'fresh/chapters/chapter_1.xml').getroot()
                self.assertEqual([n.attrib for n in x.iter('fragment')], [n.attrib for n in y.iter('fragment')])
                keys = [(n.get('page_index'), n.get('source_order')) for n in y.iter('fragment')]
                self.assertEqual(len(keys), len(set(keys)))
            output = root/'bilingual.md'
            PDFCraft().render_markdown(reopened, output, mode=RenderMode.BILINGUAL)
            self.assertIn('First', output.read_text())
            self.assertIn('Premier', output.read_text())
