"""Shared full-text allocation protocol; adapters supply semantic boundaries.

The first translation is authoritative. Fill distributes that text into source
slots, then a format projection restores immutable formulas before decoding.
Neither the allocator nor its whitespace rules know a document's XML schema.
"""
from collections.abc import Callable, Iterable
from typing import Any, Protocol
from xml.etree.ElementTree import Element

from .canonical_text import restore_canonical_whitespace
from .xml_translator.segment import InlineSegment, TextSegment, search_text_segments
from .xml_translator.xml.matching import element_id


class FormulaProjection(Protocol):
    """A format adapter for immutable formulas; never owns the fill loop."""

    def prepare(self, root: Element) -> None: ...
    def finish(self, root: Element) -> None: ...
    def interrupt_source_text_segments(self, segments: Iterable[TextSegment], /) -> Iterable[TextSegment]: ...
    def interrupt_translated_text_segments(self, segments: Iterable[TextSegment], /) -> Iterable[TextSegment]: ...
    def interrupt_block_element(self, element: Element) -> Element: ...


class StructuredTranslation:
    """One projection lifetime, shared by individual or streamed XML tasks."""

    def __init__(self, *, owner: Callable[[InlineSegment], Element] | None = None,
                 is_text_unit: Callable[[Element], bool] = lambda _owner: True,
                 formulas: FormulaProjection | None = None, identity: Callable[[Element], str] | None = None, context: Callable[[list[InlineSegment]], str] | None = None):
        self.owner = owner
        self.identity = identity
        self.is_text_unit = is_text_unit
        self.formulas = formulas
        self.context = context

    def unit(self, segment: InlineSegment) -> Element:
        return self.owner(segment) if self.owner is not None else segment.parent

    def key(self, owner: Element) -> str:
        if self.identity is not None:
            return self.identity(owner)
        return owner.get('__pdf_craft_stream_node') or str(id(owner))

    def groups(self, segments: list[InlineSegment]) -> list[list[InlineSegment]]:
        groups, previous = [], None
        for segment in segments:
            owner = self.unit(segment)
            if owner is not previous:
                groups.append([])
                previous = owner
            groups[-1].append(segment)
        return groups

    def render(self, segments: list[InlineSegment]) -> str:
        parts, previous = [], None
        for segment in segments:
            owner = self.owner(segment) if self.owner is not None else segment
            if parts and owner is not previous:
                parts.append('\n\n')
            parts.extend(text.text for text in segment)
            previous = owner
        return ''.join(parts)

    def validate(self, segments: list[InlineSegment], canonical: str, response: Element) -> str | None:
        if self.owner is not None:
            owner = self.owner(segments[0])
            if any(self.owner(segment) is not owner for segment in segments):
                return 'Canonical fill must belong to one adapter-defined translation unit.'
            if not self.is_text_unit(owner):
                return None
        response_by_id = {element_id(child): child for child in response}
        filled, sources = [], []
        previous = None
        for segment in segments:
            slot = response_by_id.get(segment.id)
            if slot is None:
                return f'Missing required canonical text slot id={segment.id}.'
            owner = self.owner(segment) if self.owner is not None else segment
            if self.owner is None and previous is not None and previous is not owner:
                # Preserve the renderer's paragraph separator without assigning
                # it to either unrelated source paragraph's text/tail fields.
                boundary = Element('boundary')
                boundary.text = '\n\n'
                filled.append(boundary)
                sources.append(boundary)
            filled.append(slot)
            sources.append(segment.create_element())
            previous = owner
        if restore_canonical_whitespace(filled, canonical, sources=sources,
                                        replaced_tags=frozenset({'expression'}), join_roots=self.owner is not None):
            return None
        actual = ''.join(t.text for slot in filled for t in search_text_segments(slot, preserve_whitespace=True))
        return ('Visible text differs from the canonical translation. Preserve words, '
                'punctuation, special spaces and zero-width anchors. '
                f'Expected {canonical!r}, got {actual!r}.')

    def prepare(self, root: Element) -> None:
        if self.formulas is not None:
            self.formulas.prepare(root)

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
