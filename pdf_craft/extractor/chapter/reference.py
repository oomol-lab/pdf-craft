import re
from typing import Iterable

from ...expression import ExpressionKind
from .chapter import (
    SourceAsset, SourceTextFragment, DisplayFormula, TextFlowItem, Reference,
    StandaloneAsset, InlineExpression,
)
from .content import Content
from .mark import Mark, transform2mark
from .numeric_marks import inline_number, html_number, SUPERSCRIPT_NUMBERS, superscript_number

_START_PREFIX_PATTERN = re.compile(r"^(?:\\?\*){1,6}\s+")
_ESCAPED_STAR_PREFIX = re.compile(r'^(?:\\\*){1,6}(?!\\?\*)\s*(?=\S)')
_NUMERIC_PREFIX = re.compile(r'^([1-9][0-9]{0,2})[.)]?\s+(?=\S)')
_DEFINITION_PREFIX = re.compile(r'^(?:([1-9][0-9]{0,2})[.)]|\(([1-9][0-9]{0,2})\))\s+(?=\S)')


class References:
    def __init__(
        self, page_index: int, items: Iterable[SourceAsset | TextFlowItem], *, numeric_marks=(), flat_marks=(),
    ) -> None:
        self._numeric_marks = numeric_marks
        self._flat_marks = frozenset(flat_marks)
        self._page_index: int = page_index
        self._unindexed_items: list[SourceAsset | TextFlowItem] = []
        self._references: list[Reference] = list(
            self._extract_references(page_index, items)
        )
        self._mark2reference: dict[str | Mark, Reference] = {}
        for reference in self._references:
            mark = reference.mark
            if mark not in self._mark2reference:
                self._mark2reference[mark] = reference

    @property
    def page_index(self) -> int:
        return self._page_index

    @property
    def values(self) -> tuple[Reference, ...]:
        return tuple(self._references)

    @property
    def unindexed_items(self) -> tuple[SourceAsset | TextFlowItem, ...]:
        """Footnote content seen before the first page-local citation mark."""

        return tuple(self._unindexed_items)

    def get(self, mark: str | Mark) -> Reference | None:
        return self._mark2reference.get(mark, None)

    def flattened_reference(self, mark: str) -> Reference | None:
        """Only a unique paired candidate can consume a flattened body digit."""
        return self.get(mark) if mark in self._flat_marks else None

    def _extract_references(
        self, page_index: int, items: Iterable[SourceAsset | TextFlowItem]
    ):
        order: int = 1
        reference: Reference | None = None
        for item in self._iter_and_inject_marks(items):
            if isinstance(item, Mark | str):
                if reference:
                    yield reference
                reference = Reference(
                    page_index=page_index,
                    order=order,
                    mark=item,
                    flow_items=[],
                )
                order += 1
            elif reference:
                reference.flow_items.append(
                    DisplayFormula(item) if isinstance(item, SourceAsset) and item.ref == "formula"
                    else StandaloneAsset(item) if isinstance(item, SourceAsset)
                    else item
                )
            else:
                # TODO: 多余的内容可能是上一页的跨页页脚注释 / 引用，也可能是必须忽略的多余内容。
                #       此处没有能力进行判断，以后看看有什么好办法。
                self._unindexed_items.append(item)
        if reference:
            yield reference

    def _iter_and_inject_marks(self, items: Iterable[SourceAsset | TextFlowItem]):
        for item in items:
            if isinstance(item, SourceAsset):
                yield item
            elif isinstance(item, TextFlowItem):
                for mark, sub_layout in self._split_paragraph_by_marks(item):
                    if mark is not None:
                        yield mark
                    yield sub_layout

    def _split_paragraph_by_marks(self, to_split_layout: TextFlowItem):
        mark_layout: tuple[Mark | str | None, TextFlowItem] = (
            None,
            TextFlowItem(
                role=to_split_layout.role,
                level=-1,
                children=[],
            ),
        )
        for block in to_split_layout.children:
            if isinstance(block, SourceAsset):
                mark_layout[1].children.append(block)
                continue
            mark, content = extract_head_mark(block.content, numeric_marks=self._numeric_marks)
            if mark is None:
                mark_layout[1].children.append(block)
            else:
                if mark_layout[1].children:
                    yield mark_layout
                mark_layout = (
                    mark,
                    TextFlowItem(
                        role=to_split_layout.role,
                        level=-1,
                        children=[
                            SourceTextFragment(
                                page_index=block.page_index,
                                source_order=block.source_order,
                                bbox=block.bbox,
                                content=content,
                                source=block.source,
                            )
                        ],
                    ),
                )
        if mark_layout[1].children:
            yield mark_layout


def extract_head_mark(content: Content, *, numeric_marks=()) -> tuple[Mark | str | None, Content]:
    if not content:
        return None, content
    first = content[0]
    numeric = inline_number(first) or html_number(first)
    if numeric is not None:
        remaining = list(content[1:])
        if remaining and isinstance(remaining[0], str):
            remaining[0] = remaining[0].lstrip()
        return numeric, remaining
    if isinstance(first, InlineExpression):
        # OCR may transport a printed marker as inline math. Accept only an
        # entire known marker, never a prefix of an actual formula.
        if first.kind not in (ExpressionKind.INLINE_PAREN, ExpressionKind.INLINE_DOLLAR):
            return None, content
        literal = first.content.strip()
        mark = transform2mark(literal)
        if literal in numeric_marks:
            mark = literal
        if mark is None and re.fullmatch(r"\*{1,6}", literal):
            mark = literal
        if mark is None:
            return None, content
        remaining = list(content[1:])
        if remaining and isinstance(remaining[0], str):
            remaining[0] = remaining[0].lstrip()
        return mark, remaining
    if not isinstance(first, str):
        return None, content
    head_text = first.lstrip()
    if not head_text:
        return None, content

    # Definition typography is evidence even when OCR omitted the body mark.
    # Bare quantities still need pairing; do not fabricate a missing reference.
    definition = _DEFINITION_PREFIX.match(head_text)
    if definition:
        return definition[1] or definition[2], [head_text[definition.end():], *content[1:]]
    numbered = _NUMERIC_PREFIX.match(head_text)
    if numbered and numbered[1] in numeric_marks:
        return numbered[1], [head_text[numbered.end():], *content[1:]]
    raised = SUPERSCRIPT_NUMBERS.match(head_text)
    if raised and (numeric := superscript_number(raised[0])) is not None:
        return numeric, [head_text[raised.end():].lstrip(), *content[1:]]

    mark: Mark | str | None = None
    rest: str = ""
    # An explicit Markdown escape already establishes a literal star. Requiring
    # a space after it loses perfectly valid attached English/CJK note text.
    matched = _ESCAPED_STAR_PREFIX.match(head_text) or _START_PREFIX_PATTERN.match(head_text)
    new_content: Content = content[1:]

    if matched:
        prefix = matched.group(0)
        mark = prefix.strip().replace("\\", "")
        rest = head_text[matched.end() :].lstrip()
    else:
        mark = transform2mark(head_text[0])
        if mark is not None:
            rest = head_text[1:].lstrip()
    if mark is None:
        return None, content

    rest = rest.lstrip()
    if rest:
        new_content = [rest] + content[1:]

    return mark, new_content
