# pylint: disable=unused-argument

from pdf_craft.usage import UsageCollector, adopted_result
from collections.abc import Mapping
from pdf_craft.policies import RetryPolicy, ErrorPolicyInput
from pdf_craft._policy import OMITTED, policy_operation
# pylint: disable=protected-access,unused-argument
from collections.abc import AsyncIterable, Callable, Iterable, Iterator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Generic, TypeVar, cast
from xml.etree.ElementTree import Element

from pdf_craft.concurrency import AsyncExecutor

from pdf_craft.llm import LLM, Message, MessageRole, runtime_for
from pdf_craft.language import is_han_char, is_latin_letter
from pdf_craft.llm.loop import ProtocolRetry, ProtocolSuccess, RepairLoopOptions, run_repair_loop
from pdf_craft.runtime import invoke_callback, IO_DOMAIN
from pdf_craft.transformer.xml_translator.segment import (
    BlockSegment, ImmutableBlockElement, InlineSegment, TextSegment,
)
from pdf_craft.progress import ProgressEvent, UnitKind
from pdf_craft.transformer.xml_translator.xml import decode_friendly, encode_friendly
from .callbacks import Callbacks, warp_callbacks
from pdf_craft._policy import current, recover
from pdf_craft.policies import FailureCategory, RetryScope
from pdf_craft._failure_sources import tag_locations, segment_locations, task_locations
from .hill_climbing import HillClimbing
from .stream_mapper import InlineSegmentMapping, XMLStreamMapper
from .submitter import SubmitKind, submit
from ...word_count import count_xml_words
from ...progress import TranslationProgress, active_progress, selected_item, selected_assets
from pdf_craft.progress import UnitOutcome
from collections import Counter
import asyncio
from pdf_craft._progress import DiskUnits

T = TypeVar("T")
SourceTextRenderer = Callable[[list[InlineSegment]], str]
CanonicalTextValidator = Callable[[list[InlineSegment], str, Element], str | None]


def _already_in_target_language(text: str, target_language: str) -> bool:
    normalized = target_language.strip().casefold().replace("_", "-")
    if normalized not in {
        "zh", "zh-cn", "zh-hans", "chinese", "simplified chinese", "中文", "简体中文",
    }:
        return False
    han_count = sum(is_han_char(char) for char in text)
    latin_count = sum(is_latin_letter(char) for char in text)
    return han_count >= 2 and han_count >= latin_count


def _groups_by_source_unit_owner(
    inline_segments: list[InlineSegment],
) -> list[list[InlineSegment]]:
    """Split PCEX canonical-validation work at logical ``<text>`` owners.

    This helper is deliberately activated only for the optional canonical
    validation path.  The ordinary XML translator keeps its generic batching
    semantics, while PCEX gets one first-stage translation result per
    TextFlowItem and therefore never infers ownership from translated prose.
    """
    groups: list[list[InlineSegment]] = []
    previous_owner: Element | None = None
    for segment in inline_segments:
        owner = next(
            (element for element in segment.head.parent_stack if element.tag == "text"),
            segment.parent,
        )
        if owner is not previous_owner:
            groups.append([])
            previous_owner = owner
        groups[-1].append(segment)
    return groups


@dataclass
class TranslationTask(Generic[T]):
    element: Element
    action: SubmitKind
    payload: T
    item_kind: UnitKind | None = None
    item_id: str | int | None = None
    word_count: int | None = None
    source_locations: tuple = ()
    outcomes: dict[str, bool] = field(default_factory=dict)


class XMLTranslator:
    def __init__(
        self,
        translation_llm: LLM,
        fill_llm: LLM,
        target_language: str,
        user_prompt: str | None,
        max_fill_displaying_errors: int,
        max_group_score: int,
        cache_seed_content: str | None = None,
        executor: AsyncExecutor | None = None,
        translation_executor: AsyncExecutor | None = None,
        fill_executor: AsyncExecutor | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = None,
    ) -> None:
        self._retry_policy = retry_policy or RetryPolicy()
        self._error_policy = error_policy
        translation_executor = translation_executor or executor
        fill_executor = fill_executor or executor
        if translation_executor is None:
            raise ValueError("XML translation requires a translation executor")
        if fill_executor is None:
            raise ValueError("XML translation requires a fill executor")
        self._translation_llm: LLM = translation_llm
        self._fill_llm: LLM = fill_llm
        self._translation_runtime = runtime_for(
            translation_llm,
            translation_executor,
            protocol_version="xml-translation-v1",
        )
        self._fill_runtime = runtime_for(
            fill_llm, fill_executor, protocol_version="xml-fill-v1",
        )
        self._target_language: str = target_language
        self._user_prompt: str | None = user_prompt
        self._max_fill_displaying_errors: int = max_fill_displaying_errors
        self._cache_seed_content: str | None = cache_seed_content
        self._max_group_score = max_group_score
        self._stream_mapper: XMLStreamMapper | None = None

    @property
    def target_language(self) -> str:
        return self._target_language

    async def _stream_mapper_async(self) -> XMLStreamMapper:
        if self._stream_mapper is None:
            self._stream_mapper = XMLStreamMapper(
                encoding=await self._translation_runtime.encoding(),
                max_group_score=self._max_group_score,
            )
        return self._stream_mapper

    @policy_operation
    async def translate_element(
        self,
        task: TranslationTask[T],
        window: int = 1,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED, *,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
        **kwargs,
    ) -> tuple[Element, T]:
        translated = await self.translate_elements(
            tasks=(task,),
            window=window,
            **kwargs,
        )
        if translated:
            return translated[0]
        raise RuntimeError("Translation failed unexpectedly")

    @policy_operation
    async def translate_elements(
        self,
        tasks: Iterable[TranslationTask[T]] | AsyncIterable[TranslationTask[T]],
        interrupt_source_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None = None,
        interrupt_translated_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None = None,
        interrupt_block_element: Callable[[Element], Element] | None = None,
        immutable_elements_for_inline_segments: Callable[[list[InlineSegment]], list[ImmutableBlockElement]] | None = None,
        source_text_renderer: SourceTextRenderer | None = None,
        canonical_text_validator: CanonicalTextValidator | None = None,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        total_words: int | None = None,
        window: int = 1, *,
        unit_counts: Mapping[UnitKind, int] | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> list[tuple[Element, T]]:
        """Translate tasks using the public XML extension contract."""
        return await self._translate_elements(
            tasks=tasks,
            interrupt_source_text_segments=interrupt_source_text_segments,
            interrupt_translated_text_segments=interrupt_translated_text_segments,
            interrupt_block_element=interrupt_block_element,
            immutable_elements_for_inline_segments=immutable_elements_for_inline_segments,
            source_text_renderer=source_text_renderer,
            canonical_text_validator=canonical_text_validator,
            on_translation_event=on_translation_event,
            total_words=total_words,
            window=window, unit_counts=unit_counts,
        )

    async def _translate_elements(
        self,
        tasks: Iterable[TranslationTask[T]] | AsyncIterable[TranslationTask[T]],
        interrupt_source_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None = None,
        interrupt_translated_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None = None,
        interrupt_block_element: Callable[[Element], Element] | None = None,
        immutable_elements_for_inline_segments: Callable[[list[InlineSegment]], list[ImmutableBlockElement]] | None = None,
        source_text_renderer: SourceTextRenderer | None = None,
        canonical_text_validator: CanonicalTextValidator | None = None,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        total_words: int | None = None,
        window: int = 1,
        on_mapping: Callable[[tuple[InlineSegment, list[TextSegment]]], object] | None = None,
        on_result: Callable[[tuple[Element, T]], object] | None = None,
        collect_results: bool = True,
        on_task_mapping=None,
        on_before_submit: Callable[[tuple[Element, list[InlineSegmentMapping], T]], object] | None = None,
        _task_store=None,
        unit_counts=None,
        _progress_path=None,
    ) -> list[tuple[Element, T]]:
        if _progress_path is None and _task_store is None and (
                isinstance(tasks, AsyncIterable) or active_progress() is not None or on_translation_event is not None):
            from pdf_craft.runtime import temporary_directory
            from .source_store import TaskStore
            async with temporary_directory('pdf-craft-task-context-') as directory:
                return await self._translate_elements(
                    tasks, interrupt_source_text_segments=interrupt_source_text_segments,
                    interrupt_translated_text_segments=interrupt_translated_text_segments,
                    interrupt_block_element=interrupt_block_element,
                    immutable_elements_for_inline_segments=immutable_elements_for_inline_segments,
                    source_text_renderer=source_text_renderer, canonical_text_validator=canonical_text_validator,
                    on_translation_event=on_translation_event,
                    total_words=total_words,
                    window=window, on_mapping=on_mapping, on_result=on_result,
                    collect_results=collect_results, on_task_mapping=on_task_mapping,
                    on_before_submit=on_before_submit,
                    _task_store=TaskStore(directory) if isinstance(tasks, AsyncIterable) else None,
                    unit_counts=unit_counts, _progress_path=directory,
                )
        if window < 1:
            raise ValueError("window must be at least 1")
        element2task: dict[int, TranslationTask[T]] = {}
        callbacks = warp_callbacks(
            interrupt_source_text_segments=interrupt_source_text_segments,
            interrupt_translated_text_segments=interrupt_translated_text_segments,
            interrupt_block_element=interrupt_block_element,
        )
        async_tasks = tasks if isinstance(tasks, AsyncIterable) else None
        task_list = () if async_tasks is not None else cast(Iterable[TranslationTask[T]], tasks)
        one_pass = async_tasks is not None or isinstance(tasks, Iterator)
        progress = active_progress()
        scope_item = selected_item() if progress is not None else None
        asset_units = selected_assets() if progress is not None else None
        owns_progress = progress is None and on_translation_event is not None
        if owns_progress:
            if one_pass and (unit_counts is None or total_words is None):
                raise ValueError('one-pass translation progress requires unit_counts and total_words')
            progress = TranslationProgress(on_translation_event)
            counts, words = Counter(), 0
            if not one_pass:
                for task in task_list:
                    counts[task.item_kind or UnitKind.CONTENT] += 1
                    words += task.word_count if task.word_count is not None else count_xml_words(task.element)
            await progress.start(unit_counts if one_pass else counts,
                                 total_words if total_words is not None else words)
        # Live units only; async tasks already persist source context on disk.
        live_units = {}
        disk_units = (await IO_DOMAIN.run(DiskUnits, _progress_path / 'progress.sqlite', progress)
                      if progress is not None and _progress_path is not None else None)
        progress_lock = asyncio.Lock()

        async def save_unit(identity, unit):
            if disk_units is not None:
                await IO_DOMAIN.run(disk_units.save, identity, unit)
            else:
                live_units[identity] = unit

        async def load_unit(identity):
            unit = (await IO_DOMAIN.run(disk_units.load, identity) if disk_units is not None
                    else live_units.get(identity))
            if unit is not None and scope_item is not None:
                return scope_item
            return progress.headers.get(unit.kind, unit) if unit is not None and progress is not None else unit


        async def start_task(task, identity):
            if progress is None:
                return
            kind = task.item_kind or UnitKind.CONTENT
            if scope_item is not None:
                await save_unit(identity, scope_item)
            elif kind in progress.headers:
                await save_unit(identity, progress.headers[kind])
            else:
                await save_unit(identity, await progress.unit(kind, words=(
                    task.word_count if task.word_count is not None else count_xml_words(task.element))))

        def generate_elements():
            for task in task_list:
                tag_locations(task.element, task_locations(task))
                element2task[id(task.element)] = task
                yield task.element

        async def generate_async_elements():
            assert async_tasks is not None
            try:
                async for task in async_tasks:
                    from .source_store import tag_source
                    assert _task_store is not None
                    identity = _task_store.allocate()
                    tag_locations(task.element, task_locations(task))
                    tag_source(task.element, identity)
                    await IO_DOMAIN.run(_task_store.save, identity, task)
                    yield task.element
            finally:
                close = getattr(async_tasks, "aclose", None)
                if close is not None:
                    await close()

        results: list[tuple[Element, T]] = []
        stream_mapper = await self._stream_mapper_async()
        async def mapping_callback(mapping):
            from .source_store import source_identity
            origin, _ = mapping
            task = (await IO_DOMAIN.run(_task_store.load, source_identity(origin.head.root))
                    if _task_store is not None else element2task[id(origin.head.root)])
            if on_task_mapping is not None:
                await on_task_mapping(task, mapping)
            await invoke_callback(on_mapping, mapping)

        async def round_completed(segments):
            if progress is None:
                return
            from .source_store import source_identity
            async with progress_lock:
                by_unit = {}
                for segment in segments:
                    if getattr(segment, '_translation_context', False):
                        continue
                    if asset_units is not None:
                        slot = next((node.get('translation_slot') for node in segment.head.parent_stack
                                     if node.tag == 'asset'), None)
                        unit = asset_units.get(slot) if slot is not None else None
                        if unit is None:
                            # Context-only blocks never carry asset work weights.
                            if segment._progress_words:
                                raise RuntimeError('asset source words have no logical unit')
                            continue
                        identity = None
                    else:
                        identity = source_identity(segment.head.root) if _task_store is not None else id(segment.head.root)
                        unit = await load_unit(identity)
                        assert unit is not None
                    key = unit if asset_units is not None else identity
                    if key not in by_unit:
                        by_unit[key] = [unit, 0]
                    by_unit[key][1] += segment._progress_words
                for key, (unit, words) in by_unit.items():
                    await unit.progress(words=words)
                    if asset_units is None:
                        await save_unit(key, unit)

        async def translate_round(segments):
            if progress is not None and asset_units is None:
                from .source_store import source_identity
                async with progress_lock:
                    for segment in segments:
                        if getattr(segment, '_translation_context', False):
                            continue
                        identity = source_identity(segment.head.root) if _task_store is not None else id(segment.head.root)
                        if await load_unit(identity) is None:
                            task = (await IO_DOMAIN.run(_task_store.load, identity)
                                    if _task_store is not None else element2task[identity])
                            await start_task(task, identity)
            return await self._translate_inline_segments_async(
                inline_segments=segments, callbacks=callbacks,
                immutable_elements_for_inline_segments=immutable_elements_for_inline_segments,
                source_text_renderer=source_text_renderer,
                canonical_text_validator=canonical_text_validator,
                on_round=round_completed)

        async def empty_task(element):
            from .source_store import source_identity, strip_source
            identity = source_identity(element) if _task_store is not None else id(element)
            task = (await IO_DOMAIN.run(_task_store.pop, identity) if _task_store is not None
                    else element2task.pop(identity))
            # The mapper has consumed the actual projection and proved there
            # is no model work. Settle raw task units here, not at pre-scan.
            if progress is not None and scope_item is None and asset_units is None:
                kind = task.item_kind or UnitKind.CONTENT
                if kind not in progress.headers:
                    unit = await progress.unit(kind, words=(task.word_count
                        if task.word_count is not None else count_xml_words(task.element)))
                    await unit.finish(UnitOutcome.SKIPPED)
            strip_source(element)

        mapping_stream = stream_mapper.map_stream_async(
            elements=generate_async_elements() if async_tasks is not None else generate_elements(),
            callbacks=callbacks, window=window, track_progress=progress is not None,
            on_mapping=mapping_callback if on_mapping is not None or on_task_mapping is not None else None,
            map=translate_round, on_empty=empty_task,
        )
        async with aclosing(mapping_stream):
            async for element, mappings in mapping_stream:
                from .source_store import source_identity, strip_source
                task = (await IO_DOMAIN.run(_task_store.pop, source_identity(element))
                        if _task_store is not None else element2task.pop(id(element), None))
                if task is None:
                    continue
                from .source_store import owner_outcomes
                task.outcomes.update(owner_outcomes(element, mappings))
                if on_before_submit is not None:
                    await invoke_callback(on_before_submit, (element, mappings, task.payload))
                identity = source_identity(element) if _task_store is not None else id(element)
                strip_source(element)
                translated_element = submit(
                    element=element, action=task.action, mappings=mappings,
                )
                unit = await load_unit(identity)
                if disk_units is not None:
                    await IO_DOMAIN.run(disk_units.delete, identity)
                else:
                    live_units.pop(identity, None)
                if unit is not None:
                    if task.outcomes and not all(task.outcomes.values()):
                        unit.outcome = UnitOutcome.FALLBACK
                    if scope_item is None and progress is not None and unit not in progress.headers.values():
                        await unit.finish()
                strip_source(translated_element)
                result = (translated_element, task.payload)
                await invoke_callback(on_result, result)
                if collect_results:
                    results.append(result)

        if owns_progress and progress is not None:
            await progress.finish()
        return results

    async def _translate_inline_segments_async(
        self,
        inline_segments: list[InlineSegment],
        callbacks: Callbacks,
        immutable_elements_for_inline_segments: Callable[
            [list[InlineSegment]], list[ImmutableBlockElement]
        ] | None,
        source_text_renderer: SourceTextRenderer | None,
        canonical_text_validator: CanonicalTextValidator | None,
        on_round=None,
    ) -> list[InlineSegmentMapping | None]:
        segment_groups = (
            _groups_by_source_unit_owner(inline_segments)
            if canonical_text_validator is not None else [inline_segments]
        )
        mappings: list[InlineSegmentMapping | None] = []
        for group in segment_groups:
            mappings.extend(await self._translate_inline_segment_group_async(
                inline_segments=group,
                callbacks=callbacks,
                immutable_elements=(
                    immutable_elements_for_inline_segments(group)
                    if immutable_elements_for_inline_segments is not None else []
                ),
                source_text_renderer=source_text_renderer,
                canonical_text_validator=canonical_text_validator,
            ))
            if on_round is not None:
                await on_round(group)
        return mappings

    @adopted_result(lambda mappings: any(mapping is not None for mapping in mappings))
    async def _translate_inline_segment_group_async(
        self,
        inline_segments: list[InlineSegment],
        callbacks: Callbacks,
        immutable_elements: list[ImmutableBlockElement],
        source_text_renderer: SourceTextRenderer | None,
        canonical_text_validator: CanonicalTextValidator | None,
    ) -> list[InlineSegmentMapping | None]:
        primary = [segment for segment in inline_segments if not getattr(segment, '_translation_context', False)]
        if not primary:
            return [None] * len(inline_segments)
        context_targets = segment_locations([segment for segment in inline_segments
                                             if getattr(segment, '_translation_context', False)])
        hill_climbing = HillClimbing(
            encoding=await self._fill_runtime.encoding(),
            max_fill_displaying_errors=self._max_fill_displaying_errors,
            block_segment=BlockSegment(
                root_tag="xml",
                inline_segments=inline_segments,
                immutable_elements=immutable_elements,
            ),
        )
        source_text = (
            source_text_renderer(inline_segments)
            if source_text_renderer is not None
            else "".join(self._render_source_text_parts(inline_segments))
        )
        targets = segment_locations(primary)
        try:
            translated_text = await self._translate_text_async(source_text)
        except Exception as error:
            await recover(error, FailureCategory.TRANSLATION, 'text_translation',
                          'omit_translation', targets=targets, context_locations=context_targets,
                          backend='llm', model=getattr(self._translation_llm, 'model', None))
            return [None] * len(inline_segments)
        try:
            await self._request_and_submit_async(
                hill_climbing=hill_climbing, source_text=source_text,
                translated_text=translated_text, callbacks=callbacks,
                canonical_text_validator=(
                    (lambda element: canonical_text_validator(inline_segments, translated_text, element))
                    if canonical_text_validator is not None else None
                ),
            )
        except Exception as error:
            await recover(error, FailureCategory.FILL, 'xml_fill', 'omit_translation',
                          targets=targets, context_locations=context_targets, backend='llm', model=getattr(self._fill_llm, 'model', None))
            return [None] * len(inline_segments)
        mappings: list[InlineSegmentMapping | None] = []
        for mapping in hill_climbing.gen_mappings():
            if mapping and not mapping[1]:
                mapping = None
            mappings.append(mapping)
        return mappings

    def _render_source_text_parts(self, inline_segments: list[InlineSegment]):
        for i, inline_segment in enumerate(inline_segments):
            if i > 0:
                yield "\n\n"
            for text_segment in inline_segment:
                yield text_segment.text

    async def _translate_text_async(self, text: str) -> str:
        if _already_in_target_language(text, self._target_language):
            return text
        template = await self._translation_runtime.template("translate")
        async with self._translation_runtime.context(
            cache_seed_content=self._cache_seed_content
        ) as ctx:
            return await ctx.request(input=[
                Message(
                    role=MessageRole.SYSTEM,
                    message=template.render(
                        target_language=self._target_language,
                        user_prompt=self._user_prompt,
                    ),
                ),
                Message(role=MessageRole.USER, message=text),
            ])

    async def _request_and_submit_async(
        self,
        hill_climbing: HillClimbing,
        source_text: str,
        translated_text: str,
        callbacks: Callbacks,
        canonical_text_validator: Callable[[Element], str | None] | None = None,
    ) -> None:
        from ...canonical_math import CanonicalMath
        canonical_math = CanonicalMath(translated_text) if canonical_text_validator is not None else None
        user_message = (
            f"Source text:\n{source_text}\n\n"
            f"XML template:\n```XML\n{encode_friendly(hill_climbing.request_element())}\n```\n\n"
            f"Translated text:\n{canonical_math.text if canonical_math is not None else translated_text}"
        )
        if canonical_text_validator is not None:
            user_message += (
                "\n\nCanonical visible-text constraint:\n"
                "For each PCEX <text> unit, concatenating all visible text in its "
                "fragment slots must reproduce the corresponding canonical "
                "translation above after the caller's shared whitespace normalization "
                "and unambiguous recovery. Preserve words, punctuation and special "
                "spaces; normal cosmetic whitespace can be reconstructed by the caller. "
                "<anchor .../> nodes are zero-width structural "
                "tokens: do not add a space or any character on either side of one."
                " Inline <expression> nodes are ordinary text slots like <span>: "
                "fill them with the corresponding canonical translated text, even "
                "when it uses plain symbols or prose instead of source LaTeX. "
                "The caller restores original formulas after this fill loop; do "
                "not copy source formula spelling when it differs from the "
                "canonical translation. If a required inline <expression> has "
                "no corresponding characters in the canonical translation, "
                "keep that expression tag and its exact ID with empty contents "
                "at its corresponding position (for example <expression id=\"5\"/>). "
                "Do not append source formula text to make the slot nonempty: "
                "the caller restores its original formula even from an empty slot. "
                "Preserve all required tags and IDs."
            )
            assert canonical_math is not None
            user_message += canonical_math.instructions()
        fill_template = await self._fill_runtime.template("fill")
        fixed_messages = [
            Message(MessageRole.SYSTEM, fill_template.render()),
            Message(MessageRole.USER, user_message),
        ]
        async with self._fill_runtime.context(
            cache_seed_content=self._cache_seed_content
        ) as llm_context:
            translator = self
            last_error: str | None = None

            class _XMLProtocol:
                async def validate(self, response: str, state, attempt: int, max_attempts: int):
                    nonlocal last_error
                    validated = translator._extract_xml_element(response)
                    if isinstance(validated, str):
                        error = validated
                    elif canonical_math is not None and (reference_error := canonical_math.expand(validated)):
                        error = reference_error
                    elif canonical_text_validator is None:
                        error = hill_climbing.validate(validated)
                        if error is None:
                            error = hill_climbing.submit(validated)
                    else:
                        error = hill_climbing.validate(validated)
                        if error is None:
                            error = canonical_text_validator(validated)
                            if error is not None and canonical_math is not None:
                                error += canonical_math.instructions()
                        if error is None:
                            error = hill_climbing.submit(validated)
                    if error is None:
                        last_error = None
                        return ProtocolSuccess(None, state)
                    last_error = error
                    return ProtocolRetry(error, state, include_response=True, reset_history=True)

                async def empty(self, state, attempt: int, max_attempts: int):
                    nonlocal last_error
                    error = "LLM returned an empty XML response. Please return one complete <xml> block."
                    last_error = error
                    return ProtocolRetry(error, state)

                async def exhausted(self, state, attempts: int, response: str | None):
                    error = last_error or "XML fill exhausted retries; no usable response was produced."
                    raise XMLFillError(error, attempts=attempts)

            await run_repair_loop(RepairLoopOptions(
                messages=fixed_messages,
                request=lambda current, index, maximum: llm_context.request(
                    current, retry_index=index, retry_max=maximum, use_cache=False,
                ),
                protocol=_XMLProtocol(),
                state=None,
                max_attempts=current().retry.attempts(RetryScope.FILL),
            ))

    def _extract_xml_element(self, text: str) -> Element | str:
        first_xml_element: Element | None = None
        all_xml_elements: int = 0

        for xml_element in decode_friendly(text, tags="xml"):
            if first_xml_element is None:
                first_xml_element = xml_element
            all_xml_elements += 1

        if first_xml_element is None:
            return "No complete <xml>...</xml> block found. Please ensure you have properly closed the XML with </xml> tag."  # noqa: E501

        if all_xml_elements > 1:
            return (
                f"Found {all_xml_elements} <xml>...</xml> blocks. "
                "Please return only one XML block without any examples or explanations."
            )
        return first_xml_element


class XMLFillError(Exception):
    """No complete, validated structure after the business repair budget."""
    def __init__(self, message: str, *, attempts: int):
        super().__init__(message)
        self.attempts = attempts
