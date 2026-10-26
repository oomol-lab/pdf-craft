"""Scalar-only PCEX pre-scan sharing the execution adapters' source units."""
# pylint: disable=protected-access
from collections import Counter
from contextlib import asynccontextmanager
from functools import wraps

from ..common import read_xml
from ..document.content import chapter_paths, file_content
from ..extractor.chapter.chapter import decode
from ..runtime import IO_DOMAIN, worker_context
from ..progress import UnitKind
from .anchored_translation import _asset_slots, _asset_has_text, asset_word_count
from .content import _unit
from .narrative_xml import _metadata_element
from .progress import TranslationProgress, active_progress, progress_context
from .word_count import count_xml_words


def scan(paths, transformer=None, *, metadata, with_anchored, with_furniture):
    from .narrative_xml import NarrativeXMLTransformer
    count_words = transformer.source_word_count if transformer is not None else NarrativeXMLTransformer.source_word_count
    counts, total = Counter(), 0

    def add(kind, words, *, include_empty=False):
        nonlocal total
        if words or include_empty:
            counts[kind] += 1
            total += words

    for path in chapter_paths(paths.chapters):
        sources = (_unit(item) for item in file_content(path) if item.value is not None) if transformer is None or transformer.supports_stream else (decode(read_xml(path)),)
        for source in sources:
            add(UnitKind.CONTENT, count_words(source))
            if with_anchored:
                for slot in _asset_slots(source):
                    if _asset_has_text(slot.asset):
                        add(UnitKind.ASSET, asset_word_count(slot.asset), include_empty=True)
    if metadata is not None:
        add(UnitKind.METADATA, count_xml_words(_metadata_element(metadata)[0]))
    if with_furniture and paths.furnitures.exists():
        from ..common.words import count_text_words
        root = read_xml(paths.furnitures)
        for position in root.findall('patterns/pattern/position'):
            if position.get('folio_style') is not None:
                words = count_text_words(position.get('folio_prefix', '')) + count_text_words(position.get('folio_suffix', ''))
                add(UnitKind.FURNITURE, words)
            elif position.get('toc_id') is None:
                add(UnitKind.FURNITURE, count_text_words(position.text or ''))
        for page in root.findall('pages/page'):
            add(UnitKind.FURNITURE, sum(count_text_words(section.text or '') for section in page.findall('section')
                                      if not len(section) and section.get('toc_id') is None))
    return counts, total


@asynccontextmanager
async def pcex_progress(extraction, transformer, callback, *, metadata=None,
                        with_anchored=False, with_furniture=False):
    progress = active_progress()
    if (callback is None and progress is None) or (progress is not None and progress.started):
        yield
        return
    owns = progress is None
    progress = progress or TranslationProgress(callback)
    async with worker_context(extraction._materialize()) as paths:
        counts, total = await IO_DOMAIN.run(scan, paths, transformer, metadata=metadata,
                                           with_anchored=with_anchored, with_furniture=with_furniture)
    await progress.start(counts, total)
    with progress_context(progress):
        yield
        if owns and not progress.pending_streams and all(progress.finished[kind] == count for kind, count in progress.counts.items()):
            await progress.finish()


def report_pcex_translation(function):
    """Begin statistics before metadata; outer operations own publication."""
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
