"""All adapters use full-text acceptance and one formula projection lifetime."""
# pylint: disable=protected-access
from copy import deepcopy
import unicodedata
from typing import cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import SubmitKind
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import SourceAsset, InlineExpression, encode, Chapter, StandaloneAsset
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml.friendly import decode_friendly
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.test_anchored_content_translation import _repairing_translator, _ResponseRuntime
from tests.translation_model import translator_fixture


def image(parts):
    return SourceAsset(1, 'image', (1, 1, 100, 30), caption=parts)


def neutral_translation(text):
    return text.replace('Figure', '图注').replace('before', '之前').replace('after', '之后')


def offline_translator():
    translator, model = translator_fixture()
    translator._translate_text_async = AsyncMock(side_effect=lambda text, **_kwargs: neutral_translation(text))
    answer = model.answer
    model.answer = lambda messages: neutral_translation(answer(messages))
    return translator


class StructuredTranslationTests(IsolatedAsyncioTestCase):
    async def test_non_narrative_units_have_independent_canonical_translations(self):
        canonical = {'one': 'first', 'two': 'second'}
        for sources in (('one', 'two'), ('two', 'one')):
            with self.subTest(sources=sources):
                translator, model = translator_fixture()
                translator._translate_text_async = AsyncMock(side_effect=lambda text, **_kwargs: '\n\n'.join(canonical[part] for part in text.split('\n\n')))
                template_answer = model.answer

                def answer(messages, source_answer=template_answer):
                    candidate = fromstring(source_answer(messages))
                    unit = candidate.find('unit')
                    assert unit is not None
                    payload = messages[1]['content'].split('Translated text:\n', 1)[1]
                    translated = next(decode_friendly(payload, tags='translation'))
                    for node, text in zip(candidate, ''.join(translated.itertext()).split('\n\n'), strict=True):
                        node.text = text
                    return tostring(candidate, encoding='unicode')

                # The provider echoes the actual envelope IDs in reverse response order.
                model.answer = answer
                protocol = StructuredTranslation(owner=lambda segment: segment.parent)
                root = fromstring('<document>' + ''.join(f'<unit>{text}</unit>' for text in sources) + '</document>')
                target, _ = await protocol.translate(translator, TranslationTask(root, SubmitKind.REPLACE, None))
                self.assertEqual([node.text for node in target], [canonical[text] for text in sources])
                self.assertEqual(len(model.wire_calls), 1)


    async def test_asset_fill_retains_unicode_equivalence_from_narrative(self):
        translator, runtime = _repairing_translator(['<xml><caption id="1">Всё хорошо.</caption></xml>'])
        canonical = 'Все\u0308 хорошо.'
        translator._translate_text_async = AsyncMock(return_value=canonical)
        target, = await AnchoredContentXMLTransformer(translator).transform_assets(
            [AnchoredContent('a', 0, -1, image(['source']), '')], error_policy=False)
        assert target is not None
        self.assertEqual(unicodedata.normalize('NFC', ''.join(part for part in target.caption if isinstance(part, str))),
                         unicodedata.normalize('NFC', canonical))
        self.assertEqual(runtime.context_value.calls, 1)


    async def test_asset_context_is_read_only_not_an_extra_translation_unit(self):
        translator, runtime = _repairing_translator(['<xml><caption id="1">图注</caption></xml>'])
        del translator._translate_text_async
        translation_runtime = _ResponseRuntime(['图注'], translator._translation_runtime)
        translator._translation_runtime = translation_runtime  # type: ignore[assignment]
        result, = await AnchoredContentXMLTransformer(translator).transform_assets(
            [AnchoredContent('a', 0, -1, image(['Figure']), 'Nearby source context')], error_policy=False)
        assert result is not None
        self.assertEqual(result.caption, ['图注'])
        self.assertEqual(translation_runtime.context_value.calls, 1)
        self.assertEqual(runtime.context_value.calls, 1)
        messages = translation_runtime.context_value.messages[0][0]
        self.assertIn('Read-only background for understanding the source', messages[-1].message)
        self.assertIn('Nearby source context', messages[-1].message)

    async def test_shared_formula_projection_keeps_adjacent_identical_formulas_and_tails(self):
        expression = InlineExpression(ExpressionKind.INLINE_DOLLAR, 'x')
        source = image(['before ', expression, deepcopy(expression), ' after'])
        target, = await AnchoredContentXMLTransformer(offline_translator()).transform_assets(
            [AnchoredContent('a', 0, -1, source, '')], error_policy=False)
        assert target is not None
        self.assertEqual(target.caption, ['之前 ', expression, expression, ' 之后'])
        tree = encode(Chapter(None, -1, [StandaloneAsset(image(target.caption))]))
        self.assertFalse(any(key.startswith('__') for node in tree.iter() for key in node.attrib))
        self.assertEqual(len(list(tree.iter('inline_expr'))), 2)

    async def test_table_cells_keep_independent_units_and_nested_formula_ownership(self):
        def tag(name, children):
            definition = tag_definition(name)
            assert definition is not None
            return HTMLTag(definition, [], children)
        formula = InlineExpression(ExpressionKind.INLINE_DOLLAR, 'x')
        source = SourceAsset(1, 'table', (1, 1, 100, 30), content=[tag('table', [tag('tr', [
            tag('td', ['before ', tag('em', [formula]), ' after']),
            tag('td', ['before ', deepcopy(formula), ' after']),
        ])])])
        translator = offline_translator()
        target, = await AnchoredContentXMLTransformer(translator).transform_assets(
            [AnchoredContent('a', 0, -1, source, '')], error_policy=False)
        assert target is not None
        self.assertEqual(cast(AsyncMock, translator._translate_text_async).await_count, 1)
        encoded = encode(Chapter(None, -1, [StandaloneAsset(SourceAsset(
            1, 'table', source.bbox, content=target.content))]))
        self.assertEqual(len(encoded.findall('.//td/em/inline_expr')), 1)
        self.assertEqual(len(encoded.findall('.//td/inline_expr')), 1)
