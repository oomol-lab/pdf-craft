from collections.abc import Callable
from html import escape as escape_html
from io import StringIO
from xml.etree.ElementTree import Element

from .tag import TagKind
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
    # Escape every ampersand, including literal entity spellings, so a single
    # decoding pass can distinguish "<span>" from the prose "&lt;span&gt;".
    return escape_html(text, quote=False)


def _encode_element(
    buffer: StringIO,
    element: Element,
    indent: int,
    depth: int,
    escape: Callable[[str], str],
    inline: bool = False,
) -> None:
    if not inline:
        _write_indent(buffer, indent, depth)
    if len(element) == 0 and not element.text:
        tag = element_to_tag(element, TagKind.SELF_CLOSING)
        buffer.write(str(tag))
    else:
        text = element.text or ""
        opening_tag = element_to_tag(element, TagKind.OPENING)
        closing_tag = element_to_tag(element, TagKind.CLOSING)
        buffer.write(str(opening_tag))
        # Only the protocol wrapper can be pretty-printed. Even an element
        # with no own text may contain adjacent inline text slots; indenting
        # those changes visible text. Preserve every inner text/tail boundary.
        pretty = (depth == 0 and element.tag == 'xml' and element.text is None
                  and all(child.tail is None for child in element))
        if text:
            buffer.write(escape(text))

        for child in element:
            if pretty:
                buffer.write("\n")
            _encode_element(
                buffer=buffer,
                element=child,
                indent=indent,
                depth=depth + 1,
                escape=escape,
                inline=not pretty,
            )
            child_tail = child.tail or ""
            if child_tail:
                buffer.write(escape(child_tail))

        if pretty and len(element):
            buffer.write("\n")
            _write_indent(buffer, indent, depth)

        buffer.write(str(closing_tag))


def _write_indent(buffer: StringIO, indent: int, depth: int) -> None:
    for _ in range(indent * depth):
        buffer.write(" ")
