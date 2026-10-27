# pylint: disable=protected-access
"""Real codec and fill-loop regressions for GitHub #526 and #527 (no network)."""
import unittest
from unittest.mock import AsyncMock
from xml.etree.ElementTree import Element, SubElement, fromstring

from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment, Content, BlockMember, encode
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.xml_translator.xml import encode_friendly, decode_friendly
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLFillError, TranslationTask
from pdf_craft.transformer.xml_translator.xml_translator.submitter import SubmitKind
from tests.test_anchored_content_translation import _repairing_translator


def source_chapter(styled=False):
    content: Content = ['Use a tag.']
    if styled:
        definition = tag_definition('span')
        assert definition is not None
        content = [HTMLTag[BlockMember](definition, [('class', f'style-{i}')], [letter])
                   for i, letter in enumerate('Yes')]
    fragment = SourceTextFragment(1, 0, (0, 0, 100, 20), content)
    paragraph = TextFlowItem('body', -1, [fragment])
    return Chapter(None, -1, [paragraph])


class FriendlyTextProtocolTests(unittest.TestCase):
    def test_long_citation_text_round_trips_without_formatting_spaces(self):
        text = ("① I will not discuss in this book, as I have done elsewhere, "
                "the broader implications of these terms. In my view, what is "
                "most important here is the structure of something beyond the parents.")
        for content in (text, ' ' + text + ' '):
            with self.subTest(content=content):
                root = Element('xml')
                SubElement(root, 'fragment', {'id': '1'}).text = content
                decoded = next(decode_friendly(encode_friendly(root), tags='xml'))
                self.assertEqual(decoded[0].text, content)

    def test_mixed_content_round_trips_exact_text_and_tail_boundaries(self):
        root = fromstring('<xml><fragment id="1">Before <span id="2">inside</span> after '
                          '<anchor id="3"/>ending. </fragment></xml>')
        decoded = next(decode_friendly(encode_friendly(root), tags='xml'))
        self.assertEqual(decoded[0].text, root[0].text)
        self.assertEqual(decoded[0][0].text, root[0][0].text)
        self.assertEqual(decoded[0][0].tail, root[0][0].tail)
        self.assertEqual(decoded[0][1].tail, root[0][1].tail)

    def test_adjacent_inline_slots_do_not_gain_indentation_text(self):
        root = fromstring('<xml><fragment id="1"><span id="2">one</span>'
                          '<span id="3">two</span></fragment></xml>')
        decoded = next(decode_friendly(encode_friendly(root), tags='xml'))
        self.assertEqual(list(decoded[0].itertext()), ['one', 'two'])

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
            ('&#128;', chr(128)), ('&#x1F600;', chr(0x1F600)),
            ('&quot;&apos;', '\"\''), ('&amp;lt;span&amp;gt;', '&lt;span&gt;'),
        ):
            with self.subTest(encoded=encoded):
                result = next(decode_friendly(f'<xml>{encoded}<span/>{encoded}</xml>', tags='xml'))
                self.assertEqual(result.text, plain)
                self.assertEqual(result[0].tail, plain)
                self.assertEqual(len(result), 1)

    def test_invalid_and_unknown_references_remain_literal(self):
        for text in ('&unknown;', '&lt', '&#0;', '&#xD800;', '&#1114112;', '&#xZZ;'):
            with self.subTest(text=text):
                result = next(decode_friendly(f'<xml>{text}</xml>', tags='xml'))
                self.assertEqual(result.text, text)

    def test_raw_comparisons_ampersands_and_tolerant_tag_closing(self):
        result = next(decode_friendly('<xml>A & B; x < y<span>tail</xml>', tags='xml'))
        self.assertEqual(result.text, 'A & B; x < y')
        self.assertEqual(result[0].text, 'tail')


class FillTextProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_generated_long_fill_is_canonical_on_first_attempt(self):
        text = ("① I will not discuss in this book, as I have done elsewhere, "
                "the broader implications of these terms. In my view, what is "
                "most important here is the structure of something beyond the parents.")
        root = Element('xml')
        SubElement(root, 'fragment', {'id': '1'}).text = text
        response = encode_friendly(root)
        translator, runtime = _repairing_translator([response, response])
        translator._translate_text_async = AsyncMock(return_value=text)
        result = await NarrativeXMLTransformer(translator).transform(source_chapter())
        self.assertEqual(runtime.context_value.calls, 1)
        item = result.flow_items[0]
        assert isinstance(item, TextFlowItem)
        fragment = item.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertEqual(fragment.content, [text])

    async def test_added_boundary_space_still_requires_corrected_fill(self):
        text = 'A complete sentence.'
        bad = '<xml><fragment id="1">A complete sentence. </fragment></xml>'
        good = '<xml><fragment id="1">A complete sentence.</fragment></xml>'
        translator, runtime = _repairing_translator([bad, good])
        translator._translate_text_async = AsyncMock(return_value=text)
        result = await NarrativeXMLTransformer(translator).transform(source_chapter())
        item = result.flow_items[0]
        assert isinstance(item, TextFlowItem)
        fragment = item.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertEqual(fragment.content, [text])
        self.assertEqual(runtime.context_value.calls, 2)

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


    async def test_generic_xhtml_fill_preserves_escaped_text(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">使用 &lt;span&gt; 和 &amp;lt;xml&amp;gt;。</p></xml>',
        ])
        text = '使用 <span> 和 &lt;xml&gt;。'
        translator._translate_text_async = AsyncMock(return_value=text)
        result, _ = await translator.translate_element(TranslationTask(
            fromstring('<body><p>Use a tag.</p></body>'), SubmitKind.REPLACE, None,
        ))
        self.assertEqual(''.join(result.itertext()), text)
        self.assertEqual(len(result[0]), 0, 'literal tags must not become XHTML children')
        self.assertEqual(runtime.context_value.calls, 1)

    async def test_noncanonical_fill_prompt_has_no_pcex_canonical_instructions(self):
        translator, runtime = _repairing_translator(['<xml><p id="1">是</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='是')
        result, _ = await translator.translate_element(TranslationTask(
            fromstring('<body><p>Yes</p></body>'), SubmitKind.REPLACE, None,
        ))
        self.assertEqual(''.join(result.itertext()), '是')
        messages = runtime.context_value.messages[0][0]
        self.assertNotIn('Canonical visible-text constraint:', messages[1].message)
        self.assertIn('empty inline', messages[0].message.lower())

    async def test_cdata_does_not_bypass_structural_or_full_text_validation(self):
        bad = '<xml><fragment id="1"><![CDATA[使用 <span> 标签。]]></fragment></xml>'
        good = '<xml><fragment id="1">使用 &lt;span&gt; 标签。</fragment></xml>'
        translator, runtime = _repairing_translator([bad, good])
        translator._translate_text_async = AsyncMock(return_value='使用 <span> 标签。')
        result = await NarrativeXMLTransformer(translator).transform(source_chapter())
        self.assertEqual(''.join(part.strip() for part in encode(result).itertext()), '使用 <span> 标签。')
        self.assertEqual(runtime.context_value.calls, 2)
