"""Validate final mathematical values, beyond acceptance of the fill response."""
# pylint: disable=protected-access
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import tostring

from pdf_craft.markdown.paragraph import HTMLTag, tag_definition
from pdf_craft.extractor.chapter.chapter import (
    Chapter, SourceAsset, StandaloneAsset, encode,
)
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from tests.test_structured_translation import offline_translator
from tests.fill_fixtures import expression








class AnchoredFormulaDeliveryTests(IsolatedAsyncioTestCase):


    async def test_asset_mixed_cell_and_nested_blocks_preserve_owning_markup(self):
        def tag(name, children):
            definition = tag_definition(name)
            assert definition is not None
            return HTMLTag(definition, [], children)
        source = SourceAsset(1, 'table', (0, 0, 90, 20), content=[tag('table', [tag('tr', [
            tag('td', ['before ', tag('p', ['value ', expression('x=y')]), ' after']),
        ])])])
        result, = await AnchoredContentXMLTransformer(offline_translator()).transform_assets([
            AnchoredContent('a', 0, -1, source, '')], error_policy=False)
        assert result is not None
        # Decode through the actual asset pipeline before inspecting the
        # final table tree; two allocations share the outer td block.
        encoded = encode(Chapter(None, -1, [StandaloneAsset(SourceAsset(
            1, 'table', source.bbox, content=result.content))]))
        self.assertEqual([node.text for node in encoded.findall('.//td/p/inline_expr')], ['x=y'])
        self.assertEqual(len(encoded.findall('.//td/p')), 1)
        self.assertNotIn('__PDF_CRAFT', tostring(encoded, encoding='unicode'))
