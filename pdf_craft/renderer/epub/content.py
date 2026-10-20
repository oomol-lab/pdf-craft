"""Incremental PCEX to EPUB-record consumer; only packaging waits for EOF."""
import pickle
from typing import cast
from contextlib import aclosing

from epub_generator import Chapter as ChapterRecord, EpubData, generate_epub

from ...extractor.chapter.chapter import Chapter, Reference, TextFlowItem, FlowItem
from ...metering import check_aborted
from ...runtime import IO_DOMAIN, TEX_DOMAIN, run_cancellable
from ..paragraph import display_values
from .render import _convert_chapter_to_epub, _iter_text_in_title, _ReferenceNumbers
from .toc_collection import TocCollection
from .anchored import FloatMarkerRegistry, apply_float_markers


def load_records(path):
    elements, notes = [], {}
    with path.open('rb') as file:
        while True:
            try:
                record = pickle.load(file)
            except EOFError:
                break
            elements.extend(record.elements)
            for note in record.footnotes:
                if note.contents:
                    notes[note.id] = note
    return ChapterRecord(elements=elements, footnotes=list(notes.values()))


async def render_content(items, view, numbers, notify, directory, output, *, mode,
                         book_meta, language, table_render, latex_render, inline_latex, aborted):
    toc = await IO_DOMAIN.run(TocCollection, view.toc if view.toc.exists() else None)
    markers = FloatMarkerRegistry()
    chapters = {}
    references = _ReferenceNumbers(numbers)
    reference_filename = None

    def append(item):
        nonlocal reference_filename
        # Ordered content is chapter-contiguous. Only the active XHTML owns
        # occurrence counts; definition numbers remain document-wide.
        if item.filename != reference_filename:
            references.occurrences.clear()
            reference_filename = item.filename
        info = chapters.setdefault(item.filename, [item.chapter_id, directory / (item.filename + '.pickle'), '', 0])
        values = display_values(item, mode)
        if not values:
            return
        if isinstance(item.value, Reference):
            chapter = Chapter(item.chapter_id, item.level, [], cast(list[Reference], values))
        else:
            chapter = Chapter(item.chapter_id, item.level, cast(list[FlowItem], values))
            info[3] += 1
            if info[3] == 1 and isinstance(item.value, TextFlowItem) and item.value.role == 'heading':
                info[2] = ''.join(_iter_text_in_title(cast(TextFlowItem, values[0]))).strip() or 'Untitled'
        record = _convert_chapter_to_epub(chapter, view.assets, inline_latex, references, markers)
        with info[1].open('ab') as file:
            pickle.dump(record, file, protocol=pickle.HIGHEST_PROTOCOL)

    async with aclosing(notify.iterate(items)) as incoming:
        async for item in incoming:
            check_aborted(aborted)
            await IO_DOMAIN.run(append, item)
            await notify.append(item)
    head = None
    for chapter_id, path, title, count in chapters.values():
        if not path.exists():
            continue
        def get_chapter(path=path):
            return load_records(path)
        if chapter_id is None:
            head = get_chapter
        elif title:
            toc.collect(chapter_id, title, count > 1, get_chapter if count > 1 else None)
    cover = view.cover if await IO_DOMAIN.run(view.cover.exists) else None
    data = EpubData(meta=book_meta, get_head=head, chapters=toc.normalize().target, cover_image_path=cover)
    await run_cancellable(TEX_DOMAIN, lambda cancelled: generate_epub(
        epub_data=data, epub_file_path=output, lan=language, table_render=table_render,
        latex_render=latex_render, assert_not_aborted=lambda: check_aborted(lambda: cancelled() or aborted()),
    ), original_aborted=aborted)
    await IO_DOMAIN.run(apply_float_markers, output, markers.sides)
