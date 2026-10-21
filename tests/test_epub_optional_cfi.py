"""Optional source locations never suppress accepted EPUB body fragments."""
# Each subtest finishes translation and callback cleanup before the next iteration.
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any, get_type_hints
from unittest.mock import patch
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

from pdf_craft import (AsyncPDFCraft, PDFCraft, ConcurrentExecutor, FixedCapacity,
                       EpubFragmentContent, EpubTranslation, LLM, SubmitKind, XMLTranslator)
from pdf_craft.pipeline.epub.cfi import ContentCFILocator
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from tests.test_epub_cfi import endpoints


def book(path, documents):
    """Include non-body tasks so the missing-locator filter is exercised too."""
    with ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml',
                         '<container><rootfiles><rootfile full-path="EPUB/book.opf"/></rootfiles></container>')
        manifest = ''.join(f'<item id="c{i}" href="{name}" media-type="{media}"/>'
                           for i, (name, media, _) in enumerate(documents))
        spine = ''.join(f'<itemref idref="c{i}"/>' for i in range(len(documents)))
        archive.writestr('EPUB/book.opf',
                         '<package><metadata><title>Book title</title></metadata><manifest>'
                         '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
                         + manifest + '</manifest><spine>' + spine + '</spine></package>')
        archive.writestr('EPUB/toc.ncx', '<ncx><navMap><navPoint id="n"><navLabel><text>Navigation</text>'
                         '</navLabel><content src="c0.html"/></navPoint></navMap></ncx>')
        for name, _, content in documents:
            archive.writestr('EPUB/' + name, content)


def document(text, *, html=False):
    namespace = '' if html else ' xmlns="http://www.w3.org/1999/xhtml"'
    return f'<html{namespace}><head><title>Document title</title></head><body><p>{text}</p></body></html>'


def options() -> dict[str, Any]:
    return dict(target_language='zh', submit=SubmitKind.REPLACE,
                llm=LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                executor=ConcurrentExecutor(FixedCapacity(2)), window=2)


async def mappings(_translator, inline_segments, **_kwargs):
    result = []
    for segment in inline_segments:
        element = segment.create_element()
        for node in element.iter():
            if node.text and node.text.strip():
                node.text = '译:' + node.text
            if node.tail and node.tail.strip():
                node.tail = '译:' + node.tail
        result.append((segment.parent, list(search_text_segments(segment.assign_attributes(element)))))
    return result


class OptionalCFITests(unittest.IsolatedAsyncioTestCase):
    async def test_html_delivers_only_body_text_and_awaits_callback_in_all_modes(self):
        for mode in SubmitKind:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, output = root / 'source.epub', root / 'out.epub'
                book(source, [('c0.html', 'text/html', document('HTML paragraph.', html=True))])
                received = []
                async def callback(value):
                    await asyncio.sleep(0)
                    received.append(value)
                with patch.object(XMLTranslator, '_translate_inline_segments_async', new=mappings), patch(
                    'pdf_craft.pipeline.epub.translation.translator.ContentCFILocator',
                    side_effect=AssertionError('HTML must not request a locator'),
                ):
                    await AsyncPDFCraft().translate_epub(source, output,
                        **{**options(), 'submit': mode}, on_body=callback)
                self.assertEqual(len(received), 1)
                self.assertEqual(received[0], EpubFragmentContent(None, 'HTML paragraph.', '译:HTML paragraph.'))
                with ZipFile(output) as archive:
                    self.assertIn('译:HTML paragraph.', archive.read('EPUB/c0.html').decode())
                    self.assertIn('译:Document title', archive.read('EPUB/c0.html').decode())

    async def test_mixed_documents_keep_xhtml_ranges_and_source_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / 'source.epub', root / 'out.epub'
            documents = [('c0.html', 'text/html', document('HTML first.', html=True)),
                         ('c1.xhtml', 'application/xhtml+xml', document('XHTML 😀 middle.')),
                         ('c2.html', 'text/html', document('HTML last.', html=True))]
            book(source, documents)
            received = []
            with patch.object(XMLTranslator, '_translate_inline_segments_async', new=mappings):
                await AsyncPDFCraft().translate_epub(source, output, **options(), on_body=received.append)
            self.assertEqual([value.original for value in received],
                             ['HTML first.', 'XHTML 😀 middle.', 'HTML last.'])
            self.assertEqual([value.translated for value in received],
                             ['译:HTML first.', '译:XHTML 😀 middle.', '译:HTML last.'])
            self.assertIsNone(received[0].epubcfi)
            self.assertIsNone(received[2].epubcfi)
            cfi = received[1].epubcfi
            assert cfi is not None
            self.assertTrue(cfi.startswith('epubcfi(/6/4!'))
            start, end = endpoints(fromstring(documents[1][2]), cfi)
            self.assertEqual(start[1], '')
            self.assertEqual(end[1], 'XHTML 😀 middle.')

    async def test_missing_package_location_still_delivers_xhtml_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / 'source.epub', root / 'out.epub'
            book(source, [('c0.xhtml', 'application/xhtml+xml', document('XHTML text.'))])
            received = []
            with patch.object(XMLTranslator, '_translate_inline_segments_async', new=mappings), patch(
                'pdf_craft.pipeline.epub.translation.translator.spine_cfi_paths', return_value={},
            ):
                await AsyncPDFCraft().translate_epub(source, output, **options(), on_body=received.append)
            self.assertEqual(received, [EpubFragmentContent(None, 'XHTML text.', '译:XHTML text.')])
            self.assertTrue(output.is_file())

    async def test_no_callback_does_not_build_locations_or_public_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / 'source.epub', root / 'out.epub'
            book(source, [('c0.html', 'text/html', document('HTML text.', html=True)),
                          ('c1.xhtml', 'application/xhtml+xml', document('XHTML text.'))])
            with patch.object(XMLTranslator, '_translate_inline_segments_async', new=mappings), patch(
                'pdf_craft.pipeline.epub.translation.translator.ContentCFILocator', side_effect=AssertionError('unused'),
            ), patch('pdf_craft.pipeline.epub.translation.translator.EpubFragmentContent', side_effect=AssertionError('unused')):
                await AsyncPDFCraft().translate_epub(source, output, **options())
            self.assertTrue(output.is_file())

    async def test_locator_errors_are_not_downgraded_to_missing_cfi(self):
        for error in (ValueError('source offset is outside the original text'), KeyError('source mapping')):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, output = root / 'source.epub', root / 'out.epub'
                book(source, [('c0.xhtml', 'application/xhtml+xml', document('XHTML text.'))])
                output.write_bytes(b'existing output')
                with patch.object(XMLTranslator, '_translate_inline_segments_async', new=mappings), patch.object(
                    ContentCFILocator, 'capture', side_effect=error,
                ):
                    with self.assertRaises(type(error)):
                        await AsyncPDFCraft().translate_epub(source, output, **options(), on_body=lambda _: None)
                self.assertEqual(output.read_bytes(), b'existing output')
                self.assertFalse(list(root.glob('*.tmp')))

    async def test_real_parse_and_translation_errors_still_abort(self):
        async def broken_translation(translator, inline_segments, **kwargs):
            if any('HTML text.' in segment.text for inline in inline_segments for segment in inline):
                raise RuntimeError('translation failed')
            return await mappings(translator, inline_segments, **kwargs)
        for kind in ('parse', 'translation'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, output = root / 'source.epub', root / 'out.epub'
                content = '<html><body><p>Broken</body></html>' if kind == 'parse' else document('HTML text.', html=True)
                book(source, [('c0.html', 'text/html', content)])
                output.write_bytes(b'existing output')
                expected = ValueError if kind == 'parse' else RuntimeError
                with patch.object(XMLTranslator, '_translate_inline_segments_async',
                                  new=mappings if kind == 'parse' else broken_translation):
                    with self.assertRaises(expected):
                        await AsyncPDFCraft().translate_epub(source, output, **options(), on_body=lambda _: None)
                self.assertEqual(output.read_bytes(), b'existing output')
                self.assertFalse(list(root.glob('*.tmp')))

    async def test_html_callback_errors_and_cancellation_preserve_existing_output(self):
        for error in (RuntimeError('callback failed'), asyncio.CancelledError()):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, output = root / 'source.epub', root / 'out.epub'
                book(source, [('c0.html', 'text/html', document('HTML text.', html=True))])
                output.write_bytes(b'existing output')
                async def callback(_value):
                    await asyncio.sleep(0)
                    raise error
                with patch.object(XMLTranslator, '_translate_inline_segments_async', new=mappings):
                    with self.assertRaises(type(error)):
                        await AsyncPDFCraft().translate_epub(source, output, **options(), on_body=callback)
                self.assertEqual(output.read_bytes(), b'existing output')
                self.assertFalse(list(root.glob('*.tmp')))


class SyncOptionalCFITests(unittest.TestCase):
    def test_sync_facade_awaits_optional_cfi_callback_and_keeps_alias(self):
        received = []
        async def callback(value):
            await asyncio.sleep(0)
            received.append(value)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / 'source.epub', root / 'out.epub'
            book(source, [('c0.html', 'text/html', document('HTML text.', html=True))])
            with patch.object(XMLTranslator, '_translate_inline_segments_async', new=mappings):
                PDFCraft().translate_epub(source, output, **options(), on_body=callback)
            self.assertEqual(received, [EpubFragmentContent(None, 'HTML text.', '译:HTML text.')])
            self.assertTrue(output.is_file())
        self.assertIs(EpubTranslation, EpubFragmentContent)
        self.assertEqual(get_type_hints(EpubFragmentContent)['epubcfi'], str | None)
