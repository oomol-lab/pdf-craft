import re
from typing import Iterable

from .chapter import (
    SourceAsset, SourceTextFragment, DisplayFormula, TextFlowItem, Reference,
    StandaloneAsset,
)
from .content import Content
from .mark import Mark, transform2mark

_START_PREFIX_PATTERN = re.compile(r"^\*{1,6}\s+")


class References:
    def __init__(
        self, page_index: int, items: Iterable[SourceAsset | TextFlowItem]
    ) -> None:
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
            if not isinstance(block, SourceTextFragment):
                raise ValueError("footnote TextFlowItem cannot contain anchored assets before flow assembly")
            mark, content = self._extract_head_mark(block.content)
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
                            )
                        ],
                    ),
                )
        if mark_layout[1].children:
            yield mark_layout

    def _extract_head_mark(self, content: Content) -> tuple[Mark | str | None, Content]:
        if not content or not isinstance(content[0], str):
            return None, content
        head_text = content[0].lstrip()
        if not head_text:
            return None, content

        mark: Mark | str | None = None
        rest: str = ""
        matched = _START_PREFIX_PATTERN.match(head_text)
        new_content: Content = content[1:]

        if matched:
            prefix = matched.group(0)
            mark = prefix.strip()
            rest = head_text[matched.end() :].lstrip()
        else:
            mark = transform2mark(head_text[0])
            if mark is not None:
                rest = head_text[1:].lstrip()

        rest = rest.lstrip()
        if rest:
            new_content = [rest] + content[1:]

        return mark, new_content
