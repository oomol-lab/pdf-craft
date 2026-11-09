"""Engineering source formula evidence; never a translation output protocol."""
from .canonical_projection import SourceMath
from xml.etree.ElementTree import Element, SubElement
from .xml_translator.xml.friendly import encode_friendly
from .xml_translator.segment import TextSegment


class SourceMathTranslation:
    """One bounded group's source evidence for direct XML identity annotation."""

    def __init__(self, sources: list[SourceMath]):
        self.sources = [source for source in sources if not source.context]
        self.context_slots = {id(source.slot) for source in sources if source.context}
        self.context = '\n\n'.join(source.latex for source in sources if source.context)
        self.values = {f's{index}': source for index, source in enumerate(self.sources, 1)}
        self.evidence = {}

    def readable(self, segment: TextSegment) -> str:
        """Present original mathematical text without exposing engineering IDs."""
        if any(id(node) in self.context_slots for node in segment.parent_stack):
            return ''
        return segment.text

    def capture(self, segments):
        """Give the engineer contextual occurrence evidence, never the artist."""
        slots = {id(source.slot): key for key, source in self.values.items()}
        parts, spans, offset = [], {}, 0
        for segment in segments:
            for text in segment:
                value = self.readable(text)
                key = next((slots[id(node)] for node in text.parent_stack if id(node) in slots), None)
                if key is not None:
                    start, _ = spans.get(key, (offset, offset))
                    spans[key] = start, offset + len(value)
                parts.append(value)
                offset += len(value)
        source = ''.join(parts)
        self.evidence = {key: dict(before=source[max(0, start-120):start],
                                   text=source[start:end], after=source[end:end+120])
                         for key, (start, end) in spans.items()}

    def encode(self) -> str:
        root = Element('source-formulas')
        for key, source in self.values.items():
            formula = SubElement(root, 'formula', key=key)
            SubElement(formula, 'value').text = source.latex
            for name, value in self.evidence.get(key, {}).items():
                SubElement(formula, name).text = value
        return encode_friendly(root)
