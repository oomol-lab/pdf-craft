"""A real-PDF smoke for the PDF text-layer patch path.

Run manually with ``poetry run python test.py test_pdf_patch_smoke``.  The
review workflow additionally renders its generated output for visual review.
"""
# pylint: disable=no-member

import tempfile
import unittest
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import tostring

import pypdf

from pdf_craft.pipeline.pdf import (
    PDFPatcher, PatchTextOptions,
)
from pdf_craft.pipeline.pdf.pipeline import PDFTranslationPipeline
from pdf_craft.extractor.chapter.chapter import BlockLayout, Chapter, ParagraphLayout, encode
from tests.extraction_helpers import make_extraction


_ASSET_ROOT = Path(__file__).parent / "assets" / "pdf"


class TestPDFPatchSmoke(unittest.TestCase):
    def test_pipeline_fills_real_fixture_as_extractable_pdf_text(self):
        """Exercise ParagraphLayout extraction, erasure, filler, and output."""
        source = _ASSET_ROOT / "friendly.pdf"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = Path(temp_dir) / "friendly-translated.pdf"
            extraction_root = root / "friendly.pcex"
            extraction = make_extraction(
                extraction_root, page_pixel_sizes={1: (827, 1169)}, render_dpi=100,
            )
            # These 100-DPI blocks cover two real adjacent body lines on page one.
            chapter = Chapter(None, -1, [ParagraphLayout("text", 0, [
                BlockLayout(1, 1, (85, 270, 785, 303), ["source first line "]),
                BlockLayout(1, 2, (85, 309, 785, 342), ["source second line"]),
            ])])
            (extraction_root / "chapters/chapter_1.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + tostring(encode(chapter), encoding="unicode")
            )
            translated = (
                "Smoke replacement text intentionally spans two real source boxes so the first full "
                "line stops at the first rectangle and the next complete line begins in the second rectangle."
            )
            calls: list[str] = []

            def translate_paragraph(text: str) -> str:
                calls.append(text)
                return translated

            PDFTranslationPipeline(patcher=PDFPatcher(
                options=PatchTextOptions(max_font_size=10, min_font_size=8)
            )).translate(source, target, extraction, translate_paragraph)

            reader = pypdf.PdfReader(str(target))
            self.assertEqual(len(reader.pages), 7)
            first_page: Any = reader.pages[0]
            self.assertIn("Smoke", first_page.extract_text())
            self.assertEqual(calls, ["source first line source second line"])
            # No page-wide raster image is introduced by patching: existing
            # source images are retained, and Qt's text overlay adds none.
            source_page: Any = pypdf.PdfReader(str(source)).pages[0]
            source_images = len(list(source_page.images))
            self.assertEqual(len(list(first_page.images)), source_images)
