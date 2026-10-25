"""Optional, blocking input assertions. Conversion never calls these functions."""
from os import PathLike
from pathlib import Path
from tempfile import TemporaryDirectory

from .pdf.poppler import PopplerDocument


def assert_pdf_readable(
    pdf_path: PathLike | str, *, poppler_path: PathLike | str | None = None,
) -> None:
    """Open with Poppler and read a positive page count, without rendering.

    Return None on success; propagate backend exceptions on failure. This does
    not guarantee successful page extraction, OCR, or translation.
    """
    document = PopplerDocument(Path(pdf_path), Path(poppler_path) if poppler_path is not None else None)
    try:
        _ = document.pages_count
    finally:
        document.close()


def assert_pdf_renderable(
    pdf_path: PathLike | str, *, poppler_path: PathLike | str | None = None,
) -> None:
    """Render every page with Poppler at 72 DPI, releasing each image in turn.

    Return None on success; propagate backend exceptions on failure. Higher-DPI
    rendering and later OCR or translation can still fail.
    """
    document = PopplerDocument(Path(pdf_path), Path(poppler_path) if poppler_path is not None else None)
    try:
        for index in range(1, document.pages_count + 1):
            with document.render_page(index, 72):
                pass
    finally:
        document.close()


def assert_pdf_editable(pdf_path: PathLike | str) -> None:
    """Read, clone, write and reopen with pypdf, leaving the source untouched.

    Return None on success; propagate backend exceptions on failure. Checks
    basic PDF editing, not every operation required for PDF text backfill.
    """
    from pypdf import PdfReader, PdfWriter

    with PdfReader(Path(pdf_path)) as reader:
        count = len(reader.pages)
        if count <= 0:
            raise ValueError("PDF has no pages")
        with TemporaryDirectory(prefix="pdf-craft-editable-") as directory:
            target = Path(directory) / "trial.pdf"
            writer = PdfWriter(clone_from=reader)
            try:
                writer.write(target)
            finally:
                writer.close()
            with PdfReader(target) as trial:
                if len(trial.pages) != count:
                    raise ValueError("Trial write changed PDF page count")


def assert_epub_readable(epub_path: PathLike | str) -> None:
    """Parse the EPUB inputs consumed by translation, without translating/writing.

    Return None on success; propagate input-parser exceptions on failure. This
    is a compatibility assertion, not full EPUB standards validation.
    """
    from .pipeline.epub.adapter.zip import ReadOnlyZip
    from .pipeline.epub.adapter import read_metadata, read_toc
    from .pipeline.epub.adapter.chapters import read_chapters

    with ReadOnlyZip(Path(epub_path)) as archive:
        with archive.read(Path("mimetype")) as file:
            file.read()
        read_toc(archive)
        read_metadata(archive)
        for _ in read_chapters(archive):
            pass
