"""Capture detached public provenance at format adapters, before model work."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from ._policy import current, source_locations
from .policies import FailureLocation
from .document.paragraph import SourceRegion

_pages: ContextVar[dict] = ContextVar("pdf_craft_source_pages", default={})


@contextmanager
def page_sources(pages: dict) -> Iterator[None]:
    token = _pages.set(pages)
    try:
        yield
    finally:
        _pages.reset(token)


def chapter_locations(chapter) -> tuple[FailureLocation, ...]:
    from .extractor.chapter.chapter import narrative_texts, SourceTextFragment
    from .extractor.chapter.text_projection import iter_continuous_content
    from .markdown.paragraph import flatten

    result = []
    pages = _pages.get()
    for item in narrative_texts(chapter):
        fragments = [
            part for part in item.children if isinstance(part, SourceTextFragment)
        ]
        if not fragments:
            continue
        regions = tuple(
            SourceRegion(pages[part.page_index], part.source_order, part.bbox)
            for part in fragments
            if part.page_index in pages
        )
        first = fragments[0]
        channel = (
            "footnote"
            if any(
                item is text for ref in chapter.definitions for text in ref.flow_items
            )
            else "body"
        )
        result.append(
            FailureLocation(
                "regions",
                source=current().source,
                member_path=f"chapters/chapter_{chapter.id if chapter.id is not None else 'head'}.xml",
                unit_id=f"{channel}:{first.page_index}:{first.source_order}",
                channel=channel,
                page_indexes=tuple(
                    dict.fromkeys(part.page_index for part in fragments)
                ),
                regions=regions,
                original="".join(
                    part if isinstance(part, str) else str(getattr(part, "content", ""))
                    for part in flatten(list(iter_continuous_content(item)))
                ),
            )
        )
    return tuple(result)


def element_locations(element) -> tuple[FailureLocation, ...]:
    """Provenance is data, never executable pickle from an input document."""
    import json
    from .document.access import PageGeometry

    encoded = element.get("__pdf_craft_failure_sources")
    if not encoded:
        return ()
    result = []
    for raw in json.loads(encoded):
        regions = tuple(
            SourceRegion(
                PageGeometry(**region["page"]),
                region["source_order_in_page"],
                tuple(region["bbox"]),
            )
            for region in raw.pop("regions", [])
        )
        raw["page_indexes"] = tuple(raw.get("page_indexes", []))
        if raw.get("page") is not None:
            raw["page"] = PageGeometry(**raw["page"])
        if raw.get("bbox") is not None:
            raw["bbox"] = tuple(raw["bbox"])
        result.append(FailureLocation(**raw, regions=regions))
    return tuple(result)


def tag_locations(element, values) -> None:
    import json
    from dataclasses import asdict

    if values:
        element.set(
            "__pdf_craft_failure_sources",
            json.dumps([asdict(value) for value in values]),
        )


def segment_locations(segments) -> tuple[FailureLocation, ...]:
    values = []
    for segment in segments:
        root = segment.head.root
        candidates = next(
            (
                element_locations(parent)
                for parent in reversed(segment.head.parent_stack)
                if element_locations(parent)
            ),
            element_locations(root),
        )
        owner = next(
            (parent for parent in segment.head.parent_stack if parent.tag == "text"),
            None,
        )
        if owner is not None:
            fragment = owner.find("fragment")
            if fragment is not None:
                suffix = f":{fragment.get('page_index')}:{fragment.get('source_order')}"
                candidates = (
                    tuple(
                        value
                        for value in candidates
                        if value.unit_id and value.unit_id.endswith(suffix)
                    )
                    or candidates
                )
        for value in candidates:
            if value not in values:
                values.append(value)
    return tuple(values)


def analysis_location(page) -> FailureLocation:
    pages = _pages.get()
    geometry = pages.get(page.page_index)
    regions = tuple(
        SourceRegion(
            geometry,
            layout.source_order
            if layout.source_order is not None
            else layout.document_order,
            layout.bbox,
        )
        for layout in page.layouts
        if geometry is not None and layout.source_order is not None
    )
    return FailureLocation(
        "regions" if regions else "page",
        source=current().source,
        page_indexes=(page.page_index,),
        regions=regions,
    )


def ocr_locations(path, limit=None) -> tuple[FailureLocation, ...]:
    """Failure of a book-wide request has a book-wide operand, not a guessed box."""
    import re

    numbered = sorted(
        (int(match.group(1)), item)
        for item in path.glob("page_*.xml")
        if (match := re.fullmatch(r"page_(\d+)\.xml", item.name))
    )
    return tuple(
        FailureLocation("page", source=current().source, page_indexes=(index,))
        for index, _ in numbered[:limit]
    )


def task_locations(task) -> tuple[FailureLocation, ...]:
    """Adapt non-narrative public identities without exposing private payloads."""
    from .transformer.furniture import FurniturePosition
    from .transformer.anchored_content import AnchoredContent

    payload = task.payload
    pages = _pages.get()
    if source_locations():
        return source_locations()
    if task.source_locations:
        return task.source_locations
    if isinstance(payload, FurniturePosition):
        return (
            FailureLocation(
                "template",
                source=current().source,
                member_path="furnitures.xml",
                unit_id=f"{payload.pattern_id}:{payload.position_id}",
                original=payload.content,
            ),
        )
    if (
        isinstance(payload, tuple)
        and payload
        and isinstance(payload[0], AnchoredContent)
    ):
        result = []
        for item in payload:
            page = pages.get(item.asset.page_index)
            regions = ()
            result.append(
                FailureLocation(
                    "regions",
                    source=current().source,
                    member_path=f"chapters/chapter_{item.chapter_id}.xml",
                    unit_id=str(item.identity),
                    channel="asset",
                    page_indexes=(item.asset.page_index,),
                    regions=regions,
                    field_path="title/content/caption",
                    page=page,
                    bbox=item.asset.bbox,
                )
            )
        return tuple(result)
    page = getattr(payload, "page_index", None)
    return (
        FailureLocation(
            "page" if page is not None else "document",
            source=current().source,
            unit_id=str(task.item_id) if task.item_id is not None else None,
            page_indexes=(page,) if page is not None else (),
        ),
    )
