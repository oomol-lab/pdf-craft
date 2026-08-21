"""
EPUB 数据结构与 XML 的编码/解码转换

将 Toc 和 MetadataField 等数据结构转换为 XML Element，以便进行翻译处理。
"""

from xml.etree.ElementTree import Element

from pdf_craft.transformer.xml_translator.epub import Toc
from pdf_craft.transformer.xml_translator.epub.metadata import MetadataField


def encode_toc(toc: Toc) -> Element:
    elem = Element("toc-item")

    if toc.href is not None:
        elem.set("href", toc.href)
    if toc.fragment is not None:
        elem.set("fragment", toc.fragment)
    if toc.id is not None:
        elem.set("id", toc.id)

    title_elem = Element("title")
    title_elem.text = toc.title
    elem.append(title_elem)

    for child in toc.children:
        child_elem = encode_toc(child)
        elem.append(child_elem)

    return elem


def decode_toc(elem: Element) -> Toc:
    href = elem.get("href")
    fragment = elem.get("fragment")
    toc_id = elem.get("id")

    title_elem = elem.find("title")
    if title_elem is None or title_elem.text is None:
        raise ValueError("Missing title element in toc-item")
    title = title_elem.text

    children = []
    for child_elem in elem.findall("toc-item"):
        child_toc = decode_toc(child_elem)
        children.append(child_toc)

    return Toc(
        title=title,
        href=href,
        fragment=fragment,
        id=toc_id,
        children=children,
    )


def encode_toc_list(toc_list: list[Toc]) -> Element:
    root = Element("toc-list")

    for toc in toc_list:
        toc_elem = encode_toc(toc)
        root.append(toc_elem)

    return root


def decode_toc_list(elem: Element) -> list[Toc]:
    if elem.tag != "toc-list":
        raise ValueError(f"Expected 'toc-list' element, got '{elem.tag}'")

    toc_list = []
    for toc_elem in elem.findall("toc-item"):
        toc = decode_toc(toc_elem)
        toc_list.append(toc)

    return toc_list


def encode_metadata(fields: list[MetadataField]) -> Element:
    root = Element("metadata-list")

    for field in fields:
        field_elem = Element("field")
        field_elem.set("tag", field.tag_name)
        field_elem.text = field.text
        root.append(field_elem)

    return root


def decode_metadata(elem: Element) -> list[MetadataField]:
    if elem.tag != "metadata-list":
        raise ValueError(f"Expected 'metadata-list' element, got '{elem.tag}'")

    fields = []
    for field_elem in elem.findall("field"):
        tag_name = field_elem.get("tag")
        if tag_name is None:
            raise ValueError("Missing 'tag' attribute in field element")

        text = field_elem.text
        if text is None:
            raise ValueError(f"Missing text content in field element (tag={tag_name})")

        fields.append(MetadataField(tag_name=tag_name, text=text))

    return fields
