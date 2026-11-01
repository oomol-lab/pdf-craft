"""All adapters use full-text acceptance and one formula projection lifetime."""
# pylint: disable=protected-access
from copy import deepcopy
import os
import hashlib
import unicodedata
from typing import cast
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import fromstring, tostring
from zipfile import ZipFile

from pdf_craft import AsyncPDFCraft, SubmitKind
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import SourceAsset, InlineExpression, decode, encode, Chapter, StandaloneAsset
from pdf_craft.llm.runtime import LLMContext
from pdf_craft.markdown.paragraph import HTMLTag, tag_definition
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from pdf_craft.transformer.anchored_translation import _asset_slots
from pdf_craft.transformer.package import AnchoredContentExtractionTransformer
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.test_anchored_content_translation import _repairing_translator, _ResponseRuntime


def image(parts):
    return SourceAsset(1, 'image', (1, 1, 100, 30), content=parts)


def neutral_translation(text):
    return text.replace('Figure', '图注').replace('before', '之前').replace('after', '之后')


def offline_translator():
    translator, _ = _repairing_translator([])
    translator._translate_text_async = AsyncMock(side_effect=lambda text, **_kwargs: neutral_translation(text))

    async def fill(*, hill_climbing, canonical_text_validator, **_kwargs):
        candidate = hill_climbing.request_element()
        for node in candidate.iter():
            if node.text:
                node.text = neutral_translation(node.text)
            if node.tail:
                node.tail = neutral_translation(node.tail)
        error = canonical_text_validator(candidate)
        if error is not None:
            raise AssertionError(error)
        error = hill_climbing.submit(candidate)
        if error is not None:
            raise AssertionError(error)
    translator._request_and_submit_async = AsyncMock(side_effect=fill)
    return translator


class StructuredTranslationTests(IsolatedAsyncioTestCase):
    async def test_non_narrative_units_have_independent_canonical_translations(self):
        translator, runtime = _repairing_translator([
            '<xml><unit id="1">first</unit></xml>',
            '<xml><unit id="1">second</unit></xml>',
        ])
        translator._translate_text_async = AsyncMock(side_effect=['first', 'second'])
        protocol = StructuredTranslation(owner=lambda segment: segment.parent)
        root = fromstring('<document><unit>one</unit><unit>two</unit></document>')
        target, _ = await protocol.translate(translator, TranslationTask(root, SubmitKind.REPLACE, None))
        self.assertEqual([node.text for node in target], ['first', 'second'])
        self.assertEqual(runtime.context_value.calls, 2)

    async def test_asset_uses_canonical_math_references_and_exact_text_acceptance(self):
        translator, runtime = _repairing_translator([
            '<xml><content id="1">错字 <expression><math-ref key="m1"/></expression> 之后</content></xml>',
            '<xml><content id="1">之前 <expression><math-ref key="m1"/></expression> 之后</content></xml>',
        ])
        translator._translate_text_async = AsyncMock(return_value='之前 $x$ 之后')
        source = image(['before ', InlineExpression(ExpressionKind.INLINE_DOLLAR, 'x'), ' after'])
        saved = deepcopy(source)
        target, = await AnchoredContentXMLTransformer(translator).transform_assets(
            [AnchoredContent('a', 0, -1, source, '')], error_policy=False)
        assert target is not None
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertEqual(source, saved)
        self.assertEqual(target.content, ['之前 ', InlineExpression(ExpressionKind.INLINE_DOLLAR, 'x'), ' 之后'])

    async def test_asset_fill_retains_unicode_equivalence_from_narrative(self):
        translator, runtime = _repairing_translator(['<xml><content id="1">Всё хорошо.</content></xml>'])
        canonical = 'Все\u0308 хорошо.'
        translator._translate_text_async = AsyncMock(return_value=canonical)
        target, = await AnchoredContentXMLTransformer(translator).transform_assets(
            [AnchoredContent('a', 0, -1, image(['source']), '')], error_policy=False)
        assert target is not None
        self.assertEqual(unicodedata.normalize('NFC', ''.join(part for part in target.content if isinstance(part, str))),
                         unicodedata.normalize('NFC', canonical))
        self.assertEqual(runtime.context_value.calls, 1)

    async def test_asset_merged_canonical_formula_uses_one_ref_and_restores_empty_slot(self):
        translator, runtime = _repairing_translator([
            '<xml><content id="1">取 <expression id="2"><math-ref key="m1"/></expression>'
            '<expression id="3"/>。</content></xml>',
        ])
        translator._translate_text_async = AsyncMock(return_value=r'取 \(x=y\)。')
        expressions = [InlineExpression(ExpressionKind.INLINE_PAREN, 'x='),
                       InlineExpression(ExpressionKind.INLINE_PAREN, 'y')]
        target, = await AnchoredContentXMLTransformer(translator).transform_assets(
            [AnchoredContent('a', 0, -1, image(['before ', *expressions, ' after']), '')], error_policy=False)
        assert target is not None
        self.assertEqual(target.content, ['取 ', *expressions, '。'])
        self.assertEqual(runtime.context_value.calls, 1)

    async def test_asset_context_is_read_only_not_an_extra_translation_unit(self):
        translator, runtime = _repairing_translator(['<xml><content id="1">图注</content></xml>'])
        del translator._translate_text_async
        translation_runtime = _ResponseRuntime(['图注'], translator._translation_runtime)
        translator._translation_runtime = translation_runtime  # type: ignore[assignment]
        result, = await AnchoredContentXMLTransformer(translator).transform_assets(
            [AnchoredContent('a', 0, -1, image(['Figure']), 'Nearby source context')], error_policy=False)
        assert result is not None
        self.assertEqual(result.content, ['图注'])
        self.assertEqual(translation_runtime.context_value.calls, 1)
        self.assertEqual(runtime.context_value.calls, 1)
        messages = translation_runtime.context_value.messages[0][0]
        self.assertIn('Read-only context', messages[-1].message)
        self.assertIn('Nearby source context', messages[-1].message)

    async def test_shared_formula_projection_keeps_adjacent_identical_formulas_and_tails(self):
        expression = InlineExpression(ExpressionKind.INLINE_DOLLAR, 'x')
        source = image(['before ', expression, deepcopy(expression), ' after'])
        target, = await AnchoredContentXMLTransformer(offline_translator()).transform_assets(
            [AnchoredContent('a', 0, -1, source, '')], error_policy=False)
        assert target is not None
        self.assertEqual(target.content, ['之前 ', expression, ' ', expression, ' 之后'])
        tree = encode(Chapter(None, -1, [StandaloneAsset(image(target.content))]))
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
        self.assertEqual(cast(AsyncMock, translator._translate_text_async).await_count, 2)
        encoded = encode(Chapter(None, -1, [StandaloneAsset(SourceAsset(
            1, 'table', source.bbox, content=target.content))]))
        self.assertEqual(len(encoded.findall('.//td/em/inline_expr')), 1)
        self.assertEqual(len(encoded.findall('.//td/inline_expr')), 1)

    async def test_mathml_adapter_uses_shared_canonical_fill_before_restoring_source(self):
        from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
        translator, runtime = _repairing_translator([
            '<xml><p id="1">Avant <expression><math-ref key="m1"/></expression> après</p>'
            '<p id="2">Deuxième</p></xml>',
        ])
        translator._translate_text_async = AsyncMock(return_value='Avant $x$ après\n\nDeuxième')
        root = fromstring('<body><p>Before <math><mi>x</mi></math> after</p><p>Second</p></body>')
        protocol = StructuredTranslation(formulas=XMLInterrupter())
        target, _ = await protocol.translate(translator, TranslationTask(root, SubmitKind.REPLACE, None))
        self.assertEqual(target.find('p/math/mi').text, 'x')
        self.assertEqual(target.find('p').text, 'Avant ')
        self.assertEqual(target.find('p/math').tail, ' après')
        self.assertEqual(target.findall('p')[1].text, 'Deuxième')
        self.assertFalse(any(key.startswith('__') for node in target.iter() for key in node.attrib))
        self.assertEqual(runtime.context_value.calls, 1)

    @skipUnless(os.environ.get('PDF_CRAFT_ASSET_FORMULA_REGRESSION_PCEX'), 'private PCEX not configured')
    async def test_service_286_caption_is_adopted_in_export_and_document_renders(self):
        source = Path(os.environ['PDF_CRAFT_ASSET_FORMULA_REGRESSION_PCEX'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         'd1798ab5c4e04dad836b16f8c3e18038c07ceb640224ff6818c7d7d4dfebad6d')
        with ZipFile(source) as archive:
            chapter = decode(fromstring(archive.read('chapters/chapter_6.xml')))
        original = next(slot.payload for slot in _asset_slots(chapter) if slot.payload.identity == ('6', 3, -1))
        translator = offline_translator()
        async def no_network(*_args, **_kwargs):
            raise AssertionError('Offline gate attempted a model request')
        with TemporaryDirectory() as folder, patch.object(LLMContext, 'request', no_network):
            root = Path(folder)
            craft = AsyncPDFCraft()
            extraction = await craft.open_extraction(source)
            output = await AnchoredContentExtractionTransformer(
                AnchoredContentXMLTransformer(translator)).transform(extraction, root / 'translated.pcex', error_policy=False)
            with output._materialize() as paths:
                from pdf_craft.common import read_xml
                target_chapter = decode(read_xml(paths.chapters / 'chapter_6.xml'))
                target = next(slot.payload for slot in _asset_slots(target_chapter) if slot.payload.identity == original.identity)
                self.assertEqual(target.asset.asset_hash, original.asset.asset_hash)
                formulas = [part for part in target.asset.content if isinstance(part, InlineExpression)]
                old_formulas = [part for part in original.asset.content if isinstance(part, InlineExpression)]
                self.assertEqual(formulas, old_formulas)
                self.assertEqual(len(formulas), 1)
                coverage = read_xml(paths.translation)
                entry = coverage.find("anchored/asset[@chapter_id='6'][@flow_index='3'][@child_index='-1']")
                assert entry is not None
                self.assertEqual(entry.get('state'), 'translated')
                xml = tostring(encode(target_chapter), encoding='unicode')
                self.assertNotIn('__PDF_CRAFT', xml)
                self.assertNotIn('<expression', xml)
                self.assertNotIn('<math-ref', xml)
            await craft.render_markdown(output, root / 'translated.md')
            self.assertTrue((root / 'translated.md').is_file())
            self.assertTrue(str(target.asset.content[0]).startswith('图注 3'))
            # Image content is persisted independently of the picture renderer,
            # which displays title/caption fields rather than image.content.
            print('REAL_ASSET_FORMULA_GATE', {'identity': original.identity, 'formula_count': len(formulas),
                                             'translation_adopted_in_export': True, 'document_render_completed': True,
                                             'image_content_display': 'existing renderer displays title/caption only',
                                             'network_requests': 0})
