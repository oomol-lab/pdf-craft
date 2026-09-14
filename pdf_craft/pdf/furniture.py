"""Native PDF PageFurniture extraction and compact XML serialization."""
from dataclasses import dataclass, field
import json
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from ..common import indent, save_xml


@dataclass
class FurnitureSection:
    page_index: int
    det: tuple[int, int, int, int]
    content: str
    associations: list[tuple[str, int, int]] = field(default_factory=list)
    fragments: tuple[tuple[float, float, float, float, str], ...] = ()


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


def extract_furnitures(pdf_path: Path, ocr_path: Path, *, dpi: int = 300) -> ET.Element:
    """Extract native text not covered by OCR flow bboxes.

    Image-only pages naturally produce no native blocks and therefore no furniture.
    Pattern discovery is deliberately conservative: a position requires the same
    normalized geometry/content on three pages in either page-step space.
    """
    pages: dict[int, list[FurnitureSection]] = {}
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    available_pages = {
        int(path.stem.removeprefix("page_"))
        for path in ocr_path.glob("page_*.xml")
        if path.stem.removeprefix("page_").isdigit()
    }
    page_sizes = _read_page_sizes(ocr_path / "page_pixel_sizes.json")
    for page_index, page in enumerate(reader.pages, 1):
        if available_pages and page_index not in available_pages:
            continue
        native = _native_sections(page, page_index, dpi, page_sizes.get(page_index))
        ocr_boxes = _read_ocr_boxes(ocr_path / f"page_{page_index}.xml")
        pages[page_index] = [s for s in native if not any(_is_covered(s.det, box) for box in ocr_boxes)]

    patterns = _discover_patterns(pages)
    root = ET.Element("furnitures")
    patterns_el = ET.SubElement(root, "patterns")
    for pattern in patterns:
        p_el = ET.SubElement(patterns_el, "pattern", {"id": str(pattern.id), "kind": pattern.kind})
        for position in pattern.positions:
            ET.SubElement(p_el, "position", {"id": str(position.id)}).text = position.content
    pages_el = ET.SubElement(root, "pages")
    for page_index, sections in sorted(pages.items()):
        page_el = ET.SubElement(pages_el, "page", {"index": str(page_index)})
        for section in sections:
            attrs = {"det": ",".join(map(str, section.det))}
            if section.associations:
                section_el = ET.SubElement(page_el, "section", attrs)
                for kind, pattern_id, position_id in section.associations:
                    ET.SubElement(section_el, "association", {"kind": kind,
                        "pattern_id": str(pattern_id), "position_id": str(position_id)})
            else:
                ET.SubElement(page_el, "section", attrs).text = section.content
    return indent(root)


def write_furnitures(pdf_path: Path, ocr_path: Path, destination: Path, *, dpi: int = 300) -> None:
    save_xml(extract_furnitures(pdf_path, ocr_path, dpi=dpi), destination)


def _read_ocr_boxes(path: Path) -> list[tuple[int, int, int, int]]:
    if not path.exists():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    boxes = []
    for layout in root.iter("layout"):
        raw = layout.get("det")
        if raw:
            try:
                values = tuple(int(v) for v in raw.split(","))
                if len(values) == 4:
                    boxes.append(values)
            except ValueError:
                continue
    return boxes


def _read_page_sizes(path: Path) -> dict[int, tuple[int, int]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {int(index): (int(size[0]), int(size[1])) for index, size in raw.items()}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _native_sections(page, page_index: int, dpi: int, page_size: tuple[int, int] | None) -> list[FurnitureSection]:
    """Read native PDF glyph runs with pypdf's visitor API.

    PDFs do not always expose glyph advance widths.  A conservative font-size
    based width estimate is sufficient here because OCR boxes are only an
    exclusion mask; the native text itself remains the authoritative content.
    """
    sections: list[FurnitureSection] = []
    scale = dpi / 72.0
    height = float(page.mediabox.height)

    def visit(text, _cm, tm, _font, font_size):
        raw_text = str(text)
        content = " ".join(raw_text.split())
        if not content or float(font_size or 0) <= 0:
            return
        x, y = float(tm[4]), float(tm[5])
        size = float(font_size)
        width = max(size, len(content) * size * 0.55)
        # PDF coordinates start at bottom-left; OCR raster coordinates at top-left.
        det = (round(x * scale), round((height - y - size) * scale),
               round((x + width) * scale), round((height - y) * scale))
        if page_size is not None:
            max_width, max_height = page_size
            det = (max(0, min(det[0], max_width - 1)), max(0, min(det[1], max_height - 1)),
                   max(1, min(det[2], max_width)), max(1, min(det[3], max_height)))
        if det[2] <= det[0] or det[3] <= det[1]:
            return
        sections.append(FurnitureSection(page_index, det, content,
                                         fragments=_fragments(raw_text)))

    page.extract_text(visitor_text=visit)
    return sections


def _coverage(a, b) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    area = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    return intersection / area if area else 0.0


def _is_covered(native, ocr) -> bool:
    """Tolerate PDF glyph-width differences without accepting nearby furniture."""
    if _coverage(native, ocr) >= 0.95:
        return True
    left, top = max(native[0], ocr[0]), max(native[1], ocr[1])
    right, bottom = min(native[2], ocr[2]), min(native[3], ocr[3])
    native_width = max(1, native[2] - native[0])
    native_height = max(1, native[3] - native[1])
    return (max(0, right - left) / native_width >= 0.3
            and max(0, bottom - top) / native_height >= 0.9)


def _discover_patterns(pages: dict[int, list[FurnitureSection]]) -> list[FurniturePattern]:
    patterns: list[FurniturePattern] = []
    next_pattern = 0
    for kind, step in (("universal", 1), ("same_side", 2)):
        max_page = max(pages, default=0)
        claimed: set[int] = set()
        for start in range(1, max_page + 1):
            if not pages.get(start):
                continue
            used: set[int] = set()
            positions: list[FurniturePosition] = []
            for first in pages[start]:
                if id(first) in claimed:
                    continue
                matches = _initial_matches(first, start, pages, step, max_page,
                                           claimed | used)
                if len(matches) < 3:
                    continue
                _extend_position(matches, pages, step, max_page, claimed | used)
                counts = Counter(s.content for s in matches)
                first_seen = {}
                for index, section in enumerate(matches):
                    first_seen.setdefault(section.content, index)
                canonical = max(counts, key=lambda text: (counts[text], -first_seen[text]))
                position = FurniturePosition(len(positions), canonical, matches)
                positions.append(position)
                used.update(id(s) for s in matches)
            if positions:
                pattern = FurniturePattern(next_pattern, kind, positions)
                patterns.append(pattern)
                for position in positions:
                    for section in position.sections:
                        section.associations.append((kind, pattern.id, position.id))
                        claimed.add(id(section))
                next_pattern += 1
    return patterns


def _initial_matches(first: FurnitureSection, start: int,
                     pages: dict[int, list[FurnitureSection]], step: int,
                     max_page: int, unavailable: set[int]) -> list[FurnitureSection]:
    """Build a candidate track before pattern creation.

    A single absent section is tolerated while discovering a position. Two
    consecutive misses terminate the candidate; at least three real sections
    are still required before it becomes a pattern position.
    """
    matches = [first]
    previous = first
    misses = 0
    page_index = start + step
    while page_index <= max_page and misses < 2:
        candidate = next((section for section in pages.get(page_index, [])
                          if id(section) not in unavailable
                          and _similar(previous, section)), None)
        if candidate is None:
            misses += 1
        else:
            matches.append(candidate)
            previous = candidate
            misses = 0
        page_index += step
    return matches


def _similar(a: FurnitureSection, b: FurnitureSection) -> bool:
    aw, ah = a.det[2] - a.det[0], a.det[3] - a.det[1]
    bw, bh = b.det[2] - b.det[0], b.det[3] - b.det[1]
    if min(aw, bw) / max(aw, bw) < 0.75 or min(ah, bh) / max(ah, bh) < 0.85:
        return False
    # Compare normalized outer rectangles, allowing scan translation but not
    # accepting a different topology merely because width/height are similar.
    center_a = ((a.det[0] + a.det[2]) / 2, (a.det[1] + a.det[3]) / 2)
    center_b = ((b.det[0] + b.det[2]) / 2, (b.det[1] + b.det[3]) / 2)
    if abs(center_a[0] - center_b[0]) > max(aw, bw) * 0.3 + 100:
        return False
    if abs(center_a[1] - center_b[1]) > max(ah, bh) * 0.3 + 100:
        return False
    fragments_a, fragments_b = a.fragments, b.fragments
    if not fragments_a or not fragments_b:
        return _text_similarity(a.content, b.content) >= 0.5
    # Match token identity and normalized geometry, then compare the
    # topological relations between fragments.  This prevents two sections
    # with similar outer boxes from being associated when their internal
    # layout differs (for example, a two-column header vs a single line).
    def normalize(fragments):
        min_x = min(fragment[0] for fragment in fragments)
        min_y = min(fragment[1] for fragment in fragments)
        max_x = max(fragment[0] + fragment[2] for fragment in fragments)
        max_y = max(fragment[1] + fragment[3] for fragment in fragments)
        width = max(max_x - min_x, 1e-9)
        height = max(max_y - min_y, 1e-9)
        return tuple((
            (x - min_x) / width, (y - min_y) / height,
            w / width, h / height, text,
        ) for x, y, w, h, text in fragments)

    left, right = normalize(fragments_a), normalize(fragments_b)
    unmatched = list(range(len(right)))
    matched_pairs = []
    for fragment_a in left:
        # Geometry is authoritative here.  Headers often contain changing
        # dates/page numbers, so token identity must not prevent association.
        candidates = list(unmatched)
        if not candidates:
            continue
        index = min(candidates, key=lambda candidate: (
            abs(right[candidate][0] - fragment_a[0])
            + abs(right[candidate][1] - fragment_a[1])
            + abs(right[candidate][2] - fragment_a[2])
            + abs(right[candidate][3] - fragment_a[3])
            # Text is only a tie-breaker; changing content remains valid.
            + (0.01 if right[candidate][4] != fragment_a[4] else 0.0)))
        fragment_b = right[index]
        if (abs(fragment_a[0] - fragment_b[0]) <= 0.25
                and abs(fragment_a[1] - fragment_b[1]) <= 0.25
                and abs(fragment_a[2] - fragment_b[2]) <= 0.3
                and abs(fragment_a[3] - fragment_b[3]) <= 0.3):
            matched_pairs.append((fragment_a, fragment_b))
            unmatched.remove(index)
    if len(matched_pairs) / max(len(left), len(right)) < 0.5:
        return False

    def relation(first, second):
        fx, fy, fw, fh, _ = first
        sx, sy, sw, sh, _ = second
        overlap_x = min(fx + fw, sx + sw) - max(fx, sx)
        overlap_y = min(fy + fh, sy + sh) - max(fy, sy)
        if overlap_x > 0 and overlap_y > 0:
            return "overlap"
        if abs((fy + fh / 2) - (sy + sh / 2)) <= 0.2:
            return "left" if fx < sx else "right"
        return "above" if fy < sy else "below"

    for first_index, (first_a, first_b) in enumerate(matched_pairs):
        for second_index, (second_a, second_b) in enumerate(matched_pairs):
            if first_index == second_index:
                continue
            if relation(first_a, second_a) != relation(first_b, second_b):
                return False
    return True


def _fragments(content: str) -> tuple[tuple[float, float, float, float, str], ...]:
    """Estimate relative glyph-run fragments from a native text callback.

    The visitor gives us the text run and its outer transform, but not stable
    per-glyph rectangles across PDF producers.  Keeping line and token
    positions here still preserves the internal topology needed to distinguish
    furniture with the same outer box while remaining deliberately tolerant of
    font metric differences.
    """
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        lines = [content]
    line_height = 1.0 / max(len(lines), 1)
    fragments = []
    for line_index, line in enumerate(lines):
        words = line.split()
        if not words:
            continue
        weights = [max(len(word), 1) for word in words]
        total = sum(weights) + max(len(words) - 1, 0)
        cursor = 0.0
        for word, weight in zip(words, weights):
            width = weight / max(total, 1)
            fragments.append((cursor, line_index * line_height, width,
                              line_height, word))
            cursor += width + 1.0 / max(total, 1)
    return tuple(fragments)


def _text_similarity(left: str, right: str) -> float:
    left_words, right_words = left.split(), right.split()
    if not left_words or not right_words:
        return 0.0
    return sum(word in right_words for word in left_words) / max(len(left_words), len(right_words))


def _extend_position(matches, pages, step: int, max_page: int, unavailable: set[int]) -> None:
    """Extend a confirmed three-page track, allowing a single missing page."""
    page_index = matches[-1].page_index + step
    misses = 0
    while page_index <= max_page and misses < 2:
        candidate = next((section for section in pages.get(page_index, [])
                          if id(section) not in unavailable and _similar(matches[-1], section)), None)
        if candidate is None:
            misses += 1
        else:
            matches.append(candidate)
            unavailable.add(id(candidate))
            misses = 0
        page_index += step
