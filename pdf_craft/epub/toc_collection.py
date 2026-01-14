from pathlib import Path

from epub_generator import ChapterGetter, TocItem

from ..common import read_xml
from ..toc import Toc, decode


class TocCollection:
    def __init__(self, toc_path: Path | None) -> None:
        self._root: list[Toc]
        self._root_toc_items: list[TocItem] = []
        self._extra_toc_items: list[TocItem] = []
        self._id_to_toc_item: dict[int, TocItem] = {}
        self._having_body_toc_set: set[int] = set()

        if toc_path:
            self._root = decode(read_xml(toc_path)).content
        else:
            self._root = []

    @property
    def target(self) -> list[TocItem]:
        return self._root_toc_items + self._extra_toc_items

    def collect(
        self,
        toc_id: int,
        title: str,
        have_body: bool,
        get_chapter: ChapterGetter | None,
    ) -> None:
        toc_item: TocItem | None = None
        stack = self._find_raw_toc_item_stack(toc_id)

        if stack:
            current_toc_items = self._root_toc_items
            for raw_item in stack:
                toc_item = self._find_or_append_toc_item(raw_item.id, current_toc_items)
                current_toc_items = toc_item.children
            assert toc_item
            toc_item.title = title
            toc_item.get_chapter = get_chapter
        else:
            toc_item = TocItem(
                title=title,
                get_chapter=get_chapter,
            )
            self._extra_toc_items.append(toc_item)
            self._id_to_toc_item[toc_id] = toc_item

        if have_body:
            self._having_body_toc_set.add(id(toc_item))

    def normalize(self) -> "TocCollection":
        self._clean_no_content_items(self._root_toc_items)
        self._clean_no_content_items(self._extra_toc_items)
        return self

    def _find_raw_toc_item_stack(self, toc_id: int) -> list[Toc]:
        index: int = 0
        current_toc_items: list[Toc] = self._root
        stack: list[tuple[int, list[Toc], Toc]] = []
        while True:
            if index >= len(current_toc_items):
                if not stack:
                    break
                index, current_toc_items, _ = stack.pop()
                index += 1
            else:
                item = current_toc_items[index]
                stack.append((index, current_toc_items, item))
                if item.id == toc_id:
                    break
                current_toc_items = item.children
                index = 0

        return [item for _, _, item in stack]

    def _find_or_append_toc_item(self, id: int, toc_items: list[TocItem]) -> TocItem:
        toc_item = self._id_to_toc_item.get(id, None)
        if toc_item:
            if toc_item in toc_items:
                return toc_item
            else:
                raise RuntimeError(
                    f"TOC item with ID {id} already exists in another branch."
                )
        else:
            toc_item = TocItem(
                title="unknown",
                get_chapter=None,
            )
            toc_items.append(toc_item)
            self._id_to_toc_item[id] = toc_item
        return toc_item

    def _clean_no_content_items(self, toc_items: list[TocItem]):
        index = 0
        while index < len(toc_items):
            should_keep = True
            toc_item = toc_items[index]
            self._clean_no_content_items(toc_item.children)

            if id(toc_item) not in self._having_body_toc_set and not toc_item.children:
                # 有子节点同时自己也没有内容，变为仅存在于目录中的章节有助于更好的阅读体验
                toc_item.get_chapter = None
                should_keep = False

            if should_keep:
                index += 1
            else:
                toc_items.pop(index)
