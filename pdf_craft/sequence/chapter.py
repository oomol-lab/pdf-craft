from dataclasses import dataclass
from typing import cast, Generator, Iterable
from xml.etree.ElementTree import Element

from ..common import indent, AssetRef, ASSET_TAGS
from .mark import Mark

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
    lines: list["LineLayout"]

@dataclass
class LineLayout:
    page_index: int
    det: tuple[int, int, int, int]
    content: list["str | Reference"]

@dataclass
class Reference:
    page_index: int
    order: int
    mark: str | Mark
    layouts: list[AssetLayout | ParagraphLayout]

    @property
    def id(self) -> tuple[int, int]:
        return (self.page_index, self.order)

def search_references_in_chapter(chapter: Chapter) -> Generator[Reference, None, None]:
    seen: set[tuple[int, int]] = set()
    if chapter.title is not None:
        for line in chapter.title.lines:
            for part in line.content:
                if isinstance(part, Reference):
                    ref_id = part.id
                    if ref_id not in seen:
                        seen.add(ref_id)
                        yield part
    for layout in chapter.layouts:
        if isinstance(layout, ParagraphLayout):
            for line in layout.lines:
                for part in line.content:
                    if isinstance(part, Reference):
                        ref_id = part.id
                        if ref_id not in seen:
                            seen.add(ref_id)
                            yield part

def references_to_map(references: Iterable[Reference]) -> dict[tuple[int, int], int]:
    ref_id_to_number = {}
    for i, ref in enumerate(references, 1):
        ref_id_to_number[ref.id] = i
    return ref_id_to_number

def decode(element: Element) -> Chapter:
    references_el = element.find("references")
    references_map: dict[tuple[int, int], Reference] = {}
    if references_el is not None:
        for ref_el in references_el.findall("ref"):
            reference = _decode_reference(ref_el)
            references_map[reference.id] = reference

    title_el = element.find("title")
    title = _decode_paragraph(title_el, references_map) if title_el is not None else None
    body_el = element.find("body")
    if body_el is None:
        raise ValueError("<chapter> missing required <body> element")

    layouts: list[ParagraphLayout | AssetLayout] = []
    for child in list(body_el):
        tag = child.tag
        if tag == "asset":
            layouts.append(_decode_asset(child))
        elif tag == "paragraph":
            layouts.append(_decode_paragraph(child, references_map))

    return Chapter(
        title=title,
        layouts=layouts,
    )

def encode(chapter: Chapter) -> Element:
    root = Element("chapter")

    if chapter.title is not None:
        title_el = Element("title")
        title_el.set("ref", chapter.title.ref)
        _encode_line_elements(title_el, chapter.title.lines)
        root.append(title_el)

    body_el = Element("body")
    for layout in chapter.layouts:
        if isinstance(layout, AssetLayout):
            body_el.append(_encode_asset(layout))
        else:
            body_el.append(_encode_paragraph(layout))

    root.append(body_el)
    references = _collect_references(chapter)
    if references:
        references_el = Element("references")
        for ref in references:
            references_el.append(_encode_reference(ref))
        root.append(references_el)

    return indent(root, skip_tags=("line",))

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

def _decode_paragraph(element: Element, references_map: dict[tuple[int, int], Reference] | None = None) -> ParagraphLayout:
    ref_attr = element.get("ref")
    if ref_attr is None:
        raise ValueError("<paragraph> missing required attribute 'ref'")

    lines = _decode_line_elements(element, context_tag="paragraph", references_map=references_map)

    return ParagraphLayout(ref=ref_attr, lines=lines)

def _encode_paragraph(layout: ParagraphLayout) -> Element:
    el = Element("paragraph")
    el.set("ref", layout.ref)
    _encode_line_elements(el, layout.lines)
    return el

def _parse_det(det_str: str, context: str) -> tuple[int, int, int, int]:
    try:
        det_list = list(map(int, det_str.split(",")))
    except Exception as e:
        raise ValueError(f"{context}: det must be comma-separated integers, got: {det_str}") from e
    if len(det_list) != 4:
        raise ValueError(f"{context}: det must have 4 values, got {len(det_list)}")
    return (det_list[0], det_list[1], det_list[2], det_list[3])

def _decode_line_elements(parent: Element, *, context_tag: str, references_map: dict[tuple[int, int], Reference] | None = None) -> list[LineLayout]:
    lines: list[LineLayout] = []
    for line_el in parent.findall("line"):
        page_index_attr = line_el.get("page_index")
        if page_index_attr is None:
            raise ValueError(f"<{context_tag}><line> missing required attribute 'page_index'")
        try:
            page_index = int(page_index_attr)
        except ValueError as e:
            raise ValueError(f"<{context_tag}><line> attribute 'page_index' must be int, got: {page_index_attr}") from e

        det_str = line_el.get("det")
        if det_str is None:
            raise ValueError(f"<{context_tag}><line> missing required attribute 'det'")
        det = _parse_det(det_str, context=f"<{context_tag}><line>@det")
        content: list[str | Reference] = []
        if line_el.text:
            content.append(line_el.text)

        for child in line_el:
            if child.tag == "ref":
                ref_id = child.get("id")
                if ref_id is None:
                    raise ValueError(f"<{context_tag}><line><ref> missing required attribute 'id'")

                try:
                    parts = ref_id.split('-')
                    if len(parts) != 2:
                        raise ValueError(f"<{context_tag}><line><ref> attribute 'id' must be in format 'page-order'")
                    ref_page_index = int(parts[0])
                    ref_order = int(parts[1])
                except ValueError as e:
                    raise ValueError(f"<{context_tag}><line><ref> attribute 'id' must contain valid integers") from e

                if references_map is not None:
                    ref_key = (ref_page_index, ref_order)
                    if ref_key in references_map:
                        content.append(references_map[ref_key])
                    else:
                        raise ValueError(f"<{context_tag}><line><ref> references undefined reference: {ref_id}")

            if child.tail:
                content.append(child.tail)

        lines.append(LineLayout(page_index=page_index, det=det, content=content))
    return lines

def _encode_line_elements(parent: Element, lines: list[LineLayout]) -> None:
    for line in lines:
        line_el = Element("line")
        line_el.set("page_index", str(line.page_index))
        line_el.set("det", ",".join(map(str, line.det)))
        has_elements = False
        for part in line.content:
            if isinstance(part, str):
                if not has_elements:
                    line_el.text = part
                else:
                    last_child = line_el[-1]
                    last_child.tail = part
            elif isinstance(part, Reference):
                ref_el = Element("ref")
                ref_el.set("id", f"{part.page_index}-{part.order}")
                line_el.append(ref_el)
                has_elements = True

        parent.append(line_el)

def _collect_references(chapter: Chapter) -> list[Reference]:
    references: list[Reference] = []
    seen: set[tuple[int, int]] = set()

    def collect_from_layouts(layouts: list[AssetLayout | ParagraphLayout]) -> None:
        for layout in layouts:
            if isinstance(layout, ParagraphLayout):
                for line in layout.lines:
                    for part in line.content:
                        if isinstance(part, Reference):
                            ref_id = part.id
                            if ref_id not in seen:
                                seen.add(ref_id)
                                references.append(part)

    if chapter.title is not None:
        for line in chapter.title.lines:
            for part in line.content:
                if isinstance(part, Reference):
                    ref_id = part.id
                    if ref_id not in seen:
                        seen.add(ref_id)
                        references.append(part)

    collect_from_layouts(chapter.layouts)
    references.sort(key=lambda ref: ref.id)

    return references

def _encode_reference(ref: Reference) -> Element:
    ref_el = Element("ref")
    ref_el.set("id", f"{ref.page_index}-{ref.order}")

    mark_el = Element("mark")
    mark_el.text = str(ref.mark)
    ref_el.append(mark_el)

    for layout in ref.layouts:
        if isinstance(layout, AssetLayout):
            ref_el.append(_encode_asset(layout))
        else:
            ref_el.append(_encode_paragraph(layout))

    return ref_el

def _decode_reference(element: Element) -> Reference:
    ref_id = element.get("id")
    if ref_id is None:
        raise ValueError("<references><ref> missing required attribute 'id'")

    try:
        parts = ref_id.split('-')
        if len(parts) != 2:
            raise ValueError("<references><ref> attribute 'id' must be in format 'page-order'")
        page_index = int(parts[0])
        order = int(parts[1])
    except ValueError as e:
        raise ValueError("<references><ref> attribute 'id' must contain valid integers") from e

    mark_el = element.find("mark")
    if mark_el is None or mark_el.text is None:
        raise ValueError("<references><ref> missing required <mark> element")
    mark_text = mark_el.text

    from .mark import transform2mark
    mark = transform2mark(mark_text)
    if mark is None:
        # 如果不是特殊标记，就使用原始字符串
        mark = mark_text

    layouts: list[AssetLayout | ParagraphLayout] = []
    for child in element:
        if child.tag == "mark":
            continue
        elif child.tag == "asset":
            layouts.append(_decode_asset(child))
        elif child.tag == "paragraph":
            layouts.append(_decode_paragraph(child, references_map=None))

    return Reference(
        page_index=page_index,
        order=order,
        mark=mark,
        layouts=layouts,
    )
