"""The PDF consumer of the shared PCEX content stream."""
# pylint: disable=protected-access
import asyncio
from dataclasses import replace
from pathlib import Path
from ...concurrency import fatal_error_boundary, settle_tasks
from ...policies import ErrorPolicyInput
from ..._policy import OMITTED
from ...extractor.chapter.text_projection import iter_continuous_content

from ...document.stream_view import content_view
from ...document.render import RenderMode, materialize_pdf_patch_view
from ...extractor.chapter.chapter import TextFlowItem, SourceTextFragment, Reference
from ...runtime import IO_DOMAIN, temporary_directory, worker_context
from ...transformer.translation_coverage import read_coverage, paragraph_identity
from ...renderer.paragraph import paragraph_notifications, display_values
from ...renderer.output import publish
from ...document.paragraph import PDFParagraphContent, PDFSourceRegion
from .feed import _PreviewReplacement, ReplacementFeed, write_record, read_record, dependency_windows
from .streaming import source_furniture_coverage, produce_furniture, deliver_previews, update_metadata
from .pipeline import PDFTranslationPipeline, _to_pdf_patch_content, _replacement_source_order
from .patcher import PDFPatcher
from .metadata import pdf_document_metadata


def source_key(text):
    return tuple((fragment.page_index, fragment.source_order, fragment.bbox)
                 for fragment in text.children if isinstance(fragment, SourceTextFragment))


def prepare_index(chapters, furniture, coverage, pages, dpi, directory, translating):
    """Index geometry/identity only; no chapter-wide text graph is retained."""
    from ...document.content import chapter_paths, file_content
    from ...extractor.chapter.chapter import Chapter
    from .pipeline import _regions_for_fragments, _chapter_obstacle_regions
    from .models import PDFReplacement
    entries, obstacles, translated_boxes = {}, [], set()
    eligible = set()
    channels = {}
    from .source_safety import unsafe_source_geometry, source_identity
    for path in chapter_paths(chapters):
        for item in file_content(path):
            reference = item.value if isinstance(item.value, Reference) else None
            values = item.value.flow_items if isinstance(item.value, Reference) else [item.value]
            for number, text in enumerate(values):
                if text is None:
                    continue
                chapter = Chapter(item.chapter_id, item.level, [text])
                obstacles.extend(_chapter_obstacle_regions(chapter, pages, dpi,
                                                           error_policy=False, include_reference_text=False))
                if not isinstance(text, TextFlowItem):
                    continue
                key = source_key(text)
                channels[key] = "notes" if reference is not None else "body"
                if not key or key in entries:
                    continue
                fragments = [fragment for fragment in text.children if isinstance(fragment, SourceTextFragment)]
                regions = _regions_for_fragments(fragments, pages, dpi, False)
                if not regions:
                    continue
                first = regions[0]
                identity = paragraph_identity(chapter, text)
                covered = translating or coverage.narrative.get(identity) == 'translated'
                value, formulas = _to_pdf_patch_content(iter_continuous_content(text))
                if reference is not None and number == 0:
                    value = str(reference.mark) + ' ' + value
                replacement = None
                if covered and value.strip():
                    replacement = PDFReplacement(first.page_index, first.bbox, value.strip(),
                        first.page_pixel_size, first.dpi, reading_order=first.reading_order,
                        regions=regions, layout_ref=text.role, layout_level=text.level, inline_formulas=formulas)
                    eligible.add(source_identity(key))
                    if not translating:
                        translated_boxes.update((region.page_index, region.bbox) for region in regions)
                else:
                    obstacles.extend(regions)
                if translating:
                    # Coverage arrives later. Every source rectangle must already
                    # protect untouched text, including future failed paragraphs.
                    # The planner also treats accepted source rectangles as
                    # obstacles, so this does not change their layout semantics.
                    obstacles.extend(regions)
                prototype = directory / ('prototype-' + str(len(entries)))
                write_record(prototype, replacement)
                entries[key] = (prototype, (first.page_index, first.reading_order, first.bbox[1], first.bbox[0]))
    # Furniture is fixed metadata, independent of natural paragraph delivery.
    furniture_items = list(PDFTranslationPipeline()._iter_covered_replacements([], furniture, coverage, pages, dpi))
    for replacement in furniture_items:
        obstacles.extend(replacement.obstacle_regions)
        translated_boxes.update((region.page_index, region.bbox) for region in replacement.source_regions())
    obstacles = tuple({(r.page_index, r.bbox): r for r in obstacles
                       if (r.page_index, r.bbox) not in translated_boxes}.values())
    obstacles_by_page = {}
    for region in obstacles:
        obstacles_by_page.setdefault(region.page_index, []).append(region)
    ordered = sorted(entries, key=lambda key: entries[key][1])
    rejected, unsafe = unsafe_source_geometry(chapters, furniture_items, eligible, pages, dpi, directory / 'source-safety.sqlite')
    return {key: (index, entries[key][0], entries[key][1]) for index, key in enumerate(ordered)}, obstacles_by_page, furniture_items, rejected, unsafe, channels


def _with_page_obstacles(replacement, obstacles):
    # A live window needs only geometry on its source pages, not a copy of
    # every paragraph's rectangles throughout the book on every feed record.
    page_indexes = dict.fromkeys(region.page_index for region in replacement.source_regions())
    return replace(replacement, obstacle_regions=tuple(
        region for page in page_indexes for region in obstacles.get(page, ())
    ))


@fatal_error_boundary
async def patch_content(source, extraction, target, *, translator=None, translation_id=None,
                        window=1, error_policy: ErrorPolicyInput=OMITTED, on_body=None, on_footnote=None,
                        pdf_handler=None, with_furniture=False, on_translation_event=None, on_metadata=None):
    """Translation and native composition overlap through a disk-only process feed."""
    from .error_policy import _process_error_policy_checker
    async with _process_error_policy_checker(error_policy) as checker:
        mode = RenderMode.REPLACE if translation_id is not None else RenderMode.SOURCE
        deferred = {}
        async with content_view(extraction, mode=mode, translation_id=translation_id,
                                translator=translator, window=window, with_anchored=False, with_furniture=with_furniture,
                                on_translation_event=on_translation_event, on_metadata=on_metadata, deferred=deferred) as (view, pages, numbers, items):
            async with worker_context(materialize_pdf_patch_view(extraction, translation_id)) as patch_view:
                coverage = await IO_DOMAIN.run(read_coverage, patch_view.coverage)
                if view.furniture_coverage is not None:
                    furniture_states = await IO_DOMAIN.run(read_coverage, view.furniture_coverage)
                    coverage = replace(coverage, positions=furniture_states.positions, sections=furniture_states.sections)
                async with temporary_directory('pdf-craft-pdf-content-') as directory:
                    root = Path(directory)
                    feed_root, previews = root / 'feed', root / 'previews'
                    await IO_DOMAIN.run(feed_root.mkdir)
                    await IO_DOMAIN.run(previews.mkdir)
                    sizes = {index: (page.width, page.height) for index, page in pages.items()}
                    dpi = next(iter(pages.values())).render_dpi if pages else 300
                    schedule = deferred.get('schedule')
                    dynamic_furniture = schedule is not None and schedule.furniture is not None
                    furniture_path = view.furnitures or patch_view.furnitures
                    initial_coverage = (await IO_DOMAIN.run(source_furniture_coverage, furniture_path, coverage)
                                        if dynamic_furniture else coverage)
                    index, obstacles, furniture, rejected, unsafe, source_channels = await IO_DOMAIN.run(
                        prepare_index, view.chapters, furniture_path, initial_coverage,
                        sizes, dpi, root, translator is not None)
                    if rejected:
                        from ..._policy import recover
                        from ...policies import FailureCategory, FailureLocation
                        from ...document.paragraph import SourceRegion
                        await recover(ValueError('Text source bbox substantially covers unrelated source content'),
                                      FailureCategory.PDF_FILL, 'source_geometry', 'original_pdf_page',
                                      targets=tuple(FailureLocation('regions', source=str(source),
                                          page_indexes=(r.page_index,), regions=(SourceRegion(pages[r.page_index], r.reading_order, r.bbox),))
                                          for r in rejected),
                                      affected=tuple(FailureLocation('page', source=str(source), page_indexes=(page,)) for page in sorted(unsafe)))
                    # Furniture and narrative are sorted together by original geometry.
                    all_entries = [(key, data[1], data[2]) for key, data in index.items()]
                    # Reserve geometry from immutable source; translated values
                    # arrive per page without changing the dependency graph.
                    if dynamic_furniture:
                        for value in furniture:
                            for region in value.source_regions():
                                obstacles[region.page_index] = [*obstacles.get(region.page_index, ()), region]
                    for number, value in enumerate(furniture):
                        value = _with_page_obstacles(value, obstacles)
                        path = root / ('furniture-' + str(number))
                        await IO_DOMAIN.run(write_record, path, value)
                        all_entries.append((None, path, _replacement_source_order(value)))
                    all_entries.sort(key=lambda entry: entry[2])
                    slots = {key: (slot, path) for slot, (key, path, _) in enumerate(all_entries) if key is not None}
                    channel_slots = {'body': [], 'notes': []}
                    furniture_slots, spans = {}, []
                    for slot, (key, path, _) in enumerate(all_entries):
                        if key is not None:
                            channel_slots[source_channels[key]].append(slot)
                            indexes = [part[0] for part in key]
                            spans.append((slot, min(indexes), max(indexes)))
                        if key is None:
                            if dynamic_furniture:
                                value = await IO_DOMAIN.run(read_record, path)
                                furniture_slots.setdefault(value.page_index, []).append((slot, value))
                                spans.append((slot, value.page_index, value.page_index))
                                continue
                            value = await IO_DOMAIN.run(read_record, path)
                            spans.append((slot, value.page_index, value.page_index))
                            await IO_DOMAIN.run(write_record, feed_root / str(slot), value)
                            await IO_DOMAIN.run(write_record, previews / str(slot), None)
                    del all_entries
                    count = len(slots) + len(furniture)
                    patcher = PDFPatcher(pdf_handler=pdf_handler)
                    patcher._preserved_source_pages = unsafe
                    patcher._stream_pages = {n: page.render_dpi for n, page in pages.items()}
                    if on_body is not None or on_footnote is not None:
                        patcher._preview_path = previews
                    feed = ReplacementFeed(feed_root, count, dependency_windows(spans))
                    # Notification helper renders Markdown only when a PDF channel is requested.
                    async with paragraph_notifications(pages, view.assets, numbers) as notify:
                        async def produce():
                            written = set()
                            try:
                                async for item in items:
                                    effective = display_values(item, RenderMode.REPLACE)
                                    if effective:
                                        item = replace(item, value=effective[0])
                                    notes = isinstance(item.value, Reference)
                                    values = item.value.flow_items if isinstance(item.value, Reference) else [item.value]
                                    originals = item.original.flow_items if isinstance(item.original, Reference) else [item.original]
                                    for number, text in enumerate(values):
                                        if not isinstance(text, TextFlowItem):
                                            continue
                                        key = source_key(text)
                                        if key not in slots or key in written:
                                            continue
                                        written.add(key)
                                        slot, path = slots[key]
                                        prototype = await IO_DOMAIN.run(read_record, path)
                                        identity = (str(item.chapter_id) if item.chapter_id is not None else 'head',
                                                    key[0][0], key[0][1])
                                        missing = (item.narrative_states is not None and
                                                   item.narrative_states.get(identity) != 'translated')
                                        if missing:
                                            # Presentation may show the original, but it is
                                            # not a trusted translation or a fill target.
                                            prototype = None
                                        patch_text, formulas = _to_pdf_patch_content(iter_continuous_content(text))
                                        if isinstance(item.value, Reference) and number == 0:
                                            patch_text = str(item.value.mark) + ' ' + patch_text
                                        handler = on_footnote if notes else on_body
                                        preview = None
                                        if handler is not None:
                                            original = originals[number] if not missing and number < len(originals) else None
                                            preview = await IO_DOMAIN.run(notify.content, text, original, item.level)
                                        if prototype is None:
                                            await IO_DOMAIN.run(write_record, feed_root / str(slot), None)
                                            empty = None if preview is None else (notes, PDFParagraphContent(
                                                preview.text, preview.original, [PDFSourceRegion(
                                                    r.page, r.source_order_in_page, r.bbox, None, []) for r in preview.regions]))
                                            await IO_DOMAIN.run(write_record, previews / str(slot), empty)
                                            continue
                                        value = _with_page_obstacles(replace(
                                            prototype, text=patch_text.strip(), inline_formulas=formulas), obstacles)
                                        if preview is not None:
                                            value = _PreviewReplacement.attach(value, preview, notes, slot)
                                        await IO_DOMAIN.run(write_record, feed_root / str(slot), value if value.text else None)
                                        if preview is None:
                                            await IO_DOMAIN.run(write_record, previews / str(slot), None)
                                        elif not value.text:
                                            empty = PDFParagraphContent(preview.text, preview.original, [
                                                PDFSourceRegion(r.page, r.source_order_in_page, r.bbox, None, [])
                                                for r in preview.regions])
                                            await IO_DOMAIN.run(write_record, previews / str(slot), (notes, empty))
                                for key, (slot, _) in slots.items():
                                    if key not in written:
                                        await IO_DOMAIN.run(write_record, feed_root / str(slot), None)
                                        await IO_DOMAIN.run(write_record, previews / str(slot), None)
                            except BaseException as error:
                                await IO_DOMAIN.run(write_record, feed_root / 'error', str(error))
                                raise
                            finally:
                                await items.aclose()

                        production = asyncio.create_task(produce())
                        furniture_task = (asyncio.create_task(produce_furniture(
                            schedule, furniture_slots, feed_root, previews, sizes, dpi, root, obstacles))
                            if dynamic_furniture else None)
                        staged = root / 'output.pdf'
                        patching = asyncio.create_task(patcher.patch(source, staged, feed,
                            error_policy=checker, document_metadata=pdf_document_metadata(view.metadata)))
                        producers = [production, patching] + ([furniture_task] if furniture_task is not None else [])
                        delivery = asyncio.create_task(deliver_previews(root, previews, channel_slots, producers,
                            on_body, on_footnote)) if on_body is not None or on_footnote is not None else None
                        tasks = producers + ([delivery] if delivery is not None else [])
                        try:
                            await asyncio.gather(*tasks)
                            if schedule is not None:
                                from ...document.render import _effective_metadata
                                overlay = await schedule.header()
                                await IO_DOMAIN.run(update_metadata, staged, pdf_document_metadata(
                                    _effective_metadata(view.metadata, overlay, RenderMode.REPLACE)))
                            await IO_DOMAIN.run(publish, [(staged, target)])
                        finally:
                            await IO_DOMAIN.run(write_record, feed_root / 'error', 'PDF content stream stopped')
                            for task in tasks:
                                task.cancel()
                            await settle_tasks(tasks)
