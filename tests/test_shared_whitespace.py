"""Ordinary text normalization and service #369's actual XML fill candidates."""
# pylint: disable=protected-access
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from types import SimpleNamespace
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring

from pdf_craft.common.whitespace import normalize_whitespace
from pdf_craft.extractor.chapter.chapter import Chapter, SourceTextFragment, TextFlowItem, decode, encode
from pdf_craft.pipeline.epub.cfi import ContentCFILocator
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.segment import search_text_segments, search_inline_segments
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft import SubmitKind
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.free_translation import allocate
from pdf_craft.transformer.canonical_text import restore_canonical_whitespace
from pdf_craft.common import save_xml
from pdf_craft.pipeline.pdf import PDFTranslationPipeline, PDFPatcher
from tests.extraction_helpers import make_extraction
from tests.test_anchored_content_translation import _repairing_translator, _CapturePatcher


async def patch_text(chapter: Chapter, fixture) -> str:
    """Real content traversal/coverage; only the final PDF drawing is replaced."""
    with TemporaryDirectory() as directory:
        root = Path(directory) / 'source'
        extraction = make_extraction(root, page_pixel_sizes={
            int(index): tuple(size) for index, size in fixture['page_sizes'].items()})
        save_xml(encode(chapter), root / 'chapters/chapter_head.xml')
        first = fixture['source_location']
        (root / 'translation.xml').write_text(
            '<translation><narrative><paragraph chapter_id="head" '
            f'page_index="{first["page_indexes"][0]}" order="{first["source_orders"][0]}" '
            'state="translated"/></narrative></translation>')
        capture = _CapturePatcher()
        await PDFTranslationPipeline(patcher=cast(PDFPatcher, capture)).patch(
            Path('input.pdf'), Path('output.pdf'), extraction)
        assert len(capture.replacements) == 1
        return capture.replacements[0].text


class SharedWhitespaceTests(TestCase):
    def test_unicode_spaces_controls_and_padding(self):
        spaces = ''.join(chr(i) for i in range(0x110000) if chr(i).isspace())
        for gap in spaces + '\x08':
            self.assertEqual(normalize_whitespace('a' + gap + gap + 'b'), 'a b')
        for gap in '\u200b\u2060\ufeff':
            self.assertEqual(normalize_whitespace('a' + gap + 'b'), 'ab')
        self.assertEqual(normalize_whitespace(' \u3000a\ufeff\x08 \u200b b\t'), ' a b ')
        self.assertEqual(normalize_whitespace(' \u3000a\t', trim=True), 'a')
        text = 'a\u200cب\u200d👩\u200d💻\ufe0f'
        self.assertEqual(normalize_whitespace(text), text)
        normalized = normalize_whitespace(' \ufeff\u3000a\x08b\u200b ')
        self.assertEqual(normalize_whitespace(normalized), normalized)

    def test_normal_and_preserved_text_use_different_boundaries(self):
        root = fromstring('<fragment>a\u200bb\u3000c</fragment>')
        self.assertTrue(restore_canonical_whitespace([root], 'ab c'))
        self.assertEqual(root.text, 'ab c')
        source = fromstring('<fragment xml:space="preserve">original</fragment>')
        root = fromstring('<fragment>ab</fragment>')
        self.assertTrue(restore_canonical_whitespace([root], 'a\u3000b', [source]))
        self.assertEqual(root.text, 'a\u3000b')

    def test_allocation_comparison_and_target_offsets_share_normalization(self):
        calls = [SimpleNamespace(source_math=None, preserve_space=False)]
        answer = allocate(json.dumps([dict(id='0', text='甲乙', formulas=[])]), calls, '甲\u3000\u3000乙')
        self.assertEqual(answer, {'0': '甲 乙'})
        with self.assertRaises(ValueError):
            allocate(json.dumps([dict(id='0', text='甲丙', formulas=[])]), calls, '甲\u3000乙')

    def test_raw_origins_and_cfi_survive_a_shorter_normalized_view(self):
        root = fromstring('<body><p>A\u3000\u3000B😀</p></body>')
        locator = ContentCFILocator(root)
        segments = list(search_text_segments(root, track_origins=True))
        self.assertEqual(''.join(part.text for part in segments), 'A B😀')
        self.assertEqual(root[0].text, 'A\u3000\u3000B😀')
        captured = locator.capture(segments)
        self.assertEqual(captured.start.offset, 0)
        self.assertEqual(captured.end.offset, 6)  # Original UTF-16, not normalized length.

    def test_ordinary_metadata_and_ocr_normalize_backspace_before_xml_filtering(self):
        from pdf_craft.document import DocumentAuthor, DocumentMetadata
        from pdf_craft.pdf.page_extractor import PageExtractorNode
        self.assertEqual(DocumentAuthor('A\x08B\u3000C').name, 'A B C')
        self.assertEqual(DocumentMetadata(title='A\x08B').title, 'A B')
        self.assertEqual(object.__new__(PageExtractorNode)._normalize_text('A\x08B\u200bC'), 'A BC')

    def test_tail_recovery_never_crosses_an_anchor_or_a_rich_slot(self):
        from xml.etree.ElementTree import tostring
        owner = fromstring('<body><fragment>a</fragment><fragment>c</fragment></body>')
        protocol = StructuredTranslation(owner=lambda _segment: owner)
        segments = list(search_inline_segments(search_text_segments(owner)))
        for index, segment in enumerate(segments, 1):
            segment.id = index
        for response in ('<xml><fragment id="1">a</fragment>b<anchor/><fragment id="2">c</fragment></xml>',
                         '<xml><fragment id="1"><em>a</em></fragment>b<fragment id="2">c</fragment></xml>'):
            candidate = fromstring(response)
            before = tostring(candidate)
            error = protocol.validate(segments, 'abc', candidate)
            assert error is not None
            self.assertIn('ambiguous', error)
            self.assertEqual(tostring(candidate), before)


class Service369ReplayTests(IsolatedAsyncioTestCase):
    async def test_epub_xhtml_prose_uses_the_same_engineering_rule(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">Hello<em>world</em>.</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='Hello\u3000world\u200b.')
        result, _ = await StructuredTranslation().translate(
            translator, TranslationTask(fromstring('<body><p>Source <em>words</em>.</p></body>'),
                                        SubmitKind.REPLACE, None), error_policy=False)
        self.assertEqual(''.join(result.itertext()), 'Hello world.')
        self.assertEqual(result.find('.//em').text, 'world')
        self.assertEqual(runtime.context_value.calls, 1)

    async def test_recorded_candidates_are_adopted_without_repair_or_omission(self):
        fixtures = json.loads((Path(__file__).parent / 'assets/translation/service_369_whitespace.json').read_text())
        for fixture in fixtures:
            for response in fixture['responses']:
                with self.subTest(log=fixture['failure_log_id'], response=response):
                    # Preserve the actual owner, fragment/page identities and
                    # source geometry from the original PCEX, not just the prompt.
                    source = decode(fromstring('<chapter><flow>' + fixture['source_owner_xml'] + '</flow></chapter>'))
                    translator, runtime = _repairing_translator([response])
                    translate = AsyncMock(return_value=fixture['canonical'])
                    translator._translate_text_async = translate
                    target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
                    self.assertEqual(runtime.context_value.calls, 1)
                    self.assertEqual(translate.await_count, 1)
                    item = target.flow_items[0]
                    assert isinstance(item, TextFlowItem)
                    actual = ''.join(part for child in item.children if isinstance(child, SourceTextFragment)
                                     for part in child.content if isinstance(part, str))
                    self.assertEqual(actual,
                                     normalize_whitespace(fixture['canonical'], trim=True))
                    self.assertNotIn('\u3000', actual)
                    self.assertIn('而', actual)
                    self.assertIn('而', await patch_text(target, fixture))
