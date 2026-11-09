"""Validate complete group XML and project typed formulas through format adapters."""
from copy import deepcopy
from collections.abc import Callable, Iterable
from typing import Any, Protocol
from xml.etree.ElementTree import Element

from ..common.whitespace import is_whitespace
from .canonical_text import restore_canonical_whitespace, canonical_difference
from .canonical_projection import MathMapping, SourceMath
from .source_math import SourceMathTranslation
from .xml_math import project_xml_math
from .xml_translator.segment import InlineSegment, TextSegment
from .xml_translator.xml.matching import element_id


class FormulaProjection(Protocol):
    """A format adapter for immutable formulas; never owns the fill loop."""

    def prepare(self, root: Element) -> None: ...
    def finish(self, root: Element) -> None: ...
    def interrupt_source_text_segments(self, segments: Iterable[TextSegment], /) -> Iterable[TextSegment]: ...
    def interrupt_translated_text_segments(self, segments: Iterable[TextSegment], /) -> Iterable[TextSegment]: ...
    def interrupt_block_element(self, element: Element) -> Element: ...
    def source_math(self, segments: Iterable[TextSegment]) -> list[SourceMath]: ...
    def residual_math(self, canonical: str) -> Element: ...


class StructuredTranslation:
    """One projection lifetime, shared by individual or streamed XML tasks."""

    def __init__(self, *, owner: Callable[[InlineSegment], Element] | None = None,
                 is_text_unit: Callable[[Element], bool] = lambda _owner: True,
                 formulas: FormulaProjection | None = None, identity: Callable[[Element], str] | None = None, context: Callable[[list[InlineSegment]], str] | None = None,
                 record_units: bool = False, require_nonempty_units: bool = False):
        self.owner = owner
        self.identity = identity
        self.is_text_unit = is_text_unit
        self.formulas = formulas
        self.context = context
        self._record_units = record_units
        self.require_nonempty_units = require_nonempty_units
        self.validated_text_units: set[str] = set()
        self.projected_math_units: set[str] = set()

    def unit(self, segment: InlineSegment) -> Element:
        return self.owner(segment) if self.owner is not None else segment.parent

    def key(self, owner: Element) -> str:
        if self.identity is not None:
            return self.identity(owner)
        return owner.get('__pdf_craft_stream_node') or str(id(owner))

    def render(self, segments: list[InlineSegment], math: SourceMathTranslation | None = None) -> str:
        parts, previous = [], None
        for segment in segments:
            if getattr(segment, '_translation_context', False):
                continue
            owner = self.owner(segment) if self.owner is not None else segment
            if parts and owner is not previous:
                parts.append('\n\n')
            parts.extend(math.readable(text) if math is not None else text.text for text in segment)
            previous = owner
        return ''.join(parts)

    def validate(self, segments: list[InlineSegment], canonical: str, response: Element) -> str | None:
        # Top-level tails are outside the adapter's adopted fragment fields.
        # Plain fragments can retain their own
        # trailing text. Never move it across an anchor or a rich inline tag.
        # This adapter discards display-equation context during restoration.
        # It must never count artist words hidden in those non-output slots.
        if any(not is_whitespace(char) for context in response.iter('formula_context')
               for text in context.itertext() for char in text):
            return 'formula_context is read-only background. Leave it empty and put artist text in output slots.'
        original_response = response
        response = deepcopy(response)
        if any(not is_whitespace(char) for char in response.text or ''):
            return 'Visible text outside the required translation fragments.'
        for index, child in enumerate(response):
            if not any(not is_whitespace(char) for char in child.tail or ''):
                continue
            if (self.owner is None or child.tag != 'fragment'
                    or len(child) or element_id(child) not in {segment.id for segment in segments}
                    or (index+1 < len(response) and response[index+1].tag != 'fragment')):
                return 'Visible tail has ambiguous fragment ownership.'
            child.text = (child.text or '') + (child.tail or '')
            child.tail = None
        source_by_id = {segment.id: segment for segment in segments}
        filled, sources, separators = [], [], set()
        previous = None
        for slot in response:
            segment = source_by_id.get(element_id(slot))
            if segment is None:  # Immutable anchors were checked by the structure validator.
                continue
            owner = self.unit(segment)
            if previous is not None and previous is not owner:
                boundary = Element('boundary')
                boundary.text = '\n\n'
                separators.add(boundary)
                filled.append(boundary)
                sources.append(deepcopy(boundary))
            previous = owner
            filled.append(slot)
            sources.append(segment.create_element())
        if restore_canonical_whitespace(filled, canonical, sources=sources,
                                        join_roots=True, replaced_tags=frozenset({'source-math'}),
                                        transparent_tags=frozenset({'source-math'}),
                                        formatting_tags=frozenset({'expression'}),
                                        virtual_roots=frozenset(separators)):
            if self.require_nonempty_units:
                by_owner = {}
                for segment in segments:
                    owner = self.unit(segment)
                    entry = by_owner.setdefault(id(owner), [owner, [], []])
                    entry[1].append(segment)
                    entry[2].append(next(node for node in response if element_id(node) == segment.id))
                for owner, parts, slots in by_owner.values():
                    # Only judge complete source units in this group; a partial
                    # long owner may legitimately be empty in one of its rounds.
                    source = ''.join(part.text for segment in parts for part in segment)
                    from ..common.whitespace import normalize_whitespace
                    if (normalize_whitespace(source, trim=True)
                            and normalize_whitespace(source, trim=True) == normalize_whitespace(''.join(owner.itertext()), trim=True)
                            and not normalize_whitespace(''.join(''.join(slot.itertext()) for slot in slots), trim=True)):
                        empty_index = list(response).index(slots[0])
                        neighbors = [node for node in list(response)[max(0, empty_index-1):empty_index+2]
                                     if node not in slots and normalize_whitespace(''.join(node.itertext()), trim=True)]
                        nearby = '; '.join(f'block id={node.get("id")}: {"".join(node.itertext())[:260]!r}' for node in neighbors)
                        return (f'Nonempty source <{owner.tag}> must receive its own translated content. '
                                f'Neighboring current allocations: {nearby}. '

                                f'Empty XML block ids={[slot.get("id") for slot in slots]}, source text={source[:260]!r}. '
                                'These sections can be physical LINE FRAGMENTS, not independent sentences. Split existing translated sentences or phrases across adjacent sections so every required section receives text. MOVE text out of neighboring full sections; do not COPY or duplicate it. Never add words or retranslate.')
            original_response[:] = list(response)
            return None
        return canonical_difference(canonical, filled)


    def prepare(self, root: Element) -> None:
        if self.formulas is not None:
            self.formulas.prepare(root)

    def source_math(self, segments: list[InlineSegment]) -> SourceMathTranslation | None:
        if self.formulas is None:
            return None
        segments = [segment for segment in segments if not getattr(segment, '_translation_context', False)]
        sources = self.formulas.source_math(text for segment in segments for text in segment)
        if not sources:
            return None
        math = SourceMathTranslation(sources)
        math.capture(segments)
        return math

    def project_math(self, mappings: list[MathMapping | None], _canonical: str,
                     math: SourceMathTranslation | None, response: Element,
                     segments: list[InlineSegment]) -> list[MathMapping | None] | None:
        if self.formulas is None:
            return None
        projected = project_xml_math(mappings, response, math, self.formulas.residual_math)
        if projected is not None and self._record_units:
            self.projected_math_units.update(self.key(self.unit(segment)) for segment in segments)
        return projected

    def accepted_unit(self, owner: Element) -> None:
        """Record only units whose complete canonical fill has succeeded."""
        if self._record_units:
            self.validated_text_units.add(self.key(owner))

    def finish(self, root: Element) -> None:
        if self.formulas is not None:
            self.formulas.finish(root)

    def arguments(self) -> dict[str, Any]:
        values: dict[str, Any] = dict(translation_protocol=self, source_text_renderer=self.render,
                      canonical_text_validator=self.validate)
        if self.formulas is not None:
            values.update(interrupt_source_text_segments=self.formulas.interrupt_source_text_segments,
                          interrupt_translated_text_segments=self.formulas.interrupt_translated_text_segments,
                          interrupt_block_element=self.formulas.interrupt_block_element)
        return values

    async def translate(self, translator, task, **kwargs):
        self.prepare(task.element)
        root, payload = await translator.translate_element(task, **self.arguments(), **kwargs)
        self.finish(root)
        return root, payload

    async def translate_many(self, translator, tasks, **kwargs):
        for task in tasks:
            self.prepare(task.element)
        results = await translator.translate_elements(tasks, **self.arguments(), **kwargs)
        for root, _payload in results:
            self.finish(root)
        return results
