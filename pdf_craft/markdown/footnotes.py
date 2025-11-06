from typing import Generator
from ..sequence import AssetLayout, ParagraphLayout
from ..sequence.chapter import Reference


def collect_chapter_references(chapter) -> Generator[Reference, None, None]:
    seen: set[tuple[int, int]] = set()
    if chapter.title is not None:
        for line in chapter.title.lines:
            for part in line.content:
                if isinstance(part, Reference):
                    ref_id = part.id
                    if ref_id not in seen:
                        seen.add(ref_id)
                        yield part
    for layout in chapter.layouts:
        if isinstance(layout, ParagraphLayout):
            for line in layout.lines:
                for part in line.content:
                    if isinstance(part, Reference):
                        ref_id = part.id
                        if ref_id not in seen:
                            seen.add(ref_id)
                            yield part

def create_reference_mapping(references: list[Reference]) -> dict[tuple[int, int], int]:
    ref_id_to_number = {}
    for i, ref in enumerate(references, 1):
        ref_id_to_number[ref.id] = i
    return ref_id_to_number


def render_footnotes_section(references: list[Reference]) -> Generator[str, None, None]:
    if not references:
        return
    yield "\n\n---\n\n## References\n\n"
    for i, ref in enumerate(references, 1):
        yield f"[^{i}]: "
        yield from _render_reference_content(ref)
        yield "\n\n"

def _render_reference_content(ref: Reference):
    first = True
    for layout in ref.layouts:
        if isinstance(layout, AssetLayout):
            if layout.content:
                if not first:
                    yield " "
                yield layout.content.strip()
                first = False
        elif isinstance(layout, ParagraphLayout):
            for line in layout.lines:
                for part in line.content:
                    if isinstance(part, str):
                        if not first:
                            yield " "
                        yield part
                        first = False
