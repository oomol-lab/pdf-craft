from typing import Generator
from ..sequence import AssetLayout, ParagraphLayout
from ..sequence.chapter import Reference


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
