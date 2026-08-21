from collections.abc import Callable
from pathlib import Path

from pdf_craft.sequence.chapter import Chapter, ParagraphLayout
from pdf_craft.sequence.reader import create_chapters_reader
from pdf_craft.pdf.handler import PDFHandler
from pdf_craft.pipeline.pdf.patcher import PDFPatcher, PDFReplacement


class PDFTranslationPipeline:
    """Apply a replace-only text transformer to an extracted PDF package."""

    def __init__(self, pdf_handler: PDFHandler | None = None, patcher: PDFPatcher | None = None, dpi: int = 300) -> None:
        self.pdf_handler = pdf_handler
        self.patcher = patcher or PDFPatcher()
        self.dpi = dpi

    def translate(
        self,
        pdf_path: Path,
        target_path: Path,
        chapters_path: Path,
        transformer: Callable[[str], str],
    ) -> None:
        document = self.pdf_handler.open(pdf_path) if self.pdf_handler else None
        replacements: list[PDFReplacement] = []
        try:
            pages: dict[int, tuple[int, int]] = {}
            reader = create_chapters_reader(chapters_path)
            for chapter in reader():
                self._collect_chapter(chapter, transformer, document, pages, replacements)
        finally:
            if document:
                document.close()
        self.patcher.patch(pdf_path, target_path, replacements)

    def _collect_chapter(self, chapter: Chapter, transformer, document, pages, replacements) -> None:
        for layout in chapter.layouts:
            if not isinstance(layout, ParagraphLayout) or layout.ref not in {"text", "sub_title"}:
                continue
            for block in layout.blocks:
                source = "".join(item for item in block.content if isinstance(item, str)).strip()
                if not source:
                    continue
                translated = transformer(source)
                if not translated or translated == source:
                    continue
                if block.page_index not in pages:
                    if document is None:
                        raise ValueError("PDF handler is required to resolve page dimensions")
                    image = document.render_page(block.page_index, self.dpi)
                    pages[block.page_index] = image.size
                replacements.append(PDFReplacement(block.page_index, block.det, translated, pages[block.page_index], self.dpi))
