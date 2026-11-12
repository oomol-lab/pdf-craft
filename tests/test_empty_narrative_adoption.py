"""An accepted empty allocation must survive coverage, layers and rendering."""
# pylint: disable=protected-access
from copy import deepcopy
from pathlib import Path
import re
import json
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from zipfile import ZipFile

from pdf_craft import AsyncPDFCraft, RenderMode
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import Chapter, Reference, SourceTextFragment, TextFlowItem, encode, narrative_texts
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.package import ChapterExtractionTransformer, append_translation_layer_to_workspace
from pdf_craft.transformer.translation_coverage import read_coverage
from pdf_craft.transformer.xml_translator.xml import decode_friendly, encode_friendly
from tests.extraction_helpers import make_extraction
from tests.translation_model import translator_fixture


class _ChapterBatch(NarrativeXMLTransformer):
    @property
    def supports_stream(self):
        return False


def paragraph(order, text):
    return TextFlowItem('body', 0, [SourceTextFragment(1, order, (0, order * 10, 90, order * 10 + 9), [text])])


class EmptyNarrativeAdoptionTests(IsolatedAsyncioTestCase):
    async def test_empty_body_and_notes_survive_both_coverage_writers_and_selected_layer(self):
        for stream in (True, False):
            for notes in (False, True):
                with self.subTest(stream=stream, notes=notes), TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = make_extraction(root / 'source', language='en')
                    chapter = Chapter(None, -1, [paragraph(1, 'First original.'), paragraph(2, 'Second original.')])
                    if notes:
                        reference = Reference(1, 1, '1', [paragraph(3, 'First note original.'), paragraph(4, 'Second note original.')])
                        body = chapter.flow_items[0]
                        assert isinstance(body, TextFlowItem)
                        fragment = body.children[0]
                        assert isinstance(fragment, SourceTextFragment)
                        fragment.content.append(reference)
                    original = root / 'source/chapters/chapter_head.xml'
                    save_xml(encode(chapter), original)
                    before = original.read_bytes()
                    translator, model = translator_fixture()
                    def answer(messages):
                        if messages[0]['content'].startswith('You are a translator.'):
                            raw = messages[1]['content'].split('Text to translate:\n', 1)[-1].strip()
                            if re.search(r'\b1\b', raw):
                                return '1 Translated once.' if raw.startswith('1') else 'Translated once. 1'
                            return 'Translated once.'
                        template = re.search(r'```XML\n(.*?)\n```', messages[1]['content'], re.S)
                        assert template is not None, messages[-1]['content'][:1800]
                        filled = next(decode_friendly(template[1], tags='xml'))
                        first = True
                        for node in filled:
                            if node.tag == 'fragment':
                                for part in node.iter():
                                    part.text = part.tail = None
                                node.text = 'Translated once.' if first else None
                                first = False
                        return encode_friendly(filled)
                    model.answer = answer
                    transformer = (NarrativeXMLTransformer if stream else _ChapterBatch)(translator)
                    translated = await ChapterExtractionTransformer(transformer)._transform_to_workspace_async(
                        source, root / 'translated')
                    coverage = read_coverage(root / 'translated/translation.xml').narrative
                    self.assertEqual(len(coverage), 4 if notes else 2)
                    self.assertEqual(set(coverage.values()), {'translated'})
                    layered = append_translation_layer_to_workspace(source, translated, root / 'layered',
                        translation_id='review', target_language='en')
                    await AsyncPDFCraft().render_markdown(layered, root / 'actual.md', Path('assets'),
                        mode=RenderMode.REPLACE, translation_id='review')
                    actual = (root / 'actual.md').read_text(encoding='utf-8')
                    self.assertIn('Translated once.', actual)
                    self.assertNotIn('original.', actual)
                    if not notes:
                        self.assertEqual(actual.strip(), 'Translated once.')
                    await AsyncPDFCraft().render_epub(layered, root / 'actual.epub',
                        mode=RenderMode.REPLACE, translation_id='review')
                    with ZipFile(root / 'actual.epub') as archive:
                        xhtml = ''.join(archive.read(name).decode('utf-8') for name in archive.namelist()
                                        if name.endswith(('.html', '.xhtml')))
                    self.assertIn('Translated once.', xhtml)
                    self.assertNotIn('original.', xhtml)
                    self.assertEqual(original.read_bytes(), before)

    async def test_v8_note_availability_migrates_without_changing_source_or_explicit_empty(self):
        from pdf_craft.common import read_xml
        from tests.test_pcex_translation_rendering import _source, _add_translation
        for explicit, visible in ((True, False), (False, False), (False, True)):
            with self.subTest(explicit=explicit, visible=visible), TemporaryDirectory() as directory:
                root = Path(directory)
                source = _source(root / 'source')
                _add_translation(root / 'source', 'review', 'EN')
                path = root / 'source/translations/review/chapters/chapter_1.xml'
                chapter = read_xml(path)
                fragment = chapter.find('references/ref/flow/text/fragment')
                assert fragment is not None
                if not visible:
                    fragment.text = None
                save_xml(chapter, path)
                coverage_path = root / 'source/translations/review/coverage.xml'
                coverage = read_xml(coverage_path)
                narrative = coverage.find('narrative')
                assert narrative is not None
                if not explicit:
                    for entry in list(narrative):
                        if entry.get('order') == '3':
                            narrative.remove(entry)
                save_xml(coverage, coverage_path)
                manifest_path = root / 'source/manifest.json'
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                manifest['format_version'] = 8
                manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
                source_bytes, target_bytes = (root / 'source/chapters/chapter_1.xml').read_bytes(), path.read_bytes()
                # Private materialization must apply migration every time without
                # mutating the historical workspace or layer.
                for _ in range(2):
                    with source._materialize() as paths:
                        self.assertEqual(json.loads(paths.manifest.read_text(encoding='utf-8'))['format_version'], 9)
                        states = read_coverage(paths.translations / 'review/coverage.xml').narrative
                        self.assertEqual(states[('1', 1, 3)], 'translated' if explicit or visible else 'preserved')
                        self.assertEqual((paths.chapters / 'chapter_1.xml').read_bytes(), source_bytes)
                        self.assertEqual((paths.translations / 'review/chapters/chapter_1.xml').read_bytes(), target_bytes)
                self.assertEqual(json.loads(manifest_path.read_text(encoding='utf-8'))['format_version'], 8)
                self.assertEqual(path.read_bytes(), target_bytes)

    async def test_missing_state_is_preserved_even_when_target_is_empty(self):
        class MissingSecond:
            async def transform(self, chapter):
                target = deepcopy(chapter)
                items = list(narrative_texts(target))
                items[0].children[0].content = ['Translated once.']
                items[1].children[0].content = []
                setattr(items[1], '_translation_missing', True)
                return target
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source', language='en')
            save_xml(encode(Chapter(None, -1, [paragraph(1, 'First original.'), paragraph(2, 'Second original.')])),
                     root / 'source/chapters/chapter_head.xml')
            translated = await ChapterExtractionTransformer(MissingSecond())._transform_to_workspace_async(
                source, root / 'translated')
            self.assertEqual(list(read_coverage(root / 'translated/translation.xml').narrative.values()),
                             ['translated', 'preserved'])
            layered = append_translation_layer_to_workspace(source, translated, root / 'layered',
                translation_id='review', target_language='en')
            await AsyncPDFCraft().render_markdown(layered, root / 'actual.md', Path('assets'),
                mode=RenderMode.REPLACE, translation_id='review')
            self.assertEqual((root / 'actual.md').read_text(encoding='utf-8').strip(), 'Translated once.\n\nSecond original.')
