"""Immutable canonical math references for the XML allocation protocol.

Source expression slots are geometry/identity boundaries, not boundaries of
mathematical expressions in the first-stage translation. A reference transports
one complete canonical expression without asking the fill model to retype its
delimiters or split it according to the source slots.
"""
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from jinja2 import Template
from xml.etree.ElementTree import Element, SubElement
from .xml_translator.xml.friendly import encode_friendly
from .math_spans import math_matches
from ..common.whitespace import is_whitespace

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

    def duplication_hint(self, root: Element) -> str:
        """Locate excessive value copies without guessing or changing source identities.

        Only diagnostic: repeated aliases are valid when the complete artist
        text has that many equal occurrences. Final text validation is decisive.
        """
        occurrences = {}
        def walk(node, block=None, identities=()):
            if node.get('id') is not None and block is None:
                block = node.get('id')
            if node.tag == 'source-math':
                identities = tuple(node.get('keys', node.get('key', '')).split())
            if node.tag == _REFERENCE_TAG and node.get('key') in self.values:
                key = node.get('key')
                occurrences.setdefault(self.values[key], []).append((key, block, identities))
            for child in node:
                walk(child, block, identities)
        for block in root:
            walk(block)
        expected = Counter(self.values.values())
        rows = []
        for value, copies in occurrences.items():
            if len(copies) <= expected[value]:
                continue
            keys = [key for _, _, identities in copies for key in identities]
            rows.append(dict(value=value, expected=expected[value], copies=copies,
                             source_keys=' '.join(keys), reference=copies[0][0]))
        return _duplication_template().render(rows=rows) if rows else ''

    def difference_hint(self, root: Element) -> str:
        """Explain the first literal mismatch while retaining model-chosen IDs.

        This is a diagnostic of the immutable artist stream, never an adoption
        or a mapping from source identity/slot order to translated formulas.
        """
        visible = lambda text: ''.join(c for c in unicodedata.normalize('NFD', text) if not is_whitespace(c))
        parts, references, offset = [], [], 0
        def text(value, block=None, identities=()):
            nonlocal offset
            original = value or ''
            value = visible(original)
            if value and identities:
                references.append(dict(start=offset, end=offset+len(value), reference=None,
                                       block=block, identities=identities, value=original))
            parts.append(value)
            offset += len(value)
        def walk(node, block, identities=()):
            if node.tag == 'source-math':
                identities = tuple(node.get('keys', node.get('key', '')).split())
            key = node.get('key')
            if node.tag == _REFERENCE_TAG and key in self.values:
                start = offset
                text(self.values[key])
                references.append(dict(start=start, end=offset, reference=key, block=block, identities=identities, value=self.values[key]))
            else:
                text(node.text, block, identities)
                for child in node:
                    walk(child, block, identities)
                    text(child.tail, block, identities)
        text(root.text)
        for block in root:
            walk(block, block.get('id'))
            text(block.tail)
        expected, actual = visible(self.original), ''.join(parts)
        position = next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b), min(len(expected), len(actual)))
        if expected == actual:
            return ''
        located = next((item for item in references if item['start'] <= position < item['end']), None)
        if located is None:
            return ''
        matches = self.matches(self.original)
        index = next((i for i, match in enumerate(matches)
                      if len(visible(self.original[:match.start()])) <= position < len(visible(self.original[:match.end()]))), None)
        if index is None:
            return ''
        wanted = f'm{index+1}'
        if self.values[wanted] == located['value']:
            return ''
        return _alignment_template().render(located=located, wanted=wanted,
                                            expected=self.values[wanted], actual=located['value'],
                                            following=[(f'm{i+1}', self.values[f'm{i+1}'])
                                                       for i in range(index, min(index+9, len(matches)))])

    def expand(self, root: Element, *, require_complete: bool = True) -> str | None:
        """Expand validated references, retaining all canonical delimiter bytes.

        Literal responses keep their existing exact-validation path. If a
        response uses references, the reference table must be complete and unique.
        Validate the entire table before mutating any response node.
        require_complete=False is only safe when the caller subsequently checks
        the entire expanded text against canonical: it admits mixed literal and
        reference spellings, never missing or duplicate mathematical content.
        """
        # With full-text validation, references are immutable VALUE shortcuts.
        # Reusing one cannot change accepted text: the complete artist stream
        # must still match, and independent source-math identities remain unique.
        references = [node for node in root.iter() if node.tag == _REFERENCE_TAG]
        if not references:
            return None
        seen = set()
        for node in references:
            key = node.get('key')
            if set(node.attrib) != {'key'} or key not in self.values or len(node) or node.text:
                return f'Invalid math-ref key={key!r}: use a known key as an empty <math-ref key="mN"/>. Found attributes={node.attrib}, children={[child.tag for child in node]}, text={node.text!r}.'
            if key in seen and require_complete:
                return f'Duplicate canonical math reference {key}: use each occurrence once.'
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


@lru_cache(maxsize=1)
def _duplication_template() -> Template:
    path = Path(__file__).parent / 'xml_translator/data/canonical_duplicate.jinja'
    return Template(path.read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def _alignment_template() -> Template:
    path = Path(__file__).parent / 'xml_translator/data/canonical_alignment.jinja'
    return Template(path.read_text(encoding='utf-8'))
