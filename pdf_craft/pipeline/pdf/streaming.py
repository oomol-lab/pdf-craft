"""Local furniture joins and independent PDF preview channels."""
# pylint: disable=protected-access
import asyncio
from dataclasses import replace

from ...common import read_xml, save_xml
from ...common.stream import DiskDelivery
from ...runtime import IO_DOMAIN
from ...concurrency import settle_tasks
from ...transformer.translation_coverage import TranslationCoverage
from .feed import read_record, write_record
from .pipeline import PDFTranslationPipeline


def source_furniture_coverage(path, coverage):
    """Reserve every source furniture slot before any translation completes."""
    if not path.exists():
        return coverage
    root = read_xml(path)
    return replace(coverage,
        positions={(p.get('id', ''), v.get('id', '')): 'translated'
                   for p in root.findall('patterns/pattern') for v in p.findall('position')},
        sections={(int(p.get('index', '0')), s.get('det', '')): 'translated'
                  for p in root.findall('pages/page') for s in p.findall('section')})


def page_furniture(plan, index, pages, dpi, directory):
    snapshot = plan.page_snapshot(index)
    if snapshot is None:
        return None
    root, positions, sections = snapshot
    path = directory / f'furniture-page-{index}.xml'
    save_xml(root, path)
    coverage = TranslationCoverage({}, {(v.pattern_id, v.position_id): v.state for v in positions},
                                   {(v.page_index, v.det): v.state for v in sections}, {})
    return list(PDFTranslationPipeline()._iter_covered_replacements([], path, coverage, pages, dpi))


async def produce_furniture(schedule, slots, feed_root, previews, pages, dpi, directory, obstacles):
    from .content import _with_page_obstacles
    pending = dict(slots)
    while pending:
        schedule.furniture_changed.clear()
        if schedule.runner.done():
            schedule.runner.result()
        for index in list(pending):
            values = await IO_DOMAIN.run(page_furniture, schedule.furniture, index, pages, dpi, directory)
            if values is None:
                continue
            by_box = {value.bbox: value for value in values}
            for slot, prototype in pending.pop(index):
                value = by_box.get(prototype.bbox)
                if value is not None:
                    value = _with_page_obstacles(value, obstacles)
                await IO_DOMAIN.run(write_record, feed_root / str(slot), value)
                await IO_DOMAIN.run(write_record, previews / str(slot), None)
        if pending:
            if schedule.runner.done():
                raise RuntimeError('furniture ended with unresolved page dependencies')
            await schedule.furniture_changed.wait()


async def deliver_previews(root, previews, channels, producers, on_body, on_footnote):
    async def channel(name, callback):
        if callback is None:
            return
        delivery = DiskDelivery(root / name, callback)
        try:
            for slot in channels[name]:
                path = previews / str(slot)
                while not await IO_DOMAIN.run(path.exists):
                    for task in [*producers, delivery._task]:  # pylint: disable=protected-access
                        if task.done():
                            task.result()
                    if all(task.done() for task in producers):
                        break  # Ignored failed layout has no accepted preview.
                    await asyncio.sleep(.01)
                if await IO_DOMAIN.run(path.exists):
                    value = await IO_DOMAIN.run(read_record, path)
                    if value is not None:
                        await delivery.append(value[1])
            await delivery.finish()
        finally:
            await delivery.cancel()
    tasks = [asyncio.create_task(channel('body', on_body)), asyncio.create_task(channel('notes', on_footnote))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await settle_tasks(tasks)


def update_metadata(path, metadata):
    """Document information has no role in Qt layout; join it at publication."""
    if not metadata:
        return
    import pypdf
    from .patcher import PDFPatcher
    writer = pypdf.PdfWriter(clone_from=str(path))
    writer.add_metadata(metadata)
    PDFPatcher._write_output(writer, path)  # pylint: disable=protected-access
