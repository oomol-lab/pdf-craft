"""Carry immutable source formula occurrences through the first translation."""
from collections import Counter
import re
from xml.etree.ElementTree import Element, fromstring, tostring, ParseError

from .canonical_math import CanonicalMath
from .canonical_projection import MathProjectionError, SourceMath, bind_source_math
from .xml_translator.segment import TextSegment

_REFERENCE = re.compile(r'<source-math\b[^>]*?/\s*>|<source-math\b[^>]*>.*?</source-math\s*>', re.DOTALL)


class SourceMathTranslation:
    """One semantic unit's references; neither text slots nor translated values own identity."""

    def __init__(self, sources: list[SourceMath]):
        self.sources = [source for source in sources if not source.context]
        self.context_slots = {id(source.slot) for source in sources if source.context}
        self.context = '\n\n'.join(source.latex for source in sources if source.context)
        self.values = {f's{index}': source for index, source in enumerate(self.sources, 1)}
        self.slots = {id(source.slot): key for key, source in self.values.items()}
        self.bindings: list[list[SourceMath]] = []

    def render(self, segment: TextSegment, seen: set[str]) -> str:
        if any(id(node) in self.context_slots for node in segment.parent_stack):
            return ''
        key = next((self.slots[id(node)] for node in segment.parent_stack if id(node) in self.slots), None)
        if key is None:
            return segment.text
        if key in seen:
            return ''
        seen.add(key)
        leading = segment.text[:len(segment.text) - len(segment.text.lstrip(' \t\r\n'))]
        trailing = segment.text[len(segment.text.rstrip(' \t\r\n')):]
        node = Element('source-math', {'key': key})
        node.text = self.values[key].latex
        return leading + tostring(node, encoding='unicode') + trailing

    def resolve(self, response: str) -> str:
        """Accept source identities or recover an unambiguous, unchanged literal reply."""
        references = list(_REFERENCE.finditer(response))
        if not references:
            if re.search(r'</?source-math\b', response, re.IGNORECASE):
                raise MathProjectionError('Use complete, empty source-math references with their original keys.')
            self.bindings = bind_source_math(response, self.sources)
            return response
        keys = []
        for match in references:
            try:
                node = fromstring(match.group())
            except ParseError as error:
                raise MathProjectionError('Use empty source-math references with their original keys.') from error
            key = node.get('key')
            if (node.tag != 'source-math' or set(node.attrib) != {'key'} or key not in self.values or len(node)):
                raise MathProjectionError('Unknown source-math identity: copy the original keys exactly.')
            payload = (node.text or '').strip(' \t\r\n')
            if payload and payload != self.values[key].latex.strip(' \t\r\n'):
                raise MathProjectionError('A source-math payload changed: preserve its original formula or return an empty reference.')
            keys.append(key)
        counts = Counter(keys)
        if counts != Counter(self.values.keys()):
            missing = [key for key in self.values if not counts[key]]
            duplicates = {key: count for key, count in counts.items() if count > 1}
            raise MathProjectionError(
                f'Preserve every source-math identity exactly once. Missing keys: {missing}; '
                f'duplicate keys (occurrence counts): {duplicates}.')
        ordinary = _REFERENCE.sub('', response)
        if re.search(r'</?source-math\b', ordinary) or CanonicalMath.matches(ordinary):
            raise MathProjectionError('Do not invent or retype formulas; use only the original source-math references.')
        bindings = [[self.values[key]] for key in keys]
        iterator = iter(keys)
        canonical = _REFERENCE.sub(lambda _match: self.values[next(iterator)].latex, response)
        if [match.group() for match in CanonicalMath.matches(canonical)] != [group[0].latex for group in bindings]:
            raise MathProjectionError('Source formula boundaries cannot be represented safely.')
        self.bindings = bindings
        return canonical
