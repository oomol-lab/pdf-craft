"""Validated source/translation views shared by document renderers."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator
from xml.etree import ElementTree

from ..common import save_xml
from .package import PDFCraftExtraction, TranslationInfo, _book_meta, _read_translation_index


class RenderMode(str, Enum):
    """Select which PCEX document layers are visible in rendered output."""

    SOURCE = "source"
    REPLACE = "replace"
    BILINGUAL = "bilingual"


@dataclass(frozen=True)
class RenderView:
    """Materialized paths and metadata for one validated rendering choice."""

    chapters: Path
    assets: Path
    toc: Path
    cover: Path
    metadata: dict[str, Any]
    language: str | None
    translation: TranslationInfo | None

    def book_meta(self):
        """Convert the effective PCEX metadata to epub-generator metadata."""
        return _book_meta(self.metadata)


def resolve_translation(
    extraction: PDFCraftExtraction,
    translation_id: str | None = None,
) -> TranslationInfo:
    """Resolve an explicit translation ID or the stable index default."""
    translations = extraction._translations()  # pylint: disable=protected-access
    return _select_translation(translations, translation_id)


def _select_translation(
    translations: tuple[TranslationInfo, ...], translation_id: str | None,
) -> TranslationInfo:
    if translation_id is None:
        if not translations:
            raise ValueError("PCEX has no translations")
        return translations[0]
    for translation in translations:
        if translation.id == translation_id:
            return translation
    raise ValueError(f"PCEX has no translation with id: {translation_id}")


@contextmanager
def materialize_render_view(
    extraction: PDFCraftExtraction,
    mode: RenderMode = RenderMode.SOURCE,
    translation_id: str | None = None,
    *,
    require_toc: bool = False,
) -> Iterator[RenderView]:
    """Yield one validated document-level view for Markdown or EPUB."""
    extraction._validate(require_toc=require_toc)  # pylint: disable=protected-access
    if mode == RenderMode.SOURCE and translation_id is not None:
        raise ValueError("translation_id is only valid for replace or bilingual rendering")

    with extraction._materialize() as paths:  # pylint: disable=protected-access
        source_metadata = json.loads(paths.manifest.read_text(encoding="utf-8"))["document"]
        if mode == RenderMode.SOURCE:
            yield RenderView(
                paths.chapters, paths.assets, paths.toc, paths.cover,
                source_metadata, _language(source_metadata), None,
            )
            return

        translation = _select_translation(
            _read_translation_index(paths.translations), translation_id,
        )
        layer = paths.translations / translation.id
        overlay = _read_metadata_overlay(layer / "metadata.json")
        metadata = _effective_metadata(source_metadata, overlay, mode)
        language = _language(metadata) or translation.target_language
        if mode == RenderMode.REPLACE:
            yield RenderView(
                layer / "chapters", paths.assets, paths.toc, paths.cover,
                metadata, language, translation,
            )
            return

        if mode != RenderMode.BILINGUAL:
            raise ValueError(f"unsupported render mode: {mode}")
        with TemporaryDirectory(prefix="pdf-craft-render-") as directory:
            chapters = Path(directory) / "chapters"
            chapters.mkdir()
            _write_bilingual_chapters(
                paths.chapters, layer / "chapters", layer / "coverage.xml", chapters,
            )
            yield RenderView(
                chapters, paths.assets, paths.toc, paths.cover,
                metadata, language, translation,
            )


def _read_metadata_overlay(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _effective_metadata(
    source: dict[str, Any], overlay: dict[str, Any], mode: RenderMode,
) -> dict[str, Any]:
    if mode == RenderMode.REPLACE:
        return {**source, **overlay}
    result = dict(source)
    for key, translated in overlay.items():
        if key == "language":
            result[key] = translated
        elif key == "subjects" and isinstance(translated, list):
            result[key] = _combine_lists(source.get(key), translated)
        elif key in {
            "title", "original_title", "description", "publisher", "edition", "rights",
        }:
            result[key] = _combine_text(source.get(key), translated)
        else:
            result[key] = translated
    return result


def _combine_lists(source: Any, translated: list[Any]) -> list[Any]:
    originals = source if isinstance(source, list) else []
    return [
        _combine_text(originals[index] if index < len(originals) else None, value)
        for index, value in enumerate(translated)
    ] + originals[len(translated):]


def _combine_text(source: Any, translated: Any) -> Any:
    if not isinstance(translated, str) or not translated.strip():
        return source
    if not isinstance(source, str) or not source.strip():
        return translated
    if source == translated:
        return source
    return f"{source} {translated}"


def _language(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("language")
    return value if isinstance(value, str) and value.strip() else None


def _write_bilingual_chapters(
    source_path: Path, translated_path: Path, coverage_path: Path, output_path: Path,
) -> None:
    translated_identities = _translated_narrative_identities(coverage_path)
    for source_file in source_path.glob("*.xml"):
        source = ElementTree.parse(source_file).getroot()
        translated = ElementTree.parse(translated_path / source_file.name).getroot()
        _merge_chapter(source, translated, translated_identities)
        save_xml(source, output_path / source_file.name)


def _translated_narrative_identities(path: Path) -> set[tuple[str, int, int]]:
    root = ElementTree.parse(path).getroot()
    result: set[tuple[str, int, int]] = set()
    for entry in root.findall("narrative/paragraph"):
        if entry.get("state") == "translated":
            result.add((
                entry.get("chapter_id", ""),
                int(entry.get("page_index", "0")),
                int(entry.get("order", "0")),
            ))
    return result


def _merge_chapter(
    source: ElementTree.Element,
    translated: ElementTree.Element,
    translated_identities: set[tuple[str, int, int]],
) -> None:
    chapter_id = source.get("id", "head")
    source_flow = source.find("flow")
    translated_flow = translated.find("flow")
    if source_flow is not None and translated_flow is not None:
        targets = {
            identity: item
            for item in translated_flow
            if (identity := _text_identity(item)) is not None
        }
        _merge_flow(
            source_flow, targets,
            lambda identity, _source, _target: (
                (chapter_id, identity[0], identity[1]) in translated_identities
            ),
        )
    _merge_references(source, translated)


def _merge_references(source: ElementTree.Element, translated: ElementTree.Element) -> None:
    source_references = source.find("references")
    translated_references = translated.find("references")
    if source_references is None or translated_references is None:
        return
    targets = {reference.get("id", ""): reference for reference in translated_references}
    for reference in source_references:
        target = targets.get(reference.get("id", ""))
        if target is None:
            continue
        source_flow = reference.find("flow")
        target_flow = target.find("flow")
        if source_flow is None or target_flow is None:
            continue
        target_items = {
            identity: item
            for item in target_flow
            if (identity := _text_identity(item)) is not None
        }
        _merge_flow(
            source_flow, target_items,
            lambda _identity, source_item, target_item: (
                _visible_text(source_item) != _visible_text(target_item)
            ),
        )


def _merge_flow(source_flow, targets, should_append) -> None:
    merged: list[ElementTree.Element] = []
    for source_item in list(source_flow):
        merged.append(source_item)
        identity = _text_identity(source_item)
        target = targets.get(identity) if identity is not None else None
        if target is None or not should_append(identity, source_item, target):
            continue
        if source_item.get("role") == "heading":
            _append_heading(source_item, target)
        else:
            target_copy = deepcopy(target)
            for asset in list(target_copy.findall("asset")):
                target_copy.remove(asset)
            if _visible_text(target_copy):
                merged.append(target_copy)
    source_flow[:] = merged


def _text_identity(item: ElementTree.Element) -> tuple[int, int] | None:
    if item.tag != "text":
        return None
    fragment = item.find("fragment")
    if fragment is None:
        return None
    return int(fragment.get("page_index", "0")), int(fragment.get("source_order", "0"))


def _append_heading(source: ElementTree.Element, translated: ElementTree.Element) -> None:
    targets = {
        (fragment.get("page_index"), fragment.get("source_order")): fragment
        for fragment in translated.findall("fragment")
    }
    for fragment in source.findall("fragment"):
        target = targets.get((fragment.get("page_index"), fragment.get("source_order")))
        if target is not None and _visible_text(target):
            _append_element_content(fragment, target)


def _append_element_content(
    destination: ElementTree.Element, source: ElementTree.Element,
) -> None:
    _append_text(destination, " ")
    _append_text(destination, source.text or "")
    for child in source:
        destination.append(deepcopy(child))


def _append_text(element: ElementTree.Element, value: str) -> None:
    if not value:
        return
    if len(element):
        element[-1].tail = (element[-1].tail or "") + value
    else:
        element.text = (element.text or "") + value


def _visible_text(element: ElementTree.Element) -> str:
    return "".join(element.itertext()).strip()
