"""Read-only fill acceptance followed by format-specific delivery."""
from collections.abc import Callable, Iterable
from typing import Any, Protocol
from xml.etree.ElementTree import Element

from ..common.whitespace import is_whitespace
from .fill_text import text_difference
from .formula_source import MathMapping, FormatDeliveryError
from .xml_translator.segment import (InlineSegment, TextSegment, TextPosition, combine_text_segments, search_text_segments)
from .xml_translator.xml.matching import element_id


class FormulaProjection(Protocol):
    """Source structure and final format adapter; never owns the model loop."""

    def prepare(self, root: Element) -> None: ...
    def finish(self, root: Element) -> None: ...
    def interrupt_source_text_segments(self, segments: Iterable[TextSegment], /) -> Iterable[TextSegment]: ...
    def interrupt_translated_text_segments(self, segments: Iterable[TextSegment], /) -> Iterable[TextSegment]: ...
    def interrupt_block_element(self, element: Element) -> Element: ...
    def materialize(self, root: Element) -> Element: ...
    def visible_text(self, root: Element) -> str: ...
    def delivery_source_text(self, root: Element) -> str: ...


class StructuredTranslation:
    """One projection lifetime, shared by individual or streamed XML tasks."""

    def __init__(self, *, owner: Callable[[InlineSegment], Element] | None = None,
                 is_text_unit: Callable[[Element], bool] = lambda _owner: True,
                 formulas: FormulaProjection | None = None, identity: Callable[[Element], str] | None = None, context: Callable[[list[InlineSegment]], str] | None = None,
                 record_units: bool = False):
        self.owner = owner
        self.identity = identity
        self.is_text_unit = is_text_unit
        self.formulas = formulas
        self.context = context
        self._record_units = record_units
        self.validated_text_units: set[str] = set()
        self.projected_math_units: set[str] = set()

    def unit(self, segment: InlineSegment) -> Element:
        return self.owner(segment) if self.owner is not None else segment.parent

    def key(self, owner: Element) -> str:
        if self.identity is not None:
            return self.identity(owner)
        return owner.get('__pdf_craft_stream_node') or str(id(owner))

    def render(self, segments: list[InlineSegment]) -> str:
        parts, previous = [], None
        for segment in segments:
            if getattr(segment, '_translation_context', False):
                continue
            owner = self.owner(segment) if self.owner is not None else segment
            if parts and owner is not previous:
                parts.append('\n\n')
            parts.extend(text.text for text in segment)
            previous = owner
        return ''.join(parts)

    def validate(self, segments: list[InlineSegment], canonical: str, response: Element) -> str | None:
        if any(not is_whitespace(char) for char in response.text or ''):
            return 'Visible text outside the required translation fragments.'
        for index, child in enumerate(response):
            if not any(not is_whitespace(char) for char in child.tail or ''):
                continue
            if (self.owner is None or child.tag != 'fragment' or len(child)
                    or element_id(child) not in {segment.id for segment in segments}
                    or (index+1 < len(response) and response[index+1].tag != 'fragment')):
                return 'Visible tail has ambiguous fragment ownership.'
        slots_by_id = {element_id(slot): slot for slot in response}
        filled = [slots_by_id[segment.id] for segment in segments if segment.id in slots_by_id]
        return text_difference(canonical, ''.join(''.join(slot.itertext()) + (slot.tail or '') for slot in filled), filled)


    def prepare(self, root: Element) -> None:
        if self.formulas is not None:
            self.formulas.prepare(root)

    def materialize_accepted(self, mappings: list[MathMapping | None], canonical: str,
                              _response: Element, segments: list[InlineSegment]) -> list[MathMapping | None] | None:
        if self.formulas is None:
            return None
        projected, restored = [], []
        assert self.formulas is not None
        # Called only AFTER the XML repair loop has accepted its original answer.
        for segment, mapping in zip(segments, mappings, strict=True):
            if mapping is None:
                raise FormatDeliveryError('A complete candidate must retain every source block.')
            block, fields = mapping
            roots = list(combine_text_segments(part.clone().strip_block_parents() for part in fields))
            if len(roots) != 1:
                raise FormatDeliveryError('A candidate must retain its owning block.')
            restored.append(''.join(roots[0][0].itertext()))
            original = roots[0][0]
            root = self.formulas.materialize(original)
            error = text_difference(self.formulas.delivery_source_text(original), self.formulas.visible_text(root))
            if error is not None:
                raise FormatDeliveryError('Format delivery changed an accepted answer. ' + error)
            output = list(search_text_segments(root, normalize_whitespace=False)) or [
                TextSegment('', [root], 0, 0, 1, TextPosition.TEXT)]
            projected.append((block, output))
        error = text_difference(canonical, ''.join(restored))
        if error is not None:
            raise FormatDeliveryError(error)
        if self._record_units:
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
