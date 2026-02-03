from dataclasses import dataclass
from pathlib import Path

from ..common import XMLReader, avg, read_xml, split_by_cv
from ..config import MAX_LEVELS, MAX_TITLE_CV
from ..pdf import TITLE_TAGS, Page, PageLayout
from ..pdf import decode as decode_as_page
from .text import normalize_text
from .toc_pages import PageRef

_MAX_TOC_CV = 0.75  # 不宜过小导致过多分组

Ref2Level = dict[tuple[int, int], int]  # key: (page_index, order) value: level


def analyse_title_levels(pages: XMLReader[Page]) -> Ref2Level:
    return _extract_content_title_levels(pages)


def analyse_toc_levels(
    pages: XMLReader[Page], pages_path: Path, toc_pages: list[PageRef]
) -> Ref2Level:
    ref2meta, toc_page_indexes = _extract_ref2meta(
        pages_path=pages_path,
        toc_pages=toc_pages,
    )
    ref2global_level = _extract_content_title_levels(
        pages=pages,
        disable_page_indexes=toc_page_indexes,
        ref2meta=ref2meta,
    )
    toc_level_offset = _extract_toc_level_offset(
        ref2meta=ref2meta,
        ref2level=ref2global_level,
    )
    ref2level: Ref2Level = {}
    for (page_index, order), meta in ref2meta.items():
        level_offset = toc_level_offset.get(meta.toc_page_index, None)
        if (
            level_offset is not None
        ):  # toc_level_offset 比 ref2meta 范围小，目录页子圈会被排除
            global_level = meta.relative_level + level_offset
            ref2level[(page_index, order)] = global_level

    return ref2level


@dataclass
class _Hook:
    layout: PageLayout
    references: list[tuple[int, int]]  # (page_index, order)


@dataclass
class _TitleMeta:
    toc_page_index: int
    relative_level: int
    collected_global_levels: list[int]


_Ref2Meta = dict[tuple[int, int], _TitleMeta]


def _extract_ref2meta(
    pages_path: Path, toc_pages: list[PageRef]
) -> tuple[_Ref2Meta, set[int]]:
    ref2meta: _Ref2Meta = {}  # key: (page_index, order)
    toc_page_indexes: set[int] = set()

    for ref in toc_pages:
        toc_page_indexes.add(ref.page_index)
        grouped_hooks = _analyse_toc_page_hooks(
            ref=ref,
            page_path=Path(pages_path / f"page_{ref.page_index}.xml"),
        )
        for level, hooks in enumerate(grouped_hooks):
            for hook in sorted(hooks, key=lambda x: x.layout.order):
                for page_index, order in hook.references:
                    if (page_index, order) not in ref2meta:
                        ref2meta[(page_index, order)] = _TitleMeta(
                            toc_page_index=ref.page_index,
                            relative_level=level,
                            collected_global_levels=[],
                        )
    return ref2meta, toc_page_indexes


def _analyse_toc_page_hooks(ref: PageRef, page_path: Path) -> list[list[_Hook]]:
    page = decode_as_page(read_xml(page_path))
    hooks_items: list[tuple[float, _Hook]] = []

    for layout in page.body_layouts:
        layout_text = normalize_text(layout.text)
        references_set: set[tuple[int, int]] = set()
        for title in ref.matched_titles:
            if title.text not in layout_text:
                continue
            for reference in title.references:
                references_set.add(
                    (
                        reference.page_index,
                        reference.order,
                    )
                )
        if not references_set:
            continue
        _, top, _, bottom = layout.det
        height = bottom - top
        hooks_items.append(
            (
                height,
                _Hook(
                    layout=layout,
                    references=list(references_set),
                ),
            )
        )
    hooks = split_by_cv(
        payload_items=hooks_items,
        max_groups=MAX_LEVELS,
        max_cv=_MAX_TOC_CV,
    )
    hooks.reverse()  # 字体最大的是 Level 0，故颠倒
    return hooks


def _extract_content_title_levels(
    pages: XMLReader[Page],
    disable_page_indexes: set[int] | None = None,
    ref2meta: _Ref2Meta | None = None,
) -> Ref2Level:
    title_items: list[tuple[float, tuple[int, int]]] = []
    for page in pages.read():
        if disable_page_indexes and page.index in disable_page_indexes:
            continue
        for layout in page.body_layouts:
            if layout.ref not in TITLE_TAGS:
                continue
            ref = (page.index, layout.order)
            if ref2meta and ref not in ref2meta:
                continue
            _, top, _, bottom = layout.det
            height = bottom - top
            title_items.append((height, ref))

    title2levels: Ref2Level = {}
    for level, refs in enumerate(
        reversed(
            split_by_cv(  # 字体最大的是 Level 0，故颠倒
                payload_items=title_items,
                max_groups=MAX_LEVELS,
                max_cv=MAX_TITLE_CV,
            )
        )
    ):
        for ref in refs:
            title2levels[ref] = level

    return title2levels


def _extract_toc_level_offset(
    ref2meta: _Ref2Meta, ref2level: Ref2Level
) -> dict[int, int]:
    # 目录可能有多页，每页自己的 Level 只能表示相对关系，这里计算出全局目录的 level
    for page_index, order in sorted(ref2level.keys()):
        meta = ref2meta[(page_index, order)]
        level = ref2level[(page_index, order)]
        meta.collected_global_levels.append(level)

    page2metas: dict[int, list[_TitleMeta]] = {}
    for meta in ref2meta.values():
        metas = page2metas.get(meta.toc_page_index, None)
        if metas is None:
            metas = []
            page2metas[meta.toc_page_index] = metas
        metas.append(meta)

    avg_level_items: list[tuple[float, int]] = []  # (avg_level, page_index)
    for page_index, metas in page2metas.items():
        metas.sort(key=lambda x: x.relative_level)
        meta = metas[0]
        levels = metas[0].collected_global_levels
        if levels:
            avg_level = avg(levels)
            avg_level_items.append((avg_level, page_index))

    toc_level_offset: dict[int, int] = {}
    for offset, page_indexes in enumerate(
        split_by_cv(
            payload_items=avg_level_items,
            max_groups=MAX_LEVELS,
            max_cv=_MAX_TOC_CV,
        )
    ):
        # 大多数情况下只有一组，便只有 offset=0 的情况。
        for page_index in page_indexes:
            toc_level_offset[page_index] = offset

    return toc_level_offset
