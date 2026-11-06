from dataclasses import dataclass
from typing import Generator, Iterable, cast

from ...pdf import PageLayout
from .chapter import SourceAsset, SourceTextFragment, TextSource, TextFlowItem
from .content import first, last, _normalize_paragraph_content
from .asset_text import AssetLayoutParser
from .source import _is_empty_text_layout
from .mergeable import check_mergeable, has_numbered_prefix, reported_turn_boundary
from .paragraph_geometry import (
    indented_line_boundary, indented_line_starts, printed_separator_boundary, colophon_record_starts,
    completed_equation_rows, independent_horizontal_fields,
)
from .reading_serials import split_reading_serials


@dataclass
class _LastTail:
    page_para: TextFlowItem
    override: list[SourceAsset]


@dataclass(frozen=True)
class JoinedPageProgress:
    """Processed pages may still own a tail after ``closed_through``.

    No later joined item can originate on or before the closed watermark.
    """

    closed_through: int
    processed_through: int


class Jointer:
    def __init__(self, layouts: Iterable[tuple[int, list[PageLayout]]],
                 sources: dict[tuple[int, int], tuple[int, TextSource]] | None = None) -> None:
        self._layouts = layouts
        self._sources = sources if sources is not None else {}
        self._reading_positions: dict[tuple[int, tuple[int, int, int, int]], int] = {}
        self._asset_parser = AssetLayoutParser(self._sources, self._reading_positions)
        self._indented_starts: set[tuple[int, int, int, int]] = set()
        self._current_page: int | None = None
        self._colophon_starts: set[tuple[int, int, int, int]] = set()
        self._equation_rows: dict[tuple[int, int, int, int], int] = {}
        self._last_tail: _LastTail | None = None
        self._last_page_index: int | None = None
        self._closed_through = -1

    def execute(self) -> Generator[TextFlowItem | SourceAsset, None, None]:
        for item in self.execute_with_progress():
            if not isinstance(item, JoinedPageProgress):
                yield item

    def execute_with_progress(self) -> Generator[TextFlowItem | SourceAsset | JoinedPageProgress, None, None]:
        for page_index, layouts in self._layouts:
            yield from self.append(page_index, layouts)
        yield from self.finish()

    def append(self, page_index: int, page_layouts: list[PageLayout]):
        """Join one available page without reading or waiting for another."""
        for page_index, raw_layouts in self._iter_layout_serials(((page_index, page_layouts),), with_progress=True):
            self._last_page_index = page_index
            if raw_layouts is None:
                safe_page = page_index
                if self._last_tail is not None:
                    safe_page = self._last_tail.page_para.children[0].page_index - 1
                self._closed_through = max(self._closed_through, safe_page)
                yield JoinedPageProgress(self._closed_through, page_index)
                continue
            if not raw_layouts:
                if self._last_tail:
                    _normalize_paragraph_content(self._last_tail.page_para)
                    yield self._last_tail.page_para
                    yield from self._last_tail.override
                    self._last_tail = None
                continue

            # 此处为完成如下业务要求：
            # 1. 当阅读序列跨越 group（跨页、跨分栏、跨因图片而挤变形拆分的段落）时，必须对连接处验证。若它们是被拆分的自然段，则拼起来。
            # 2. 因为插图、表格而拆分的自然段，需将插图存起来接到完整的自然段最后，而不是任其分割自然段。
            layouts = list(self._embed_assets(self._asset_parser.parse(page_index, raw_layouts)))
            head, body, tail = self._split_layouts(layouts)

            if not body:
                if self._last_tail:
                    self._last_tail.override.extend(head)
                    self._last_tail.override.extend(tail)
                else:
                    yield from head
                    yield from tail
                continue

            first_layout = cast(TextFlowItem, body[0])
            # Assets retain their position between source text fragments.
            # Only display formulas block an author paragraph; images/tables
            # become children when the text on both sides is continuous.
            if self._last_tail and any(asset.ref == "formula" for asset in [*self._last_tail.override, *head]):
                _normalize_paragraph_content(self._last_tail.page_para)
                yield self._last_tail.page_para
                yield from self._last_tail.override
                self._last_tail = None
            if self._last_tail and self._can_merge_paragraphs(
                self._last_tail.page_para, first_layout,
                across_assets=[*self._last_tail.override, *head],
            ):
                # ``blocks`` is a compatibility projection.  Extend the real
                # ordered v3 children so cross-page source fragments survive.
                self._last_tail.page_para.children.extend(self._last_tail.override)
                self._last_tail.page_para.children.extend(head)
                self._last_tail.page_para.children.extend(first_layout.children)
                self._last_tail.override = []
                head = []
                del body[0]

            if not body:
                if self._last_tail:
                    self._last_tail.override.extend(head)
                    self._last_tail.override.extend(tail)
                else:
                    yield from head
                    yield from tail
                continue

            # 至此，连续吞并段落的流程遇阻而结束
            if self._last_tail:
                _normalize_paragraph_content(self._last_tail.page_para)
                yield self._last_tail.page_para
                yield from self._last_tail.override
                self._last_tail = None

            yield from head
            for i in range(len(body) - 1):
                yield body[i]

            self._last_tail = _LastTail(
                page_para=cast(TextFlowItem, body[-1]),
                override=list(tail),
            )

    def finish(self):
        """Close the retained paragraph at input EOF."""
        if self._last_tail:
            _normalize_paragraph_content(self._last_tail.page_para)
            yield self._last_tail.page_para
            yield from self._last_tail.override
        if self._last_page_index is not None and self._last_page_index > self._closed_through:
            yield JoinedPageProgress(self._last_page_index, self._last_page_index)

        self._last_tail = None
        self._closed_through = self._last_page_index if self._last_page_index is not None else -1

    def _iter_layout_serials(
        self, pages, *, with_progress: bool = False,
    ) -> Generator[tuple[int, list[PageLayout] | None], None, None]:
        for page_index, raw_layouts in pages:
            self._reading_positions.clear()
            self._current_page = page_index
            self._indented_starts = indented_line_starts(raw_layouts)
            self._colophon_starts = colophon_record_starts(raw_layouts)
            self._equation_rows = completed_equation_rows(raw_layouts)
            for layout in raw_layouts:
                record = self._sources.get((page_index, id(layout)))
                if record is not None:
                    self._reading_positions[(page_index, layout.det)] = record[1].reading_order
            found_serial = False
            for layouts in split_reading_serials(
                [layout for layout in raw_layouts if not _is_empty_text_layout(layout)]
            ):
                found_serial = True
                yield page_index, layouts
            if not found_serial:
                yield page_index, []
            if with_progress:
                yield page_index, None

    def _split_layouts(self, layouts: list[TextFlowItem | SourceAsset]):
        head: list[SourceAsset] = []
        tail: list[SourceAsset] = []

        for layout in layouts:
            if isinstance(layout, TextFlowItem):
                break
            head.append(layout)

        for i in range(len(layouts) - 1, -1, -1):
            if i < len(head):
                break
            layout = layouts[i]
            if isinstance(layout, TextFlowItem):
                break
            tail.append(layout)

        tail.reverse()
        body = layouts[len(head) : len(layouts) - len(tail)]

        return head, body, tail


    def _embed_assets(self, layouts):
        """Decide same-serial asset seams here, using the page's raw geometry.

        Flow packaging must not perform a second, weaker paragraph inference.
        Keep only the current author paragraph and its intervening assets.
        """
        current = None
        pending = []
        for layout in layouts:
            if isinstance(layout, SourceAsset):
                pending.append(layout)
                continue
            if (current is not None and pending
                    and not any(asset.ref == "formula" for asset in pending)
                    and self._can_merge_paragraphs(current, layout, across_assets=pending)):
                current.children.extend(pending)
                current.children.extend(layout.children)
                pending = []
                continue
            if current is not None:
                yield current
            yield from pending
            pending = []
            current = layout
        if current is not None:
            yield current
        yield from pending

    def _can_merge_paragraphs(
        self, para1: TextFlowItem, para2: TextFlowItem, *, across_assets: Iterable[SourceAsset] = (),
    ) -> bool:
        if para1.role != "body":
            return False
        if para1.role != para2.role:
            return False

        block1 = para1.children[-1]
        block2 = para2.children[0]
        if not isinstance(block1, SourceTextFragment) or not isinstance(block2, SourceTextFragment):
            return False

        if independent_horizontal_fields(block1, block2):
            return False

        if (block1.page_index == block2.page_index == self._current_page
                and block2.bbox in self._colophon_starts):
            return False
        assets = tuple(across_assets)
        if not assets and block1.page_index == block2.page_index == self._current_page:
            row_end = self._equation_rows.get(block1.bbox)
            indent = block2.bbox in self._indented_starts or (
                block2.source is not None and block2.source.first_line_inset is not None
                and block2.source.first_line_inset >= .75)
            if (row_end is not None and indent
                    and block2.bbox[1] >= row_end + .5 * (block1.bbox[3] - block1.bbox[1])):
                return False
        if not assets and printed_separator_boundary(para1, para2):
            return False

        # OCR sometimes uses quotation brackets for numbered commentary entries.
        # Do not treat a quoted numeral in ordinary prose as an entry: establish
        # the numbered context from the paragraph head, even across line seams.
        origin = para1.children[0]
        if (not assets and isinstance(origin, SourceTextFragment)
                and reported_turn_boundary(first(origin.content), last(block1.content), first(block2.content))):
            return False
        if (isinstance(origin, SourceTextFragment)
                and has_numbered_prefix(origin.content, include_quoted=True)
                and has_numbered_prefix(block2.content, include_quoted=True)):
            return False

        # A figure alongside text may distort the column. A block fully between
        # two text boxes cannot cause the following line's observed inset.
        isolated = (assets and block1.page_index == block2.page_index and all(
            asset.page_index == block2.page_index
            and block1.bbox[3] <= asset.bbox[1] and asset.bbox[3] <= block2.bbox[1]
            for asset in assets))
        if (not assets or isolated) and indented_line_boundary(
            para1, para2, column_indented=(block2.page_index == self._current_page
                                          and block2.bbox in self._indented_starts),
        ):
            return False

        return check_mergeable(block1.content, block2.content,
                               continued_page=block1.page_index != block2.page_index)


# 将单词的连接符 `-` 删去，并将后半节单词移到前面一段拼接
