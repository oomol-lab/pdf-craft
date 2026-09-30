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
from .package import (
    PDFCraftExtraction, TranslationInfo, _book_meta, _read_translation_index,
    validate_translation_id,
)


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


@dataclass(frozen=True)
class _PDFPatchView:
    """Validated paths for patching the source or one stored translation."""

    chapters: Path
    coverage: Path
    furnitures: Path
    metadata: dict[str, Any]


@contextmanager
def materialize_pdf_patch_view(
    extraction: PDFCraftExtraction,
    translation_id: str | None = None,
) -> Iterator[_PDFPatchView]:
    """Select the exact PCEX content consumed by PDF patching.

    Omitting ``translation_id`` selects the current PCEX root content. An explicit ID
    selects that translation's chapters, coverage, and metadata while falling
    back to source furniture when the translation did not replace it.
    """
    extraction._validate()  # pylint: disable=protected-access
    with extraction._materialize() as paths:  # pylint: disable=protected-access
        source_metadata = json.loads(
            paths.manifest.read_text(encoding="utf-8")
        )["document"]
        if translation_id is None:
            yield _PDFPatchView(
                paths.chapters, paths.translation, paths.furnitures,
                source_metadata,
            )
            return

        translation = _select_translation(
            _read_translation_index(paths.translations), translation_id,
        )
        translation_path = paths.translations / translation.id
        overlay = _read_metadata_overlay(translation_path / "metadata.json")
        translated_furniture = translation_path / "furnitures.xml"
        yield _PDFPatchView(
            translation_path / "chapters",
            translation_path / "coverage.xml",
            translated_furniture if translated_furniture.exists() else paths.furnitures,
            _effective_metadata(source_metadata, overlay, RenderMode.REPLACE),
        )


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
    validate_translation_id(translation_id)
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
        if mode not in {RenderMode.REPLACE, RenderMode.BILINGUAL}:
            raise ValueError(f"unsupported render mode: {mode}")
        with TemporaryDirectory(prefix="pdf-craft-render-") as directory:
            chapters = Path(directory) / "chapters"
            chapters.mkdir()
            _write_effective_chapters(
                paths.chapters, layer / "chapters", layer / "coverage.xml",
                chapters, mode,
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


def _write_effective_chapters(
    source_path: Path, translated_path: Path, coverage_path: Path, output_path: Path,
    mode: RenderMode,
) -> None:
    translated_identities = _translated_narrative_identities(coverage_path)
    translated_assets = _translated_anchored_identities(coverage_path)
    for source_file in source_path.glob("*.xml"):
        source = ElementTree.parse(source_file).getroot()
        translated = ElementTree.parse(translated_path / source_file.name).getroot()
        _compose_chapter(
            source, translated, translated_identities, translated_assets, mode,
        )
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


def _translated_anchored_identities(path: Path) -> set[tuple[str, int, int]]:
    root = ElementTree.parse(path).getroot()
    return {
        (
            entry.get("chapter_id", ""),
            int(entry.get("flow_index", "0")),
            int(entry.get("child_index", "0")),
        )
        for entry in root.findall("anchored/asset")
        if entry.get("state") == "translated"
    }


def _compose_chapter(
    source: ElementTree.Element,
    translated: ElementTree.Element,
    translated_identities: set[tuple[str, int, int]],
    translated_assets: set[tuple[str, int, int]],
    mode: RenderMode,
) -> None:
    chapter_id = source.get("id", "head")
    source_flow = source.find("flow")
    translated_flow = translated.find("flow")
    if source_flow is not None and translated_flow is not None:
        targets = _flow_targets(translated_flow)
        is_translated = lambda identity: (
            identity[0] == "display-formula"
            or (chapter_id, identity[0], identity[1]) in translated_identities
        )
        if mode == RenderMode.REPLACE:
            _replace_flow(source_flow, targets, is_translated)
        else:
            _merge_flow(source_flow, targets, is_translated)
        _compose_anchored_assets(
            source_flow, translated_flow, chapter_id, translated_assets, mode,
        )
    _compose_references(source, translated, mode)


def _compose_anchored_assets(
    effective_flow: ElementTree.Element,
    translated_flow: ElementTree.Element,
    chapter_id: str,
    translated_assets: set[tuple[str, int, int]],
    mode: RenderMode,
) -> None:
    """Compose image/table text independently from narrative coverage."""
    for flow_index, (effective_item, translated_item) in enumerate(zip(
        effective_flow, translated_flow, strict=False,
    )):
        if effective_item.tag == "standalone-asset":
            if (chapter_id, flow_index, -1) in translated_assets:
                _compose_asset_element(
                    effective_item.find("asset"), translated_item.find("asset"), mode,
                )
            continue
        if effective_item.tag != "text" or translated_item.tag != "text":
            continue
        for child_index, (effective_child, translated_child) in enumerate(zip(
            effective_item, translated_item, strict=False,
        )):
            if (chapter_id, flow_index, child_index) not in translated_assets:
                continue
            if effective_child.tag == "asset" and translated_child.tag == "asset":
                _compose_asset_element(effective_child, translated_child, mode)


def _compose_asset_element(
    effective: ElementTree.Element | None,
    translated: ElementTree.Element | None,
    mode: RenderMode,
) -> None:
    if effective is None or translated is None:
        return
    for name in ("title", "content", "caption"):
        target = translated.find(name)
        if target is None or not _visible_text(target):
            continue
        destination = effective.find(name)
        if mode == RenderMode.REPLACE:
            copy = deepcopy(target)
            if destination is None:
                effective.append(copy)
            else:
                effective[list(effective).index(destination)] = copy
        elif destination is None:
            effective.append(deepcopy(target))
        else:
            _append_element_content(destination, target)


def _compose_references(
    source: ElementTree.Element, translated: ElementTree.Element, mode: RenderMode,
) -> None:
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
        target_items = _flow_targets(target_flow)
        # References have no coverage-sidecar entries. Their established ref
        # and fragment identities locate the target; non-empty target content
        # is availability, even when its visible text equals the source.
        available_targets = {
            identity for identity, item in target_items.items() if _visible_text(item)
        }
        has_target = available_targets.__contains__
        if mode == RenderMode.REPLACE:
            _replace_flow(source_flow, target_items, has_target)
        else:
            _merge_flow(source_flow, target_items, has_target)


def _replace_flow(source_flow, targets, should_replace) -> None:
    effective: list[ElementTree.Element] = []
    for index, source_item in enumerate(source_flow):
        identity = _flow_identity(source_item, index)
        target = targets.get(identity) if identity is not None else None
        if (
            identity is not None
            and target is not None
            and should_replace(identity)
            and _visible_text(target)
        ):
            effective.append(deepcopy(target))
        else:
            effective.append(source_item)
    source_flow[:] = effective


def _merge_flow(source_flow, targets, should_append) -> None:
    merged: list[ElementTree.Element] = []
    for index, source_item in enumerate(list(source_flow)):
        merged.append(source_item)
        identity = _flow_identity(source_item, index)
        target = targets.get(identity) if identity is not None else None
        if target is None or not should_append(identity) or not _visible_text(target):
            continue
        if source_item.tag == "display-formula":
            _merge_display_formula(source_item, target)
        elif source_item.get("role") == "heading":
            _append_heading(source_item, target)
        else:
            target_copy = deepcopy(target)
            for asset in list(target_copy.findall("asset")):
                target_copy.remove(asset)
            if _visible_text(target_copy):
                merged.append(target_copy)
    source_flow[:] = merged


def _flow_targets(flow: ElementTree.Element) -> dict[tuple[Any, ...], ElementTree.Element]:
    return {
        identity: item
        for index, item in enumerate(flow)
        if (identity := _flow_identity(item, index)) is not None
    }


def _flow_identity(
    item: ElementTree.Element, index: int,
) -> tuple[Any, ...] | None:
    if item.tag == "display-formula":
        # Translation-layer validation guarantees identical ordered flow
        # structure and formula geometry within the chapter/reference scope.
        return ("display-formula", index)
    return _text_identity(item)


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
    translated_fragments: list[ElementTree.Element] = []
    for fragment in source.findall("fragment"):
        target = targets.get((fragment.get("page_index"), fragment.get("source_order")))
        if target is not None and _visible_text(target):
            translated_fragments.append(deepcopy(target))
    if translated_fragments:
        first = translated_fragments[0]
        first.text = " " + (first.text or "")
        source.extend(translated_fragments)


def _merge_display_formula(
    source: ElementTree.Element, translated: ElementTree.Element,
) -> None:
    source_asset = source.find("asset")
    translated_asset = translated.find("asset")
    if source_asset is None or translated_asset is None:
        return
    for name in ("title", "caption"):
        target = translated_asset.find(name)
        if target is None or not _visible_text(target):
            continue
        destination = source_asset.find(name)
        if destination is not None:
            _append_element_content(destination, target)
            continue
        copy = deepcopy(target)
        if name == "title":
            source_asset.insert(0, copy)
        else:
            source_asset.append(copy)


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
