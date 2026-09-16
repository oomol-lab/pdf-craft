"""Conservative EPUB float rendering for image assets anchored in text flow.

PCEX records an asset's *reading* position, not a command to reproduce the
PDF's geometry.  This module therefore opts in to a float only when the
neighbouring source fragments make the original side-by-side arrangement
unambiguous.  The resulting CSS is deliberately optional: readers which do
not support it retain normal block order.
"""
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal
from xml.etree import ElementTree
from zipfile import ZIP_STORED, ZipFile

from ...extractor.chapter import SourceAsset, SourceTextFragment, TextFlowItem


FloatSide = Literal["start", "end"]
_XHTML_NS = "http://www.w3.org/1999/xhtml"
_EPUB_NS = "http://www.idpf.org/2007/ops"
_MARKER_ATTRIBUTE = "data-pdf-craft-float"
MARKER_CLASS = "pdf-craft-float-marker"
_FLOAT_CSS = """

/* A best-effort rendering of a small source-PDF side illustration.  EPUB
 * readers may ignore float or this media query; document order still leaves
 * the image between its surrounding text in that case. */
div.pdf-craft-anchored-float {
  max-width: 45%;
  break-inside: avoid;
  page-break-inside: avoid;
}

div.pdf-craft-anchored-float-start {
  float: left;
  margin: 0.25em 1em 0.75em 0;
}

div.pdf-craft-anchored-float-end {
  float: right;
  margin: 0.25em 0 0.75em 1em;
}

div.pdf-craft-anchored-float img {
  display: block;
  max-width: 100%;
  height: auto;
}

@media screen and (max-width: 35em) {
  div.pdf-craft-anchored-float {
    float: none;
    max-width: 100%;
    margin: 1em 0;
  }
}
"""


@dataclass
class FloatMarkerRegistry:
    """Creates per-occurrence markers without relying on an asset hash.

    The same clipped image can appear both as a standalone block and inside
    text flow.  A content hash alone would make post-processing float every
    occurrence, so each eligible occurrence receives a short-lived marker.
    """

    _sides: dict[str, FloatSide] = field(default_factory=dict)

    def new(self, side: FloatSide) -> str:
        marker_id = f"anchor-{len(self._sides) + 1}"
        self._sides[marker_id] = side
        return marker_id

    @property
    def sides(self) -> dict[str, FloatSide]:
        return dict(self._sides)


def float_side(item: TextFlowItem, asset_index: int) -> FloatSide | None:
    """Return a side only for a clearly small, side-by-side source image.

    A float is intentionally not inferred from logical nesting alone.  Both
    adjacent text fragments must be on the image's page, lie wholly on its
    opposite side, overlap it vertically, and be substantially wider.  This
    makes uncertain, old-schema, full-width, table, and merely interleaved
    assets retain ordinary block rendering.
    """
    if item.role != "body" or asset_index <= 0 or asset_index >= len(item.children) - 1:
        return None
    asset = item.children[asset_index]
    before = item.children[asset_index - 1]
    after = item.children[asset_index + 1]
    if (
        not isinstance(asset, SourceAsset)
        or asset.ref != "image"
        or asset.asset_hash is None
        or not isinstance(before, SourceTextFragment)
        or not isinstance(after, SourceTextFragment)
        or before.page_index != asset.page_index
        or after.page_index != asset.page_index
    ):
        return None

    left, top, right, bottom = asset.bbox
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None
    fragments = (before, after)
    if any(not _overlaps_vertically(asset, fragment) for fragment in fragments):
        return None

    fragment_widths = [fragment.bbox[2] - fragment.bbox[0] for fragment in fragments]
    if any(fragment_width <= 0 or width > fragment_width * 0.55 for fragment_width in fragment_widths):
        return None

    # The image must be completely to one side of both neighbouring source
    # fragments.  Partial horizontal overlap is evidence of a normal block,
    # not evidence for a responsive float.
    if right <= min(fragment.bbox[0] for fragment in fragments):
        return "start"
    if left >= max(fragment.bbox[2] for fragment in fragments):
        return "end"
    return None


def _overlaps_vertically(asset: SourceAsset, fragment: SourceTextFragment) -> bool:
    asset_height = asset.bbox[3] - asset.bbox[1]
    fragment_height = fragment.bbox[3] - fragment.bbox[1]
    overlap = min(asset.bbox[3], fragment.bbox[3]) - max(asset.bbox[1], fragment.bbox[1])
    return overlap > 0 and overlap / min(asset_height, fragment_height) >= 0.35


def apply_float_markers(epub_path: Path, marker_sides: dict[str, FloatSide]) -> None:
    """Replace internal markers in a generated EPUB with optional float CSS.

    ``epub-generator`` intentionally exposes image blocks but no image-class
    hook.  Markers preserve exact occurrence identity through its public
    model; after it has packed assets we decorate only the following image
    block.  All markers are removed even when a target cannot be found, so a
    failed best-effort enhancement leaves a clean block-layout EPUB.
    """
    if not marker_sides:
        return

    ElementTree.register_namespace("", _XHTML_NS)
    ElementTree.register_namespace("epub", _EPUB_NS)
    replacements: dict[str, bytes] = {}
    applied = 0

    with ZipFile(epub_path) as source:
        for info in source.infolist():
            if not info.filename.startswith("OEBPS/Text/") or not info.filename.endswith(".xhtml"):
                continue
            root = ElementTree.fromstring(source.read(info.filename))
            changed, count = _apply_markers_to_document(root, marker_sides)
            if changed:
                replacements[info.filename] = ElementTree.tostring(
                    root, encoding="utf-8", xml_declaration=True,
                )
            applied += count

        if not replacements:
            return
        if applied:
            style_name = "OEBPS/styles/style.css"
            replacements[style_name] = source.read(style_name) + _FLOAT_CSS.encode("utf-8")
        _rewrite_epub(epub_path, source, replacements)


def _apply_markers_to_document(root, marker_sides: dict[str, FloatSide]) -> tuple[bool, int]:
    parents = {child: parent for parent in root.iter() for child in parent}
    changed = False
    applied = 0
    for marker in list(root.iter(f"{{{_XHTML_NS}}}span")):
        marker_id = marker.get(_MARKER_ATTRIBUTE)
        if marker_id not in marker_sides:
            continue
        paragraph = parents.get(marker)
        if paragraph is None:
            continue
        container = parents.get(paragraph)
        if container is None:
            continue
        target = _next_sibling(container, paragraph)
        if target is not None and _is_image_wrapper(target):
            _append_class(target, "pdf-craft-anchored-float")
            _append_class(target, f"pdf-craft-anchored-float-{marker_sides[marker_id]}")
            applied += 1
        # The marker lives in a deliberately empty paragraph.  Remove the
        # whole paragraph whether or not decoration succeeded to avoid a
        # visible blank line in the safe fallback path.
        container.remove(paragraph)
        changed = True
    return changed, applied


def _next_sibling(container, item):
    children = list(container)
    try:
        index = children.index(item)
    except ValueError:
        return None
    return children[index + 1] if index + 1 < len(children) else None


def _is_image_wrapper(element) -> bool:
    classes = element.get("class", "").split()
    if "alt-wrapper" not in classes and "asset" not in classes:
        return False
    return element.find(f".//{{{_XHTML_NS}}}img") is not None


def _append_class(element, value: str) -> None:
    classes = element.get("class", "").split()
    if value not in classes:
        classes.append(value)
        element.set("class", " ".join(classes))


def _rewrite_epub(epub_path: Path, source: ZipFile, replacements: dict[str, bytes]) -> None:
    with NamedTemporaryFile(dir=epub_path.parent, suffix=".epub", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with ZipFile(temporary_path, "w") as target:
            for info in source.infolist():
                data = replacements.get(info.filename, source.read(info.filename))
                # EPUB requires the mimetype entry to be first and uncompressed.
                if info.filename == "mimetype":
                    info.compress_type = ZIP_STORED
                target.writestr(info, data)
        temporary_path.replace(epub_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
