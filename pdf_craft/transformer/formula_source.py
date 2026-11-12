"""Source formula evidence and typed mapping primitives shared by format adapters."""
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from .xml_translator.segment import TextSegment

MathMapping = tuple[Element, list[TextSegment]]


class FormatDeliveryError(ValueError):
    """An accepted fill answer could not be delivered without changing content."""



@dataclass(frozen=True)
class SourceMath:
    latex: str
    element: Element
    slot: Element | None = None
    context: bool = False

