"""The PDF consumer must submit only accepted translations to native layout."""
# pylint: disable=protected-access,unused-argument
from contextlib import asynccontextmanager
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pypdf
from PIL import Image
from reportlab.pdfgen import canvas

from pdf_craft import AsyncPDFCraft, XMLTranslator
from pdf_craft.common import save_xml
from pdf_craft.error import NoUsableFillPagesError
from pdf_craft.extractor.chapter.chapter import Chapter, Reference, encode
from pdf_craft.pipeline.pdf import PDFPatcher
from tests.extraction_helpers import make_extraction
from tests.test_paragraph_content import text, translated, translator
from tests.test_pdf_patcher import BlankVisualBaseCompiler


async def partial(runtime, inline_segments, **kwargs):
    result = []
    for segment in inline_segments:
        if 'Fail' in ''.join(part.text for part in segment):
            result.append(None)
        else:
            result.extend(await translated(runtime, [segment], **kwargs))
    return result


class PDFTranslationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        @asynccontextmanager
        async def local_policy(checker):
            yield checker

        async def compose(function, *args, **kwargs):
            return function(*args, **kwargs)

        handler = Mock()
        document = Mock()
        document.render_page.side_effect = lambda *_args: Image.new('RGB', (200, 200), 'white')
        handler.open.return_value = document
        self.patcher = PDFPatcher(font_size=8, visual_base_compiler=BlankVisualBaseCompiler(),
                                  _native_pdf_handler=handler)
        patches = [
            patch('pdf_craft.pipeline.pdf.content.PDFPatcher', return_value=self.patcher),
            patch('pdf_craft.pipeline.pdf.error_policy._process_error_policy_checker', local_policy),
            patch('pdf_craft.pipeline.pdf.patcher._process_error_policy_checker', local_policy),
            patch('pdf_craft.pipeline.pdf.patcher.QT_DOMAIN.run', side_effect=compose),
            patch.object(XMLTranslator, '_translate_inline_segments_async', new=partial),
        ]
        for replacement in patches:
            replacement.start()
            self.addCleanup(replacement.stop)

    @staticmethod
    def source(root, *, footnote=False, same_page=False):
        source = make_extraction(root / 'source', page_pixel_sizes={1: (200, 200), 2: (200, 200)})
        failed = text(1, 'Fail source.', 1)
        good = text(3, 'Good source.', 1 if same_page else 2)
        chapter = (Chapter(None, -1, [], [Reference(1, 1, '*', [failed, good])])
                   if footnote else Chapter(None, -1, [failed, good]))
        save_xml(encode(chapter), root / 'source/chapters/chapter_head.xml')
        pdf = root / 'source.pdf'
        document = canvas.Canvas(str(pdf), pagesize=(200, 200))
        document.drawString(10, 170, 'Fail source.')
        document.showPage()
        document.drawString(10, 130, 'Good source.')
        document.save()
        return source, pdf, failed, good

    async def test_missing_body_is_not_a_replacement_and_preview_has_no_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, pdf, failed, _ = self.source(root)
            values = []
            with patch.object(self.patcher._filler, 'fit', wraps=self.patcher._filler.fit) as fit:
                await AsyncPDFCraft(error_policy=True).translate_pdf(
                    pdf, source, root / 'output.pdf', translator=translator(), on_body=values.append,
                )
            self.assertTrue(fit.call_args_list)
            self.assertTrue(all('Fail' not in call.args[0].text for call in fit.call_args_list))
            self.assertTrue(all(region.page_index == 2 for call in fit.call_args_list
                                for region in call.args[0].obstacle_regions))
            self.assertEqual(len(values), 2)
            self.assertIn('Fail source.', values[0].text)
            self.assertIsNone(values[0].original)
            self.assertEqual(values[0].regions[0].bbox, failed.children[0].bbox)
            self.assertIsNone(values[0].regions[0].font_size)
            self.assertFalse(values[0].regions[0].lines)
            self.assertFalse(values[0].regions[0].erasures)
            self.assertTrue(values[1].regions[0].lines)
            page = pypdf.PdfReader(root / 'output.pdf').pages[0]
            self.assertIn('Fail source.', page.extract_text())  # pylint: disable=no-member

    async def test_missing_footnote_preserves_its_regions_as_layout_obstacles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, pdf, failed, _ = self.source(root, footnote=True, same_page=True)
            values = []
            with patch.object(self.patcher._filler, 'fit', wraps=self.patcher._filler.fit) as fit:
                await AsyncPDFCraft(error_policy=True).translate_pdf(
                    pdf, source, root / 'output.pdf', translator=translator(), on_footnote=values.append,
                )
            self.assertTrue(fit.call_args_list)
            for call in fit.call_args_list:
                replacement = call.args[0]
                self.assertNotIn('Fail', replacement.text)
                self.assertIn(failed.children[0].bbox, [r.bbox for r in replacement.obstacle_regions])
            self.assertEqual(len(values), 2)
            self.assertIsNone(values[0].original)
            self.assertFalse(values[0].regions[0].lines)
            self.assertFalse(values[0].regions[0].erasures)
            self.assertTrue(values[1].regions[0].lines)

    async def test_only_trusted_translation_fill_failure_cannot_be_masked_by_source(self):
        for footnote in (False, True):
            with self.subTest(footnote=footnote), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, pdf, _, _ = self.source(root, footnote=footnote)
                target = root / 'output.pdf'
                target.write_bytes(b'existing output')
                with patch.object(self.patcher, '_write_erasure_overlay', side_effect=RuntimeError('fill failed')), \
                        self.assertRaises(NoUsableFillPagesError) as caught:
                    await AsyncPDFCraft(error_policy=True).translate_pdf(
                        pdf, source, target, translator=translator(),
                    )
                self.assertEqual(caught.exception.failed_page_indexes, (2,))
                self.assertEqual(target.read_bytes(), b'existing output')
