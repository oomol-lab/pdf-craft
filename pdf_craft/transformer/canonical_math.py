"""Immutable canonical math references for the XML allocation protocol.

Source expression slots are geometry/identity boundaries, not boundaries of
mathematical expressions in the first-stage translation. A reference transports
one complete canonical expression without asking the fill model to retype its
delimiters or split it according to the source slots.
"""
import re
from xml.etree.ElementTree import Element

_MATH = re.compile(
    r'\\+\(.*?\\+\)|\\+\[.*?\\+\]|(?<!\\)\$\$.*?(?<!\\)\$\$|(?<![\\$])\$(?!\$).*?(?<!\\)\$(?!\$)',
    re.DOTALL,
)
_REFERENCE_TAG = 'math-ref'


class CanonicalMath:
    """A request-local read-only math table; no references survive validation."""

    def __init__(self, text: str):
        self.values: dict[str, str] = {}

        def reference(match):
            key = f'm{len(self.values) + 1}'
            self.values[key] = match.group(0)
            return f'<{_REFERENCE_TAG} key="{key}"/>'

        self.text = _MATH.sub(reference, text)

    def instructions(self) -> str:
        if not self.values:
            return ''
        legend = '\n'.join(f'{key}: {value}' for key, value in self.values.items())
        return (
            '\n\nImmutable canonical math references:\n' + legend + '\n'
            'The translated text above uses read-only <math-ref key="mN"/> tokens. '
            'Place each token exactly once; NEVER retype its formula or delimiters. '
            'Source <expression> IDs are allocation slots, NOT independent math boundaries. '
            'When the canonical translation combines multiple source formulas, put the '
            'ONE math-ref token in one corresponding expression slot and keep the other '
            'required expression slots empty. All source tags and IDs must remain. '
            'For example a canonical combined x=y with two source slots can be filled as '
            '<expression id="2"><math-ref key="m1"/></expression><expression id="3"/>. '
            'Do not split a math-ref or surround it with additional math delimiters. '
            'The caller expands these exact canonical values BEFORE structural and '
            'full visible-text validation, then restores source formulas AFTER acceptance.'
        )

    def expand(self, root: Element) -> str | None:
        """Expand validated references, retaining all canonical delimiter bytes.

        Literal responses keep their existing exact-validation path. If a
        response uses references, the reference table must be complete and unique.
        Validate the entire table before mutating any response node.
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
        if missing:
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
