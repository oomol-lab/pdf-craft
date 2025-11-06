import re

from typing import Iterable

from .chapter import Reference, LineLayout, AssetLayout, ParagraphLayout
from .mark import transform2mark, Mark


_START_PREFIX_PATTERN = re.compile(r"^\*{1,6}\s+")

class References:
    def __init__(self, page_index: int, layouts: Iterable[AssetLayout | ParagraphLayout]) -> None:
        self._page_index: int = page_index
        self._references: list[Reference] = list(self._extract_references(page_index, layouts))
        self._mark2reference: dict[str | Mark, Reference] = {}
        for reference in self._references:
            mark = reference.mark
            if mark not in self._mark2reference:
                self._mark2reference[mark] = reference

    @property
    def page_index(self) -> int:
        return self._page_index

    def get(self, mark: str | Mark) -> Reference | None:
        return self._mark2reference.get(mark, None)

    def _extract_references(self, page_index: int, layouts: Iterable[AssetLayout | ParagraphLayout]):
        order: int = 1
        reference: Reference | None = None
        for item in self._iter_and_inject_marks(layouts):
            if isinstance(item, Mark | str):
                if reference:
                    yield reference
                reference = Reference(
                    page_index=page_index,
                    order=order,
                    mark=item,
                    layouts=[],
                )
                order += 1
            elif reference:
                reference.layouts.append(item)
            else:
                # TODO: 多余的内容可能是上一页的跨页页脚注释 / 引用，也可能是必须忽略的多余内容。
                #       此处没有能力进行判断，以后看看有什么好办法。
                pass
        if reference:
            yield reference

    def _iter_and_inject_marks(self, layouts: Iterable[AssetLayout | ParagraphLayout]):
        for layout in layouts:
            if isinstance(layout, AssetLayout):
                yield layout
            elif isinstance(layout, ParagraphLayout):
                for mark, sub_layout in self._split_paragraph_by_marks(layout):
                    if mark is not None:
                        yield mark
                    yield sub_layout

    def _split_paragraph_by_marks(self, to_split_layout: ParagraphLayout):
        mark_layout: tuple[Mark | str | None, ParagraphLayout] = (
            None,
            ParagraphLayout(ref=to_split_layout.ref, lines=[]),
        )
        for line in to_split_layout.lines:
            mark, content = self._extract_mark(line.content)
            if mark is None:
                mark_layout[1].lines.append(line)
            else:
                if mark_layout[1].lines:
                    yield mark_layout
                mark_layout = (mark, ParagraphLayout(
                    ref=to_split_layout.ref,
                    lines=[LineLayout(
                        page_index=line.page_index,
                        det=line.det,
                        content=[content],
                    )],
                ))
        if mark_layout[1].lines:
            yield mark_layout

    def _extract_mark(self, line_contents: list[str | Reference]) -> tuple[Mark | str | None, str]:
        content = ""
        for item in line_contents:
            # 此阶段不会有 Reference 故可断言全为 str
            if isinstance(item, str):
                content += item
        content = content.lstrip()
        if not content:
            return None, ""

        matched = _START_PREFIX_PATTERN.match(content)
        if matched:
            prefix = matched.group(0)
            stars = prefix.strip()
            rest = content[matched.end():]
            return stars, rest
        else:
            mark = transform2mark(content[0])
            if mark is not None:
                return mark, content[1:]
            return None, content