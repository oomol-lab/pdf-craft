"""Native PDF PageFurniture extraction and compact XML serialization."""
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from ..common import indent, save_xml


@dataclass
class FurnitureSection:
    page_index: int
    det: tuple[int, int, int, int]
    content: str
    associations: list[tuple[str, int, int]] = field(default_factory=list)


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
    for page_index, page in enumerate(reader.pages, 1):
        if available_pages and page_index not in available_pages:
            continue
        native = _native_sections(page, page_index, dpi)
        ocr_boxes = _read_ocr_boxes(ocr_path / f"page_{page_index}.xml")
        pages[page_index] = [s for s in native if not any(_coverage(s.det, box) >= 0.95 for box in ocr_boxes)]

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
                for kind, pattern_id, position_id in section.associations:
                    assoc = ET.SubElement(page_el, "section", {**attrs, "kind": kind,
                        "pattern_id": str(pattern_id), "position_id": str(position_id)})
                    assoc.text = None
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


def _native_sections(page, page_index: int, dpi: int) -> list[FurnitureSection]:
    """Read native PDF glyph runs with pypdf's visitor API.

    PDFs do not always expose glyph advance widths.  A conservative font-size
    based width estimate is sufficient here because OCR boxes are only an
    exclusion mask; the native text itself remains the authoritative content.
    """
    sections: list[FurnitureSection] = []
    scale = dpi / 72.0
    height = float(page.mediabox.height)

    def visit(text, _cm, tm, _font, font_size):
        content = " ".join(str(text).split())
        if not content or float(font_size or 0) <= 0:
            return
        x, y = float(tm[4]), float(tm[5])
        size = float(font_size)
        width = max(size, len(content) * size * 0.55)
        # PDF coordinates start at bottom-left; OCR raster coordinates at top-left.
        det = (round(x * scale), round((height - y - size) * scale),
               round((x + width) * scale), round((height - y) * scale))
        sections.append(FurnitureSection(page_index, det, content))

    page.extract_text(visitor_text=visit)
    return sections


def _coverage(a, b) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    area = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    return intersection / area if area else 0.0


def _discover_patterns(pages: dict[int, list[FurnitureSection]]) -> list[FurniturePattern]:
    patterns: list[FurniturePattern] = []
    next_pattern = 0
    for kind, step in (("universal", 1), ("same_side", 2)):
        max_page = max(pages, default=0)
        for start in range(1, max_page - 2 * step + 1):
            sample_pages = (start, start + step, start + 2 * step)
            if any(not pages.get(i) for i in sample_pages):
                continue
            used: set[int] = set()
            positions: list[FurniturePosition] = []
            for first in pages[sample_pages[0]]:
                matches = [first]
                for page_index in sample_pages[1:]:
                    candidate = next((s for s in pages[page_index] if id(s) not in used and _similar(first, s)), None)
                    if candidate is None:
                        break
                    matches.append(candidate)
                if len(matches) < 3:
                    continue
                position = FurniturePosition(len(positions), max(set(s.content for s in matches), key=lambda t: sum(s.content == t for s in matches)), matches)
                positions.append(position)
                used.update(id(s) for s in matches)
            if positions:
                pattern = FurniturePattern(next_pattern, kind, positions)
                patterns.append(pattern)
                for position in positions:
                    for section in position.sections:
                        section.associations.append((kind, pattern.id, position.id))
                next_pattern += 1
    return patterns


def _similar(a: FurnitureSection, b: FurnitureSection) -> bool:
    aw, ah = a.det[2] - a.det[0], a.det[3] - a.det[1]
    bw, bh = b.det[2] - b.det[0], b.det[3] - b.det[1]
    return min(aw, bw) / max(aw, bw) >= 0.9 and min(ah, bh) / max(ah, bh) >= 0.9
