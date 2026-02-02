import logging
import re
from enum import Enum, auto
from pathlib import Path

from ..common import XMLReader, read_xml, save_xml
from ..llm import LLM
from ..pdf import TITLE_TAGS, Page
from ..pdf import decode as decode_pdf
from .toc_levels import Ref2Level, analyse_title_levels, analyse_toc_levels
from .toc_pages import PageRef, find_toc_pages
from .types import Toc, TocInfo
from .types import decode as decode_toc
from .types import encode as encode_toc

logger = logging.getLogger(__name__)

_TITLE_HEAD_REGX = re.compile(r"^\s*#{1,6}\s*")


class TocExtractionMode(Enum):
    NO_TOC_PAGE = auto()  # 不检测目录页，从正文标题提取
    AUTO_DETECT = auto()  # 检测目录页并用统计学分析
    LLM_ENHANCED = auto()  # 检测目录页并用 LLM 分析


def analyse_toc(
    pages_path: Path,
    toc_path: Path,
    mode: TocExtractionMode,
    llm: LLM | None = None,
) -> TocInfo:
    if toc_path.exists():
        return decode_toc(read_xml(toc_path))

    toc_path.parent.mkdir(parents=True, exist_ok=True)
    toc_info = _do_analyse_toc(pages_path, mode, llm)
    save_xml(encode_toc(toc_info), toc_path)

    return toc_info


def _do_analyse_toc(
    pages_path: Path,
    mode: TocExtractionMode,
    llm: LLM | None,
) -> TocInfo:
    pages: XMLReader[Page] = XMLReader(
        prefix="page",
        dir_path=pages_path,
        decode=decode_pdf,
    )
    ref2level: Ref2Level
    toc_page_indexes: list[int] = []
    toc_pages: list[PageRef] = []

    # Try to find TOC pages if mode is not NO_TOC_PAGE
    if mode != TocExtractionMode.NO_TOC_PAGE:
        toc_pages = find_toc_pages(
            iter_titles=lambda: (
                list(
                    (layout.order, _TITLE_HEAD_REGX.sub("", layout.text))
                    for layout in page.body_layouts
                    if layout.ref in TITLE_TAGS
                )
                for page in pages.read()
            ),
            iter_page_bodies=lambda: (
                "".join(layout.text for layout in page.body_layouts)
                for page in pages.read()
            ),
        )

    # Analyze TOC hierarchy based on mode
    if toc_pages and mode == TocExtractionMode.AUTO_DETECT:
        ref2level = analyse_toc_levels(
            pages=pages,
            pages_path=pages_path,
            toc_pages=toc_pages,
        )
        toc_page_indexes.extend(ref.page_index for ref in toc_pages)

    elif toc_pages and mode == TocExtractionMode.LLM_ENHANCED:
        if llm is None:
            raise ValueError("LLM instance is required for LLM_ENHANCED mode")

        from .toc_levels_by_llm import LLMAnalysisError, analyse_toc_levels_by_llm

        try:
            ref2level = analyse_toc_levels_by_llm(
                llm=llm,
                toc_page_refs=toc_pages,
                toc_page_contents=list(pages.read(
                    page_indexes={toc_page.page_index for toc_page in toc_pages},
                )),
            )
            toc_page_indexes.extend(ref.page_index for ref in toc_pages)

        except LLMAnalysisError as e:
            print(f"LLM analysis failed, falling back to statistical method: {e}")
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
            break  # 防御性

        stack[-1].children.append(toc)
        stack.append(toc)

    return root.children
