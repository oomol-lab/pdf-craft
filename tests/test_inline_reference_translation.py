"""Deterministic template round trips preserve zero-width inline nodes."""

import unittest
from collections.abc import Iterable
from xml.etree.ElementTree import Element, fromstring, tostring

from tiktoken import get_encoding

from pdf_craft.extractor.chapter.chapter import decode, encode
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.xml_translator.segment import (
    BlockSegment, InlineSegment, search_inline_segments, search_text_segments,
)
from pdf_craft.transformer.xml_translator.xml_translator.callbacks import warp_callbacks
from pdf_craft.transformer.xml_translator.xml_translator.stream_mapper import (
    InlineSegmentMapping, XMLStreamMapper,
)
from pdf_craft.transformer.xml_translator.xml_translator.submitter import SubmitKind, submit
from pdf_craft.transformer.xml_translator.xml_translator.validation import (
    generate_error_message, nest_as_errors_group,
)


def _chapter(fragments: list[str]) -> Element:
    slots = ''.join(
        f'<fragment page_index="1" source_order="{index}" bbox="0,0,100,20">'
        f'{content}</fragment>'
        for index, content in enumerate(fragments, start=1)
    )
    return fromstring(
        '<chapter><flow><text role="body">' + slots + '</text></flow>'
        '<references><ref id="1-1"><mark>1</mark><flow><text role="body">'
        '<fragment page_index="1" source_order="3" bbox="0,30,100,50">Footnote</fragment>'
        '</text></flow></ref><ref id="1-2"><mark>2</mark><flow/></ref></references></chapter>'
    )


def _identity_mappings(inlines: Iterable[InlineSegment]) -> list[InlineSegmentMapping]:
    block = BlockSegment('xml', list(inlines))
    template = block.create_element()
    errors = list(block.validate(template))
    if errors:
        raise AssertionError(errors)
    return [
        (item.origin_text_segments[0].block_parent,
         list(search_text_segments(item.submitted_element)))
        for item in block.submit(template)
    ]


class _IdentityTranslator:
    """Exercise real segmentation, template validation, mapping and submission."""

    def __init__(self):
        self.outputs: list[Element] = []

    async def translate_element(self, task, **kwargs):
        return (await self.translate_elements([task], **kwargs))[0]

    async def translate_elements(self, tasks, **kwargs):
        callbacks = warp_callbacks(
            interrupt_source_text_segments=kwargs['interrupt_source_text_segments'],
            interrupt_translated_text_segments=kwargs['interrupt_translated_text_segments'],
            interrupt_block_element=kwargs['interrupt_block_element'],

        )
        mapper = XMLStreamMapper(get_encoding('cl100k_base'), max_group_score=10_000)

        async def identity(inlines: list[InlineSegment]) -> list[InlineSegmentMapping | None]:
            return list(_identity_mappings(inlines))

        task_list = list(tasks)
        by_root = {id(task.element): task for task in task_list}
        results = []
        async for root, mappings in mapper.map_stream_async(
            iter(task.element for task in task_list), callbacks, identity, window=1,
        ):
            task = by_root[id(root)]
            translated = submit(root, task.action, mappings)
            self.outputs.append(translated)
            results.append((translated, task.payload))
        return results


class TestEmptyInlineRoundTrip(unittest.TestCase):
    def test_xhtml_empty_nodes_keep_order_attributes_and_text(self):
        for content in (
            'Before<br/>after',
            '<br/>Before<img src="cover.png"/>after<wbr/>',
            'Before<span><a id="target"/></span>after',
            '<a id="one"/><a id="two"/>',
            'Before<token display="inline" key="a"/>after',
            '<em>one</em><em>two</em><br/><em>three</em>',
        ):
            with self.subTest(content=content):
                root = fromstring('<html><body><p>' + content + '</p></body></html>')
                expected = tostring(root)
                result = submit(root, SubmitKind.REPLACE, _identity_mappings(
                    search_inline_segments(search_text_segments(root)),
                ))
                self.assertEqual(tostring(result), expected)

    def test_empty_inline_is_part_of_template_validation(self):
        root = fromstring('<div><p>Before<br/>after</p></div>')
        block = BlockSegment('xml', list(search_inline_segments(search_text_segments(root))))
        template = block.create_element()
        line_break = template.find('.//br')
        self.assertIsNotNone(line_break)
        assert line_break is not None
        template[0].remove(line_break)
        errors = nest_as_errors_group(block.validate(template))
        self.assertIsNotNone(errors)
        assert errors is not None
        message = generate_error_message(get_encoding('cl100k_base'), errors)
        self.assertIn('Missing `<br>`', message or '')

    def test_empty_block_remains_a_block(self):
        root = fromstring('<div><p>Before</p><section key="kept"/><p>after</p></div>')
        expected = tostring(root)
        result = submit(root, SubmitKind.REPLACE, _identity_mappings(
            search_inline_segments(search_text_segments(root)),
        ))
        self.assertEqual(tostring(result), expected)

    def test_explicit_block_display_overrides_html_inline_default(self):
        root = fromstring(
            '<div><mark display="block">1</mark><section/>'
            '<mark display="block">2</mark></div>'
        )
        expected = tostring(root)
        inlines = list(search_inline_segments(search_text_segments(root)))
        self.assertEqual([inline.parent.tag for inline in inlines], ['mark', 'mark'])
        self.assertEqual(tostring(submit(root, SubmitKind.REPLACE,
                                         _identity_mappings(inlines))), expected)


class TestInlineReferenceTranslation(unittest.IsolatedAsyncioTestCase):
    async def test_identity_translation_keeps_refs_in_their_original_fragment_positions(self):
        for fragments in (
            ['Before<ref id="1-1"/>after'],
            ['<ref id="1-1"/>Before<ref id="1-2"/>'],
            ['Before<ref id="1-1"/><ref id="1-2"/>after'],
            ['Before<ref id="1-1"/><ref id="1-1"/>after'],
            ['Before<ref id="1-1"/>', '<ref id="1-2"/>after'],
            ['<ref id="1-1"/>', 'Following text'],
        ):
            with self.subTest(fragments=fragments):
                chapter = decode(_chapter(fragments))
                expected = tostring(encode(chapter))
                translator = _IdentityTranslator()
                result = await NarrativeXMLTransformer(translator).transform(chapter)
                # Exact encoded equality checks ownership, text positions, IDs and geometry,
                # including the reference definition and its separately translated flow.
                self.assertEqual(tostring(encode(result)), expected)
                self.assertEqual(tostring(encode(chapter)), expected)
                decode(translator.outputs[0], allow_legacy=False)
                self.assertFalse(translator.outputs[0].findall('.//ref[@display]'))

    async def test_batch_translation_uses_the_same_reference_adapter(self):
        chapters = [
            decode(_chapter(['Before<ref id="1-1"/>after'])),
            decode(_chapter(['<ref id="1-2"/>Before', 'after<ref id="1-1"/>'])),
        ]
        translator = _IdentityTranslator()
        result = await NarrativeXMLTransformer(translator).transform_many(
            (chapter, str(index)) for index, chapter in enumerate(chapters)
        )
        self.assertEqual([tostring(encode(chapter)) for chapter in result],
                         [tostring(encode(chapter)) for chapter in chapters])
        for root in translator.outputs:
            decode(root, allow_legacy=False)
