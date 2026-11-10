"""PCEX fill validates the translated text before restoring source formulas."""
# pylint: disable=protected-access
import asyncio
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.extractor.chapter.chapter import (
    InlineExpression, SourceAsset, SourceTextFragment, TextFlowItem, decode, encode,
)
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_anchored_content_translation import _repairing_translator
from pdf_craft.transformer.xml_translator.xml.friendly import decode_friendly

FIXTURE = Path(__file__).parent / 'assets/translation/fill_formula_cases.json'


def formulas(chapter):
    return [part.content for item in chapter.flow_items if isinstance(item, TextFlowItem)
            for child in item.children if isinstance(child, SourceTextFragment)
            for part in child.content if isinstance(part, InlineExpression)]


def protected_fixture(case):
    """Express the saved allocation using source identities, keeping the fixture unchanged."""
    root = next(decode_friendly(case['reference_fill'], tags='xml'))
    nodes = list(root.iter('expression'))
    keys = {node.get('id'): f's{index}' for index, node in
            enumerate(sorted(nodes, key=lambda node: int(node.get('id', '0'))), 1)}
    source = decode(fromstring(case['source_xml']), allow_legacy=False)
    assert len(nodes) == len(formulas(source))
    for node in nodes:
        node.text = r'\(' + formulas(source)[int(keys[node.get('id')][1:]) - 1] + r'\)'
        for child in list(node):
            node.remove(child)
    canonical = ''.join(root.itertext())
    return canonical, tostring(root, encoding='unicode')


class FillFormulaLoopTests(unittest.TestCase):
    def test_three_real_paragraphs_accept_flattened_fill_then_restore_formulas(self):
        for case in json.loads(FIXTURE.read_text())['cases']:
            with self.subTest(case=case['name']):
                source = decode(fromstring(case['source_xml']), allow_legacy=False)
                original = deepcopy(source)
                canonical, fill = protected_fixture(case)
                translator, runtime = _repairing_translator([fill])
                translator._translate_text_async = AsyncMock(return_value=canonical)
                target = asyncio.run(NarrativeXMLTransformer(translator).transform(source))
                self.assertEqual(runtime.context_value.calls, 1)
                self.assertEqual(source, original)
                self.assertFalse(getattr(target.flow_items[0], '_translation_missing'))
                self.assertEqual(Counter(formulas(target)), Counter(formulas(source)))
                source_text, target_text = source.flow_items[0], target.flow_items[0]
                assert isinstance(source_text, TextFlowItem)
                assert isinstance(target_text, TextFlowItem)
                self.assertEqual(
                    [(c.page_index, c.source_order, c.bbox) for c in target_text.children
                     if isinstance(c, SourceTextFragment)],
                    [(c.page_index, c.source_order, c.bbox) for c in source_text.children
                     if isinstance(c, SourceTextFragment)],
                )
                self.assertEqual(
                    [c for c in target_text.children if isinstance(c, SourceAsset)],
                    [c for c in source_text.children if isinstance(c, SourceAsset)],
                )
                xml = tostring(encode(target), encoding='unicode')
                self.assertNotIn('<expression', xml)
                self.assertNotIn('<anchor', xml)
                self.assertNotIn('__PDF_CRAFT_CHAPTER_FORMULA', xml)
                self.assertEqual(decode(encode(target), allow_legacy=False), target)
