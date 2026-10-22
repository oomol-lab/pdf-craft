"""Finite extraction recovery, independent of damaged chapter object graphs.

Only the recovery path scans source pages. It rebuilds surviving contiguous
runs separately, so no reference or continuation can cross a discarded page.
OCR caches and already delivered previews are never rewritten/replayed.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import shutil
from xml.etree.ElementTree import ParseError

from ..common import read_xml, save_xml, AssetHub
from ..concurrency import NonContinuableError
from ..error import InterruptedError, NoUsableOCRPagesError, PDFError
from ..policies import AllFallbackError, ErrorCallbackError, FailureCategory, FailureLocation
from .._policy import recover
from ..runtime import IO_DOMAIN, worker_iter
from ..common.stream import DiskStream
from .chapter.chapter import Chapter, StandaloneAsset, SourceAsset, TextFlowItem, SourceTextFragment, encode
from .chapter.generation import _generate_chapters
from .chapter.page_analysis import PageAnalysis, continuation_sequences


def recoverable_structure(error: Exception) -> bool:
    """Do not convert user decisions, storage faults, or interruption into images."""
    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, (OSError, NonContinuableError, InterruptedError,
                              AllFallbackError, ErrorCallbackError)) or (
            getattr(cause, '_pdf_craft_decided', False) or
            getattr(cause, '_pdf_craft_callback', False)
        ):
            return False
        cause = cause.__cause__
    return isinstance(error, (ValueError, TypeError, AssertionError, KeyError, IndexError, RuntimeError, ParseError))


def failure_pages(error: Exception) -> set[int]:
    pages: set[int] = set(getattr(error, 'page_indexes', ()))
    index = getattr(error, 'page_index', None)
    if index is not None:
        pages.add(index)
    return pages


def affected_pages(targets: set[int], analyses: Iterable[PageAnalysis]) -> set[int]:
    """Close over author paragraphs, shared definitions and their references."""
    pages = list(analyses)
    groups: dict[tuple[str, str | None], list] = {}
    definitions: dict[tuple[int, int], set[int]] = {}
    citation_pages: dict[str, set[int]] = {}
    for page in pages:
        for citation in page.citations:
            citation_pages.setdefault(citation.citation_id, set()).add(page.page_index)
    for page in pages:
        for citation in page.citations:
            if citation.index is not None:
                definitions[page.page_index, citation.index] = citation_pages[citation.citation_id]
        for layout in page.layouts:
            if layout.ownership != 'ignored':
                groups.setdefault((layout.ownership, layout.citation_id), []).append(layout)
    links = list(citation_pages.values())
    for layouts in groups.values():
        for sequence in continuation_sequences(sorted(layouts, key=lambda item: item.document_order)):
            for left, right in zip(sequence, sequence[1:]):
                if right.continues_from_previous:
                    links.append({left.page_index, right.page_index})
    for page in pages:
        for layout in page.layouts:
            for reference in layout.references:
                links.append({page.page_index} | definitions.get(
                    (reference.citation_page_index, reference.citation_index), set()))
    affected = set(targets)
    for _ in range(len(pages)):
        expanded = affected.union(*(link for link in links if link & affected))
        if expanded == affected:
            break
        affected = expanded
    return affected


def source_dependencies(path: Path, indexes: list[int], targets: set[int]) -> set[int]:
    """Conservative page links when no valid analysis graph is available.

    Inspect one raw page at a time. This does not restore damaged analysis or
    infer reference ownership. Consecutive footnote pages are conservatively
    coupled, even if their precise definition identities cannot be recovered.
    """
    from ..pdf import decode, TITLE_TAGS
    from ..common import ASSET_TAGS
    from .chapter.jointer import _parse_block_content
    from .chapter.mergeable import check_mergeable
    links = []
    previous = None
    for index in indexes:
        try:
            page = decode(read_xml(path / f'page_{index}.xml'))
        except Exception as error:
            if not recoverable_structure(error):
                raise
            return set(indexes)  # Cannot establish safe cross-page boundaries.
        if previous is not None:
            left = previous.body_layouts
            right = page.body_layouts
            left_texts = [i for i, layout in enumerate(left) if layout.ref not in (*ASSET_TAGS, *TITLE_TAGS)]
            right_texts = [i for i, layout in enumerate(right) if layout.ref not in (*ASSET_TAGS, *TITLE_TAGS)]
            if left_texts and right_texts:
                before, after = left_texts[-1], right_texts[0]
                boundaries = [*left[before + 1:], *right[:after]]
                if not any(layout.ref in ('equation', 'formula', *TITLE_TAGS) for layout in boundaries):
                    if check_mergeable(_parse_block_content(left[before].text), _parse_block_content(right[after].text)):
                        links.append({previous.index, page.index})
            if previous.footnotes_layouts and page.footnotes_layouts:
                links.append({previous.index, page.index})
        previous = page
    affected = set(targets)
    for _ in indexes:
        expanded = affected.union(*(link for link in links if link & affected))
        if expanded == affected:
            break
        affected = expanded
    return affected


async def recover_chapters(draft, ocr, error: Exception, directory: Path, *, analyses=(), aborted):
    """Prepare replacement chapters; disk/publication errors are outside this boundary.

    Each source run is tried at most once. Unknown failures conservatively
    discard that run, never repeatedly repair the same graph.
    """
    if not recoverable_structure(error):
        raise error
    indexes = sorted(int(path.stem[5:]) for path in draft.pages_path.glob('page_*.xml') if path.stem[5:].isdigit())
    content_indexes = set(indexes) - set(draft.toc.page_indexes)
    if not content_indexes:
        raise error  # An empty fallback cannot turn a failed operation into success.
    targets = failure_pages(error) & content_indexes
    if not targets:
        targets = set(content_indexes)
    analyses = list(analyses)
    try:
        affected = (affected_pages(targets, analyses) if analyses else await IO_DOMAIN.run(
            source_dependencies, draft.pages_path, [index for index in indexes if index in content_indexes], targets)) & content_indexes
    except Exception as scope_error:
        if not recoverable_structure(scope_error):
            raise
        affected = set(content_indexes)  # The scope probe is optional, never another recovery prerequisite.
    prepared = directory / 'chapters'
    prepared.mkdir(parents=True)
    degraded = set()
    fragments: dict[int, DiskStream[Chapter]] = {}

    def locations(values, cause=None):
        return tuple(FailureLocation('page', source=str(draft.pdf_path), page_indexes=(index,),
                                     page=ocr.page_geometry(index, draft.dpi or 300),
                                     bbox=getattr(cause, 'bbox', None) if index == getattr(cause, 'page_index', None) else None)
                     for index in sorted(values))

    def check_cancelled():
        if aborted():
            from ..metering import InterruptedKind
            raise InterruptedError(InterruptedKind.ABORT, draft.metering)

    async def degrade(values, cause):
        check_cancelled()
        await recover(cause, FailureCategory.ANALYSIS, 'chapter_recovery', 'page_image',
                      targets=locations(failure_pages(cause) & values or values, cause), affected=locations(values))
        for index in sorted(values):
            check_cancelled()
            try:
                page = await IO_DOMAIN.run(ocr.recover_page_image, draft.pdf_path,
                                          draft.extraction_paths.assets, index, draft.dpi or 300)
            except PDFError as render_error:
                await recover(render_error, FailureCategory.PDF_RENDER, 'chapter_recovery_render',
                              'page_placeholder', targets=locations({index}), affected=locations({index}))
                page = ocr._create_fallback_page(AssetHub(draft.extraction_paths.assets), index, None)  # pylint: disable=protected-access
            if page.body_layouts[0].ref == 'image':
                layout = page.body_layouts[0]
                item = StandaloneAsset(SourceAsset(index, 'image', layout.det, asset_hash=layout.hash))
            else:
                # Use known geometry, never the placeholder's synthetic bbox.
                size = ocr.last_page_pixel_sizes.get(index)
                bbox = (0, 0, *size) if size else (0, 0, 0, 0)
                item = TextFlowItem('body', -1, [SourceTextFragment(index, 0, bbox, [page.body_layouts[0].text])])
            records: DiskStream[Chapter] = DiskStream(directory / 'fallback' / str(index))
            await IO_DOMAIN.run(records.append, Chapter(None, -1, [item]))
            records.close()
            fragments[index] = records
            degraded.add(index)

    await degrade(affected, error)
    # Separate surviving runs at every degraded page. Fresh resolution removes
    # all source refs/gaps to discarded content; it never edits the OCR cache.
    run = []
    runs = []
    for index in indexes:
        if index in affected or index in draft.toc.page_indexes:
            if run:
                runs.append(run)
                run = []
        else:
            run.append(index)
    if run:
        runs.append(run)
    for run in runs:
        check_cancelled()
        path = directory / str(run[0])
        path.mkdir()
        for index in run:
            await IO_DOMAIN.run(shutil.copyfile, draft.pages_path / f'page_{index}.xml', path / f'page_{index}.xml')
        records = DiskStream(path / 'records')
        def collect(path=path, records=records):
            for chapter in _generate_chapters(path, draft.toc):
                records.append(chapter)
            records.close()
        try:
            await IO_DOMAIN.run(collect)
        except Exception as next_error:
            if not recoverable_structure(next_error):
                raise
            await degrade(set(run), next_error)
        else:
            # Keep original TOC chapter identities for the surviving run.
            fragments[run[0]] = records
    current = Chapter(None, -1, [])
    for index in indexes:
        records = fragments.get(index)
        if records is None:
            continue
        async for part in worker_iter(records.read()):
            check_cancelled()
            if part.id is not None and part.id != current.id:
                await IO_DOMAIN.run(save_xml, encode(current), prepared / f'chapter_{current.id if current.id is not None else "head"}.xml')
                current = part
            else:
                current.flow_items.extend(part.flow_items)
                current.definitions.extend(part.definitions)
    await IO_DOMAIN.run(save_xml, encode(current), prepared / f'chapter_{current.id if current.id is not None else "head"}.xml')
    ocr_failed = {index for index in content_indexes if (draft.pages_path / f'page_{index}.failed').exists()}
    if content_indexes and not (content_indexes - degraded - ocr_failed):
        raise NoUsableOCRPagesError(tuple(sorted(degraded | ocr_failed)))
    return prepared
