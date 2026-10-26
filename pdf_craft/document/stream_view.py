"""Select fixed PCEX context and a lazy source/replacement content stream."""
# pylint: disable=protected-access
from contextlib import asynccontextmanager, aclosing
from dataclasses import replace
import json

from .access import page_geometries
from .content import read_content
from .package import _read_pages, _read_translation_index
from .render import RenderMode, RenderView, _select_translation, _effective_metadata
from ..runtime import IO_DOMAIN, worker_context, temporary_directory


@asynccontextmanager
async def _content_view(extraction, *, mode=RenderMode.SOURCE, translation_id=None,
                       translator=None, window=1, with_anchored=True, with_furniture=False,
                       on_metadata=None, on_body=None, on_footnote=None, aborted=lambda: False):
    """No translated workspace: the consumer receives the producer directly."""
    if translator is not None and translation_id is not None:
        raise ValueError("translation_id cannot be combined with translator")
    if translator is None and mode == RenderMode.SOURCE and translation_id is not None:
        raise ValueError("translation_id requires a translated render mode")
    async with worker_context(extraction._materialize()) as paths, temporary_directory("pdf-craft-fixed-furniture-") as furniture_root:
        def fixed():
            from .content import chapter_paths, file_content
            from ..extractor.chapter.chapter import Reference
            metadata = json.loads(paths.manifest.read_text(encoding='utf-8'))['document']
            dpi, sizes = _read_pages(paths.pages)
            ids = sorted({item.value.id for path in chapter_paths(paths.chapters)
                          for item in file_content(path) if isinstance(item.value, Reference)})
            return metadata, page_geometries(sizes, dpi), {key: n for n, key in enumerate(ids, 1)}
        metadata, pages, numbers = await IO_DOMAIN.run(fixed)
        selected = paths.chapters
        translated = False
        coverage = None
        if translator is None and mode != RenderMode.SOURCE:
            info = await IO_DOMAIN.run(lambda: _select_translation(
                _read_translation_index(paths.translations), translation_id))
            layer = paths.translations / info.id
            overlay = await IO_DOMAIN.run(lambda: json.loads((layer / 'metadata.json').read_text(encoding='utf-8')))
            metadata = _effective_metadata(metadata, overlay, mode)
            from ..transformer.translation_coverage import read_coverage
            coverage = await IO_DOMAIN.run(read_coverage, layer / 'coverage.xml')
            selected = layer / 'chapters'
            translated = True
        view = RenderView(selected, paths.assets, paths.toc, paths.cover,
                          metadata, metadata.get('language'), None)

        async def selected_items():
            if not translated:
                async for item in read_content(selected):
                    yield item
            else:
                originals = read_content(paths.chapters)
                try:
                    async for item in read_content(selected):
                        original = await anext(originals)
                        if (item.filename, item.index) != (original.filename, original.index):
                            raise ValueError('translation content does not align with source')
                        chapter_id = str(item.chapter_id) if item.chapter_id is not None else 'head'
                        assert coverage is not None
                        children = frozenset(child for (chapter, index, child), state in coverage.anchored.items()
                                             if chapter == chapter_id and index == item.index and state == 'translated')
                        selected_item = replace(item, original=original.value, asset_context=original.asset_context,
                                                narrative_states=coverage.narrative, asset_children=children)
                        yield selected_item
                finally:
                    await originals.aclose()

        items = selected_items()
        if translator is not None:
            from ..transformer.narrative_xml import NarrativeXMLTransformer
            transformer = NarrativeXMLTransformer(translator, window=window)
            if not transformer.supports_stream:
                overlay = await transformer.translate_metadata(metadata, on_metadata=on_metadata)
                view = replace(view, metadata=_effective_metadata(metadata, overlay, RenderMode.REPLACE if mode == RenderMode.SOURCE else mode),
                               language=overlay.get("language", transformer.target_language) or view.language)
                # Explicit whole-element extension contract; standard XMLTranslator
                # uses the streaming branch below.
                from ..transformer.package import ChapterExtractionTransformer, AnchoredContentExtractionTransformer
                from .package import PDFCraftExtraction
                legacy = await ChapterExtractionTransformer(transformer)._transform_to_workspace_async(
                    PDFCraftExtraction._from_workspace(paths.root), furniture_root / 'legacy')
                if with_anchored:
                    legacy = await AnchoredContentExtractionTransformer(transformer._anchored_transformer())._transform_to_workspace_async(
                        legacy, furniture_root / 'anchored')
                selected = (furniture_root / ('anchored' if with_anchored else 'legacy')) / 'chapters'
                from ..transformer.translation_coverage import read_coverage
                coverage = await IO_DOMAIN.run(read_coverage, selected.parent / 'translation.xml')
                translated = True
                items = selected_items()
            else:
                from ..transformer.pcex_schedule import pcex_schedule
                async def translate_metadata():
                    return await transformer.translate_metadata(metadata, on_metadata=on_metadata)
                from ..renderer.paragraph import paragraph_notifications
                async with paragraph_notifications(pages, paths.assets, numbers, on_body, on_footnote) as notify:
                    async with pcex_schedule(items, transformer, window, paths=paths, root=furniture_root,
                                             with_furniture=with_furniture, with_anchored=with_anchored,
                                             aborted=aborted, pages=pages, metadata=translate_metadata, notifier=notify) as schedule:
                        overlay = await schedule.header()
                        view = replace(view, metadata=_effective_metadata(metadata, overlay, RenderMode.REPLACE if mode == RenderMode.SOURCE else mode),
                                       language=overlay.get('language', transformer.target_language) or view.language)
                        if with_furniture:
                            assert schedule.furniture is not None
                            view = replace(view, furnitures=schedule.furniture.path,
                                           furniture_coverage=schedule.furniture.coverage)
                        items = schedule.results([], [])
                        async with aclosing(items):
                            yield view, pages, numbers, items
                return
        async with aclosing(items):
            yield view, pages, numbers, items


@asynccontextmanager
async def content_view(extraction, **kwargs):
    """Keep a single progress scope around preprocessing and lazy consumption."""
    from ..transformer.pcex_progress import pcex_progress
    from ..transformer.narrative_xml import NarrativeXMLTransformer
    callback = kwargs.pop('on_translation_event', None)
    translator = kwargs.get('translator')
    if translator is None:
        async with _content_view(extraction, **kwargs) as view:
            yield view
    else:
        metadata = await IO_DOMAIN.run(extraction._document_metadata)
        async with pcex_progress(extraction, NarrativeXMLTransformer(translator),
                                 callback, metadata=metadata,
                                 with_anchored=kwargs.get('with_anchored', True),
                                 with_furniture=kwargs.get('with_furniture', False)):
            async with _content_view(extraction, **kwargs) as view:
                yield view
