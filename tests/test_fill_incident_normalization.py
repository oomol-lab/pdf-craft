"""Small public counterparts of Nov-10 fill incidents; no private book text."""
# pylint: disable=protected-access
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring

from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.segment import BlockSegment, search_inline_segments, search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.xml_translator.xml_translator.hill_climbing import HillClimbing
from pdf_craft.transformer.xml_translator.xml_translator.submitter import SubmitKind, submit
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


    def test_existing_protected_blank_lines_survive_structural_separator(self):
        response = self.validate('<body><pre>run()\n\n</pre><p>After</p></body>',
                                 'run()\n\n\n\nAfter',
                                 '<xml><pre id="1">run()\n\n</pre><p id="2">After</p></xml>')
        self.assertEqual(response[0].text, 'run()\n\n')


    def test_missing_inline_feedback_shows_source_and_current_wrong_assignment(self):
        _, _, hill = setup('<body><p><code class="boolean">true</code> then <code class="function">errors.Is()</code></p></body>')
        candidate = fromstring('<xml><p id="1">verdadeiro then <code id="2">errors.Is()</code></p></xml>')
        self.assertIsNotNone(hill.validate(candidate))
        blocks = hill.inline_feedback_blocks(candidate)
        self.assertEqual(len(blocks), 1)
        self.assertIn('<code id="2">true</code>', blocks[0]['source'])
        self.assertIn('<code id="3">errors.Is()</code>', blocks[0]['source'])
        self.assertIn('<code id="2">errors.Is()</code>', blocks[0]['candidate'])


    def test_incident_epub_publisher_restores_mathml_namespace(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from zipfile import ZipFile
        from pdf_craft_tool.smoke.fill_incident_regression import publish_group
        source = fromstring('<body><p>Value <math id="value"><mi>x</mi></math>.</p></body>')
        with TemporaryDirectory() as directory:
            publish_group(source, Path(directory))
            with ZipFile(Path(directory) / 'adopted.epub') as archive:
                restored = fromstring(archive.read('content.xhtml'))
            math = restored.find('.//{http://www.w3.org/1998/Math/MathML}math')
            assert math is not None
            self.assertEqual(math.get('id'), 'value')
            self.assertEqual(math[0].tag, '{http://www.w3.org/1998/Math/MathML}mi')
        self.assertNotIn('xmlns', source[0][0].attrib)

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
