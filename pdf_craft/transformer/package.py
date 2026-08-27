"""Transformers operating on render-ready document packages."""

from collections.abc import Callable
from pathlib import Path
from shutil import copy2, copytree
from typing import Protocol, cast
from xml.etree.ElementTree import Element

from pdf_craft.common.xml import read_xml, save_xml
from pdf_craft.document import DocumentPackage
from pdf_craft.extractor.chapter.chapter import decode, encode
from pdf_craft.transformer.protocol import ChapterTransformer
from pdf_craft.transformer.events import TranslationEvent, TranslationEventKind, TranslationItemKind
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.chapter_xml import ChapterXMLTransformer
from pdf_craft.transformer.xml_translator.xml_translator import SubmitKind


class PackageTransformer(Protocol):
    """A format-neutral transformation from one package to another."""

    def transform(self, package: DocumentPackage, output_path: Path) -> DocumentPackage: ...


class ChapterPackageTransformer:
    """Copy a package and transform its chapter XML files independently."""

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
        package: DocumentPackage,
        output_path: Path,
        *,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> DocumentPackage:
        package.validate()
        if output_path.exists():
            raise FileExistsError(f"output package already exists: {output_path}")
        output_path.mkdir(parents=True)
        copytree(package.chapters_path, output_path / "chapters")
        copytree(package.assets_path, output_path / "assets")
        for source in (package.toc_path, package.cover_path, package.metadata_path):
            if source is not None and source.exists():
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
        for path, chapter, item_id, character_count in chapter_tasks:
            if on_translation_event is not None:
                on_translation_event(TranslationEvent(
                    kind=TranslationEventKind.ITEM_START,
                    item_kind=TranslationItemKind.CHAPTER,
                    item_id=item_id,
                ))
            is_xml_transformer = isinstance(self.chapter_transformer, ChapterXMLTransformer)
            if is_xml_transformer:
                transformed = cast(ChapterXMLTransformer, self.chapter_transformer).transform(
                    chapter,
                    on_translation_event=on_translation_event,
                    item_id=item_id,
                    completed_characters=completed_characters,
                    total_characters=total_characters,
                )
            else:
                transformed = self.chapter_transformer.transform(chapter)
            save_xml(encode(transformed), path)
            completed_characters += character_count
            if on_translation_event is not None:
                if not is_xml_transformer:
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

        if self.toc_transformer is not None and package.toc_path is not None and package.toc_path.exists():
            save_xml(self.toc_transformer(read_xml(output_path / package.toc_path.name)), output_path / package.toc_path.name)
        if on_translation_event is not None:
            on_translation_event(TranslationEvent(
                kind=TranslationEventKind.COMPLETE,
                completed_characters=completed_characters,
                total_characters=total_characters,
            ))
        return DocumentPackage.from_path(output_path).validate()
