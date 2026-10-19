"""One-shot translations must compose display views, not duplicate PCEX sources."""
# pylint: disable=protected-access
import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from PIL import Image

from pdf_craft import AsyncPDFCraft, PDFCraft, SubmitKind, RenderMode
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.document.render import materialize_render_view
from epub_generator import BookMeta
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, Reference, SourceAsset, SourceTextFragment, TextFlowItem, StandaloneAsset, encode, decode,
)
from pdf_craft.extractor.toc.types import Toc, TocInfo, encode as encode_toc
from pdf_craft.metering import OCRTokensMetering
from pdf_craft.transformer.xml_translator.segment import search_inline_segments, search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator.submitter import submit
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.package import append_translation_layer_to_workspace
from tests.test_pcex_translation_rendering import _add_translation
from tests.extraction_helpers import make_extraction
from tests.test_inline_reference_translation import _identity_mappings

WORDS = {'Heading': 'Titre', 'First': 'Premier', 'Second': 'Deuxieme',
         'Next': 'Suivant', 'Note': 'Annotation', 'Caption': 'Legende', 'Grid': 'Grille', 'Fallback': 'Repli'}


class DeterministicTranslator:
    """Only the network response is replaced; real template and submit code runs."""
    target_language = 'en'

    async def translate_element(self, task, **_kwargs):
        original = deepcopy(task.element)
        mappings = _identity_mappings(search_inline_segments(search_text_segments(task.element)))
        for _parent, segments in mappings:
            for segment in segments:
                for source, target in WORDS.items():
                    segment.text = segment.text.replace(source, target)
        result = submit(task.element, task.action, mappings)
        if task.action == SubmitKind.REPLACE:
            # Template segmentation normalizes formatting. Preserve whitespace
            # slots in this deterministic response, as required by asset markup.
            for before, after in zip(original.iter(), result.iter()):
                for field in ('text', 'tail'):
                    value = getattr(before, field)
                    if value and not value.strip() and not getattr(after, field):
                        setattr(after, field, value)
        return result, task.payload


def table_asset():
    chapter = decode(ET.fromstring(
        '<chapter><flow><standalone-asset><asset ref="table" page_index="1" bbox="0,75,90,78" asset_hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">'
        '<content><table><tr><td>Grid</td></tr></table></content>'
        '</asset></standalone-asset></flow><references/></chapter>'))
    item = chapter.flow_items[0]
    assert isinstance(item, StandaloneAsset)
    return item.asset


def source_fixture(root):
    extraction = make_extraction(root, with_toc=True, language='en',
                                 book_meta=BookMeta(title='Heading'))
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
        TextFlowItem('body', 0, [
            SourceTextFragment(1, 6, (0, 72, 90, 75), ['Fallback']),
            table_asset(),
        ]),
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
    def convert(self, root, kind, mode, *, sync=False, existing=False, with_anchored=True):
        extraction = source_fixture(root/'source')
        if existing:
            _add_translation(root/'source', 'older', 'OLD')
            # The shared fixture helper uses orders 1/2; this fixture starts at 0.
            coverage_path = root/'source/translations/older/coverage.xml'
            coverage = ET.parse(coverage_path).getroot()
            first_entry = coverage.find('./narrative/paragraph')
            assert first_entry is not None
            first_entry.set('order', '0')
            coverage.findall('./narrative/paragraph')[1].set('order', '1')
            save_xml(coverage, coverage_path)
            extraction._validate()
        original_bytes = (root/'source/chapters/chapter_1.xml').read_bytes()
        output = root/f'book.{kind}'
        layers_created = []

        def append_layer(source, translated, output_path, *, translation_id,
                         target_language, include_furniture=False, metadata_overlay=None):
            layered = append_translation_layer_to_workspace(
                source, translated, output_path, translation_id=translation_id,
                target_language=target_language, include_furniture=include_furniture,
                metadata_overlay=metadata_overlay,
            )._validate()
            with layered._materialize() as paths:
                self.assertEqual(original_bytes, (paths.chapters/'chapter_1.xml').read_bytes())
                info = layered._translations()[-1]
                target = ET.parse(paths.translations/info.id/'chapters/chapter_1.xml').getroot()
                original = ET.fromstring(original_bytes)
                self.assertEqual([n.attrib for n in original.iter('fragment')],
                                 [n.attrib for n in target.iter('fragment')])
                keys = [(n.get('page_index'), n.get('source_order')) for n in target.iter('fragment')]
                self.assertEqual(len(keys), len(set(keys)))
                self.assertNotIn('First', ''.join(target.itertext()))
                if existing:
                    self.assertEqual(layered._translations()[0].id, 'older')
                layers_created.append(info.id)
            return layered

        metering = OCRTokensMetering(0, 0)
        with patch('pdf_craft.craft.append_translation_layer_to_workspace', side_effect=append_layer), \
                patch.object(AsyncPDFCraft, '_extract_to_workspace',
                          AsyncMock(return_value=(extraction, metering))):
            craft = PDFCraft() if sync else AsyncPDFCraft()
            call = getattr(craft, f'convert_pdf_to_{"epub" if kind == "epub" else "markdown"}')
            result = call('unused.pdf', output, translator=DeterministicTranslator(), submit=mode, with_anchored=with_anchored)
            if not sync:
                asyncio.run(result)
        self.assertEqual(len(layers_created), 1)
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
                        for word in ('Titre', 'Premier', 'Deuxieme', 'Suivant', 'Annotation', 'Legende', 'Grille', 'Same'):
                            self.assertIn(word, text)
                        self.assertEqual(text.count('Same'), 1 if mode == SubmitKind.REPLACE else 2)
                        if kind == 'epub':
                            documents = epub_documents(output)
                            tags = [node for doc in documents for node in doc.iter()]
                            self.assertEqual(sum(n.tag.endswith('}img') for n in tags), 1)
                            self.assertEqual(sum(n.tag.endswith('}table') for n in tags), 1)
                            for doc in documents:
                                ids = [n.get('id') for n in doc.iter() if n.get('id')]
                                self.assertEqual(len(ids), len(set(ids)))
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

    def test_new_translation_is_selected_and_assets_can_be_preserved(self):
        for kind in ('md', 'epub'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                output = self.convert(Path(directory), kind, SubmitKind.APPEND_BLOCK,
                                      existing=True, with_anchored=False)
                text = output.read_text() if kind == 'md' else '\n'.join(
                    ''.join(doc.itertext()) for doc in epub_documents(output))
                self.assertIn('Premier', text)
                self.assertNotIn('OLD', text)
                self.assertIn('Caption', text)
                self.assertNotIn('Legende', text)

    def test_one_shot_epub_keeps_language_inference_for_human_language_labels(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            DeterministicTranslator, 'target_language', 'English',
        ):
            output = self.convert(Path(directory), 'epub', SubmitKind.APPEND_BLOCK)
            self.assertIn('Premier', '\n'.join(blocks(output)))

    def test_generic_xhtml_submission_modes_keep_their_contract(self):
        for mode in SubmitKind:
            root = ET.fromstring('<html><body><p>First</p></body></html>')
            result, _ = asyncio.run(DeterministicTranslator().translate_element(
                TranslationTask(root, mode, None)))
            paragraphs = result.findall('.//p')
            self.assertEqual(len(paragraphs), 2 if mode == SubmitKind.APPEND_BLOCK else 1)
            self.assertIn('Premier', ''.join(result.itertext()))
            self.assertEqual('First' in ''.join(result.itertext()), mode != SubmitKind.REPLACE)

    def test_stored_layers_keep_source_identities_and_allow_all_render_styles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_fixture(root/'source')
            craft = AsyncPDFCraft()
            translated = asyncio.run(craft.translate_extraction(
                source, root/'translated.pcex', cast(Any, DeterministicTranslator()), translation_id='fresh'))
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
                coverage = ET.parse(b.translations/'fresh/coverage.xml').getroot()
                entry = next(n for n in coverage.findall('./narrative/paragraph') if n.get('order') == '6')
                entry.set('state', 'preserved')
                note_entry = next(n for n in coverage.findall('./narrative/paragraph') if n.get('order') == '5')
                note_entry.set('state', 'preserved')
                save_xml(coverage, b.translations/'fresh/coverage.xml')
                view_source = PDFCraftExtraction._from_workspace(b.root)._validate()
                for mode in (RenderMode.REPLACE, RenderMode.BILINGUAL, RenderMode.BILINGUAL_TEXT):
                    with materialize_render_view(view_source, mode, 'fresh') as view:
                        rendered = ET.parse(view.chapters/'chapter_1.xml').getroot()
                        text = ''.join(rendered.itertext())
                        self.assertIn('Fallback', text)
                        self.assertNotIn('Repli', text)
                        self.assertIn('Note', text)
                        self.assertNotIn('Annotation', text)
                        self.assertIn('Grille', text)
                        self.assertEqual(len(rendered.findall('.//asset[@ref="image"]')), 1)
                        self.assertEqual(len(rendered.findall('.//asset[@ref="table"]')), 1)
                        self.assertEqual(len(rendered.findall('./references/ref')), 1)
                        self.assertEqual(view.metadata['title'], 'Titre' if mode == RenderMode.REPLACE else 'Heading Titre')
            output = root/'bilingual.md'
            PDFCraft().render_markdown(reopened, output, mode=RenderMode.BILINGUAL)
            self.assertIn('First', output.read_text())
            self.assertIn('Premier', output.read_text())
