"""Cross-chapter asset tokens and source errors at the real fill boundary."""
# pylint: disable=protected-access,cell-var-from-loop
import re
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock
from xml.etree.ElementTree import Element, SubElement

from pdf_craft.extractor.chapter.chapter import (
    Chapter, SourceAsset, SourceTextFragment, TextFlowItem, encode,
)
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer, _NarrativeAnchorProjection
from pdf_craft.transformer.xml_translator.segment import (
    BlockSegment, ImmutableBlockElement, SourceAnchorError, search_inline_segments, search_text_segments,
)
from pdf_craft.transformer.xml_translator.xml import decode_friendly, encode_friendly
from pdf_craft.transformer.xml_translator.xml_translator.callbacks import warp_callbacks
from tests.test_anchored_content_translation import _repairing_translator


def chapter(index):
    return Chapter(None, -1, [TextFlowItem('body', 0, [
        SourceTextFragment(index, 0, (0, 0, 90, 15), [f'Before {index}.']),
        SourceAsset(index, 'image', (0, 20, 90, 40), asset_hash=str(index) * 64),
        SourceTextFragment(index, 1, (0, 45, 90, 60), [f'After {index}.']),
    ])])


class FillAnchorScopeTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_shared_translation(self, source, streaming, boundaries):
        translator, runtime = _repairing_translator(())
        translator._translate_text_async = AsyncMock(
            side_effect=lambda text, **_kwargs: text.replace('Before', 'Avant').replace('After', 'Après'))
        seen = []

        async def request(*args, **kwargs):
            messages = args[0] if args else kwargs['input']
            match = re.search(r'```XML\n(.*?)```', messages[1].message, re.S)
            assert match is not None
            root = next(decode_friendly(match[1], tags='xml'))
            block_count = 0
            anchors = []
            for node in root:
                if node.tag == 'anchor':
                    anchors.append((node.get('anchor_key'), block_count))
                else:
                    block_count += 1
            seen.append(anchors)
            for node in root.iter():
                if node.text:
                    node.text = node.text.replace('Before', 'Avant').replace('After', 'Après')
            return encode_friendly(root)

        runtime.context_value.request = request
        transformer = NarrativeXMLTransformer(translator)
        if streaming:
            async def chapters():
                for index, value in enumerate(source):
                    yield value, index
            results = []
            async def result(value, _identity):
                results.append(value)
            await transformer._transform_stream(chapters(), result)
        else:
            results = await transformer.transform_many((value, index) for index, value in enumerate(source))
        self.assertEqual(seen, [boundaries])
        self.assertEqual(translator._translate_text_async.await_count, 1)
        self.assertEqual(len(results), len(source))
        for original, value in zip(source, results, strict=True):
            expected = deepcopy(original)
            for flow in expected.flow_items:
                assert isinstance(flow, TextFlowItem)
                for fragment in flow.children:
                    if isinstance(fragment, SourceTextFragment):
                        fragment.content = [part.replace('Before', 'Avant').replace('After', 'Après')
                                            if isinstance(part, str) else part for part in fragment.content]
            # Compare the entire chapter: text, asset identity and exact position.
            self.assertEqual(encode_friendly(encode(value)), encode_friendly(encode(expected)))

    async def test_cross_chapter_sync_and_stream_preserve_assets_and_share_one_fill(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                await self._assert_shared_translation([chapter(1), chapter(2)], streaming,
                                                      [('0:0', 1), ('1:0', 3)])

    async def test_trailing_chapter_anchor_stays_before_the_next_chapter(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                first, second = chapter(1), chapter(2)
                flow = first.flow_items[0]
                assert isinstance(flow, TextFlowItem)
                flow.children.pop()
                await self._assert_shared_translation([first], streaming, [('0:0', 1)])
                await self._assert_shared_translation([second], streaming, [('0:0', 1)])
                await self._assert_shared_translation([first, second], streaming,
                                                      [('0:0', 1), ('1:0', 2)])

    async def test_trailing_flow_anchor_stays_before_the_next_flow(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                first = chapter(1)
                flow = first.flow_items[0]
                assert isinstance(flow, TextFlowItem)
                flow.children.pop()
                second = chapter(2)
                first.flow_items.extend(second.flow_items)
                await self._assert_shared_translation([first], streaming,
                                                      [('0:0', 1), ('0:1', 2)])

    async def test_invalid_source_contract_never_calls_either_model(self):
        for malformed in ('duplicate', 'missing-key', 'dangling-boundary', 'unordered-boundary', 'text'):
            with self.subTest(malformed=malformed):
                translator, runtime = _repairing_translator(())
                translator._translate_text_async = AsyncMock(side_effect=AssertionError('must not translate'))
                source = Element('xml')
                SubElement(source, 'fragment').text = 'Before.'
                SubElement(source, 'fragment').text = 'After.'
                segments = list(search_inline_segments(search_text_segments(source)))
                token = Element('anchor', {'anchor_key': '0'})
                items = [ImmutableBlockElement(token, 1)]
                if malformed == 'duplicate':
                    items.append(ImmutableBlockElement(deepcopy(token), 2))
                elif malformed == 'missing-key':
                    token.attrib.clear()
                elif malformed == 'dangling-boundary':
                    items = [ImmutableBlockElement(token, 3)]
                elif malformed == 'unordered-boundary':
                    items = [ImmutableBlockElement(token, 2), ImmutableBlockElement(Element('anchor', {'anchor_key': '1'}), 0)]
                else:
                    token.text = 'Not an opaque anchor'
                with self.assertRaises(SourceAnchorError):
                    await translator._translate_inline_segment_group_async(
                        inline_segments=segments, callbacks=warp_callbacks(None, None, None),
                        immutable_elements=items, source_text_renderer=None, canonical_text_validator=None)
                translator._translate_text_async.assert_not_awaited()
                self.assertEqual(runtime.context_value.calls, 0)

    async def test_missing_or_duplicated_source_asset_mapping_is_not_model_feedback(self):
        for malformed in ('missing', 'duplicate', 'wrong-parent'):
            with self.subTest(malformed=malformed):
                root = encode(chapter(1))
                projection = _NarrativeAnchorProjection(root)
                projection.replace_assets()
                segments = list(search_inline_segments(search_text_segments(root)))
                parent = next(node for node in root.iter() if node.find('anchor') is not None)
                token = parent.find('anchor')
                assert token is not None
                if malformed == 'missing':
                    parent.remove(token)
                elif malformed == 'duplicate':
                    parent.append(deepcopy(token))
                else:
                    parent.remove(token)
                    root.append(token)
                with self.assertRaises(SourceAnchorError):
                    projection.immutable_elements_for_inline_segments(segments)

    def test_valid_source_does_not_weaken_candidate_anchor_checks(self):
        root = Element('xml')
        SubElement(root, 'fragment').text = 'Before.'
        SubElement(root, 'fragment').text = 'After.'
        segments = list(search_inline_segments(search_text_segments(root)))
        block = BlockSegment('xml', segments, [ImmutableBlockElement(Element('anchor', {'anchor_key': '0:0'}), 1)])
        valid = block.create_element()
        self.assertEqual(list(block.validate(valid)), [])
        for malformed in ('missing', 'duplicate', 'renamed', 'moved'):
            candidate = deepcopy(valid)
            token = candidate.find('anchor')
            assert token is not None
            if malformed == 'missing':
                candidate.remove(token)
            elif malformed == 'duplicate':
                candidate.insert(1, deepcopy(token))
            elif malformed == 'renamed':
                token.set('anchor_key', '1:0')
            else:
                candidate.remove(token)
                candidate.append(token)
            self.assertTrue(list(block.validate(candidate)), malformed)
