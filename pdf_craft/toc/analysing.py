import re

from pathlib import Path

from ..common import save_xml, read_xml, XMLReader
from ..pdf import decode as decode_pdf, Page, TITLE_TAGS

from .types import encode as encode_toc, decode as decode_toc, Toc, TocInfo
from .toc_pages import find_toc_pages, PageRef
from .toc_levels import analyse_toc_levels, analyse_title_levels, Ref2Level


_TITLE_HEAD_REGX = re.compile(r"^\s*#{1,6}\s*")

def analyse_toc(pages_path: Path, toc_path: Path, toc_assumed: bool) -> TocInfo:
    if toc_path.exists():
        return decode_toc(read_xml(toc_path))

    toc_path.parent.mkdir(parents=True, exist_ok=True)
    toc_info = _do_analyse_toc(pages_path, toc_assumed)
    save_xml(encode_toc(toc_info), toc_path)

    return toc_info

def _do_analyse_toc(pages_path: Path, toc_assumed: bool) -> TocInfo:
    pages: XMLReader[Page] = XMLReader(
        prefix="page",
        dir_path=pages_path,
        decode=decode_pdf,
    )
    ref2level: Ref2Level
    toc_page_indexes: list[int] = []
    toc_pages: list[PageRef] = []
    if toc_assumed:
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

    if toc_pages:
        ref2level = analyse_toc_levels(
            pages=pages,
            pages_path=pages_path,
            toc_pages=toc_pages,
        )
        toc_page_indexes.extend(ref.page_index for ref in toc_pages)
    else:
        ref2level = analyse_title_levels(pages)

    return TocInfo(
        content=_structure_toc_by_levels(ref2level),
        page_indexes=sorted(toc_page_indexes),
    )

def _structure_toc_by_levels(ref2level: Ref2Level) -> list[Toc]:
     # 虚拟根节点
    root = Toc(
        id=-1,
        page_index=-1,
        order=-1,
        level=-1,
        children=[],
    )
    next_id: int = 1
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
