"""Adopt empty slots without mistaking them for missing translations.

The numeric fixture retains the repeated 20-slot shape of service #382; book
names, page metadata and original geometry are deliberately absent.
"""
# pylint: disable=protected-access
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import Element, SubElement

from pdf_craft import RetryPolicy
from pdf_craft.common import read_xml, save_xml
from pdf_craft.document.render import materialize_pdf_patch_view
from pdf_craft.extractor.chapter.chapter import Chapter, SourceTextFragment, TextFlowItem, encode
from pdf_craft.pipeline.pdf.pipeline import PDFTranslationPipeline
from pdf_craft.transformer.furniture import FurniturePosition, FurnitureSection
from pdf_craft.transformer.furniture_xml import FurnitureXMLTransformer
from pdf_craft.transformer.package import FurnitureExtractionTransformer, append_translation_layer_to_workspace
from pdf_craft.transformer.translation_coverage import TranslationCoverage, read_coverage
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLFillError
from pdf_craft.transformer.xml_translator.xml import encode_friendly
from tests.extraction_helpers import make_extraction
from tests.fill_fixtures import model_replies
from tests.translation_model import translator_fixture

SOURCE = ['0', '1', 'Melee', '3', '4+', '4', '0', '1', 'Melee', '3',
          '4+', '4', '-2', '1', 'Melee', '2', '4+', '4', '0', '1']
ARTIST = '\n\n'.join(value.replace('Melee', 'Cuerpo a cuerpo') for value in SOURCE[:-2])


def sections():
    return [FurnitureSection(1, (10+index*30, 40, 35+index*30, 60), text, str(index+71))
            for index, text in enumerate(SOURCE)]


def answer():
    root = Element('xml')
    for index, text in enumerate(ARTIST.split('\n\n') + ['', ''], 1):
        SubElement(root, 'section', id=str(index)).text = text
    return root


class EmptyFurnitureTests(IsolatedAsyncioTestCase):
    async def test_original_shape_adopts_empty_slots_without_repair(self):
        translator, model = translator_fixture()
        model_replies(model, ARTIST, [encode_friendly(answer())])
        result = await FurnitureXMLTransformer(translator).transform_sections(
            1, sections(), error_policy=False, retry_policy=RetryPolicy(fill=0))
        self.assertEqual(result, ARTIST.split('\n\n') + ['', ''])
        self.assertEqual(len(model.wire_calls), 2, 'one artist and one fill request')

    async def test_missing_artist_content_and_structure_still_fail(self):
        for kind in ['artist_has_tail', 'missing', 'extra', 'duplicate', 'order', 'node', 'id']:
            with self.subTest(kind=kind):
                candidate = answer()
                artist = ARTIST
                if kind == 'artist_has_tail':
                    artist += '\n\n0\n\n1'
                elif kind == 'missing':
                    candidate[0].text = ''
                elif kind == 'extra':
                    candidate[-1].text = 'new'
                elif kind == 'duplicate':
                    candidate[1].text = '0 1'
                elif kind == 'order':
                    candidate[0].text, candidate[1].text = candidate[1].text, candidate[0].text
                elif kind == 'node':
                    candidate.remove(candidate[-1])
                else:
                    candidate[-1].set('id', '19')
                translator, model = translator_fixture()
                model_replies(model, artist, [encode_friendly(candidate)])
                with self.assertRaises(XMLFillError):
                    await FurnitureXMLTransformer(translator).transform_sections(
                        1, sections(), error_policy=False, retry_policy=RetryPolicy(fill=0))
                self.assertEqual(len(model.wire_calls), 2)

    async def test_leaf_position_adopts_empty_and_nonempty_text(self):
        for text in ['', 'Translated header']:
            with self.subTest(text=text):
                translator, model = translator_fixture()
                root = Element('xml')
                SubElement(root, 'furniture-position', id='1').text = text
                model_replies(model, text, [encode_friendly(root)])
                result = await FurnitureXMLTransformer(translator).transform_position(
                    FurniturePosition(1, 1, 'universal', 'Header'), error_policy=False)
                self.assertEqual(result, text)
                self.assertEqual(len(model.wire_calls), 2)

    async def test_empty_artist_group_is_adopted_without_source_padding(self):
        translator, model = translator_fixture()
        root = answer()
        for node in root:
            node.text = None
        model_replies(model, '', [encode_friendly(root)])
        result = await FurnitureXMLTransformer(translator).transform_sections(
            1, sections(), error_policy=False, retry_policy=RetryPolicy(fill=0))
        self.assertEqual(result, ['']*20)
        self.assertEqual(len(model.wire_calls), 2)

    async def test_persisted_layer_coverage_and_pdf_plan_keep_empty_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = make_extraction(root/'source', page_pixel_sizes={1: (650, 200)}, render_dpi=72)
            save_xml(encode(Chapter(None, -1, [])), root/'source/chapters/chapter_head.xml')
            furniture = Element('furnitures')
            patterns = SubElement(furniture, 'patterns')
            pattern = SubElement(patterns, 'pattern', id='1', kind='universal')
            SubElement(pattern, 'position', id='1').text = 'Header'
            page = SubElement(SubElement(furniture, 'pages'), 'page', index='1')
            for section in sections():
                assert section.section_id is not None
                SubElement(page, 'section', id=section.section_id,
                           det=','.join(map(str, section.det))).text = section.content
            associated = SubElement(page, 'section', id='91', det='10,110,100,130')
            SubElement(associated, 'association', kind='universal', pattern_id='1', position_id='1')
            save_xml(furniture, root/'source/furnitures.xml')
            before = (root/'source/furnitures.xml').read_bytes()
            translator, model = translator_fixture()

            def respond(messages):
                if messages[0]['content'].startswith('You are a translator.'):
                    return '' if messages[1]['content'].endswith('Header') else ARTIST
                return ('<xml><furniture-position id="1"/></xml>'
                        if '<furniture-position' in messages[1]['content'] else encode_friendly(answer()))
            model.answer = respond
            translated = await FurnitureExtractionTransformer(FurnitureXMLTransformer(translator)).transform(
                source, root/'translated.pcex', error_policy=False)
            layered = append_translation_layer_to_workspace(
                source, translated, root/'layered', translation_id='spanish',
                target_language='Spanish', include_furniture=True)
            exported = await layered._export_async(root/'layered.pcex')
            with materialize_pdf_patch_view(exported, 'spanish') as view:
                stored = read_xml(view.furnitures)
                cells = stored.findall('pages/page/section')
                self.assertEqual(len(cells), 21)
                self.assertEqual([node.text or '' for node in cells[:20]], ARTIST.split('\n\n')+['', ''])
                position = stored.find('patterns/pattern/position')
                assert position is not None
                self.assertIsNone(position.text)
                coverage = read_coverage(view.coverage)
                self.assertEqual(set(coverage.sections.values()), {'translated'})
                self.assertEqual(coverage.positions, {('1', '1'): 'translated'})
                replacements = list(PDFTranslationPipeline()._iter_covered_replacements(
                    [], view.furnitures, coverage, {1: (650, 200)}, 72))
                self.assertEqual(len(replacements), 21)
                empties = {value.furniture_identity for value in replacements if value.text == ''}
                self.assertEqual(empties, {(1, '89'), (1, '90'), (1, '91')})
                for value in replacements:
                    if value.text == '':
                        self.assertNotIn(value.source_regions()[0], value.obstacle_regions)
            self.assertEqual((root/'source/furnitures.xml').read_bytes(), before)
            self.assertEqual(len(model.wire_calls), 4)

    async def test_pdf_empty_narrative_uses_the_same_adoption_semantics(self):
        first = SourceTextFragment(1, 1, (10, 10, 40, 30), [])
        second = SourceTextFragment(1, 2, (50, 10, 80, 30), [])
        chapter = Chapter(None, -1, [TextFlowItem('body', 0, [first]), TextFlowItem('body', 0, [second])])
        coverage = TranslationCoverage({('head', 1, 1): 'translated', ('head', 1, 2): 'preserved'}, {}, {}, {})
        with tempfile.TemporaryDirectory() as folder:
            replacements = list(PDFTranslationPipeline()._iter_covered_replacements(
                [chapter], Path(folder)/'no-furniture.xml', coverage, {1: (100, 100)}, 72))
        self.assertEqual(len(replacements), 1)
        self.assertEqual(replacements[0].text, '')
        self.assertEqual(replacements[0].bbox, first.bbox)
        self.assertEqual([region.bbox for region in replacements[0].obstacle_regions], [second.bbox])

    async def test_none_and_real_failures_stay_preserved(self):
        translator, model = translator_fixture()
        model_replies(model, ARTIST, ['<xml/>'])
        result = await FurnitureXMLTransformer(translator).transform_sections(
            1, sections(), error_policy=True, retry_policy=RetryPolicy(fill=0))
        self.assertEqual(result, (None,)*20)
        self.assertEqual(len(model.wire_calls), 2)
