from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element

from pdf_craft.transformer.xml_translator.xml import XMLLikeNode, plain_text
from .common import find_opf_path, strip_namespace
from .zip import Zip


@dataclass
class Toc:
    """
    EPUB 2.0 对应关系:
        - title <-> <navLabel><text>
        - href <-> <content src> (不包含 # 后的部分)
        - fragment <-> <content src> (# 后的部分)
        - children <-> 嵌套的 <navPoint>
        - id <-> <navPoint id>

    EPUB 3.0 对应关系:
        - title <-> <a> 标签的文本内容
        - href <-> <a href> (不包含 # 后的部分)
        - fragment <-> <a href> (# 后的部分)
        - children <-> 嵌套的 <ol><li>
        - id <-> <li id> 或 <a id>
    """

    title: str
    href: str | None = None
    fragment: str | None = None
    id: str | None = None
    children: list["Toc"] = field(default_factory=list)

    @property
    def full_href(self) -> str | None:
        if self.href is None:
            return None
        if self.fragment:
            return f"{self.href}#{self.fragment}"
        return self.href


@dataclass
class TocContext:
    version: int
    toc_path: Path
    xml_node: XMLLikeNode


def read_toc(zip: Zip) -> tuple[list[Toc], TocContext]:
    version = _detect_epub_version(zip)
    toc_path = _find_toc_path(zip, version)

    if toc_path is None:
        raise ValueError("Cannot find TOC file in EPUB")

    with zip.read(toc_path) as f:
        xml_node = XMLLikeNode(f, is_html_like=False)

    if version == 3:
        toc_list = _read_nav_toc(xml_node.element)
    else:
        toc_list = _read_ncx_toc(xml_node.element)

    context = TocContext(version=version, toc_path=toc_path, xml_node=xml_node)
    return toc_list, context


def write_toc(zip: Zip, toc: list[Toc], context: TocContext) -> None:
    if context.version == 2:
        _update_ncx_toc(context.xml_node.element, toc)
    else:
        _update_nav_toc(context.xml_node.element, toc)

    with zip.replace(context.toc_path) as f:
        context.xml_node.save(f)


def _detect_epub_version(zip: Zip) -> int:
    opf_path = find_opf_path(zip)
    with zip.read(opf_path) as f:
        content = f.read()
        root = ET.fromstring(content)
        version_str = root.get("version", "2.0")

        if version_str.startswith("3"):
            return 3
        else:
            return 2


def _find_toc_path(zip: Zip, version: int) -> Path | None:
    opf_path = find_opf_path(zip)
    opf_dir = opf_path.parent

    with zip.read(opf_path) as f:
        content = f.read()
        root = ET.fromstring(content)
        strip_namespace(root)

        manifest = root.find(".//manifest")
        if manifest is None:
            return None

        if version == 2:
            # EPUB 2: 查找 NCX 文件 (media-type="application/x-dtbncx+xml")
            for item in manifest.findall("item"):
                media_type = item.get("media-type")
                if media_type == "application/x-dtbncx+xml":
                    href = item.get("href")
                    if href:
                        return opf_dir / href
        else:
            # EPUB 3: 查找 nav 文件 (properties="nav")
            for item in manifest.findall("item"):
                properties = item.get("properties", "")
                if "nav" in properties.split():
                    href = item.get("href")
                    if href:
                        return opf_dir / href

        return None


def _read_ncx_toc(root: Element) -> list[Toc]:
    nav_map = root.find(".//navMap")
    if nav_map is None:
        return []

    result = []
    for nav_point in nav_map.findall("navPoint"):
        toc_item = _parse_nav_point(nav_point)
        if toc_item:
            result.append(toc_item)

    return result


def _parse_nav_point(nav_point: Element) -> Toc | None:
    nav_id = nav_point.get("id")
    nav_label = nav_point.find("navLabel")
    if nav_label is None:
        return None

    text_elem = nav_label.find("text")
    if text_elem is None:
        return None

    title = plain_text(text_elem).strip()
    if not title:
        return None

    content_elem = nav_point.find("content")
    href = None
    fragment = None

    if content_elem is not None:
        src = content_elem.get("src")
        if src:
            href, fragment = _split_href(src)

    children = []
    for child_nav_point in nav_point.findall("navPoint"):
        child_toc = _parse_nav_point(child_nav_point)
        if child_toc:
            children.append(child_toc)

    return Toc(
        title=title,
        href=href,
        fragment=fragment,
        id=nav_id,
        children=children,
    )


def _update_ncx_toc(root: Element, toc_list: list[Toc]) -> None:
    nav_map = root.find(".//navMap")
    if nav_map is None:
        raise ValueError("Cannot find navMap in NCX file")
    _update_nav_points(nav_map, toc_list, None)


def _update_nav_points(parent: Element, toc_list: list[Toc], ns: str | None, start_play_order: int = 1) -> int:
    tag_prefix = f"{{{ns}}}" if ns else ""
    nav_point_tag = f"{tag_prefix}navPoint"
    existing_nav_points = [elem for elem in parent if elem.tag == nav_point_tag]
    matched_pairs = _match_toc_with_elements(toc_list, existing_nav_points)
    for nav_point in existing_nav_points:
        parent.remove(nav_point)

    play_order = start_play_order
    for toc, existing_elem in matched_pairs:
        if existing_elem is not None:
            nav_point = existing_elem
            _update_nav_point_content(nav_point, toc, ns, play_order)
        else:
            nav_point = _create_nav_point(toc, ns, play_order)

        parent.append(nav_point)
        play_order += 1
        play_order = _update_nav_points(nav_point, toc.children, ns, play_order)

    return play_order


def _update_nav_point_content(nav_point: Element, toc: Toc, ns: str | None, play_order: int) -> None:
    tag_prefix = f"{{{ns}}}" if ns else ""
    if toc.id:
        nav_point.set("id", toc.id)

    nav_point.set("playOrder", str(play_order))

    nav_label = nav_point.find(f"{tag_prefix}navLabel")
    if nav_label is not None:
        text_elem = nav_label.find(f"{tag_prefix}text")
        if text_elem is not None:
            text_elem.text = toc.title

    content_elem = nav_point.find(f"{tag_prefix}content")
    if content_elem is not None and toc.href is not None:
        full_href = toc.full_href
        if full_href:
            content_elem.set("src", full_href)


def _create_nav_point(toc: Toc, ns: str | None, play_order: int) -> Element:
    tag_prefix = f"{{{ns}}}" if ns else ""

    nav_point = Element(f"{tag_prefix}navPoint")
    if toc.id:
        nav_point.set("id", toc.id)
    else:
        nav_point.set("id", f"navPoint-{play_order}")
    nav_point.set("playOrder", str(play_order))

    nav_label = Element(f"{tag_prefix}navLabel")
    text_elem = Element(f"{tag_prefix}text")
    text_elem.text = toc.title
    nav_label.append(text_elem)
    nav_point.append(nav_label)

    if toc.href is not None:
        content_elem = Element(f"{tag_prefix}content")
        full_href = toc.full_href
        if full_href:
            content_elem.set("src", full_href)
        nav_point.append(content_elem)

    return nav_point


def _read_nav_toc(root: Element) -> list[Toc]:
    nav_elem = None
    for nav in root.findall(".//nav"):
        epub_type = nav.get("type")
        if epub_type == "toc":
            nav_elem = nav
            break

    if nav_elem is None:
        return []

    ol = nav_elem.find(".//ol")
    if ol is None:
        return []

    result = []
    for li in ol.findall("li"):
        toc_item = _parse_nav_li(li)
        if toc_item:
            result.append(toc_item)

    return result


def _parse_nav_li(li: Element) -> Toc | None:
    li_id = li.get("id")
    a = li.find("a")
    if a is None:
        span = li.find("span")
        if span is not None:
            title = plain_text(span).strip()
            if not title:
                return None
            href = None
            fragment = None
            a_id = None
        else:
            return None
    else:
        title = plain_text(a).strip()
        if not title:
            return None

        a_id = a.get("id")
        href_attr = a.get("href")

        if href_attr:
            href, fragment = _split_href(href_attr)
        else:
            href = None
            fragment = None

    final_id = li_id if li_id else (a_id if "a_id" in locals() else None)
    children = []
    child_ol = li.find("ol")
    if child_ol is not None:
        for child_li in child_ol.findall("li"):
            child_toc = _parse_nav_li(child_li)
            if child_toc:
                children.append(child_toc)

    return Toc(
        title=title,
        href=href,
        fragment=fragment,
        id=final_id,
        children=children,
    )


def _update_nav_toc(root: Element, toc_list: list[Toc]) -> None:
    nav_elem = None
    for nav in root.findall(".//nav"):
        epub_type = nav.get("type")
        if epub_type == "toc":
            nav_elem = nav
            break

    if nav_elem is None:
        raise ValueError("Cannot find nav element with type='toc'")

    ol = nav_elem.find(".//ol")
    if ol is None:
        raise ValueError("Cannot find ol in nav element")

    _update_nav_lis(ol, toc_list, None)


def _update_nav_lis(ol: Element, toc_list: list[Toc], ns: str | None) -> None:
    tag_prefix = f"{{{ns}}}" if ns else ""
    li_tag = f"{tag_prefix}li"
    existing_lis = [elem for elem in ol if elem.tag == li_tag]
    matched_pairs = _match_toc_with_elements(toc_list, existing_lis)

    for li in existing_lis:
        ol.remove(li)

    for toc, existing_elem in matched_pairs:
        if existing_elem is not None:
            li = existing_elem
            _update_nav_li_content(li, toc, ns)
        else:
            li = _create_nav_li(toc, ns)

        ol.append(li)

        if toc.children:
            child_ol = li.find(f"{tag_prefix}ol")
            if child_ol is None:
                child_ol = Element(f"{tag_prefix}ol")
                li.append(child_ol)
            _update_nav_lis(child_ol, toc.children, ns)


def _update_nav_li_content(li: Element, toc: Toc, ns: str | None) -> None:
    tag_prefix = f"{{{ns}}}" if ns else ""
    if toc.id:
        li.set("id", toc.id)

    a = li.find(f"{tag_prefix}a")
    span = li.find(f"{tag_prefix}span")

    if toc.href is not None:
        if a is not None:
            a.text = toc.title
            full_href = toc.full_href
            if full_href:
                a.set("href", full_href)
        elif span is not None:
            li.remove(span)
            a = Element(f"{tag_prefix}a")
            a.text = toc.title
            full_href = toc.full_href
            if full_href:
                a.set("href", full_href)
            li.insert(0, a)
    else:
        if span is not None:
            span.text = toc.title
        elif a is not None:
            li.remove(a)
            span = Element(f"{tag_prefix}span")
            span.text = toc.title
            li.insert(0, span)


def _create_nav_li(toc: Toc, ns: str | None) -> Element:
    tag_prefix = f"{{{ns}}}" if ns else ""
    li = Element(f"{tag_prefix}li")

    if toc.id:
        li.set("id", toc.id)

    if toc.href is not None:
        a = Element(f"{tag_prefix}a")
        a.text = toc.title
        full_href = toc.full_href
        if full_href:
            a.set("href", full_href)
        li.append(a)
    else:
        span = Element(f"{tag_prefix}span")
        span.text = toc.title
        li.append(span)

    return li


def _split_href(href: str) -> tuple[str | None, str | None]:
    if "#" in href:
        parts = href.split("#", 1)
        return parts[0] if parts[0] else None, parts[1] if parts[1] else None
    else:
        return href, None


def _match_toc_with_elements(toc_list: list[Toc], elements: list[Element]) -> list[tuple[Toc, Element | None]]:
    """
    使用混合策略匹配 Toc 对象和 XML 元素

    策略优先级：
    1. 通过 id 匹配
    2. 通过 href 匹配
    3. 通过位置匹配
    """
    result = []
    used_elements = set()

    for toc in toc_list:
        matched = None
        if toc.id:
            for i, elem in enumerate(elements):
                if i in used_elements:
                    continue
                elem_id = elem.get("id")
                if elem_id == toc.id:
                    matched = elem
                    used_elements.add(i)
                    break
        result.append((toc, matched))

    for i, (toc, matched) in enumerate(result):
        if matched is None and toc.href:
            for j, elem in enumerate(elements):
                if j in used_elements:
                    continue
                elem_href = _extract_href_from_element(elem)
                if elem_href and elem_href == toc.full_href:
                    result[i] = (toc, elem)
                    used_elements.add(j)
                    break

    unmatched_indices = [i for i, (_, matched) in enumerate(result) if matched is None]
    available_elements = [elem for j, elem in enumerate(elements) if j not in used_elements]

    for i, elem in zip(unmatched_indices, available_elements):
        toc, _ = result[i]
        result[i] = (toc, elem)

    return result


def _extract_href_from_element(elem: Element) -> str | None:
    # NCX 格式：查找 content/@src
    content = elem.find(".//content")
    if content is not None:
        return content.get("src")

    # nav 格式：查找 a/@href
    a = elem.find(".//a")
    if a is not None:
        return a.get("href")

    return None
