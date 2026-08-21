from collections.abc import Callable
from html import escape as escape_html
from io import StringIO
from xml.etree.ElementTree import Element

from .parser import parse_tags
from .tag import Tag, TagKind
from .transform import element_to_tag


# why implement XML encoding?
# https://github.com/oomol-lab/pdf-craft/issues/149
def encode_friendly(element: Element, indent: int = 2) -> str:
    buffer = StringIO()
    _encode_element(
        buffer=buffer,
        element=element,
        indent=indent,
        depth=0,
        escape=_escape_text,
    )
    return buffer.getvalue()


def _escape_text(text: str) -> str:
    buffer = StringIO()
    for cell in parse_tags(text):
        if isinstance(cell, Tag):
            cell = escape_html(str(cell))
        buffer.write(cell)
    return buffer.getvalue()


_TINY_TEXT_LEN = 35


def _encode_element(
    buffer: StringIO,
    element: Element,
    indent: int,
    depth: int,
    escape: Callable[[str], str],
) -> None:
    _write_indent(buffer, indent, depth)
    if len(element) == 0 and not element.text:
        tag = element_to_tag(element, TagKind.SELF_CLOSING)
        buffer.write(str(tag))
    else:
        text = (element.text or "").strip()
        opening_tag = element_to_tag(element, TagKind.OPENING)
        closing_tag = element_to_tag(element, TagKind.CLOSING)
        buffer.write(str(opening_tag))
        is_one_line = len(text) <= _TINY_TEXT_LEN and len(element) == 0 and "\n" not in text
        if text:
            if not is_one_line:
                buffer.write("\n")
                _write_indent(buffer, indent, depth + 1)
            buffer.write(escape(text))

        for child in element:
            buffer.write("\n")
            _encode_element(
                buffer=buffer,
                element=child,
                indent=indent,
                depth=depth + 1,
                escape=escape,
            )
            child_tail = (child.tail or "").strip()
            if child_tail:
                buffer.write("\n")
                _write_indent(buffer, indent, depth + 1)
                buffer.write(escape(child_tail))

        if not is_one_line:
            buffer.write("\n")
            _write_indent(buffer, indent, depth)

        buffer.write(str(closing_tag))


def _write_indent(buffer: StringIO, indent: int, depth: int) -> None:
    for _ in range(indent * depth):
        buffer.write(" ")
