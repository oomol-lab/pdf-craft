import re

from pathlib import Path
from typing import Generator

from ...common import save_xml, read_xml, XMLReader
from ...pdf import decode as decode_pdf, Page, TITLE_TAGS
from ..common import encode as encode_toc, decode as decode_toc, PageRef

from .types import Toc
from .toc_pages import find_toc_pages
from .toc_levels import analyse_toc_levels, analyse_title_levels, Ref2Level


_TITLE_HEAD_REGX = re.compile(r"^\s*#{1,6}\s*")

# def analyse_toc_range(pages_path: Path, toc_pages_path: Path | None) -> list[PageRef]:
#     if toc_pages_path.exists():
#         return decode_toc(read_xml(toc_pages_path))

#     toc_pages_path.parent.mkdir(parents=True, exist_ok=True)
#     save_xml(encode_toc(toc_pages), toc_pages_path)

#     return toc_pages

def _analyse_toc(pages_path: Path, focus_toc: bool) -> list[Toc]:
    pages: XMLReader[Page] = XMLReader(
        prefix="page",
        dir_path=pages_path,
        decode=decode_pdf,
    )
    ref2level: Ref2Level
    if focus_toc:
        toc_pages = find_toc_pages(
            iter_titles=lambda:(
                list(
                    (layout.order, _TITLE_HEAD_REGX.sub("", layout.text))
                    for layout in page.body_layouts
                    if layout.ref in TITLE_TAGS
                )
                for page in pages.read()
            ),
            iter_page_bodies=lambda:(
                "".join(layout.text for layout in page.body_layouts)
                for page in pages.read()
            ),
        )
        ref2level = analyse_toc_levels(
            pages=pages,
            pages_path=pages_path,
            toc_pages=toc_pages,
        )
    else:
        ref2level = analyse_title_levels(pages)

    return _structure_toc_by_levels(ref2level)

def _structure_toc_by_levels(ref2level: Ref2Level) -> list[Toc]:
    next_id: int = 1
    root = Toc(id=-1, page_index=-1, order=-1, level=-1, children=[]) # 虚拟根节点
    stack: list[Toc] = [root]

    for (page_index, order), level in sorted(ref2level.items(), key=lambda x: x[0]):
        toc = Toc(
            id=next_id,
            page_index=page_index,
            order=order,
            level=level,
            children=[],
        )
        next_id += 1
        while stack and stack[-1].level >= level:
            stack.pop()

        if not stack:
            break # 防御性

        stack[-1].children.append(toc)
        stack.append(toc)

    return root.children
