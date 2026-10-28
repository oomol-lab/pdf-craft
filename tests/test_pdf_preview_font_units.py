"""Preview font sizes and frozen line geometry must share one pixel space."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pdf_craft.document.access import PageGeometry
from pdf_craft.document.paragraph import ParagraphContent, SourceRegion
from pdf_craft.pipeline.pdf import PDFReplacement, PatchTextOptions, QTextParagraphFiller
from pdf_craft.pipeline.pdf.feed import _PreviewReplacement
from pdf_craft.pipeline.pdf.preview import WindowPreview, lines_for_placement
from pdf_craft.pipeline.pdf.text_layout import _qt_modules


class TestPreviewFontUnits(unittest.TestCase):
    def test_failed_page_releases_its_preview_slot_without_content(self):
        page = PageGeometry(1, 200, 200, render_dpi=72)
        source = SourceRegion(page, 0, (0, 0, 200, 200))
        content = ParagraphContent("Translated", "Original", [source])
        replacement = _PreviewReplacement.attach(
            PDFReplacement(1, source.bbox, content.text, (200, 200)),
            content, False, 7,
        )
        window = SimpleNamespace(_summaries=[SimpleNamespace(replacement=replacement)])
        with tempfile.TemporaryDirectory() as directory:
            preview = WindowPreview(Path(directory), window, None, {1: (200, 200)})
            try:
                self.assertEqual(list(preview.contents({1})), [(7, False, None)])
            finally:
                preview.close()

    def test_size_and_baselines_use_qt_logical_pixels_then_page_geometry(self):
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=12, min_font_size=12))
        for pixels in (200, 400, 800):
            with self.subTest(pixels=pixels), tempfile.TemporaryDirectory() as directory:
                # Deliberately unrelated metadata: actual page dimensions own
                # geometry conversion, not a second render_dpi / 72 multiplier.
                page = PageGeometry(1, pixels, pixels, render_dpi=300)
                source = SourceRegion(page, 0, (0, 0, pixels, pixels))
                content = ParagraphContent("A short translated paragraph", "Original", [source])
                replacement = _PreviewReplacement.attach(
                    PDFReplacement(1, source.bbox, content.text, (pixels, pixels)),
                    content, False, 0,
                )
                fitted = filler.fit(replacement, {1: (200, 200)})
                placement = fitted.placements[0]
                frozen = lines_for_placement(filler, placement)
                _, qt_gui = _qt_modules()
                dpi = qt_gui.QFontMetricsF(qt_gui.QFont()).fontDpi()
                scale = pixels / 200
                window = SimpleNamespace(_summaries=[SimpleNamespace(replacement=replacement)])
                preview = WindowPreview(Path(directory), window, filler, {1: (200, 200)})
                try:
                    contribution = SimpleNamespace(
                        paragraph_index=0, regions=replacement.source_regions(),
                        placements=fitted.placements,
                    )
                    preview.page(1, [contribution], [])
                    _, _, exported = next(preview.contents(set()))
                    assert exported is not None
                    region = exported.regions[0]
                    self.assertAlmostEqual(region.font_size, placement.font_size * dpi / 72 * scale)
                    self.assertEqual(len(region.lines), len(frozen))
                    for actual, expected in zip(region.lines, frozen):
                        self.assertAlmostEqual(actual.x, expected.x * scale)
                        self.assertAlmostEqual(actual.baseline, expected.baseline * scale)
                finally:
                    preview.close()
