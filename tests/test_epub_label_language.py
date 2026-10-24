"""Fixed EPUB labels must never narrow the document's accepted languages."""
# pylint: disable=protected-access
import asyncio
from inspect import signature
from pathlib import Path
import tempfile
from typing import Any, cast
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from epub_generator import BookMeta

from pdf_craft import (AsyncPDFCraft, PDFCraft, RenderMode, XMLTranslator,
                       OCRTokensMetering)
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, Reference, SourceTextFragment, TextFlowItem, encode,
)
from pdf_craft.renderer import EpubRenderer
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from tests.extraction_helpers import make_extraction
from tests.test_pdf_preflight import write_pdf


BODY = '한국어 본문입니다.'
NOTE = '한국어 각주입니다.'


def source_book(root, language='ko'):
    document = make_extraction(root, language=language, book_meta=BookMeta(title='책 제목'))
    note = Reference(1, 1, '1', [TextFlowItem('body', 0, [
        SourceTextFragment(1, 1, (0, 60, 90, 90), [NOTE])])])
    save_xml(encode(Chapter(None, -1, [TextFlowItem('body', 0, [
        SourceTextFragment(1, 0, (0, 0, 90, 50), [BODY, note])])])),
        root / 'chapters/chapter_head.xml')
    return document._validate()


def inspect_epub(path):
    with ZipFile(path) as archive:
        nav = ET.fromstring(archive.read('OEBPS/nav.xhtml'))
        body = archive.read('OEBPS/Text/head.xhtml').decode()
    return nav.findtext('.//{*}h1'), body


async def korean_mappings(_translator, inline_segments, **_kwargs):
    """Replace only model responses; real adapters and EPUB serialization run."""
    results = []
    for segment in inline_segments:
        element = segment.create_element()
        for node in element.iter():
            for name in ('text', 'tail'):
                value = getattr(node, name)
                if value and value.strip():
                    setattr(node, name, '번역:' + value)
        results.append((segment.parent, list(search_text_segments(segment.assign_attributes(element)))))
    return results


class EpubLabelLanguageTests(unittest.TestCase):
    def test_real_generator_labels_fallback_without_touching_korean_body_or_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = source_book(root / 'source')
            values = ('zh', 'en', 'ko', 'Korean', 'fr', 'ja', 'ar', 'zh-CN', 'en-US',
                      'ZH', 'zh ', 'Simplified Chinese', 'unknown', '')
            for index, language in enumerate(values):
                received, notes = [], []
                with self.subTest(label_language=language):
                    output = root / f'{index}.epub'
                    asyncio.run(EpubRenderer().render(source, output, label_language=language,
                                                     on_body=received.append, on_footnote=notes.append))
                    heading, body = inspect_epub(output)
                    self.assertEqual(heading, '目录' if language == 'zh' else 'Table of Contents')
                    self.assertIn(BODY, body)
                    self.assertIn(NOTE, body)
                    self.assertIn(BODY, received[0].text)
                    self.assertIn(NOTE, notes[0].text)
                    self.assertEqual(source._language(), 'ko')

    def test_defaults_are_english_even_for_chinese_source_and_public_signatures_match(self):
        for method in (PDFCraft.render_epub, AsyncPDFCraft.render_epub,
                       PDFCraft.convert_pdf_to_epub, AsyncPDFCraft.convert_pdf_to_epub,
                       EpubRenderer.render):
            parameters = signature(method).parameters
            self.assertNotIn('lan', parameters)
            self.assertEqual(parameters['label_language'].default, 'en')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = source_book(root / 'source', language='zh')
            PDFCraft().render_epub(source, root / 'book.epub')
            self.assertEqual(inspect_epub(root / 'book.epub')[0], 'Table of Contents')

    def test_saved_korean_translation_and_online_stream_match_both_facades(self):
        async def exercise(root):
            from pdf_craft import LLM, ConcurrentExecutor, FixedCapacity
            llm = LLM('unused', 'https://example.invalid', 'unused', 'o200k_base')
            translator_instance = XMLTranslator(
                llm, llm, 'ko', None, 10, 1000,
                executor=ConcurrentExecutor(FixedCapacity(2)),
            )
            source = source_book(root / 'source', language='en')
            craft = AsyncPDFCraft()
            metadata, saved_body, saved_notes = [], [], []
            with patch.object(XMLTranslator, '_translate_inline_segments_async', new=korean_mappings):
                stored = await craft.translate_extraction(source, root / 'ko.pcex', translator_instance,
                    with_anchored=False, on_metadata=metadata.append)
                await craft.render_epub(stored, root / 'saved.epub', mode=RenderMode.REPLACE,
                    label_language='Korean', on_body=saved_body.append, on_footnote=saved_notes.append)
                live_metadata, live_body, live_notes = [], [], []
                await craft.render_epub(source, root / 'live.epub', translator=translator_instance,
                    with_anchored=False, label_language='ko', on_metadata=live_metadata.append,
                    on_body=live_body.append, on_footnote=live_notes.append)
            self.assertEqual(metadata[0].metadata.language, 'ko')
            self.assertEqual(live_metadata[0].metadata.language, 'ko')
            self.assertEqual(translator_instance.target_language, 'ko')
            self.assertEqual(stored._translations()[0].target_language, 'ko')
            self.assertEqual(saved_body, live_body)
            self.assertEqual(saved_notes, live_notes)
            for name in ('saved', 'live'):
                heading, body = inspect_epub(root / f'{name}.epub')
                self.assertEqual(heading, 'Table of Contents')
                self.assertIn('번역:' + BODY, body)
                self.assertIn('번역:' + NOTE, body)
            return stored
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stored = asyncio.run(exercise(root))
            received = []
            PDFCraft().render_epub(stored, root / 'sync.epub', label_language='ko',
                                  mode=RenderMode.REPLACE, on_body=received.append)
            self.assertIn('번역:' + BODY, received[0].text)
            self.assertEqual(inspect_epub(root / 'sync.epub')[0], 'Table of Contents')

    def test_one_shot_pdf_conversion_preserves_korean_target_and_forwards_label_choice(self):
        from tests.test_pcex_translation_layers import _Prefix
        class KoreanTranslator(_Prefix):
            target_language = 'ko'

        async def exercise(root, label):
            source = source_book(root / f'source-{label}', language='ko')
            model = KoreanTranslator('번역')
            craft = AsyncPDFCraft()
            output = root / f'{label}.epub'
            with patch.object(craft, '_extract_to_workspace', new=AsyncMock(
                return_value=(source, OCRTokensMetering(0, 0)),
            )):
                await craft.convert_pdf_to_epub(root / 'source.pdf', output,
                    translator=cast(Any, model), with_anchored=False, label_language=label)
            self.assertEqual(inspect_epub(output)[0], '目录' if label == 'zh' else 'Table of Contents')
            self.assertIn('번역:' + BODY, inspect_epub(output)[1])
            self.assertEqual(source._language(), 'ko')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_pdf(root / 'source.pdf', pages=1)
            for label in ('ko', 'zh'):
                asyncio.run(exercise(root, label))
