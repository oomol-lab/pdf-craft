"""PCEX adapter over the shared translation engine, yielding existing items."""
# pylint: disable=protected-access
from copy import deepcopy
from dataclasses import replace

from ..concurrency import fatal_error_boundary
from ..common import read_xml, save_xml
from ..document.content import read_content
from ..extractor.chapter.chapter import (
    Chapter, Reference, TextFlowItem, narrative_texts, search_references_in_chapter,
)
from ..runtime import IO_DOMAIN, invoke_callback
from .anchored_translation import _asset_slots, _translate_slots_async
from .events import TranslationEvent, TranslationEventKind
from .translation_coverage import (
    AnchoredContentCoverage, NarrativeCoverage, paragraph_identity,
    write_anchored_coverage, write_narrative_coverage, accepted_translation,
)
from .xml_translator.xml_translator.concurrency import run_async_parameters


def _unit(item):
    if isinstance(item.value, Reference):
        return Chapter(item.chapter_id, item.level, [], [deepcopy(item.value)])
    chapter = Chapter(item.chapter_id, item.level, [deepcopy(item.value)])
    # Reference occurrences carry identity, not another independently translated
    # copy of their definitions. Definitions have their own stream entries.
    for reference in search_references_in_chapter(chapter):
        reference.flow_items = []
    return chapter


@fatal_error_boundary
async def translate_content(items, transformer, window, coverage, anchored_coverage,
                            *, with_anchored=False, aborted=lambda: False, on_completed=None, pretranslated=None, pages=None):
    """One format adapter; every LLM operation remains in XMLTranslator."""
    from ..metering import check_aborted

    async def translate(item):
        check_aborted(aborted)
        if item.value is None:
            if on_completed is not None:
                await on_completed(item)
            return item, (), ()
        source = _unit(item)
        path = (pretranslated.get((item.filename, item.index))
                if pretranslated is not None and isinstance(item.value, TextFlowItem)
                and item.value.role == 'heading' else None)
        if path is None:
            from .._failure_sources import page_sources
            with page_sources(pages or {}):
                target = await transformer.transform(source, emit_scope_events=False, emit_item_events=False)
        else:
            import pickle
            def load():
                with path.open('rb') as file:
                    return pickle.load(file)
            target = Chapter(item.chapter_id, item.level, [await IO_DOMAIN.run(load)])
        states = []
        source_texts = list(narrative_texts(source))
        target_texts = list(narrative_texts(target))
        for original, translated in zip(source_texts, target_texts, strict=True):
            identity = paragraph_identity(source, original)
            if identity is not None:
                from .package import _has_visible_content
                states.append(NarrativeCoverage(*identity, "translated" if _has_visible_content(translated) and accepted_translation(translated) else "preserved"))
        asset_states = []
        if with_anchored:
            slots = list(_asset_slots(target))
            for slot in slots:
                slot.payload = replace(slot.payload, flow_index=item.index, context=item.asset_context)
            from .._failure_sources import page_sources
            with page_sources(pages or {}):
                translations = await _translate_slots_async(slots, transformer._anchored_transformer())
            for slot, translation in zip(slots, translations, strict=True):
                state = "preserved"
                if translation is not None:
                    slot.asset.title = translation.title
                    slot.asset.content = translation.content
                    slot.asset.caption = translation.caption
                    state = "translated"
                asset_states.append(AnchoredContentCoverage(*slot.payload.identity, state))
        value = target.definitions[0] if isinstance(item.value, Reference) else target.flow_items[0]
        if isinstance(value, Reference):
            assert isinstance(item.value, Reference)
            value.mark = item.value.mark
        if on_completed is not None:
            await on_completed(item)
        return replace(item, value=value, original=item.value), states, asset_states

    from .._policy import Completion
    completion = Completion()
    results = run_async_parameters(items, translate, window)
    try:
        async for item, states, asset_states in results:
            coverage.extend(states)
            anchored_coverage.extend(asset_states)
            # Main narrative work is independent of metadata and asset success.
            primary = isinstance(item.original, Reference) or (isinstance(item.original, TextFlowItem) and item.original.role != 'heading')
            if primary:
                completion.succeeded += sum(state.state == 'translated' for state in states)
                completion.degraded += sum(state.state == 'preserved' for state in states)
            yield replace(item, narrative_states={
                (state.chapter_id, state.page_index, state.order): state.state for state in states
            }, asset_children=frozenset(state.child_index for state in asset_states if state.state == 'translated'))
        completion.check('translation')
    finally:
        await results.aclose()


async def translate_workspace(extraction, output, transformer, *, window=1,
                              with_anchored=False, on_body=None, on_footnote=None,
                              on_translation_event=None, emit_translation_events=False,
                              aborted=lambda: False, preserve_layers=False, toc_transformer=None):
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
    counts, characters, identifiers = {}, {}, {}
    completed = 0
    total = 0
    report = emit_translation_events and on_translation_event is not None
    if report:
        from ..extractor.chapter.reader import create_chapters_reader
        from ..runtime import worker_iter
        reader = await IO_DOMAIN.run(create_chapters_reader, output / "chapters")
        async for chapter in worker_iter(reader()):
            from ..document.content import chapter_filename
            filename = chapter_filename(chapter)
            if not transformer.has_translatable_content(chapter):
                continue
            counts[filename] = 1 + len(chapter.flow_items) + sum(1 for _ in search_references_in_chapter(chapter))
            characters[filename] = transformer.source_character_count(chapter)
            identifiers[filename] = chapter.id if chapter.id is not None else "head"
        total = sum(characters.values())
        await invoke_callback(on_translation_event, TranslationEvent(
            kind=TranslationEventKind.START, chapter_count=len(counts),
            has_toc=False, has_metadata=False, total_characters=total, completed_characters=0,
        ))
        from .events import TranslationItemKind
        for filename, item_id in identifiers.items():
            await invoke_callback(on_translation_event, TranslationEvent(
                kind=TranslationEventKind.ITEM_START, item_kind=TranslationItemKind.CHAPTER,
                item_id=item_id, item_completed_characters=0,
                item_total_characters=characters[filename], total_characters=total,
                completed_characters=0,
            ))
    import asyncio
    report_lock = asyncio.Lock()

    async def on_completed(item):
        nonlocal completed
        if not report or item.filename not in counts:
            return
        async with report_lock:
            counts[item.filename] -= 1
            if counts[item.filename] == 0:
                completed += characters[item.filename]
                from .events import TranslationItemKind
                for kind in (TranslationEventKind.PROGRESS, TranslationEventKind.ITEM_COMPLETE):
                    await invoke_callback(on_translation_event, TranslationEvent(
                        kind=kind, item_kind=TranslationItemKind.CHAPTER,
                        item_id=identifiers[item.filename],
                        item_completed_characters=characters[item.filename],
                        item_total_characters=characters[item.filename],
                        total_characters=total, completed_characters=completed,
                    ))
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
    items = translate_content(read_content(output / 'chapters'), transformer, window, coverage, assets,
                              with_anchored=with_anchored, on_completed=on_completed, aborted=aborted, pages=pages)
    from contextlib import aclosing
    async with paragraph_notifications(pages, output / 'assets', numbers, on_body, on_footnote) as notify:
        async with aclosing(notify.iterate(items)) as incoming:
            async for item in incoming:
                await IO_DOMAIN.run(writer.append, item)
                await notify.append(item)
        await IO_DOMAIN.run(writer.finish)
    await IO_DOMAIN.run(write_narrative_coverage, output / "translation.xml", coverage)
    if assets:
        await IO_DOMAIN.run(write_anchored_coverage, output / "translation.xml", assets)
    toc_path = output / "toc.xml"
    if toc_transformer is not None and await IO_DOMAIN.run(toc_path.exists):
        toc = await IO_DOMAIN.run(read_xml, toc_path)
        transformed_toc = await toc_transformer(toc)
        await IO_DOMAIN.run(save_xml, transformed_toc, toc_path)
    if emit_translation_events and on_translation_event is not None:
        await invoke_callback(on_translation_event, TranslationEvent(
            kind=TranslationEventKind.COMPLETE, total_characters=total,
            completed_characters=completed,
        ))
    return await IO_DOMAIN.run(PDFCraftExtraction._from_workspace(output)._validate)
