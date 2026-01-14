from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from xml.etree.ElementTree import Element

from PIL.Image import Image

from ..common import indent

DeepSeekOCRSize = Literal["tiny", "small", "base", "large", "gundam"]


@dataclass
class Page:
    index: int
    image: Image | None
    body_layouts: list["PageLayout"]
    footnotes_layouts: list["PageLayout"]
    input_tokens: int
    output_tokens: int


@dataclass
class PageLayout:
    ref: str
    det: tuple[int, int, int, int]
    text: str
    order: int
    hash: str | None


@dataclass
class PDFDocumentMetadata:
    title: str | None
    description: str | None
    publisher: str | None
    isbn: str | None
    authors: list[str]
    editors: list[str]
    translators: list[str]
    modified: datetime


def decode(element: Element) -> Page:
    index = int(element.get("index", "0"))
    input_tokens = int(element.get("input_tokens", "0"))
    output_tokens = int(element.get("output_tokens", "0"))
    body_layouts = []
    body_element = element.find("body")
    if body_element is not None:
        for order, layout_element in enumerate(body_element.findall("layout")):
            body_layouts.append(_decode_layout(layout_element, order))

    footnotes_layouts = []
    footnotes_element = element.find("footnotes")
    if footnotes_element is not None:
        for order, layout_element in enumerate(footnotes_element.findall("layout")):
            footnotes_layouts.append(_decode_layout(layout_element, order))

    return Page(
        index=index,
        image=None,
        body_layouts=body_layouts,
        footnotes_layouts=footnotes_layouts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def encode(page: Page) -> Element:
    page_element = Element("page")
    page_element.set("index", str(page.index))
    page_element.set("input_tokens", str(page.input_tokens))
    page_element.set("output_tokens", str(page.output_tokens))
    if page.body_layouts:
        body_element = Element("body")
        for i, layout in enumerate(page.body_layouts):
            assert layout.order == i, (
                f"body_layouts[{i}].order should be {i}, got {layout.order}"
            )
            body_element.append(_encode_layout(layout))
        page_element.append(body_element)
    if page.footnotes_layouts:
        footnotes_element = Element("footnotes")
        for i, layout in enumerate(page.footnotes_layouts):
            assert layout.order == i, (
                f"footnotes_layouts[{i}].order should be {i}, got {layout.order}"
            )
            footnotes_element.append(_encode_layout(layout))
        page_element.append(footnotes_element)
    return indent(page_element)


def _decode_layout(element: Element, order: int) -> PageLayout:
    ref = element.get("ref", "")
    det_str = element.get("det", "0,0,0,0")
    det_list = list(map(int, det_str.split(",")))
    if len(det_list) != 4:
        raise ValueError(f"det must have 4 values, got {len(det_list)}")
    det = (det_list[0], det_list[1], det_list[2], det_list[3])
    text = element.text.strip() if element.text else ""
    hash_value = element.get("hash")
    return PageLayout(
        ref=ref,
        det=det,
        text=text,
        order=order,
        hash=hash_value,
    )


def _encode_layout(layout: PageLayout) -> Element:
    layout_element = Element("layout")
    layout_element.set("ref", layout.ref)
    layout_element.set("det", ",".join(map(str, layout.det)))
    if layout.hash is not None:
        layout_element.set("hash", layout.hash)
    layout_element.text = layout.text
    return layout_element
