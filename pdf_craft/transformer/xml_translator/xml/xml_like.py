import io
import re
import warnings
from html.entities import html5
from typing import IO
from xml.etree.ElementTree import Element, fromstring, tostring

from .self_closing import self_close_void_elements, unclose_void_elements
from .xml import iter_with_stack

_XML_NAMESPACE_URI = "http://www.w3.org/XML/1998/namespace"

_COMMON_NAMESPACES = {
    "http://www.w3.org/1999/xhtml": "xhtml",
    "http://www.idpf.org/2007/ops": "epub",
    "http://www.w3.org/1998/Math/MathML": "m",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://www.daisy.org/z3986/2005/ncx/": "ncx",
    "http://www.idpf.org/2007/opf": "opf",
    "http://www.w3.org/2000/svg": "svg",
    "urn:oasis:names:tc:opendocument:xmlns:container": "container",
    "http://www.w3.org/XML/1998/namespace": "xml",  # Reserved XML namespace
}

_ROOT_NAMESPACES = {
    "http://www.w3.org/1999/xhtml",  # XHTML
    "http://www.daisy.org/z3986/2005/ncx/",  # NCX
    "http://www.idpf.org/2007/opf",  # OPF
    "urn:oasis:names:tc:opendocument:xmlns:container",  # Container
}

_ENCODING_PATTERN = re.compile(r'encoding\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_FIRST_ELEMENT_PATTERN = re.compile(r"<(?![?!])[a-zA-Z]")
_NAMESPACE_IN_TAG = re.compile(r"\{([^}]+)\}")
_HTML_NAMED_ENTITY_PATTERN = re.compile(r"&([A-Za-z][A-Za-z0-9]+);")
_XML_PREDEFINED_ENTITIES = {"amp", "lt", "gt", "apos", "quot"}

# When an attribute name exists in multiple namespaces (e.g., 'type' in XHTML and EPUB ops),
# _attr_to_namespace only records ONE namespace per attribute name. During serialization,
# the global string replacement wrongly adds namespace prefixes to ALL occurrences of that
# attribute, including ones that should remain unprefixed (e.g., <link type="text/css">).
#
# Example problem:
#   Original file has:
#     - <link type="text/css">           (no namespace, standard HTML attribute)
#     - <nav epub:type="toc">             (EPUB ops namespace)
#   After parsing, _attr_to_namespace records: {'type': 'http://www.idpf.org/2007/ops'}
#   During serialization, ALL ' type="' get replaced to ' epub:type="', breaking <link>
#
# This workaround fixes specific known cases where HTML standard attributes should not
# be prefixed, even if the same attribute name appears with a namespace elsewhere.
_STANDARD_HTML_ATTRS = (
    (re.compile(r'<link([^>]*?) epub:type="'), r'<link\1 type="'),  # <link type="...">
    (re.compile(r'<link([^>]*?) epub:rel="'), r'<link\1 rel="'),  # <link rel="...">
)


class XMLLikeNode:
    def __init__(self, file: IO[bytes], is_html_like: bool = False) -> None:
        raw_content = file.read()
        self._is_html_like = is_html_like
        self._encoding: str = self._detect_encoding(raw_content)
        content = raw_content.decode(self._encoding)
        self._header, xml_content = self._extract_header(content)
        self._namespaces: dict[str, str] = {}
        self._tag_to_namespace: dict[str, str] = {}
        self._attr_to_namespace: dict[str, str] = {}

        try:
            # 不必判断类型，这是一个防御性极强的函数，可做到 shit >> XML
            xml_content = self_close_void_elements(xml_content)
            xml_content = _normalize_unquoted_html_entities(xml_content)
            self.element = self._extract_and_clean_namespaces(
                element=fromstring(xml_content),
            )
        except Exception as error:
            raise ValueError("Failed to parse XML-like content") from error

    @property
    def encoding(self) -> str:
        return self._encoding

    @property
    def namespaces(self) -> list[str]:
        return list(self._namespaces.keys())

    def save(self, file: IO[bytes]) -> None:
        writer = io.TextIOWrapper(file, encoding=self._encoding, write_through=True)
        try:
            if self._header:
                writer.write(self._header)

            content = self._serialize_with_namespaces(self.element)

            # For non-standard HTML files (text/html), convert back from <br/> to <br>
            # to maintain compatibility with HTML parsers that don't support XHTML
            # For XHTML files (application/xhtml+xml), keep self-closing format
            if self._is_html_like:
                content = unclose_void_elements(content)

            writer.write(content)

        finally:
            writer.detach()

    def _detect_encoding(self, raw_content: bytes) -> str:
        if raw_content.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        elif raw_content.startswith(b"\xff\xfe"):
            return "utf-16-le"
        elif raw_content.startswith(b"\xfe\xff"):
            return "utf-16-be"

        # 尝试从 XML 声明中提取编码：只读取前 1024 字节来查找 XML 声明
        header_bytes = raw_content[:1024]
        for try_encoding in ("utf-8", "utf-16-le", "utf-16-be", "iso-8859-1"):
            try:
                header_str = header_bytes.decode(try_encoding)
                match = _ENCODING_PATTERN.search(header_str)
                if match:
                    declared_encoding = match.group(1).lower()
                    try:
                        raw_content.decode(declared_encoding)
                        return declared_encoding
                    except (LookupError, UnicodeDecodeError):
                        pass
            except UnicodeDecodeError:
                continue

        try:
            raw_content.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            pass
        return "iso-8859-1"

    def _extract_header(self, content: str) -> tuple[str, str]:
        match = _FIRST_ELEMENT_PATTERN.search(content)
        if match:
            split_pos = match.start()
            header = content[:split_pos]
            xml_content = content[split_pos:]
            return header, xml_content
        return "", content

    def _extract_and_clean_namespaces(self, element: Element) -> Element:
        for _, elem in iter_with_stack(element):
            match = _NAMESPACE_IN_TAG.match(elem.tag)
            if match:
                namespace_uri = match.group(1)
                if namespace_uri not in self._namespaces:
                    prefix = _COMMON_NAMESPACES.get(namespace_uri, f"ns{len(self._namespaces)}")
                    self._namespaces[namespace_uri] = prefix

                tag_name = elem.tag[len(match.group(0)) :]

                # Record tag -> namespace mapping (warn if conflict)
                if tag_name in self._tag_to_namespace and self._tag_to_namespace[tag_name] != namespace_uri:
                    warnings.warn(
                        f"Tag '{tag_name}' has multiple namespaces: "
                        f"{self._tag_to_namespace[tag_name]} and {namespace_uri}. "
                        f"Using the first one.",
                        stacklevel=2,
                    )
                else:
                    self._tag_to_namespace[tag_name] = namespace_uri

                # Clean: remove namespace URI completely
                elem.tag = tag_name

            for attr_key in list(elem.attrib.keys()):
                match = _NAMESPACE_IN_TAG.match(attr_key)
                if match:
                    namespace_uri = match.group(1)
                    if namespace_uri not in self._namespaces:
                        prefix = _COMMON_NAMESPACES.get(namespace_uri, f"ns{len(self._namespaces)}")
                        self._namespaces[namespace_uri] = prefix

                    attr_name = attr_key[len(match.group(0)) :]
                    attr_value = elem.attrib.pop(attr_key)

                    # Record attr -> namespace mapping (warn if conflict)
                    if attr_name in self._attr_to_namespace and self._attr_to_namespace[attr_name] != namespace_uri:
                        warnings.warn(
                            f"Attribute '{attr_name}' has multiple namespaces: "
                            f"{self._attr_to_namespace[attr_name]} and {namespace_uri}. "
                            f"Using the first one.",
                            stacklevel=2,
                        )
                    else:
                        self._attr_to_namespace[attr_name] = namespace_uri

                    # Clean: remove namespace URI completely
                    elem.attrib[attr_name] = attr_value
        return element

    def _serialize_with_namespaces(self, element: Element) -> str:
        # First, add namespace declarations to root element (before serialization)
        for namespace_uri, prefix in self._namespaces.items():
            # Skip the reserved xml namespace - it's implicit
            if namespace_uri == _XML_NAMESPACE_URI:
                continue
            if namespace_uri in _ROOT_NAMESPACES:
                element.attrib["xmlns"] = namespace_uri
            else:
                element.attrib[f"xmlns:{prefix}"] = namespace_uri

        # Serialize the element tree as-is (tags are simple names without prefixes)
        xml_string = tostring(element, encoding="unicode")

        # Now restore namespace prefixes in the serialized string
        # For each tag that should have a namespace prefix, wrap it with the prefix
        for tag_name, namespace_uri in self._tag_to_namespace.items():
            if namespace_uri not in _ROOT_NAMESPACES:
                # Get the prefix for this namespace
                prefix = self._namespaces[namespace_uri]
                # Replace opening and closing tags
                xml_string = xml_string.replace(f"<{tag_name} ", f"<{prefix}:{tag_name} ")
                xml_string = xml_string.replace(f"<{tag_name}>", f"<{prefix}:{tag_name}>")
                xml_string = xml_string.replace(f"</{tag_name}>", f"</{prefix}:{tag_name}>")
                xml_string = xml_string.replace(f"<{tag_name}/>", f"<{prefix}:{tag_name}/>")

        for attr_name, namespace_uri in self._attr_to_namespace.items():
            if namespace_uri not in _ROOT_NAMESPACES:
                prefix = self._namespaces[namespace_uri]
                xml_string = xml_string.replace(f' {attr_name}="', f' {prefix}:{attr_name}="')

        # Apply workaround to fix standard HTML attributes (see _STANDARD_HTML_ATTRS comment)
        for pattern, replacement in _STANDARD_HTML_ATTRS:
            xml_string = pattern.sub(replacement, xml_string)

        return xml_string


def _normalize_unquoted_html_entities(content: str) -> str:
    result: list[str] = []
    index = 0
    quote: str | None = None
    in_tag = False

    while index < len(content):
        char = content[index]

        if char == "<" and quote is None:
            in_tag = True
        elif char == ">" and quote is None:
            in_tag = False

        if in_tag and char in ("'", '"'):
            quote = None if quote == char else char if quote is None else quote
            result.append(char)
            index += 1
            continue

        if quote is None and char == "&":
            match = _HTML_NAMED_ENTITY_PATTERN.match(content, index)
            if match:
                entity_name = match.group(1)
                replacement = _html_entity_to_numeric_reference(entity_name)
                if replacement is not None:
                    result.append(replacement)
                    index = match.end()
                    continue

        result.append(char)
        index += 1

    return "".join(result)


def _html_entity_to_numeric_reference(entity_name: str) -> str | None:
    if entity_name in _XML_PREDEFINED_ENTITIES:
        return None

    value = html5.get(f"{entity_name};")
    if value is None:
        return None

    return "".join(f"&#{ord(char)};" for char in value)
