"""Translation coverage sidecar shared by pcex translation stages and PDF patching.

The source chapter and furniture XML remain useful even when a transformer
declines a unit.  Coverage is consequently explicit rather than inferred from
source/target text equality: an absent entry is always treated as preserved.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

from pdf_craft.common import indent, read_xml, save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, SourceAsset, SourceTextFragment, StandaloneAsset, TextFlowItem,
)


CoverageState = str


@dataclass(frozen=True)
class NarrativeCoverage:
    """One PDF-patchable Narrative paragraph's stable source identity."""

    chapter_id: str
    page_index: int
    order: int
    state: CoverageState


@dataclass(frozen=True)
class FurniturePositionCoverage:
    pattern_id: str
    position_id: str
    state: CoverageState


@dataclass(frozen=True)
class FurnitureSectionCoverage:
    page_index: int
    det: str
    state: CoverageState


@dataclass(frozen=True)
class AnchoredContentCoverage:
    """One image/table text payload's stable FlowItem identity."""

    chapter_id: str
    flow_index: int
    child_index: int
    state: CoverageState


@dataclass(frozen=True)
class TranslationCoverage:
    """A parsed sidecar. Missing entries intentionally mean ``preserved``."""

    narrative: dict[tuple[str, int, int], CoverageState]
    positions: dict[tuple[str, str], CoverageState]
    sections: dict[tuple[int, str], CoverageState]
    anchored: dict[tuple[str, int, int], CoverageState]


def paragraph_identity(chapter: Chapter, item: TextFlowItem) -> tuple[str, int, int] | None:
    """Return the first source-fragment identity used by PDF patching.

    A text flow may span several boxes/pages, but its first source fragment is
    a durable primary key through XML translation. A flow without source text
    has no PDF geometry and therefore no coverage record.
    """
    fragment = next((child for child in item.children if isinstance(child, SourceTextFragment)), None)
    if fragment is None:
        return None
    return (str(chapter.id) if chapter.id is not None else "head", fragment.page_index, fragment.source_order)


def anchored_content_identities(chapter: Chapter) -> set[tuple[str, int, int]]:
    """Return every v3 image/table slot eligible for independent translation."""
    chapter_id = str(chapter.id) if chapter.id is not None else "head"
    identities: set[tuple[str, int, int]] = set()
    for flow_index, item in enumerate(chapter.flow_items):
        if isinstance(item, TextFlowItem):
            identities.update(
                (chapter_id, flow_index, child_index)
                for child_index, child in enumerate(item.children)
                if isinstance(child, SourceAsset) and child.ref in {"image", "table"}
            )
        elif isinstance(item, StandaloneAsset) and item.asset.ref in {"image", "table"}:
            identities.add((chapter_id, flow_index, -1))
    return identities


def read_coverage(path: Path) -> TranslationCoverage:
    if not path.exists():
        return TranslationCoverage({}, {}, {}, {})
    root = read_xml(path)
    narrative: dict[tuple[str, int, int], CoverageState] = {}
    positions: dict[tuple[str, str], CoverageState] = {}
    sections: dict[tuple[int, str], CoverageState] = {}
    anchored: dict[tuple[str, int, int], CoverageState] = {}
    for entry in root.findall("narrative/paragraph"):
        narrative[(entry.get("chapter_id", ""), int(entry.get("page_index", "0")), int(entry.get("order", "0")))] = entry.get("state", "preserved")
    for entry in root.findall("furnitures/position"):
        positions[(entry.get("pattern_id", ""), entry.get("position_id", ""))] = entry.get("state", "preserved")
    for entry in root.findall("furnitures/section"):
        sections[(int(entry.get("page_index", "0")), entry.get("det", ""))] = entry.get("state", "preserved")
    for entry in root.findall("anchored/asset"):
        anchored[(
            entry.get("chapter_id", ""),
            int(entry.get("flow_index", "0")),
            int(entry.get("child_index", "0")),
        )] = entry.get("state", "preserved")
    return TranslationCoverage(narrative, positions, sections, anchored)


def write_narrative_coverage(path: Path, entries: Iterable[NarrativeCoverage]) -> None:
    root = _load_or_create(path)
    _replace_child(root, "narrative", _narrative_element(entries), before="furnitures")
    _save(root, path)


def write_furniture_coverage(
    path: Path,
    positions: Iterable[FurniturePositionCoverage],
    sections: Iterable[FurnitureSectionCoverage],
) -> None:
    root = _load_or_create(path)
    _replace_child(root, "furnitures", _furniture_element(positions, sections))
    _save(root, path)


def write_anchored_coverage(
    path: Path,
    entries: Iterable[AnchoredContentCoverage],
) -> None:
    root = _load_or_create(path)
    _replace_child(root, "anchored", _anchored_element(entries))
    _save(root, path)


def _load_or_create(path: Path) -> Element:
    return read_xml(path) if path.exists() else Element("translation")


def _replace_child(root: Element, tag: str, replacement: Element, *, before: str | None = None) -> None:
    existing = root.find(tag)
    if existing is not None:
        root.remove(existing)
    if before is not None:
        sibling = root.find(before)
        if sibling is not None:
            root.insert(list(root).index(sibling), replacement)
            return
    root.append(replacement)


def _narrative_element(entries: Iterable[NarrativeCoverage]) -> Element:
    narrative = Element("narrative")
    for entry in entries:
        SubElement(narrative, "paragraph", {
            "chapter_id": entry.chapter_id,
            "page_index": str(entry.page_index),
            "order": str(entry.order),
            "state": entry.state,
        })
    return narrative


def _furniture_element(
    positions: Iterable[FurniturePositionCoverage],
    sections: Iterable[FurnitureSectionCoverage],
) -> Element:
    furniture = Element("furnitures")
    for entry in positions:
        SubElement(furniture, "position", {
            "pattern_id": entry.pattern_id,
            "position_id": entry.position_id,
            "state": entry.state,
        })
    for entry in sections:
        SubElement(furniture, "section", {
            "page_index": str(entry.page_index),
            "det": entry.det,
            "state": entry.state,
        })
    return furniture


def _anchored_element(entries: Iterable[AnchoredContentCoverage]) -> Element:
    anchored = Element("anchored")
    for entry in entries:
        SubElement(anchored, "asset", {
            "chapter_id": entry.chapter_id,
            "flow_index": str(entry.flow_index),
            "child_index": str(entry.child_index),
            "state": entry.state,
        })
    return anchored


def _save(root: Element, path: Path) -> None:
    if root.tag != "translation":
        raise ValueError("translation.xml root must be <translation>")
    save_xml(indent(root), path)
