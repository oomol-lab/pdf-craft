"""Offline EPUB fill/reorder/CFI regression for service #285."""
# pylint: disable=protected-access,unused-argument
import asyncio
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

from pdf_craft import ConcurrentExecutor, FixedCapacity, LLM, SubmitKind, XMLTranslator
from pdf_craft.llm.runtime import LLMContext
from pdf_craft.pipeline.epub.adapter.chapters import read_chapters
from pdf_craft.pipeline.epub.adapter.spines import spine_cfi_paths
from pdf_craft.pipeline.epub.adapter.zip import ReadOnlyZip
from pdf_craft.pipeline.epub.translation.translator import translate
from pdf_craft.transformer.xml_translator.segment import search_inline_segments, search_text_segments
from pdf_craft.transformer.xml_translator.segment.text_segment import TextPosition
from tests.test_epub_cfi import endpoints
from tests.test_epub_optional_cfi import book


async def template_translation(source, output, *, max_group_tokens=2600):
    """Exercise real fill adoption with identity text; never contact a provider."""
    fragments, events, groups = [], [], []

    async def text(_self, source_text):
        return source_text

    async def fill(_self, *, hill_climbing, **_kwargs):
        groups.append(None)
        await asyncio.sleep(0)
        error = hill_climbing.submit(hill_climbing.request_element())
        if error is not None:
            raise AssertionError(error)

    async def no_network(*_args, **_kwargs):
        raise AssertionError('Offline EPUB regression attempted a model request')

    with patch.object(XMLTranslator, '_translate_text_async', text), \
         patch.object(XMLTranslator, '_request_and_submit_async', fill), \
         patch.object(LLMContext, 'request', no_network):
        await translate(source, output, 'Korean', SubmitKind.REPLACE,
                        llm=LLM('offline', 'http://127.0.0.1:1', 'offline', 'o200k_base'),
                        window=4, executor=ConcurrentExecutor(FixedCapacity(4)), max_group_tokens=max_group_tokens,
                        on_body=fragments.append, on_translation_event=events.append)
    return fragments, events, len(groups)


class EpubTaskContextTests(IsolatedAsyncioTestCase):
    async def test_progress_and_body_callbacks_keep_exact_chapter_ranges(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            documents = []
            for chapter in range(2):
                paragraphs = ''.join(f'<p>Chapter {chapter} paragraph {index} 😀 <em>inside</em> tail.</p>'
                                     for index in range(6))
                documents.append((f'c{chapter}.xhtml', 'application/xhtml+xml',
                                  '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                                  '<title>Title</title></head><body>' + paragraphs + '</body></html>'))
            book(root / 'source.epub', documents)
            fragments, events, groups = await template_translation(
                root / 'source.epub', root / 'output.epub', max_group_tokens=120)
            self.assertTrue(events)
            self.assertGreater(groups, 1)
            self.assertEqual(len(fragments), 12)
            for number, fragment in enumerate(fragments):
                chapter, paragraph = divmod(number, 6)
                initial = f'Chapter {chapter} paragraph {paragraph} 😀 '
                self.assertEqual(fragment.original, initial + 'inside tail.')
                self.assertEqual(fragment.translated, fragment.original)
                cfi = fragment.epubcfi
                assert cfi is not None
                self.assertTrue(cfi.startswith(f'epubcfi(/6/{2 * (chapter + 1)}!'))
                start, end = endpoints(fromstring(documents[chapter][2]), cfi)
                self.assertEqual(start, (initial, ''))
                self.assertEqual(end, (' tail.', ' tail.'))
            with ZipFile(root / 'output.epub') as archive:
                for name, _, _ in documents:
                    rendered = fromstring(archive.read('EPUB/' + name))
                    self.assertEqual(sum(node.tag.endswith('}p') for node in rendered.iter()), 6)

    @skipUnless(os.environ.get('PDF_CRAFT_EPUB_CFI_REGRESSION'), 'private EPUB not configured')
    async def test_original_service_285_epub_through_fill_and_callbacks(self):
        source = Path(os.environ['PDF_CRAFT_EPUB_CFI_REGRESSION'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         '6311357266baca53feb5f0eadb165dca088c64adda1e31f68f07281af39dcb09')
        # Read endpoint text directly from original nodes, independently of the
        # locator index. Merely producing a nonempty CFI would not catch a
        # callback attached to another valid chapter's locator.
        expected = []
        with ReadOnlyZip(source) as archive:
            packages = spine_cfi_paths(archive)
            for path, _, xml in read_chapters(archive):
                body = xml.element.find('body')
                if body is None or not any(segment.text.strip() for segment in search_text_segments(body)):
                    continue
                for fragment in search_inline_segments(search_text_segments(body, track_origins=True)):
                    spans = [span for segment in fragment for span in segment.origins]
                    points = []
                    for span, offset in ((spans[0], spans[0].start), (spans[-1], spans[-1].end)):
                        text = (span.element.text if span.position == TextPosition.TEXT else span.element.tail) or ''
                        points.append((text, text[:offset]))
                    expected.append((xml.element, packages[path], tuple(points)))
        with TemporaryDirectory() as folder:
            fragments, events, groups = await template_translation(source, Path(folder) / 'output.epub')
            self.assertTrue(events)
            self.assertEqual(groups, 253)
            self.assertEqual(len(fragments), 4545)
            self.assertTrue(all(fragment.original == fragment.translated for fragment in fragments))
            for fragment, (tree, package, points) in zip(fragments, expected, strict=True):
                cfi = fragment.epubcfi
                assert cfi is not None
                self.assertTrue(cfi.startswith(f'epubcfi({package}!'))
                self.assertEqual(endpoints(tree, cfi), points)
            print('REAL_EPUB_CFI_GATE', {'groups': groups, 'fragments': len(fragments),
                                        'network_requests': 0, 'scope': 'offline template fill, not Korean model replay'})
