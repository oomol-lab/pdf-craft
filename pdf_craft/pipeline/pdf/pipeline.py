from collections.abc import Callable
from pathlib import Path
from typing import cast

from pdf_craft.extractor.chapter.chapter import Chapter, ParagraphLayout, encode
from pdf_craft.extractor.chapter.chapter import InlineExpression, Reference
from pdf_craft.extractor.chapter.reader import create_chapters_reader
from pdf_craft.markdown.paragraph import HTMLTag
from pdf_craft.expression import to_markdown_string
from pdf_craft.document import DocumentPackage
from pdf_craft.transformer.events import TranslationEvent, TranslationEventKind, TranslationItemKind
from pdf_craft.transformer.chapter_xml import ChapterXMLTransformer
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.pdf.handler import PDFHandler
from pdf_craft.pipeline.pdf.patcher import PDFPatcher, PDFReplacement
from pdf_craft.transformer import ChapterTransformer


class PDFTranslationPipeline:
    """Apply a replace-only text transformer to an extracted PDF package."""

    def __init__(self, pdf_handler: PDFHandler | None = None, patcher: PDFPatcher | None = None, dpi: int = 300) -> None:
        self.pdf_handler = pdf_handler
        self.patcher = patcher or PDFPatcher(pdf_handler=pdf_handler)
        self.dpi = dpi

    def translate(
        self,
        pdf_path: Path,
        target_path: Path,
        package: DocumentPackage | Path,
        transformer: Callable[[str], str] | ChapterTransformer,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> None:
        package = package if isinstance(package, DocumentPackage) else DocumentPackage.from_path(package)
        package.validate()
        document = self.pdf_handler.open(pdf_path) if self.pdf_handler else None
        replacements: list[PDFReplacement] = []
        try:
            pages = package.page_pixel_sizes()
            reader = create_chapters_reader(package.chapters_path)
            chapters = list(reader())
            chapter_tasks = []
            for chapter in chapters:
                segments = list(search_text_segments(encode(chapter)))
                if any(segment.text.strip() for segment in segments):
                    chapter_tasks.append((chapter, sum(len(segment.text) for segment in segments)))
            total_characters = sum(item[1] for item in chapter_tasks)
            if on_translation_event is not None:
                on_translation_event(TranslationEvent(
                    kind=TranslationEventKind.START,
                    chapter_count=len(chapter_tasks),
                    has_toc=False,
                    has_metadata=False,
                    total_characters=total_characters,
                    completed_characters=0,
                ))
            completed_characters = 0
            for chapter, character_count in chapter_tasks:
                structured = not callable(transformer)
                item_id = chapter.id if chapter.id is not None else "head"
                is_xml_transformer = isinstance(transformer, ChapterXMLTransformer)
                if on_translation_event is not None and not is_xml_transformer:
                    on_translation_event(TranslationEvent(
                        kind=TranslationEventKind.ITEM_START,
                        item_kind=TranslationItemKind.CHAPTER,
                        item_id=item_id,
                    ))
                if is_xml_transformer:
                    transformed = cast(ChapterXMLTransformer, transformer).transform(
                        chapter,
                        on_translation_event=on_translation_event,
                        item_id=item_id,
                        completed_characters=completed_characters,
                        total_characters=total_characters,
                        emit_scope_events=False,
                    )
                else:
                    transformed = transformer.transform(chapter) if structured else chapter
                callback = transformer if callable(transformer) else (lambda text: text)
                self._collect_chapter(transformed, callback, document, pages, replacements, structured)
                completed_characters += character_count
                if on_translation_event is not None and not is_xml_transformer:
                    on_translation_event(TranslationEvent(
                        kind=TranslationEventKind.PROGRESS,
                        completed_characters=completed_characters,
                        total_characters=total_characters,
                    ))
                    on_translation_event(TranslationEvent(
                        kind=TranslationEventKind.ITEM_COMPLETE,
                        item_kind=TranslationItemKind.CHAPTER,
                        item_id=item_id,
                        completed_characters=completed_characters,
                        total_characters=total_characters,
                    ))
        finally:
            if document:
                document.close()
        self.patcher.patch(pdf_path, target_path, replacements)
        if on_translation_event is not None:
            on_translation_event(TranslationEvent(
                kind=TranslationEventKind.COMPLETE,
                completed_characters=completed_characters,
                total_characters=total_characters,
            ))

    def patch(
        self,
        pdf_path: Path,
        target_path: Path,
        package: DocumentPackage | Path,
    ) -> None:
        """Write the text already present in ``package`` back to ``pdf_path``.

        This is deliberately separate from :meth:`translate`: the package is
        already the source of the replacement text, so no OCR or LLM
        transformer is involved.
        """
        package = package if isinstance(package, DocumentPackage) else DocumentPackage.from_path(package)
        package.validate()
        document = self.pdf_handler.open(pdf_path) if self.pdf_handler else None
        replacements: list[PDFReplacement] = []
        try:
            pages = package.page_pixel_sizes()
            reader = create_chapters_reader(package.chapters_path)
            for chapter in reader():
                self._collect_chapter(
                    chapter, lambda text: text, document, pages, replacements, structured=True,
                )
        finally:
            if document:
                document.close()
        self.patcher.patch(pdf_path, target_path, replacements)

    def _collect_chapter(self, chapter: Chapter, transformer, document, pages, replacements, structured: bool = False) -> None:
        for layout in chapter.layouts:
            if not isinstance(layout, ParagraphLayout) or layout.ref not in {"text", "sub_title"}:
                continue
            for block in layout.blocks:
                source = _to_patch_text(block.content).strip()
                if not source:
                    continue
                translated = transformer(source)
                if not translated or (translated == source and not structured):
                    continue
                if block.page_index not in pages:
                    if document is None:
                        raise ValueError("PDF handler is required to resolve page dimensions")
                    image = document.render_page(block.page_index, self.dpi)
                    pages[block.page_index] = image.size
                replacements.append(PDFReplacement(
                    block.page_index, block.det, translated, pages[block.page_index], self.dpi,
                    reading_order=block.order,
                ))


def _to_patch_text(items) -> str:
    """Serialize structured Chapter content without silently dropping nodes.

    The patcher can only draw text, so formulas retain their Markdown delimiters,
    references retain their printed mark, and HTML wrappers retain their children.
    """
    parts: list[str] = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, InlineExpression):
            parts.append(to_markdown_string(item.kind, item.content))
        elif isinstance(item, Reference):
            parts.append(str(item.mark))
        elif isinstance(item, HTMLTag):
            parts.append(_to_patch_text(item.children))
        else:
            raise TypeError(f"unsupported chapter content for PDF patching: {type(item).__name__}")
    return "".join(parts)
