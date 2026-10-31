"""PCEX adapter over the shared translation engine, yielding existing items."""
# pylint: disable=protected-access
from copy import deepcopy

from ..concurrency import fatal_error_boundary
from ..common import read_xml, save_xml
from ..document.content import read_content
from ..extractor.chapter.chapter import (
    Chapter, Reference, search_references_in_chapter,
)
from ..runtime import IO_DOMAIN
from .translation_coverage import (
    write_anchored_coverage, write_narrative_coverage,
)


def _unit(item):
    if isinstance(item.value, Reference):
        return Chapter(item.chapter_id, item.level, [], [deepcopy(item.value)])
    chapter = Chapter(item.chapter_id, item.level, [deepcopy(item.value)])
    # Reference occurrences carry identity, not another independently translated
    # copy of their definitions. Definitions have their own stream entries.
    for reference in search_references_in_chapter(chapter):
        reference.flow_items = []
        reference.mark = ''  # Immutable display metadata, not another translation/billing field.
    return chapter


@fatal_error_boundary
async def translate_content(items, transformer, window, coverage, anchored_coverage,
                            *, with_anchored=False, aborted=lambda: False, on_completed=None, pages=None):
    """One format adapter; every LLM operation remains in XMLTranslator."""
    from .pcex_schedule import pcex_schedule
    async with pcex_schedule(items, transformer, window, with_anchored=with_anchored,
                             aborted=aborted, pages=pages) as schedule:
        results = schedule.results(coverage, anchored_coverage, on_completed)
        try:
            async for item in results:
                yield item
        finally:
            await results.aclose()


async def _translate_workspace(extraction, output, transformer, *, window=1,
                              with_anchored=False, on_body=None, on_footnote=None,
                              with_furniture=False, metadata=None, metadata_result=None, aborted=lambda: False, preserve_layers=False, toc_transformer=None):
    """The translation-layer writer and renderers share this stream adapter."""
    from .package import _copy_extraction_to_workspace
    from ..document import PDFCraftExtraction

    def prepare():
        extraction._validate()
        output.mkdir(parents=True, exist_ok=False)
        _copy_extraction_to_workspace(extraction, output, include_translations=preserve_layers)
        if not preserve_layers:
            # This is a replacement-only internal view, not a new source for
            # previously stored layers. Append uses the untouched input later.
            from xml.etree.ElementTree import Element
            save_xml(Element("translation"), output / "translation.xml")
    await IO_DOMAIN.run(prepare)
    coverage, assets = [], []
    # Source entries are decoded once, then completed content is persisted by
    # the same consumer used for non-translated rendering.
    from ..document.content import ChapterWriter
    from ..document.package import _read_pages
    from ..document.access import page_geometries
    from ..renderer.paragraph import paragraph_notifications
    from ..document.content import chapter_paths, file_content
    def fixed():
        dpi, sizes = _read_pages(output / 'pages.xml')
        identities = sorted({item.value.id for path in chapter_paths(output / 'chapters')
                             for item in file_content(path) if isinstance(item.value, Reference)})
        return page_geometries(sizes, dpi), {identity: number for number, identity in enumerate(identities, 1)}
    pages, numbers = await IO_DOMAIN.run(fixed)
    writer = ChapterWriter(output / 'chapters')
    from .pcex_schedule import pcex_schedule
    from ..document import ExtractionPaths
    paths = ExtractionPaths.at(output)
    from contextlib import aclosing
    async with paragraph_notifications(pages, output / 'assets', numbers, on_body, on_footnote) as notify:
        async with pcex_schedule(read_content(output / 'chapters'), transformer, window,
                                 paths=paths, root=output, with_furniture=with_furniture,
                                 with_anchored=with_anchored, aborted=aborted, pages=pages,
                                 metadata=metadata, notifier=notify) as schedule:
            items = schedule.results(coverage, assets)
            async with aclosing(notify.iterate(items)) as incoming:
                async for item in incoming:
                    await IO_DOMAIN.run(writer.append, item)
            await IO_DOMAIN.run(writer.finish)
            overlay = await schedule.header()
            if metadata_result is not None:
                metadata_result.update(overlay)
    if with_furniture and await IO_DOMAIN.run((output / 'coverage.xml').exists):
        from .translation_coverage import copy_furniture_coverage
        await IO_DOMAIN.run(copy_furniture_coverage, output / 'coverage.xml', output / 'translation.xml')
        await IO_DOMAIN.run((output / 'coverage.xml').unlink)
    await IO_DOMAIN.run(write_narrative_coverage, output / "translation.xml", coverage)
    if assets:
        await IO_DOMAIN.run(write_anchored_coverage, output / "translation.xml", assets)
    toc_path = output / "toc.xml"
    if toc_transformer is not None and await IO_DOMAIN.run(toc_path.exists):
        toc = await IO_DOMAIN.run(read_xml, toc_path)
        transformed_toc = await toc_transformer(toc)
        await IO_DOMAIN.run(save_xml, transformed_toc, toc_path)
    return await IO_DOMAIN.run(PDFCraftExtraction._from_workspace(output)._validate)


async def translate_workspace(extraction, output, transformer, **kwargs):
    """Report the standalone chapter transformation using operation accounting."""
    from .pcex_progress import pcex_progress
    callback = kwargs.get('on_translation_event') if kwargs.get('emit_translation_events') else None
    kwargs.pop('on_translation_event', None)
    kwargs.pop('emit_translation_events', None)
    async with pcex_progress(extraction, transformer, callback,
                             with_anchored=kwargs.get('with_anchored', False)):
        return await _translate_workspace(extraction, output, transformer, **kwargs)
