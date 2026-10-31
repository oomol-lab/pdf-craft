"""Materialize explicitly identified non-linear OCR text as source artwork."""
from dataclasses import replace

from ..common import AssetHub
from .chapter.page_analysis import _AnalysedAssetSource


def preserve_graphics(page, pdf, handler, pixel_size, assets, dpi, aborted):
    selected = [layout for layout in page.layouts if layout.annotations.preserve_as_image]
    if not selected:
        return page
    aborted()
    document = handler.open(pdf)
    try:
        image = document.render_page(page.page_index, dpi)
    finally:
        document.close()
    try:
        if image.size != pixel_size:
            resized = image.resize(pixel_size)
            image.close()
            image = resized
        hub = AssetHub(assets)
        replacements = {}
        for layout in selected:
            aborted()
            source = layout.source
            replacements[layout.document_order] = replace(layout, source=_AnalysedAssetSource(
                page.page_index, 'image', source.bbox, (), (), (), hub.clip(image, source.bbox),
                source.reading_order, source.origin), annotations=replace(
                    layout.annotations, continues_from_previous=False, continues_to_next=False,
                    paragraph_role=None, paragraph_level=None, references=[], preserve_as_image=False))
        return replace(page, layouts=[replacements.get(item.document_order, item) for item in page.layouts])
    finally:
        image.close()
