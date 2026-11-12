"""Typed delivery primitives shared by format adapters."""
from xml.etree.ElementTree import Element

from .xml_translator.segment import TextSegment

MathMapping = tuple[Element, list[TextSegment]]


class FormatDeliveryError(ValueError):
    """An accepted fill answer could not be delivered without changing content."""

