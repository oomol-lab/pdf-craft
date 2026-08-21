from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable


@dataclass(frozen=True)
class PDFReplacement:
    page_index: int
    bbox: tuple[int, int, int, int]
    text: str
    page_pixel_size: tuple[int, int]
    dpi: int = 300


class PDFPatcher:
    """Replace OCR-backed text regions in a PDF with translated text.

    The implementation uses pypdf for page preservation and reportlab for a
    transparent overlay. Both imports are lazy so normal OCR/Markdown users do
    not need the patching stack at import time.
    """

    def __init__(self, font_name: str = "Helvetica", font_size: float = 10.0) -> None:
        self.font_name = font_name
        self.font_size = font_size

    def patch(self, source_path: Path, target_path: Path, replacements: Iterable[PDFReplacement]) -> None:
        try:
            import pypdf
            from reportlab.pdfgen import canvas
        except ImportError as error:
            raise RuntimeError("PDF patching requires the optional 'reportlab' dependency") from error

        reader = pypdf.PdfReader(str(source_path))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        replacements_by_page: dict[int, list[PDFReplacement]] = {}
        for replacement in replacements:
            self.validate(replacement, pages_count=len(reader.pages))
            replacements_by_page.setdefault(replacement.page_index, []).append(replacement)

        writer = pypdf.PdfWriter()
        for index, page in enumerate(reader.pages, 1):
            page_replacements = replacements_by_page.get(index, [])
            if page_replacements:
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)
                with NamedTemporaryFile(suffix=".pdf") as overlay_file:
                    overlay = canvas.Canvas(overlay_file.name, pagesize=(width, height))
                    for replacement in page_replacements:
                        self._draw_replacement(overlay, replacement, width, height)
                    overlay.save()
                    overlay_reader = pypdf.PdfReader(overlay_file.name)
                    page.merge_page(overlay_reader.pages[0])
            writer.add_page(page)

        with target_path.open("wb") as output:
            writer.write(output)

    def validate(self, replacement: PDFReplacement, pages_count: int | None = None) -> None:
        left, top, right, bottom = replacement.bbox
        if replacement.page_index < 1:
            raise ValueError("page_index must be positive")
        if pages_count is not None and replacement.page_index > pages_count:
            raise ValueError(f"page_index {replacement.page_index} exceeds source page count {pages_count}")
        if left < 0 or top < 0 or right <= left or bottom <= top:
            raise ValueError(f"invalid bbox: {replacement.bbox}")
        if not replacement.text.strip():
            raise ValueError("replacement text must not be empty")
        if replacement.page_pixel_size[0] <= 0 or replacement.page_pixel_size[1] <= 0:
            raise ValueError("page_pixel_size must be positive")
        if right > replacement.page_pixel_size[0] or bottom > replacement.page_pixel_size[1]:
            raise ValueError("bbox exceeds page_pixel_size")

    def _draw_replacement(self, overlay, replacement: PDFReplacement, width: float, height: float) -> None:
        pixel_width, pixel_height = replacement.page_pixel_size
        left, top, right, bottom = replacement.bbox
        scale_x = width / pixel_width
        scale_y = height / pixel_height
        x = left * scale_x
        y = height - bottom * scale_y
        box_width = (right - left) * scale_x
        box_height = (bottom - top) * scale_y

        overlay.setFillColorRGB(1, 1, 1)
        overlay.rect(x, y, box_width, box_height, stroke=0, fill=1)
        overlay.setFillColorRGB(0, 0, 0)
        overlay.setFont(self.font_name, self.font_size)
        line_height = self.font_size * 1.2
        words = replacement.text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and overlay.stringWidth(candidate, self.font_name, self.font_size) > box_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        if not lines:
            lines = [replacement.text]
        max_lines = max(1, int(box_height // line_height))
        text = overlay.beginText(x + 1, y + box_height - line_height)
        for line in lines[:max_lines]:
            text.textLine(line)
        overlay.drawText(text)
