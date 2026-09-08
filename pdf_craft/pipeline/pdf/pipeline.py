# pylint: disable=protected-access

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

from pdf_craft.extractor.chapter.chapter import Chapter, ParagraphLayout, encode
from pdf_craft.extractor.chapter.chapter import InlineExpression, Reference
from pdf_craft.extractor.chapter.reader import create_chapters_reader
from pdf_craft.markdown.paragraph import HTMLTag
from pdf_craft.expression import to_markdown_string
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.transformer.events import TranslationEvent, TranslationEventKind, TranslationItemKind
from pdf_craft.transformer.chapter_xml import ChapterXMLTransformer
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.pdf.handler import PDFHandler
from pdf_craft.pipeline.pdf.models import PDFInlineFormula, PDFReplacement, PDFReplacementRegion
from pdf_craft.pipeline.pdf.patcher import PDFPatcher
from pdf_craft.transformer import ChapterTransformer


_INLINE_FORMULA_MARKER = "\ufffc"


class PDFTranslationPipeline:
    """Apply a replace-only text transformer to a PDFCraftExtraction."""

    def __init__(self, pdf_handler: PDFHandler | None = None, patcher: PDFPatcher | None = None, dpi: int = 300) -> None:
        self.patcher = patcher or PDFPatcher(pdf_handler=pdf_handler, dpi=dpi)

    def translate(
        self,
        pdf_path: Path,
        target_path: Path,
        extraction: PDFCraftExtraction | Path,
        transformer: Callable[[str], str] | ChapterTransformer,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> None:
        extraction = _ensure_extraction(extraction)
        extraction.validate()
        pages = extraction.page_pixel_sizes()
        render_dpi = extraction.render_dpi()
        with extraction._materialize() as paths:
            reader = create_chapters_reader(paths.chapters)
            chapter_count = 0
            total_characters = 0
            for chapter in reader():
                segments = list(search_text_segments(encode(chapter)))
                if any(segment.text.strip() for segment in segments):
                    chapter_count += 1
                    total_characters += sum(len(segment.text) for segment in segments)
            if on_translation_event is not None:
                on_translation_event(TranslationEvent(
                    kind=TranslationEventKind.START,
                    chapter_count=chapter_count,
                    has_toc=False,
                    has_metadata=False,
                    total_characters=total_characters,
                    completed_characters=0,
                ))
            completed_characters = 0

            def translated_replacements() -> Iterator[PDFReplacement]:
                nonlocal completed_characters
                for chapter in reader():
                    segments = list(search_text_segments(encode(chapter)))
                    if not any(segment.text.strip() for segment in segments):
                        continue
                    character_count = sum(len(segment.text) for segment in segments)
                    structured = not callable(transformer)
                    item_id = chapter.id if chapter.id is not None else "head"
                    is_xml_transformer = isinstance(transformer, ChapterXMLTransformer)
                    if on_translation_event is not None and not is_xml_transformer:
                        on_translation_event(TranslationEvent(
                            kind=TranslationEventKind.ITEM_START,
                            item_kind=TranslationItemKind.CHAPTER,
                            item_id=item_id,
                            item_completed_characters=0,
                            item_total_characters=character_count,
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
                        transformed = (
                            cast(ChapterTransformer, transformer).transform(chapter)
                            if structured else chapter
                        )
                    callback = transformer if callable(transformer) else (lambda text: text)
                    yield from self._iter_chapter_replacements(
                        transformed, callback, pages, render_dpi, structured,
                    )
                    completed_characters += character_count
                    if on_translation_event is not None and not is_xml_transformer:
                        on_translation_event(TranslationEvent(
                            kind=TranslationEventKind.PROGRESS,
                            item_kind=TranslationItemKind.CHAPTER,
                            item_id=item_id,
                            item_completed_characters=character_count,
                            item_total_characters=character_count,
                            completed_characters=completed_characters,
                            total_characters=total_characters,
                        ))
                        on_translation_event(TranslationEvent(
                            kind=TranslationEventKind.ITEM_COMPLETE,
                            item_kind=TranslationItemKind.CHAPTER,
                            item_id=item_id,
                            item_completed_characters=character_count,
                            item_total_characters=character_count,
                            completed_characters=completed_characters,
                            total_characters=total_characters,
                        ))

            self.patcher.patch(pdf_path, target_path, translated_replacements())
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
        extraction: PDFCraftExtraction | Path,
    ) -> None:
        """Write text already present in ``extraction`` back to ``pdf_path``.

        This is deliberately separate from :meth:`translate`: the extraction is
        already the source of the replacement text, so no OCR or LLM
        transformer is involved.
        """
        extraction = _ensure_extraction(extraction)
        extraction.validate()
        pages = extraction.page_pixel_sizes()
        render_dpi = extraction.render_dpi()
        with extraction._materialize() as paths:
            reader = create_chapters_reader(paths.chapters)
            def replacements() -> Iterator[PDFReplacement]:
                for chapter in reader():
                    yield from self._iter_chapter_replacements(
                        chapter, lambda text: text, pages, render_dpi, structured=True,
                    )

            self.patcher.patch(pdf_path, target_path, replacements())

    def _iter_chapter_replacements(
        self, chapter: Chapter, transformer, pages,
        render_dpi: int, structured: bool = False,
    ) -> Iterator[PDFReplacement]:
        for layout in chapter.layouts:
            if not isinstance(layout, ParagraphLayout) or layout.ref not in {"text", "sub_title"}:
                continue
            source = "".join(_to_patch_text(block.content) for block in layout.blocks).strip()
            if not source:
                continue
            translated = transformer(source)
            if not translated or (translated == source and not structured):
                continue
            inline_formulas: tuple[PDFInlineFormula, ...] = ()
            patch_text = translated
            if structured:
                patch_text, inline_formulas = _to_pdf_patch_content(
                    item
                    for block in layout.blocks
                    for item in block.content
                )
                patch_text = patch_text.strip()
            if not patch_text:
                continue

            regions: list[PDFReplacementRegion] = []
            for block in layout.blocks:
                if block.page_index not in pages:
                    raise ValueError(
                        f"PDFCraftExtraction pages.xml is missing page {block.page_index}"
                    )
                regions.append(PDFReplacementRegion(
                    block.page_index, block.det, pages[block.page_index], render_dpi,
                    reading_order=block.order,
                ))
            if not regions:  # A ParagraphLayout without blocks has no source geometry.
                continue

            first = regions[0]
            yield PDFReplacement(
                first.page_index, first.bbox, patch_text, first.page_pixel_size, first.dpi,
                reading_order=first.reading_order, regions=tuple(regions),
                layout_ref=layout.ref, layout_level=layout.level,
                inline_formulas=inline_formulas,
            )


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


def _to_pdf_patch_content(items) -> tuple[str, tuple[PDFInlineFormula, ...]]:
    """Return visible text with structural markers for embedded formulas.

    XML translation intentionally keeps :class:`InlineExpression` nodes in
    the translated Chapter.  Do not serialize them back to delimiter-wrapped
    LaTeX here: the PDF filler can then render a vector atom when local TeX is
    available, or use its plain-text fallback when it is not.
    """
    parts: list[str] = []
    formulas: list[PDFInlineFormula] = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, InlineExpression):
            parts.append(_INLINE_FORMULA_MARKER)
            formulas.append(PDFInlineFormula(item.content.strip()))
        elif isinstance(item, Reference):
            parts.append(str(item.mark))
        elif isinstance(item, HTMLTag):
            children, child_formulas = _to_pdf_patch_content(item.children)
            parts.append(children)
            formulas.extend(child_formulas)
        else:
            raise TypeError(f"unsupported chapter content for PDF patching: {type(item).__name__}")
    return "".join(parts), tuple(formulas)


def _ensure_extraction(value: PDFCraftExtraction | Path) -> PDFCraftExtraction:
    if isinstance(value, PDFCraftExtraction):
        return value
    return PDFCraftExtraction.open(value)
