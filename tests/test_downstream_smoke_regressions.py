"""Small offline reproductions of the downstream paid-smoke failures (#936)."""
# pylint: disable=protected-access,unused-argument
import asyncio
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import SubmitKind
from pdf_craft._policy import policy_operation
from pdf_craft.extractor.chapter.chapter import SourceAsset
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLFillError
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.segment import search_inline_segments, search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.translation_model import translator_fixture
from tests.test_anchored_content_translation import _repairing_translator
from tests.fill_fixtures import source as formula_source
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_structured_translation import offline_translator


class DownstreamSmokeRegressions(IsolatedAsyncioTestCase):
    @policy_operation
    async def _translations(self, translator, texts, *, retry_policy=None):
        return await asyncio.gather(*(translator._translate_text_async(text) for text in texts),
                                    return_exceptions=True)


    async def test_identical_text_and_quotations_are_not_quality_retry_triggers(self):
        translator, model = translator_fixture()
        source = 'A name, a quotation: «原文», and a mathematical symbol x.'
        self.assertEqual(await self._translations(translator, [source]), [source])
        self.assertEqual(len(model.calls), 1)

    async def test_epub_whitespace_only_units_preserve_text_without_requests(self):
        translator, model = translator_fixture()
        root = fromstring('<html><body><p xml:space="preserve">  </p><p> </p></body></html>')
        before = tostring(root)
        result, _ = await StructuredTranslation().translate(
            translator, TranslationTask(root, SubmitKind.REPLACE, None))
        self.assertEqual(tostring(result), before)
        self.assertEqual(model.calls, [])

    async def test_empty_cells_and_their_tails_keep_parent_and_position(self):
        def tag(name, children, attrs=()):
            definition = tag_definition(name)
            assert definition is not None
            return HTMLTag(definition, list(attrs), children)
        source = SourceAsset(4, 'table', (0, 0, 100, 100), content=[tag('table', [
            tag('td', [], [('colspan', '13')]), 'First field',
            tag('td', [], [('colspan', '13')]), 'Second field',
            tag('td', [], [('colspan', '13')]), 'Last field'])])
        saved = deepcopy(source)
        target, = await AnchoredContentXMLTransformer(offline_translator()).transform_assets([
            AnchoredContent('1', 32, -1, source, '')], error_policy=False)
        assert target is not None
        self.assertEqual(target.content, source.content)
        self.assertEqual(source, saved)


    async def test_extra_canonical_math_cannot_be_adopted_as_a_new_source_formula(self):
        translator, runtime = _repairing_translator(['<xml><fragment id="1"/></xml>'] * 2)
        translator._translate_text_async = AsyncMock(return_value='$x$ $y$ $z$')
        with self.assertRaises(XMLFillError):
            await NarrativeXMLTransformer(translator).transform(formula_source(), error_policy=False)
        self.assertEqual(runtime.context_value.calls, 2)

    def test_cross_slot_gap_uses_decomposed_character_offsets(self):
        root = fromstring('<text><fragment>é label</fragment><fragment>next</fragment></text>')
        segments = list(search_inline_segments(search_text_segments(root)))
        for identity, segment in enumerate(segments, 1):
            segment.id = identity
        response = fromstring('<xml><fragment id="1">e\u0301 label</fragment><fragment id="2">next</fragment></xml>')
        protocol = StructuredTranslation(owner=lambda _segment: root)
        self.assertIsNone(protocol.validate(segments, 'é label next', response))
        self.assertEqual(response[0].text, 'e\u0301 label')

    def test_cosmetic_gap_between_slots_of_one_owner_survives_submission(self):
        source = fromstring('<text><fragment>11. Visit disposition</fragment><fragment>Figure 10</fragment></text>')
        segments = list(search_inline_segments(search_text_segments(source)))
        for identity, segment in enumerate(segments, 1):
            segment.id = identity
        protocol = StructuredTranslation(owner=lambda _segment: source)
        response = fromstring('<xml><fragment id="1">11. 就诊处置</fragment><fragment id="2">图10</fragment></xml>')
        self.assertIsNone(protocol.validate(segments, '11. 就诊处置 图10', response))
        self.assertEqual(response[0].text, '11. 就诊处置')
        self.assertIsNone(response[0].tail)
        self.assertNotIn(b'math-ref', tostring(response))
