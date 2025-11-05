from dataclasses import dataclass
from typing import cast
from xml.etree.ElementTree import Element

from ..common import indent, AssetRef, ASSET_TAGS

@dataclass
class Chapter:
    title: "ParagraphLayout | None"
    layouts: list["ParagraphLayout | AssetLayout"]

@dataclass
class AssetLayout:
    page_index: int
    ref: AssetRef
    det: tuple[int, int, int, int]
    title: str | None
    content: str
    caption: str | None
    hash: str | None

@dataclass
class ParagraphLayout:
    ref: str
    page_indexes: list[int]
    content: list[tuple[tuple[int, int, int, int], str]]


def decode(element: Element) -> Chapter:
    title_el = element.find("title")
    title = _decode_paragraph(title_el) if title_el is not None else None

    layouts: list[ParagraphLayout | AssetLayout] = []
    for child in list(element):
        tag = child.tag
        if tag == "asset":
            layouts.append(_decode_asset(child))
        elif tag == "paragraph":
            layouts.append(_decode_paragraph(child))

    return Chapter(
        title=title,
        layouts=layouts,
    )
def encode(chapter: Chapter) -> Element:
    root = Element("chapter")

    if chapter.title is not None:
        title_el = Element("title")
        title_el.set("ref", chapter.title.ref)
        if chapter.title.page_indexes:
            title_el.set("page_indexes", ",".join(map(str, chapter.title.page_indexes)))
        _encode_line_elements(title_el, chapter.title.content)
        root.append(title_el)

    for layout in chapter.layouts:
        if isinstance(layout, AssetLayout):
            root.append(_encode_asset(layout))
        else:
            root.append(_encode_paragraph(layout))

    return indent(root)


def _decode_asset(element: Element) -> AssetLayout:
    ref_attr = element.get("ref")
    if ref_attr is None:
        raise ValueError("<asset> missing required attribute 'ref'")
    if ref_attr not in ASSET_TAGS:
        raise ValueError(f"<asset> attribute 'ref' must be one of {ASSET_TAGS}, got: {ref_attr}")
    page_index_attr = element.get("page_index")
    if page_index_attr is None:
        raise ValueError("<asset> missing required attribute 'page_index'")
    try:
        page_index = int(page_index_attr)
    except ValueError as e:
        raise ValueError(f"<asset> attribute 'page_index' must be int, got: {page_index_attr}") from e

    det_str = element.get("det")
    if det_str is None:
        raise ValueError("<asset> missing required attribute 'det'")
    det = _parse_det(det_str, context="<asset>@det")

    hash_value = element.get("hash")

    title_el = element.find("title")
    title = title_el.text if title_el is not None else None

    content_el = element.find("content")
    content = content_el.text if content_el is not None and content_el.text is not None else ""

    caption_el = element.find("caption")
    caption = caption_el.text if caption_el is not None else None

    return AssetLayout(
        page_index=page_index,
        ref=cast(AssetRef, ref_attr),
        det=det,
        title=title,
        content=content,
        caption=caption,
        hash=hash_value,
    )


def _encode_asset(layout: AssetLayout) -> Element:
    el = Element("asset")
    el.set("ref", layout.ref)
    el.set("page_index", str(layout.page_index))
    el.set("det", ",".join(map(str, layout.det)))
    if layout.hash is not None:
        el.set("hash", layout.hash)

    if layout.title is not None:
        title_el = Element("title")
        title_el.text = layout.title
        el.append(title_el)

    content_el = Element("content")
    content_el.text = layout.content
    el.append(content_el)

    if layout.caption is not None:
        caption_el = Element("caption")
        caption_el.text = layout.caption
        el.append(caption_el)

    return el


def _decode_paragraph(element: Element) -> ParagraphLayout:
    ref_attr = element.get("ref")
    if ref_attr is None:
        raise ValueError("<paragraph> missing required attribute 'ref'")
    page_indexes_str = element.get("page_indexes")
    if page_indexes_str is None:
        raise ValueError("<paragraph> missing required attribute 'page_indexes'")
    page_indexes = [int(s) for s in page_indexes_str.split(",") if s.strip() != ""]

    content = _decode_line_elements(element, context_tag="paragraph")

    return ParagraphLayout(ref=ref_attr, page_indexes=page_indexes, content=content)


def _encode_paragraph(layout: ParagraphLayout) -> Element:
    el = Element("paragraph")
    el.set("ref", layout.ref)
    if layout.page_indexes:
        el.set("page_indexes", ",".join(map(str, layout.page_indexes)))

    _encode_line_elements(el, layout.content)

    return el


def _parse_det(det_str: str, context: str) -> tuple[int, int, int, int]:
    try:
        det_list = list(map(int, det_str.split(",")))
    except Exception as e:
        raise ValueError(f"{context}: det must be comma-separated integers, got: {det_str}") from e
    if len(det_list) != 4:
        raise ValueError(f"{context}: det must have 4 values, got {len(det_list)}")
    return (det_list[0], det_list[1], det_list[2], det_list[3])


def _decode_line_elements(parent: Element, *, context_tag: str) -> list[tuple[tuple[int, int, int, int], str]]:
    lines: list[tuple[tuple[int, int, int, int], str]] = []
    for line_el in parent.findall("line"):
        det_str = line_el.get("det")
        if det_str is None:
            raise ValueError(f"<{context_tag}><line> missing required attribute 'det'")
        det = _parse_det(det_str, context=f"<{context_tag}><line>@det")
        text = line_el.text.strip() if line_el.text else ""
        lines.append((det, text))
    return lines


def _encode_line_elements(parent: Element, lines: list[tuple[tuple[int, int, int, int], str]]) -> None:
    for det, text in lines:
        line_el = Element("line")
        line_el.set("det", ",".join(map(str, det)))
        line_el.text = text
        parent.append(line_el)
