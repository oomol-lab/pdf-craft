"""Small offline reproductions of the downstream paid-smoke failures (#936)."""
# pylint: disable=protected-access,unused-argument
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import RetryPolicy, SubmitKind
from pdf_craft._policy import policy_operation
from pdf_craft.extractor.chapter.chapter import SourceAsset
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from pdf_craft.transformer.canonical_math import CanonicalMath
from pdf_craft.transformer.request_batch import BatchResponseError, RequestBatcher
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.segment import search_inline_segments, search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.test_request_batch import translator_fixture
from tests.test_anchored_content_translation import _repairing_translator
from tests.test_canonical_math_allocation import source
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_structured_translation import offline_translator


class DownstreamSmokeRegressions(IsolatedAsyncioTestCase):
    @policy_operation
    async def _translations(self, translator, texts, *, retry_policy=None):
        return await asyncio.gather(*(translator._translate_text_async(text) for text in texts),
                                    return_exceptions=True)

    async def test_partial_envelope_retries_only_unbound_translation(self):
        translator, _ = translator_fixture()
        invocations = []
        async def invoke(messages, *args, **kwargs):
            invocations.append(messages)
            if len(invocations) == 1:
                return '[{"id":"0","text":"accepted"},{"id":"1","text":"x"},{"id":"1","text":"y"}]'
            return messages[-1].message
        translator._translation_runtime.__dict__['_invoke'] = invoke
        result = await self._translations(translator, ['source A', 'source B'], retry_policy=RetryPolicy(translation=1))
        self.assertEqual(result, ['accepted', 'source B'])
        self.assertEqual(len(invocations), 2)
        self.assertNotIn('source A', str(invocations[1]))

    async def test_envelope_exhaustion_is_bounded_and_retains_raw_response(self):
        with TemporaryDirectory() as directory:
            translator, _ = translator_fixture()
            translator._translation_llm.failure_log_path = Path(directory)
            invocations = 0
            async def invoke(*_args, **_kwargs):
                nonlocal invocations
                invocations += 1
                return '[{"id":"unknown","text":"bad identity"}]'
            translator._translation_runtime.__dict__['_invoke'] = invoke
            result = await self._translations(translator, ['source A', 'source B'],
                                              retry_policy=RetryPolicy(translation=1))
            self.assertEqual(invocations, 2)
            self.assertTrue(all(isinstance(error, BatchResponseError) for error in result))
            self.assertEqual(len(list(Path(directory).iterdir())), 2)
            for error in result:
                identifier = getattr(error, 'failure_log_id')
                log = Path(directory) / identifier
                summary = json.loads((log / 'summary.json').read_text())
                self.assertEqual(summary['stage'], 'text_translation')
                self.assertEqual(summary['attempts'], 2)
                records = [json.loads(row) for row in (log / 'transports.jsonl').read_text().splitlines()]
                self.assertTrue(any(row.get('response') == '[{"id":"unknown","text":"bad identity"}]'
                                    for row in records))

    def test_bad_entry_does_not_discard_unambiguous_siblings(self):
        self.assertEqual(RequestBatcher._answers('[{"id":"0","text":"ok"},{"id":"1","text":"x"},'
                         '{"id":"1","text":"y"},{"id":"9","text":"unknown"}]', 2), {'0': 'ok'})

    def test_ambiguous_json_object_does_not_erase_other_identities(self):
        self.assertEqual(RequestBatcher._answers(
            '[{"id":"0","text":"ok"},{"id":"1","id":"2","text":"ambiguous"}]', 3),
            {'0': 'ok'})
        self.assertEqual(RequestBatcher._answers(
            '[{"id":"0","text":"ok"},{"id":"0","text":null}]', 1), {})

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

    def test_mixed_literal_math_still_requires_exact_canonical_text(self):
        protocol = StructuredTranslation()
        segments = list(search_inline_segments(search_text_segments(fromstring('<p>source</p>'))))
        segments[0].id = 1
        canonical = '$x$ and $y$'
        math = CanonicalMath(canonical)
        response = fromstring('<xml><p id="1"><math-ref key="m1"/> and $y$</p></xml>')
        self.assertIsNone(math.expand(response, require_complete=False))
        self.assertIsNone(protocol.validate(segments, canonical, response))
        broken = fromstring('<xml><p id="1"><math-ref key="m1"/> and </p></xml>')
        self.assertIsNone(math.expand(broken, require_complete=False))
        self.assertIsNotNone(protocol.validate(segments, canonical, broken))

    async def test_extra_math_reference_does_not_require_an_extra_source_slot(self):
        # The real character paragraph has 22 canonical formulas but 21 source
        # slots. Crop to three references/two slots, keeping the same mismatch.
        bad = ('<xml><fragment id="1"><expression id="2"><math-ref key="m1"/></expression> '
               '<expression id="3"><math-ref key="m2"/></expression> '
               '<expression id="4"><math-ref key="m3"/></expression></fragment></xml>')
        good = ('<xml><fragment id="1"><expression id="2"><math-ref key="m1"/> '
                '<math-ref key="m2"/></expression> '
                '<expression id="3"><math-ref key="m3"/></expression></fragment></xml>')
        translator, runtime = _repairing_translator([bad, good])
        translator._translate_text_async = AsyncMock(return_value='$x$ $y$ $z$')
        await NarrativeXMLTransformer(translator).transform(source(), error_policy=False)
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertIn('Unexpected inline element', runtime.context_value.messages[1][0][-1].message)

    def test_cross_slot_gap_uses_decomposed_character_offsets(self):
        root = fromstring('<text><fragment>é label</fragment><fragment>next</fragment></text>')
        segments = list(search_inline_segments(search_text_segments(root)))
        for identity, segment in enumerate(segments, 1):
            segment.id = identity
        response = fromstring('<xml><fragment id="1">e\u0301 label</fragment><fragment id="2">next</fragment></xml>')
        protocol = StructuredTranslation(owner=lambda _segment: root)
        self.assertIsNone(protocol.validate(segments, 'é label next', response))
        self.assertEqual(response[0].text, 'e\u0301 label ')

    def test_cosmetic_gap_between_slots_of_one_owner_survives_submission(self):
        source = fromstring('<text><fragment>11. Visit disposition</fragment><fragment>Figure 10</fragment></text>')
        segments = list(search_inline_segments(search_text_segments(source)))
        for identity, segment in enumerate(segments, 1):
            segment.id = identity
        protocol = StructuredTranslation(owner=lambda _segment: source)
        response = fromstring('<xml><fragment id="1">11. 就诊处置</fragment><fragment id="2">图10</fragment></xml>')
        self.assertIsNone(protocol.validate(segments, '11. 就诊处置 图10', response))
        self.assertEqual(response[0].text, '11. 就诊处置 ')
        self.assertIsNone(response[0].tail)
        self.assertNotIn(b'math-ref', tostring(response))
