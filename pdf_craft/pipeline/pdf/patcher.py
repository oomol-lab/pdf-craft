from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Iterable

from pdf_craft.pdf.handler import DefaultPDFHandler, PDFHandler

from .text_layout import BoxTextLayout, PatchTextOptions


@dataclass(frozen=True)
class PDFReplacementRegion:
    """One OCR source region belonging to a logical replacement."""

    page_index: int
    bbox: tuple[int, int, int, int]
    page_pixel_size: tuple[int, int]
    dpi: int = 300
    reading_order: int = 0


@dataclass(frozen=True)
class PDFReplacement:
    """Replacement text and the ordered OCR regions it replaces.

    The legacy top-level geometry identifies the first region, so callers that
    replace a single box keep their existing API.  ``regions`` carries the
    complete paragraph geometry for the PDF translation pipeline.  The
    current box patcher deliberately only renders one-region replacements;
    multi-region flow belongs to the paragraph filler.
    """

    page_index: int
    bbox: tuple[int, int, int, int]
    text: str
    page_pixel_size: tuple[int, int]
    dpi: int = 300
    reading_order: int = 0
    regions: tuple[PDFReplacementRegion, ...] = ()


@dataclass(frozen=True)
class PDFSkippedReplacement:
    """An explicitly skipped overflow, retained for callers to inspect."""

    page_index: int
    bbox: tuple[int, int, int, int]
    reason: str


class PDFPatcher:
    """Replace OCR-backed text regions in a PDF with translated text.

    The implementation uses pypdf for page preservation and reportlab for a
    transparent overlay. Both imports are lazy so normal OCR/Markdown users do
    not need the patching stack at import time.
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

        ``font_name`` and ``font_size`` remain accepted for compatibility. A
        supplied font size is the maximum fitted size, not a forced size.
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
        self._layout = BoxTextLayout(options)
        self._pdf_handler = pdf_handler or DefaultPDFHandler()
        self.dpi = dpi
        self.skipped_replacements: tuple[PDFSkippedReplacement, ...] = ()

    def patch(self, source_path: Path, target_path: Path, replacements: Iterable[PDFReplacement]) -> None:
        try:
            import pypdf
            from reportlab.lib.utils import ImageReader
            from reportlab.pdfgen import canvas
        except ImportError as error:
            raise RuntimeError("PDF patching requires the optional 'reportlab' dependency") from error

        reader = pypdf.PdfReader(str(source_path))
        skipped: list[PDFSkippedReplacement] = []
        replacements_by_page: dict[int, list[PDFReplacement]] = {}
        for replacement in replacements:
            self.validate(replacement, pages_count=len(reader.pages))
            replacements_by_page.setdefault(replacement.page_index, []).append(replacement)
        for page_replacements in replacements_by_page.values():
            page_replacements.sort(key=lambda replacement: replacement.reading_order)

        # Preflight all pages before drawing any white rectangles or creating a
        # target file. A failed fit must not masquerade as a successful patch.
        layouts: dict[int, list[tuple[PDFReplacement, object]]] = {}
        for index, page in enumerate(reader.pages, 1):
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            for replacement in replacements_by_page.get(index, []):
                try:
                    fitted = self._fit_replacement(replacement, width, height)
                except ValueError as error:
                    if self.options.overflow == "skip":
                        skipped.append(PDFSkippedReplacement(index, replacement.bbox, str(error)))
                        continue
                    raise ValueError(
                        f"page {index}, bbox {replacement.bbox}: {error}"
                    ) from error
                layouts.setdefault(index, []).append((replacement, fitted))

        writer = pypdf.PdfWriter()
        document = self._pdf_handler.open(source_path)
        try:
            for index, page in enumerate(reader.pages, 1):
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)
                with TemporaryDirectory() as temp_dir:
                    overlay_path = Path(temp_dir) / "overlay.pdf"
                    overlay = canvas.Canvas(str(overlay_path), pagesize=(width, height))
                    page_layouts = layouts.get(index, [])
                    render_dpi = page_layouts[0][0].dpi if page_layouts else self.dpi
                    raw_page = document.render_page(index, render_dpi)
                    overlay.drawImage(ImageReader(raw_page), 0, 0, width=width, height=height)
                    for replacement, _ in page_layouts:
                        self._draw_background(overlay, replacement, width, height)
                    for replacement, fitted in page_layouts:
                        self._draw_text(overlay, replacement, fitted, width, height)
                    overlay.save()
                    overlay_reader = pypdf.PdfReader(str(overlay_path))
                    writer.add_page(overlay_reader.pages[0])
        finally:
            document.close()

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target_path.parent, suffix=".pdf", delete=False) as output:
            temporary_path = Path(output.name)
            writer.write(output)
        temporary_path.replace(target_path)
        self.skipped_replacements = tuple(skipped)

    def validate(self, replacement: PDFReplacement, pages_count: int | None = None) -> None:
        self._validate_text(replacement)
        if len(replacement.regions) > 1:
            raise ValueError(
                "replacement spans multiple source boxes; paragraph filling is not available"
            )
        regions = replacement.regions or (PDFReplacementRegion(
            replacement.page_index,
            replacement.bbox,
            replacement.page_pixel_size,
            replacement.dpi,
            replacement.reading_order,
        ),)
        for region in regions:
            self._validate_region(region, pages_count)

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

    @staticmethod
    def _validate_text(replacement: PDFReplacement) -> None:
        if not replacement.text.strip():
            raise ValueError("replacement text must not be empty")

    def _fit_replacement(self, replacement: PDFReplacement, width: float, height: float):
        _, _, box_width, box_height = self._box_in_points(replacement, width, height)
        return self._layout.fit(replacement.text, box_width, box_height)

    @staticmethod
    def _box_in_points(replacement: PDFReplacement, width: float, height: float) -> tuple[float, float, float, float]:
        pixel_width, pixel_height = replacement.page_pixel_size
        left, top, right, bottom = replacement.bbox
        scale_x = width / pixel_width
        scale_y = height / pixel_height
        x = left * scale_x
        y = height - bottom * scale_y
        return x, y, (right - left) * scale_x, (bottom - top) * scale_y

    def _draw_background(self, overlay, replacement: PDFReplacement, width: float, height: float) -> None:
        x, y, box_width, box_height = self._box_in_points(replacement, width, height)
        overlay.setFillColorRGB(1, 1, 1)
        overlay.rect(x, y, box_width, box_height, stroke=0, fill=1)

    def _draw_text(self, overlay, replacement: PDFReplacement, fitted, width: float, height: float) -> None:
        x, y, _, box_height = self._box_in_points(replacement, width, height)
        overlay.setFillColorRGB(0, 0, 0)
        fitted.paragraph.drawOn(
            overlay,
            x + self.options.horizontal_padding,
            y + box_height - self.options.vertical_padding - fitted.height,
        )
