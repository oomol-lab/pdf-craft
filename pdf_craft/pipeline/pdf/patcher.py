"""Compose source PDF pages with independent erasure and text overlays."""

from collections.abc import Iterable
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from pdf_craft.pdf.handler import DefaultPDFHandler, PDFHandler

from .eraser import EraseOptions, EraseRectangle, RectangularEraser
from .models import PDFReplacement, PDFReplacementRegion, PDFSkippedReplacement
from .text_layout import (
    PatchTextOptions, QTextParagraphFiller, WindowedParagraphPlanner,
)


class PDFPatcher:
    """Replace OCR-backed paragraph regions without flattening source pages.

    The source page remains the base PDF page. The eraser owns a locally
    sampled background-color rectangle overlay; the filler owns a separate
    Qt-generated PDF text overlay. Keeping those layers separate makes a
    future image-aware eraser a local replacement rather than a typography
    rewrite.
    """

    def __init__(
        self,
        font_name: str | None = None,
        font_size: float | None = None,
        options: PatchTextOptions | None = None,
        pdf_handler: PDFHandler | None = None,
        dpi: int = 300,
        erase_options: EraseOptions | None = None,
    ) -> None:
        """Create a patcher.

        ``pdf_handler`` renders source pages only to sample local background
        colors for erasure. The source PDF page remains the output base and is
        never replaced with that raster image.
        """
        if options is not None and (font_name is not None or font_size is not None):
            raise ValueError("pass either options or legacy font_name/font_size arguments")
        if options is None:
            defaults = PatchTextOptions()
            options = PatchTextOptions(
                font_name=font_name if font_name is not None else defaults.font_name,
                max_font_size=font_size if font_size is not None else defaults.max_font_size,
                min_font_size=min(
                    defaults.min_font_size,
                    font_size if font_size is not None else defaults.min_font_size,
                ),
            )
        self.options = options
        self._eraser = RectangularEraser(erase_options)
        self._filler = QTextParagraphFiller(options)
        self._pdf_handler = pdf_handler or DefaultPDFHandler()
        self.dpi = dpi
        self.skipped_replacements: tuple[PDFSkippedReplacement, ...] = ()

    def patch(self, source_path: Path, target_path: Path, replacements: Iterable[PDFReplacement]) -> None:
        """Compose source pages, rectangular erasure, then Qt PDF text layers."""
        try:
            import pypdf
            from reportlab.pdfgen import canvas
        except ImportError as error:  # pragma: no cover - declared dependencies.
            raise RuntimeError("PDF patching requires pypdf and reportlab") from error

        reader = pypdf.PdfReader(str(source_path))
        page_sizes = {
            index: (float(page.mediabox.width), float(page.mediabox.height))
            for index, page in enumerate(reader.pages, 1)
        }
        def validated_replacements():
            for replacement in replacements:
                self.validate(replacement, pages_count=len(reader.pages))
                yield replacement

        planner = WindowedParagraphPlanner(self._filler, page_sizes, self.options)
        writer = pypdf.PdfWriter()
        next_page_index = 1
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for window in planner.plan(validated_replacements()):
                for index in range(next_page_index, window.first_page_index):
                    writer.add_page(reader.pages[index - 1])
                self._compose_window(
                    pypdf, canvas, source_path, reader, writer, root, page_sizes, window,
                )
                next_page_index = window.last_page_index + 1
            for index in range(next_page_index, len(reader.pages) + 1):
                writer.add_page(reader.pages[index - 1])

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target_path.parent, suffix=".pdf", delete=False) as output:
            temporary_path = Path(output.name)
            writer.write(output)
        temporary_path.replace(target_path)
        self.skipped_replacements = tuple(
            PDFSkippedReplacement(replacement.page_index, replacement.bbox, str(reason))
            for replacement, reason in planner.skipped
        )

    def _compose_window(
        self, pypdf, canvas, source_path: Path, reader, writer, root: Path,
        page_sizes: dict[int, tuple[float, float]], window,
    ) -> None:
        """Compose one window while loading one serialized page plan at a time."""
        document = None
        try:
            if window.has_page_contributions:
                document = self._pdf_handler.open(source_path)
            for index in range(window.first_page_index, window.last_page_index + 1):
                page = reader.pages[index - 1]
                page_width, page_height = page_sizes[index]
                contributions = tuple(window.page_contributions(index))
                page_regions = tuple(
                    region
                    for contribution in contributions
                    for region in contribution.regions
                )
                page_erasures = self._plan_page_erasures(
                    document, index, page_regions, page_sizes,
                )
                if page_erasures:
                    erasure_path = root / f"erase-{index}.pdf"
                    self._write_erasure_overlay(canvas, erasure_path, page_width, page_height, page_erasures)
                    page.merge_page(pypdf.PdfReader(str(erasure_path)).pages[0])
                page_placements = tuple(
                    placement
                    for contribution in contributions
                    for placement in contribution.placements
                )
                if page_placements:
                    text_path = root / f"text-{index}.pdf"
                    self._filler.draw_pdf_overlay(text_path, (page_width, page_height), page_placements)
                    page.merge_page(pypdf.PdfReader(str(text_path)).pages[0])
                writer.add_page(page)
        finally:
            if document is not None:
                document.close()
            window.close()

    def _plan_page_erasures(
        self,
        document,
        page_index: int,
        regions: Iterable[PDFReplacementRegion],
        page_sizes: dict[int, tuple[float, float]],
    ) -> tuple[EraseRectangle, ...]:
        """Sample one source raster, plan that page's masks, then release it."""
        page_regions = tuple(regions)
        if not page_regions:
            return ()
        if document is None:  # Defensive: non-empty regions always opened it.
            raise RuntimeError("source document is required for erasure")
        dpi = page_regions[0].dpi
        if any(region.dpi != dpi for region in page_regions[1:]):
            raise ValueError(f"page {page_index} has incompatible erasure render DPI values")
        page_image = document.render_page(page_index, dpi)
        try:
            return self._eraser.plan(page_regions, page_sizes, {page_index: page_image})
        finally:
            page_image.close()

    def validate(self, replacement: PDFReplacement, pages_count: int | None = None) -> None:
        if not replacement.text.strip():
            raise ValueError("replacement text must not be empty")
        regions = replacement.source_regions()
        if replacement.regions and len(regions) == 1:
            self._validate_single_region_matches_replacement(replacement, regions[0])
        for region in regions:
            self._validate_region(region, pages_count)

    @staticmethod
    def _validate_single_region_matches_replacement(
        replacement: PDFReplacement, region: PDFReplacementRegion,
    ) -> None:
        if (
            replacement.page_index != region.page_index
            or replacement.bbox != region.bbox
            or replacement.page_pixel_size != region.page_pixel_size
            or replacement.dpi != region.dpi
            or replacement.reading_order != region.reading_order
        ):
            raise ValueError("replacement geometry must match its only source region")

    @staticmethod
    def _validate_region(region: PDFReplacementRegion, pages_count: int | None = None) -> None:
        left, top, right, bottom = region.bbox
        if region.page_index < 1:
            raise ValueError("page_index must be positive")
        if pages_count is not None and region.page_index > pages_count:
            raise ValueError(f"page_index {region.page_index} exceeds source page count {pages_count}")
        if left < 0 or top < 0 or right <= left or bottom <= top:
            raise ValueError(f"invalid bbox: {region.bbox}")
        if region.page_pixel_size[0] <= 0 or region.page_pixel_size[1] <= 0:
            raise ValueError("page_pixel_size must be positive")
        if right > region.page_pixel_size[0] or bottom > region.page_pixel_size[1]:
            raise ValueError("bbox exceeds page_pixel_size")

    def _write_erasure_overlay(
        self, canvas, output_path: Path, width: float, height: float,
        erasures: tuple[EraseRectangle, ...],
    ) -> None:
        overlay = canvas.Canvas(str(output_path), pagesize=(width, height))
        self._eraser.draw(overlay, erasures, height)
        overlay.save()
