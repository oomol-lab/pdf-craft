# pylint: disable=protected-access,unused-argument
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from typing import Generic, TypeVar
from xml.etree.ElementTree import Element

from pdf_craft.llm import LLM, Message, MessageRole, runtime_for
from pdf_craft.llm.loop import ProtocolRetry, ProtocolSuccess, RepairLoopOptions, run_repair_loop
from pdf_craft.transformer.xml_translator.segment import (
    BlockSegment, ImmutableBlockElement, InlineSegment, TextSegment,
)
from pdf_craft.transformer.events import TranslationEvent, TranslationEventKind, TranslationItemKind
from pdf_craft.transformer.xml_translator.xml import decode_friendly, encode_friendly
from .callbacks import Callbacks, FillFailedEvent, warp_callbacks
from .hill_climbing import HillClimbing
from .stream_mapper import InlineSegmentMapping, XMLStreamMapper
from .submitter import SubmitKind, submit

T = TypeVar("T")
SourceTextRenderer = Callable[[list[InlineSegment]], str]
CanonicalTextValidator = Callable[[list[InlineSegment], str, Element], str | None]


@dataclass
class TranslationTask(Generic[T]):
    element: Element
    action: SubmitKind
    payload: T
    item_kind: TranslationItemKind | None = None
    item_id: str | int | None = None
    character_count: int | None = None


class XMLTranslator:
    def __init__(
        self,
        translation_llm: LLM,
        fill_llm: LLM,
        target_language: str,
        user_prompt: str | None,
        ignore_translated_error: bool,
        max_retries: int,
        max_fill_displaying_errors: int,
        max_group_score: int,
        cache_seed_content: str | None = None,
    ) -> None:
        self._translation_llm: LLM = translation_llm
        self._fill_llm: LLM = fill_llm
        self._translation_runtime = runtime_for(translation_llm, protocol_version="xml-translation-v1")
        self._fill_runtime = runtime_for(fill_llm, protocol_version="xml-fill-v1")
        self._target_language: str = target_language
        self._user_prompt: str | None = user_prompt
        self._ignore_translated_error: bool = ignore_translated_error
        self._max_retries: int = max_retries
        self._max_fill_displaying_errors: int = max_fill_displaying_errors
        self._cache_seed_content: str | None = cache_seed_content
        self._stream_mapper: XMLStreamMapper = XMLStreamMapper(
            encoding=translation_llm.encoding,
            max_group_score=max_group_score,
        )

    def translate_element(
        self,
        task: TranslationTask[T],
        concurrency: int = 1,
        interrupt_source_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None = None,
        interrupt_translated_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None = None,
        interrupt_block_element: Callable[[Element], Element] | None = None,
        immutable_elements_for_inline_segments: Callable[[list[InlineSegment]], list[ImmutableBlockElement]] | None = None,
        source_text_renderer: SourceTextRenderer | None = None,
        canonical_text_validator: CanonicalTextValidator | None = None,
        on_fill_failed: Callable[[FillFailedEvent], None] | None = None,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
        completed_characters: int = 0,
        total_characters: int | None = None,
        emit_scope_events: bool = True,
        emit_item_events: bool = True,
    ) -> tuple[Element, T]:
        translated_elements = self.translate_elements(
            tasks=((task),),
            concurrency=concurrency,
            interrupt_source_text_segments=interrupt_source_text_segments,
            interrupt_translated_text_segments=interrupt_translated_text_segments,
            interrupt_block_element=interrupt_block_element,
            immutable_elements_for_inline_segments=immutable_elements_for_inline_segments,
            source_text_renderer=source_text_renderer,
            canonical_text_validator=canonical_text_validator,
            on_fill_failed=on_fill_failed,
            on_translation_event=on_translation_event,
            completed_characters=completed_characters,
            total_characters=total_characters,
            emit_scope_events=emit_scope_events,
            emit_item_events=emit_item_events,
        )
        translated = next(translated_elements, None)
        if translated is not None:
            # Exhaust the generator so scope completion is delivered to the
            # callback even for this single-task convenience method.
            for _ in translated_elements:
                pass
            return translated

        raise RuntimeError("Translation failed unexpectedly")

    def translate_elements(
        self,
        tasks: Iterable[TranslationTask[T]],
        concurrency: int = 1,
        interrupt_source_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None = None,
        interrupt_translated_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None = None,
        interrupt_block_element: Callable[[Element], Element] | None = None,
        immutable_elements_for_inline_segments: Callable[[list[InlineSegment]], list[ImmutableBlockElement]] | None = None,
        source_text_renderer: SourceTextRenderer | None = None,
        canonical_text_validator: CanonicalTextValidator | None = None,
        on_fill_failed: Callable[[FillFailedEvent], None] | None = None,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
        completed_characters: int = 0,
        total_characters: int | None = None,
        emit_scope_events: bool = True,
        emit_item_events: bool = True,
    ) -> Generator[tuple[Element, T], None, None]:
        element2task: dict[int, TranslationTask[T]] = {}
        callbacks = warp_callbacks(
            interrupt_source_text_segments=interrupt_source_text_segments,
            interrupt_translated_text_segments=interrupt_translated_text_segments,
            interrupt_block_element=interrupt_block_element,
            on_fill_failed=on_fill_failed,
        )

        task_list = list(tasks)
        total = total_characters
        if total is None:
            total = sum(task.character_count or 0 for task in task_list)
        if on_translation_event is not None and emit_scope_events:
            chapter_count = sum(
                task.item_kind == TranslationItemKind.CHAPTER for task in task_list
            )
            on_translation_event(TranslationEvent(
                kind=TranslationEventKind.START,
                chapter_count=chapter_count,
                has_toc=any(task.item_kind == TranslationItemKind.TOC for task in task_list),
                has_metadata=any(task.item_kind == TranslationItemKind.METADATA for task in task_list),
                total_characters=total,
                completed_characters=completed_characters,
            ))

        def generate_elements():
            for task in task_list:
                element2task[id(task.element)] = task
                if on_translation_event is not None and emit_item_events and task.item_kind is not None:
                    on_translation_event(TranslationEvent(
                        kind=TranslationEventKind.ITEM_START,
                        item_kind=task.item_kind,
                        item_id=task.item_id,
                        item_completed_characters=0,
                        item_total_characters=task.character_count or 0,
                        total_characters=total,
                    ))
                yield task.element

        for element, mappings in self._stream_mapper.map_stream(
            elements=generate_elements(),
            callbacks=callbacks,
            concurrency=concurrency,
            map=lambda inline_segments: self._translate_inline_segments(
                inline_segments=inline_segments,
                callbacks=callbacks,
                immutable_elements=(
                    immutable_elements_for_inline_segments(inline_segments)
                    if immutable_elements_for_inline_segments is not None else []
                ),
                source_text_renderer=source_text_renderer,
                canonical_text_validator=canonical_text_validator,
            ),
        ):
            task = element2task.get(id(element), None)
            if task:
                translated_element = submit(
                    element=element,
                    action=task.action,
                    mappings=mappings,
                )
                if on_translation_event is not None and task.item_kind is not None:
                    completed_characters += task.character_count or 0
                    on_translation_event(TranslationEvent(
                        kind=TranslationEventKind.PROGRESS,
                        item_kind=task.item_kind,
                        item_id=task.item_id,
                        item_completed_characters=task.character_count or 0,
                        item_total_characters=task.character_count or 0,
                        completed_characters=completed_characters,
                        total_characters=total,
                    ))
                    if emit_item_events:
                        on_translation_event(TranslationEvent(
                            kind=TranslationEventKind.ITEM_COMPLETE,
                            item_kind=task.item_kind,
                            item_id=task.item_id,
                            item_completed_characters=task.character_count or 0,
                            item_total_characters=task.character_count or 0,
                            completed_characters=completed_characters,
                            total_characters=total,
                        ))
                yield translated_element, task.payload

        if on_translation_event is not None and emit_scope_events:
            on_translation_event(TranslationEvent(
                kind=TranslationEventKind.COMPLETE,
                completed_characters=completed_characters,
                total_characters=total,
            ))

    def _translate_inline_segments(
        self,
        inline_segments: list[InlineSegment],
        callbacks: Callbacks,
        immutable_elements: list[ImmutableBlockElement],
        source_text_renderer: SourceTextRenderer | None,
        canonical_text_validator: CanonicalTextValidator | None,
    ) -> list[InlineSegmentMapping | None]:
        hill_climbing = HillClimbing(
            encoding=self._fill_llm.encoding,
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
        translated_text = self._translate_text(source_text)

        self._request_and_submit(
            hill_climbing=hill_climbing,
            source_text=source_text,
            translated_text=translated_text,
            callbacks=callbacks,
            canonical_text_validator=(
                lambda element: canonical_text_validator(
                    inline_segments, translated_text, element,
                )
                if canonical_text_validator is not None else None
            ),
        )
        mappings: list[InlineSegmentMapping | None] = []
        for mapping in hill_climbing.gen_mappings():
            if mapping:
                _, text_segments = mapping
                if not text_segments:
                    mapping = None
            mappings.append(mapping)

        return mappings

    def _render_source_text_parts(self, inline_segments: list[InlineSegment]):
        for i, inline_segment in enumerate(inline_segments):
            if i > 0:
                yield "\n\n"
            for text_segment in inline_segment:
                yield text_segment.text

    def _translate_text(self, text: str) -> str:
        with self._translation_runtime.context(cache_seed_content=self._cache_seed_content) as ctx:
            return ctx.request(
                input=[
                    Message(
                        role=MessageRole.SYSTEM,
                        message=self._translation_llm.template("translate").render(
                            target_language=self._target_language,
                            user_prompt=self._user_prompt,
                        ),
                    ),
                    Message(role=MessageRole.USER, message=text),
                ]
            )

    def _request_and_submit(
        self,
        hill_climbing: HillClimbing,
        source_text: str,
        translated_text: str,
        callbacks: Callbacks,
        canonical_text_validator: Callable[[Element], str | None] | None = None,
    ) -> None:
        user_message = (
            f"Source text:\n{source_text}\n\n"
            f"XML template:\n```XML\n{encode_friendly(hill_climbing.request_element())}\n```\n\n"
            f"Translated text:\n{translated_text}"
        )
        if canonical_text_validator is not None:
            user_message += (
                "\n\nCanonical visible-text constraint:\n"
                "For each PCEX <text> unit, concatenating all visible text in its "
                "fragment slots must exactly reproduce the corresponding canonical "
                "translation above. <anchor .../> nodes are zero-width structural "
                "tokens: do not add a space or any character on either side of one."
            )
        fixed_messages: list[Message] = [
            Message(
                role=MessageRole.SYSTEM,
                message=self._fill_llm.template("fill").render(),
            ),
            Message(
                role=MessageRole.USER,
                message=user_message,
            ),
        ]
        with self._fill_runtime.context(cache_seed_content=self._cache_seed_content) as llm_context:
            translator = self
            last_error: str | None = None
            class _XMLProtocol:
                def validate(self, response: str, state, attempt: int, max_attempts: int):
                    nonlocal last_error
                    validated = translator._extract_xml_element(response)
                    if isinstance(validated, str):
                        error = validated
                    elif canonical_text_validator is None:
                        # Preserve the generic XMLTranslator path exactly: it
                        # lets HillClimbing both validate and record partial
                        # improvements in one operation.
                        error = hill_climbing.submit(validated)
                    else:
                        error = hill_climbing.validate(validated)
                        if error is None:
                            error = canonical_text_validator(validated)
                        if error is None:
                            error = hill_climbing.submit(validated)
                    if error is None:
                        last_error = None
                        return ProtocolSuccess(None, state)
                    last_error = error
                    callbacks.on_fill_failed(FillFailedEvent(error, attempt + 1, False))
                    return ProtocolRetry(error, state, include_response=True, reset_history=True)

                def empty(self, state, attempt: int, max_attempts: int):
                    nonlocal last_error
                    error = "LLM returned an empty XML response. Please return one complete <xml> block."
                    last_error = error
                    callbacks.on_fill_failed(FillFailedEvent(error, attempt + 1, False))
                    return ProtocolRetry(error, state)

                def exhausted(self, state, attempts: int, response: str | None):
                    error = last_error or "XML fill exhausted retries; no usable response was produced."
                    callbacks.on_fill_failed(FillFailedEvent(
                        error,
                        attempts, True,
                    ))
                    return None

            run_repair_loop(RepairLoopOptions(
                messages=fixed_messages,
                request=lambda current, index, maximum: llm_context.request(
                    current, retry_index=index, retry_max=maximum, use_cache=False),
                protocol=_XMLProtocol(), state=None,
                max_attempts=max(1, self._max_retries),
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
