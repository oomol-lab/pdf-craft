"""All narrative formulas have real editable output slots, without background copies."""
# pylint: disable=protected-access
import unittest
from typing import cast
from xml.etree.ElementTree import tostring

from pdf_craft import RetryPolicy
from pdf_craft.extractor.chapter.chapter import Chapter, DisplayFormula, SourceAsset, TextFlowItem, InlineExpression, SourceTextFragment, encode, decode
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.translation_coverage import formula_states, accepted_translation
from tests.fill_fixtures import chapter, expression, model_replies
from tests.translation_model import translator_fixture


def equation(value='x+1'):
    return DisplayFormula(SourceAsset(2, 'formula', (0, 20, 90, 40), content=[value]))


class ChapterFormulaTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_equation_is_translated_once_at_its_real_position(self):
        source = chapter(['Before.'])
        source.flow_items.append(equation())
        source.flow_items.extend(chapter(['After.']).flow_items)
        translator, model = translator_fixture()
        model_replies(model, '前。$$x+2$$后。', [
            '<xml><fragment id="1">前。</fragment><expression id="2">$$x+2$$</expression><fragment id="3">后。</fragment></xml>'])
        result = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(cast(DisplayFormula, result.flow_items[1]).asset.content, ['x+2'])
        self.assertEqual(decode(encode(result)), result)
        self.assertTrue(accepted_translation(result.flow_items[1]))
        self.assertEqual(list(formula_states(source, result))[0].state, 'translated')
        self.assertEqual(len(model.wire_calls), 2)
        for call in model.wire_calls:
            for message in call:
                self.assertNotIn('formula_context', message.message)
        self.assertEqual(model.wire_calls[0][1].message.count('$$x+1$$'), 1)

    async def test_equation_only_chapter_has_the_same_path(self):
        source = Chapter(5, -1, [equation('4')])
        translator, model = translator_fixture()
        model_replies(model, '$$40$$', ['<xml><expression id="1">$$40$$</expression></xml>'])
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(cast(DisplayFormula, target.flow_items[0]).asset.content, ['40'])
        self.assertEqual(list(formula_states(source, target))[0].order, 'body:0')
        self.assertEqual(len(model.wire_calls), 2)

    async def test_failed_equation_is_not_claimed_as_translated(self):
        source = Chapter(5, -1, [equation()])
        translator, model = translator_fixture()
        model_replies(model, '$$x+2$$', ['<xml><expression id="1">wrong</expression></xml>'])
        target = await NarrativeXMLTransformer(translator).transform(source, retry_policy=RetryPolicy(fill=0), error_policy=True)
        self.assertFalse(accepted_translation(target.flow_items[0]))
        self.assertEqual(list(formula_states(source, target))[0].state, 'preserved')

    async def test_inline_reuse_cannot_replace_accepted_notation(self):
        source = chapter([expression(r'\mathbf{\Sigma}=1')])
        translator, model = translator_fixture()
        artist = r'\[\mathbf{\Sigma}=1\]'
        model_replies(model, artist, [rf'<xml><fragment id="1"><expression>{artist}</expression></fragment></xml>'])
        target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(cast(InlineExpression, cast(SourceTextFragment, cast(TextFlowItem, target.flow_items[0]).children[0]).content[0]).kind.name, 'DISPLAY_BRACKET')
        self.assertEqual(len(model.wire_calls), 2)

    async def test_engineering_failure_does_not_retry_model(self):
        from unittest.mock import patch
        source = chapter([expression('4')])
        translator, model = translator_fixture()
        model_replies(model, '40', ['<xml><fragment id="1"><expression>40</expression></fragment></xml>'])
        with patch('pdf_craft.transformer.chapter_formula_interrupter.ChapterFormulaInterrupter.materialize',
                   side_effect=ValueError('engineering failure')):
            with self.assertRaisesRegex(ValueError, 'engineering failure'):
                await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
        self.assertEqual(len(model.wire_calls), 2)

    async def test_chinese_source_is_not_silently_accepted_by_character_ratio(self):
        translator, model = translator_fixture()
        model_replies(model, '修改后的中文。', ['<xml><fragment id="1">修改后的中文。</fragment></xml>'])
        target = await NarrativeXMLTransformer(translator).transform(chapter(['原始中文。']), error_policy=False)
        self.assertIn('修改后的中文。', tostring(encode(target), encoding='unicode'))
        self.assertEqual(len(model.wire_calls), 2)

    async def test_formula_only_workspace_persists_adoption_and_current_format(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from pdf_craft.common import save_xml, read_xml
        from pdf_craft.document.migrations import CURRENT_VERSION
        from pdf_craft.transformer.content import translate_workspace
        from pdf_craft.transformer.translation_coverage import read_coverage
        from tests.extraction_helpers import make_extraction
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root/'source')
            value = DisplayFormula(SourceAsset(1, 'formula', (0,20,90,40), content=['4']))
            save_xml(encode(Chapter(None, -1, [value])), root/'source/chapters/chapter_head.xml')
            translator, model = translator_fixture()
            model_replies(model, '$$40$$', ['<xml><expression id="1">$$40$$</expression></xml>'])
            result = await translate_workspace(source, root/'translated', NarrativeXMLTransformer(translator))
            result._validate()
            self.assertEqual(read_coverage(root/'translated/translation.xml').narrative,
                             {('head', 'formula', 'body:0'): 'translated'})
            self.assertEqual(read_xml(root/'translated/chapters/chapter_head.xml').findtext('.//content'), '40')
            self.assertEqual(json.loads((root/'translated/manifest.json').read_text())['format_version'], CURRENT_VERSION)
            self.assertEqual(len(model.wire_calls), 2)
