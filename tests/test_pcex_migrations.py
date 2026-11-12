"""Historical PCEX shapes are consumed only at the materialization boundary."""
# pylint: disable=protected-access
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock
from typing import cast
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from PIL import Image

from pdf_craft import AsyncPDFCraft, RenderMode
from pdf_craft.common import read_xml, save_xml
from pdf_craft.document.package import FORMAT_VERSION
from pdf_craft.document.migrations import migrate_workspace
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import Chapter, InlineExpression, SourceAsset, StandaloneAsset, encode
from tests.extraction_helpers import make_extraction
from tests.test_structured_translation import offline_translator


def required(root: ET.Element, path: str) -> ET.Element:
    value = root.find(path)
    assert value is not None, path
    return value


def old_archive(source: Path, target: Path, *, migrate_layers=True):
    """Turn a canonical caption into the exact historical content-only shape."""
    with ZipFile(source) as before, ZipFile(target, 'w', compression=ZIP_DEFLATED) as after:
        for member in before.infolist():
            data = before.read(member)
            if member.filename == 'manifest.json':
                manifest = json.loads(data)
                manifest['format_version'] = 4
                data = json.dumps(manifest).encode()
            elif member.filename.endswith('chapter_head.xml') and (
                    member.filename.startswith('chapters/') or migrate_layers):
                root = ET.fromstring(data)
                required(root, './/asset/caption').tag = 'content'
                data = ET.tostring(root)
            after.writestr(member, data)


class PCEXMigrationTests(TestCase):
    def test_invalid_historical_workspace_is_rejected_before_copying(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            extraction = make_extraction(root / 'source')
            save_xml(encode(Chapter(None, -1, [])), root / 'outside.xml')
            (root / 'source/chapters/chapter_head.xml').symlink_to(root / 'outside.xml')
            manifest_path = root / 'source/manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['format_version'] = 4
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'invalid chapter member'):
                extraction._validate()

    def test_v5_image_content_is_opaque_even_without_caption(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            extraction = make_extraction(root / 'source')
            asset = SourceAsset(1, 'image', (1, 1, 80, 40), content=['OPAQUE_CONTENT'])
            save_xml(encode(Chapter(None, -1, [StandaloneAsset(asset)])),
                     root / 'source/chapters/chapter_head.xml')
            with extraction._materialize() as paths:
                image = required(read_xml(paths.chapters / 'chapter_head.xml'), './/asset')
                self.assertIsNotNone(image.find('content'))
                self.assertIsNone(image.find('caption'))
            before = (root / 'source/chapters/chapter_head.xml').read_bytes()
            migrate_workspace(root / 'source')
            self.assertEqual((root / 'source/chapters/chapter_head.xml').read_bytes(), before)

    def test_explicit_v4_caption_and_opaque_content_are_not_merged(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            extraction = make_extraction(root / 'source')
            asset = SourceAsset(1, 'image', (1, 1, 80, 40), content=['Opaque'], caption=['Caption'])
            save_xml(encode(Chapter(None, -1, [StandaloneAsset(asset)])), root / 'source/chapters/chapter_head.xml')
            manifest = json.loads((root / 'source/manifest.json').read_text(encoding='utf-8'))
            manifest['format_version'] = 4
            (root / 'source/manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with extraction._materialize() as paths:
                image = required(read_xml(paths.chapters / 'chapter_head.xml'), './/asset')
                self.assertEqual(image.findtext('content'), 'Opaque')
                self.assertEqual(image.findtext('caption'), 'Caption')
                self.assertEqual(json.loads(paths.manifest.read_text(encoding='utf-8'))['format_version'], FORMAT_VERSION)
            self.assertEqual(json.loads((root / 'source/manifest.json').read_text(encoding='utf-8'))['format_version'], 4)


class PCEXLayerMigrationTests(IsolatedAsyncioTestCase):
    async def test_v5_missing_fragment_ids_migrate_source_layers_and_coverage_together(self):
        from pdf_craft.extractor.chapter.chapter import SourceTextFragment, TextFlowItem, decode
        craft = AsyncPDFCraft()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root/'source')
            paragraph = TextFlowItem('body', -1, [SourceTextFragment(1, 7, (0,0,90,30), ['Figure before'])])
            save_xml(encode(Chapter(None, -1, [paragraph])), root/'source/chapters/chapter_head.xml')
            await craft.translate_extraction(source, root/'current.pcex', offline_translator(),
                                             translation_id='existing', target_language='zh-CN', error_policy=False)
            with ZipFile(root/'current.pcex') as before, ZipFile(root/'v5.pcex', 'w') as after:
                for member in before.infolist():
                    data = before.read(member)
                    if member.filename == 'manifest.json':
                        manifest = json.loads(data)
                        manifest['format_version'] = 5
                        data = json.dumps(manifest).encode()
                    elif member.filename.endswith('.xml') and ('/chapters/' in member.filename or member.filename.startswith('chapters/')):
                        xml = ET.fromstring(data)
                        for fragment in xml.iter('fragment'):
                            for key in ('fragment_id', 'source_start', 'source_end', 'source_text'):
                                fragment.attrib.pop(key, None)
                        data = ET.tostring(xml)
                    after.writestr(member, data)
            original = (root/'v5.pcex').read_bytes()
            opened = await craft.open_extraction(root/'v5.pcex')
            with opened._materialize() as paths:
                for path in (paths.chapters/'chapter_head.xml', paths.translations/'existing/chapters/chapter_head.xml'):
                    node = required(read_xml(path), './flow/text/fragment')
                    self.assertEqual(node.get('fragment_id'), '7')
                    self.assertEqual(node.get('source_start'), '0')
                    self.assertIsNone(node.get('source_text'), 'migration cannot invent raw OCR evidence')
                    decode(read_xml(path))
                coverage = read_xml(paths.translations/'existing/coverage.xml')
                self.assertEqual(required(coverage, './narrative/paragraph').get('order'), '7')
                snapshot = {path.relative_to(paths.root): path.read_bytes() for path in paths.root.rglob('*') if path.is_file()}
                migrate_workspace(paths.root)
                self.assertEqual(snapshot, {path.relative_to(paths.root): path.read_bytes() for path in paths.root.rglob('*') if path.is_file()})
            await craft.render_markdown(opened, root/'bilingual.md', mode=RenderMode.BILINGUAL, translation_id='existing')
            self.assertIn('Figure before', (root/'bilingual.md').read_text())
            self.assertIn('图注 之前', (root/'bilingual.md').read_text())
            self.assertEqual((root/'v5.pcex').read_bytes(), original)

    async def test_v6_keeps_source_and_layer_provenance_without_reinterpreting_fields(self):
        from pdf_craft.extractor.chapter.chapter import SourceTextFragment, TextFlowItem
        from pdf_craft.transformer.package import append_translation_layer_to_workspace
        from pdf_craft.transformer.translation_coverage import (
            NarrativeCoverage, FurnitureSectionCoverage, write_narrative_coverage, write_furniture_coverage,
        )
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = make_extraction(root / 'source')
            fragment = SourceTextFragment(1, 7, (0, 0, 90, 20), ['Body'], fragment_id=42,
                                          source_start=2, source_end=6, source_text='__Body__')
            save_xml(encode(Chapter(None, -1, [TextFlowItem('body', 0, [fragment])])),
                     root / 'source/chapters/chapter_head.xml')
            furniture = ET.fromstring('<furnitures><patterns/><pages><page index="1">'
                                      '<section id="9" det="0,30,90,50">Cell</section>'
                                      '</page></pages></furnitures>')
            save_xml(furniture, root / 'source/furnitures.xml')
            write_narrative_coverage(root / 'source/translation.xml', [NarrativeCoverage('head', 1, 42, 'translated')])
            write_furniture_coverage(root / 'source/translation.xml', [], [FurnitureSectionCoverage(1, '0,30,90,50', 'translated', '9')])
            layered = append_translation_layer_to_workspace(source, source, root / 'layered',
                translation_id='existing', target_language='en', include_furniture=True)
            await layered._export_async(root / 'current.pcex')
            preserved = {}
            with ZipFile(root / 'current.pcex') as before, ZipFile(root / 'v6.pcex', 'w') as after:
                for item in before.infolist():
                    data = before.read(item.filename)
                    if item.filename == 'manifest.json':
                        manifest = json.loads(data)
                        manifest['format_version'] = 6
                        data = json.dumps(manifest).encode()
                    elif not item.is_dir():
                        preserved[item.filename] = data
                    after.writestr(item, data)
            original = (root / 'v6.pcex').read_bytes()
            opened = await AsyncPDFCraft().open_extraction(root / 'v6.pcex')
            with opened._materialize() as paths:
                self.assertEqual(json.loads(paths.manifest.read_text())['format_version'], FORMAT_VERSION)
                for name, data in preserved.items():
                    self.assertEqual((paths.root / name).read_bytes(), data)
                for name in ['chapters/chapter_head.xml', 'translations/existing/chapters/chapter_head.xml']:
                    node = required(read_xml(paths.root / name), './flow/text/fragment')
                    self.assertEqual((node.get('fragment_id'), node.get('source_start'), node.get('source_end')),
                                     ('42', '2', '6'))
                snapshot = {p.relative_to(paths.root): p.read_bytes() for p in paths.root.rglob('*') if p.is_file()}
                migrate_workspace(paths.root)
                self.assertEqual(snapshot, {p.relative_to(paths.root): p.read_bytes() for p in paths.root.rglob('*') if p.is_file()})
            self.assertEqual((root / 'v6.pcex').read_bytes(), original)
            # v7's empty-text extension must not legalize malformed v6 input.
            with ZipFile(root / 'v6.pcex') as before, ZipFile(root / 'invalid-v6.pcex', 'w') as after:
                for item in before.infolist():
                    data = before.read(item.filename)
                    if item.filename == 'furnitures.xml':
                        value = ET.fromstring(data)
                        required(value, './pages/page/section').text = None
                        data = ET.tostring(value)
                    after.writestr(item, data)
            with self.assertRaisesRegex(ValueError, 'missing content'):
                await AsyncPDFCraft().open_extraction(root / 'invalid-v6.pcex')

    async def test_current_opaque_content_is_not_translated_or_rendered(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = make_extraction(root / 'source')
            asset = SourceAsset(1, 'image', (1, 1, 80, 40), content=['OPAQUE_ONLY'], asset_hash='a' * 64)
            Image.new('RGB', (2, 2)).save(root / 'source/assets' / ('a' * 64 + '.png'))
            save_xml(encode(Chapter(None, -1, [StandaloneAsset(asset)])), root / 'source/chapters/chapter_head.xml')
            craft = AsyncPDFCraft()
            translator = offline_translator()
            target = await craft.translate_extraction(source, root / 'current.pcex', translator,
                                                      translation_id='opaque-layer', target_language='zh-CN')
            cast(AsyncMock, translator._translate_text_async).assert_not_awaited()
            await craft.render_markdown(target, root / 'output.md')
            self.assertNotIn('OPAQUE_ONLY', (root / 'output.md').read_text(encoding='utf-8'))

    async def test_v4_source_and_layers_migrate_together_before_translation_or_rendering(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            craft = AsyncPDFCraft()
            source = make_extraction(root / 'source')
            formula = InlineExpression(ExpressionKind.INLINE_DOLLAR, 'x')
            asset = SourceAsset(1, 'image', (1, 1, 80, 40),
                                caption=['Figure before ', formula, ' after'], asset_hash='a' * 64)
            Image.new('RGB', (2, 2)).save(root / 'source/assets' / ('a' * 64 + '.png'))
            save_xml(encode(Chapter(None, -1, [StandaloneAsset(asset)])), root / 'source/chapters/chapter_head.xml')
            await craft.translate_extraction(source, root / 'current.pcex', offline_translator(),
                                             translation_id='caption-layer', target_language='zh-CN', error_policy=False)
            old_archive(root / 'current.pcex', root / 'legacy.pcex')
            original_bytes = (root / 'legacy.pcex').read_bytes()
            opened = await craft.open_extraction(root / 'legacy.pcex')
            with opened._materialize() as paths:
                manifest = json.loads(paths.manifest.read_text(encoding='utf-8'))
                self.assertEqual(manifest['format_version'], FORMAT_VERSION)
                for chapters, expected in ((paths.chapters, 'Figure before '),
                                           (paths.translations / 'caption-layer/chapters', '图注 之前 ')):
                    image = required(read_xml(chapters / 'chapter_head.xml'), './/asset')
                    self.assertIsNone(image.find('content'))
                    self.assertEqual(required(image, 'caption').text, expected)
                    self.assertEqual(required(image, 'caption/inline_expr').text, 'x')
                snapshot = {p.relative_to(paths.root): p.read_bytes() for p in paths.root.rglob('*') if p.is_file()}
                migrate_workspace(paths.root)
                self.assertEqual(snapshot, {p.relative_to(paths.root): p.read_bytes()
                                           for p in paths.root.rglob('*') if p.is_file()})
            await craft.export_extraction(opened, root / 'exported.pcex')
            with ZipFile(root / 'legacy.pcex') as old, ZipFile(root / 'exported.pcex') as new:
                for name in old.namelist():
                    if name.startswith('assets/') or name.endswith(('coverage.xml', 'metadata.json', 'index.json')):
                        self.assertEqual(old.read(name), new.read(name))
            for mode, original_count, translated_count in (
                    (RenderMode.SOURCE, 1, 0), (RenderMode.REPLACE, 0, 1), (RenderMode.BILINGUAL, 1, 1)):
                md = root / f'{mode.value}.md'
                await craft.render_markdown(opened, md, mode=mode,
                                            translation_id=None if mode == RenderMode.SOURCE else 'caption-layer')
                self.assertEqual(md.read_text(encoding='utf-8').count('Figure before'), original_count)
                self.assertEqual(md.read_text(encoding='utf-8').count('图注 之前'), translated_count)
            self.assertEqual((root / 'legacy.pcex').read_bytes(), original_bytes)
            updated = await craft.translate_extraction(opened, root / 'second-layer.pcex', offline_translator(),
                                                       translation_id='another-layer', target_language='zh-CN')
            with updated._materialize() as paths:
                self.assertTrue((paths.translations / 'caption-layer/chapters/chapter_head.xml').is_file())
                self.assertTrue((paths.translations / 'another-layer/chapters/chapter_head.xml').is_file())
            # Invalid old layers must not be silently repaired by migration.
            old_archive(root / 'current.pcex', root / 'invalid.pcex', migrate_layers=False)
            with self.assertRaisesRegex(ValueError, 'structure'):
                await craft.open_extraction(root / 'invalid.pcex')
            # Structurally valid but conflicting fields must not lose a layer's
            # existing caption when the source's caption was empty.
            with ZipFile(root / 'legacy.pcex') as before, ZipFile(root / 'ambiguous.pcex', 'w') as after:
                for member in before.infolist():
                    data = before.read(member)
                    if member.filename.endswith('chapter_head.xml'):
                        chapter = ET.fromstring(data)
                        caption = ET.SubElement(required(chapter, './/asset'), 'caption')
                        if member.filename.startswith('translations/'):
                            caption.text = 'Existing translated caption'
                        data = ET.tostring(chapter)
                    after.writestr(member, data)
            with self.assertRaisesRegex(ValueError, 'ambiguous legacy image caption'):
                await craft.open_extraction(root / 'ambiguous.pcex')


class FormulaCoverageMigrationTests(TestCase):
    def test_v7_formula_layers_migrate_without_rewriting_content(self):
        from tests.test_pcex_translation_rendering import _source, _add_translation
        from pdf_craft.extractor.chapter.chapter import DisplayFormula, decode
        from pdf_craft.transformer.translation_coverage import read_coverage
        with TemporaryDirectory() as folder:
            root = Path(folder)/'source'
            _source(root)
            path = root/'chapters/chapter_1.xml'
            chapter = decode(read_xml(path))
            chapter.flow_items.append(DisplayFormula(SourceAsset(1, 'formula', (1,80,90,90), content=['x+1'])))
            save_xml(encode(chapter), path)
            original = path.read_bytes()
            _add_translation(root, 'old-layer', 'EN')
            translated_path = root/'translations/old-layer/chapters/chapter_1.xml'
            translated = translated_path.read_bytes()
            manifest_path = root/'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['format_version'] = 7
            manifest_path.write_text(json.dumps(manifest))
            migrate_workspace(root)
            self.assertEqual(json.loads(manifest_path.read_text())['format_version'], FORMAT_VERSION)
            states = read_coverage(root/'translations/old-layer/coverage.xml').narrative
            self.assertEqual(states[('1', 'formula', f'body:{len(chapter.flow_items)-1}')], 'preserved')
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(translated_path.read_bytes(), translated)
            coverage = (root/'translations/old-layer/coverage.xml').read_bytes()
            migrate_workspace(root)
            self.assertEqual((root/'translations/old-layer/coverage.xml').read_bytes(), coverage)
