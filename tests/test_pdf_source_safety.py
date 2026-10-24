"""Contradictory OCR geometry must not authorize destructive PDF erasures."""
# pylint: disable=protected-access,no-member,cell-var-from-loop
import asyncio
from pathlib import Path
import tempfile
import unittest

import pypdf
from PIL import Image
from reportlab.pdfgen import canvas

from pdf_craft import AsyncPDFCraft, FailureCategory
from pdf_craft.policies import ErrorCallbackError
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, FlowItem, SourceAsset, SourceTextFragment, StandaloneAsset, TextFlowItem, Reference, encode,
)
from pdf_craft.pipeline.pdf.source_safety import unsafe_source_geometry, paragraph_key
from pdf_craft.pipeline.pdf.models import PDFReplacement
from pdf_craft.transformer.translation_coverage import write_narrative_coverage, NarrativeCoverage
from tests.extraction_helpers import make_extraction


def page_stream(page):
    contents = page.get_contents()
    assert contents is not None
    return contents.get_data()


def paragraph(page, order, bbox, value='Text'):
    return TextFlowItem('body', -1, [SourceTextFragment(page, order, bbox, [value])])


class SourceGeometryTests(unittest.TestCase):
    def test_transparent_assets_and_footnotes_close_all_affected_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'chapters').mkdir()
            bad = paragraph(1, 0, (0, 0, 200, 300), '4')
            span = TextFlowItem('body', -1, [
                SourceTextFragment(1, 1, (10, 20, 80, 40), ['Before']),
                SourceAsset(2, 'table', (10, 20, 180, 180)),
                SourceTextFragment(3, 1, (10, 20, 80, 40), ['After']),
            ])
            note = Reference(1, 4, '*', [TextFlowItem('body', -1, [
                SourceTextFragment(3, 2, (10, 200, 80, 220), ['Note']),
                SourceTextFragment(4, 1, (10, 20, 80, 40), ['Continued note']),
            ])])
            save_xml(encode(Chapter(None, -1, [bad, span], [note])), root / 'chapters/chapter_head.xml')
            rejected, pages = unsafe_source_geometry(root / 'chapters', [], {paragraph_key(bad)},
                {n: (200, 300) for n in range(1, 5)}, 300, root / 'index.sqlite')
            self.assertEqual([(r.page_index, r.bbox, r.reading_order) for r in rejected], [(1, (0, 0, 200, 300), 0)])
            self.assertEqual(pages, {1, 2, 3, 4})

    def test_assets_inside_paragraph_and_untranslated_text_are_protected(self):
        for kind in ('image', 'table', 'text'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'chapters').mkdir()
                bad = paragraph(1, 0, (0, 0, 200, 300))
                if kind == 'text':
                    items: list[FlowItem] = [bad, paragraph(1, 1, (20, 40, 80, 60), 'Failed translation stays original')]
                else:
                    bad.children.append(SourceAsset(1, kind, (20, 40, 80, 100)))
                    items = [bad]
                save_xml(encode(Chapter(None, -1, items)), root / 'chapters/chapter_head.xml')
                rejected, pages = unsafe_source_geometry(root / 'chapters', [], {paragraph_key(bad)},
                    {1: (200, 300)}, 300, root / 'index.sqlite')
                self.assertEqual(pages, {1})
                self.assertEqual(len(rejected), 1)

    def test_normal_anchors_small_overlaps_and_untranslated_large_boxes_do_not_trigger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'chapters').mkdir()
            normal = TextFlowItem('body', -1, [
                SourceTextFragment(1, 0, (10, 10, 180, 40), ['Before']),
                SourceAsset(1, 'image', (10, 50, 180, 150)),
                SourceTextFragment(1, 1, (10, 160, 180, 190), ['After']),
            ])
            second = paragraph(1, 2, (10, 185, 180, 210))  # small OCR overlap
            save_xml(encode(Chapter(None, -1, [normal, second])), root / 'chapters/chapter_head.xml')
            rejected, pages = unsafe_source_geometry(root / 'chapters', [], {paragraph_key(normal), paragraph_key(second)},
                {1: (200, 300)}, 300, root / 'index.sqlite')
            self.assertEqual((rejected, pages), ((), set()))
            # A source-only giant bbox never gets erased: it is not an eligible writer.
            bad = paragraph(1, 3, (0, 0, 200, 300))
            save_xml(encode(Chapter(None, -1, [normal, second, bad])), root / 'chapters/chapter_head.xml')
            rejected, pages = unsafe_source_geometry(root / 'chapters', [], {paragraph_key(normal)},
                {1: (200, 300)}, 300, root / 'second.sqlite')
            self.assertEqual((rejected, pages), ((), set()))

    def test_furniture_cannot_erase_other_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'chapters').mkdir()
            save_xml(encode(Chapter(None, -1, [paragraph(1, 1, (20, 60, 180, 100))])),
                     root / 'chapters/chapter_head.xml')
            rejected, pages = unsafe_source_geometry(root / 'chapters',
                [PDFReplacement(1, (0, 0, 200, 300), '4', (200, 300), layout_ref='footer')], set(),
                {1: (200, 300)}, 300, root / 'index.sqlite')
            self.assertEqual(pages, {1})
            self.assertEqual(len(rejected), 1)


def fixture(root):
    extraction = make_extraction(root / 'source', page_pixel_sizes={1: (200, 300), 2: (200, 300)})
    bad = paragraph(1, 0, (0, 0, 200, 300), '45')
    untouched = paragraph(1, 1, (20, 200, 180, 240), 'Original body')
    good = paragraph(2, 0, (20, 20, 180, 60), 'Successful translation')
    image = SourceAsset(1, 'image', (20, 60, 180, 180), asset_hash='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
    Image.new('RGB', (160, 120), 'red').save(root / 'source/assets/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png')
    save_xml(encode(Chapter(None, -1, [bad, StandaloneAsset(image), untouched, good])),
             root / 'source/chapters/chapter_head.xml')
    write_narrative_coverage(root / 'source/translation.xml', [
        NarrativeCoverage('head', 1, 0, 'translated'),
        NarrativeCoverage('head', 2, 0, 'translated'),
    ])  # The body translation failed; it must remain on the source page.
    pdf = root / 'original.pdf'
    doc = canvas.Canvas(str(pdf), pagesize=(200, 300))
    doc.drawString(20, 280, '4')
    doc.drawImage(str(root / 'source/assets/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png'), 20, 120, 160, 120)
    doc.drawString(20, 70, 'Original body')
    doc.showPage()
    doc.drawString(20, 270, 'Original second page')
    doc.save()
    return extraction, pdf


class SourceSafetyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_retains_actual_original_page_and_skips_false_preview_but_translates_other_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction, source = fixture(root)
            failures, previews = [], []
            def accept(context):
                failures.append(context)
                return True
            await asyncio.wait_for(AsyncPDFCraft().patch_pdf_with_extraction(
                source, extraction, root / 'out.pdf', error_policy=accept, on_body=previews.append), 30)
            original, output = pypdf.PdfReader(source), pypdf.PdfReader(root / 'out.pdf')
            self.assertEqual(len(output.pages), 2)
            self.assertEqual(page_stream(output.pages[0]), page_stream(original.pages[0]))
            self.assertEqual(len(output.pages[0].images), 1)
            self.assertIn('Original body', output.pages[0].extract_text())
            self.assertIn('Successful translation', ' '.join(output.pages[1].extract_text().split()))
            self.assertEqual(len(previews), 2)
            self.assertEqual([r.page.page_index for p in previews for r in p.regions if r.lines], [2])
            self.assertFalse(any(p.text == '45' for p in previews))
            self.assertEqual(previews[0].text, 'Original body')
            self.assertTrue(all(not r.lines and r.font_size is None for r in previews[0].regions))
            context = next(f for f in failures if f.stage == 'source_geometry')
            self.assertEqual(context.category, FailureCategory.PDF_FILL)
            self.assertEqual(context.fallback, 'original_pdf_page')
            self.assertEqual(context.targets[0].regions[0].bbox, (0, 0, 200, 300))
            self.assertEqual(context.affected[0].page_indexes, (1,))

    async def test_strict_callback_failure_and_cancellation_propagate_without_replacing_target(self):
        for mode in ('strict', 'callback', 'cancel'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                extraction, source = fixture(root)
                target = root / 'out.pdf'
                target.write_bytes(b'old output')
                def decide(_context):
                    if mode == 'callback':
                        raise RuntimeError('user error')
                    if mode == 'cancel':
                        raise asyncio.CancelledError()
                    return False
                expected = asyncio.CancelledError if mode == 'cancel' else ErrorCallbackError if mode == 'callback' else ValueError
                with self.assertRaises(expected):
                    await AsyncPDFCraft().patch_pdf_with_extraction(source, extraction, target, error_policy=decide)
                self.assertEqual(target.read_bytes(), b'old output')

    async def test_all_primary_pages_preserved_is_failure_not_fake_success(self):
        from pdf_craft.error import NoUsableFillPagesError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction, source = fixture(root)
            first = paragraph(1, 0, (0, 0, 200, 300), '45')
            second = paragraph(2, 0, (0, 0, 200, 300), '6')
            save_xml(encode(Chapter(None, -1, [first, second,
                paragraph(1, 1, (20, 200, 180, 240)), paragraph(2, 1, (20, 200, 180, 240))])),
                root / 'source/chapters/chapter_head.xml')
            target = root / 'out.pdf'
            target.write_bytes(b'previous')
            with self.assertRaises(NoUsableFillPagesError):
                await asyncio.wait_for(AsyncPDFCraft().patch_pdf_with_extraction(
                    source, extraction, target, error_policy=True), 30)
            self.assertEqual(target.read_bytes(), b'previous')

    async def test_live_translation_and_repeated_cached_render_keep_geometry_guard(self):
        from unittest.mock import patch
        from tests.test_paragraph_content import translated, translator
        from pdf_craft import XMLTranslator
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction, source = fixture(root)
            failures, previews = [], []
            def accept(context):
                failures.append(context)
                return True
            with patch.object(XMLTranslator, '_translate_inline_segments_async', new=translated):
                await asyncio.wait_for(AsyncPDFCraft().translate_pdf(
                    source, extraction, root / 'live.pdf', translator=translator(),
                    error_policy=accept, on_body=previews.append), 30)
            self.assertTrue(any(f.stage == 'source_geometry' for f in failures))
            self.assertEqual([r.page.page_index for p in previews for r in p.regions if r.lines], [2])
            for number in range(2):
                output = root / f'cached-{number}.pdf'
                await asyncio.wait_for(AsyncPDFCraft().patch_pdf_with_extraction(
                    source, extraction, output, error_policy=True), 30)
                original = pypdf.PdfReader(source).pages[0]
                restored = pypdf.PdfReader(output).pages[0]
                self.assertEqual(page_stream(restored), page_stream(original))
                self.assertEqual(len(restored.images), 1)
