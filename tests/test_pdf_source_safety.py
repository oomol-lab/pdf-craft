"""Source overlap alone must not discard translations or connected pages."""
# pylint: disable=protected-access,no-member
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pypdf
from PIL import Image
from reportlab.pdfgen import canvas

from pdf_craft import AsyncPDFCraft, XMLTranslator
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, SourceAsset, SourceTextFragment, StandaloneAsset, TextFlowItem, encode,
)
from pdf_craft.transformer.translation_coverage import write_narrative_coverage, NarrativeCoverage
from tests.extraction_helpers import make_extraction
from tests.test_paragraph_content import translated, translator


def paragraph(page, order, bbox, value):
    return TextFlowItem('body', -1, [SourceTextFragment(page, order, bbox, [value])])


def fixture(root):
    extraction = make_extraction(root / 'source', page_pixel_sizes={1: (200, 300), 2: (200, 300)})
    large = paragraph(1, 0, (0, 0, 200, 300), 'Large box translation')
    untouched = paragraph(1, 1, (20, 200, 180, 240), 'Untranslated body')
    good = paragraph(2, 0, (20, 20, 180, 60), 'Second page translation')
    asset_hash = 'a' * 64
    image = SourceAsset(1, 'image', (20, 60, 180, 180), asset_hash=asset_hash)
    image_path = root / f'source/assets/{asset_hash}.png'
    Image.new('RGB', (160, 120), 'red').save(image_path)
    save_xml(encode(Chapter(None, -1, [large, StandaloneAsset(image), untouched, good])),
             root / 'source/chapters/chapter_head.xml')
    write_narrative_coverage(root / 'source/translation.xml', [
        NarrativeCoverage('head', 1, 0, 'translated'),
        NarrativeCoverage('head', 2, 0, 'translated'),
    ])
    pdf = root / 'original.pdf'
    doc = canvas.Canvas(str(pdf), pagesize=(200, 300))
    doc.drawString(20, 280, 'Original first page')
    doc.drawImage(str(image_path), 20, 120, 160, 120)
    doc.drawString(20, 70, 'Untranslated body')
    doc.showPage()
    doc.drawString(20, 270, 'Original second page')
    doc.save()
    return extraction, pdf


class SourceOverlapIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_furniture_box_does_not_skip_body_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / 'source', page_pixel_sizes={1: (200, 300)})
            body = paragraph(1, 0, (20, 200, 180, 240), 'Translated body')
            save_xml(encode(Chapter(None, -1, [body])), root / 'source/chapters/chapter_head.xml')
            (root / 'source/furnitures.xml').write_text(
                "<furnitures><patterns/><pages><page index='1'>"
                "<section det='0,0,200,300'>Translated furniture</section>"
                "</page></pages></furnitures>", encoding='utf-8')
            (root / 'source/translation.xml').write_text(
                "<translation><narrative>"
                "<paragraph chapter_id='head' page_index='1' order='0' state='translated'/>"
                "</narrative><furnitures><section page_index='1' det='0,0,200,300' state='translated'/>"
                "</furnitures></translation>", encoding='utf-8')
            source = root / 'original.pdf'
            doc = canvas.Canvas(str(source), pagesize=(200, 300))
            doc.drawString(20, 70, 'Original body')
            doc.save()
            previews = []
            await asyncio.wait_for(AsyncPDFCraft().patch_pdf_with_extraction(
                source, extraction, root / 'out.pdf', error_policy=False, on_body=previews.append), 30)
            content = ' '.join(pypdf.PdfReader(root / 'out.pdf').pages[0].extract_text().split())
            self.assertIn('Translated body', content)
            self.assertIn('Translated furniture', content)
            self.assertEqual(len(previews), 1)
            self.assertTrue(previews[0].regions[0].lines)

    async def test_cached_overlap_is_written_with_real_preview_under_strict_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction, source = fixture(root)
            for number in range(2):
                previews = []
                output = root / f'cached-{number}.pdf'
                await asyncio.wait_for(AsyncPDFCraft().patch_pdf_with_extraction(
                    source, extraction, output, error_policy=False, on_body=previews.append), 30)
                pages = pypdf.PdfReader(output).pages
                self.assertEqual(len(pages), 2)
                self.assertIn('Large box translation', ' '.join(pages[0].extract_text().split()))
                self.assertIn('Second page translation', ' '.join(pages[1].extract_text().split()))
                self.assertEqual(len(previews), 3)
                written = [p for p in previews if any(r.lines for r in p.regions)]
                self.assertEqual([p.text for p in written], ['Large box translation', 'Second page translation'])
                missing = next(p for p in previews if p.text == 'Untranslated body')
                self.assertTrue(all(not r.lines and r.font_size is None for r in missing.regions))

    async def test_live_overlap_is_not_reported_as_geometry_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction, source = fixture(root)
            failures, previews = [], []

            def reject(context):
                failures.append(context)
                return False

            with patch.object(XMLTranslator, '_translate_inline_segments_async', new=translated):
                await asyncio.wait_for(AsyncPDFCraft().translate_pdf(
                    source, extraction, root / 'live.pdf', translator=translator(),
                    error_policy=reject, on_body=previews.append), 30)
            self.assertEqual(failures, [])
            self.assertEqual(len(previews), 3)
            self.assertTrue(all(any(r.lines for r in p.regions) for p in previews))
            self.assertIn('Large box translation', ' '.join(
                pypdf.PdfReader(root / 'live.pdf').pages[0].extract_text().split()))

    async def test_overlap_does_not_discard_connected_cross_page_paragraphs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / 'source',
                page_pixel_sizes={n: (200, 300) for n in range(1, 4)})
            large = paragraph(1, 0, (0, 0, 200, 300), 'Large translated box')
            first = TextFlowItem('body', -1, [
                SourceTextFragment(1, 1, (20, 200, 180, 240), ['First translated paragraph']),
                SourceTextFragment(2, 0, (20, 20, 180, 60), [' continued.']),
            ])
            second = TextFlowItem('body', -1, [
                SourceTextFragment(2, 1, (20, 200, 180, 240), ['Second translated paragraph']),
                SourceTextFragment(3, 0, (20, 20, 180, 60), [' continued.']),
            ])
            save_xml(encode(Chapter(None, -1, [large, first, second])),
                     root / 'source/chapters/chapter_head.xml')
            write_narrative_coverage(root / 'source/translation.xml', [
                NarrativeCoverage('head', 1, 0, 'translated'),
                NarrativeCoverage('head', 1, 1, 'translated'),
                NarrativeCoverage('head', 2, 1, 'translated'),
            ])
            source = root / 'original.pdf'
            doc = canvas.Canvas(str(source), pagesize=(200, 300))
            for _ in range(3):
                doc.drawString(20, 270, 'Original page')
                doc.showPage()
            doc.save()
            previews = []
            await asyncio.wait_for(AsyncPDFCraft().patch_pdf_with_extraction(
                source, extraction, root / 'out.pdf', error_policy=False, on_body=previews.append), 30)
            pages = pypdf.PdfReader(root / 'out.pdf').pages
            self.assertEqual(len(pages), 3)
            self.assertEqual(len(previews), 3)
            self.assertEqual({r.page.page_index for p in previews for r in p.regions}, {1, 2, 3})
            self.assertTrue(all(any(r.lines for r in p.regions) for p in previews))
            self.assertIn('Large translated box', ' '.join(pages[0].extract_text().split()))
            for preview in previews[1:]:
                placed = ''.join(line.text for region in preview.regions for line in region.lines)
                self.assertEqual(''.join(placed.split()), ''.join(preview.text.split()))
