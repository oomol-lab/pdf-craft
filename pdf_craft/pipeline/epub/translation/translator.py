# pylint: disable=unused-argument

from collections.abc import Mapping
from pdf_craft.policies import RetryPolicy, ErrorPolicyInput
from pdf_craft._policy import OMITTED, policy_operation, has_error_callback
# pylint: disable=protected-access
from collections.abc import Callable, Generator
from dataclasses import dataclass
from enum import Enum, auto
from importlib.metadata import version as get_package_version
from os import PathLike
from pathlib import Path
from xml.etree.ElementTree import Element

from pdf_craft.pipeline.epub.adapter import (
    MetadataContext,
    TocContext,
    Zip,
    read_metadata,
    read_toc,
    write_metadata,
    write_toc,
)
from pdf_craft.llm import LLM
from pdf_craft.concurrency import AsyncExecutor
from pdf_craft.runtime import ARCHIVE_DOMAIN, temporary_directory
from pdf_craft.common.stream import DiskDelivery
from ..cfi import ContentCFILocator
from ..events import EpubFragmentContent
from ..adapter.spines import spine_cfi_paths
from ..adapter.chapters import read_chapters
from pdf_craft.transformer.events import TranslationEvent, TranslationItemKind
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.xml_translator.xml import (
    XMLLikeNode,
    clone_element,
    deduplicate_ids_in_element,
    find_first,
)
from pdf_craft.transformer.xml_translator.xml_translator import SubmitKind, TranslationTask, XMLTranslator
from .epub_transcode import decode_metadata, decode_toc_list, encode_metadata, encode_toc_list
from .punctuation import unwrap_french_quotes
from .xml_interrupter import XMLInterrupter
from pdf_craft.transformer.xml_translator.xml_translator.submitter import submit as submit_xml
from pdf_craft.document.metadata import MetadataCallback
from pdf_craft.runtime import invoke_callback


class _ElementType(Enum):
    TOC = auto()
    METADATA = auto()
    CHAPTER = auto()


@dataclass
class _ElementContext:
    element_type: _ElementType
    chapter_data: tuple[Path, XMLLikeNode] | None = None
    toc_context: TocContext | None = None
    metadata_context: MetadataContext | None = None
    chapter_title_data: tuple[Path, XMLLikeNode, Element] | None = None
    locator: ContentCFILocator | None = None
    cfi_package: str | None = None
    title_only: bool = False
    source_root_id: int = 0
    missing_metadata_tags: tuple[str, ...] = ()


@policy_operation
async def translate(
    source_path: PathLike | str,
    target_path: PathLike | str,
    target_language: str,
    submit: SubmitKind,
    user_prompt: str | None = None,
    max_group_tokens: int = 2600,
    llm: LLM | None = None,
    translation_llm: LLM | None = None,
    fill_llm: LLM | None = None,
    on_translation_event: Callable[[TranslationEvent], object] | None = None,
    executor: AsyncExecutor | None = None,
    translation_executor: AsyncExecutor | None = None,
    fill_executor: AsyncExecutor | None = None,
    window: int = 1,
    on_body: Callable[[EpubFragmentContent], object] | None = None,
    on_metadata: MetadataCallback | None = None, *,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> None:
    """Translate an EPUB with native async LLM concurrency and pooled ZIP I/O."""
    if window < 1:
        raise ValueError("window must be at least 1")
    translation_llm = translation_llm or llm
    fill_llm = fill_llm or llm
    if translation_llm is None:
        raise ValueError("Either translation_llm or llm must be provided")
    if fill_llm is None:
        raise ValueError("Either fill_llm or llm must be provided")
    translator = XMLTranslator(
        translation_llm=translation_llm,
        fill_llm=fill_llm,
        target_language=target_language,
        user_prompt=user_prompt,
        max_fill_displaying_errors=10,
        max_group_score=max_group_tokens,
        executor=executor,
        translation_executor=translation_executor,
        fill_executor=fill_executor,
        cache_seed_content=f"{_get_version()}:{target_language}",
    )
    archive: Zip | None = None
    book_metadata_context: MetadataContext | None = None

    def prepare():
        nonlocal archive, book_metadata_context
        archive = Zip(
            source_path=Path(source_path).resolve(),
            target_path=Path(target_path).resolve(),
        )
        try:
            archive.__enter__()
            archive.migrate(Path("mimetype"))
            toc_list, toc_context = read_toc(archive)
            metadata_fields, metadata_context = read_metadata(archive)
            book_metadata_context = metadata_context
            return _generate_tasks_from_book(
                zip=archive,
                toc_list=toc_list,
                toc_context=toc_context,
                metadata_fields=metadata_fields,
                metadata_context=metadata_context,
                submit=submit,
                capture_cfi=on_body is not None or has_error_callback(),
            )
        except BaseException as error:
            archive.__exit__(type(error), error, error.__traceback__)
            archive = None
            raise

    def write_results(results):
        assert archive is not None
        chapters_to_write: dict[Path, XMLLikeNode] = {}
        translated_metadata_element = next(
            (
                unwrap_french_quotes(element)
                for element, context in results
                if context.element_type == _ElementType.METADATA
            ),
            None,
        )
        translated_metadata = (
            decode_metadata(translated_metadata_element)
            if translated_metadata_element is not None else []
        )
        missing_tags = {tag for _, context in results for tag in context.missing_metadata_tags}
        translated_metadata = [field for field in translated_metadata if field.tag_name not in missing_tags]
        document_title = next(
            (
                field.text for field in translated_metadata
                if field.tag_name == "title"
            ),
            None,
        )
        for translated_elem, context in results:
            if context.element_type == _ElementType.TOC:
                translated_elem = unwrap_french_quotes(translated_elem)
                if context.toc_context is not None:
                    write_toc(
                        archive,
                        decode_toc_list(translated_elem),
                        context.toc_context,
                        document_title=document_title,
                    )
            elif context.element_type == _ElementType.METADATA:
                continue
            elif context.element_type == _ElementType.CHAPTER and context.chapter_data is not None:
                chapter_path, xml = context.chapter_data
                chapters_to_write[chapter_path] = xml
            elif context.chapter_title_data is not None:
                chapter_path, xml, title = context.chapter_title_data
                translated_title = find_first(translated_elem, "title")
                if translated_title is not None:
                    title.text = translated_title.text
                    title[:] = [clone_element(child) for child in translated_title]
                chapters_to_write[chapter_path] = xml
        for chapter_path, xml in chapters_to_write.items():
            deduplicate_ids_in_element(xml.element)
            with archive.replace(chapter_path) as target_file:
                xml.save(target_file)
        if book_metadata_context is not None:
            write_metadata(
                archive,
                translated_metadata,
                book_metadata_context,
                target_language=target_language,
            )

    delivery: DiskDelivery[EpubFragmentContent] | None = None
    failure: BaseException | None = None
    try:
        tasks = await ARCHIVE_DOMAIN.run(prepare)
        from ..adapter.metadata import translated_metadata_content
        metadata_notified = False
        has_metadata_task = False

        async def notify_metadata(fields):
            nonlocal metadata_notified
            if on_metadata is not None:
                assert book_metadata_context is not None
                content = await ARCHIVE_DOMAIN.run(
                    translated_metadata_content, book_metadata_context, fields, target_language,
                )
                await invoke_callback(on_metadata, content)
            metadata_notified = True

        interrupter = XMLInterrupter()
        header_results = []
        translated_titles = {}

        async def task_stream():
            nonlocal has_metadata_task
            try:
                while True:
                    task = await ARCHIVE_DOMAIN.run(next, tasks, None)
                    if task is None:
                        if not has_metadata_task and not metadata_notified:
                            await notify_metadata([])
                        return
                    if task.payload.element_type == _ElementType.METADATA:
                        has_metadata_task = True
                    if task.payload.element_type == _ElementType.CHAPTER and not has_metadata_task and not metadata_notified:
                        # No translatable OPF fields: still deliver the complete book view.
                        await notify_metadata([])
                    if on_body is not None:
                        task.payload.source_root_id = id(task.element)
                    yield task
            finally:
                await ARCHIVE_DOMAIN.run(tasks.close)

        def commit_content(element, context):
            assert archive is not None
            chapter = context.chapter_data
            if context.chapter_title_data is not None:
                path, xml, title = context.chapter_title_data
                translated_title = find_first(element, "title")
                if translated_title is not None:
                    title.text = translated_title.text
                    title[:] = [clone_element(child) for child in translated_title]
                    translated_titles[path] = clone_element(title)
                chapter = (path, xml) if context.title_only else None
            if chapter is not None:
                path, xml = chapter
                if context.chapter_data is not None:
                    old_body = find_first(xml.element, 'body')
                    if old_body is not None and old_body is not element:
                        for parent in xml.element.iter():
                            if old_body in list(parent):
                                position = list(parent).index(old_body)
                                parent.remove(old_body)
                                parent.insert(position, element)
                                break
                if path in translated_titles:
                    title = find_first(xml.element, 'title')
                    if title is not None:
                        translated_title = translated_titles[path]
                        title.text = translated_title.text
                        title[:] = [clone_element(child) for child in translated_title]
                    translated_titles.pop(path, None)
                deduplicate_ids_in_element(xml.element)
                with archive.replace(path) as file:
                    xml.save(file)

        from ...._policy import Completion
        completion = Completion()

        async def on_result(result):
            element, context = result
            if context.element_type == _ElementType.METADATA and on_metadata is None:
                await notify_metadata(decode_metadata(unwrap_french_quotes(element)))
            if context.element_type in (_ElementType.TOC, _ElementType.METADATA):
                header_results.append(result)
            else:
                await ARCHIVE_DOMAIN.run(commit_content, element, context)

        async def on_before_submit(value):
            element, mappings, context = value
            if context.element_type == _ElementType.CHAPTER and context.chapter_data is not None:
                outcomes = list(_primary_outcomes(element))
                completion.succeeded += sum(outcomes)
                completion.degraded += sum(not accepted for accepted in outcomes)
            if context.element_type != _ElementType.METADATA:
                return
            context.missing_metadata_tags = tuple(node.get('tag', '') for node in element.iter('field')
                                                 if node.get('__pdf_craft_missing') == '1')
            if on_metadata is None:
                return

            def replacement_fields():
                from copy import deepcopy
                # Copy together so mapping references still address the copied
                # source tree. Presentation writeback keeps the original maps.
                source, replacements = deepcopy((element, mappings))
                replacement = submit_xml(source, SubmitKind.REPLACE, replacements)
                return [field for field in decode_metadata(unwrap_french_quotes(replacement))
                        if field.tag_name not in context.missing_metadata_tags]

            await notify_metadata(await ARCHIVE_DOMAIN.run(replacement_fields))

        async def on_mapping(task, mapping):
            origin, translated = mapping
            context = task.payload
            # Only body tasks produce fragment notifications. Title, TOC and
            # metadata tasks also reach this hook, independently of CFI support.
            if context.element_type != _ElementType.CHAPTER or context.chapter_data is None:
                return
            cfi = None
            if context.locator is not None and context.cfi_package is not None:
                cfi = _source_cfi(context.locator, list(origin), context.cfi_package)
            assert delivery is not None
            await delivery.append(EpubFragmentContent(
                epubcfi=cfi,
                original="".join(segment.text for segment in origin),
                translated="".join(segment.text for segment in translated),
            ))

        async with temporary_directory("pdf-craft-epub-callbacks-") as directory:
            if on_body is not None:
                delivery = DiskDelivery(directory, on_body)
            try:
                await translator._translate_elements(
                    window=window,
                    interrupt_source_text_segments=interrupter.interrupt_source_text_segments,
                    interrupt_translated_text_segments=interrupter.interrupt_translated_text_segments,
                    interrupt_block_element=interrupter.interrupt_block_element,
                    on_translation_event=on_translation_event,
                    tasks=task_stream(),
                    on_result=on_result,
                    collect_results=False,
                    on_task_mapping=on_mapping if on_body is not None else None,
                    on_before_submit=on_before_submit,
                )
                if delivery is not None:
                    await delivery.finish()
            finally:
                if delivery is not None:
                    await delivery.cancel()
        completion.check('EPUB translation')
        await ARCHIVE_DOMAIN.run(write_results, header_results)
    except BaseException as error:
        failure = error
        raise
    finally:
        if archive is not None:
            await ARCHIVE_DOMAIN.run(
                archive.__exit__,
                type(failure) if failure is not None else None,
                failure,
                failure.__traceback__ if failure is not None else None,
            )
def _generate_tasks_from_book(
    zip: Zip,
    toc_list: list,
    toc_context: TocContext,
    metadata_fields: list,
    metadata_context: MetadataContext,
    submit: SubmitKind,
    capture_cfi: bool = False,
) -> Generator[TranslationTask[_ElementContext], None, None]:
    from ....policies import FailureLocation
    from ...._policy import current
    head_submit = submit
    if head_submit == SubmitKind.APPEND_BLOCK:
        head_submit = SubmitKind.APPEND_TEXT

    if toc_list:
        element = encode_toc_list(toc_list)
        yield TranslationTask(
            element=element,
            action=head_submit,
            payload=_ElementContext(element_type=_ElementType.TOC, toc_context=toc_context),
            item_kind=TranslationItemKind.TOC,
            source_locations=tuple(FailureLocation('field', source=current().source,
                member_path=str(toc_context.toc_path), unit_id=str(index), field_path='navigation.title',
                original=item.title) for index, item in enumerate(toc_list)),
            item_id="toc",
            character_count=sum(len(segment.text) for segment in search_text_segments(element)),
        )

    if metadata_fields:
        element = encode_metadata(metadata_fields)
        yield TranslationTask(
            element=element,
            action=head_submit,
            payload=_ElementContext(element_type=_ElementType.METADATA, metadata_context=metadata_context),
            item_kind=TranslationItemKind.METADATA,
            source_locations=tuple(FailureLocation('field', source=current().source,
                member_path=str(metadata_context.opf_path), unit_id=str(index), field_path=field.tag_name,
                original=field.text) for index, field in enumerate(metadata_fields)),
            item_id="metadata",
            character_count=sum(len(segment.text) for segment in search_text_segments(element)),
        )

    packages = spine_cfi_paths(zip) if capture_cfi else {}
    for chapter_path, media_type, xml in read_chapters(zip):
        from ....transformer.xml_translator.xml_translator.source_store import strip_source
        strip_source(xml.element)
        # HTML parser repair can change node positions in a reader. Translate
        # and notify normally, but never manufacture a CFI for that document.
        locator = (ContentCFILocator(xml.element)
                   if capture_cfi and media_type == "application/xhtml+xml" else None)
        body_element = find_first(xml.element, "body")
        title_element = find_first(xml.element, "title")
        if title_element is not None and any(
            segment.text.strip() for segment in search_text_segments(title_element)
        ):
            title_copy = clone_element(title_element)
            title_wrapper = Element("metadata")
            title_wrapper.append(title_copy)
            yield TranslationTask(
                element=title_wrapper,
                action=head_submit,
                payload=_ElementContext(
                    element_type=_ElementType.CHAPTER,
                    chapter_title_data=(chapter_path, xml, title_element),
                    title_only=body_element is None or not any(
                        segment.text.strip() for segment in search_text_segments(body_element)
                    ),
                ),
                item_kind=TranslationItemKind.METADATA,
                source_locations=(FailureLocation('field', source=current().source,
                    member_path=str(chapter_path), unit_id='head/title', field_path='head/title',
                    original=title_element.text, media_type=media_type),),
                item_id=f"{chapter_path}#title",
                character_count=sum(
                    len(segment.text) for segment in search_text_segments(title_wrapper)
                ),
            )
        if body_element is not None and any(
            segment.text.strip() for segment in search_text_segments(body_element)
        ):
            from ...._failure_sources import tag_locations
            from ....transformer.xml_translator.segment import search_inline_segments
            for index, fragment in enumerate(search_inline_segments(search_text_segments(body_element, track_origins=True))):
                cfi = _source_cfi(locator, list(fragment), packages.get(chapter_path))
                location = FailureLocation('fragment', source=current().source,
                    member_path=str(chapter_path), unit_id=f'{chapter_path}:fragment:{index}',
                    channel='body', epubcfi=cfi, media_type=media_type,
                    original=''.join(part.text for part in fragment))
                from ...._failure_sources import element_locations
                tag_locations(fragment.parent, (*element_locations(fragment.parent), location))
            yield TranslationTask(
                element=body_element,
                action=submit,
                payload=_ElementContext(
                    element_type=_ElementType.CHAPTER,
                    chapter_data=(chapter_path, xml),
                    locator=locator,
                    cfi_package=packages.get(chapter_path),
                ),
                item_kind=TranslationItemKind.CHAPTER,
                item_id=str(chapter_path),
                character_count=sum(len(segment.text) for segment in search_text_segments(body_element)),
            )


def _primary_outcomes(element: Element, auxiliary: bool = False) -> Generator[bool, None, None]:
    """Headings, including nested inline owners, cannot stand in for body work."""
    auxiliary = auxiliary or element.tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or "heading" in element.get("role", "").split()
    if not auxiliary and element.get("__pdf_craft_unit") is not None:
        yield element.get("__pdf_craft_missing") != "1"
    for child in element:
        yield from _primary_outcomes(child, auxiliary)


def _get_version() -> str:
    try:
        return get_package_version("epub-translator")
    except Exception:
        return "development"


def _source_cfi(locator, segments, package):
    if locator is None or package is None:
        return None
    # Unsupported media types have no locator. Unexpected locator errors are
    # implementation/parse errors, not permission to invent a missing CFI.
    return locator.capture(segments).cfi(package)
