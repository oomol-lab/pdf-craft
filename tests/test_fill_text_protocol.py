# pylint: disable=protected-access
"""Real codec and fill-loop regressions for GitHub #526 and #527 (no network)."""
import unittest
from unittest.mock import AsyncMock
from xml.etree.ElementTree import Element, SubElement

from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment, encode
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.xml_translator.xml import encode_friendly, decode_friendly
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLFillError
from tests.test_anchored_content_translation import _repairing_translator


def source_chapter(styled=False):
    content = [HTMLTag(tag_definition('span'), [('class', f'style-{i}')], [letter])
               for i, letter in enumerate('Yes')] if styled else ['Use a tag.']
    return Chapter(None, -1, [TextFlowItem('body', -1, [
        SourceTextFragment(1, 0, (0, 0, 100, 20), content),
    ])])


class FriendlyTextProtocolTests(unittest.TestCase):
    def test_literal_text_and_tail_round_trip_without_double_decoding(self):
        for text in ('Use <span> and <xml>.', 'A & B; x < y > z',
                     '&lt;span&gt;', '&amp; &#60; &#x3c;', '"quotes" and apostrophes'):
            with self.subTest(text=text):
                root = Element('xml')
                fragment = SubElement(root, 'fragment', {'id': '1'})
                fragment.text = text
                child = SubElement(fragment, 'span', {'id': '2'})
                child.text = text
                child.tail = text
                decoded = next(decode_friendly(encode_friendly(root), tags='xml'))
                self.assertEqual((decoded[0].text or "").strip(), text)
                self.assertEqual((decoded[0][0].text or "").strip(), text)
                self.assertEqual((decoded[0][0].tail or "").strip(), text)
                self.assertEqual([node.tag for node in decoded.iter()], ['xml', 'fragment', 'span'])

    def test_entities_decode_once_after_structure_is_identified(self):
        for encoded, plain in (
            ('&lt;span&gt;', '<span>'), ('&#60;xml&#62;', '<xml>'),
            ('&#x3C;span&#x3E;', '<span>'), ('A &amp; B', 'A & B'),
            ('&quot;&apos;', '\"\''), ('&amp;lt;span&amp;gt;', '&lt;span&gt;'),
        ):
            with self.subTest(encoded=encoded):
                result = next(decode_friendly(f'<xml>{encoded}<span/>{encoded}</xml>', tags='xml'))
                self.assertEqual(result.text, plain)
                self.assertEqual(result[0].tail, plain)
                self.assertEqual(len(result), 1)

    def test_raw_comparisons_ampersands_and_tolerant_tag_closing(self):
        result = next(decode_friendly('<xml>A & B; x < y<span>tail</xml>', tags='xml'))
        self.assertEqual(result.text, 'A & B; x < y')
        self.assertEqual(result[0].text, 'tail')


class FillTextProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_pcex_escaped_translation_passes_real_canonical_fill(self):
        for text, escaped in [('使用 <span> 标签。', '使用 &lt;span&gt; 标签。'),
                              ('使用 &lt;xml&gt; 和 &。', '使用 &amp;lt;xml&amp;gt; 和 &amp;。')]:
            with self.subTest(text=text):
                response = f'<xml><fragment id="1">{escaped}</fragment></xml>'
                translator, runtime = _repairing_translator([response, response])
                translator._translate_text_async = AsyncMock(return_value=text)
                result = await NarrativeXMLTransformer(translator).transform(source_chapter())
                self.assertEqual(''.join(part.strip() for part in encode(result).itertext()), text)
                self.assertEqual(runtime.context_value.calls, 1)

    async def test_real_fill_prompt_allows_empty_format_slots_without_source_fallback(self):
        response = '<xml><fragment id="1"><span id="2">是</span><span id="3"/><span id="4"/></fragment></xml>'
        translator, runtime = _repairing_translator([response])
        translator._translate_text_async = AsyncMock(return_value='是')
        result = await NarrativeXMLTransformer(translator).transform(source_chapter(styled=True))
        self.assertEqual(''.join(part.strip() for part in encode(result).itertext()), '是')
        system = runtime.context_value.messages[0][0][0].message.lower()
        self.assertIn('empty inline', system)
        self.assertNotIn('use source text', system)
        self.assertNotIn('mixed language is acceptable', system)
        self.assertEqual(len(list(encode(result).iter('span'))), 3)

    async def test_source_padding_is_rejected_then_correct_empty_slots_are_accepted(self):
        bad = '<xml><fragment id="1"><span id="2">是</span><span id="3">e</span><span id="4">s</span></fragment></xml>'
        good = '<xml><fragment id="1"><span id="2">是</span><span id="3"/><span id="4"/></fragment></xml>'
        translator, runtime = _repairing_translator([bad, good])
        translator._translate_text_async = AsyncMock(return_value='是')
        result = await NarrativeXMLTransformer(translator).transform(source_chapter(styled=True))
        self.assertEqual(''.join(part.strip() for part in encode(result).itertext()), '是')
        self.assertEqual(runtime.context_value.calls, 2)
        translator, _ = _repairing_translator([bad, bad])
        translator._translate_text_async = AsyncMock(return_value='是')
        with self.assertRaises(XMLFillError):
            await NarrativeXMLTransformer(translator).transform(source_chapter(styled=True))
