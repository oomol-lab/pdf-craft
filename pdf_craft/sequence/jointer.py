import re
from dataclasses import dataclass
from typing import Iterable

from ..pdf import PageLayout
from ..asset import ASSET_TAGS, AssetRef


_ASSET_CPATION_TAGS = tuple(f"{t}_caption" for t in ASSET_TAGS)

_LATEX_PATTERNS = [
    (re.compile(r"\\\["), re.compile(r"\\\]")),  # \[...\]
    (re.compile(r"\$\$"), re.compile(r"\$\$")),  # $$...$$
    (re.compile(r"\\\("), re.compile(r"\\\)")),  # \(...\)
    (re.compile(r"\$"), re.compile(r"\$")),      # $...$
]

_TABLE_PATTERN = re.compile(r"<table[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)

@dataclass
class AssetLayout:
    ref: AssetRef
    det: tuple[int, int, int, int]
    title: str | None
    content: str
    caption: str | None
    hash: str | None

class Jointer:
    def __init__(self) -> None:
        pass

    def execute(self, layouts_in_page: Iterable[tuple[int, list[PageLayout]]]):
        for page_index, layouts in layouts_in_page:
            pass

    def _join_asset_layouts(self, layouts: list[PageLayout]):
        last_asset: AssetLayout | None = None
        jointed_layouts: list[PageLayout | AssetLayout] = []
        for layout in layouts:
            if layout.ref in ASSET_TAGS:
                if last_asset:
                    jointed_layouts.append(last_asset)
                last_asset = AssetLayout(
                    ref=layout.ref,
                    det=layout.det,
                    title=None,
                    content=layout.text,
                    caption=None,
                    hash=layout.hash,
                )
            elif layout.ref in _ASSET_CPATION_TAGS:
                if last_asset:
                    if last_asset.caption:
                        last_asset.caption += "\n" + layout.text
                    else:
                        last_asset.caption = layout.text
            else:
                if last_asset:
                    jointed_layouts.append(last_asset)
                    last_asset = None
                jointed_layouts.append(layout)

        for layout in jointed_layouts:
            if isinstance(layout, AssetLayout):
                if layout.ref == "equation":
                    _normalize_equation(layout)
                if layout.ref == "table":
                    _normalize_table(layout)
        return jointed_layouts


def _normalize_equation(layout: AssetLayout):
    content = layout.content
    if not content:
        return
    latex_start = -1
    latex_end = -1

    for start_pat, end_pat in _LATEX_PATTERNS:
        start_match = start_pat.search(content)
        if start_match:
            start_idx = start_match.start()
            end_match = end_pat.search(content[start_match.end():])
            if end_match:
                latex_start = start_idx
                latex_end = start_match.end() + end_match.end()
                break

    if latex_start >= 0 and latex_end > latex_start:
        _extract_and_split_content(layout, latex_start, latex_end)

def _normalize_table(layout: AssetLayout):
    content = layout.content
    if not content:
        return
    table_match = _TABLE_PATTERN.search(content)

    if table_match:
        table_start = table_match.start()
        table_end = table_match.end()
        _extract_and_split_content(layout, table_start, table_end)

def _extract_and_split_content(layout: AssetLayout, start: int, end: int):
    content = layout.content
    extracted = content[start:end]
    before = content[:start].strip()

    if before:
        if layout.title:
            layout.title = layout.title + "\n" + before
        else:
            layout.title = before

    after = content[end:].strip()
    if after:
        if layout.caption:
            layout.caption = layout.caption + "\n" + after
        else:
            layout.caption = after

    layout.content = extracted