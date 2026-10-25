from collections.abc import Generator, Iterable
from xml.etree.ElementTree import Element
import re

from pdf_craft.transformer.xml_translator.xml import clone_element
from .parser import parse_tags
from .tag import Tag, TagKind
from .transform import tag_to_element


_ENTITIES = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}
_ENTITY = re.compile(r"&(?P<name>lt|gt|amp|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);")


def _unescape_text(text: str) -> str:
    """Decode XML entities once, after identifying structural tags.

    Unknown or invalid references stay literal. Unlike HTML unescaping, XML
    numeric references do not remap code points using legacy character sets.
    """
    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name in _ENTITIES:
            return _ENTITIES[name]
        try:
            code = int(name[2:], 16) if name.startswith("#x") else int(name[1:])
        except ValueError:
            return match.group(0)
        if (code in (9, 10, 13) or 0x20 <= code <= 0xD7FF
                or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF):
            return chr(code)
        return match.group(0)

    return _ENTITY.sub(replace, text)


# why implement XML decoding?
# https://github.com/oomol-lab/pdf-craft/issues/149
def decode_friendly(chars: Iterable[str], tags: Iterable[str] | str = ()) -> Generator[Element, None, None]:
    if isinstance(tags, str):
        tags = set((tags,))
    else:
        tags = set(tags)

    for element in _collect_elements(chars):
        if element.tag in tags or len(tags) == 0:
            yield clone_element(element)


def _collect_elements(chars: Iterable[str]) -> Generator[Element, None, None]:
    opening_stack: list[Element] = []
    last_closed_element: Element | None = None

    for cell in parse_tags(chars):
        if isinstance(cell, Tag):
            tag: Tag = cell
            element = tag_to_element(tag)
            if tag.kind == TagKind.CLOSING:
                popped = _pop_element(tag.name, opening_stack)
                if popped is not None:
                    yield popped
                    last_closed_element = popped
                elif last_closed_element is not None:
                    _append_to_tail(last_closed_element, tag.proto)
            else:
                if opening_stack:
                    opening_stack[-1].append(element)
                if tag.kind == TagKind.SELF_CLOSING:
                    yield element
                    last_closed_element = element
                elif tag.kind == TagKind.OPENING:
                    opening_stack.append(element)
                    last_closed_element = None

        else:
            # Do not decode the input stream before parsing: escaped tag-shaped
            # prose must never become a structural element. Appended text is
            # decoded independently, never by revisiting an existing tail.
            text = _unescape_text(cell)
            if last_closed_element is not None:
                _append_to_tail(last_closed_element, text)
            elif opening_stack:
                opening_stack[-1].text = text


def _append_to_tail(element: Element, text: str) -> None:
    if element.tail:
        element.tail += text
    else:
        element.tail = text


def _pop_element(tag_name: str, opening_stack: list[Element]) -> Element | None:
    index = -1
    for i in range(len(opening_stack) - 1, -1, -1):
        opening_element = opening_stack[i]
        if tag_name == opening_element.tag:
            index = i
            break
    if index == -1:
        return None

    popped: Element | None = None
    for _ in range(len(opening_stack) - index):
        popped = opening_stack.pop()
    return popped
