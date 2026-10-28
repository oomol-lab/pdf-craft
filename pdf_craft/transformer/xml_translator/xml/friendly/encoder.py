from collections.abc import Callable
from html import escape as escape_html
from io import StringIO
from xml.etree.ElementTree import Element
from ....whitespace import WhiteSpace, mode_for, normalize, can_indent

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
    inherited: WhiteSpace = WhiteSpace.NORMAL,
) -> None:
    mode = mode_for(element, inherited)
    if not inline:
        _write_indent(buffer, indent, depth)
    if len(element) == 0 and not element.text:
        tag = element_to_tag(element, TagKind.SELF_CLOSING)
        buffer.write(str(tag))
    else:
        text = normalize(element.text or "", mode)
        opening_tag = element_to_tag(element, TagKind.OPENING)
        closing_tag = element_to_tag(element, TagKind.CLOSING)
        buffer.write(str(opening_tag))
        # Only structural inter-block gaps are disposable indentation. Mixed
        # text/inline content follows the same policy used during acceptance.
        pretty = can_indent(element, mode)
        if text and not pretty:
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
                inherited=mode,
            )
            child_tail = normalize(child.tail or "", mode)
            if child_tail and not pretty:
                buffer.write(escape(child_tail))

        if pretty and len(element):
            buffer.write("\n")
            _write_indent(buffer, indent, depth)

        buffer.write(str(closing_tag))


def _write_indent(buffer: StringIO, indent: int, depth: int) -> None:
    for _ in range(indent * depth):
        buffer.write(" ")
