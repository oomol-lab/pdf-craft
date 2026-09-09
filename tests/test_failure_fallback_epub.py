"""End-to-end coverage for recoverable and fatal PDF/OCR page failures."""

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping, cast
import unittest
import zipfile

from PIL import Image

from pdf_craft import (
    ExtractionOptions,
    NoUsableOCRPagesError,
    OCRBillingError,
    OCRError,
    PDFCraft,
)
from pdf_craft.common import AssetHub
from pdf_craft.extractor.chapter import AssetLayout, create_chapters_reader
from pdf_craft.ocr_config import DeepSeekOCRLocalConfig
from pdf_craft.pdf import PDFDocumentMetadata
from pdf_craft.pdf.page_extractor import PageExtractorNode
from pdf_craft.pdf.types import Page, PageLayout
from pdf_craft.transform import PDFExtractionEngine


class _ScriptedDocument:
    def __init__(self, pages: Mapping[int, Image.Image | Exception]) -> None:
        self._pages = pages

    @property
    def pages_count(self) -> int:
        return len(self._pages)

    def metadata(self) -> PDFDocumentMetadata:
        return PDFDocumentMetadata(
            title="Failure coverage", description=None, publisher=None, isbn=None,
            authors=[], editors=[], translators=[], modified=datetime.now(timezone.utc),
        )

    def page_size(self, page_index: int) -> tuple[float, float]:
        page = self._pages[page_index]
        if isinstance(page, Exception):
            raise page
        return (page.width / 72, page.height / 72)

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        del dpi
        page = self._pages[page_index]
        if isinstance(page, Exception):
            raise page
        return page.copy()

    def close(self) -> None:
        pass


class _ScriptedHandler:
    def __init__(self, document: _ScriptedDocument) -> None:
        self.document = document

    def open(self, pdf_path: Path) -> _ScriptedDocument:
        del pdf_path
        return self.document


class _ScriptedRecognizer:
    def __init__(self, outcomes: Mapping[int, str | Exception]) -> None:
        self._outcomes = outcomes

    def image2page(self, image, page_index: int, **_kwargs) -> Page:
        outcome = self._outcomes[page_index]
        if isinstance(outcome, Exception):
            raise outcome
        return Page(
            index=page_index,
            image=None,
            body_layouts=[PageLayout(
                ref="text", det=(0, 0, image.width, image.height), text=outcome,
                order=0, hash=None,
            )],
            footnotes_layouts=[],
            input_tokens=0,
            output_tokens=0,
        )


class _ProviderPaymentRequiredError(Exception):
    status_code = 402


class _PaymentRequiredResults:
    def __iter__(self):
        return self

    def __next__(self):
        raise _ProviderPaymentRequiredError("payment required")


class _PaymentRequiredExtractor:
    def extract_page_results(self, **_kwargs):
        return _PaymentRequiredResults()


class TestFailureFallbackEPUB(unittest.TestCase):
    def test_pdf_render_failure_on_every_page_stops_without_an_epub(self):
        craft = self._craft(
            {1: RuntimeError("broken page one"), 2: RuntimeError("broken page two")},
            {1: "unused", 2: "unused"},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "book.epub"
            with self.assertRaises(NoUsableOCRPagesError) as raised:
                self._convert(craft, target, root / "analysis", ignore_pdf_errors=True)
            self.assertEqual(raised.exception.failed_page_indexes, (1, 2))
            self.assertFalse(target.exists())

    def test_pdf_render_failure_on_one_page_keeps_the_textual_fallback(self):
        craft = self._craft(
            {1: RuntimeError("broken page"), 2: self._page((20, 30, 40))},
            {1: "unused", 2: "usable second page"},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "book.epub"
            events = self._convert(craft, target, root / "analysis", ignore_pdf_errors=True)
            self.assertTrue(target.is_file())
            self.assertEqual(self._failed_pages(events), [1])
            self.assertIn("Page 1 extraction failed due to PDF rendering error", self._epub_text(target))

    def test_ocr_failure_on_every_page_stops_without_an_epub(self):
        craft = self._craft(
            {1: self._page((200, 10, 10)), 2: self._page((10, 200, 10))},
            {1: OCRError("page one failed", 1, 1), 2: OCRError("page two failed", 2, 1)},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "book.epub"
            with self.assertRaises(NoUsableOCRPagesError) as raised:
                self._convert(craft, target, root / "analysis", ignore_ocr_errors=True)
            self.assertEqual(raised.exception.failed_page_indexes, (1, 2))
            self.assertFalse(target.exists())

    def test_ocr_failure_on_one_page_embeds_its_original_raster_in_epub(self):
        failed_page = self._page((200, 10, 10))
        craft = self._craft(
            {1: failed_page, 2: self._page((10, 200, 10))},
            {1: OCRError("page one failed", 1, 1), 2: "usable second page"},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "book.epub"
            events = self._convert(craft, target, root / "analysis", ignore_ocr_errors=True)
            self.assertTrue(target.is_file())
            self.assertEqual(self._failed_pages(events), [1])

            assets = self._chapter_assets(root / "analysis" / "extraction")
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0].page_index, 1)
            self.assertEqual(assets[0].ref, "image")
            self.assertIsNotNone(assets[0].hash)
            self.assertIn(
                (failed_page.size, failed_page.getpixel((0, 0))),
                self._epub_pngs(target),
            )

    def test_pages_without_failures_produce_a_normal_epub(self):
        craft = self._craft(
            {1: self._page((10, 10, 200)), 2: self._page((10, 200, 10))},
            {1: "first page", 2: "second page"},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "book.epub"
            events = self._convert(craft, target, root / "analysis", ignore_ocr_errors=True)
            self.assertTrue(target.is_file())
            self.assertEqual(self._failed_pages(events), [])
            self.assertEqual(self._chapter_assets(root / "analysis" / "extraction"), [])
            self.assertIn("first page", self._epub_text(target))

    def test_payment_required_is_fatal_even_when_ocr_errors_are_ignored(self):
        craft = self._craft(
            {1: self._page((10, 10, 200)), 2: self._page((10, 200, 10))},
            {1: OCRBillingError(1), 2: "unused"},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "book.epub"
            with self.assertRaises(OCRBillingError):
                self._convert(craft, target, root / "analysis", ignore_ocr_errors=True)
            self.assertFalse(target.exists())
            self.assertFalse((root / "analysis" / "ocr" / "page_1.xml").exists())

    def test_provider_http_402_is_classified_as_a_fatal_billing_error(self):
        node = PageExtractorNode(DeepSeekOCRLocalConfig(local_only=True))
        node._page_extractor = cast(Any, _PaymentRequiredExtractor())  # pylint: disable=protected-access
        with TemporaryDirectory() as directory, self.assertRaises(OCRBillingError):
            node.image2page(
                image=self._page((10, 10, 200)), page_index=1,
                asset_hub=AssetHub(Path(directory) / "assets"), ocr_size="tiny",
                includes_footnotes=False, includes_raw_image=False, plot_path=None,
                max_tokens=None, max_output_tokens=None, device_number=None,
                aborted=lambda: False,
            )

    @staticmethod
    def _page(color: tuple[int, int, int]) -> Image.Image:
        return Image.new("RGB", (17, 13), color)

    @staticmethod
    def _craft(
        pages: Mapping[int, Image.Image | Exception], outcomes: Mapping[int, str | Exception],
    ) -> PDFCraft:
        engine = PDFExtractionEngine(
            pdf_handler=_ScriptedHandler(_ScriptedDocument(pages)),
            ocr=DeepSeekOCRLocalConfig(local_only=True),
        )
        engine._ocr._extractor = cast(Any, _ScriptedRecognizer(outcomes))  # pylint: disable=protected-access
        return PDFCraft.from_engine(engine)

    @staticmethod
    def _convert(
        craft: PDFCraft, target: Path, analysis: Path, *,
        ignore_pdf_errors: bool = False, ignore_ocr_errors: bool = False,
    ):
        events = []
        craft.convert_pdf_to_epub(
            "scripted.pdf", target, analysing_path=analysis,
            extraction=ExtractionOptions(
                ignore_pdf_errors=ignore_pdf_errors,
                ignore_ocr_errors=ignore_ocr_errors,
                on_ocr_event=events.append,
            ),
        )
        return events

    @staticmethod
    def _failed_pages(events) -> list[int]:
        return [event.page_index for event in events if event.kind.name == "FAILED"]

    @staticmethod
    def _chapter_assets(extraction_path: Path) -> list[AssetLayout]:
        assets: list[AssetLayout] = []
        for chapter in create_chapters_reader(extraction_path / "chapters")():
            assets.extend(layout for layout in chapter.layouts if isinstance(layout, AssetLayout))
        return assets

    @staticmethod
    def _epub_text(epub: Path) -> str:
        with zipfile.ZipFile(epub) as archive:
            return "\n".join(
                archive.read(name).decode("utf-8")
                for name in archive.namelist()
                if name.endswith((".xhtml", ".html"))
            )

    @staticmethod
    def _epub_pngs(epub: Path) -> list[tuple[tuple[int, int], tuple[int, int, int]]]:
        pngs = []
        with zipfile.ZipFile(epub) as archive:
            for name in archive.namelist():
                if name.endswith(".png"):
                    with Image.open(BytesIO(archive.read(name))) as image:
                        pngs.append((image.size, image.convert("RGB").getpixel((0, 0))))
        return pngs


if __name__ == "__main__":
    unittest.main()
