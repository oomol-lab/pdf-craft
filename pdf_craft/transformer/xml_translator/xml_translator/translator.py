# pylint: disable=unused-argument

from pdf_craft.common.whitespace import normalize_whitespace
from pdf_craft.usage import UsageCollector, adopted_result
from collections.abc import Mapping
from pdf_craft.policies import RetryPolicy, ErrorPolicyInput
from pdf_craft._policy import OMITTED, policy_operation
# pylint: disable=protected-access,unused-argument
from collections.abc import AsyncIterable, Callable, Iterable, Iterator
from contextlib import aclosing
from dataclasses import asdict, dataclass, field
from typing import Any, Generic, TypeVar, cast, TYPE_CHECKING
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
from ...request_batch import request as request_model, with_request_window
from pdf_craft._policy import current, recover
from pdf_craft.policies import FailureCategory, RetryScope
from pdf_craft._failure_sources import tag_locations, segment_locations, task_locations
from .hill_climbing import HillClimbing
from .stream_mapper import InlineSegmentMapping, XMLStreamMapper
from .submitter import SubmitKind, submit
from ...word_count import count_xml_words
from ...source_math import SourceMathTranslation
from ...canonical_projection import MathProjectionError
from ...xml_math import binding_spans, strip_bindings
from ...progress import TranslationProgress, active_progress, selected_item, selected_assets
from pdf_craft.progress import UnitOutcome
from collections import Counter
import asyncio
from pdf_craft._progress import DiskUnits, progress_directory

if TYPE_CHECKING:
    from ...structured_translation import StructuredTranslation

T = TypeVar("T")
SourceTextRenderer = Callable[[list[InlineSegment]], str]
CanonicalTextValidator = Callable[[list[InlineSegment], str, Element], str | None]
CanonicalMathProjector = Callable[[list[InlineSegmentMapping | None], str, Element], list[InlineSegmentMapping | None] | None]


def _already_in_target_language(text: str, target_language: str) -> bool:
    normalized = target_language.strip().casefold().replace("_", "-")
    if normalized not in {
        "zh", "zh-cn", "zh-hans", "chinese", "simplified chinese", "中文", "简体中文",
    }:
        return False
    han_count = sum(is_han_char(char) for char in text)
    latin_count = sum(is_latin_letter(char) for char in text)
    return han_count >= 2 and han_count >= latin_count


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
    async def retain_failure(self, error: Exception, *, stage: str, context: dict[str, Any]) -> None:
        """Retain adapter acceptance evidence in the configured failure directory."""
        from ....llm.failure_log import FailureLog
        log = FailureLog(self._fill_llm.failure_log_path, {'stage': stage, **context})
        if self._fill_llm.key:
            log.secrets.add(self._fill_llm.key)
        try:
            identifier = await log.retain(error, 0)
            if identifier is not None:
                setattr(error, 'failure_log_id', identifier)
        finally:
            await log.close()

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
        translation_executor = translation_executor if translation_executor is not None else executor
        fill_executor = fill_executor if fill_executor is not None else executor
        if translation_executor is None:
            raise ValueError("XML translation requires a translation executor")
        if fill_executor is None:
            raise ValueError("XML translation requires a fill executor")
        self._translation_llm: LLM = translation_llm
        self._fill_llm: LLM = fill_llm
        self._translation_runtime = runtime_for(
            translation_llm,
            translation_executor,
            protocol_version="free-translation-v2",
        )
        self._fill_runtime = runtime_for(
            fill_llm, fill_executor, protocol_version="xml-fill-group-v5",
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
        translation_protocol: "StructuredTranslation | None" = None,
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
            canonical_text_validator=canonical_text_validator, translation_protocol=translation_protocol,
            on_translation_event=on_translation_event,
            total_words=total_words,
            window=window, unit_counts=unit_counts,
        )

    @with_request_window
    async def _translate_elements(
        self,
        tasks: Iterable[TranslationTask[T]] | AsyncIterable[TranslationTask[T]],
        interrupt_source_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None = None,
        interrupt_translated_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None = None,
        interrupt_block_element: Callable[[Element], Element] | None = None,
        immutable_elements_for_inline_segments: Callable[[list[InlineSegment]], list[ImmutableBlockElement]] | None = None,
        source_text_renderer: SourceTextRenderer | None = None,
        canonical_text_validator: CanonicalTextValidator | None = None,
        translation_protocol: "StructuredTranslation | None" = None,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        total_words: int | None = None,
        window: int = 1,
        on_mapping: Callable[[tuple[InlineSegment, list[TextSegment]]], object] | None = None,
        on_result: Callable[[tuple[Element, T]], object] | None = None,
        collect_results: bool = True,
        on_task_result=None,
        ordered_roots=True,
        on_task_mapping=None,
        on_before_submit: Callable[[tuple[Element, list[InlineSegmentMapping], T]], object] | None = None,
        _task_store=None,
        unit_counts=None,
        _progress_path=None,
        _progress_owner=None,
    ) -> list[tuple[Element, T]]:
        if translation_protocol is None and canonical_text_validator is None:
            from ...structured_translation import StructuredTranslation
            translation_protocol = StructuredTranslation()
            source_text_renderer = source_text_renderer or translation_protocol.render
            canonical_text_validator = translation_protocol.validate
        if _progress_path is None and _task_store is None and _progress_owner is None and (
                isinstance(tasks, AsyncIterable) or active_progress() is not None or on_translation_event is not None):
            from pdf_craft.runtime import temporary_directory
            from .source_store import TaskStore
            owner = active_progress() or (TranslationProgress(on_translation_event) if on_translation_event is not None else None)
            storage = (temporary_directory('pdf-craft-task-context-') if isinstance(tasks, AsyncIterable)
                       else progress_directory(owner, 'pdf-craft-progress-'))
            async with storage as directory:
                result = await self._translate_elements(
                    tasks, interrupt_source_text_segments=interrupt_source_text_segments,
                    interrupt_translated_text_segments=interrupt_translated_text_segments,
                    interrupt_block_element=interrupt_block_element,
                    immutable_elements_for_inline_segments=immutable_elements_for_inline_segments,
                    source_text_renderer=source_text_renderer, canonical_text_validator=canonical_text_validator, translation_protocol=translation_protocol,
                    on_translation_event=on_translation_event,
                    total_words=total_words,
                    window=window, on_mapping=on_mapping, on_result=on_result,
                    collect_results=collect_results, on_task_mapping=on_task_mapping,
                    on_task_result=on_task_result, ordered_roots=ordered_roots,
                    on_before_submit=on_before_submit,
                    _task_store=TaskStore(directory) if isinstance(tasks, AsyncIterable) else None,
                    unit_counts=unit_counts, _progress_path=directory, _progress_owner=owner,
                )
            if owner is not None and active_progress() is None:
                await owner.finish()
            return result
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
        progress = active_progress() or _progress_owner
        scope_item = selected_item() if progress is not None else None
        asset_units = selected_assets() if progress is not None else None
        owns_progress = active_progress() is None and on_translation_event is not None
        if owns_progress:
            progress = progress or TranslationProgress(on_translation_event)

            async def prepare_progress():
                if one_pass and (unit_counts is None or total_words is None):
                    raise ValueError('one-pass translation progress requires unit_counts and total_words')
                counts, words = Counter(), 0
                if not one_pass:
                    for task in task_list:
                        counts[task.item_kind or UnitKind.CONTENT] += 1
                        words += task.word_count if task.word_count is not None else count_xml_words(task.element)
                await progress.start(unit_counts if one_pass else counts,
                                     total_words if total_words is not None else words)
            await progress.attempt(prepare_progress)
        # Live units only; async tasks already persist source context on disk.
        live_units = {}
        disk_units = (await progress.attempt(IO_DOMAIN.run, DiskUnits, _progress_path / 'progress.sqlite', progress)
                      if progress is not None and _progress_path is not None else None)
        progress_lock = asyncio.Lock()

        async def save_unit(identity, unit):
            if progress is None or progress.disabled:
                return
            if disk_units is not None:
                await progress.attempt(IO_DOMAIN.run, disk_units.save, identity, unit)
            else:
                live_units[identity] = unit

        async def load_unit(identity):
            if progress is None or progress.disabled:
                return None
            unit = (await progress.attempt(IO_DOMAIN.run, disk_units.load, identity) if disk_units is not None
                    else live_units.get(identity))
            if unit is not None and scope_item is not None:
                return scope_item
            return progress.headers.get(unit.kind, unit) if unit is not None and progress is not None else unit


        async def start_task(task, identity):
            if progress is None or progress.disabled:
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
            if progress is None or progress.disabled:
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

        async def report_round(segments):
            if progress is not None:
                await progress.attempt(round_completed, segments)

        async def translate_round(segments):
            if progress is not None and not progress.disabled and asset_units is None:
                from .source_store import source_identity
                async with progress_lock:
                    for segment in segments:
                        if getattr(segment, '_translation_context', False):
                            continue
                        identity = source_identity(segment.head.root) if _task_store is not None else id(segment.head.root)
                        if await load_unit(identity) is None and not progress.disabled:
                            task = (await IO_DOMAIN.run(_task_store.load, identity)
                                    if _task_store is not None else element2task[identity])
                            await progress.attempt(start_task, task, identity)
            return await self._translate_inline_segments_async(
                inline_segments=segments, callbacks=callbacks,
                immutable_elements_for_inline_segments=immutable_elements_for_inline_segments,
                source_text_renderer=source_text_renderer,
                canonical_text_validator=canonical_text_validator, translation_protocol=translation_protocol,
                on_round=report_round)

        async def empty_task(element):
            from .source_store import source_identity, strip_source
            identity = source_identity(element) if _task_store is not None else id(element)
            task = (await IO_DOMAIN.run(_task_store.pop, identity) if _task_store is not None
                    else element2task.pop(identity))
            # The mapper has consumed the actual projection and proved there
            # is no model work. Settle raw task units here, not at pre-scan.
            async def settle_empty():
                if progress is not None and scope_item is None and asset_units is None:
                    kind = task.item_kind or UnitKind.CONTENT
                    if kind not in progress.headers:
                        unit = await progress.unit(kind, words=(task.word_count
                            if task.word_count is not None else count_xml_words(task.element)))
                        await unit.finish(UnitOutcome.SKIPPED)
            if progress is not None:
                await progress.attempt(settle_empty)
            strip_source(element)
            await invoke_callback(on_task_result, (element, task))

        mapping_stream = stream_mapper.map_stream_async(
            elements=generate_async_elements() if async_tasks is not None else generate_elements(),
            callbacks=callbacks, window=window, progress=progress, ownership=translation_protocol, ordered_roots=ordered_roots,
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
                if disk_units is not None and progress is not None:
                    await progress.attempt(IO_DOMAIN.run, disk_units.delete, identity)
                else:
                    live_units.pop(identity, None)
                if unit is not None:
                    if task.outcomes and not all(task.outcomes.values()):
                        unit.outcome = UnitOutcome.FALLBACK
                    if scope_item is None and progress is not None and unit not in progress.headers.values():
                        await unit.finish()
                strip_source(translated_element)
                await invoke_callback(on_task_result, (translated_element, task))
                result = (translated_element, task.payload)
                await invoke_callback(on_result, result)
                if collect_results:
                    results.append(result)

        if owns_progress and progress is not None and _progress_owner is None:
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
        on_round=None, translation_protocol: "StructuredTranslation | None" = None,
    ) -> list[InlineSegmentMapping | None]:
        group = inline_segments
        mappings: list[InlineSegmentMapping | None] = []
        primary = [segment for segment in group if not segment._translation_context]
        if not primary:
            return [None] * len(group)
        overlap = [segment for segment in group if segment._translation_context]
        context = '\n\n'.join(part for part in (
            translation_protocol.context(primary)
            if translation_protocol is not None and translation_protocol.context else '',
            ''.join(self._render_source_text_parts(overlap))) if part)
        source_math = translation_protocol.source_math(primary) if translation_protocol is not None else None
        result = await self._translate_inline_segment_group_async(
            inline_segments=primary,
            callbacks=callbacks,
            immutable_elements=(
                immutable_elements_for_inline_segments(primary)
                if immutable_elements_for_inline_segments is not None else []
            ),
            source_text_renderer=((lambda segments, math=source_math: translation_protocol.render(segments, math))
                                  if source_math is not None and translation_protocol is not None
                                  else source_text_renderer),
            canonical_text_validator=canonical_text_validator,
            source_context=context,
            canonical_math_projector=(
                (lambda mappings, canonical, response, math=source_math:
                 translation_protocol.project_math(mappings, canonical, math, response, primary))
                if translation_protocol is not None else None),
            source_math=source_math,
            require_nonempty_units=bool(translation_protocol and translation_protocol.require_nonempty_units),
            allow_direct_fill=(translation_protocol is not None
                               and translation_protocol.is_text_unit(translation_protocol.unit(primary[0]))),
        )
        if translation_protocol is not None and all(mapping is not None for mapping in result):
            for segment in primary:
                translation_protocol.accepted_unit(translation_protocol.unit(segment))
        if on_round is not None:
            await on_round(group)
        results = iter(result)
        mappings.extend(None if segment._translation_context else next(results) for segment in group)
        return mappings

    @adopted_result(lambda mappings: any(mapping is not None for mapping in mappings))
    async def _translate_inline_segment_group_async(
        self,
        inline_segments: list[InlineSegment],
        callbacks: Callbacks,
        immutable_elements: list[ImmutableBlockElement],
        source_text_renderer: SourceTextRenderer | None,
        canonical_text_validator: CanonicalTextValidator | None,
        source_context: str = '',
        canonical_math_projector: CanonicalMathProjector | None = None,
        source_math: SourceMathTranslation | None = None,
        require_nonempty_units: bool = False,
        allow_direct_fill: bool = False,
    ) -> list[InlineSegmentMapping | None]:
        primary = [segment for segment in inline_segments if not getattr(segment, '_translation_context', False)]
        if not primary:
            return [None] * len(inline_segments)
        if not any(normalize_whitespace(text.text, trim=True) for segment in primary for text in segment):
            # Unicode whitespace needs no translation. Clone rather than strip
            # it so special spaces, inline nodes and empty block anchors survive;
            # read-only context must not turn a blank primary unit into work.
            return [(segment.parent, [text.clone() for text in segment]) for segment in inline_segments]
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
            if source_math is not None:
                context = '\n\n'.join(value for value in (source_context, source_math.context) if value)
                translated_text = await self._translate_text_async(source_text, context=context)
            else:
                translated_text = (await self._translate_text_async(source_text, context=source_context)
                                   if source_context else await self._translate_text_async(source_text))
        except Exception as error:
            await recover(error, FailureCategory.TRANSLATION, 'text_translation',
                          'omit_translation', targets=targets, context_locations=context_targets,
                          backend='llm', model=getattr(self._translation_llm, 'model', None))
            return [None] * len(inline_segments)
        try:
            projected = await self._request_and_submit_async(
                hill_climbing=hill_climbing, source_text=source_text,
                translated_text=translated_text, callbacks=callbacks,
                failure_locations=targets,
                canonical_text_validator=(
                    (lambda element, canonical=translated_text: canonical_text_validator(inline_segments, canonical, element))
                    if canonical_text_validator is not None else None
                ),
                canonical_math_projector=canonical_math_projector,
                allow_direct_fill=allow_direct_fill, source_math=source_math,
                require_nonempty_units=require_nonempty_units,
            )
        except Exception as error:
            await recover(error, FailureCategory.FILL, 'xml_fill', 'omit_translation',
                          targets=targets, context_locations=context_targets, backend='llm', model=getattr(self._fill_llm, 'model', None))
            return [None] * len(inline_segments)
        if projected is not None:
            return projected
        mappings: list[InlineSegmentMapping | None] = []
        for index, mapping in enumerate(hill_climbing.gen_mappings()):
            if mapping and not mapping[1]:
                if canonical_text_validator is not None:
                    # An accepted empty artist allocation is an empty result,
                    # not the failure sentinel that restores untranslated text.
                    block = mapping[0]
                    mapping = (block, [TextSegment('', [block], 0, 0, 1, next(iter(inline_segments[index])).position)])
                else:
                    mapping = None
            mappings.append(mapping)
        return mappings

    def _render_source_text_parts(self, inline_segments: list[InlineSegment]):
        for i, inline_segment in enumerate(inline_segments):
            if i > 0:
                yield "\n\n"
            for text_segment in inline_segment:
                yield text_segment.text

    async def _translate_text_async(self, text: str, *, context: str = "") -> str:
        if not normalize_whitespace(text, trim=True) or _already_in_target_language(text, self._target_language):
            return text
        template = await self._translation_runtime.template("translate")
        messages = [Message(MessageRole.SYSTEM, template.render(
            target_language=self._target_language, user_prompt=self._user_prompt)),
            Message(MessageRole.USER, (await self._translation_runtime.template('translation_input')).render(
                text=text, context=context))]
        async with self._translation_runtime.context(cache_seed_content=self._cache_seed_content) as ctx:
            return await request_model(ctx, messages, allow_empty=True)

    async def _request_and_submit_async(
        self,
        hill_climbing: HillClimbing,
        source_text: str,
        translated_text: str,
        callbacks: Callbacks,
        canonical_text_validator: Callable[[Element, str], str | None] | None = None,
        failure_locations: tuple = (),
        canonical_math_projector: CanonicalMathProjector | None = None,
        require_nonempty_units: bool = False,
        allow_direct_fill: bool = False,
        source_math: SourceMathTranslation | None = None,
    ) -> list[InlineSegmentMapping | None] | None:
        from ...canonical_math import CanonicalMath
        canonical_math = CanonicalMath(translated_text) if canonical_text_validator is not None else None

        def validate_candidate(element):
            projected = None
            error = canonical_math.expand(element, require_complete=False) if canonical_math is not None else None
            if error is None:
                try:
                    binding_spans(element, source_math)
                except MathProjectionError as failure:
                    error = str(failure)
            if error is None:
                error = hill_climbing.validate(strip_bindings(element))
            if error is None and canonical_text_validator is not None:
                error = canonical_text_validator(element, translated_text)
            if error is None and canonical_math_projector is not None:
                try:
                    projected = canonical_math_projector(
                        hill_climbing.candidate_mappings(strip_bindings(element)), translated_text, element)
                except MathProjectionError as projection_error:
                    error = str(projection_error)
            if error is None:
                error = hill_climbing.submit(strip_bindings(element))
            return error, projected

        if (allow_direct_fill or translated_text == '') and canonical_text_validator is not None and not getattr(source_math, 'values', {}):
            for candidate in self._direct_fill_candidates(hill_climbing, translated_text):
                error, projected = validate_candidate(candidate)
                if error is None:
                    return projected

        user_message = (await self._fill_runtime.template('fill_input')).render(
            source_text=source_text, xml_template=encode_friendly(hill_climbing.request_element()),
            translated_text=canonical_math.encode() if canonical_math is not None else translated_text,
            math_instructions=canonical_math.instructions() if canonical_math is not None else '',
            source_formulas=source_math.encode() if source_math is not None else encode_friendly(Element('source-formulas')))
        canonical_instructions = ((await self._fill_runtime.template('canonical_fill')).render(has_source_math=bool(getattr(source_math, 'values', {})), require_nonempty_units=require_nonempty_units)
                                  if canonical_math is not None else '')
        fill_template = await self._fill_runtime.template("fill")
        fixed_messages = [
            Message(MessageRole.SYSTEM, fill_template.render() + "\n\n" + canonical_instructions),
            Message(MessageRole.USER, user_message),
        ]
        # Budget a complete group XML, including stable IDs and formula annotations.
        # Source grouping remains bounded; retries share this same output allowance.
        encoding = await self._fill_runtime.encoding()
        output_budget = max(4096, len(encoding.encode(encode_friendly(hill_climbing.request_element())))
                            + 2 * len(encoding.encode(translated_text))
                            + 32 * len(getattr(source_math, 'values', {})) + 512)
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
                        error, projected = validated, None
                        history = None
                    else:
                        # Encode the original parsed candidate before validators
                        # expand references or recover its engineering text.
                        history = encode_friendly(validated)
                        error, projected = validate_candidate(validated)
                    if error is None:
                        last_error = None
                        return ProtocolSuccess(projected, state)
                    last_error = error
                    return ProtocolRetry(error, state, include_response=history is not None,
                                         response_for_history=history, reset_history=True)

                async def empty(self, state, attempt: int, max_attempts: int):
                    nonlocal last_error
                    error = "LLM returned an empty XML response. Please return one complete <xml> block."
                    last_error = error
                    return ProtocolRetry(error, state)

                async def exhausted(self, state, attempts: int, response: str | None):
                    error = last_error or "XML fill exhausted retries; no usable response was produced."
                    raise XMLFillError(error, attempts=attempts)

            return await run_repair_loop(RepairLoopOptions(
                messages=fixed_messages,
                request=lambda current, index, maximum: request_model(
                    llm_context, current, retry_index=index, retry_max=maximum, use_cache=False,
                    max_tokens=output_budget,
                ),
                protocol=_XMLProtocol(),
                state=None,
                max_attempts=current().retry.attempts(RetryScope.FILL),
                failure_log_path=getattr(self._fill_llm, 'failure_log_path', None),
                failure_log_context={'stage': 'xml_fill', 'model': self._fill_llm.model,
                    'targets': [asdict(location) for location in failure_locations]},
            ))

    @staticmethod
    def _direct_fill_candidates(hill_climbing: HillClimbing, canonical: str) -> Iterable[Element]:
        # Preserve existing inline and source-slot ownership when the original
        # structure already contains the exact canonical text. The caller must
        # still validate the complete text and formula projection before submit.
        candidate = hill_climbing.request_element()
        if canonical == '':
            # An accepted empty translation has one possible allocation even
            # across multiple styled slots: keep every element/attribute and
            # clear its text. Validate and submit through the ordinary path;
            # never ask the engineer to translate the source again.
            for node in candidate.iter():
                node.text = node.tail = None
            yield candidate
            return
        yield candidate
        # One leaf block has exactly one allocation. Insert XML text, never
        # parse canonical prose as markup or guess boundaries between fields.
        if len(candidate) == 1 and not len(candidate[0]) and candidate[0].get('id') is not None:
            candidate = hill_climbing.request_element()
            candidate[0].text = canonical
            yield candidate
        elif len(candidate) == 1 and candidate[0].get('id') is not None and all(
                child.tag == 'expression' and not len(child) for child in candidate[0]):
            # A single owner with only formula allocation containers has no
            # remaining prose ownership decision. Keep every required container
            # empty and insert the complete canonical text in the owner. The
            # normal validator/projector must still restore each source by its
            # accepted engineering identity, never by these physical slots.
            candidate = hill_climbing.request_element()
            candidate[0].text = canonical
            for child in candidate[0]:
                child.text = child.tail = None
            yield candidate

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
        self.failure_log_id: str | None = None
