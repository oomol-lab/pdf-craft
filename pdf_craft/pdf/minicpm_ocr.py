"""Turn MiniCPM-OCR document-parsing blocks into page layouts.

MiniCPM-OCR services return both page Markdown and the regions the Markdown was
assembled from. The region list is what pdf-craft needs: `ocr.blocks` on the
hosted API, `minicpm_ocr.blocks` on the self-hosted Web Demo backend. Each block
carries a label, a pixel box and its recognized content.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from doc_page_extractor.types import Layout, LayoutKind
from PIL import Image

_TABLE_CAPTION_PATTERNS = (
    re.compile(r"^\s*第\s*[一二三四五六七八九十百千万零〇\d\s]+\s*表"),
    re.compile(r"^\s*表\s*[一二三四五六七八九十百千万零〇\d\s]+"),
    re.compile(r"^\s*table\s+\d+\b", re.IGNORECASE),
)

_TEXT_LABELS = {
    "abstract",
    "algorithm",
    "content",
    "reference",
    "reference_content",
    "seal",
    "text",
    "vertical_text",
}
_TITLE_LABELS = {"doc_title", "paragraph_title", "sub_title", "title"}
_CAPTION_LABELS = {"figure_title", "vision_footnote"}
_IMAGE_LABELS = {"figure", "image"}
_TABLE_LABELS = {"chart", "table"}
_EQUATION_LABELS = {"display_formula", "equation", "formula", "inline_formula"}

_IGNORED_LABELS = {
    "aside": LayoutKind.ASIDE,
    "aside_text": LayoutKind.ASIDE,
    "footer": LayoutKind.FOOTER,
    "footer_image": LayoutKind.FOOTER,
    "formula_number": LayoutKind.PAGE_NUMBER,
    "header": LayoutKind.HEADER,
    "header_image": LayoutKind.HEADER,
    "number": LayoutKind.PAGE_NUMBER,
}

_ASSET_KINDS = {
    LayoutKind.IMAGE,
    LayoutKind.TABLE,
    LayoutKind.EQUATION,
}
_CAPTION_KINDS = {
    LayoutKind.IMAGE_CAPTION,
    LayoutKind.TABLE_CAPTION,
    LayoutKind.EQUATION_CAPTION,
}
_CAPTION_FOR_ASSET = {
    LayoutKind.IMAGE: LayoutKind.IMAGE_CAPTION,
    LayoutKind.TABLE: LayoutKind.TABLE_CAPTION,
    LayoutKind.EQUATION: LayoutKind.EQUATION_CAPTION,
}


def parse_minicpm_ocr_layouts(
    data: Mapping[str, Any],
    image_path: Path,
    *,
    source: str,
) -> list[Layout]:
    """Build layouts for the page image sent to the service.

    Coordinates are scaled from the service's reported image size to the page
    image that pdf-craft will store, so the page geometry and asset clipping
    stay aligned with the PDF.
    """
    page = _page_payload(data)
    _require_unrotated_page(data, page)
    blocks = page.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError(
            "MiniCPM-OCR response did not include document layout blocks; "
            "point the configuration at an endpoint that runs MiniCPM-OCR "
            "document parsing, not a plain vision-model endpoint.",
        )

    width, height = _image_size(image_path)
    reported_width = _positive_number(page.get("image_width"))
    reported_height = _positive_number(page.get("image_height"))
    if reported_width is None or reported_height is None:
        scale_x = scale_y = 1.0
    else:
        scale_x = width / reported_width
        scale_y = height / reported_height

    layouts: list[Layout] = []
    for block in _ordered_blocks(blocks):
        layout = _build_layout(
            block, scale_x, scale_y, width, height, source,
        )
        if layout is not None:
            layouts.append(layout)
    if blocks and not layouts:
        raise ValueError(
            "MiniCPM-OCR returned layout blocks, but none had a usable "
            "region and content. Check that the service implements the "
            "MiniCPM-OCR document parsing response."
        )
    return _associate_caption_kinds(layouts)


def _page_payload(data: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("ocr", "minicpm_ocr"):
        page = data.get(key)
        if isinstance(page, Mapping):
            return page
    raise ValueError(
        "MiniCPM-OCR response did not include an 'ocr' or 'minicpm_ocr' page."
    )


def _require_unrotated_page(
    data: Mapping[str, Any],
    page: Mapping[str, Any],
) -> None:
    for payload in (page, data):
        rotation = payload.get("rotation")
        if isinstance(rotation, Mapping) and _rotation_applied(rotation.get("applied")):
            break
        if _rotation_applied(payload.get("rotated")):
            break
    else:
        return
    raise ValueError(
        "MiniCPM-OCR applied orientation correction to the page, so its "
        "layout boxes no longer match the rendered PDF page. Use a service "
        "that leaves orientation correction disabled."
    )


def _rotation_applied(value: Any) -> bool:
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.strip().lower() in {"1", "true"}


def _associate_caption_kinds(layouts: list[Layout]) -> list[Layout]:
    """Give each caption the kind of the asset it is next to.

    The service labels every figure or table caption `figure_title`, and a
    chart is represented as a table. Attaching by the adjacent asset keeps
    captions grouped with the region they describe.
    """
    for index, layout in enumerate(layouts):
        if layout.kind not in _CAPTION_KINDS:
            continue
        asset = _adjacent_asset(layouts, index)
        if asset is not None:
            layout.kind = _CAPTION_FOR_ASSET[asset.kind]
    return layouts


def _adjacent_asset(layouts: list[Layout], index: int) -> Layout | None:
    previous = _neighbor_layout(layouts, index, -1)
    following = _neighbor_layout(layouts, index, 1)
    candidates = [
        layout for layout in (previous, following)
        if layout is not None and layout.kind in _ASSET_KINDS
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return min(
        candidates,
        key=lambda layout: _vertical_gap(layouts[index], layout),
    )


def _neighbor_layout(
    layouts: list[Layout],
    index: int,
    direction: int,
) -> Layout | None:
    position = index + direction
    while 0 <= position < len(layouts):
        layout = layouts[position]
        if layout.kind not in _CAPTION_KINDS:
            return layout
        position += direction
    return None


def _vertical_gap(
    caption: Layout,
    asset: Layout,
) -> int:
    if caption.det[3] <= asset.det[1]:
        return asset.det[1] - caption.det[3]
    if asset.det[3] <= caption.det[1]:
        return caption.det[1] - asset.det[3]
    return 0


def _ordered_blocks(blocks: Sequence[Any]) -> list[Any]:
    indexed = list(enumerate(blocks))
    indexed.sort(key=lambda item: _block_order(item[1], item[0]))
    return [block for _, block in indexed]


def _block_order(block: Any, index: int) -> tuple[int, int]:
    if isinstance(block, Mapping):
        order = block.get("order")
        if isinstance(order, int) and not isinstance(order, bool):
            return order, index
    return index, index


def _build_layout(
    block: Any,
    scale_x: float,
    scale_y: float,
    width: int,
    height: int,
    source: str,
) -> Layout | None:
    if not isinstance(block, Mapping):
        return None
    label = _block_text(block.get("label")).strip().lower()
    content = _block_text(block.get("content"))
    det = _block_det(block)
    if det is None:
        return None
    det = _scale_det(det, scale_x, scale_y, width, height)
    if det is None:
        return None

    kind = _kind_for(label, content)
    if kind != LayoutKind.IMAGE and not content.strip():
        return None
    if kind == LayoutKind.TABLE:
        return Layout(
            det=det, text=None, type=label or None, html=content,
            source=source, kind=kind,
        )
    if kind == LayoutKind.IMAGE:
        return Layout(
            det=det, text=None, type=label or None,
            source=source, kind=kind,
        )
    return Layout(
        det=det, text=content, type=label or None, source=source, kind=kind,
    )


def _kind_for(label: str, content: str) -> LayoutKind:
    if label in _IGNORED_LABELS:
        return _IGNORED_LABELS[label]
    if label in _TITLE_LABELS:
        return LayoutKind.TITLE
    if label in _CAPTION_LABELS:
        if _looks_like_table_caption(content):
            return LayoutKind.TABLE_CAPTION
        return LayoutKind.IMAGE_CAPTION
    if label == "image_caption":
        return LayoutKind.IMAGE_CAPTION
    if label == "table_caption":
        return LayoutKind.TABLE_CAPTION
    if label == "equation_caption":
        return LayoutKind.EQUATION_CAPTION
    if label in _IMAGE_LABELS:
        return LayoutKind.IMAGE
    if label in _TABLE_LABELS:
        # An empty table or chart still came from a real page region. Keeping
        # it as an image preserves the visual instead of dropping it silently.
        return LayoutKind.TABLE if content.strip() else LayoutKind.IMAGE
    if label in _EQUATION_LABELS:
        return LayoutKind.EQUATION
    if label == "footnote":
        return LayoutKind.FOOTNOTE
    if label in _TEXT_LABELS:
        return LayoutKind.TEXT
    return LayoutKind.TEXT


def _looks_like_table_caption(content: str) -> bool:
    return any(pattern.search(content) for pattern in _TABLE_CAPTION_PATTERNS)


def _block_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _block_det(block: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    det = _det_tuple(block.get("bbox"))
    if det is not None:
        return det
    regions = block.get("regions")
    if not isinstance(regions, Sequence) or isinstance(regions, (str, bytes)):
        return None
    region_dets = [
        region_det for region in regions
        if (region_det := _det_tuple(region)) is not None
    ]
    if not region_dets:
        return None
    return (
        min(det[0] for det in region_dets),
        min(det[1] for det in region_dets),
        max(det[2] for det in region_dets),
        max(det[3] for det in region_dets),
    )


def _det_tuple(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        numbers.append(float(item))
    return numbers[0], numbers[1], numbers[2], numbers[3]


def _scale_det(
    det: tuple[float, float, float, float],
    scale_x: float,
    scale_y: float,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    left = max(0, min(width, round(det[0] * scale_x)))
    top = max(0, min(height, round(det[1] * scale_y)))
    right = max(0, min(width, round(det[2] * scale_x)))
    bottom = max(0, min(height, round(det[3] * scale_y)))
    if left >= right or top >= bottom:
        return None
    return left, top, right, bottom


def _image_size(image_path: Path) -> tuple[int, int]:
    with Image.open(image_path) as image:
        return image.size


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 else None
