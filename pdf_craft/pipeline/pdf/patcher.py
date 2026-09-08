"""Compose source PDF pages with independent erasure and text overlays."""

from collections.abc import Iterable
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from pdf_craft.pdf.handler import PDFHandler

from .eraser import EraseRectangle, RectangularEraser
from .models import PDFReplacement, PDFReplacementRegion, PDFSkippedReplacement
from .text_layout import FittedParagraph, PatchTextOptions, QTextParagraphFiller


class PDFPatcher:
    """Replace OCR-backed paragraph regions without flattening source pages.

    The source page remains the base PDF page. The eraser owns a visual white
    rectangle overlay for now; the filler owns a separate Qt-generated PDF text
    overlay. Keeping those layers separate makes a future image-aware eraser a
    local replacement rather than a typography rewrite.
    """

    def __init__(
        self,
        font_name: str | None = None,
        font_size: float | None = None,
        options: PatchTextOptions | None = None,
        pdf_handler: PDFHandler | None = None,
        dpi: int = 300,
    ) -> None:
        """Create a patcher.

        ``pdf_handler`` remains accepted for source compatibility but is no
        longer used: patching must not render source pages to a bitmap.
        """
        del pdf_handler
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
        self._eraser = RectangularEraser()
        self._filler = QTextParagraphFiller(options)
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
        replacement_list = list(replacements)
        for replacement in replacement_list:
            self.validate(replacement, pages_count=len(reader.pages))

        page_sizes = {
            index: (float(page.mediabox.width), float(page.mediabox.height))
            for index, page in enumerate(reader.pages, 1)
        }
        fitted, skipped = self._preflight(replacement_list, page_sizes)
        erasures = self._eraser.plan(
            (region for replacement, _ in fitted for region in replacement.source_regions()),
            page_sizes,
        )

        erasures_by_page = self._group_erasures(erasures)
        text_by_page = self._group_text_placements(fitted)
        writer = pypdf.PdfWriter()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index, page in enumerate(reader.pages, 1):
                page_width, page_height = page_sizes[index]
                page_erasures = erasures_by_page.get(index, ())
                if page_erasures:
                    erasure_path = root / f"erase-{index}.pdf"
                    self._write_erasure_overlay(canvas, erasure_path, page_width, page_height, page_erasures)
                    page.merge_page(pypdf.PdfReader(str(erasure_path)).pages[0])
                page_placements = text_by_page.get(index, ())
                if page_placements:
                    text_path = root / f"text-{index}.pdf"
                    self._filler.draw_pdf_overlay(
                        text_path, (page_width, page_height), page_placements,
                    )
                    page.merge_page(pypdf.PdfReader(str(text_path)).pages[0])
                writer.add_page(page)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target_path.parent, suffix=".pdf", delete=False) as output:
            temporary_path = Path(output.name)
            writer.write(output)
        temporary_path.replace(target_path)
        self.skipped_replacements = tuple(skipped)

    def validate(self, replacement: PDFReplacement, pages_count: int | None = None) -> None:
        if not replacement.text.strip():
            raise ValueError("replacement text must not be empty")
        regions = replacement.source_regions()
        if replacement.regions and len(regions) == 1:
            self._validate_single_region_matches_replacement(replacement, regions[0])
        for region in regions:
            self._validate_region(region, pages_count)

    def _preflight(
        self,
        replacements: Iterable[PDFReplacement],
        page_sizes: dict[int, tuple[float, float]],
    ) -> tuple[tuple[tuple[PDFReplacement, FittedParagraph], ...], list[PDFSkippedReplacement]]:
        fitted: list[tuple[PDFReplacement, FittedParagraph]] = []
        skipped: list[PDFSkippedReplacement] = []
        for replacement in replacements:
            try:
                fitted.append((replacement, self._filler.fit(replacement, page_sizes)))
            except ValueError as error:
                if self.options.overflow == "skip":
                    skipped.append(PDFSkippedReplacement(
                        replacement.page_index, replacement.bbox, str(error),
                    ))
                    continue
                raise ValueError(
                    f"page {replacement.page_index}, bbox {replacement.bbox}: {error}"
                ) from error
        return tuple(fitted), skipped

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

    @staticmethod
    def _group_erasures(erasures: Iterable[EraseRectangle]) -> dict[int, tuple[EraseRectangle, ...]]:
        result: dict[int, list[EraseRectangle]] = {}
        for erase in erasures:
            result.setdefault(erase.page_index, []).append(erase)
        return {page_index: tuple(items) for page_index, items in result.items()}

    @staticmethod
    def _group_text_placements(fitted: Iterable[tuple[PDFReplacement, FittedParagraph]]):
        result = {}
        for _, paragraph in fitted:
            for placement in paragraph.placements:
                result.setdefault(placement.page_index, []).append(placement)
        return {page_index: tuple(items) for page_index, items in result.items()}
