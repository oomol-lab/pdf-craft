"""Source values and occurrence identities survive translation, fill and decoding."""
# pylint: disable=protected-access,unused-argument
from types import MethodType
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring
from pdf_craft.transformer.xml_translator.xml.friendly import decode_friendly

from pdf_craft import SubmitKind
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask, XMLTranslator
from tests.test_anchored_content_translation import _repairing_translator


def provider(translator, replies):
    """Exercise the real first-stage protocol and budget, without network calls."""
    responses = iter(replies)
    calls = []

    async def invoke(messages, *_args, **_kwargs):
        calls.append(messages)
        return next(responses)

    translator._translate_text_async = MethodType(XMLTranslator._translate_text_async, translator)
    translator._translation_runtime.__dict__['_invoke'] = invoke
    return calls


class SourceMathIdentityTests(IsolatedAsyncioTestCase):
    async def test_arbitrary_artist_text_reaches_xml_fill_verbatim(self):
        for raw in ('', 's10 χ mod q', '<free>unstructured</free>', '  unusual\n\nresponse  '):
            with self.subTest(raw=raw):
                response = Element('xml')
                SubElement(response, 'p', id='1').text = raw
                translator, runtime = _repairing_translator([tostring(response, encoding='unicode')])
                calls = provider(translator, [raw])
                result, _ = await StructuredTranslation().translate(translator, TranslationTask(
                    fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None), error_policy=False)
                self.assertEqual(''.join(result.itertext()), raw)
                self.assertEqual(len(calls), 1)
                self.assertEqual(runtime.context_value.calls, 1)
                payload = runtime.context_value.messages[0][0][1].message.split('Translated text:\n', 1)[1]
                decoded = next(decode_friendly(payload, tags='translation'))
                self.assertEqual(''.join(decoded.itertext()), raw)


    async def test_fill_retry_does_not_retranslate(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">wrong</p></xml>', '<xml><p id="1">Free translation.</p></xml>'])
        calls = provider(translator, ['Free translation.'])
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None), error_policy=False)
        self.assertEqual(''.join(result.itertext()), 'Free translation.')
        self.assertEqual(len(calls), 1)
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertTrue(all('Free translation.' in messages[0][1].message
                            for messages in runtime.context_value.messages))
