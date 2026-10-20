"""Optional legacy translation progress over the same paragraph stream."""
from contextlib import aclosing

from ..document.content import chapter_paths, file_content
from ..runtime import IO_DOMAIN, invoke_callback
from .content import _unit
from .events import TranslationEvent, TranslationEventKind, TranslationItemKind


async def report_content(items, chapters, transformer, callback):
    """Index scalar totals only; report consumed chapters without a second translation."""
    def totals():
        result = {}
        for path in chapter_paths(chapters):
            count, characters, identifier = 0, 0, 'head'
            for item in file_content(path):
                count += 1
                identifier = item.chapter_id if item.chapter_id is not None else 'head'
                if item.value is not None:
                    characters += transformer.source_character_count(_unit(item))
            if characters:
                result[path.name] = [count, characters, identifier]
        return result
    pending = await IO_DOMAIN.run(totals)
    total = sum(value[1] for value in pending.values())
    completed = 0
    await invoke_callback(callback, TranslationEvent(
        TranslationEventKind.START, chapter_count=len(pending), has_toc=False,
        has_metadata=False, total_characters=total, completed_characters=0))
    for _, characters, identifier in pending.values():
        await invoke_callback(callback, TranslationEvent(
            TranslationEventKind.ITEM_START, item_kind=TranslationItemKind.CHAPTER,
            item_id=identifier, item_total_characters=characters,
            item_completed_characters=0, total_characters=total, completed_characters=0))
    async with aclosing(items):
        async for item in items:
            yield item
            if item.filename in pending:
                progress = pending[item.filename]
                progress[0] -= 1
                if progress[0] == 0:
                    completed += progress[1]
                    for kind in (TranslationEventKind.PROGRESS, TranslationEventKind.ITEM_COMPLETE):
                        await invoke_callback(callback, TranslationEvent(
                            kind, item_kind=TranslationItemKind.CHAPTER, item_id=progress[2],
                            item_total_characters=progress[1], item_completed_characters=progress[1],
                            total_characters=total, completed_characters=completed))
    await invoke_callback(callback, TranslationEvent(
        TranslationEventKind.COMPLETE, total_characters=total, completed_characters=completed))
