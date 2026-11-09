"""Model-boundary regressions; only the provider is scripted, never XML acceptance."""
# pylint: disable=protected-access
import json
import re
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import Element, SubElement, fromstring

from pdf_craft import RetryPolicy
from pdf_craft._policy import policy_operation
from pdf_craft.concurrency import ConcurrentExecutor, FixedCapacity
from pdf_craft.llm import LLM, Message, MessageRole
from pdf_craft.llm.runtime import runtime_for
from pdf_craft.transformer.canonical_projection import SourceMath
from pdf_craft.transformer.free_translation import fill_translation_group
from pdf_craft.transformer.source_math import SourceMathTranslation
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml.friendly import encode_friendly, decode_friendly
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask, SubmitKind
from tests.test_anchored_content_translation import _repairing_translator
from tests.translation_model import translator_fixture


def decoded(text, tag):
    return next(decode_friendly(text, tags=tag))


class LLMXMLBoundaryTests(IsolatedAsyncioTestCase):
    @policy_operation
    async def test_allocation_json_xml_fields_use_codec_on_initial_and_repair_requests(self):
        config = LLM('test', 'https://example.invalid', 'offline', 'cl100k_base')
        runtime = runtime_for(config, ConcurrentExecutor(FixedCapacity(1)))
        node = Element('inline_expr', {'kind': r'\(', 'label': '中文 " & <tag>\n'})
        node.text = 'x & \n  y '
        child = SubElement(node, 'span')
        child.text = '<span> &lt;span&gt;'
        child.tail = ' tail &'
        math = SourceMathTranslation([SourceMath(r'\(x\)', node)])
        calls = [SimpleNamespace(source_math=math, messages=[Message(MessageRole.USER, 'x')])]
        answer = json.dumps([dict(id='0', text='x', formulas=[dict(key='s1', text='x')])])
        transport = AsyncMock(side_effect=['[]', answer])
        with patch.object(runtime, '_invoke', transport, create=True), patch(
                'pdf_craft.transformer.free_translation.encode_friendly', wraps=encode_friendly) as encoder:
            result = await fill_translation_group(runtime, calls, 'x')
        self.assertEqual(result, {'0': r'\(x\)'})
        encoder.assert_called_once_with(node, preserve_whitespace=True)
        self.assertEqual(transport.await_count, 2)
        for invocation in transport.await_args_list:
            payload = json.loads(invocation.args[0][1].message)
            xml = payload['sources'][0]['formula_nodes']['s1']
            restored = decoded(xml, 'inline_expr')
            self.assertEqual(restored.attrib, node.attrib)
            self.assertEqual(restored.text, node.text)
            self.assertEqual(restored[0].text, child.text)
            self.assertEqual(restored[0].tail, child.tail)
            self.assertEqual(payload['translation'], 'x')
        self.assertTrue(math.allocated)

    async def test_fill_input_history_and_output_share_codec_without_changing_artist(self):
        raw = 'A & B; <span>; &lt;span&gt;.'
        wrong = '<xml><p id="1">wrong & text</p></xml>'
        good = '<xml><p id="1">A &amp; B; &lt;span&gt;; &amp;lt;span&amp;gt;.</p></xml>'
        translator, runtime = _repairing_translator([wrong, good])
        artist = AsyncMock(return_value=raw)
        translator._translate_text_async = artist
        module = 'pdf_craft.transformer.xml_translator.xml_translator.translator'
        with patch(module + '.decode_friendly', wraps=decode_friendly) as decoder:
            result, _ = await StructuredTranslation().translate(translator, TranslationTask(
                fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None),
                error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(''.join(result.itertext()), raw)
        self.assertEqual(len(result[0]), 0)
        self.assertEqual(artist.await_count, 1)
        self.assertEqual(decoder.call_count, 2)
        self.assertEqual(runtime.context_value.calls, 2)
        for recorded in runtime.context_value.messages:
            messages = recorded[0]
            user = messages[1].message
            payload = re.search(r'<translation\b.*?</translation>', user, re.S)
            assert payload is not None
            self.assertEqual(''.join(decoded(payload[0], 'translation').itertext()), raw)
        history = runtime.context_value.messages[1][0][2].message
        self.assertIn('wrong &amp; text', history)
        self.assertEqual(''.join(decoded(history, 'xml').itertext()).strip(), 'wrong & text')

    @policy_operation
    async def test_json_batch_xml_members_use_real_fill_decoder(self):
        translator, model = translator_fixture()
        # Avoid single-leaf deterministic fill; exercise batching with real runtime.
        async def roots():
            for index in range(4):
                root = Element('body')
                paragraph = SubElement(root, 'p', title='A & B')
                paragraph.text = f'Paragraph {index}: A & B; literal <span> &lt;span&gt;.'
                yield TranslationTask(root, SubmitKind.REPLACE, index)
        module = 'pdf_craft.transformer.xml_translator.xml_translator.translator'
        with patch(module + '.decode_friendly', wraps=decode_friendly) as decoder:
            results = await translator._translate_elements(
                roots(), **StructuredTranslation().arguments(), window=4)
        self.assertEqual(len(results), 4)
        self.assertGreaterEqual(decoder.call_count, 4)
        for index, (root, _payload) in enumerate(results):
            self.assertEqual(''.join(root.itertext()),
                             f'Paragraph {index}: A & B; literal <span> &lt;span&gt;.')
            self.assertEqual(len(root[0]), 0)
            self.assertEqual(root[0].get('title'), 'A & B')
        fill_batches = [messages for messages in model.wire_calls
                        if messages[0].message.startswith('Execute each independent conversation')]
        self.assertTrue(fill_batches)
        self.assertTrue(any(len(json.loads(messages[1].message)) > 1 for messages in fill_batches))
        for messages in fill_batches:
            for entry in json.loads(messages[1].message):
                user = entry['messages'][0]['content']
                template = re.search(r'XML template:\n```XML\n(.*?)\n```', user, re.S)
                assert template is not None
                self.assertIn('A &amp; B', template[1])

    async def test_literal_reference_text_cannot_collide_with_real_engineering_reference(self):
        raw = 'Literal <math-ref key="m1"/> and '+r'\(x\)'+'.'
        response = ('<xml><p id="1">Literal &lt;math-ref key="m1"/&gt; and '
                    '<math-ref key="m1"/>.</p></xml>')
        translator, runtime = _repairing_translator([response])
        translator._translate_text_async = AsyncMock(return_value=raw)
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None), error_policy=False)
        self.assertEqual(''.join(result.itertext()), raw)
        self.assertEqual(len(result[0]), 0)
        user = runtime.context_value.messages[0][0][1].message
        payload = re.search(r'<translation\b.*?</translation>', user, re.S)
        assert payload is not None
        tree = decoded(payload[0], 'translation')
        self.assertEqual(tree.text, 'Literal <math-ref key="m1"/> and ')
        self.assertEqual([node.tag for node in tree], ['math-ref'])

    async def test_unparseable_xml_is_logged_but_not_reinjected_as_raw_xml(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">unfinished', '<xml><p id="1">Complete.</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='Complete.')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(''.join(result.itertext()), 'Complete.')
        retry = runtime.context_value.messages[1][0]
        self.assertTrue(all(message.role != MessageRole.ASSISTANT for message in retry))
        self.assertIn('No complete', retry[-1].message)

    async def test_noncanonical_free_text_is_not_implicitly_xml(self):
        raw = '<arbitrary>A & B</arbitrary> &lt;literal&gt;'
        translator, runtime = _repairing_translator([
            '<xml><p id="1">&lt;arbitrary&gt;A &amp; B&lt;/arbitrary&gt; &amp;lt;literal&amp;gt;</p></xml>'])
        artist = AsyncMock(return_value=raw)
        translator._translate_text_async = artist
        result, _ = await translator.translate_element(TranslationTask(
            fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None), error_policy=False)
        self.assertEqual(''.join(result.itertext()), raw)
        self.assertEqual(artist.await_count, 1)
        self.assertIn('Translated text:\n' + raw, runtime.context_value.messages[0][0][1].message)
