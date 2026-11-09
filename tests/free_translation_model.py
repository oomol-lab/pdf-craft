"""Deterministic engineering model for allocation fixtures, never production code."""
import json
from copy import deepcopy
import unicodedata
from xml.etree.ElementTree import Element, SubElement, tostring
from pdf_craft.transformer.xml_translator.xml.friendly import encode_friendly, decode_friendly
from pdf_craft.transformer.canonical_projection import SourceMath, formula_content, MathProjectionError
from pdf_craft.transformer.canonical_math import CanonicalMath


def _math_identity(value: str) -> str:
    # Inner TeX whitespace may belong to \text/\verb, not CSS document space.
    return unicodedata.normalize('NFD', value.strip(' \t\r\n'))


class _FormulaPool:
    def __init__(self, sources: list[SourceMath]):
        self.sources = sources
        self.parts = [formula_content(source.latex)[0].strip(' \t\r\n') for source in self.sources]
        self.used: set[int] = set()

    def take(self, value: str) -> list[SourceMath]:
        body, _ = formula_content(value)
        expected = _math_identity(body)
        # Reuse exact source presentation, including an unchanged sequence of
        # source formulas merged into one canonical expression. Never consume a
        # source occurrence twice, and never infer identity from a fill slot.
        groups = [[index] for index, part in enumerate(self.parts)
                  if index not in self.used and _math_identity(part) == expected]
        if not groups:
            for start in range(len(self.sources)):
                compact, spaced = '', ''
                for end in range(start, len(self.sources)):
                    if end in self.used:
                        break
                    compact += self.parts[end]
                    spaced += (' ' if end > start else '') + self.parts[end]
                    candidates = (_math_identity(compact), _math_identity(spaced))
                    if end > start and expected in candidates:
                        groups.append(list(range(start, end + 1)))
                        break
                    if min(map(len, candidates)) > len(expected):
                        break
        if groups:
            def signature(indexes):
                nodes = [deepcopy(self.sources[index].element) for index in indexes]
                for node in nodes:
                    node.tail = None
                return tuple(tostring(node) for node in nodes)
            if len({signature(indexes) for indexes in groups}) != 1:
                raise MathProjectionError('Ambiguous source formula: preserve its source-math identity.')
            indexes = groups[0]
            self.used.update(indexes)
            return [self.sources[index] for index in indexes]
        raise MathProjectionError('A translated formula has no unchanged source counterpart. Preserve source-math identities.')


def bind_source_math(canonical: str, sources: list[SourceMath]) -> list[list[SourceMath]]:
    """Recover literal replies only when every source occurrence is accounted for."""
    pool = _FormulaPool(sources)
    bindings = [pool.take(value) for value in CanonicalMath(canonical).values.values()]
    if len(pool.used) != len(sources):
        raise MathProjectionError('Missing source formulas: preserve every source-math identity once.')
    return bindings


def allocation_answer(payload):
    raw = payload['translation']
    sources = payload['sources']
    if len(sources) == 1:
        texts = [raw]
    else:
        texts = [source['source'].split('Text to translate:\n', 1)[-1].rstrip('\n') for source in sources]
    result = []
    for source, text in zip(sources, texts):
        originals = [SourceMath(value, Element('formula')) for key, value in source['formulas'].items()]
        keys = {id(item.element): key for key, item in zip(source['formulas'], originals)}
        formulas = []
        try:
            groups = bind_source_math(text, originals)
        except ValueError:
            groups = []
        for match, group in zip(CanonicalMath.matches(text), groups):
            formulas.append(dict(keys=[keys[id(item.element)] for item in group], text=match.group(),
                                 before=text[:match.start()], after=text[match.end():]))
        result.append(dict(id=source['id'], text=text, formulas=formulas))
    return json.dumps(result)


def canonical_fill_response(text):
    """Script an engineering response from explicit canonical reference nodes."""
    translated = next(decode_friendly(CanonicalMath(text).encode(), tags='translation'))
    root = Element('xml')
    fragment = SubElement(root, 'fragment', id='1')
    fragment.text = translated.text
    for index, reference in enumerate(translated, 2):
        node = SubElement(fragment, 'expression', id=str(index))
        node.tail = reference.tail
        reference.tail = None
        node.append(reference)
    return encode_friendly(root)
