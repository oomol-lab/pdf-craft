"""Opt-in offline regression against the private, original service #364 archives.

Run with PDF_CRAFT_SERVICE_364_FIXTURES pointing to the retained fixture directory.
Only provider responses are replaced; grouping, allocation, XML fill and adoption run.
"""
# pylint: disable=protected-access
import json
import os
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, skipUnless
from xml.etree.ElementTree import Element, SubElement
from zipfile import ZipFile

from pdf_craft import SubmitKind
from pdf_craft.transformer.whitespace import normalize
from pdf_craft.transformer.canonical_projection import formula_content
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.test_canonical_math_projection import chapter, expression, content_of
from pdf_craft.extractor.chapter.chapter import InlineExpression, Chapter, TextFlowItem, SourceTextFragment
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.translation_model import translator_fixture

FIXTURES = os.environ.get('PDF_CRAFT_SERVICE_364_FIXTURES')
FAILURES = ('068f180d55a7404a8f15eff27f37dce3', '1522a3ce44c440a5897de4ad2272f35c',
            '3408dd0a153a4f398231802d5e22dc88', '365a8d8e0aa9490e93239e621bb3039e')


def completed_prefix(response):
    """Read only already-complete objects; never repair or accept truncated JSON."""
    decoder = json.JSONDecoder()
    start, objects = response.find('{'), []
    while start >= 0:
        try:
            unit, length = decoder.raw_decode(response[start:])
        except ValueError:
            break
        objects.append(unit)
        start = response.find('{', start + length)
    return objects


def oracle_allocations(payload, response):
    """Reference boundaries, not a production recovery algorithm.

    The complete objects in the saved prefix supply source ownership and exact text.
    The final 1/2/2/3 paragraphs were manually checked against the corresponding
    final source units (concretizing; preference scoring; license/budget;
    deduplication). Their paragraph boundaries supply the missing oracle ends.
    """
    raw, cursor, expected = payload['translation'], 0, []
    prefix = completed_prefix(response)
    for unit in prefix:
        assert unit['id'] == str(len(expected))
        value = unit['text'].strip()
        start = raw.find(value, cursor)
        assert start >= 0 and not raw[cursor:start].strip()
        end = start + len(value)
        expected.append(raw[cursor:end])
        cursor = end
    tail = raw[cursor:]
    remaining = tail.strip().split('\n\n')
    assert len(prefix) + len(remaining) == len(payload['sources'])
    for value in remaining:
        end = raw.index(value, cursor) + len(value)
        expected.append(raw[cursor:end])
        cursor = end
    expected[-1] += raw[cursor:]
    return expected


def xml_oracle(template, texts, sources, allocations):
    """Turn manually retained ownership evidence into XML model responses.

    This is an offline fixture builder, not a production fallback or model stage.
    XML structure, complete text, identities and adoption still run in production.
    """
    import re
    from pdf_craft.transformer.xml_translator.xml.friendly import decode_friendly, encode_friendly
    xml = re.search(r'XML template:\n```XML\n(.*?)\n```', template, re.S)
    assert xml is not None
    root = next(decode_friendly(xml[1], tags='xml'))
    offset = 0
    for node, text, source, allocation in zip(root, texts, sources, allocations, strict=True):
        for field in node.iter():
            field.text = field.tail = None
        spans = []
        for formula in allocation.get('formulas', []):
            literal = formula['text']
            positions = [match.start() for match in re.finditer(re.escape(literal), text)]
            before, after = formula.get('before', ''), formula.get('after', '')
            positions = [start for start in positions if text[:start].endswith(before)
                         and text[start+len(literal):].startswith(after)]
            assert len(positions) == 1, (literal, positions)
            cursor = positions[0]
            parts = formula.get('parts', [dict(keys=[formula['key']], text=literal)])
            for part in parts:
                for key in part.get('keys', []):
                    spans.append((cursor, cursor+len(part['text']), f's{offset+int(key[1:])}'))
                cursor += len(part['text'])
        offset += len(source['formulas'])
        cursor, previous, tokens = 0, None, []
        for start, end, key in sorted(spans):
            if previous is None:
                node.text = text[cursor:start]
            else:
                previous.tail = text[cursor:start]
            previous = Element('source-math', key=key)
            previous.text = text[start:end]
            tokens.append(previous)
            cursor = end
        if previous is None:
            node.text = text
        else:
            previous.tail = text[cursor:]
        # Keep annotation order independent of empty template expression slots.
        node[:] = tokens + [child for child in node if child.tag != 'source-math']
    return encode_friendly(root)


@skipUnless(FIXTURES, 'Set PDF_CRAFT_SERVICE_364_FIXTURES for original private offline cases')
class Service364ReplayTests(IsolatedAsyncioTestCase):
    async def test_original_medical_unit_eight(self):
        assert FIXTURES is not None
        archive = Path(FIXTURES) / 'a2ea4d58-0c5f-4db1-93c6-17d140f7cbc8.zip'
        with ZipFile(archive) as stored:
            name = next(name for name in stored.namelist() if name.endswith(
                '/33b837bd6d464c248d47d6f5fac46a74/attempts.jsonl'))
            rows = [json.loads(line) for line in stored.read(name).decode().splitlines()]
        payload = json.loads(rows[-1]['messages'][1]['content'])
        source = next(unit for unit in payload['sources'] if unit['id'] == '8')
        unit = next(unit for unit in json.loads(rows[-1]['response']) if unit['id'] == '8')
        unit = dict(unit, id='0')
        raw = unit['text']
        # Reconstruct the original local paragraph from its recorded evidence,
        # not a mock source containing only 4. It contains all four formulas.
        rest = source['source'].split('Text to translate:\n', 1)[-1]
        content = []
        for value in source['formulas'].values():
            before, rest = rest.split(value, 1)
            content.extend([before, expression(formula_content(value)[0])])
        content.append(rest)
        translator, model = translator_fixture()
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return raw
            return xml_oracle(messages[1]['content'], [raw], [source], [unit])
        model.answer = answer
        target = await NarrativeXMLTransformer(translator).transform(chapter(content), error_policy=False)
        translated = content_of(target)
        formulas = [part for part in translated if isinstance(part, InlineExpression)]
        self.assertEqual(len(formulas), 4)
        self.assertEqual(formulas[-1].content, '40')
        index = translated.index(formulas[-1])
        residual = translated[index+1]
        assert isinstance(residual, str)
        self.assertTrue(residual.startswith('亿'))
        self.assertEqual(len(model.wire_calls), 2)

    async def test_whole_original_medical_group_repairs_without_retranslation(self):
        assert FIXTURES is not None
        archive = Path(FIXTURES) / 'a2ea4d58-0c5f-4db1-93c6-17d140f7cbc8.zip'
        with ZipFile(archive) as stored:
            name = next(name for name in stored.namelist() if name.endswith(
                '/33b837bd6d464c248d47d6f5fac46a74/attempts.jsonl'))
            rows = [json.loads(line) for line in stored.read(name).decode().splitlines()]
        payload = json.loads(rows[0]['messages'][1]['content'])
        items = []
        for index, source in enumerate(payload['sources']):
            rest = source['source'].split('Text to translate:\n', 1)[-1]
            content = []
            for value in source['formulas'].values():
                before, rest = rest.split(value, 1)
                content.extend([before, expression(formula_content(value)[0])])
            content.append(rest)
            items.append(TextFlowItem('body', index, [SourceTextFragment(
                2, index, (0, index*30, 100, index*30+25), content)]))
        translator, model = translator_fixture(max_group_score=100000)
        translator._direct_fill_candidates = type(translator)._direct_fill_candidates
        artists, engineers = [], []
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                artists.append(messages)
                return payload['translation']
            engineers.append(messages)
            units = json.loads(rows[10]['response'])
            xml = xml_oracle(messages[1]['content'], [unit['text'] for unit in units], payload['sources'], units)
            return xml.replace('key="s1"', 'key="unknown"', 1) if len(engineers) == 1 else xml
        model.answer = answer
        target = await NarrativeXMLTransformer(translator).transform(Chapter(None, -1, items), error_policy=False)
        self.assertEqual(len(artists), 1)
        self.assertEqual(len(engineers), 2)
        self.assertEqual(len(target.flow_items), 20)
        amount = target.flow_items[8]
        assert isinstance(amount, TextFlowItem)
        translated = amount.children[0].content
        formulas = [part for part in translated if isinstance(part, InlineExpression)]
        self.assertEqual(len(formulas), 4)
        self.assertEqual(formulas[-1].content, '40')
        residual = translated[translated.index(formulas[-1])+1]
        assert isinstance(residual, str)
        self.assertTrue(residual.startswith('亿'))

    async def test_all_four_original_epub_groups(self):
        assert FIXTURES is not None
        archive = Path(FIXTURES) / 'e8945c2b-0f69-4dbf-972c-8ac67b4bfaa0.zip'
        with ZipFile(archive) as stored:
            for identifier in FAILURES:
                with self.subTest(failure_log_id=identifier):
                    name = next(name for name in stored.namelist() if name.endswith(f'/{identifier}/attempts.jsonl'))
                    rows = [json.loads(line) for line in stored.read(name).decode().splitlines()]
                    payload = json.loads(rows[0]['messages'][1]['content'])
                    response = rows[-1]['response']
                    expected = oracle_allocations(payload, response)
                    await self._through_epub_pipeline(payload, expected)

    async def _through_epub_pipeline(self, payload, expected):
        translator, model = translator_fixture(max_group_score=100000)
        translator._direct_fill_candidates = type(translator)._direct_fill_candidates
        artists, engineers = [], []
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                artists.append(messages)
                return payload['translation']
            engineers.append(messages)
            xml = xml_oracle(messages[1]['content'], expected, payload['sources'], [{}]*len(expected))
            return xml.replace('id="1"', 'id="9999"', 1) if len(engineers) == 1 else xml
        model.answer = answer
        tasks = []
        for source in payload['sources']:
            root = Element('body')
            SubElement(root, 'p').text = source['source'].split('Text to translate:\n', 1)[-1]
            tasks.append(TranslationTask(root, SubmitKind.REPLACE, source['id']))
        results = await StructuredTranslation(formulas=XMLInterrupter()).translate_many(
            translator, tasks, error_policy=False)
        self.assertEqual(len(artists), 1, 'Do not fragment or retranslate the original artist group')
        self.assertEqual(len(engineers), 2, 'One malformed XML then complete XML repair, within the normal budget')
        self.assertEqual(len(results), len(expected))
        for index, ((root, identity), text) in enumerate(zip(results, expected)):
            self.assertEqual(identity, str(index))
            self.assertEqual(normalize(''.join(root.itertext()), trim=True), normalize(text, trim=True))
