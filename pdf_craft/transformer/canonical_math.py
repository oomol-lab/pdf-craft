"""Immutable canonical math references for the XML allocation protocol.

Source expression slots are geometry/identity boundaries, not boundaries of
mathematical expressions in the first-stage translation. A reference transports
one complete canonical expression without asking the fill model to retype its
delimiters or split it according to the source slots.
"""
import re
from functools import lru_cache
from pathlib import Path
from jinja2 import Template
from xml.etree.ElementTree import Element, SubElement
from .xml_translator.xml.friendly import encode_friendly
from .math_spans import math_matches

_REFERENCE_TAG = 'math-ref'


class CanonicalMath:
    """A request-local read-only math table; no references survive validation."""

    def __init__(self, text: str):
        self.values: dict[str, str] = {}
        self.original = text

        for match in self.matches(text):
            self.values[f'm{len(self.values) + 1}'] = match.group(0)

    def encode(self) -> str:
        """Encode the engineering XML payload without promoting literal prose to tags."""
        root = Element('translation')
        previous = None
        cursor = 0
        for index, match in enumerate(self.matches(self.original), 1):
            text = self.original[cursor:match.start()]
            if previous is None:
                root.text = text
            else:
                previous.tail = text
            previous = SubElement(root, _REFERENCE_TAG, key=f'm{index}')
            cursor = match.end()
        if previous is None:
            root.text = self.original
        else:
            previous.tail = self.original[cursor:]
        # Canonical source formatting is data, even when it contains newlines.
        root.set('xml:space', 'preserve')
        return encode_friendly(root)

    def instructions(self) -> str:
        if not self.values:
            return ''
        return _instructions_template().render(values=self.values)

    @staticmethod
    def matches(text: str) -> list[re.Match[str]]:
        return math_matches(text)

    def expand(self, root: Element, *, require_complete: bool = True) -> str | None:
        """Expand validated references, retaining all canonical delimiter bytes.

        Literal responses keep their existing exact-validation path. If a
        response uses references, the reference table must be complete and unique.
        Validate the entire table before mutating any response node.
        require_complete=False is only safe when the caller subsequently checks
        the entire expanded text against canonical: it admits mixed literal and
        reference spellings, never missing or duplicate mathematical content.
        """
        references = [node for node in root.iter() if node.tag == _REFERENCE_TAG]
        if not references:
            return None
        seen = set()
        for node in references:
            key = node.get('key')
            if (set(node.attrib) != {'key'} or key not in self.values
                    or key in seen or len(node) or node.text):
                return 'Invalid canonical math reference: use each known key once as an empty <math-ref key="mN"/>.'
            seen.add(key)
        missing = self.values.keys() - seen
        if missing and require_complete:
            return f'Missing canonical math references: {", ".join(sorted(missing))}. Retain every token exactly once.'

        for parent in list(root.iter()):
            previous = None
            for child in list(parent):
                if child.tag == _REFERENCE_TAG:
                    key = child.get('key')
                    assert key is not None  # The complete table was validated above.
                    text = self.values[key] + (child.tail or '')
                    if previous is None:
                        parent.text = (parent.text or '') + text
                    else:
                        previous.tail = (previous.tail or '') + text
                    parent.remove(child)
                else:
                    previous = child
        return None


@lru_cache(maxsize=1)
def _instructions_template() -> Template:
    path = Path(__file__).parent / 'xml_translator/data/canonical_math.jinja'
    return Template(path.read_text(encoding='utf-8'))
