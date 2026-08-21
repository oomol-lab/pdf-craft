from collections.abc import Generator
from xml.etree.ElementTree import Element

from ..utils import normalize_whitespace
from .const import ID_KEY


def normalize_text_in_element(text: str | None) -> str | None:
    if text is None:
        return None
    text = normalize_whitespace(text)
    if not text.strip():
        return None
    return text


def append_text_in_element(origin_text: str | None, append_text: str) -> str:
    if origin_text is None:
        return append_text
    else:
        return origin_text + append_text


def index_of_parent(parent: Element, checked_element: Element) -> int:
    for i, child in enumerate(parent):
        if child == checked_element:
            return i
    raise ValueError("Element not found in parent.")


def expand_left_element_texts(element: Element) -> Generator[str, None, None]:
    yield "<"
    yield element.tag
    yield " "
    yield ID_KEY
    yield '="99">'


def expand_right_element_texts(element: Element) -> Generator[str, None, None]:
    yield "</"
    yield element.tag
    yield ">"
