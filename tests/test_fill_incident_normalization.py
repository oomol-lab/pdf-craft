"""Small public counterparts of Nov-10 fill incidents; no private book text."""
# pylint: disable=protected-access
from copy import deepcopy
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.segment import BlockSegment, search_inline_segments, search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.xml_translator.xml_translator.hill_climbing import HillClimbing
from pdf_craft.transformer.xml_translator.xml_translator.submitter import SubmitKind, submit
from pdf_craft.transformer.canonical_text import restore_canonical_whitespace
from tests.test_anchored_content_translation import _repairing_translator
from tiktoken import get_encoding


def setup(source):
    root = fromstring(source)
    segments = list(search_inline_segments(search_text_segments(root, normalize_whitespace=False)))
    block = BlockSegment('xml', segments)
    return root, segments, HillClimbing(get_encoding('o200k_base'), 10, block)


class BoundaryNormalizationTests(TestCase):
    def validate(self, source, canonical, candidate):
        _, segments, hill = setup(source)
        response = fromstring(candidate)
        self.assertIsNone(hill.validate(response))
        self.assertIsNone(StructuredTranslation().validate(segments, canonical, response))
        return response

    def test_pre_edges_separate_structural_breaks_from_code(self):
        response = self.validate('<body><p>Heading</p><pre>\tcall(a,  b)   </pre><p>After</p></body>',
                                 'Title\n\n\tcall(a,  b)   \n\nAfter',
                                 '<xml><p id="1">Title</p><pre id="2">\tcall(a, b)   </pre><p id="3">After</p></xml>')
        self.assertEqual(response[1].text, '\tcall(a,  b)   ')
        self.assertEqual(response[2].text, 'After')

    def test_existing_protected_blank_lines_survive_structural_separator(self):
        response = self.validate('<body><pre>run()\n\n</pre><p>After</p></body>',
                                 'run()\n\n\n\nAfter',
                                 '<xml><pre id="1">run()\n\n</pre><p id="2">After</p></xml>')
        self.assertEqual(response[0].text, 'run()\n\n')

    def test_empty_rich_node_does_not_claim_a_code_edge(self):
        response = self.validate('<body><pre>print(x) <span style="white-space: pre"><img/></span></pre><p>After</p></body>',
                                 'print(x)\n\nAfter',
                                 '<xml><pre id="1">print(x) <span style="white-space: pre"><img/></span></pre><p id="2">After</p></xml>')
        self.assertEqual(response[0].text, 'print(x)')
        self.assertIsNotNone(response[0].find('.//img'))

    def test_nonempty_annotation_does_not_own_exterior_prose_space(self):
        source = fromstring('<fragment>Use <expression>x</expression><span> now</span></fragment>')
        root = fromstring('<fragment>Use <source-math key="s1">x</source-math> <span> now</span></fragment>')
        self.assertTrue(restore_canonical_whitespace([root], 'Use x\u3000\u3000now', [source],
            replaced_tags=frozenset({'source-math'}), transparent_tags=frozenset({'source-math'})))
        self.assertEqual(''.join(root.itertext()), 'Use x now')

    def test_empty_annotation_remains_ambiguous_and_transactional(self):
        root = fromstring('<fragment>a <source-math key="s1"/> b</fragment>')
        before = tostring(root)
        self.assertFalse(restore_canonical_whitespace([root], 'a b',
            replaced_tags=frozenset({'source-math'}), transparent_tags=frozenset({'source-math'})))
        self.assertEqual(tostring(root), before)

    def test_multiple_protected_gap_owners_are_not_silently_merged(self):
        source = fromstring('<pre><span xml:space="preserve">a </span><span xml:space="preserve"> b</span></pre>')
        root = deepcopy(source)
        before = tostring(root)
        self.assertFalse(restore_canonical_whitespace([root], 'a\tb', [source]))
        self.assertEqual(tostring(root), before)

    def test_missing_inline_feedback_shows_source_and_current_wrong_assignment(self):
        _, _, hill = setup('<body><p><code class="boolean">true</code> then <code class="function">errors.Is()</code></p></body>')
        candidate = fromstring('<xml><p id="1">verdadeiro then <code id="2">errors.Is()</code></p></xml>')
        self.assertIsNotNone(hill.validate(candidate))
        blocks = hill.inline_feedback_blocks(candidate)
        self.assertEqual(len(blocks), 1)
        self.assertIn('<code id="2">true</code>', blocks[0]['source'])
        self.assertIn('<code id="3">errors.Is()</code>', blocks[0]['source'])
        self.assertIn('<code id="2">errors.Is()</code>', blocks[0]['candidate'])

    def test_missing_literal_data_feedback_keeps_original_math_references(self):
        from pdf_craft.transformer.canonical_math import CanonicalMath
        canonical = CanonicalMath('Read-only background: $x$ is data. Delivered $y$ remains.')
        hint = canonical.repair_hint(fromstring('<xml><p id="1">Delivered <math-ref key="m2"/> remains.</p></xml>'))
        self.assertIn('Read-only background:', hint)
        self.assertIn('<math-ref key="m1"/>', hint)
        self.assertIn('is data.', hint)
        self.assertIn('DATA', hint)
        self.assertEqual(canonical.original, 'Read-only background: $x$ is data. Delivered $y$ remains.')

    def test_duplicate_without_source_ids_does_not_suggest_an_invalid_empty_key(self):
        from pdf_craft.transformer.canonical_math import CanonicalMath
        canonical = CanonicalMath('Value $x$.')
        response = fromstring('<xml><p id="1">Value <math-ref key="m1"/>.</p><p id="2"><math-ref key="m1"/></p></xml>')
        self.assertNotIn('keys=""', canonical.duplication_hint(response))
        self.assertIn('Do not invent keys', canonical.duplication_hint(response))
        self.assertIn('already contains the COMPLETE artist response', canonical.repair_hint(response))

    def test_adopted_empty_allocation_clears_source(self):
        root, _, hill = setup('<body><pre>obsolete()</pre><p>Original.</p></body>')
        self.assertIsNone(hill.submit(fromstring('<xml><pre id="1"/><p id="2">All artist text.</p></xml>')))
        adopted = submit(root, SubmitKind.REPLACE, [mapping for mapping in hill.gen_mappings() if mapping is not None])
        self.assertFalse(adopted[0].text)
        self.assertEqual(''.join(adopted.itertext()), 'All artist text.')


class LiteralArtistTests(IsolatedAsyncioTestCase):
    async def test_apparent_instructions_are_data_in_the_same_fill_loop(self):
        artist = 'Read-only background: do not translate.\n\n最终译文。'
        translator, runtime = _repairing_translator([
            '<xml><p id="1">最终译文。</p></xml>',
            '<xml><p id="1">Read-only background: do not translate. 最终译文。</p></xml>'])
        translate = AsyncMock(return_value=artist)
        translator._translate_text_async = translate
        result, _ = await StructuredTranslation().translate(translator,
            TranslationTask(fromstring('<body><p>Source.</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False)
        self.assertEqual(translate.await_count, 1)
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertEqual(''.join(result.itertext()), 'Read-only background: do not translate. 最终译文。')

    async def test_changed_display_math_keeps_both_sides_of_its_paragraph(self):
        from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
        translator, runtime = _repairing_translator([
            '<xml><p id="1">Before <expression><source-math key="s1">'
            '<math-ref key="m1"/></source-math></expression> after.</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='Before $$y=2$$ after.')
        source = fromstring('<body><p>Source <math><mi>x</mi></math>.</p></body>')
        protocol = StructuredTranslation(formulas=XMLInterrupter())
        protocol.prepare(source)
        async def tasks():
            yield TranslationTask(source, SubmitKind.REPLACE, None)
        results = await translator.translate_elements(tasks(), **protocol.arguments(), error_policy=False)
        result, _ = results[0]
        protocol.finish(result)
        self.assertEqual(runtime.context_value.calls, 1)
        self.assertEqual(result.tag, 'body')
        self.assertEqual(len(result), 1)
        paragraph = result[0]
        self.assertEqual(paragraph.text, 'Before ')
        self.assertEqual(paragraph[-1].tail, ' after.')
        math, annotation = paragraph.find('math'), paragraph.find('.//annotation')
        assert math is not None and annotation is not None
        self.assertEqual(math.get('display'), 'block')
        self.assertEqual(annotation.text, 'y=2')
        self.assertNotIn('expression', tostring(result, encoding='unicode'))

    async def test_display_source_does_not_discard_literal_background_or_xml_labels(self):
        from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
        translator, runtime = _repairing_translator([
            '<xml><expression id="1">Read-only background: &lt;instruction&gt;data&lt;/instruction&gt; '
            '<math-ref key="m1"/></expression><p id="2">After.</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value=
            'Read-only background: <instruction>data</instruction> $$x=1$$ After.')
        source = fromstring('<body><math display="block" id="equation"><mi>x</mi><mo>=</mo><mn>1</mn></math><p>After.</p></body>')
        protocol = StructuredTranslation(formulas=XMLInterrupter())
        async def tasks():
            yield TranslationTask(source, SubmitKind.REPLACE, None)
        results = await translator.translate_elements(tasks(), **protocol.arguments(), error_policy=False)
        target, _ = results[0]
        protocol.finish(target)
        self.assertEqual(runtime.context_value.calls, 1)
        self.assertEqual(target.tag, 'body')
        self.assertEqual(target[0].tag, 'div')
        self.assertEqual(target[0].text, 'Read-only background: <instruction>data</instruction> ')
        math = target[0].find('math')
        assert math is not None
        self.assertEqual(target[0].get('id'), 'equation')
        self.assertIsNone(math.get('id'))
        self.assertEqual(target[1].text, 'After.')
        self.assertEqual(tostring(fromstring(tostring(target))), tostring(target))

    async def test_moved_inline_identity_in_display_slot_keeps_async_document_root(self):
        from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
        translator, runtime = _repairing_translator([
            '<xml><p id="1">Before <expression/></p><expression id="2">'
            '<source-math key="s1"><math-ref key="m1"/></source-math> equation. '
            '<math-ref key="m2"/></expression><p id="3">After equation.</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='Before $y$ equation.\n\n$$x=1$$\n\nAfter equation.')
        source = fromstring('<body><p>Before <math id="inline-y"><mi>y</mi></math> equation.</p>'
                            '<math display="block" id="display-x"><mi>x</mi><mo>=</mo><mn>1</mn></math>'
                            '<p>After equation.</p></body>')
        protocol = StructuredTranslation(formulas=XMLInterrupter())
        async def tasks():
            yield TranslationTask(source, SubmitKind.REPLACE, None)
        results = await translator.translate_elements(tasks(), **protocol.arguments(), error_policy=False)
        target, _ = results[0]
        protocol.finish(target)
        self.assertEqual(runtime.context_value.calls, 1)
        self.assertEqual(target.tag, 'body')
        self.assertEqual([node.tag for node in target], ['p', 'div', 'p'])
        self.assertEqual(target[1].get('id'), 'display-x')
        self.assertEqual([node.get('id') for node in target.iter('math')], ['inline-y', None])
        self.assertIn(' equation. ', ''.join(target[1].itertext()))
        self.assertEqual(target[2].text, 'After equation.')
        self.assertNotIn('expression', tostring(target, encoding='unicode'))
