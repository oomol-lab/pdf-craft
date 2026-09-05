"""Small valid PDFCraftExtraction fixtures used by boundary tests."""

# pylint: disable=protected-access

from pathlib import Path

from epub_generator import BookMeta

from pdf_craft.common import save_xml
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.document.package import write_manifest, write_pages
from pdf_craft.extractor.toc.types import TocInfo, encode as encode_toc


def make_extraction(
    root: Path,
    *,
    page_pixel_sizes: dict[int, tuple[int, int]] | None = None,
    render_dpi: int = 300,
    with_toc: bool = False,
    book_meta: BookMeta | None = None,
    language: str | None = None,
) -> PDFCraftExtraction:
    """Create a directory-backed fixture through the private workspace path."""
    (root / "chapters").mkdir(parents=True)
    (root / "assets").mkdir()
    if with_toc:
        save_xml(encode_toc(TocInfo(content=[], page_indexes=[])), root / "toc.xml")
    write_pages(
        root,
        render_dpi=render_dpi,
        page_pixel_sizes=page_pixel_sizes or {1: (100, 100)},
    )
    write_manifest(root, book_meta=book_meta, language=language)
    return PDFCraftExtraction._from_workspace(root)
