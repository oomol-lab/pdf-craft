from dataclasses import dataclass
from typing import Generator, Iterable, cast
from xml.etree.ElementTree import Element

from ..common import ASSET_TAGS, AssetRef, indent
from ..expression import ExpressionKind, decode_expression_kind, encode_expression_kind
from ..markdown.paragraph import HTMLTag, flatten
from ..markdown.paragraph import decode as decode_content
from ..markdown.paragraph import encode as encode_content
from .mark import Mark


@dataclass
class Chapter:
    id: int | None
    level: int
    layouts: list["ParagraphLayout | AssetLayout"]


@dataclass
class ParagraphLayout:
    ref: str
    level: int
    blocks: list["BlockLayout"]


@dataclass
class InlineExpression:
    kind: ExpressionKind
    content: str


@dataclass
class Reference:
    page_index: int
    order: int
    mark: str | Mark
    layouts: list["AssetLayout | ParagraphLayout"]

    @property
    def id(self) -> tuple[int, int]:
        return (self.page_index, self.order)


BlockMember = InlineExpression | Reference
RefIdMap = dict[tuple[int, int], int]


@dataclass
class AssetLayout:
    page_index: int
    ref: AssetRef
    det: tuple[int, int, int, int]
    title: list[str | BlockMember | HTMLTag[BlockMember]]
    content: list[str | BlockMember | HTMLTag[BlockMember]]
    caption: list[str | BlockMember | HTMLTag[BlockMember]]
    hash: str | None


@dataclass
class BlockLayout:
    page_index: int
    order: int
    det: tuple[int, int, int, int]
    content: list[str | BlockMember | HTMLTag[BlockMember]]


def search_references_in_chapter(chapter: Chapter) -> Generator[Reference, None, None]:
    seen: set[tuple[int, int]] = set()
    for part in _search_parts_in_chapter(chapter):
        if isinstance(part, Reference):
            ref_id = part.id
            if ref_id not in seen:
                seen.add(ref_id)
                yield part


def references_to_map(references: Iterable[Reference]) -> RefIdMap:
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

    id_attr = element.get("id")
    id_value = int(id_attr) if id_attr is not None else None

    level_attr = element.get("level")
    level = int(level_attr) if level_attr is not None else -1

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
        id=id_value,
        level=level,
        layouts=layouts,
    )


def encode(chapter: Chapter) -> Element:
    root = Element("chapter")

    if chapter.id is not None:
        root.set("id", str(chapter.id))

    if chapter.level != -1:
        root.set("level", str(chapter.level))

    body_el = Element("body")
    for layout in chapter.layouts:
        if isinstance(layout, AssetLayout):
            body_el.append(_encode_asset(layout))
        else:
            body_el.append(_encode_paragraph(layout))

    root.append(body_el)
    references = list(search_references_in_chapter(chapter))
    references.sort(key=lambda ref: ref.id)

    if references:
        references_el = Element("references")
        for ref in references:
            references_el.append(_encode_reference(ref))
        root.append(references_el)

    return indent(root)


def _search_parts_in_chapter(chapter: Chapter):
    for layout in chapter.layouts:
        if isinstance(layout, ParagraphLayout):
            for block in layout.blocks:
                yield from flatten(block.content)


def _decode_asset(element: Element) -> AssetLayout:
    ref_attr = element.get("ref")
    if ref_attr is None:
        raise ValueError("<asset> missing required attribute 'ref'")
    if ref_attr not in ASSET_TAGS:
        raise ValueError(
            f"<asset> attribute 'ref' must be one of {ASSET_TAGS}, got: {ref_attr}"
        )
    page_index_attr = element.get("page_index")
    if page_index_attr is None:
        raise ValueError("<asset> missing required attribute 'page_index'")
    try:
        page_index = int(page_index_attr)
    except ValueError as e:
        raise ValueError(
            f"<asset> attribute 'page_index' must be int, got: {page_index_attr}"
        ) from e

    det_str = element.get("det")
    if det_str is None:
        raise ValueError("<asset> missing required attribute 'det'")
    det = _parse_det(det_str, context="<asset>@det")

    hash_value = element.get("hash")

    def decode_block_member(child: Element) -> BlockMember:
        if child.tag == "ref":
            raise ValueError("<asset> cannot contain <ref> elements")
        elif child.tag == "inline_expr":
            kind_attr = child.get("kind")
            if kind_attr is None:
                raise ValueError(
                    "<asset><inline_expr> missing required attribute 'kind'"
                )
            kind = decode_expression_kind(kind_attr)
            expr_text = child.text if child.text is not None else ""
            return InlineExpression(kind=kind, content=expr_text)
        else:
            raise ValueError(f"<asset> contains unknown element: <{child.tag}>")

    title_el = element.find("title")
    title = (
        decode_content(title_el, decode_block_member) if title_el is not None else []
    )

    content_el = element.find("content")
    content = (
        decode_content(content_el, decode_block_member)
        if content_el is not None
        else []
    )

    caption_el = element.find("caption")
    caption = (
        decode_content(caption_el, decode_block_member)
        if caption_el is not None
        else []
    )

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

    if layout.title:
        title_el = Element("title")
        encode_content(
            root=title_el,
            children=layout.title,
            encode_payload=_encode_block_member,
        )
        el.append(title_el)

    if layout.content:
        content_el = Element("content")
        encode_content(
            root=content_el,
            children=layout.content,
            encode_payload=_encode_block_member,
        )
        el.append(content_el)

    if layout.caption:
        caption_el = Element("caption")
        encode_content(
            root=caption_el,
            children=layout.caption,
            encode_payload=_encode_block_member,
        )
        el.append(caption_el)

    return el


def _decode_paragraph(
    element: Element, references_map: dict[tuple[int, int], Reference] | None = None
) -> ParagraphLayout:
    ref_attr = element.get("ref")
    if ref_attr is None:
        raise ValueError("<paragraph> missing required attribute 'ref'")

    level_attr = element.get("level")
    level = int(level_attr) if level_attr is not None else -1

    blocks = _decode_block_elements(
        parent=element,
        context_tag="paragraph",
        references_map=references_map,
    )
    return ParagraphLayout(ref=ref_attr, level=level, blocks=blocks)


def _encode_paragraph(layout: ParagraphLayout) -> Element:
    el = Element("paragraph")
    el.set("ref", layout.ref)
    if layout.level != -1:
        el.set("level", str(layout.level))
    for block in layout.blocks:
        el.append(_encode_block_element(block))
    return el


def _parse_det(det_str: str, context: str) -> tuple[int, int, int, int]:
    try:
        det_list = list(map(int, det_str.split(",")))
    except Exception as e:
        raise ValueError(
            f"{context}: det must be comma-separated integers, got: {det_str}"
        ) from e
    if len(det_list) != 4:
        raise ValueError(f"{context}: det must have 4 values, got {len(det_list)}")
    return (det_list[0], det_list[1], det_list[2], det_list[3])


def _decode_block_elements(
    parent: Element,
    context_tag: str,
    references_map: dict[tuple[int, int], Reference] | None = None,
) -> list[BlockLayout]:
    blocks: list[BlockLayout] = []
    for block_el in parent.findall("block"):
        page_index_attr = block_el.get("page_index")
        if page_index_attr is None:
            raise ValueError(
                f"<{context_tag}><block> missing required attribute 'page_index'"
            )
        try:
            page_index = int(page_index_attr)
        except ValueError as e:
            raise ValueError(
                f"<{context_tag}><block> attribute 'page_index' must be int, got: {page_index_attr}"
            ) from e

        order_attr = block_el.get("order")
        if order_attr is None:
            raise ValueError(
                f"<{context_tag}><block> missing required attribute 'order'"
            )
        try:
            order = int(order_attr)
        except ValueError as e:
            raise ValueError(
                f"<{context_tag}><block> attribute 'order' must be int, got: {order_attr}"
            ) from e

        det_str = block_el.get("det")
        if det_str is None:
            raise ValueError(f"<{context_tag}><block> missing required attribute 'det'")
        det = _parse_det(det_str, context=f"<{context_tag}><block>@det")

        def decode_block_member(child: Element) -> BlockMember:
            if child.tag == "ref":
                ref_id = child.get("id")
                if ref_id is None:
                    raise ValueError(
                        f"<{context_tag}><block><ref> missing required attribute 'id'"
                    )

                try:
                    parts = ref_id.split("-")
                    if len(parts) != 2:
                        raise ValueError(
                            f"<{context_tag}><block><ref> attribute 'id' must be in format 'page-order'"
                        )
                    ref_page_index = int(parts[0])
                    ref_order = int(parts[1])
                except ValueError as e:
                    raise ValueError(
                        f"<{context_tag}><block><ref> attribute 'id' must contain valid integers"
                    ) from e

                if references_map is not None:
                    ref_key = (ref_page_index, ref_order)
                    if ref_key in references_map:
                        return references_map[ref_key]
                    else:
                        raise ValueError(
                            f"<{context_tag}><block><ref> references undefined reference: {ref_id}"
                        )
                else:
                    raise ValueError(
                        f"<{context_tag}><block><ref> cannot resolve reference without references_map"
                    )

            elif child.tag == "inline_expr":
                kind_attr = child.get("kind")
                if kind_attr is None:
                    raise ValueError(
                        f"<{context_tag}><block><inline_expr> missing required attribute 'kind'"
                    )
                kind = decode_expression_kind(kind_attr)
                expr_text = child.text if child.text is not None else ""
                return InlineExpression(kind=kind, content=expr_text)

            else:
                raise ValueError(
                    f"<{context_tag}><block> contains unknown element: <{child.tag}>"
                )

        blocks.append(
            BlockLayout(
                page_index=page_index,
                order=order,
                det=det,
                content=decode_content(block_el, decode_block_member),
            )
        )

    return blocks


def _encode_block_element(block: BlockLayout) -> Element:
    block_el = Element("block")
    block_el.set("page_index", str(block.page_index))
    block_el.set("order", str(block.order))
    block_el.set("det", ",".join(map(str, block.det)))
    encode_content(
        root=block_el,
        children=block.content,
        encode_payload=_encode_block_member,
    )
    return block_el


def _encode_block_member(part: BlockMember) -> Element:
    if isinstance(part, InlineExpression):
        expr_el = Element("inline_expr")
        expr_el.set("kind", encode_expression_kind(part.kind))
        expr_el.text = part.content
        return expr_el

    elif isinstance(part, Reference):
        ref_el = Element("ref")
        ref_el.set("id", f"{part.page_index}-{part.order}")
        return ref_el

    else:
        raise ValueError("Unknown BlockMember type")


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
        parts = ref_id.split("-")
        if len(parts) != 2:
            raise ValueError(
                "<references><ref> attribute 'id' must be in format 'page-order'"
            )
        page_index = int(parts[0])
        order = int(parts[1])
    except ValueError as e:
        raise ValueError(
            "<references><ref> attribute 'id' must contain valid integers"
        ) from e

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
