"""The PDF consumer of the shared PCEX content stream."""
# pylint: disable=protected-access
import asyncio
from dataclasses import replace
from pathlib import Path
from ...policies import ErrorPolicyInput
from ..._policy import OMITTED
from ...extractor.chapter.text_projection import iter_continuous_content

from ...common.stream import DiskDelivery
from ...document.stream_view import content_view
from ...document.render import RenderMode, materialize_pdf_patch_view
from ...extractor.chapter.chapter import TextFlowItem, SourceTextFragment, Reference
from ...runtime import IO_DOMAIN, temporary_directory, worker_context
from ...transformer.translation_coverage import read_coverage, paragraph_identity
from ...renderer.paragraph import paragraph_notifications, display_values
from ...renderer.output import publish
from ...document.paragraph import PDFParagraphContent, PDFSourceRegion
from .feed import _PreviewReplacement, ReplacementFeed, write_record, read_record
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
                    translated_boxes.update((region.page_index, region.bbox) for region in regions)
                else:
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
    ordered = sorted(entries, key=lambda key: entries[key][1])
    return {key: (index, entries[key][0], entries[key][1]) for index, key in enumerate(ordered)}, obstacles, furniture_items


async def patch_content(source, extraction, target, *, translator=None, translation_id=None,
                        window=1, error_policy: ErrorPolicyInput=OMITTED, on_body=None, on_footnote=None,
                        pdf_handler=None, with_furniture=False, on_translation_event=None, on_metadata=None):
    """Translation and native composition overlap through a disk-only process feed."""
    from .error_policy import _process_error_policy_checker
    async with _process_error_policy_checker(error_policy) as checker:
        mode = RenderMode.REPLACE if translation_id is not None else RenderMode.SOURCE
        async with content_view(extraction, mode=mode, translation_id=translation_id,
                                translator=translator, window=window, with_anchored=False, with_furniture=with_furniture,
                                on_translation_event=on_translation_event, on_metadata=on_metadata) as (view, pages, numbers, items):
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
                    index, obstacles, furniture = await IO_DOMAIN.run(
                        prepare_index, view.chapters, view.furnitures or patch_view.furnitures, coverage,
                        sizes, dpi, root, translator is not None)
                    # Furniture and narrative are sorted together by original geometry.
                    all_entries = [(key, data[1], data[2]) for key, data in index.items()]
                    for number, value in enumerate(furniture):
                        path = root / ('furniture-' + str(number))
                        await IO_DOMAIN.run(write_record, path, value)
                        all_entries.append((None, path, _replacement_source_order(value)))
                    all_entries.sort(key=lambda entry: entry[2])
                    slots = {key: (slot, path) for slot, (key, path, _) in enumerate(all_entries) if key is not None}
                    for slot, (key, path, _) in enumerate(all_entries):
                        if key is None:
                            value = await IO_DOMAIN.run(read_record, path)
                            await IO_DOMAIN.run(write_record, feed_root / str(slot), value)
                            await IO_DOMAIN.run(write_record, previews / str(slot), None)
                    del all_entries
                    count = len(slots) + len(furniture)
                    patcher = PDFPatcher(pdf_handler=pdf_handler)
                    patcher._stream_pages = {n: page.render_dpi for n, page in pages.items()}
                    if on_body is not None or on_footnote is not None:
                        patcher._preview_path = previews
                    feed = ReplacementFeed(feed_root, count)
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
                                        patch_text, formulas = _to_pdf_patch_content(iter_continuous_content(text))
                                        if isinstance(item.value, Reference) and number == 0:
                                            patch_text = str(item.value.mark) + ' ' + patch_text
                                        handler = on_footnote if notes else on_body
                                        preview = None
                                        if handler is not None:
                                            original = originals[number] if number < len(originals) else None
                                            preview = await IO_DOMAIN.run(notify.content, text, original, item.level)
                                        if prototype is None:
                                            await IO_DOMAIN.run(write_record, feed_root / str(slot), None)
                                            empty = None if preview is None else (notes, PDFParagraphContent(
                                                preview.text, preview.original, [PDFSourceRegion(
                                                    r.page, r.source_order_in_page, r.bbox, None, []) for r in preview.regions]))
                                            await IO_DOMAIN.run(write_record, previews / str(slot), empty)
                                            continue
                                        value = replace(prototype, text=patch_text.strip(), inline_formulas=formulas,
                                                        obstacle_regions=obstacles)
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

                        async def deliver(patching):
                            body = DiskDelivery(root / 'body', on_body) if on_body is not None else None
                            notes = DiskDelivery(root / 'notes', on_footnote) if on_footnote is not None else None
                            try:
                                for slot in range(count):
                                    path = previews / str(slot)
                                    while not await IO_DOMAIN.run(path.exists):
                                        for channel in (body, notes):
                                            if channel is not None and channel._task.done():
                                                channel._task.result()
                                        if patching.done() and production.done():
                                            patching.result()
                                            production.result()
                                            break  # ignored failed page has no accepted layout
                                        await asyncio.sleep(.01)
                                    if await IO_DOMAIN.run(path.exists):
                                        value = await IO_DOMAIN.run(read_record, path)
                                        if value is not None:
                                            channel, content = value
                                            delivery = notes if channel else body
                                            if delivery is not None:
                                                await delivery.append(content)
                                for delivery in (body, notes):
                                    if delivery is not None:
                                        await delivery.finish()
                            finally:
                                for delivery in (body, notes):
                                    if delivery is not None:
                                        await delivery.cancel()

                        production = asyncio.create_task(produce())
                        staged = root / 'output.pdf'
                        patching = asyncio.create_task(patcher.patch(source, staged, feed,
                            error_policy=checker, document_metadata=pdf_document_metadata(view.metadata)))
                        delivery = asyncio.create_task(deliver(patching)) if on_body is not None or on_footnote is not None else None
                        tasks = [production, patching] + ([delivery] if delivery is not None else [])
                        try:
                            await asyncio.gather(*tasks)
                            await IO_DOMAIN.run(publish, [(staged, target)])
                        finally:
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
