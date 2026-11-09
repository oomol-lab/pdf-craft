"""Source formula evidence and typed mapping primitives shared by format adapters."""
from dataclasses import dataclass
import re
from xml.etree.ElementTree import Element

from .xml_translator.segment import TextSegment

MathMapping = tuple[Element, list[TextSegment]]


class MathProjectionError(ValueError):
    """Canonical math cannot be represented without changing accepted content."""

    attempts: int = 1


@dataclass(frozen=True)
class SourceMath:
    latex: str
    element: Element
    slot: Element | None = None
    context: bool = False


def formula_content(value: str) -> tuple[str, bool]:
    """Read the delimiters recognized by the canonical transport protocol."""
    if value.startswith('$$'):
        return value[2:-2], False
    if value.startswith('$'):
        return value[1:-1], True
    match = re.fullmatch(r'\\+([\(\[])(.*?)\\+[)\]]', value, re.DOTALL)
    if match is None:
        raise MathProjectionError('Invalid canonical mathematical delimiters.')
    return match[2], match[1] == '('
