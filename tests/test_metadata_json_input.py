"""Offline service #375 regressions through the real metadata request boundary."""
import json
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import Element, SubElement, tostring

from pdf_craft.extractor.metadata import extract_book_metadata_from_ocr
from pdf_craft.llm import LLM
from pdf_craft.llm.runtime import LLMRuntime


FIXTURE = Path(__file__).parent / 'assets' / 'metadata' / 'service_375' / 'page_3.xml'


def complete(value, page_index, *, field='title', evidence=None):
    return json.dumps({'action': 'complete', 'metadata': {
        field: {'value': value, 'page_index': page_index,
                'evidence': value if evidence is None else evidence},
    }}, ensure_ascii=False)


def write_page(root, index, text):
    page = Element('page', {'index': str(index)})
    SubElement(SubElement(page, 'body'), 'layout', {'ref': 'text'}).text = text
    (root / f'page_{index}.xml').write_text(tostring(page, encoding='unicode'), encoding='utf-8')


class MetadataJSONInputTests(IsolatedAsyncioTestCase):
    def llm(self):
        return LLM('unused', 'https://example.invalid/v1', 'model', 'o200k_base', retry_times=0)

    async def test_service_375_uses_parsed_text_for_original_title_evidence(self):
        """Cropped incident page: preserve '&' and its actual physical page number."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            copyfile(FIXTURE, root / 'page_3.xml')

            async def respond(messages, *_args, **_kwargs):
                payload = json.loads(messages[-1].message)
                self.assertEqual(payload['remaining_pages'], 0)
                self.assertEqual(payload['pages'], [{
                    'page_index': 3,
                    'text': '# Alice & Bob Learn Secure Coding\nExample Author',
                }])
                title = payload['pages'][0]['text'].splitlines()[0].removeprefix('# ')
                return complete(title, 3, field='original_title')

            with patch.object(LLMRuntime, '_invoke_async', side_effect=respond) as transport:
                result = await extract_book_metadata_from_ocr(root, self.llm())

            self.assertEqual(result.original_title, 'Alice & Bob Learn Secure Coding')
            transport.assert_awaited_once()

    async def test_read_more_preserves_literal_entities_tags_quotes_and_newlines(self):
        text = 'Literal &amp; and &#38; with <span> and "quotes"\nNext line & 中文'
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for index in (1, 2, 3):
                write_page(root, index, f'Front page {index} & text')
            write_page(root, 7, text)
            write_page(root, 9, 'Last page')
            turns = []

            async def respond(messages, *_args, **_kwargs):
                payload = json.loads(messages[-1].message)
                turns.append(payload)
                if len(turns) == 1:
                    self.assertEqual(payload, {'remaining_pages': 2, 'pages': [
                        {'page_index': i, 'text': f'Front page {i} & text'} for i in (1, 2, 3)
                    ]})
                    return '{"action":"read_more","page_count":2}'
                self.assertEqual(payload, {'remaining_pages': 0, 'pages': [
                    {'page_index': 7, 'text': text}, {'page_index': 9, 'text': 'Last page'},
                ]})
                return complete(payload['pages'][0]['text'], 7)

            with patch.object(LLMRuntime, '_invoke_async', side_effect=respond) as transport:
                result = await extract_book_metadata_from_ocr(root, self.llm())

            self.assertEqual(result.title, text)
            self.assertEqual(transport.await_count, 2)

    async def test_entity_spelling_is_not_blindly_decoded_to_accept_wrong_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_page(root, 1, 'Alice & Bob')
            responses = [complete('Alice & Bob', 1, evidence='Alice &amp; Bob'),
                         complete('Alice & Bob', 1)]
            with patch.object(LLMRuntime, '_invoke_async', side_effect=responses) as transport:
                result = await extract_book_metadata_from_ocr(root, self.llm())
            self.assertEqual(result.title, 'Alice & Bob')
            self.assertEqual(transport.await_count, 2)

    async def test_unloaded_page_still_requires_repair(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_page(root, 1, 'Alice & Bob')
            responses = [complete('Alice & Bob', 99), complete('Alice & Bob', 1)]
            with patch.object(LLMRuntime, '_invoke_async', side_effect=responses) as transport:
                result = await extract_book_metadata_from_ocr(root, self.llm())
            self.assertEqual(result.title, 'Alice & Bob')
            self.assertEqual(transport.await_count, 2)
