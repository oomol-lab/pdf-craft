"""Prepare the fixed furniture channel using translated headings, not full body text."""
# pylint: disable=protected-access
from copy import deepcopy
import pickle
from shutil import copy2

from ..common import save_xml
from ..document.content import chapter_filename, chapter_paths, file_content
from ..extractor.chapter.chapter import Chapter, TextFlowItem, encode
from ..runtime import IO_DOMAIN, worker_iter
from .furniture_translation import translate_furnitures_in_workspace_async


def _headings(chapters):
    for path in chapter_paths(chapters):
        identifier, level, indexed = None, -1, []
        for item in file_content(path):
            identifier, level = item.chapter_id, item.level
            if isinstance(item.value, TextFlowItem) and item.value.role == 'heading':
                indexed.append((item.index, item.value))
        if indexed:
            yield Chapter(identifier, level, []), indexed


async def prepare_furniture(paths, transformer, root):
    chapters = root / 'chapters'
    await IO_DOMAIN.run(chapters.mkdir)
    cache = {}
    async for chapter, indexed in worker_iter(_headings(paths.chapters)):
        translated = await transformer.transform(Chapter(chapter.id, chapter.level,
                                                         [deepcopy(item) for _, item in indexed]),
                                                 emit_scope_events=False, emit_item_events=False)
        filename = chapter_filename(chapter)
        await IO_DOMAIN.run(save_xml, encode(translated), chapters / filename)
        for (index, _), item in zip(indexed, translated.flow_items, strict=True):
            path = root / (filename + f'.{index}.pickle')
            def save(path=path, item=item):
                with path.open('wb') as file:
                    pickle.dump(item, file, protocol=pickle.HIGHEST_PROTOCOL)
            await IO_DOMAIN.run(save)
            cache[filename, index] = path
    furniture = root / 'furnitures.xml'
    toc = root / 'toc.xml'
    if await IO_DOMAIN.run(paths.toc.exists):
        await IO_DOMAIN.run(copy2, paths.toc, toc)
    if await IO_DOMAIN.run(paths.furnitures.exists):
        await IO_DOMAIN.run(copy2, paths.furnitures, furniture)
        await translate_furnitures_in_workspace_async(
            chapters_path=chapters, toc_path=toc, furnitures_path=furniture,
            translation_path=root / 'coverage.xml', transformer=transformer._furniture_transformer())
    return cache, furniture, root / 'coverage.xml'
