import logging
import re
from pathlib import Path

from ..common import XMLReader, read_xml, save_xml
from ..llm import LLM
from ..pdf import TITLE_TAGS, Page
from ..pdf import decode as decode_pdf
from .llm_analyser import (
    LLMAnalysisError,
    analyse_title_levels_by_llm,
    analyse_toc_levels_by_llm,
)
from .toc_levels import Ref2Level, analyse_title_levels, analyse_toc_levels
from .toc_pages import PageRef, find_toc_pages
from .types import Toc, TocInfo
from .types import decode as decode_toc
from .types import encode as encode_toc

logger = logging.getLogger(__name__)

_TITLE_HEAD_REGX = re.compile(r"^\s*#{1,6}\s*")


def analyse_toc(
    pages_path: Path,
    toc_path: Path,
    toc_assumed: bool,
    toc_llm: LLM | None = None,
) -> TocInfo:
    if toc_path.exists():
        return decode_toc(read_xml(toc_path))

    toc_path.parent.mkdir(parents=True, exist_ok=True)
    toc_info = _do_analyse_toc(pages_path, toc_llm, toc_assumed)
    save_xml(encode_toc(toc_info), toc_path)

    return toc_info


def _do_analyse_toc(
    pages_path: Path,
    toc_llm: LLM | None,
    toc_assumed: bool,
) -> TocInfo:
    pages: XMLReader[Page] = XMLReader(
        prefix="page",
        dir_path=pages_path,
        decode=decode_pdf,
    )
    toc_pages: list[PageRef] = []
    if toc_assumed:
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

    ref2level: Ref2Level | None = None
    toc_page_indexes: list[int] = []

    if toc_pages:
        if toc_llm is not None:
            try:
                ref2level = analyse_toc_levels_by_llm(
                    llm=toc_llm,
                    toc_page_refs=toc_pages,
                    toc_page_contents=list(
                        pages.read(
                            page_indexes={
                                toc_page.page_index for toc_page in toc_pages
                            },
                        )
                    ),
                )
            except LLMAnalysisError as error:
                print(
                    f"LLM analysis toc failed, falling back to statistical method: {error}"
                )

        if ref2level is None:
            ref2level = analyse_toc_levels(
                pages=pages,
                pages_path=pages_path,
                toc_pages=toc_pages,
            )
        toc_page_indexes.extend(ref.page_index for ref in toc_pages)
        toc_page_indexes.sort()

    else:
        if toc_llm is not None:
            try:
                ref2level = analyse_title_levels_by_llm(toc_llm, pages)
            except LLMAnalysisError as error:
                print(
                    f"LLM analysis title failed, falling back to statistical method: {error}"
                )

        if ref2level is None:
            ref2level = analyse_title_levels(pages)

    return TocInfo(
        content=_structure_toc_by_levels(ref2level),
        page_indexes=toc_page_indexes,
    )


def _structure_toc_by_levels(ref2level: Ref2Level) -> list[Toc]:
    # virtual root
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
            break

        stack[-1].children.append(toc)
        stack.append(toc)

    return root.children
