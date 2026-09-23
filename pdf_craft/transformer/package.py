"""Transformers operating on render-ready PDFCraftExtraction objects."""

# Internal workspace methods keep one-shot conversions directory-backed.
# pylint: disable=protected-access

import asyncio
import inspect
from collections.abc import Callable
from pathlib import Path
from shutil import copy2, copytree
from tempfile import TemporaryDirectory
from typing import Protocol, cast
from xml.etree.ElementTree import Element

from pdf_craft.common.xml import read_xml, save_xml
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.document.package import EXTRACTION_SUFFIX
from pdf_craft.runtime import (
    IO_DOMAIN, TRANSLATION_DOMAIN, callback_bridge, invoke_callback,
    run_sync,
)
from pdf_craft.extractor.chapter.chapter import (
    SourceTextFragment, TextFlowItem, decode, encode,
)
from pdf_craft.transformer.protocol import AsyncChapterTransformer, ChapterTransformer
from pdf_craft.transformer.events import TranslationEvent, TranslationEventKind, TranslationItemKind
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.chapter_xml import ChapterXMLTransformer
from pdf_craft.transformer.furniture_xml import FurnitureXMLTransformer
from pdf_craft.transformer.xml_translator.xml_translator import SubmitKind
from pdf_craft.transformer.furniture import FurnitureTransformer
from pdf_craft.transformer.furniture_translation import (
    translate_furnitures_in_workspace,
    translate_furnitures_in_workspace_async,
)
from pdf_craft.transformer.anchored_content import AnchoredContentTransformer
from pdf_craft.transformer.anchored_content import AsyncAnchoredContentTransformer
from pdf_craft.transformer.anchored_translation import (
    translate_anchored_contents_in_workspace,
    translate_anchored_contents_in_workspace_async,
)
from pdf_craft.transformer.anchored_xml import AnchoredContentXMLTransformer
from pdf_craft.transformer.translation_coverage import (
    NarrativeCoverage, paragraph_identity, write_narrative_coverage,
)


class ExtractionTransformer(Protocol):
    """A format-neutral transformation from one extraction to another."""

    def transform(
        self, extraction: PDFCraftExtraction, output_path: Path
    ) -> PDFCraftExtraction: ...


class ChapterExtractionTransformer:
    """Copy an extraction and transform its chapter XML files independently."""

    def __init__(
        self,
        chapter_transformer: ChapterTransformer | AsyncChapterTransformer,
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

    async def transform_async(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
        *,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        emit_translation_events: bool = False,
    ) -> PDFCraftExtraction:
        if output_path.suffix.lower() != EXTRACTION_SUFFIX:
            raise ValueError(f"PDFCraftExtraction path must end with {EXTRACTION_SUFFIX}")
        temporary = await IO_DOMAIN.run(
            TemporaryDirectory, prefix="pdf-craft-transformed-extraction-"
        )
        try:
            transformed = await self._transform_to_workspace_async(
                extraction,
                Path(temporary.name) / "extraction",
                on_translation_event=on_translation_event,
                emit_translation_events=emit_translation_events,
            )
            return await transformed.export_async(output_path)
        finally:
            await IO_DOMAIN.run(temporary.cleanup)

    async def _transform_to_workspace_async(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
        *,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        emit_translation_events: bool = False,
    ) -> PDFCraftExtraction:
        is_xml_transformer = isinstance(self.chapter_transformer, ChapterXMLTransformer)
        is_async_transformer = inspect.iscoroutinefunction(
            self.chapter_transformer.transform
        )
        if not is_xml_transformer and not is_async_transformer:
            callback = callback_bridge(
                asyncio.get_running_loop(), on_translation_event,
            )
            return await TRANSLATION_DOMAIN.run(
                self._transform_to_workspace,
                extraction,
                output_path,
                on_translation_event=callback,
                emit_translation_events=emit_translation_events,
            )
        chapter_tasks = await IO_DOMAIN.run(
            self._prepare_async_workspace, extraction, output_path,
        )
        total_characters = sum(item[3] for item in chapter_tasks)
        if emit_translation_events and on_translation_event is not None:
            await invoke_callback(on_translation_event, TranslationEvent(
                kind=TranslationEventKind.START,
                chapter_count=len(chapter_tasks),
                has_toc=False,
                has_metadata=False,
                total_characters=total_characters,
                completed_characters=0,
            ))

        completed_characters = 0
        narrative_coverage: list[NarrativeCoverage] = []
        for path, chapter, item_id, character_count in chapter_tasks:
            source_layouts = {
                identity: layout
                for layout in chapter.flow_items
                if isinstance(layout, TextFlowItem)
                and layout.role in {"body", "heading"}
                and (identity := paragraph_identity(chapter, layout)) is not None
            }
            if is_xml_transformer:
                transformed = await cast(
                    ChapterXMLTransformer, self.chapter_transformer,
                ).transform_async(
                    chapter,
                    on_translation_event=(
                        on_translation_event if emit_translation_events else None
                    ),
                    item_id=item_id,
                    completed_characters=completed_characters,
                    total_characters=total_characters,
                    emit_scope_events=False,
                )
            else:
                if emit_translation_events and on_translation_event is not None:
                    await invoke_callback(on_translation_event, TranslationEvent(
                        kind=TranslationEventKind.ITEM_START,
                        item_kind=TranslationItemKind.CHAPTER,
                        item_id=item_id,
                        item_completed_characters=0,
                        item_total_characters=character_count,
                    ))
                transformed = await cast(
                    AsyncChapterTransformer, self.chapter_transformer,
                ).transform(chapter)
            await IO_DOMAIN.run(save_xml, encode(transformed), path)
            targets = {
                identity: layout
                for layout in transformed.flow_items
                if isinstance(layout, TextFlowItem)
                and (identity := paragraph_identity(transformed, layout)) is not None
            }
            for identity in source_layouts:
                target = targets.get(identity)
                state = (
                    "translated"
                    if target is not None and _has_visible_content(target)
                    else "preserved"
                )
                narrative_coverage.append(NarrativeCoverage(*identity, state))
            completed_characters += character_count
            if (
                not is_xml_transformer
                and emit_translation_events
                and on_translation_event is not None
            ):
                for event_kind in (
                    TranslationEventKind.PROGRESS,
                    TranslationEventKind.ITEM_COMPLETE,
                ):
                    await invoke_callback(on_translation_event, TranslationEvent(
                        kind=event_kind,
                        item_kind=TranslationItemKind.CHAPTER,
                        item_id=item_id,
                        item_completed_characters=character_count,
                        item_total_characters=character_count,
                        completed_characters=completed_characters,
                        total_characters=total_characters,
                    ))

        result = await IO_DOMAIN.run(
            self._finish_async_workspace,
            output_path,
            narrative_coverage,
        )
        if emit_translation_events and on_translation_event is not None:
            await invoke_callback(on_translation_event, TranslationEvent(
                kind=TranslationEventKind.COMPLETE,
                completed_characters=completed_characters,
                total_characters=total_characters,
            ))
        return result

    def _prepare_async_workspace(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ):
        extraction.validate()
        if output_path.exists():
            raise FileExistsError(f"output extraction workspace already exists: {output_path}")
        output_path.mkdir(parents=True)
        _copy_extraction_to_workspace(extraction, output_path)
        tasks = []
        for path in sorted((output_path / "chapters").glob("chapter*.xml")):
            chapter = decode(read_xml(path))
            if isinstance(self.chapter_transformer, ChapterXMLTransformer):
                character_count = self.chapter_transformer.source_character_count(chapter)
                has_content = self.chapter_transformer.has_translatable_content(chapter)
            else:
                segments = list(search_text_segments(encode(chapter)))
                character_count = sum(len(segment.text) for segment in segments)
                has_content = any(segment.text.strip() for segment in segments)
            if has_content:
                item_id: str | int = chapter.id if chapter.id is not None else "head"
                tasks.append((
                    path,
                    chapter,
                    item_id,
                    character_count,
                ))
        return tasks

    def _finish_async_workspace(
        self,
        output_path: Path,
        narrative_coverage: list[NarrativeCoverage],
    ) -> PDFCraftExtraction:
        toc_path = output_path / "toc.xml"
        if self.toc_transformer is not None and toc_path.exists():
            save_xml(self.toc_transformer(read_xml(toc_path)), toc_path)
        write_narrative_coverage(output_path / "translation.xml", narrative_coverage)
        return PDFCraftExtraction._from_workspace(output_path).validate()

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
        _copy_extraction_to_workspace(extraction, output_path)

        chapter_paths = sorted((output_path / "chapters").glob("chapter*.xml"))
        chapter_tasks = []
        is_xml_transformer = isinstance(self.chapter_transformer, ChapterXMLTransformer)
        for path in chapter_paths:
            chapter = decode(read_xml(path))
            if is_xml_transformer:
                character_count = cast(ChapterXMLTransformer, self.chapter_transformer).source_character_count(chapter)
                has_content = cast(ChapterXMLTransformer, self.chapter_transformer).has_translatable_content(chapter)
            else:
                segments = list(search_text_segments(encode(chapter)))
                character_count = sum(len(segment.text) for segment in segments)
                has_content = any(segment.text.strip() for segment in segments)
            if has_content:
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
        narrative_coverage: list[NarrativeCoverage] = []
        for path, chapter, item_id, character_count in chapter_tasks:
            source_layouts = {
                identity: layout
                for layout in chapter.flow_items
                if isinstance(layout, TextFlowItem)
                and layout.role in {"body", "heading"}
                and (identity := paragraph_identity(chapter, layout)) is not None
            }
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
                transformed = cast(ChapterTransformer, self.chapter_transformer).transform(
                    chapter
                )
            save_xml(encode(transformed), path)
            targets = {
                identity: layout
                for layout in transformed.flow_items
                if isinstance(layout, TextFlowItem)
                and (identity := paragraph_identity(transformed, layout)) is not None
            }
            for identity in source_layouts:
                target = targets.get(identity)
                # A successful transformer invocation is the sole affirmative
                # translation signal.  Equality with source text is not a
                # failure; a missing/empty target is explicitly preserved.
                state = "translated" if target is not None and _has_visible_content(target) else "preserved"
                narrative_coverage.append(NarrativeCoverage(*identity, state))
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
        write_narrative_coverage(output_path / "translation.xml", narrative_coverage)
        if emit_translation_events and on_translation_event is not None:
            on_translation_event(TranslationEvent(
                kind=TranslationEventKind.COMPLETE,
                completed_characters=completed_characters,
                total_characters=total_characters,
            ))
        return PDFCraftExtraction._from_workspace(output_path).validate()


class FurnitureExtractionTransformer:
    """Translate only the page-furniture layer of a translated pcex."""

    def __init__(self, furniture_transformer: FurnitureTransformer) -> None:
        self.furniture_transformer = furniture_transformer

    def transform(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        if output_path.suffix.lower() != EXTRACTION_SUFFIX:
            raise ValueError(f"PDFCraftExtraction path must end with {EXTRACTION_SUFFIX}")
        with TemporaryDirectory(prefix="pdf-craft-furnitures-transformed-") as directory:
            transformed = self._transform_to_workspace(
                extraction,
                Path(directory) / "extraction",
            )
            return transformed.export(output_path)

    def _transform_to_workspace(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        extraction.validate()
        if output_path.exists():
            raise FileExistsError(f"output extraction workspace already exists: {output_path}")
        output_path.mkdir(parents=True)
        _copy_extraction_to_workspace(extraction, output_path)
        translate_furnitures_in_workspace(
            chapters_path=output_path / "chapters",
            toc_path=output_path / "toc.xml",
            furnitures_path=output_path / "furnitures.xml",
            translation_path=output_path / "translation.xml",
            transformer=self.furniture_transformer,
        )
        return PDFCraftExtraction._from_workspace(output_path).validate()

    async def _transform_to_workspace_async(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        if not isinstance(self.furniture_transformer, FurnitureXMLTransformer):
            return await TRANSLATION_DOMAIN.run(
                self._transform_to_workspace, extraction, output_path,
            )

        def prepare():
            extraction.validate()
            if output_path.exists():
                raise FileExistsError(
                    f"output extraction workspace already exists: {output_path}"
                )
            output_path.mkdir(parents=True)
            _copy_extraction_to_workspace(extraction, output_path)

        await IO_DOMAIN.run(prepare)
        await translate_furnitures_in_workspace_async(
            chapters_path=output_path / "chapters",
            toc_path=output_path / "toc.xml",
            furnitures_path=output_path / "furnitures.xml",
            translation_path=output_path / "translation.xml",
            transformer=self.furniture_transformer,
        )
        return await IO_DOMAIN.run(
            PDFCraftExtraction._from_workspace(output_path).validate,
        )


class AnchoredContentExtractionTransformer:
    """Translate image/table text independently from NarrativeFlow."""

    def __init__(
        self,
        anchored_transformer: AnchoredContentTransformer | AsyncAnchoredContentTransformer,
    ) -> None:
        self.anchored_transformer = anchored_transformer

    def transform(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        if inspect.iscoroutinefunction(self.anchored_transformer.transform_assets):
            return run_sync(self.transform_async(extraction, output_path))
        return self._transform_sync(extraction, output_path)

    def _transform_sync(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        if output_path.suffix.lower() != EXTRACTION_SUFFIX:
            raise ValueError(f"PDFCraftExtraction path must end with {EXTRACTION_SUFFIX}")
        with TemporaryDirectory(prefix="pdf-craft-anchored-transformed-") as directory:
            transformed = self._transform_to_workspace(
                extraction,
                Path(directory) / "extraction",
            )
            return transformed.export(output_path)

    def _transform_to_workspace(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        extraction.validate()
        if output_path.exists():
            raise FileExistsError(f"output extraction workspace already exists: {output_path}")
        output_path.mkdir(parents=True)
        _copy_extraction_to_workspace(extraction, output_path)
        translate_anchored_contents_in_workspace(
            chapters_path=output_path / "chapters",
            translation_path=output_path / "translation.xml",
            transformer=cast(AnchoredContentTransformer, self.anchored_transformer),
        )
        return PDFCraftExtraction._from_workspace(output_path).validate()

    async def transform_async(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        if not isinstance(self.anchored_transformer, AnchoredContentXMLTransformer) and not inspect.iscoroutinefunction(
            self.anchored_transformer.transform_assets,
        ):
            return await TRANSLATION_DOMAIN.run(
                self._transform_sync, extraction, output_path,
            )
        temporary = await IO_DOMAIN.run(
            TemporaryDirectory, prefix="pdf-craft-anchored-transformed-",
        )
        try:
            transformed = await self._transform_to_workspace_async(
                extraction, Path(temporary.name) / "extraction",
            )
            return await transformed.export_async(output_path)
        finally:
            await IO_DOMAIN.run(temporary.cleanup)

    async def _transform_to_workspace_async(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        transformer = cast(
            AnchoredContentXMLTransformer | AsyncAnchoredContentTransformer,
            self.anchored_transformer,
        )

        def prepare():
            extraction.validate()
            if output_path.exists():
                raise FileExistsError(
                    f"output extraction workspace already exists: {output_path}"
                )
            output_path.mkdir(parents=True)
            _copy_extraction_to_workspace(extraction, output_path)

        await IO_DOMAIN.run(prepare)
        await translate_anchored_contents_in_workspace_async(
            chapters_path=output_path / "chapters",
            translation_path=output_path / "translation.xml",
            transformer=transformer,
        )
        return await IO_DOMAIN.run(
            PDFCraftExtraction._from_workspace(output_path).validate,
        )


def _copy_extraction_to_workspace(
    extraction: PDFCraftExtraction,
    output_path: Path,
) -> None:
    with extraction._materialize() as paths:
        copytree(paths.chapters, output_path / "chapters")
        copytree(paths.assets, output_path / "assets")
        for source in (
            paths.manifest,
            paths.pages,
            paths.toc,
            paths.cover,
            paths.furnitures,
            paths.translation,
        ):
            if source.exists():
                copy2(source, output_path / source.name)


def _has_visible_content(layout: TextFlowItem) -> bool:
    """Return whether a transformed PDF paragraph still has drawable content."""
    return any(
        bool(str(item).strip())
        for fragment in layout.children
        if isinstance(fragment, SourceTextFragment)
        for item in fragment.content
    )
