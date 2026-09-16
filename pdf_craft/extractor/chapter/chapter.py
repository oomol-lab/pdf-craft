"""PCEX v3 chapter flow model and XML codec.

``FlowItem`` preserves an author paragraph even when a figure/table interrupts
it.  Display equations are separate flow items, so joining text can never leap
over them.  Deprecated layout names below are compatibility aliases only.
"""
from dataclasses import dataclass
from typing import Generator, Iterable, TypeAlias, Union, cast
from xml.etree.ElementTree import Element

from ...common import ASSET_TAGS, AssetRef, indent
from ...expression import ExpressionKind, decode_expression_kind, encode_expression_kind
from ...markdown.paragraph import HTMLTag, flatten
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


def _role(value: str) -> str:
    return {"text": "body", "title": "heading", "sub_title": "heading"}.get(value, value)


def _ref(value: str) -> str:
    return {"body": "text", "heading": "sub_title"}.get(value, value)


@dataclass(init=False)
class SourceTextFragment:
    page_index: int
    source_order: int
    bbox: tuple[int, int, int, int]
    content: Content

    def __init__(self, page_index: int, source_order: int | None = None,
                 bbox: tuple[int, int, int, int] | None = None, content: Content | None = None,
                 *, order: int | None = None, det: tuple[int, int, int, int] | None = None):
        self.page_index = page_index
        self.source_order = source_order if source_order is not None else cast(int, order)
        self.bbox = bbox if bbox is not None else cast(tuple[int, int, int, int], det)
        self.content = content or []

    @property
    def order(self): return self.source_order
    @order.setter
    def order(self, value): self.source_order = value
    @property
    def det(self): return self.bbox
    @det.setter
    def det(self, value): self.bbox = value


@dataclass(init=False)
class SourceAsset:
    page_index: int
    ref: AssetRef
    bbox: tuple[int, int, int, int]
    title: Content
    content: Content
    caption: Content
    asset_hash: str | None

    def __init__(self, page_index: int, ref: AssetRef,
                 bbox: tuple[int, int, int, int] | None = None, title: Content | None = None,
                 content: Content | None = None, caption: Content | None = None,
                 asset_hash: str | None = None, *, det: tuple[int, int, int, int] | None = None,
                 hash: str | None = None):
        self.page_index, self.ref = page_index, ref
        if self.ref == "equation":  # source/OCR and v1/v2 constructor compatibility
            self.ref = cast(AssetRef, "formula")
        self.bbox = bbox if bbox is not None else cast(tuple[int, int, int, int], det)
        self.title, self.content, self.caption = title or [], content or [], caption or []
        self.asset_hash = asset_hash if asset_hash is not None else hash

    @property
    def det(self): return self.bbox
    @det.setter
    def det(self, value): self.bbox = value
    @property
    def hash(self): return self.asset_hash
    @hash.setter
    def hash(self, value): self.asset_hash = value


@dataclass(init=False)
class TextFlowItem:
    role: str
    level: int
    children: list[SourceTextFragment | SourceAsset]

    def __init__(self, role: str | None = None, level: int = -1,
                 children: list[SourceTextFragment | SourceAsset] | None = None,
                 *, ref: str | None = None, blocks: list[SourceTextFragment] | None = None):
        raw = role if role is not None else ref
        if raw is None: raise TypeError("TextFlowItem requires role")
        self.role, self.level = _role(raw), level
        if self.role not in {"body", "heading"}:
            raise ValueError("TextFlowItem role must be body or heading")
        self.children = children if children is not None else list(blocks or [])
        if any(isinstance(child, SourceAsset) and child.ref == "formula" for child in self.children):
            raise ValueError("TextFlowItem cannot contain an equation SourceAsset; use DisplayFormula")

    @property
    def ref(self): return _ref(self.role)
    @ref.setter
    def ref(self, value): self.role = _role(value)
    @property
    def blocks(self): return [v for v in self.children if isinstance(v, SourceTextFragment)]
    @blocks.setter
    def blocks(self, value): self.children = value


@dataclass
class DisplayFormula:
    asset: SourceAsset
    def __post_init__(self):
        if self.asset.ref != "formula": raise ValueError("DisplayFormula requires formula asset")


@dataclass
class StandaloneAsset:
    asset: SourceAsset
    def __post_init__(self):
        if self.asset.ref == "formula": raise ValueError("formula must be DisplayFormula")


FlowItem: TypeAlias = TextFlowItem | DisplayFormula | StandaloneAsset


def _flow(value: FlowItem | SourceAsset) -> FlowItem:
    if isinstance(value, SourceAsset):
        return DisplayFormula(value) if value.ref == "formula" else StandaloneAsset(value)
    return value


@dataclass(init=False)
class Chapter:
    id: int | None
    level: int
    flow_items: list[FlowItem]

    def __init__(self, id: int | None, level: int, flow_items: Iterable[FlowItem | SourceAsset] | None = None,
                 *, layouts: Iterable[TextFlowItem | SourceAsset] | None = None):
        self.id, self.level = id, level
        self.flow_items = [_flow(v) for v in (flow_items if flow_items is not None else (layouts or []))]

    @property
    def layouts(self):
        return [v if isinstance(v, TextFlowItem) else v.asset for v in self.flow_items]
    @layouts.setter
    def layouts(self, values): self.flow_items = [_flow(v) for v in values]


@dataclass(init=False)
class Reference:
    page_index: int
    order: int
    mark: str | Mark
    flow_items: list[FlowItem]
    def __init__(self, page_index: int, order: int, mark: str | Mark,
                 flow_items: Iterable[FlowItem | SourceAsset] | None = None,
                 *, layouts: Iterable[TextFlowItem | SourceAsset] | None = None):
        self.page_index, self.order, self.mark = page_index, order, mark
        self.flow_items = [_flow(v) for v in (flow_items if flow_items is not None else (layouts or []))]
    @property
    def id(self): return self.page_index, self.order
    @property
    def layouts(self): return [v if isinstance(v, TextFlowItem) else v.asset for v in self.flow_items]


# One-release source compatibility: values are nevertheless v3 objects.
ParagraphLayout = TextFlowItem
BlockLayout = SourceTextFragment
AssetLayout = SourceAsset


def references_to_map(references: Iterable[Reference]) -> RefIdMap:
    return {ref.id: index for index, ref in enumerate(references, 1)}


def search_references_in_chapter(chapter: Chapter) -> Generator[Reference, None, None]:
    seen: set[tuple[int, int]] = set()
    for part in _parts(chapter.flow_items):
        if isinstance(part, Reference) and part.id not in seen:
            seen.add(part.id); yield part


def decode(element: Element, *, allow_legacy: bool = True) -> Chapter:
    refs = _refs(element.find("references"), allow_legacy=allow_legacy)
    id_text = element.get("id")
    ident = int(id_text) if id_text is not None else None
    level = int(element.get("level", "-1"))
    flow = element.find("flow")
    if flow is not None:
        if not allow_legacy and any(asset.get("ref") == "equation" for asset in flow.iter("asset")):
            raise ValueError("PCEX v3 uses asset ref='formula', not legacy 'equation'")
        return Chapter(ident, level, [_decode_flow(v, refs) for v in flow])
    # v1/v2 in-memory migration; historic sibling assets cannot acquire a
    # fictional nested relation, but formulas become explicit boundaries.
    body = element.find("body")
    if body is None: raise ValueError("<chapter> missing required <flow> element")
    if not allow_legacy: raise ValueError("PCEX v3 chapter must contain <flow>, not legacy <body>")
    items: list[FlowItem] = []
    for child in body:
        if child.tag == "paragraph": items.append(_legacy_paragraph(child, refs))
        elif child.tag == "asset": items.append(_flow(_asset(child, refs)))
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


def _decode_flow(element: Element, refs: dict[tuple[int, int], Reference]) -> FlowItem:
    if element.tag == "text":
        role = element.get("role")
        if role not in {"body", "heading"}: raise ValueError("<text> role must be body or heading")
        children = []
        for child in element:
            if child.tag == "fragment": children.append(_fragment(child, refs))
            elif child.tag == "inline_expr":
                # XMLTranslator may move a frozen inline formula beside the
                # fragment that owns it.  Restore it to that fragment rather
                # than treating a harmless transport shape as chapter loss.
                if not children or not isinstance(children[-1], SourceTextFragment):
                    raise ValueError("<text><inline_expr> has no preceding fragment")
                kind = child.get("kind")
                if kind is None: raise ValueError("<text><inline_expr> missing kind")
                expression = InlineExpression(decode_expression_kind(kind), child.text or "")
                if not any(isinstance(value, InlineExpression) and value == expression for value in flatten(children[-1].content)):
                    children[-1].content.append(expression)
            elif child.tag == "asset":
                asset = _asset(child, refs)
                if asset.ref == "formula": raise ValueError("text cannot contain formula asset")
                children.append(asset)
            else: raise ValueError(f"<text> contains unknown element: <{child.tag}>")
        return TextFlowItem(role, _integer(element, "level", -1), children)
    if element.tag in {"display-formula", "standalone-asset"}:
        children = list(element)
        if len(children) != 1 or children[0].tag != "asset": raise ValueError(f"<{element.tag}> must contain exactly one <asset>")
        asset = _asset(children[0], refs)
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


def _asset(element: Element, refs: dict[tuple[int, int], Reference] | None = None) -> SourceAsset:
    ref = element.get("ref")
    if ref == "equation":
        ref = "formula"
    if ref not in ASSET_TAGS: raise ValueError(f"<asset> attribute 'ref' must be one of {ASSET_TAGS}, got: {ref}")
    return SourceAsset(_integer(element, "page_index"), cast(AssetRef, ref), _bbox(element, "det"),
        _content(element.find("title"), refs, "asset"), _content(element.find("content"), refs, "asset"),
        _content(element.find("caption"), refs, "asset"), element.get("asset_hash", element.get("hash")))


def _encode_asset(asset: SourceAsset) -> Element:
    result = Element("asset", {"ref": asset.ref, "page_index": str(asset.page_index), "bbox": _bbox_text(asset.bbox)})
    if asset.asset_hash is not None: result.set("asset_hash", asset.asset_hash)
    for name, value in (("title", asset.title), ("content", asset.content), ("caption", asset.caption)):
        if value:
            node = Element(name); encode_content(node, value, _encode_member); result.append(node)
    return result


def _legacy_paragraph(element: Element, refs: dict[tuple[int, int], Reference]) -> TextFlowItem:
    ref = element.get("ref")
    if ref is None: raise ValueError("<paragraph> missing required attribute 'ref'")
    return TextFlowItem(ref, _integer(element, "level", -1), [_legacy_block(v, refs) for v in element.findall("block")])


def _fragment(element: Element, refs: dict[tuple[int, int], Reference]) -> SourceTextFragment:
    return SourceTextFragment(_integer(element, "page_index"), _integer(element, "source_order"), _bbox(element), _content(element, refs, "fragment"))


def _legacy_block(element: Element, refs: dict[tuple[int, int], Reference]) -> SourceTextFragment:
    return SourceTextFragment(_integer(element, "page_index"), _integer(element, "order"), _bbox(element, "det"), _content(element, refs, "block"))


def _encode_fragment(fragment: SourceTextFragment) -> Element:
    result = Element("fragment", {"page_index": str(fragment.page_index), "source_order": str(fragment.source_order), "bbox": _bbox_text(fragment.bbox)})
    encode_content(result, fragment.content, _encode_member); return result


def _content(element: Element | None, refs: dict[tuple[int, int], Reference] | None, context: str) -> Content:
    if element is None: return []
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
    values = [_decode_reference(child, allow_legacy=allow_legacy) for child in element.findall("ref")]
    return {value.id: value for value in values}


def _decode_reference(element: Element, *, allow_legacy: bool = True) -> Reference:
    try: page, order = map(int, element.get("id", "").split("-", 1))
    except ValueError as error: raise ValueError("<references><ref> has invalid id") from error
    mark_el = element.find("mark")
    if mark_el is None or mark_el.text is None: raise ValueError("<references><ref> missing required <mark>")
    from .mark import transform2mark
    mark = transform2mark(mark_el.text) or mark_el.text
    flow = element.find("flow")
    if flow is not None:
        if not allow_legacy and any(asset.get("ref") == "equation" for asset in flow.iter("asset")):
            raise ValueError("PCEX v3 reference uses asset ref='formula', not legacy 'equation'")
        return Reference(page, order, mark, [_decode_flow(child, {}) for child in flow])
    if not allow_legacy:
        raise ValueError("PCEX v3 reference must contain <flow>, not legacy body elements")
    values: list[FlowItem] = []
    for child in element:
        if child.tag == "paragraph": values.append(_legacy_paragraph(child, {}))
        elif child.tag == "asset": values.append(_flow(_asset(child)))
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
