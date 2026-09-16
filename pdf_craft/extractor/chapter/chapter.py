"""PCEX v3 chapter flow model and XML codec.

``FlowItem`` preserves an author paragraph even when a figure/table interrupts
it. Display equations are separate flow items, so joining text can never leap
over them.
"""
from dataclasses import dataclass, field
from typing import Generator, Iterable, Literal, TypeAlias, Union, cast
from xml.etree.ElementTree import Element

from ...common import ASSET_TAGS, indent
from ...expression import ExpressionKind, decode_expression_kind, encode_expression_kind
from ...markdown.paragraph import HTMLTag, flatten, tag_definition
from ...markdown.paragraph import decode as decode_content
from ...markdown.paragraph import encode as encode_content
from .mark import Mark


@dataclass
class InlineExpression:
    kind: ExpressionKind
    content: str


BlockMember: TypeAlias = Union[InlineExpression, "Reference"]
Content = list[str | BlockMember | HTMLTag[BlockMember]]
RefIdMap = dict[tuple[int, int], int]
FlowAssetRef: TypeAlias = Literal["image", "table", "formula"]


@dataclass
class SourceTextFragment:
    page_index: int
    source_order: int
    bbox: tuple[int, int, int, int]
    content: Content

@dataclass
class SourceAsset:
    page_index: int
    ref: FlowAssetRef
    bbox: tuple[int, int, int, int]
    title: Content = field(default_factory=list)
    content: Content = field(default_factory=list)
    caption: Content = field(default_factory=list)
    asset_hash: str | None = None

    def __post_init__(self) -> None:
        if self.ref not in {"image", "table", "formula"}:
            raise ValueError("SourceAsset ref must be image, table, or formula")


@dataclass
class TextFlowItem:
    role: str
    level: int
    children: list[SourceTextFragment | SourceAsset]

    def __post_init__(self) -> None:
        if self.role not in {"body", "heading"}:
            raise ValueError("TextFlowItem role must be body or heading")
        if any(not isinstance(child, (SourceTextFragment, SourceAsset)) for child in self.children):
            raise ValueError("TextFlowItem children must be SourceTextFragment or SourceAsset")
        if any(isinstance(child, SourceAsset) and child.ref not in {"image", "table"} for child in self.children):
            raise ValueError("TextFlowItem can contain only image/table SourceAsset children; use DisplayFormula for formula")


@dataclass
class DisplayFormula:
    asset: SourceAsset
    def __post_init__(self):
        if not isinstance(self.asset, SourceAsset): raise ValueError("DisplayFormula requires SourceAsset")
        if self.asset.ref != "formula": raise ValueError("DisplayFormula requires formula asset")


@dataclass
class StandaloneAsset:
    asset: SourceAsset
    def __post_init__(self):
        if not isinstance(self.asset, SourceAsset): raise ValueError("StandaloneAsset requires SourceAsset")
        if self.asset.ref not in {"image", "table"}: raise ValueError("StandaloneAsset requires image/table asset")


FlowItem: TypeAlias = TextFlowItem | DisplayFormula | StandaloneAsset


def _asset_flow(asset: SourceAsset) -> FlowItem:
    """Wrap a decoded legacy asset in its v3 flow-node boundary."""
    return DisplayFormula(asset) if asset.ref == "formula" else StandaloneAsset(asset)


def _validate_flow_items(items: Iterable[FlowItem]) -> None:
    if any(not isinstance(item, (TextFlowItem, DisplayFormula, StandaloneAsset)) for item in items):
        raise ValueError("Chapter and Reference flow_items must contain FlowItem values")


@dataclass
class Chapter:
    id: int | None
    level: int
    flow_items: list[FlowItem]

    def __post_init__(self) -> None:
        _validate_flow_items(self.flow_items)


@dataclass
class Reference:
    page_index: int
    order: int
    mark: str | Mark
    flow_items: list[FlowItem]
    def __post_init__(self) -> None:
        _validate_flow_items(self.flow_items)
    @property
    def id(self): return self.page_index, self.order


def references_to_map(references: Iterable[Reference]) -> RefIdMap:
    return {ref.id: index for index, ref in enumerate(references, 1)}


def search_references_in_chapter(chapter: Chapter) -> Generator[Reference, None, None]:
    seen: set[tuple[int, int]] = set()
    for part in _parts(chapter.flow_items):
        if isinstance(part, Reference) and part.id not in seen:
            seen.add(part.id); yield part


def decode(element: Element, *, allow_legacy: bool = True) -> Chapter:
    if not allow_legacy:
        if element.tag != "chapter": raise ValueError("PCEX v3 chapter root must be <chapter>")
        _attributes(element, {"id", "level"})
        _children(element, {"flow", "references"})
    refs = _refs(element.find("references"), allow_legacy=allow_legacy)
    id_text = element.get("id")
    ident = int(id_text) if id_text is not None else None
    level = int(element.get("level", "-1"))
    flow = element.find("flow")
    if flow is not None:
        if not allow_legacy:
            _attributes(flow, set())
            if len(element.findall("flow")) != 1:
                raise ValueError("PCEX v3 chapter must contain exactly one <flow>")
            if len(element.findall("references")) > 1:
                raise ValueError("PCEX v3 chapter can contain at most one <references>")
        if not allow_legacy and any(asset.get("ref") == "equation" for asset in flow.iter("asset")):
            raise ValueError("PCEX v3 uses asset ref='formula', not legacy 'equation'")
        return Chapter(ident, level, [_decode_flow(v, refs, allow_legacy=allow_legacy) for v in flow])
    # v1/v2 in-memory migration; historic sibling assets cannot acquire a
    # fictional nested relation, but formulas become explicit boundaries.
    body = element.find("body")
    if body is None: raise ValueError("<chapter> missing required <flow> element")
    if not allow_legacy: raise ValueError("PCEX v3 chapter must contain <flow>, not legacy <body>")
    items: list[FlowItem] = []
    for child in body:
        if child.tag == "paragraph": items.append(_legacy_paragraph(child, refs))
        elif child.tag == "asset": items.append(_asset_flow(_asset(child, refs, allow_legacy=True)))
        else: raise ValueError(f"<body> contains unknown element: <{child.tag}>")
    return Chapter(ident, level, items)


def encode(chapter: Chapter) -> Element:
    root = Element("chapter")
    if chapter.id is not None: root.set("id", str(chapter.id))
    if chapter.level != -1: root.set("level", str(chapter.level))
    flow = Element("flow")
    for item in chapter.flow_items: flow.append(_encode_flow(item))
    root.append(flow)
    refs = sorted(search_references_in_chapter(chapter), key=lambda ref: ref.id)
    if refs:
        container = Element("references")
        for ref in refs: container.append(_encode_reference(ref))
        root.append(container)
    return indent(root)


def _decode_flow(element: Element, refs: dict[tuple[int, int], Reference], *, allow_legacy: bool = True) -> FlowItem:
    if element.tag == "text":
        if not allow_legacy: _attributes(element, {"role", "level"})
        role = element.get("role")
        if role not in {"body", "heading"}: raise ValueError("<text> role must be body or heading")
        children = []
        for child in element:
            if child.tag == "fragment":
                if not allow_legacy: _attributes(child, {"page_index", "source_order", "bbox"})
                children.append(_fragment(child, refs, allow_legacy=allow_legacy))
            elif child.tag == "asset":
                asset = _asset(child, refs, allow_legacy=allow_legacy)
                if asset.ref == "formula": raise ValueError("text cannot contain formula asset")
                children.append(asset)
            else: raise ValueError(f"<text> contains unknown element: <{child.tag}>")
        return TextFlowItem(role, _integer(element, "level", -1), children)
    if element.tag in {"display-formula", "standalone-asset"}:
        if not allow_legacy: _attributes(element, set())
        children = list(element)
        if len(children) != 1 or children[0].tag != "asset": raise ValueError(f"<{element.tag}> must contain exactly one <asset>")
        asset = _asset(children[0], refs, allow_legacy=allow_legacy)
        return DisplayFormula(asset) if element.tag == "display-formula" else StandaloneAsset(asset)
    raise ValueError(f"<flow> contains unknown element: <{element.tag}>")


def _encode_flow(item: FlowItem) -> Element:
    if isinstance(item, TextFlowItem):
        if any(isinstance(child, SourceAsset) and child.ref == "formula" for child in item.children):
            raise ValueError("TextFlowItem cannot encode an equation SourceAsset; use DisplayFormula")
        result = Element("text", {"role": item.role})
        if item.level != -1: result.set("level", str(item.level))
        for child in item.children: result.append(_encode_fragment(child) if isinstance(child, SourceTextFragment) else _encode_asset(child))
        return result
    result = Element("display-formula" if isinstance(item, DisplayFormula) else "standalone-asset")
    result.append(_encode_asset(item.asset)); return result


def _asset(element: Element, refs: dict[tuple[int, int], Reference] | None = None, *, allow_legacy: bool = True) -> SourceAsset:
    if not allow_legacy: _attributes(element, {"ref", "page_index", "bbox", "asset_hash"})
    if not allow_legacy:
        _children(element, {"title", "content", "caption"})
        for name in ("title", "content", "caption"):
            nodes = element.findall(name)
            if len(nodes) > 1:
                raise ValueError(f"PCEX v3 <asset> can contain at most one <{name}>")
            if nodes:
                _attributes(nodes[0], set())
    ref = element.get("ref")
    if ref == "equation":
        ref = "formula"
    if ref not in ASSET_TAGS: raise ValueError(f"<asset> attribute 'ref' must be one of {ASSET_TAGS}, got: {ref}")
    if not allow_legacy and (element.get("det") is not None or element.get("hash") is not None):
        raise ValueError("PCEX v3 asset uses bbox and asset_hash, not legacy det/hash")
    return SourceAsset(_integer(element, "page_index"), cast(FlowAssetRef, ref), _bbox(element, "det" if allow_legacy else None),
        _content(element.find("title"), refs, "asset", allow_legacy=allow_legacy),
        _content(element.find("content"), refs, "asset", allow_legacy=allow_legacy),
        _content(element.find("caption"), refs, "asset", allow_legacy=allow_legacy),
        element.get("asset_hash", element.get("hash")))


def _encode_asset(asset: SourceAsset) -> Element:
    if asset.ref not in ASSET_TAGS:
        raise ValueError("SourceAsset ref must be image, table, or formula")
    result = Element("asset", {"ref": asset.ref, "page_index": str(asset.page_index), "bbox": _bbox_text(asset.bbox)})
    if asset.asset_hash is not None: result.set("asset_hash", asset.asset_hash)
    for name, value in (("title", asset.title), ("content", asset.content), ("caption", asset.caption)):
        if value:
            node = Element(name); encode_content(node, value, _encode_member); result.append(node)
    return result


def _legacy_paragraph(element: Element, refs: dict[tuple[int, int], Reference]) -> TextFlowItem:
    ref = element.get("ref")
    if ref is None: raise ValueError("<paragraph> missing required attribute 'ref'")
    role = {"text": "body", "title": "heading", "sub_title": "heading"}.get(ref, ref)
    return TextFlowItem(role, _integer(element, "level", -1), [_legacy_block(v, refs) for v in element.findall("block")])


def _fragment(element: Element, refs: dict[tuple[int, int], Reference], *, allow_legacy: bool = True) -> SourceTextFragment:
    return SourceTextFragment(_integer(element, "page_index"), _integer(element, "source_order"), _bbox(element), _content(element, refs, "fragment", allow_legacy=allow_legacy))


def _legacy_block(element: Element, refs: dict[tuple[int, int], Reference]) -> SourceTextFragment:
    return SourceTextFragment(_integer(element, "page_index"), _integer(element, "order"), _bbox(element, "det"), _content(element, refs, "block"))


def _encode_fragment(fragment: SourceTextFragment) -> Element:
    result = Element("fragment", {"page_index": str(fragment.page_index), "source_order": str(fragment.source_order), "bbox": _bbox_text(fragment.bbox)})
    encode_content(result, fragment.content, _encode_member); return result


def _content(element: Element | None, refs: dict[tuple[int, int], Reference] | None, context: str,
             *, allow_legacy: bool = True) -> Content:
    if element is None: return []
    if not allow_legacy:
        _validate_content_members(element)
    def payload(child: Element) -> BlockMember:
        if child.tag == "inline_expr":
            kind = child.get("kind")
            if kind is None: raise ValueError(f"<{context}><inline_expr> missing required attribute 'kind'")
            return InlineExpression(decode_expression_kind(kind), child.text or "")
        if child.tag == "ref":
            try: key = tuple(map(int, child.get("id", "").split("-", 1)))
            except ValueError as error: raise ValueError(f"<{context}><ref> has invalid id") from error
            if refs is None or key not in refs: raise ValueError(f"<{context}><ref> references undefined reference")
            return refs[cast(tuple[int, int], key)]
        raise ValueError(f"<{context}> contains unknown element: <{child.tag}>")
    return decode_content(element, payload)


def _encode_member(part: BlockMember) -> Element:
    if isinstance(part, InlineExpression):
        result = Element("inline_expr", {"kind": encode_expression_kind(part.kind)}); result.text = part.content; return result
    if isinstance(part, Reference): return Element("ref", {"id": f"{part.page_index}-{part.order}"})
    raise ValueError("Unknown flow member type")


def _refs(element: Element | None, *, allow_legacy: bool = True) -> dict[tuple[int, int], Reference]:
    if element is None: return {}
    if not allow_legacy:
        _attributes(element, set())
        _children(element, {"ref"})
    values = [_decode_reference(child, allow_legacy=allow_legacy) for child in element.findall("ref")]
    return {value.id: value for value in values}


def _decode_reference(element: Element, *, allow_legacy: bool = True) -> Reference:
    if not allow_legacy:
        _attributes(element, {"id"})
        _children(element, {"mark", "flow"})
        if len(element.findall("mark")) != 1 or len(element.findall("flow")) != 1:
            raise ValueError("PCEX v3 reference must contain exactly one <mark> and one <flow>")
    try: page, order = map(int, element.get("id", "").split("-", 1))
    except ValueError as error: raise ValueError("<references><ref> has invalid id") from error
    mark_el = element.find("mark")
    if mark_el is None or mark_el.text is None: raise ValueError("<references><ref> missing required <mark>")
    if not allow_legacy: _attributes(mark_el, set())
    from .mark import transform2mark
    mark = transform2mark(mark_el.text) or mark_el.text
    flow = element.find("flow")
    if flow is not None:
        if not allow_legacy: _attributes(flow, set())
        if not allow_legacy and any(asset.get("ref") == "equation" for asset in flow.iter("asset")):
            raise ValueError("PCEX v3 reference uses asset ref='formula', not legacy 'equation'")
        return Reference(page, order, mark, [_decode_flow(child, {}, allow_legacy=allow_legacy) for child in flow])
    if not allow_legacy:
        raise ValueError("PCEX v3 reference must contain <flow>, not legacy body elements")
    values: list[FlowItem] = []
    for child in element:
        if child.tag == "paragraph": values.append(_legacy_paragraph(child, {}))
        elif child.tag == "asset": values.append(_asset_flow(_asset(child, allow_legacy=True)))
    return Reference(page, order, mark, values)


def _encode_reference(ref: Reference) -> Element:
    result = Element("ref", {"id": f"{ref.page_index}-{ref.order}"})
    mark = Element("mark"); mark.text = str(ref.mark); result.append(mark)
    flow = Element("flow")
    for item in ref.flow_items: flow.append(_encode_flow(item))
    result.append(flow); return result


def _parts(items: Iterable[FlowItem]):
    for item in items:
        if isinstance(item, TextFlowItem):
            for child in item.children:
                if isinstance(child, SourceTextFragment): yield from flatten(child.content)
                else:
                    for content in (child.title, child.content, child.caption):
                        yield from flatten(content)
        else:
            for content in (item.asset.title, item.asset.content, item.asset.caption): yield from flatten(content)


def _integer(element: Element, name: str, default: int | None = None) -> int:
    text = element.get(name)
    if text is None:
        if default is not None: return default
        raise ValueError(f"<{element.tag}> missing required attribute '{name}'")
    try: return int(text)
    except ValueError as error: raise ValueError(f"<{element.tag}> attribute '{name}' must be int") from error


def _bbox(element: Element, legacy: str | None = None) -> tuple[int, int, int, int]:
    text = element.get("bbox") or (element.get(legacy) if legacy else None)
    if text is None: raise ValueError(f"<{element.tag}> missing required attribute 'bbox'")
    try: values = tuple(map(int, text.split(",")))
    except ValueError as error: raise ValueError(f"<{element.tag}> bbox must be integers") from error
    if len(values) != 4: raise ValueError(f"<{element.tag}> bbox must have 4 values")
    return cast(tuple[int, int, int, int], values)


def _bbox_text(value: tuple[int, int, int, int]) -> str: return ",".join(map(str, value))


def _attributes(element: Element, allowed: set[str]) -> None:
    unexpected = set(element.attrib) - allowed
    if unexpected:
        raise ValueError(f"PCEX v3 <{element.tag}> has unsupported attributes: {sorted(unexpected)}")


def _children(element: Element, allowed: set[str]) -> None:
    unexpected = {child.tag for child in element} - allowed
    if unexpected:
        raise ValueError(f"PCEX v3 <{element.tag}> has unsupported children: {sorted(unexpected)}")


def _validate_content_members(element: Element) -> None:
    """Validate structural payload attributes without constraining GFM markup.

    Content may legitimately contain the whitelisted HTML tags represented by
    ``HTMLTag``.  Their attributes belong to the Markdown contract.  PCEX's
    own payload elements, on the other hand, have a closed v3 schema even when
    they are nested inside such markup.
    """
    for child in element:
        if child.tag == "inline_expr":
            _attributes(child, {"kind"})
        elif child.tag == "ref":
            _attributes(child, {"id"})
        elif tag_definition(child.tag) is not None:
            _validate_content_members(child)
