"""A real-PDF smoke for the PDF text-layer patch path.

Run manually with ``poetry run python test.py test_pdf_patch_smoke``.  The
review workflow additionally renders its generated output for visual review.
"""
# pylint: disable=no-member

import tempfile
import unittest
from pathlib import Path
from typing import Any

import pypdf

from pdf_craft.pipeline.pdf import (
    PDFPatcher, PDFReplacement, PDFReplacementRegion, PatchTextOptions,
)


_ASSET_ROOT = Path(__file__).parent / "assets" / "pdf"


class TestPDFPatchSmoke(unittest.TestCase):
    def test_fills_real_friendly_fixture_as_extractable_pdf_text(self):
        """Patch two real body-line boxes without OCR or a model download."""
        source = _ASSET_ROOT / "friendly.pdf"
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "friendly-translated.pdf"
            # These 100-DPI boxes cover the first two body lines on page one.
            regions = (
                PDFReplacementRegion(1, (85, 270, 785, 303), (827, 1169), reading_order=1),
                PDFReplacementRegion(1, (85, 309, 785, 342), (827, 1169), reading_order=2),
            )
            replacement = PDFReplacement(
                1,
                regions[0].bbox,
                "Smoke replacement text intentionally spans two real source boxes so the first full "
                "line stops at the first rectangle and the next complete line begins in the second rectangle.",
                regions[0].page_pixel_size,
                regions=regions,
            )

            PDFPatcher(options=PatchTextOptions(max_font_size=10, min_font_size=8)).patch(
                source, target, [replacement]
            )

            reader = pypdf.PdfReader(str(target))
            self.assertEqual(len(reader.pages), 7)
            first_page: Any = reader.pages[0]
            self.assertIn("Smoke", first_page.extract_text())
            # No page-wide raster image is introduced by patching: existing
            # source images are retained, and Qt's text overlay adds none.
            source_page: Any = pypdf.PdfReader(str(source)).pages[0]
            source_images = len(list(source_page.images))
            self.assertEqual(len(list(first_page.images)), source_images)
