"""Native PDF PageFurniture extraction and compact XML serialization.

The OCR cache tells us which rendered regions already belong to the document
flow. Poppler supplies the complementary native-text geometry. Everything in
this module is deliberately an extraction concern: only the compact,
translation-facing ``furnitures.xml`` leaves this module.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
from xml.etree import ElementTree as ET

from ..common import indent, save_xml


_Fragment = tuple[float, float, float, float, str]
_Box = tuple[int, int, int, int]


@dataclass
class FurnitureSection:
    page_index: int
    det: _Box
    content: str
    associations: list[tuple[str, int, int]] = field(default_factory=list)
    fragments: tuple[_Fragment, ...] = ()


@dataclass
class FurniturePosition:
    id: int
    content: str
    sections: list[FurnitureSection] = field(default_factory=list)


@dataclass
class FurniturePattern:
    id: int
    kind: str
    positions: list[FurniturePosition]


@dataclass
class _Track:
    """One logical Position before it is assigned to a Pattern interval."""

    order: int
    sections: dict[int, FurnitureSection]
    last_page_index: int
    misses: int = 0
    closed: bool = False


def extract_furnitures(pdf_path: Path, ocr_path: Path, *, dpi: int = 300) -> ET.Element:
    """Extract native text not covered by OCR flow boxes.

    Image-only pages have no Poppler text lines, so scanned pages naturally
    produce no furniture. ``dpi`` remains part of the internal API for
    compatibility with the extraction engine; geometry is instead scaled from
    each Poppler page to the actual OCR raster dimensions.
    """
    del dpi
    pages = _native_pages(pdf_path, ocr_path)
    for page_index, sections in pages.items():
        ocr_boxes = _read_ocr_boxes(ocr_path / f"page_{page_index}.xml")
        pages[page_index] = [
            section for section in sections
            if not any(_is_covered(section.det, ocr_box) for ocr_box in ocr_boxes)
        ]

    patterns = _discover_patterns(pages)
    root = ET.Element("furnitures")
    patterns_el = ET.SubElement(root, "patterns")
    for pattern in patterns:
        pattern_el = ET.SubElement(
            patterns_el, "pattern", {"id": str(pattern.id), "kind": pattern.kind}
        )
        for position in pattern.positions:
            ET.SubElement(pattern_el, "position", {"id": str(position.id)}).text = position.content

    pages_el = ET.SubElement(root, "pages")
    for page_index, sections in sorted(pages.items()):
        page_el = ET.SubElement(pages_el, "page", {"index": str(page_index)})
        for section in sections:
            section_el = ET.SubElement(page_el, "section", {"det": ",".join(map(str, section.det))})
            if section.associations:
                for kind, pattern_id, position_id in section.associations:
                    ET.SubElement(
                        section_el,
                        "association",
                        {"kind": kind, "pattern_id": str(pattern_id), "position_id": str(position_id)},
                    )
            else:
                section_el.text = section.content
    return indent(root)


def write_furnitures(pdf_path: Path, ocr_path: Path, destination: Path, *, dpi: int = 300) -> None:
    save_xml(extract_furnitures(pdf_path, ocr_path, dpi=dpi), destination)


def _native_pages(pdf_path: Path, ocr_path: Path) -> dict[int, list[FurnitureSection]]:
    """Read exact Poppler-native text lines in the OCR coordinate space.

    pypdf's visitor callback can lose Form-XObject transforms. In particular it
    returned materially wrong vertical coordinates for ``tag.pdf``. The
    ``pdftotext -bbox-layout`` executable belongs to the Poppler installation
    already required to rasterize PDFs and exposes line/word boxes directly.
    """
    available_pages = _available_ocr_pages(ocr_path)
    page_sizes = _read_page_sizes(ocr_path / "page_pixel_sizes.json")
    try:
        completed = subprocess.run(
            ["pdftotext", "-bbox-layout", str(pdf_path), "-"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        root = ET.fromstring(completed.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, ET.ParseError):
        # Furniture is optional. A PDF page remains usable if Poppler cannot
        # provide native geometry, just as a scanned page has no such output.
        return {index: [] for index in sorted(available_pages)}

    result: dict[int, list[FurnitureSection]] = {}
    page_elements = [element for element in root.iter() if _local_name(element.tag) == "page"]
    for page_index, page_el in enumerate(page_elements, 1):
        if available_pages and page_index not in available_pages:
            continue
        try:
            source_width = float(page_el.get("width", ""))
            source_height = float(page_el.get("height", ""))
        except ValueError:
            result[page_index] = []
            continue
        target_width, target_height = page_sizes.get(
            page_index,
            (round(source_width * 300 / 72), round(source_height * 300 / 72)),
        )
        if source_width <= 0 or source_height <= 0:
            result[page_index] = []
            continue
        x_scale, y_scale = target_width / source_width, target_height / source_height
        sections: list[FurnitureSection] = []
        for line in (element for element in page_el.iter() if _local_name(element.tag) == "line"):
            section = _section_from_poppler_line(
                line, page_index, x_scale, y_scale, target_width, target_height
            )
            if section is not None:
                sections.append(section)
        result[page_index] = sections
    return result


def _section_from_poppler_line(
    line: ET.Element,
    page_index: int,
    x_scale: float,
    y_scale: float,
    page_width: int,
    page_height: int,
) -> FurnitureSection | None:
    words = [element for element in line if _local_name(element.tag) == "word"]
    content = " ".join(" ".join(word.itertext()).strip() for word in words).strip()
    if not content:
        return None
    try:
        box = _scaled_box(line.attrib, x_scale, y_scale, page_width, page_height)
    except (KeyError, ValueError):
        return None
    fragments: list[_Fragment] = []
    left, top, right, bottom = box
    width, height = max(right - left, 1), max(bottom - top, 1)
    for word in words:
        try:
            word_box = _scaled_box(word.attrib, x_scale, y_scale, page_width, page_height)
        except (KeyError, ValueError):
            continue
        word_content = " ".join(word.itertext()).strip()
        if not word_content:
            continue
        word_left, word_top, word_right, word_bottom = word_box
        fragments.append(
            (
                (word_left - left) / width,
                (word_top - top) / height,
                (word_right - word_left) / width,
                (word_bottom - word_top) / height,
                word_content,
            )
        )
    return FurnitureSection(page_index, box, content, fragments=tuple(fragments))


def _scaled_box(
    attributes: dict[str, str], x_scale: float, y_scale: float, page_width: int, page_height: int
) -> _Box:
    left = round(float(attributes["xMin"]) * x_scale)
    top = round(float(attributes["yMin"]) * y_scale)
    right = round(float(attributes["xMax"]) * x_scale)
    bottom = round(float(attributes["yMax"]) * y_scale)
    left, top = max(0, left), max(0, top)
    right, bottom = min(page_width, right), min(page_height, bottom)
    if right <= left or bottom <= top:
        raise ValueError("invalid Poppler text geometry")
    return left, top, right, bottom


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _available_ocr_pages(ocr_path: Path) -> set[int]:
    return {
        int(path.stem.removeprefix("page_"))
        for path in ocr_path.glob("page_*.xml")
        if path.stem.removeprefix("page_").isdigit()
    }


def _read_ocr_boxes(path: Path) -> list[_Box]:
    if not path.exists():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    boxes: list[_Box] = []
    for layout in root.iter("layout"):
        raw = layout.get("det")
        if raw is None:
            continue
        try:
            values = tuple(int(value) for value in raw.split(","))
        except ValueError:
            continue
        if len(values) == 4:
            boxes.append(values)
    return boxes


def _read_page_sizes(path: Path) -> dict[int, tuple[int, int]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {int(index): (int(size[0]), int(size[1])) for index, size in raw.items()}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _is_covered(native: _Box, ocr: _Box) -> bool:
    """Return whether the OCR flow substantially owns a native text line."""
    if _coverage(native, ocr) >= 0.95:
        return True
    left, top = max(native[0], ocr[0]), max(native[1], ocr[1])
    right, bottom = min(native[2], ocr[2]), min(native[3], ocr[3])
    native_width = max(1, native[2] - native[0])
    native_height = max(1, native[3] - native[1])
    # OCR lines/blocks commonly have slightly different horizontal bounds. A
    # near-complete vertical overlap plus meaningful horizontal overlap removes
    # a true flow line, but not a nearby running header.
    return (
        max(0, right - left) / native_width >= 0.3
        and max(0, bottom - top) / native_height >= 0.5
    )


def _coverage(first: _Box, second: _Box) -> float:
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
    return intersection / area if area else 0.0


def _discover_patterns(pages: dict[int, list[FurnitureSection]]) -> list[FurniturePattern]:
    """Discover Position tracks first, then split patterns by their combinations."""
    for sections in pages.values():
        for section in sections:
            section.associations.clear()

    patterns: list[FurniturePattern] = []
    next_pattern_id = 0
    for kind, step in (("universal", 1), ("same_side", 2)):
        # SameSide has two independent time axes. They must not be folded into
        # one odd-page axis merely because the first page happens to be odd.
        residues = range(step)
        for residue in residues:
            axis_pages = {
                page_index: sections
                for page_index, sections in pages.items()
                if page_index % step == residue
            }
            tracks = _discover_tracks(axis_pages, step)
            kind_patterns = _patterns_from_tracks(
                tracks, axis_pages, kind, step, next_pattern_id
            )
            patterns.extend(kind_patterns)
            next_pattern_id += len(kind_patterns)
    return patterns


def _discover_tracks(pages: dict[int, list[FurnitureSection]], step: int) -> list[_Track]:
    if not pages:
        return []
    tracks: list[_Track] = []
    next_order = 0
    for page_index in range(min(pages), max(pages) + 1, step):
        current = pages.get(page_index, [])
        remaining = {id(section): section for section in current}
        # Direct continuation wins over a one-slot recovery. Both branches use
        # the same page-pair matcher, which resolves competing sections before
        # a Position sees them.
        for expected_gap in (step, step * 2):
            active = [
                track for track in tracks
                if not track.closed and track.last_page_index == page_index - expected_gap
            ]
            if not active or not remaining:
                continue
            sources = [track.sections[track.last_page_index] for track in active]
            matches = _match_page_sections(sources, list(remaining.values()))
            track_by_source = {id(track.sections[track.last_page_index]): track for track in active}
            for source, target in matches:
                track = track_by_source[id(source)]
                track.sections[page_index] = target
                track.last_page_index = page_index
                track.misses = 0
                remaining.pop(id(target), None)

        for track in tracks:
            if track.closed or track.last_page_index >= page_index:
                continue
            slots_missed = (page_index - track.last_page_index) // step
            if slots_missed >= 2:
                track.closed = True
                track.misses = 2
            elif slots_missed == 1:
                track.misses = 1

        for section in remaining.values():
            tracks.append(_Track(next_order, {page_index: section}, page_index))
            next_order += 1

    return [track for track in tracks if len(track.sections) >= 3]


def _patterns_from_tracks(
    tracks: list[_Track],
    pages: dict[int, list[FurnitureSection]],
    kind: str,
    step: int,
    first_pattern_id: int,
) -> list[FurniturePattern]:
    if not tracks or not pages:
        return []
    page_indexes = list(range(min(pages), max(pages) + 1, step))
    by_page = {
        page_index: tuple(track.order for track in tracks if page_index in track.sections)
        for page_index in page_indexes
    }
    track_by_order = {track.order: track for track in tracks}
    patterns: list[FurniturePattern] = []
    start = 0
    while start < len(page_indexes):
        combination = by_page[page_indexes[start]]
        end = start + 1
        while end < len(page_indexes) and by_page[page_indexes[end]] == combination:
            end += 1
        if combination:
            positions: list[FurniturePosition] = []
            pattern = FurniturePattern(first_pattern_id + len(patterns), kind, positions)
            for position_id, track_order in enumerate(combination):
                track = track_by_order[track_order]
                instances = [
                    track.sections[page_index]
                    for page_index in page_indexes[start:end]
                    if page_index in track.sections
                ]
                position = FurniturePosition(position_id, _canonical_content(instances), instances)
                positions.append(position)
                for section in instances:
                    section.associations.append((kind, pattern.id, position.id))
            patterns.append(pattern)
        start = end
    return patterns


def _match_page_sections(
    left_sections: list[FurnitureSection], right_sections: list[FurnitureSection]
) -> list[tuple[FurnitureSection, FurnitureSection]]:
    """Match a pair of pages with an origin-relative, one-to-one relation."""
    candidates: list[tuple[FurnitureSection, FurnitureSection, float]] = []
    for left in left_sections:
        for right in right_sections:
            score = _structure_score(left, right)
            if score is not None:
                candidates.append((left, right, score))
    if not candidates:
        return []

    # Legacy SectionMatcher selected a common top-left origin before comparing
    # the rest of the page. Keep that idea: page translation is inferred once,
    # then all other candidate relations are judged in that topology.
    origin_left = min(candidates, key=lambda item: _distance2(item[0].det))[0]
    origin_candidates = [item for item in candidates if item[0] is origin_left]
    origin = min(origin_candidates, key=lambda item: _distance2(item[1].det))
    delta_x = origin[1].det[0] - origin[0].det[0]
    delta_y = origin[1].det[1] - origin[0].det[1]

    ranked: list[tuple[float, FurnitureSection, FurnitureSection]] = []
    for left, right, score in candidates:
        width = max(left.det[2] - left.det[0], right.det[2] - right.det[0])
        height = max(left.det[3] - left.det[1], right.det[3] - right.det[1])
        displacement = abs((right.det[0] - left.det[0]) - delta_x) + abs(
            (right.det[1] - left.det[1]) - delta_y
        )
        tolerance = max(12.0, width * 0.12 + height * 0.12)
        if displacement <= tolerance:
            ranked.append((score - displacement / tolerance, left, right))
    ranked.sort(key=lambda item: item[0], reverse=True)
    used_left: set[int] = set()
    used_right: set[int] = set()
    result: list[tuple[FurnitureSection, FurnitureSection]] = []
    for _score, left, right in ranked:
        if id(left) in used_left or id(right) in used_right:
            continue
        used_left.add(id(left))
        used_right.add(id(right))
        result.append((left, right))
    return result


def _distance2(box: _Box) -> float:
    return float(box[0] * box[0] + box[1] * box[1])


def _structure_score(left: FurnitureSection, right: FurnitureSection) -> float | None:
    left_width, left_height = left.det[2] - left.det[0], left.det[3] - left.det[1]
    right_width, right_height = right.det[2] - right.det[0], right.det[3] - right.det[1]
    width_rate = min(left_width, right_width) / max(left_width, right_width)
    height_rate = min(left_height, right_height) / max(left_height, right_height)
    if width_rate < 0.75 or height_rate < 0.85:
        return None
    if not left.fragments or not right.fragments:
        text_rate = _text_similarity(left.content, right.content)
        return (width_rate + height_rate + text_rate) / 3 if text_rate >= 0.5 else None

    matched = _match_fragments(left.fragments, right.fragments)
    matched_rate = len(matched) / max(len(left.fragments), len(right.fragments))
    if matched_rate <= 0.5:
        return None
    for first_index, (first_left, first_right) in enumerate(matched):
        for second_index, (second_left, second_right) in enumerate(matched):
            if first_index != second_index and _relation(first_left, second_left) != _relation(first_right, second_right):
                return None
    return (width_rate + height_rate + matched_rate) / 3


def _similar(left: FurnitureSection, right: FurnitureSection) -> bool:
    """Compatibility helper for tests and callers of the former private API."""
    return _structure_score(left, right) is not None


def _match_fragments(
    left: tuple[_Fragment, ...], right: tuple[_Fragment, ...]
) -> list[tuple[_Fragment, _Fragment]]:
    unmatched = list(range(len(right)))
    pairs: list[tuple[_Fragment, _Fragment]] = []
    for fragment_left in left:
        if not unmatched:
            break
        candidate = unmatched[0]
        candidate_distance = _fragment_distance(fragment_left, right[candidate])
        for index in unmatched[1:]:
            distance = _fragment_distance(fragment_left, right[index])
            if distance < candidate_distance:
                candidate, candidate_distance = index, distance
        fragment_right = right[candidate]
        if candidate_distance <= 1.1:
            pairs.append((fragment_left, fragment_right))
            unmatched.remove(candidate)
    return pairs


def _fragment_distance(left: _Fragment, right: _Fragment) -> float:
    left_x, left_y, left_width, left_height, left_text = left
    right_x, right_y, right_width, right_height, right_text = right
    return (
        abs(left_x - right_x)
        + abs(left_y - right_y)
        + abs(left_width - right_width)
        + abs(left_height - right_height)
        + (0.01 if left_text != right_text else 0.0)
    )


def _relation(first: _Fragment, second: _Fragment) -> str:
    first_x, first_y, first_width, first_height, _ = first
    second_x, second_y, second_width, second_height, _ = second
    overlap_x = min(first_x + first_width, second_x + second_width) - max(first_x, second_x)
    overlap_y = min(first_y + first_height, second_y + second_height) - max(first_y, second_y)
    if overlap_x > 0 and overlap_y > 0:
        return "overlap"
    if abs((first_y + first_height / 2) - (second_y + second_height / 2)) <= 0.2:
        return "left" if first_x < second_x else "right"
    return "above" if first_y < second_y else "below"


def _canonical_content(sections: list[FurnitureSection]) -> str:
    counts = Counter(_normalize_content(section.content) for section in sections)
    first_seen: dict[str, int] = {}
    for index, section in enumerate(sections):
        first_seen.setdefault(_normalize_content(section.content), index)
    canonical = max(counts, key=lambda text: (counts[text], -first_seen[text]))
    return next(section.content for section in sections if _normalize_content(section.content) == canonical)


def _normalize_content(content: str) -> str:
    return " ".join(content.split())


def _text_similarity(left: str, right: str) -> float:
    left_words, right_words = left.split(), right.split()
    if not left_words or not right_words:
        return 0.0
    return sum(word in right_words for word in left_words) / max(len(left_words), len(right_words))
