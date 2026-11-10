"""Source formula evidence and typed mapping primitives shared by format adapters."""
from dataclasses import dataclass
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

