"""A real-PDF smoke for the PDF text-layer patch path.

Run manually with ``poetry run python test.py test_pdf_patch_smoke``.  The
review workflow additionally renders its generated output for visual review.
"""
# pylint: disable=no-member

import tempfile
import unittest
import unicodedata
from shutil import which
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import tostring

import pypdf

from pdf_craft.pipeline.pdf import (
    PDFPatcher, PDFReplacement, PatchTextOptions,
    QTextParagraphFiller,
)
from pdf_craft.pipeline.pdf.pipeline import PDFTranslationPipeline
from pdf_craft.extractor.chapter.chapter import BlockLayout, Chapter, ParagraphLayout, encode
from tests.extraction_helpers import make_extraction


_ASSET_ROOT = Path(__file__).parent / "assets" / "pdf"


class TestPDFPatchSmoke(unittest.TestCase):
    @unittest.skipUnless(which("gs"), "requires local Ghostscript")
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
            self.assertNotIn("source first line", first_page.extract_text())
            self.assertEqual(calls, ["source first line source second line"])
            # No page-wide raster image is introduced by patching: existing
            # source images are retained, and Qt's text overlay adds none.
            source_page: Any = pypdf.PdfReader(str(source)).pages[0]
            source_images = len(list(source_page.images))
            self.assertEqual(len(list(first_page.images)), source_images)

    @unittest.skipUnless(which("gs"), "requires local Ghostscript")
    def test_default_layout_fills_the_real_figure_caption_body_bbox_beyond_12pt(self):
        """The automatic first pass must not retain the former 12pt ceiling."""
        source = _ASSET_ROOT / "figure-caption.pdf"
        bbox = (603, 434, 4784, 2674)
        page_pixels = (5106, 7750)
        translated = (
            "The population was only 11.8%①. This was both a reflection of the backwardness "
            "of agriculture in the Jiangnan region and a cause of it. After the Warring States "
            "period, when the Yellow River basin achieved basic development due to the widespread "
            "use of iron tools and ox-drawn plows, and agricultural areas were connected into a "
            "large contiguous expanse, agricultural development in the south never broke through "
            "the pattern of scattered points or patchy distribution. Due to the vast land and "
            "sparse population, farming was quite extensive; many paddy fields were cultivated "
            "using the method of burning stubble and flooding with water, while dry fields were "
            "often farmed through slash-and-burn techniques②. Sima Qian wrote in the \"Records of "
            "the Grand Historian, Biographies of the Money-Makers\": \"In short, in the lands of "
            "Chu and Yue, the territory is vast and the people are few. They eat rice and fish, "
            "and sometimes use fire to clear fields and water to weed. Fruits, tubers, snails, "
            "and clams are abundant without needing to be traded; the land provides ample food, "
            "so there is no worry of famine. As a result, the people are lazy and idle, seeking "
            "only to get by, with no accumulation of wealth and much poverty.\" Although this "
            "summary may overemphasize the backwardness of the southern economy and has a certain "
            "one-sidedness, it largely reflects the actual situation. During the Warring States, "
            "Qin, and Han periods, the gap between the south and the Yellow River basin in "
            "agriculture clearly widened."
        )
        source_text = "中文来源文字"
        replacement = PDFReplacement(1, bbox, translated, page_pixels)
        page = pypdf.PdfReader(str(source)).pages[0]
        fitted = QTextParagraphFiller(PatchTextOptions()).fit(
            replacement,
            {1: (float(page.mediabox.width), float(page.mediabox.height))},
        )
        self.assertGreater(fitted.font_size, 12)
        # A source rectangle's bottom is an aim line rather than a hard edge:
        # the centred tight slot plan may straddle it slightly.  The actual
        # projected guard is still absolute (the page edge in this fixture).
        self.assertTrue(all(
            0 <= top
            and top + height <= (placement.forbidden_bottom or float(page.mediabox.height))
            for placement in fitted.placements
            for top, height in zip(placement.line_tops, placement.line_heights)
        ))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "figure-caption-translated.pdf"
            extraction_root = root / "figure-caption.pcex"
            extraction = make_extraction(
                extraction_root, page_pixel_sizes={1: page_pixels}, render_dpi=300,
            )
            chapter = Chapter(None, -1, [ParagraphLayout("text", 0, [
                BlockLayout(1, 0, bbox, [source_text]),
            ])])
            (extraction_root / "chapters/chapter_1.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + tostring(encode(chapter), encoding="unicode")
            )

            PDFTranslationPipeline().translate(
                source, target, extraction, lambda text: translated if text == source_text else text,
            )

            output_text = " ".join(
                pypdf.PdfReader(str(target)).pages[0].extract_text().split()
            )
            self.assertIn("The population was only 11.8%", output_text)

    @unittest.skipUnless(which("gs"), "requires local Ghostscript")
    def test_citation_fixture_replacement_is_rendered_and_searchable(self):
        """Keep a real citation-page replacement in the PDF text layer."""
        source = _ASSET_ROOT / "citation.pdf"
        page_pixels = (4662, 6827)
        source_lines = (
            "时他把弗洛伊德的俄狄浦斯情结重新表述为父性隐喻，其中父亲禁止孩子对",
            "母亲的欲望和母亲对孩子的欲望，并且确认母亲的缺失或欲望是与父亲相关",
        )
        translated = "Citation fixture replacement remains selectable and searchable PDF text."
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "citation-translated.pdf"
            extraction_root = root / "citation.pcex"
            extraction = make_extraction(
                extraction_root, page_pixel_sizes={1: page_pixels}, render_dpi=720,
            )
            chapter = Chapter(None, -1, [ParagraphLayout("text", 0, [
                BlockLayout(1, 1, (576, 904, 4184, 1022), [source_lines[0]]),
                BlockLayout(1, 2, (568, 1096, 4184, 1214), [source_lines[1]]),
            ])])
            (extraction_root / "chapters/chapter_1.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + tostring(encode(chapter), encoding="unicode")
            )

            PDFTranslationPipeline(patcher=PDFPatcher(
                options=PatchTextOptions(max_font_size=8, min_font_size=8),
            )).translate(source, target, extraction, lambda _text: translated)

            reader = pypdf.PdfReader(str(target))
            self.assertEqual(len(reader.pages), 3)
            output_text = unicodedata.normalize(
                "NFKC", " ".join(reader.pages[0].extract_text().split()),
            )
            self.assertIn(translated, output_text)
            self.assertNotIn(source_lines[0], output_text)
