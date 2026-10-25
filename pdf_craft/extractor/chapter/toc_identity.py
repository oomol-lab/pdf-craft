"""Coordinate structural TOC recovery without renumbering source identities."""
from dataclasses import replace
from pathlib import Path

from ...common import read_xml
from ...document.validation import ChapterTocMismatch
from ..toc import TocInfo, iter_toc


def chapter_ids(directory: Path) -> set[int]:
    """Read one chapter at a time; filenames are not authoritative identities."""
    identities = set()
    for path in directory.glob('chapter_*.xml'):
        identifier = read_xml(path).get('id')
        if identifier is not None:
            identities.add(int(identifier))
    return identities


def retained_toc(toc: TocInfo, identities: set[int], *, removed_pages: set[int] | None = None) -> TocInfo:
    """Remove unavailable anchors, lifting surviving children, never content.

    Callers must obtain an analysis decision before generic directory recovery.
    Page-image recovery only permits removal on the pages already degraded.
    """
    items = {item.id: item for item in iter_toc(toc.content)}
    missing = set(items) - identities
    unexpected = identities - set(items)
    if unexpected or (removed_pages is not None and any(items[key].page_index not in removed_pages for key in missing)):
        raise ChapterTocMismatch(tuple(sorted(missing)), tuple(sorted(unexpected)),
                                 tuple(sorted({items[key].page_index for key in missing})))

    def retain(values):
        result = []
        for item in values:
            children = retain(item.children)
            if item.id in identities:
                result.append(replace(item, children=children))
            else:
                result.extend(children)
        return result

    return replace(toc, content=retain(toc.content))
