"""Detached image prose reaches final outputs; pixel text stays in the image."""
# pylint: disable=protected-access
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from zipfile import ZipFile

from PIL import Image, ImageDraw

from pdf_craft import AsyncPDFCraft, RenderMode
from pdf_craft.common import read_xml, save_xml
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.asset_text import image_caption, normalize_image_description
from pdf_craft.extractor.chapter.chapter import Chapter, InlineExpression, SourceAsset, StandaloneAsset, encode
from pdf_craft.extractor.chapter.jointer import Jointer
from pdf_craft.pdf import PageLayout
from pdf_craft.llm.runtime import LLMContext
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from tests.extraction_helpers import make_extraction
from tests.test_structured_translation import offline_translator
from unittest.mock import AsyncMock, patch
from typing import cast


class ImageDescriptionContractTests(TestCase):
    def test_actual_extraction_associates_prose_without_adding_body_text(self):
        layout = PageLayout(ref='image', det=(1, 1, 80, 40), text='Description', order=0, hash=None)
        items = list(Jointer([(1, [layout])]).execute())
        self.assertEqual(len(items), 1)
        asset = items[0]
        assert isinstance(asset, SourceAsset)
        self.assertEqual(asset.caption, ['Description'])
        self.assertEqual(asset.content, [])

    def test_new_descriptions_use_caption_without_changing_legacy_source(self):
        source = SourceAsset(1, 'image', (1, 1, 80, 40), content=['description'])
        saved = deepcopy(source)
        normalized = normalize_image_description(source)
        self.assertEqual(normalized.caption, ['description'])
        self.assertEqual(normalized.content, [])
        self.assertEqual(source, saved)
        self.assertEqual(image_caption(source), ['description'])
        self.assertEqual(image_caption(normalized), ['description'])

    def test_explicit_caption_wins_and_title_is_not_duplicated(self):
        source = SourceAsset(1, 'image', (1, 1, 80, 40),
                             title=['Title'], content=['Embedded OCR'], caption=['Caption'])
        self.assertEqual(image_caption(source), ['Caption'])
        self.assertEqual(normalize_image_description(source), source)
        source.caption, source.content = [], ['Title']
        self.assertEqual(image_caption(source), [])
        source.content = []
        self.assertEqual(image_caption(source), [])


class ImageDescriptionOutputTests(IsolatedAsyncioTestCase):
    async def test_nonvisible_image_content_does_not_enter_translation(self):
        asset = SourceAsset(1, 'image', (1, 1, 80, 40),
                            content=['Embedded OCR'], caption=['Figure'])
        translator = offline_translator()
        result, = await AnchoredContentXMLTransformer(translator).transform_assets(
            [AnchoredContent('head', 0, -1, asset, '')], error_policy=False)
        assert result is not None
        self.assertEqual(result.content, asset.content)
        self.assertEqual(result.caption, ['图注'])
        requests = [call.args[0] for call in cast(AsyncMock, translator._translate_text_async).await_args_list]
        self.assertFalse(any('Embedded OCR' in request for request in requests))

    async def test_legacy_description_roundtrips_and_selects_layers_in_markdown_and_epub(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = make_extraction(root / 'source')
            digest = 'a' * 64
            picture = Image.new('RGB', (120, 50), 'white')
            ImageDraw.Draw(picture).text((1, 1), 'PIXEL_ONLY', fill='black')
            picture.save(root / 'source/assets' / f'{digest}.png')
            formula = InlineExpression(ExpressionKind.INLINE_DOLLAR, 'x')
            description = ['Figure before ', formula, ' after']
            image = SourceAsset(1, 'image', (1, 1, 80, 40), content=description, asset_hash=digest)
            explicit = SourceAsset(1, 'image', (1, 45, 80, 85), title=['Heading'],
                                   content=['Embedded OCR'], caption=['Figure explicit'], asset_hash=digest)
            save_xml(encode(Chapter(None, -1, [StandaloneAsset(image), StandaloneAsset(explicit)])),
                     root / 'source/chapters/chapter_head.xml')
            source_xml = (root / 'source/chapters/chapter_head.xml').read_bytes()
            async def no_network(*_args, **_kwargs):
                raise AssertionError('Offline test attempted model access')
            craft = AsyncPDFCraft()
            with patch.object(LLMContext, 'request', no_network):
                target = await craft.translate_extraction(source, root / 'translated.pcex', offline_translator(),
                    translation_id='caption-main', target_language='zh-CN', error_policy=False)
            # Reopen persisted PCEX and select its actual replacement layer.
            target = await craft.open_extraction(root / 'translated.pcex')
            with target._materialize() as paths:
                self.assertEqual(paths.chapters.joinpath('chapter_head.xml').read_bytes(), source_xml)
                layer = read_xml(paths.translations / 'caption-main/chapters/chapter_head.xml')
                field = layer.find('.//asset/content')
                assert field is not None
                self.assertEqual(field.text, '图注 之前 ')
            for mode, original_count, translated_count in (
                    (RenderMode.SOURCE, 1, 0), (RenderMode.REPLACE, 0, 1),
                    (RenderMode.BILINGUAL, 1, 1), (RenderMode.BILINGUAL_TEXT, 1, 1)):
                with self.subTest(mode=mode):
                    md_path = root / f'{mode.value}.md'
                    await craft.render_markdown(target, md_path, mode=mode, translation_id=None if mode == RenderMode.SOURCE else 'caption-main')
                    markdown = md_path.read_text()
                    self.assertEqual(markdown.count('Figure before'), original_count)
                    self.assertEqual(markdown.count('图注 之前'), translated_count)
                    self.assertEqual(markdown.count('Figure explicit'), original_count)
                    self.assertEqual(markdown.count('图注 explicit'), translated_count)
                    self.assertNotIn('PIXEL_ONLY', markdown)
                    self.assertNotIn('Embedded OCR', markdown)
                    self.assertEqual(markdown.count('Heading'), original_count + translated_count)
                    self.assertEqual(markdown.count('$x$'), original_count + translated_count)
                    self.assertLess(markdown.index('![]('), markdown.index('before' if mode == RenderMode.SOURCE else '之前'))
                    epub_path = root / f'{mode.value}.epub'
                    await craft.render_epub(target, epub_path, mode=mode, translation_id=None if mode == RenderMode.SOURCE else 'caption-main', inline_latex=True)
                    with ZipFile(epub_path) as archive:
                        html = '\n'.join(archive.read(n).decode() for n in archive.namelist()
                                         if n.endswith('.xhtml'))
                    self.assertEqual(html.count('Figure before'), original_count)
                    self.assertEqual(html.count('图注 之前'), translated_count)
                    self.assertEqual(html.count('Figure explicit'), original_count)
                    self.assertEqual(html.count('图注 explicit'), translated_count)
                    self.assertNotIn('PIXEL_ONLY', html)
                    self.assertNotIn('Embedded OCR', html)
