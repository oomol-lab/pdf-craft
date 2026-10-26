# pylint: disable=protected-access
"""Blocking source-size queries. No model configuration or model calls required."""
from os import PathLike
from pathlib import Path

from .document import PDFCraftExtraction
from .transformer.xml_translator.xml_translator import SubmitKind


def pcex_words_count(source: PDFCraftExtraction | PathLike | str, *,
                     with_anchored: bool = True, with_furniture: bool = False) -> int:
    """Count original words selected by PCEX translation, including book metadata."""
    from .transformer.pcex_progress import scan
    extraction = source if isinstance(source, PDFCraftExtraction) else PDFCraftExtraction._open(Path(source))
    with extraction._materialize() as paths:
        _, total = scan(paths, metadata=extraction._document_metadata(),
                        with_anchored=with_anchored, with_furniture=with_furniture)
    return total


def epub_words_count(source: PathLike | str, *, submit: SubmitKind = SubmitKind.REPLACE) -> int:
    """Count the same source words as EPUB translation, without writing an output."""
    from .pipeline.epub.adapter.zip import ReadOnlyZip
    from .pipeline.epub.translation.translator import source_tasks, scan_tasks
    with ReadOnlyZip(Path(source)) as archive:
        _, _, total = scan_tasks(source_tasks(archive, submit))
    return total
