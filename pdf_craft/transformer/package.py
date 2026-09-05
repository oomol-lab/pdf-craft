"""Transformers operating on render-ready PDFCraftExtraction objects."""

# Internal workspace methods keep one-shot conversions directory-backed.
# pylint: disable=protected-access

from collections.abc import Callable
from pathlib import Path
from shutil import copy2, copytree
from tempfile import TemporaryDirectory
from typing import Protocol, cast
from xml.etree.ElementTree import Element

from pdf_craft.common.xml import read_xml, save_xml
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.document.package import EXTRACTION_SUFFIX
from pdf_craft.extractor.chapter.chapter import decode, encode
from pdf_craft.transformer.protocol import ChapterTransformer
from pdf_craft.transformer.events import TranslationEvent, TranslationEventKind, TranslationItemKind
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.chapter_xml import ChapterXMLTransformer
from pdf_craft.transformer.xml_translator.xml_translator import SubmitKind


class ExtractionTransformer(Protocol):
    """A format-neutral transformation from one extraction to another."""

    def transform(
        self, extraction: PDFCraftExtraction, output_path: Path
    ) -> PDFCraftExtraction: ...


class ChapterExtractionTransformer:
    """Copy an extraction and transform its chapter XML files independently."""

    def __init__(
        self,
        chapter_transformer: ChapterTransformer,
        *,
        mode: SubmitKind = SubmitKind.REPLACE,
        toc_transformer: Callable[[Element], Element] | None = None,
    ) -> None:
        if mode != SubmitKind.REPLACE and hasattr(chapter_transformer, "with_mode"):
            chapter_transformer = getattr(chapter_transformer, "with_mode")(mode)
        self.chapter_transformer = chapter_transformer
        self.mode = mode
        self.toc_transformer = toc_transformer

    def transform(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
        *,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
        emit_translation_events: bool = False,
    ) -> PDFCraftExtraction:
        if output_path.suffix.lower() != EXTRACTION_SUFFIX:
            raise ValueError(f"PDFCraftExtraction path must end with {EXTRACTION_SUFFIX}")
        with TemporaryDirectory(prefix="pdf-craft-transformed-extraction-") as directory:
            transformed = self._transform_to_workspace(
                extraction,
                Path(directory) / "extraction",
                on_translation_event=on_translation_event,
                emit_translation_events=emit_translation_events,
            )
            return transformed.export(output_path)

    def _transform_to_workspace(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
        *,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
        emit_translation_events: bool = False,
    ) -> PDFCraftExtraction:
        extraction.validate()
        if output_path.exists():
            raise FileExistsError(f"output extraction workspace already exists: {output_path}")
        output_path.mkdir(parents=True)
        with extraction._materialize() as paths:
            copytree(paths.chapters, output_path / "chapters")
            copytree(paths.assets, output_path / "assets")
            for source in (paths.manifest, paths.pages, paths.toc, paths.cover):
                if source.exists():
                    copy2(source, output_path / source.name)

        chapter_paths = sorted((output_path / "chapters").glob("chapter*.xml"))
        chapter_tasks = []
        for path in chapter_paths:
            chapter = decode(read_xml(path))
            segments = list(search_text_segments(encode(chapter)))
            character_count = sum(len(segment.text) for segment in segments)
            if any(segment.text.strip() for segment in segments):
                item_id: str | int = chapter.id if chapter.id is not None else "head"
                chapter_tasks.append((path, chapter, item_id, character_count))

        total_characters = sum(item[3] for item in chapter_tasks)
        if emit_translation_events and on_translation_event is not None:
            on_translation_event(TranslationEvent(
                kind=TranslationEventKind.START,
                chapter_count=len(chapter_tasks),
                has_toc=False,
                has_metadata=False,
                total_characters=total_characters,
                completed_characters=0,
            ))

        completed_characters = 0
        for path, chapter, item_id, character_count in chapter_tasks:
            is_xml_transformer = isinstance(self.chapter_transformer, ChapterXMLTransformer)
            if emit_translation_events and on_translation_event is not None and not is_xml_transformer:
                on_translation_event(TranslationEvent(
                    kind=TranslationEventKind.ITEM_START,
                    item_kind=TranslationItemKind.CHAPTER,
                    item_id=item_id,
                    item_completed_characters=0,
                    item_total_characters=character_count,
                ))
            if is_xml_transformer:
                transformed = cast(ChapterXMLTransformer, self.chapter_transformer).transform(
                    chapter,
                    on_translation_event=on_translation_event if emit_translation_events else None,
                    item_id=item_id,
                    completed_characters=completed_characters,
                    total_characters=total_characters,
                    emit_scope_events=False,
                )
            else:
                transformed = self.chapter_transformer.transform(chapter)
            save_xml(encode(transformed), path)
            completed_characters += character_count
            if emit_translation_events and on_translation_event is not None and not is_xml_transformer:
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

        toc_path = output_path / "toc.xml"
        if self.toc_transformer is not None and toc_path.exists():
            save_xml(self.toc_transformer(read_xml(toc_path)), toc_path)
        if emit_translation_events and on_translation_event is not None:
            on_translation_event(TranslationEvent(
                kind=TranslationEventKind.COMPLETE,
                completed_characters=completed_characters,
                total_characters=total_characters,
            ))
        return PDFCraftExtraction._from_workspace(output_path).validate()
