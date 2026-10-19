"""Transformers operating on render-ready PDFCraftExtraction objects."""

# Internal workspace methods keep one-shot conversions directory-backed.
# pylint: disable=protected-access

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2, copytree
from tempfile import TemporaryDirectory
from typing import Protocol, cast
from xml.etree.ElementTree import Element

from pdf_craft.common.xml import read_xml, save_xml
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.document.package import (
    EXTRACTION_SUFFIX, FORMAT_VERSION, _read_manifest, _v3_manifest,
    validate_translation_id,
)
from pdf_craft.runtime import IO_DOMAIN, invoke_callback
from pdf_craft.extractor.chapter.chapter import (
    SourceTextFragment, TextFlowItem, decode, encode, narrative_texts,
)
from pdf_craft.transformer.protocol import _ChapterTransformer
from pdf_craft.transformer.events import TranslationEvent, TranslationEventKind, TranslationItemKind
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.xml_translator.xml_translator import SubmitKind
from pdf_craft.transformer.furniture import FurnitureTransformer
from pdf_craft.transformer.furniture_translation import translate_furnitures_in_workspace_async
from pdf_craft.transformer.anchored_content import AnchoredContentTransformer
from pdf_craft.transformer.anchored_translation import translate_anchored_contents_in_workspace_async
from pdf_craft.transformer.translation_coverage import (
    NarrativeCoverage, paragraph_identity, write_narrative_coverage,
)


class ExtractionTransformer(Protocol):
    """A format-neutral transformation from one extraction to another."""

    async def transform(
        self, extraction: PDFCraftExtraction, output_path: Path
    ) -> PDFCraftExtraction: ...


class ChapterExtractionTransformer:
    """Copy an extraction and transform its chapter XML files independently."""

    def __init__(
        self,
        chapter_transformer: _ChapterTransformer,
        *,
        mode: SubmitKind = SubmitKind.REPLACE,
        toc_transformer: Callable[[Element], Awaitable[Element]] | None = None,
    ) -> None:
        if mode != SubmitKind.REPLACE and hasattr(chapter_transformer, "with_mode"):
            chapter_transformer = getattr(chapter_transformer, "with_mode")(mode)
        self.chapter_transformer = chapter_transformer
        self.mode = mode
        self.toc_transformer = toc_transformer

    async def transform(
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
            return await transformed._export_async(output_path)
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
        is_xml_transformer = isinstance(self.chapter_transformer, NarrativeXMLTransformer)
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

        async def commit_chapter(task_index, transformed):
            nonlocal completed_characters
            path, chapter, item_id, character_count = chapter_tasks[task_index]
            await IO_DOMAIN.run(save_xml, encode(transformed), path)
            source_layouts = {
                identity: layout
                for layout in narrative_texts(chapter)
                if isinstance(layout, TextFlowItem)
                and layout.role in {"body", "heading"}
                and (identity := paragraph_identity(chapter, layout)) is not None
            }
            targets = {
                identity: layout
                for layout in narrative_texts(transformed)
                if isinstance(layout, TextFlowItem)
                and (identity := paragraph_identity(transformed, layout)) is not None
            }
            chapter_coverage = []
            for identity in source_layouts:
                target = targets.get(identity)
                state = (
                    "translated"
                    if target is not None and _has_visible_content(target)
                    else "preserved"
                )
                chapter_coverage.append(NarrativeCoverage(*identity, state))
            completed_characters += character_count
            if emit_translation_events and on_translation_event is not None:
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
            return chapter_coverage

        if is_xml_transformer:
            if emit_translation_events and on_translation_event is not None:
                for _, _, item_id, character_count in chapter_tasks:
                    await invoke_callback(on_translation_event, TranslationEvent(
                        kind=TranslationEventKind.ITEM_START,
                        item_kind=TranslationItemKind.CHAPTER,
                        item_id=item_id,
                        item_completed_characters=0,
                        item_total_characters=character_count,
                    ))

            transformed_chapters = await cast(
                NarrativeXMLTransformer, self.chapter_transformer,
            ).transform_many(
                (chapter, item_id)
                for _, chapter, item_id, _ in chapter_tasks
            )
            for task_index, transformed in enumerate(transformed_chapters):
                narrative_coverage.extend(
                    await commit_chapter(task_index, transformed)
                )
        else:
            for task_index, (_, chapter, item_id, character_count) in enumerate(chapter_tasks):
                if emit_translation_events and on_translation_event is not None:
                    await invoke_callback(on_translation_event, TranslationEvent(
                        kind=TranslationEventKind.ITEM_START,
                        item_kind=TranslationItemKind.CHAPTER,
                        item_id=item_id,
                        item_completed_characters=0,
                        item_total_characters=character_count,
                    ))
                transformed = await cast(
                    _ChapterTransformer, self.chapter_transformer,
                ).transform(chapter)
                narrative_coverage.extend(
                    await commit_chapter(task_index, transformed)
                )

        toc_path = output_path / "toc.xml"
        if self.toc_transformer is not None and await IO_DOMAIN.run(toc_path.exists):
            toc = await IO_DOMAIN.run(read_xml, toc_path)
            transformed_toc = await self.toc_transformer(toc)
            await IO_DOMAIN.run(save_xml, transformed_toc, toc_path)

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
        extraction._validate()
        if output_path.exists():
            raise FileExistsError(f"output extraction workspace already exists: {output_path}")
        output_path.mkdir(parents=True)
        _copy_extraction_to_workspace(extraction, output_path)
        tasks = []
        for path in sorted((output_path / "chapters").glob("chapter*.xml")):
            chapter = decode(read_xml(path))
            if isinstance(self.chapter_transformer, NarrativeXMLTransformer):
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
        write_narrative_coverage(output_path / "translation.xml", narrative_coverage)
        return PDFCraftExtraction._from_workspace(output_path)._validate()

class FurnitureExtractionTransformer:
    """Translate only the page-furniture layer of a translated pcex."""

    def __init__(
        self,
        furniture_transformer: FurnitureTransformer,
    ) -> None:
        self.furniture_transformer = furniture_transformer

    async def transform(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        if output_path.suffix.lower() != EXTRACTION_SUFFIX:
            raise ValueError(f"PDFCraftExtraction path must end with {EXTRACTION_SUFFIX}")
        temporary = await IO_DOMAIN.run(
            TemporaryDirectory, prefix="pdf-craft-furnitures-transformed-",
        )
        try:
            transformed = await self._transform_to_workspace_async(
                extraction, Path(temporary.name) / "extraction",
            )
            return await transformed._export_async(output_path)
        finally:
            await IO_DOMAIN.run(temporary.cleanup)

    async def _transform_to_workspace_async(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        def prepare():
            extraction._validate()
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
            PDFCraftExtraction._from_workspace(output_path)._validate,
        )


class AnchoredContentExtractionTransformer:
    """Translate image/table text independently from NarrativeFlow."""

    def __init__(
        self,
        anchored_transformer: AnchoredContentTransformer,
    ) -> None:
        self.anchored_transformer = anchored_transformer

    async def transform(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        if output_path.suffix.lower() != EXTRACTION_SUFFIX:
            raise ValueError(f"PDFCraftExtraction path must end with {EXTRACTION_SUFFIX}")
        temporary = await IO_DOMAIN.run(
            TemporaryDirectory, prefix="pdf-craft-anchored-transformed-",
        )
        try:
            transformed = await self._transform_to_workspace_async(
                extraction, Path(temporary.name) / "extraction",
            )
            return await transformed._export_async(output_path)
        finally:
            await IO_DOMAIN.run(temporary.cleanup)

    async def _transform_to_workspace_async(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
    ) -> PDFCraftExtraction:
        def prepare():
            extraction._validate()
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
            transformer=self.anchored_transformer,
        )
        return await IO_DOMAIN.run(
            PDFCraftExtraction._from_workspace(output_path)._validate,
        )


def _copy_extraction_to_workspace(
    extraction: PDFCraftExtraction,
    output_path: Path,
) -> None:
    with extraction._materialize() as paths:
        copytree(paths.chapters, output_path / "chapters")
        copytree(paths.assets, output_path / "assets")
        if paths.translations.exists():
            copytree(paths.translations, output_path / "translations")
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


def append_translation_layer_to_workspace(
    source: PDFCraftExtraction,
    translated: PDFCraftExtraction,
    output_path: Path,
    *,
    translation_id: str,
    target_language: str,
    include_furniture: bool = False,
    metadata_overlay: dict[str, object] | None = None,
) -> PDFCraftExtraction:
    """Append a translated variant while retaining the source root unchanged."""
    validate_translation_id(translation_id)
    if not target_language.strip():
        raise ValueError("target_language must be a non-empty string")
    source._validate()
    translated._validate()
    if output_path.exists():
        raise FileExistsError(f"output extraction workspace already exists: {output_path}")
    output_path.mkdir(parents=True)
    _copy_extraction_to_workspace(source, output_path)

    translations = output_path / "translations"
    index_path = translations / "index.json"
    if translations.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        translations.mkdir()
        index = {"translations": []}
    if any(entry["id"] == translation_id for entry in index["translations"]):
        raise ValueError(f"translation_id already exists: {translation_id}")

    layer = translations / translation_id
    layer.mkdir()
    with translated._materialize() as translated_paths:
        copytree(translated_paths.chapters, layer / "chapters")
        copy2(translated_paths.translation, layer / "coverage.xml")
        if include_furniture and translated_paths.furnitures.exists():
            copy2(translated_paths.furnitures, layer / "furnitures.xml")
    overlay = dict(metadata_overlay or {})
    overlay["language"] = target_language
    (layer / "metadata.json").write_text(
        json.dumps(overlay, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    index["translations"].append({
        "id": translation_id,
        "target_language": target_language,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = output_path / "manifest.json"
    manifest = _read_manifest(manifest_path)
    if manifest["format_version"] != FORMAT_VERSION:
        manifest = _v3_manifest(manifest)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    return PDFCraftExtraction._from_workspace(output_path)._validate()


def _apply_metadata_overlay_to_workspace(
    workspace: Path,
    overlay: dict[str, object],
) -> PDFCraftExtraction:
    """Apply translated document fields to an internal one-shot workspace."""
    manifest_path = workspace / "manifest.json"
    manifest = _read_manifest(manifest_path)
    manifest["document"] = {**manifest["document"], **overlay}
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return PDFCraftExtraction._from_workspace(workspace)._validate()


def _has_visible_content(layout: TextFlowItem) -> bool:
    """Return whether a transformed PDF paragraph still has drawable content."""
    return any(
        bool(str(item).strip())
        for fragment in layout.children
        if isinstance(fragment, SourceTextFragment)
        for item in fragment.content
    )
