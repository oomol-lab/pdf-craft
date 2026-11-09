"""Opt-in offline XML replay of four exact e81368d paid-smoke artist responses.

Old JSON supplies manually retained reference boundaries ONLY in this fixture
builder. The production model substitute returns XML; no allocator is imported.
"""
# pylint: disable=protected-access
import json
import os
import re
from functools import lru_cache, partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree.ElementTree import Element, SubElement, tostring, fromstring
from zipfile import ZipFile
import unicodedata
from typing import cast
from unittest import IsolatedAsyncioTestCase, skipUnless

from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment, InlineExpression, encode, decode
from pdf_craft.transformer.canonical_math import CanonicalMath
from pdf_craft.transformer.canonical_projection import formula_content
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.common.whitespace import normalize_whitespace
from pdf_craft.expression import to_markdown_string
from tests.test_canonical_math_projection import expression
from tests.test_service_364_replay import xml_oracle
from tests.translation_model import translator_fixture

FIXTURES = os.environ.get('PDF_CRAFT_PAID_FILL_FIXTURES')
CASES = ('68ea16f37b0a422bbb7a4d09e2843b44', '3e4d3dba890b492ea95c57c4e6be4851',
         '97ced5c0c8cc4ff4a5652b665bb4dd18', 'baf3e76ba47243c28f11984ffd57f5d7')


def reference_texts(raw, units):
    """Uniquely solve saved first-answer ends jointly to prepare a test oracle.

    This deliberately does not implement the old after protocol or accept its
    JSON in production. It verifies the retained material has one reference
    partition before constructing the XML answer tested below.
    """
    math = [match.span() for match in CanonicalMath.matches(raw)]
    @lru_cache(maxsize=None)
    def solve(index: int, cursor: int) -> list[tuple[int, ...]]:
        if index == len(units):
            return [()] if not raw[cursor:].strip() else []
        unit = units[index]
        if unit.get('text') == '':
            ends = [cursor]
        else:
            anchor = unit['end']
            ends = [match.start()+len(anchor) for match in re.finditer(re.escape(anchor), raw)
                    if match.start()+len(anchor) > cursor]
        answers = []
        for end in ends:
            if any(start < end < finish for start, finish in math):
                continue
            for tail in solve(index+1, end):
                answers.append((end, *tail))
                if len(answers) == 2:
                    return answers
        return answers
    solutions = solve(0, 0)
    assert len(solutions) == 1, 'Reference XML requires a unique complete retained partition.'
    ends: list[int] = list(solutions[0])
    ends[-1] = len(raw)
    texts, cursor = [], 0
    for end in ends:
        texts.append(raw[cursor:end])
        cursor = end
    return texts


def replay_answer(raw, texts, payload, units, messages):
    if messages[0]['content'].startswith('You are a translator.'):
        return raw
    return xml_oracle(messages[1]['content'], texts, payload['sources'], units)


@skipUnless(FIXTURES, 'Set PDF_CRAFT_PAID_FILL_FIXTURES to the retained e81368d paid output')
class PaidFillReplayTests(IsolatedAsyncioTestCase):
    async def test_four_failed_allocation_groups_use_one_xml_engineering_stage(self):
        assert FIXTURES is not None
        for identifier in CASES:
            with self.subTest(failure_log_id=identifier):
                path = next(Path(FIXTURES).rglob(identifier)) / 'attempts.jsonl'
                row = json.loads(path.read_text().splitlines()[0])
                payload = json.loads(row['messages'][1]['content'])
                units = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', row['response'].strip()))
                self.assertEqual([unit['id'] for unit in units], [source['id'] for source in payload['sources']])
                raw = payload['translation']
                texts = reference_texts(raw, units)
                occurrences = CanonicalMath.matches(raw)
                offset = 0
                for unit, text in zip(units, texts):
                    for formula in unit['formulas']:
                        occurrence = occurrences[int(formula['target'][1:])-1]
                        start, end = occurrence.start()-offset, occurrence.end()-offset
                        self.assertTrue(0 <= start < end <= len(text))
                        formula.update(text=text[start:end], before=text[:start], after=text[end:])
                    offset += len(text)
                if identifier in CASES[1:3]:
                    await self.through_epub(payload, units, texts)
                    continue
                items = []
                for index, source in enumerate(payload['sources']):
                    rest = source['source'].split('Text to translate:\n', 1)[-1]
                    content = []
                    for value in source['formulas'].values():
                        before, rest = rest.split(value, 1)
                        content.extend([before, expression(formula_content(value)[0])])
                    content.append(rest)
                    items.append(TextFlowItem('body', index, [SourceTextFragment(1, index, (0, index*30, 100, index*30+25), content)]))
                translator, model = translator_fixture(max_group_score=100000)
                model.answer = partial(replay_answer, raw, texts, payload, units)
                target = await NarrativeXMLTransformer(translator).transform(Chapter(None, -1, items), error_policy=False)
                self.assertEqual(len(target.flow_items), len(items))
                self.assertTrue(all(not getattr(item, '_translation_missing') for item in target.flow_items))
                self.assertEqual(len(model.wire_calls), 2, 'One artist and one complete XML reply; no JSON allocation')
                self.assertEqual(decode(encode(target), allow_legacy=False), target)
                for index, (item, expected, unit, source) in enumerate(zip(target.flow_items, texts, units, payload['sources'], strict=True)):
                    assert isinstance(item, TextFlowItem)
                    fragment, = item.children
                    assert isinstance(fragment, SourceTextFragment)
                    self.assertEqual((fragment.page_index, fragment.source_order), (1, index))
                    self.assertTrue(all(isinstance(part, (str, InlineExpression)) for part in fragment.content))
                    actual = ''.join(to_markdown_string(part.kind, part.content) if isinstance(part, InlineExpression) else cast(str, part) for part in fragment.content)
                    # All five formula-bearing original occurrences here have
                    # identical artist/source values. This is fixture evidence,
                    # not an inference by the production correspondence code.
                    locations = []
                    for formula in unit['formulas']:
                        self.assertEqual(formula['text'], source['formulas'][formula['key']])
                        start = len(formula['before'])
                        self.assertEqual(expected[start:start+len(formula['text'])], formula['text'])
                        locations.append((start, start+len(formula['text']), formula))
                    cursor, rendered, expected_formulas = 0, [], []
                    for start, end, formula in sorted(locations):
                        rendered.append(expected[cursor:start])
                        node = expression(formula_content(source['formulas'][formula['key']])[0])
                        rendered.append(to_markdown_string(node.kind, node.content))
                        expected_formulas.append(node)
                        cursor = end
                    rendered.append(expected[cursor:])
                    self.assertEqual([part for part in fragment.content if isinstance(part, InlineExpression)], expected_formulas)
                    self.assertEqual(normalized(actual), normalized(''.join(rendered)))

    async def through_epub(self, payload, units, texts):
        """Actual EPUB archive, chapter adoption, CFI callbacks and output XHTML."""
        from pdf_craft import AsyncPDFCraft, SubmitKind
        from pdf_craft.llm.runtime import LLMRuntime
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root/'source.epub', root/'translated.epub'
            originals = [unit['source'].split('Text to translate:\n', 1)[-1] for unit in payload['sources']]
            with ZipFile(source, 'w') as archive:
                archive.writestr('mimetype', 'application/epub+zip')
                archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="EPUB/book.opf"/></rootfiles></container>')
                manifest = ''.join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(len(originals)))
                spine = ''.join(f'<itemref idref="c{i}"/>' for i in range(len(originals)))
                # No translatable headers are added to the retained artist group.
                archive.writestr('EPUB/book.opf', '<package><metadata><identifier>offline-local-replay</identifier><language>en</language></metadata><manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'+manifest+'</manifest><spine>'+spine+'</spine></package>')
                archive.writestr('EPUB/toc.ncx', '<ncx><navMap/></ncx>')
                for i, original in enumerate(originals):
                    html = Element('html', xmlns='http://www.w3.org/1999/xhtml')
                    SubElement(SubElement(html, 'body'), 'p', id=f'source-{i}').text = original
                    archive.writestr(f'EPUB/c{i}.xhtml', tostring(html))
            translator, model = translator_fixture(max_group_score=100000)
            model.answer = partial(replay_answer, payload['translation'], texts, payload, units)
            async def invoke(_runtime, messages, *args, **kwargs):
                return await model.invoke(messages, *args, **kwargs)
            callbacks = []
            with patch.object(LLMRuntime, '_invoke_async', invoke):
                await AsyncPDFCraft().translate_epub(source, output, llm=translator._translation_llm,
                    target_language='English', submit=SubmitKind.REPLACE, window=1,
                    max_group_tokens=100000, executor=translator._translation_runtime.executor,
                    on_body=callbacks.append, error_policy=False)
            self.assertEqual(len(model.wire_calls), 2)
            self.assertEqual(len(callbacks), len(texts))
            self.assertEqual(len({value.epubcfi for value in callbacks}), len(texts))
            self.assertTrue(all(value.epubcfi is not None for value in callbacks))
            self.assertEqual([normalized(value.original) for value in callbacks], [normalized(value) for value in originals])
            self.assertEqual([normalized(value.translated) for value in callbacks], [normalized(value) for value in texts])
            with ZipFile(output) as archive:
                self.assertEqual(archive.read('mimetype'), b'application/epub+zip')
                for i, expected in enumerate(texts):
                    html = fromstring(archive.read(f'EPUB/c{i}.xhtml'))
                    paragraph, = html.findall('.//{http://www.w3.org/1999/xhtml}p')
                    self.assertEqual(paragraph.get('id'), f'source-{i}')
                    self.assertEqual(normalized(''.join(paragraph.itertext())), normalized(expected))
                    self.assertEqual(list(paragraph), [])
                    self.assertNotIn('source-math', tostring(html, encoding='unicode'))


def normalized(value):
    return normalize_whitespace(unicodedata.normalize('NFD', value), trim=True)
