"""Keep PCEX formulas visible to translation while restoring their source form.

``Chapter`` XML represents inline formulas as ``inline_expr`` nodes and display
formulas as the ``content`` of an ``asset ref=\"equation\"``.  Neither shape is
MathML, so the EPUB MathML interrupter cannot be used here.  This adapter uses
the same XMLTranslator interruption protocol with the PCEX schema instead.
"""

from collections.abc import Generator, Iterable
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from pdf_craft.expression import decode_expression_kind, to_markdown_string
from pdf_craft.transformer.xml_translator.segment import TextPosition, TextSegment
from pdf_craft.transformer.xml_translator.xml import (
    DISPLAY_ATTRIBUTE,
    plain_text,
)


_FORMULA_ID_KEY = "__PDF_CRAFT_CHAPTER_FORMULA_ID"
_FORMULA_TAG = "expression"


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
    """Expose PCEX formulas as frozen, readable XMLTranslator tokens.

    The temporary ``expression`` nodes never reach a decoded Chapter.  The
    source text shown to the language model contains Markdown-style LaTex
    delimiters, while the translated stream is replaced with the original
    ``inline_expr`` text segments. Equation assets are injected as discarded
    context tokens into their preceding and following paragraphs, so a token
    budget split cannot hide an equation from either neighboring translation.
    The original ``asset`` remains in the Chapter XML untouched.
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
        self._block_formula_context_counts: dict[str, int] = {}

    def interrupt_source_text_segments(
        self, text_segments: Iterable[TextSegment],
    ) -> Generator[TextSegment, None, None]:
        """Replace source formulas with readable temporary formula tokens."""
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

    def interrupt_translated_text_segments(
        self, text_segments: Iterable[TextSegment],
    ) -> Generator[TextSegment, None, None]:
        """Discard translated formula text and restore original inline segments."""
        for text_segment in text_segments:
            parent_element = text_segment.parent_stack[-1]
            token_id = parent_element.attrib.pop(_FORMULA_ID_KEY, None)
            if token_id is None:
                yield text_segment
                continue

            formula = self._formula_by_id.get(token_id)
            if formula is None:
                # A foreign temporary node must never become a formula result.
                continue
            if formula.is_block:
                # Equation asset content remains in the source XML.  Returning
                # no segment prevents XML submission from replacing it.
                continue

            raw_text_segments = self._raw_text_segments.pop(token_id, None)
            if not raw_text_segments:
                # Do not fall back to model-produced formula text if a token was
                # malformed or lost. Formula fidelity is stricter than text fill.
                continue

            text_basic_parent_stack = text_segment.parent_stack[:-1]
            for raw_text_segment in raw_text_segments:
                raw_text_segment.parent_stack = (
                    text_basic_parent_stack + raw_text_segment.parent_stack
                )
                yield raw_text_segment

    def interrupt_block_element(self, element: Element) -> Element:
        """Remove transient metadata before the XML submitter sees a token."""
        element.attrib.pop(_FORMULA_ID_KEY, None)
        return element

    def _formula_in(self, text_segment: TextSegment) -> tuple[_Formula | None, int | None]:
        """Return the formula owner and its parent-stack index for one segment."""
        asset_content_index: int | None = None
        in_equation_asset = False
        for index, element in enumerate(text_segment.parent_stack):
            if element.tag == "asset" and element.get("ref") == "equation":
                in_equation_asset = True
                asset_content_index = None
                continue
            if in_equation_asset and element.tag == "content":
                asset_content_index = index
                continue
            if element.tag == "inline_expr" and element.get("kind") != "text":
                if asset_content_index is not None:
                    # The equation asset content is one opaque display formula,
                    # even when its OCR representation happens to contain an
                    # inline_expr child.
                    continue
                return self._formula_for_inline(element), index

        if asset_content_index is not None:
            element = text_segment.parent_stack[asset_content_index]
            return self._formula_for_asset_content(element), asset_content_index
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

    def _formula_for_asset_content(self, element: Element) -> _Formula:
        existing = self._element_to_formula.get(id(element))
        if existing is not None:
            return existing
        formula = self._new_formula(
            element=element,
            display="block",
            latex=f"$${_serialize_formula_content(element).strip()}$$",
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
        text_segments = self._raw_text_segments.get(token_id)
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
        for text_segment in text_segments:
            # Preserve just the original formula-relative stack. It is grafted
            # back below the translated text location during restoration.
            text_segment.left_common_depth = max(0, text_segment.left_common_depth - formula_index)
            text_segment.right_common_depth = max(0, text_segment.right_common_depth - formula_index)
            text_segment.parent_stack = text_segment.parent_stack[formula_index:]
            text_segment.block_depth = _block_depth(text_segment.parent_stack)

        return TextSegment(
            text=f" {formula.latex} ",
            parent_stack=parent_stack,
            left_common_depth=left_common_depth,
            right_common_depth=right_common_depth,
            block_depth=_block_depth(parent_stack),
            position=first.position,
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
        position = neighbor.position if neighbor is not None else _text_position(parent_stack)
        return TextSegment(
            text=f" {formula.latex} ",
            parent_stack=[*parent_stack, placeholder],
            left_common_depth=0,
            right_common_depth=0,
            block_depth=_block_depth([*parent_stack, placeholder]),
            position=position,
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
        return TextSegment(
            text=f" {formula.latex} ",
            parent_stack=[*parent_stack, placeholder],
            left_common_depth=0,
            right_common_depth=0,
            block_depth=_block_depth([*parent_stack, placeholder]),
            position=position,
        )


def _serialize_formula_content(element: Element) -> str:
    """Serialize an equation asset's content without translating nested formulas."""
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
    """Return the actual ParagraphLayout block owning a text segment, if any."""
    paragraph_index: int | None = None
    block_index: int | None = None
    for index, element in enumerate(text_segment.parent_stack):
        if element.tag == "paragraph":
            paragraph_index = index
        elif paragraph_index is not None and element.tag == "block":
            block_index = index
            break
    if block_index is None:
        return None
    return text_segment.parent_stack[:block_index + 1]


def _text_position(parent_stack: list[Element]) -> TextPosition:
    """Use a neighboring text position when available; otherwise TEXT."""
    del parent_stack
    return TextPosition.TEXT
