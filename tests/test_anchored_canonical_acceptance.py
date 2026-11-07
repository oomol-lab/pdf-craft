"""Canonical text acceptance and asset structure acceptance share one boundary."""
# pylint: disable=protected-access
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import parse

from pdf_craft import AsyncPDFCraft, RetryPolicy
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import BlockMember, Chapter, Content, SourceAsset, StandaloneAsset, decode, encode
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition
from pdf_craft.transformer.anchored_content import AnchoredContent
from pdf_craft.transformer.anchored_xml import AnchoredContentXMLTransformer
from tests.extraction_helpers import make_extraction
from tests.test_anchored_content_translation import _repairing_translator
from tests.test_source_math_identity import provider


def tag(name: str, children: Content, attributes=()) -> HTMLTag[BlockMember]:
    definition = tag_definition(name)
    assert definition is not None
    return HTMLTag[BlockMember](definition, list(attributes), children)


class AnchoredCanonicalAcceptanceTests(IsolatedAsyncioTestCase):
    async def test_empty_artist_caption_is_accepted_without_source_fallback(self):
        source = SourceAsset(1, 'image', (1, 1, 20, 20),
                             caption=['Original caption.'], content=['Read-only image content.'])
        original = deepcopy(source)
        translator, runtime = _repairing_translator([], direct_fill=True)
        calls = provider(translator, [''])
        target, = await AnchoredContentXMLTransformer(translator).transform_assets(
            [AnchoredContent('head', 0, -1, source, '')],
            error_policy=False, retry_policy=RetryPolicy(translation=0, fill=12))
        assert target is not None
        self.assertEqual(target.caption, [])
        self.assertEqual(target.content, source.content)
        self.assertEqual(source, original)
        self.assertEqual(target.identity, ('head', 0, -1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(runtime.context_value.calls, 0)

    async def test_empty_table_cell_survives_public_pcex_translation_and_coverage(self):
        table = SourceAsset(1, 'table', (1, 1, 20, 20), content=[tag('table', [
            tag('tr', [tag('td', ['First cell.'], [('colspan', '2')]),
                       tag('td', ['Second cell.'])])])])
        translator, runtime = _repairing_translator([], direct_fill=True)
        calls = provider(translator, ['Translated second cell.'])
        request = runtime.context_value.request

        async def allocate(messages, **kwargs):
            if messages[0].message.startswith('You are the structural fill engineer.'):
                payload = json.loads(messages[1].message)
                return json.dumps([dict(id=unit['id'], formulas=[], text=(
                    '' if 'First cell.' in unit['source'] else payload['translation']))
                                   for unit in payload['sources']])
            return await request(messages, **kwargs)

        runtime.context_value.request = allocate
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source')
            save_xml(encode(Chapter(None, -1, [StandaloneAsset(table)])),
                     root / 'source/chapters/chapter_head.xml')
            translated = await AsyncPDFCraft().translate_anchored_contents(
                source, root / 'translated.pcex', AnchoredContentXMLTransformer(translator),
                error_policy=False, retry_policy=RetryPolicy(translation=0, fill=12))
            with translated._materialize() as paths:
                chapter_xml = parse(paths.chapters / 'chapter_head.xml').getroot()
                cells = chapter_xml.findall('.//td')
                self.assertEqual(len(cells), 2)
                self.assertFalse(''.join(cells[0].itertext()).strip())
                self.assertEqual(cells[0].get('colspan'), '2')
                self.assertEqual(''.join(cells[1].itertext()), 'Translated second cell.')
                self.assertIsNone(next(iter(chapter_xml.iter('td'))).get('__PDF_CRAFT_ASSET_MATH_UNIT'))
                asset = parse(paths.translation).find('anchored/asset')
                assert asset is not None
                self.assertEqual(asset.get('state'), 'translated')
                item = decode(chapter_xml).flow_items[0]
                assert isinstance(item, StandaloneAsset)
                self.assertEqual(item.asset.ref, 'table')
        self.assertEqual(len(calls), 1)
        self.assertEqual(runtime.context_value.calls, 0)

    async def test_nonempty_caption_can_change_text_slots_without_changing_markup(self):
        source = SourceAsset(1, 'image', (1, 1, 20, 20),
                             caption=['Original ', tag('em', ['emphasis']), ' tail.'])
        translator, runtime = _repairing_translator([
            '<xml><caption id="1"><em>Translated caption.</em></caption></xml>'])
        calls = provider(translator, ['Translated caption.'])
        target, = await AnchoredContentXMLTransformer(translator).transform_assets(
            [AnchoredContent('head', 0, -1, source, '')], error_policy=False)
        assert target is not None
        self.assertEqual(target.caption, [tag('em', ['Translated caption.'])])
        self.assertEqual(len(calls), 1)
        self.assertEqual(runtime.context_value.calls, 1)

    async def test_post_fill_structure_corruption_still_fails_and_retains_evidence(self):
        source = SourceAsset(1, 'image', (1, 1, 20, 20), caption=['Original caption.'])
        translator, _ = _repairing_translator([], direct_fill=True)
        calls = provider(translator, [''])
        translate = translator.translate_element

        async def corrupt(task, **kwargs):
            target, payload = await translate(task, **kwargs)
            asset = target.find('flow/standalone-asset/asset')
            assert asset is not None
            asset.set('ref', 'table')
            return target, payload

        with TemporaryDirectory() as directory, patch.object(translator, 'translate_element', corrupt):
            translator._fill_llm.failure_log_path = Path(directory)
            with self.assertRaisesRegex(ValueError, 'source structure') as caught:
                await AnchoredContentXMLTransformer(translator).transform_assets(
                    [AnchoredContent('head', 0, -1, source, '')], error_policy=False)
            failure_id = getattr(caught.exception, 'failure_log_id', None)
            self.assertIsNotNone(failure_id)
            assert isinstance(failure_id, str)
            summary = json.loads((Path(directory) / failure_id / 'summary.json').read_text())
            self.assertEqual(summary['stage'], 'asset_structure')
            self.assertIn('Original caption.', summary['source_assets']['head:0:-1'])
            self.assertIn('ref="table"', summary['translated_xml'])
            self.assertNotIn('Original caption.', summary['translated_xml'])
            self.assertTrue(summary['validated_text_units'])
        self.assertEqual(len(calls), 1)
