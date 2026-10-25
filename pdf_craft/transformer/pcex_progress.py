"""Pre-scan PCEX source with the adapters used by actual translation."""
# pylint: disable=protected-access
from contextlib import asynccontextmanager
from functools import wraps

from ..common import read_xml
from ..document.content import chapter_paths, file_content
from ..extractor.chapter.chapter import Chapter, StandaloneAsset, encode
from ..runtime import IO_DOMAIN, worker_context
from .anchored_translation import _asset_slots
from .content import _unit
from .events import TranslationEventKind, TranslationItemKind
from .narrative_xml import _metadata_element
from .progress import TranslationProgress, active_progress, progress_context
from .word_count import count_xml_words


def furniture_words(root):
    """Count independently processed templates/sections, excluding title reuse."""
    from ..common.words import count_text_words
    total = 0
    for position in root.findall('patterns/pattern/position'):
        if position.get('folio_style') is not None:
            total += count_text_words(position.get('folio_prefix', ''))
            total += count_text_words(position.get('folio_suffix', ''))
        elif position.get('toc_id') is None:
            total += count_text_words(position.text or '')
    for section in root.findall('pages/page/section'):
        if not len(section) and section.get('toc_id') is None:
            total += count_text_words(section.text or '')
    return total


def scan(paths, transformer, *, metadata, with_anchored, with_furniture):
    totals = {}
    for path in chapter_paths(paths.chapters):
        for item in file_content(path):
            if item.value is None:
                continue
            source = _unit(item)
            key = (TranslationItemKind.CHAPTER, item.chapter_id if item.chapter_id is not None else 'head')
            words = transformer.source_word_count(source)
            if with_anchored:
                for slot in _asset_slots(source):
                    words += count_xml_words(encode(Chapter(None, -1, [StandaloneAsset(slot.asset)])))
            totals[key] = totals.get(key, 0) + words
    if metadata is not None:
        totals[TranslationItemKind.METADATA, 'metadata'] = count_xml_words(_metadata_element(metadata)[0])
    if with_furniture and paths.furnitures.exists():
        totals[TranslationItemKind.FURNITURE, 'furniture'] = furniture_words(read_xml(paths.furnitures))
    return {key: words for key, words in totals.items() if words}


@asynccontextmanager
async def pcex_progress(extraction, transformer, callback, *, metadata=None,
                        with_anchored=False, with_furniture=False):
    if callback is None or active_progress() is not None:
        yield
        return
    async with worker_context(extraction._materialize()) as paths:
        totals = await IO_DOMAIN.run(scan, paths, transformer, metadata=metadata,
                                    with_anchored=with_anchored, with_furniture=with_furniture)
    progress = TranslationProgress(callback, totals)
    await progress.emit(TranslationEventKind.START)
    with progress_context(progress):
        yield
        await progress.finish()


def report_pcex_translation(function):
    """Wrap the persisted replacement path before its metadata LLM call."""
    @wraps(function)
    async def wrapped(self, extraction, workspace, translator, **kwargs):
        from .narrative_xml import NarrativeXMLTransformer
        metadata = await IO_DOMAIN.run(extraction._document_metadata)
        async with pcex_progress(extraction, NarrativeXMLTransformer(translator),
                                 kwargs.get('on_translation_event'), metadata=metadata,
                                 with_anchored=kwargs.get('with_anchored', True),
                                 with_furniture=kwargs.get('with_furniture', False)):
            return await function(self, extraction, workspace, translator, **kwargs)
    return wrapped
