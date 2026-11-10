"""Readable source context only; no artist or fill identity protocol."""
from .formula_source import SourceMath
from .xml_translator.segment import TextSegment


class SourceView:
    def __init__(self, sources: list[SourceMath]):
        self.context_slots = {id(source.slot) for source in sources if source.context}
        self.context = '\n\n'.join(source.latex for source in sources if source.context)

    def readable(self, segment: TextSegment) -> str:
        if any(id(node) in self.context_slots for node in segment.parent_stack):
            return ''
        return segment.text
