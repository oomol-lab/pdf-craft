"""Expose readable source formulas while preserving their original node identities.

``Chapter`` XML represents inline formulas as ``inline_expr`` nodes and display
formulas in the ``content`` of an ``asset ref=\"formula\"``.  Neither shape is
MathML, so the EPUB MathML interrupter cannot be used here.  This adapter uses
the same XMLTranslator interruption protocol with the PCEX schema instead.
"""

import json
from copy import deepcopy
from collections.abc import Generator, Iterable
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from pdf_craft.expression import decode_expression_kind, to_markdown_string
from .formula_source import SourceMath
from ..formula.comparison import same_formula
from .formula_payload import formula_payload, replace_formulas
from pdf_craft.transformer.xml_translator.segment import TextPosition, TextSegment
from pdf_craft.transformer.xml_translator.xml import (
    DISPLAY_ATTRIBUTE,
    plain_text,
)


_FORMULA_ID_KEY = "__PDF_CRAFT_CHAPTER_FORMULA_ID"
_FORMULA_TAG = "expression"
_FORMULA_CONTEXT_KEY = "__PDF_CRAFT_CHAPTER_FORMULA_CONTEXT"
_FORMULA_CONTEXT_TAG = "formula_context"
_INLINE_DISPLAY_KEY = "__PDF_CRAFT_INLINE_DISPLAY"


@dataclass(frozen=True)
class _Formula:
    """One source formula represented by a temporary translation token."""

    token_id: str
    element: Element
    display: str
    latex: str

    @property
    def is_block(self) -> bool:
        return self.display == "block"


class ChapterFormulaInterrupter:
    """Expose PCEX formulas as readable XMLTranslator allocation tokens.

    The temporary ``expression`` nodes never reach a decoded Chapter.  The
    source text shown to the language model contains Markdown-style LaTex
    delimiters. Fill directly writes the editable expression containers, which
    materialize into current-schema ``inline_expr`` nodes before acceptance.
    Equation asset *content* is injected as
    discarded context into its preceding and following paragraphs, so a token
    budget split cannot hide an equation from either neighboring translation.
    The asset's title and caption remain ordinary translatable Chapter text.
    """

    def __init__(self) -> None:
        self._next_id = 1
        self._element_to_formula: dict[int, _Formula] = {}
        self._formula_by_id: dict[str, _Formula] = {}
        self._raw_text_segments: dict[str, list[TextSegment]] = {}
        self._last_formula_id: str | None = None
        self._last_paragraph_stack: list[Element] | None = None
        self._pending_block_formulas: list[_Formula] = []
        self._block_formula_parent_stacks: dict[str, list[Element]] = {}
        self._block_formula_positions: dict[str, TextPosition] = {}
        self._block_formula_progress: dict[str, list[int]] = {}
        self._block_formula_context_counts: dict[str, int] = {}

    def prepare(self, root: Element) -> None:
        """Declare format-specific inline geometry before generic XML grouping.

        PCEX inline_expr is not an HTML tag. Leaving it block-level made the
        generic submitter preserve a second source node beside its field.
        This transient display hint keeps every formula in its real owner,
        including fragments, captions and nested table cells.
        """
        for element in root.iter('inline_expr'):
            element.set(_INLINE_DISPLAY_KEY, json.dumps(element.get(DISPLAY_ATTRIBUTE)))
            element.set(DISPLAY_ATTRIBUTE, 'inline')

    def finish(self, root: Element) -> None:
        """Remove transport metadata only after accepted text was submitted."""
        for element in root.iter('inline_expr'):
            original = element.attrib.pop(_INLINE_DISPLAY_KEY, None)
            if original is not None:
                display = json.loads(original)
                if display is None:
                    element.attrib.pop(DISPLAY_ATTRIBUTE, None)
                else:
                    element.set(DISPLAY_ATTRIBUTE, display)

    def release(self, root: Element) -> None:
        """Release a settled root's projection tables in a shared stream."""
        nodes = {id(node) for node in root.iter()}
        identities = {formula.token_id for key, formula in self._element_to_formula.items() if key in nodes}
        self._element_to_formula = {key: value for key, value in self._element_to_formula.items() if key not in nodes}
        self._formula_by_id = {key: value for key, value in self._formula_by_id.items() if value.token_id not in identities}
        for values in (self._raw_text_segments, self._block_formula_parent_stacks,
                       self._block_formula_positions, self._block_formula_progress,
                       self._block_formula_context_counts):
            for key in identities:
                values.pop(key, None)
        if self._last_paragraph_stack and id(self._last_paragraph_stack[0]) in nodes:
            self._last_paragraph_stack = None

    def source_formulas(self, segments: Iterable[TextSegment]) -> list[SourceMath]:
        identities = {node.get(_FORMULA_ID_KEY): node for segment in segments for node in segment.parent_stack
                      if _FORMULA_ID_KEY in node.attrib}
        return [SourceMath(formula.latex, formula.element, identities[key], context=formula.is_block or ':context:' in key)
                for key, formula in self._formula_by_id.items() if key in identities]

    def interrupt_source_text_segments(
        self, text_segments: Iterable[TextSegment],
    ) -> Generator[TextSegment, None, None]:
        """Replace source formulas with readable temporary formula tokens."""
        # Context never crosses independent XML roots. A stream may now
        # project many roots using this adapter instance.
        self._last_paragraph_stack = None
        for text_segment in text_segments:
            formula, formula_index = self._formula_in(text_segment)
            formula_id = formula.token_id if formula is not None else None
            if formula is not None and not formula.is_block:
                self._raw_text_segments.setdefault(formula.token_id, []).append(text_segment)
            elif formula is not None:
                if formula_index is None:  # Defensive: formula ownership includes its stack index.
                    raise RuntimeError("formula source segment has no formula stack index")
                self._block_formula_parent_stacks.setdefault(
                    formula.token_id, text_segment.parent_stack[:formula_index],
                )
                self._block_formula_positions.setdefault(formula.token_id, text_segment.position)
                self._block_formula_progress.setdefault(formula.token_id, []).extend(text_segment.progress_blocks)

            if self._last_formula_id is not None and formula_id != self._last_formula_id:
                yield from self._finish_formula(self._last_formula_id)

            self._last_formula_id = formula_id
            if formula is None:
                paragraph_stack = _paragraph_block_stack(text_segment)
                if paragraph_stack is not None:
                    for pending in self._pending_block_formulas:
                        yield self._context_token(pending, paragraph_stack, text_segment)
                    self._pending_block_formulas.clear()
                yield text_segment
                if paragraph_stack is not None:
                    self._last_paragraph_stack = paragraph_stack

        if self._last_formula_id is not None:
            yield from self._finish_formula(self._last_formula_id)
            self._last_formula_id = None
        for formula in self._pending_block_formulas:
            if self._block_formula_context_counts.get(formula.token_id, 0) == 0:
                standalone = self._standalone_block_token(formula)
                if standalone is not None:
                    yield standalone
        self._pending_block_formulas.clear()

    def materialize(self, root: Element) -> Element:
        def convert(node):
            value = ''.join(node.itertext())
            if any(_FORMULA_CONTEXT_KEY in parent.attrib for parent in node.iter()):
                return node
            source = self._formula_by_id.get(node.get(_FORMULA_ID_KEY, ''))
            if source is not None and not source.is_block and same_formula(source.latex, value):
                result = deepcopy(source.element)
                result.tail = node.tail
                return result
            result = Element('inline_expr', {'kind': 'text', DISPLAY_ATTRIBUTE: 'inline',
                             _INLINE_DISPLAY_KEY: json.dumps(None)})
            # Plain payloads (40, percentages, prose) remain legal expressions.
            # Whole-node LaTeX wrappers preserve formula notation without
            # scanning the artist's text or assigning formula identities.
            payload = formula_payload(value)
            if payload != value.strip():
                result.set('kind', value.strip()[:2] if value.strip().startswith(('\\(', '\\[')) else ('$$' if value.strip().startswith('$$') else '$'))
                result.text = payload
            else:
                result.text = value
            result.tail = node.tail
            return result
        return replace_formulas(root, convert)

    def visible_text(self, root: Element) -> str:
        if root.tag == _FORMULA_CONTEXT_TAG:
            return ''
        if root.tag == 'inline_expr' and root.get('kind') != 'text':
            return to_markdown_string(decode_expression_kind(root.get('kind', 'text')), plain_text(root))
        return (root.text or '') + ''.join(self.visible_text(child) + (child.tail or '') for child in root)

    def interrupt_translated_text_segments(self, text_segments: Iterable[TextSegment]) -> Iterable[TextSegment]:
        return (part for part in text_segments if not any(
            _FORMULA_CONTEXT_KEY in node.attrib for node in part.parent_stack))

    def interrupt_block_element(self, element: Element) -> Element:
        """Remove transient metadata before the XML submitter sees a token."""
        element.attrib.pop(_FORMULA_ID_KEY, None)
        return element

    def _formula_in(self, text_segment: TextSegment) -> tuple[_Formula | None, int | None]:
        """Return the formula owner and its parent-stack index for one segment."""
        equation_asset: Element | None = None
        equation_asset_index: int | None = None
        for index, element in enumerate(text_segment.parent_stack):
            if element.tag == "asset" and element.get("ref") == "formula":
                equation_asset = element
                equation_asset_index = index
                break

        if equation_asset is not None and equation_asset_index is not None:
            content = equation_asset.find("content")
            if content is not None and any(
                element is content
                for element in text_segment.parent_stack[equation_asset_index + 1:]
            ):
                return self._formula_for_asset(equation_asset), equation_asset_index

        for index, element in enumerate(text_segment.parent_stack):
            if element.tag == "inline_expr" and element.get("kind") != "text":
                return self._formula_for_inline(element), index
        return None, None

    def _formula_for_inline(self, element: Element) -> _Formula:
        existing = self._element_to_formula.get(id(element))
        if existing is not None:
            return existing
        kind = decode_expression_kind(element.get("kind", "text"))
        formula = self._new_formula(
            element=element,
            display="inline",
            latex=to_markdown_string(kind, plain_text(element)),
        )
        return formula

    def _formula_for_asset(self, element: Element) -> _Formula:
        """Freeze one formula asset's formula content as one unit."""
        existing = self._element_to_formula.get(id(element))
        if existing is not None:
            return existing
        content = element.find("content")
        formula = self._new_formula(
            element=element,
            display="block",
            latex=(
                f"$${_serialize_formula_content(content).strip() if content is not None else ''}$$"
            ),
        )
        return formula

    def _new_formula(self, element: Element, display: str, latex: str) -> _Formula:
        token_id = str(self._next_id)
        self._next_id += 1
        formula = _Formula(token_id, element, display, latex)
        self._element_to_formula[id(element)] = formula
        self._formula_by_id[token_id] = formula
        return formula

    def _finish_formula(self, token_id: str) -> Generator[TextSegment, None, None]:
        formula = self._formula_by_id.get(token_id)
        if formula is None:
            return
        if formula.is_block:
            # A display equation is made visible to both adjoining paragraphs.
            # This keeps its semantic context deterministic when XMLTranslator
            # has to cut the chapter into independent token-budget groups.
            if self._last_paragraph_stack is not None:
                yield self._context_token(formula, self._last_paragraph_stack, None)
            self._pending_block_formulas.append(formula)
            return
        replacement = self._pop_formula(token_id)
        if replacement is not None:
            yield replacement

    def _pop_formula(self, token_id: str) -> TextSegment | None:
        formula = self._formula_by_id.get(token_id)
        text_segments = self._raw_text_segments.pop(token_id, None)
        if formula is None or not text_segments:
            return None

        first = text_segments[0]
        _, formula_index = self._formula_in(first)
        if formula_index is None:  # Defensive: token ownership must be stable.
            return None

        placeholder = Element(
            _FORMULA_TAG,
            {
                _FORMULA_ID_KEY: token_id,
                DISPLAY_ATTRIBUTE: formula.display,
            },
        )
        parent_stack = first.parent_stack[:formula_index] + [placeholder]
        left_common_depth = first.left_common_depth
        right_common_depth = text_segments[-1].right_common_depth

        return TextSegment(
            text=f" {formula.latex} ",
            parent_stack=parent_stack,
            left_common_depth=left_common_depth,
            right_common_depth=right_common_depth,
            block_depth=_block_depth(parent_stack),
            position=first.position,
            progress_blocks=tuple(dict.fromkeys(block for segment in text_segments for block in segment.progress_blocks)),
        )

    def _context_token(
        self,
        formula: _Formula,
        parent_stack: list[Element],
        neighbor: TextSegment | None,
    ) -> TextSegment:
        """Create one output-discarded display-formula context token."""
        token_id = f"{formula.token_id}:context:{self._next_id}"
        self._next_id += 1
        self._formula_by_id[token_id] = formula
        self._block_formula_context_counts[formula.token_id] = (
            self._block_formula_context_counts.get(formula.token_id, 0) + 1
        )
        placeholder = Element(
            _FORMULA_TAG,
            {
                _FORMULA_ID_KEY: token_id,
                DISPLAY_ATTRIBUTE: "inline",
            },
        )
        context = Element(
            _FORMULA_CONTEXT_TAG,
            {
                _FORMULA_CONTEXT_KEY: token_id,
                DISPLAY_ATTRIBUTE: "inline",
            },
        )
        context.append(placeholder)
        position = neighbor.position if neighbor is not None else _text_position(parent_stack)
        return TextSegment(
            text=f" {formula.latex} ",
            # Keep the context inline with its neighboring paragraph, while
            # wrapping it in a synthetic marker.  That marker lets the return
            # path discard every model-produced character, including text the
            # model puts beside the expression token.
            parent_stack=[*parent_stack, context, placeholder],
            left_common_depth=0,
            right_common_depth=0,
            block_depth=_block_depth([*parent_stack, placeholder]),
            position=position,
            progress_blocks=tuple(dict.fromkeys(self._block_formula_progress.pop(formula.token_id, ()))),
        )

    def _standalone_block_token(self, formula: _Formula) -> TextSegment | None:
        """Keep an equation-only Chapter translatable without changing the asset."""
        parent_stack = self._block_formula_parent_stacks.get(formula.token_id)
        position = self._block_formula_positions.get(formula.token_id)
        if parent_stack is None or position is None:
            return None
        token_id = f"{formula.token_id}:standalone:{self._next_id}"
        self._next_id += 1
        self._formula_by_id[token_id] = formula
        placeholder = Element(
            _FORMULA_TAG,
            {
                _FORMULA_ID_KEY: token_id,
                DISPLAY_ATTRIBUTE: "block",
            },
        )
        context = Element(
            _FORMULA_CONTEXT_TAG,
            {
                _FORMULA_CONTEXT_KEY: token_id,
                DISPLAY_ATTRIBUTE: "inline",
            },
        )
        context.append(placeholder)
        return TextSegment(
            text=f" {formula.latex} ",
            parent_stack=[*parent_stack, context, placeholder],
            left_common_depth=0,
            right_common_depth=0,
            block_depth=_block_depth([*parent_stack, placeholder]),
            position=position,
            progress_blocks=tuple(dict.fromkeys(self._block_formula_progress.pop(formula.token_id, ()))),
        )


def _serialize_formula_content(element: Element) -> str:
    """Serialize an formula asset's content without translating nested formulas."""
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        if child.tag == "inline_expr" and child.get("kind") != "text":
            kind = decode_expression_kind(child.get("kind", "text"))
            parts.append(to_markdown_string(kind, plain_text(child)))
        else:
            parts.append(_serialize_formula_content(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _block_depth(parent_stack: list[Element]) -> int:
    """Avoid importing a private XMLTranslator implementation detail."""
    from pdf_craft.transformer.xml_translator.segment import find_block_depth

    return find_block_depth(parent_stack)


def _paragraph_block_stack(text_segment: TextSegment) -> list[Element] | None:
    """Return the v3 text/fragment stack owning a text segment.

    v1/v2 used ``paragraph/block``.  Keeping this helper tolerant of those
    historic names lets the transformer read old PCEX while all writers emit
    ``text/fragment``.
    """
    paragraph_index: int | None = None
    block_index: int | None = None
    for index, element in enumerate(text_segment.parent_stack):
        if element.tag in {"paragraph", "text"}:
            paragraph_index = index
        elif paragraph_index is not None and element.tag in {"block", "fragment"}:
            block_index = index
            break
    if block_index is None:
        return None
    return text_segment.parent_stack[:block_index + 1]


def _text_position(parent_stack: list[Element]) -> TextPosition:
    """Use a neighboring text position when available; otherwise TEXT."""
    del parent_stack
    return TextPosition.TEXT
