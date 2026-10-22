"""Public SDK contracts, including optional arguments on the synchronous facade."""

from pdf_craft import RetryPolicy
# pylint: disable=protected-access
import inspect
from functools import partial
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from typing import get_type_hints
from unittest.mock import AsyncMock

import pdf_craft
from pdf_craft import AsyncPDFCraft, PDFCraft, RenderMode, SubmitKind, XMLTranslator
from tests.test_pcex_translation_layers import _source


class PublicStreamAPITests(unittest.TestCase):
    def test_stream_entry_points_have_matching_explicit_typed_signatures(self):
        operations = ('read_pages', 'read_asset', 'render_markdown', 'render_epub',
                      'translate_extraction', 'translate_pdf', 'patch_pdf_with_extraction',
                      'translate_epub', 'convert_pdf_to_markdown', 'convert_pdf_to_epub')
        for name in operations:
            with self.subTest(operation=name):
                sync, asynchronous = getattr(PDFCraft, name), getattr(AsyncPDFCraft, name)
                sync_parameters = inspect.signature(sync).parameters
                async_parameters = inspect.signature(asynchronous).parameters
                self.assertEqual(list(sync_parameters), list(async_parameters))
                for key, value in sync_parameters.items():
                    other = async_parameters[key]
                    self.assertEqual(value.kind, other.kind)
                    if value.default is not inspect.Parameter.empty and callable(value.default):
                        self.assertEqual(value.default(), other.default())
                    else:
                        self.assertEqual(value.default, other.default)
                self.assertEqual(get_type_hints(sync), get_type_hints(asynchronous))
                for parameter in inspect.signature(sync).parameters.values():
                    self.assertNotEqual(parameter.kind, inspect.Parameter.VAR_KEYWORD)
                    if parameter.name != 'self':
                        self.assertNotEqual(parameter.annotation, inspect.Parameter.empty)

    def test_new_optional_arguments_are_forwarded_without_changing_values(self):
        # Deliberately exercise optional inputs: a required-argument smoke test
        # cannot catch losing callbacks or the translation selection at the facade.
        marker = object()
        on_body, on_note, on_metadata = AsyncMock(), AsyncMock(), AsyncMock()
        render_options = dict(mode=RenderMode.REPLACE, translation_id='chosen',
                              aborted=lambda: False, translator=marker, window=3,
                              with_anchored=False, on_body=on_body,
                              on_footnote=on_note, on_metadata=on_metadata)
        cases = (
            ('render_markdown', ('source', 'output'), render_options),
            ('render_epub', ('source', 'output'), render_options),
            ('translate_extraction', ('source', 'output', marker),
             dict(window=3, with_anchored=False, with_furniture=True,
                  translation_id='chosen', target_language='zh',
                  on_body=on_body, on_footnote=on_note, on_metadata=on_metadata)),
            ('translate_pdf', ('input.pdf', 'source', 'output', marker),
             dict(window=3, with_furniture=True, error_policy=True,
                  on_body=on_body, on_footnote=on_note, on_metadata=on_metadata)),
            ('patch_pdf_with_extraction', ('input.pdf', 'source', 'output'),
             dict(translator=marker, window=3, with_furniture=True,
                  error_policy=True, on_body=on_body,
                  on_footnote=on_note, on_metadata=on_metadata)),
            ('translate_epub', ('input.epub', 'output.epub'),
             dict(target_language='zh', submit=SubmitKind.APPEND_BLOCK,
                  user_prompt='Translate', retry_policy=RetryPolicy(analysis=2), max_group_tokens=200,
                  llm=marker, translation_llm=marker, fill_llm=marker,
                  executor=marker, translation_executor=marker, fill_executor=marker,
                  on_translation_event=marker,
                  window=3, on_body=on_body, on_metadata=on_metadata)),
        )
        for name, positional, keywords in cases:
            with self.subTest(operation=name):
                craft = PDFCraft()
                operation = AsyncMock(return_value=marker)
                setattr(craft._async_craft, name, operation)
                getattr(craft, name)(*positional, **keywords)
                operation.assert_awaited_once()
                signature = inspect.signature(getattr(AsyncPDFCraft, name))
                expected = signature.bind(None, *positional, **keywords)
                awaited = operation.await_args
                assert awaited is not None
                actual = signature.bind(None, *awaited.args, **awaited.kwargs)
                expected.apply_defaults()
                actual.apply_defaults()
                self.assertEqual(actual.arguments, expected.arguments)

    def test_sync_page_and_asset_reads_return_detached_public_values(self):
        with tempfile.TemporaryDirectory() as directory:
            document = _source(Path(directory) / 'source')
            craft = PDFCraft()
            pages = craft.read_pages(document)
            self.assertIsInstance(pages[1], pdf_craft.PageGeometry)
            self.assertEqual((pages[1].width, pages[1].height), (100, 100))
            pages.clear()
            self.assertIn(1, craft.read_pages(document))
            self.assertEqual(craft.read_asset(document, 'a' * 64), b'not-decoded-by-validation')

    def test_sync_renderers_and_translation_await_async_paragraph_callbacks(self):
        import asyncio
        from unittest.mock import patch
        from pdf_craft import ParagraphContent, PageGeometry
        from pdf_craft.common import save_xml
        from pdf_craft.extractor.chapter.chapter import Chapter, Reference, encode
        from tests.extraction_helpers import make_extraction
        from tests.test_paragraph_content import text, translated, translator

        async def collect(values, value):
            await asyncio.sleep(0)
            values.append(value)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source')
            save_xml(encode(Chapter(None, -1, [text(0, 'Body.')], [Reference(1, 1, '*', [text(1, 'Note.')])])),
                     root / 'source/chapters/chapter_head.xml')
            for operation, suffix in (('render_markdown', 'md'), ('render_epub', 'epub'),
                                      ('translate_extraction', 'pcex')):
                with self.subTest(operation=operation):
                    bodies, notes = [], []
                    options: dict[str, object] = dict(on_body=partial(collect, bodies),
                                                      on_footnote=partial(collect, notes))
                    if operation == 'translate_extraction':
                        options.update(translator=translator(), with_anchored=False)
                    with patch.object(XMLTranslator, '_translate_inline_segments_async', new=translated):
                        getattr(PDFCraft(), operation)(source, root / f'output.{suffix}', **options)
                    self.assertEqual(len(bodies), 1)
                    self.assertEqual(len(notes), 1)
                    self.assertIsInstance(bodies[0], ParagraphContent)
                    self.assertIsInstance(bodies[0].regions[0].page, PageGeometry)
                    self.assertIn('Body.', bodies[0].text)
                    self.assertIn('Note.', notes[0].text)
                    self.assertEqual(bodies[0].original, 'Body.' if operation == 'translate_extraction' else None)

    def test_public_values_are_exported_and_transport_fields_are_private(self):
        for name in ('SourceRegion', 'ParagraphContent', 'PDFSourceRegion',
                     'PDFParagraphContent', 'TextLine', 'FormulaPlacement', 'EraseArea',
                     'PageGeometry', 'ExtractionContext', 'MetadataContent',
                     'DocumentMetadata', 'DocumentAuthor', 'EpubFragmentContent'):
            self.assertIsInstance(getattr(pdf_craft, name), type)
        self.assertIs(pdf_craft.EpubTranslation, pdf_craft.EpubFragmentContent)
        self.assertFalse(any(field.name.startswith('preview') for field in fields(pdf_craft.PDFReplacement)))
        parameters = inspect.signature(XMLTranslator.translate_elements).parameters
        for name in ('on_mapping', 'on_result', 'on_task_mapping', 'on_before_submit', '_task_store', 'collect_results'):
            self.assertNotIn(name, parameters)
