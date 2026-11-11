# pylint: disable=unused-argument,protected-access

from pdf_craft.usage import UsageCollector, adopted_result
from collections.abc import Mapping
from pdf_craft.concurrency import settle_tasks
from pdf_craft.policies import RetryPolicy, ErrorPolicyInput, FailureLocation
from pdf_craft._policy import OMITTED, policy_operation
import asyncio
from pdf_craft._failure_sources import chapter_locations, tag_locations

from collections.abc import Callable, Iterable
from typing import Any, Protocol, cast
from xml.etree.ElementTree import Element

from pdf_craft.extractor.chapter.chapter import (
    Chapter, DisplayFormula, InlineExpression, SourceTextFragment, TextFlowItem,
    decode, encode,
    search_references_in_chapter,
)
from pdf_craft.markdown.paragraph import flatten
from pdf_craft.transformer.xml_translator.segment import ImmutableBlockElement, InlineSegment, SourceAnchorError
from pdf_craft.transformer.xml_translator.xml import clone_element
from pdf_craft.progress import ProgressEvent, UnitKind
from .word_count import count_xml_words
from .progress import task_word_count, translation_unit, active_progress
from .chapter_formula_interrupter import ChapterFormulaInterrupter
from .structured_translation import StructuredTranslation
from .anchored_xml import AnchoredContentXMLTransformer
from .furniture_xml import FurnitureXMLTransformer, AsyncXMLTaskTranslator as FurnitureXMLTaskTranslator
from .xml_translator.xml_translator import SubmitKind, TranslationTask


class AsyncXMLTaskTranslator(Protocol):
    async def translate_element(
        self, task: TranslationTask[Chapter], **kwargs
    ) -> tuple[Element, Chapter]: ...


class AsyncXMLBatchTranslator(AsyncXMLTaskTranslator, Protocol):
    async def translate_elements(
        self, tasks: Iterable[TranslationTask[Any]], **kwargs,
    ) -> list[tuple[Element, Any]]: ...


def _metadata_element(
    metadata: dict[str, Any],
) -> tuple[Element, list[tuple[str, str, int | None]]]:
    root = Element("metadata")
    fields: list[tuple[str, str, int | None]] = []
    for key in (
        "title", "original_title", "description", "publisher", "edition", "rights",
    ):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            child = Element(key)
            child.text = value
            root.append(child)
            fields.append((key, key, None))
    subjects = metadata.get("subjects")
    if isinstance(subjects, list):
        for index, value in enumerate(subjects):
            if isinstance(value, str) and value.strip():
                child = Element("subject")
                child.text = value
                root.append(child)
                fields.append(("subject", "subjects", index))
    return root, fields


class NarrativeXMLTransformer:
    """Adapt a format-neutral XML translator to the Chapter transformer protocol."""
    def __init__(
        self,
        translator: AsyncXMLTaskTranslator,
        mode: SubmitKind = SubmitKind.REPLACE,
        *,
        window: int = 1,
    ) -> None:
        if window < 1:
            raise ValueError("window must be at least 1")
        self._translator = translator
        self._mode = mode
        self._window = window

    @property
    def supports_stream(self) -> bool:
        """Whether the supplied translator supports incremental task admission."""
        from .xml_translator.xml_translator import XMLTranslator
        return isinstance(self._translator, XMLTranslator)

    @property
    def window(self) -> int:
        """Maximum concurrent requests in the translation stream."""
        return self._window

    @property
    def mode(self) -> SubmitKind:
        return self._mode

    @property
    def target_language(self) -> str | None:
        """Target language advertised by the underlying translation runtime."""
        value = getattr(self._translator, "target_language", None)
        if value is None:
            value = getattr(self._translator, "_target_language", None)
        return value if isinstance(value, str) else None

    def with_mode(self, mode: SubmitKind) -> "NarrativeXMLTransformer":
        """Return a transformer using the requested XML submission mode."""
        return NarrativeXMLTransformer(self._translator, mode, window=self._window)

    def _furniture_transformer(self) -> "FurnitureXMLTransformer":
        """Build the private furniture adapter over this XML translation runtime."""
        return FurnitureXMLTransformer(
            cast(FurnitureXMLTaskTranslator, self._translator), self._mode,
        )

    def _anchored_transformer(self) -> AnchoredContentXMLTransformer:
        """Build the private anchored-content adapter over this runtime."""
        return AnchoredContentXMLTransformer(cast(Any, self._translator), self._mode)

    @policy_operation
    @translation_unit(UnitKind.METADATA, lambda self, metadata, **kwargs: count_xml_words(_metadata_element(metadata)[0]))
    async def translate_metadata(
        self, metadata: dict[str, Any], *, on_metadata=None,
        target_language: str | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> dict[str, Any]:
        """Finalize metadata once for every consumer of the translation flow."""
        overlay = await self._translate_metadata_fields(metadata)
        language = target_language or self.target_language
        if language:
            overlay["language"] = language
        from ..document.metadata import notify_metadata_translation
        await notify_metadata_translation(metadata, overlay, on_metadata)
        return overlay

    @adopted_result()
    async def _translate_metadata_fields(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Translate human-readable bibliographic fields into a layer overlay."""
        element, fields = _metadata_element(metadata)
        if not fields:
            return {}
        # Collections are adopted atomically. Keep their model calls separate
        # from scalar fields so withdrawing one cannot retain its usage or
        # discard consumption belonging to an independently accepted field.
        overlay: dict[str, Any] = {}
        groups = dict.fromkeys(key if index is not None else None for _, key, index in fields)
        for group_key in groups:
            group = Element("metadata")
            group_fields = []
            for child, field in zip(element, fields):
                _, key, index = field
                if (key if index is not None else None) == group_key:
                    group.append(child)
                    group_fields.append(field)
            overlay.update(await self._translate_metadata_group(group, group_fields))
        return overlay

    @adopted_result(bool)
    async def _translate_metadata_group(
        self, element: Element, fields: list[tuple[str, str, int | None]],
    ) -> dict[str, Any]:
        from .xml_translator.xml_translator.source_store import NODE_KEY
        field_keys = []
        for child, (_, key, index) in zip(element, fields):
            identity = f'metadata:{key}:{index}'
            child.set(NODE_KEY, identity)
            field_keys.append(identity)
            tag_locations(child, (FailureLocation(
                'field', unit_id=identity, field_path=f'{key}[{index}]' if index is not None else key,
                original=child.text,
            ),))
        task = TranslationTask(
            element=element,
            action=SubmitKind.REPLACE,
            payload=Chapter(None, -1, []),
            source_locations=tuple(FailureLocation(
                'field', unit_id=f'metadata:{key}:{index}', field_path=f'{key}[{index}]' if index is not None else key)
                for _, key, index in fields),
            item_kind=UnitKind.METADATA,
            item_id="metadata",
            word_count=task_word_count(element),
        )
        translated, _ = await self._translator.translate_element(
            task,
        )
        children = list(translated)
        if len(children) != len(fields) or any(
            child.tag != tag for child, (tag, _, _) in zip(children, fields)
        ):
            raise ValueError("translated metadata does not preserve field structure")
        overlay: dict[str, Any] = {}
        missing_collections = set()
        for child, (_, key, index), identity in zip(children, fields, field_keys):
            if task.outcomes.get(identity) is False:
                if index is not None:
                    missing_collections.add(key)
                continue
            value = child.text or ""
            if index is None:
                overlay[key] = value
            else:
                values = overlay.setdefault(key, [])
                values.append(value)
        # A list overlay replaces the source list: commit it only when every
        # requested member is trustworthy, preserving the original otherwise.
        for key in missing_collections:
            overlay.pop(key, None)
        return overlay

    @policy_operation
    @translation_unit(UnitKind.CONTENT, lambda self, chapter, **kwargs: self.source_word_count(chapter))
    @adopted_result()
    async def transform(
        self,
        chapter: Chapter,
        *,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        item_id: str | int | None = None,
        total_words: int | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> Chapter:
        element = encode(chapter)
        if not self.has_translatable_content(chapter):
            return chapter
        _prepare_chapter_references(element)
        anchors = _NarrativeAnchorProjection(element)
        anchors.replace_assets()
        protocol = _narrative_protocol()
        async_translator = cast(AsyncXMLTaskTranslator, self._translator)
        task = TranslationTask(
                element=element,
                action=self._mode,
                payload=chapter,
                source_locations=chapter_locations(chapter),
                item_kind=UnitKind.CONTENT,
                item_id=item_id if item_id is not None else chapter.id,
                word_count=task_word_count(element),
        )
        translated, _ = await protocol.translate(
            async_translator, task,
            on_translation_event=on_translation_event,
            total_words=total_words,
            immutable_elements_for_inline_segments=anchors.immutable_elements_for_inline_segments,
            window=self._window,
        )
        anchors.restore_assets(translated)
        _restore_chapter_references(translated)
        result = decode(translated)
        from .translation_coverage import apply_outcomes
        apply_outcomes(result, task.outcomes)
        return result

    @policy_operation
    async def transform_many(
        self,
        chapters: Iterable[tuple[Chapter, str | int]], *,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> list[Chapter]:
        """Translate all chapter groups through one book-wide XML window."""
        if not hasattr(self._translator, "translate_elements"):
            pending = [
                asyncio.create_task(self.transform(chapter, item_id=item_id))
                for chapter, item_id in chapters
            ]
            try:
                return list(await asyncio.gather(*pending))
            except BaseException:
                for task in pending:
                    task.cancel()
                await settle_tasks(pending)
                raise

        prepared: list[
            tuple[Chapter, Element, _NarrativeAnchorProjection | None]
        ] = []
        tasks: list[TranslationTask[int]] = []
        anchors_by_root: dict[int, _NarrativeAnchorProjection] = {}
        protocol = _narrative_protocol()
        for index, (chapter, item_id) in enumerate(chapters):
            if not self.has_translatable_content(chapter):
                prepared.append((chapter, Element("empty"), None))
                continue
            element = encode(chapter)
            _prepare_chapter_references(element)
            anchors = _NarrativeAnchorProjection(element)
            anchors.replace_assets()
            prepared.append((chapter, element, anchors))
            anchors_by_root[id(element)] = anchors
            tasks.append(TranslationTask(
                element=element,
                action=self._mode,
                payload=index,
                source_locations=chapter_locations(chapter),
                item_kind=UnitKind.CONTENT,
                item_id=item_id,
                word_count=task_word_count(element),
            ))

        if not tasks:
            return [chapter for chapter, _, _ in prepared]

        def immutable_elements(
            inline_segments: list[InlineSegment],
        ) -> list[ImmutableBlockElement]:
            if not inline_segments:
                return []
            return _group_anchors(inline_segments, anchors_by_root)

        async_translator = cast(AsyncXMLBatchTranslator, self._translator)
        translated = await protocol.translate_many(
            async_translator, tasks,
            window=self._window,
            immutable_elements_for_inline_segments=immutable_elements,
        )
        results = [chapter for chapter, _, _ in prepared]
        tasks_by_index = {task.payload: task for task in tasks}
        for translated_element, index in translated:
            anchors = prepared[index][2]
            assert anchors is not None
            anchors.restore_assets(translated_element)
            _restore_chapter_references(translated_element)
            results[index] = decode(translated_element)
            from .translation_coverage import apply_outcomes
            apply_outcomes(results[index], tasks_by_index[index].outcomes)
            progress = active_progress()
            if not self.supports_stream and progress is not None and progress.started:
                words = progress.attempt_sync(self.source_word_count, prepared[index][0])
                if words:
                    unit = await progress.unit(UnitKind.CONTENT, words=words)
                    await unit.progress(1)
                    from ..progress import UnitOutcome
                    await unit.finish(UnitOutcome.FALLBACK if any(
                        not accepted for accepted in tasks_by_index[index].outcomes.values()) else UnitOutcome.PROCESSED)
        return results

    async def _transform_stream(self, chapters, on_result):
        """Project a bounded, shared content stream and settle each source unit."""
        from .xml_translator.xml_translator import XMLTranslator
        assert isinstance(self._translator, XMLTranslator)
        protocol = _narrative_protocol()
        anchors_by_root = {}
        prepared = {}

        async def tasks():
            try:
                async for chapter, identity in chapters:
                    if not self.has_translatable_content(chapter):
                        await on_result(chapter, identity)
                        continue
                    element, anchors = _project_narrative(chapter, protocol)
                    anchors_by_root[id(element)] = anchors
                    prepared[identity] = (element, anchors)
                    yield TranslationTask(element, self._mode, identity,
                        source_locations=chapter_locations(chapter), item_kind=UnitKind.CONTENT,
                        item_id=str(identity), word_count=task_word_count(element))
            finally:
                await chapters.aclose()

        def immutable(segments):
            if not segments:
                return []
            return _group_anchors(segments, anchors_by_root)

        async def result(value):
            element, task = value
            source, anchors = prepared.pop(task.payload)
            anchors_by_root.pop(id(source))
            assert isinstance(protocol.formulas, ChapterFormulaInterrupter)
            protocol.formulas.release(source)
            protocol.finish(element)
            anchors.restore_assets(element)
            _restore_chapter_references(element)
            chapter = decode(element)
            from .translation_coverage import apply_outcomes
            apply_outcomes(chapter, task.outcomes)
            await on_result(chapter, task.payload)

        await self._translator._translate_elements(
            tasks(), **protocol.arguments(), window=self._window,
            immutable_elements_for_inline_segments=immutable,
            collect_results=False, on_task_result=result, ordered_roots=False)

    @staticmethod
    def source_word_count(chapter: Chapter) -> int:
        """Count the NarrativeFlow payload, excluding anchored asset text."""
        element, _ = _project_narrative(chapter, _narrative_protocol())
        return count_xml_words(element)

    @staticmethod
    def has_translatable_content(chapter: Chapter) -> bool:
        """Return whether narrative text or a display formula needs translation.

        Embedded image/table metadata deliberately does not make a chapter a
        NarrativeFlow translation task. It belongs to the independent
        AnchoredContent stage.
        """
        flows = [chapter.flow_items]
        flows.extend(reference.flow_items for reference in search_references_in_chapter(chapter))
        for flow_items in flows:
            for item in flow_items:
                if isinstance(item, TextFlowItem):
                    if any(
                        _content_has_text(child.content)
                        for child in item.children
                        if isinstance(child, SourceTextFragment)
                    ):
                        return True
                elif isinstance(item, DisplayFormula) and any(
                    _content_has_text(content)
                    for content in (item.asset.title, item.asset.content, item.asset.caption)
                ):
                    return True
        return False


def _prepare_chapter_references(chapter: Element) -> None:
    """Declare PCEX reference semantics at the format-neutral XML boundary.

    Definition-level ``references/ref`` nodes remain blocks. Only the empty
    reference occurrences inside fragments are inline translation tokens;
    attribute assignment restores their original PCEX IDs after template IDs.
    """
    for reference in chapter.findall(".//fragment/ref"):
        reference.set("display", "inline")
    # PCEX marks are fields of reference definitions, not HTML highlights.
    # Treating them as inline makes the definition a replacement peak and
    # promotes its untranslated (possibly empty) flow to the references list.
    for mark in chapter.findall("./references/ref/mark"):
        mark.set("display", "block")


def _restore_chapter_references(chapter: Element) -> None:
    """Remove the translator-only display hint before decoding PCEX."""
    for reference in chapter.findall(".//fragment/ref"):
        reference.attrib.pop("display", None)
    for mark in chapter.findall("./references/ref/mark"):
        mark.attrib.pop("display", None)


def _narrative_protocol() -> StructuredTranslation:
    return StructuredTranslation(owner=_source_unit_owner, identity=_source_unit_identity,
                                 is_text_unit=lambda owner: owner.tag == 'text',
                                 formulas=ChapterFormulaInterrupter())


def _source_unit_identity(owner: Element) -> str:
    fragment = owner.find('fragment') if owner.tag == 'text' else None
    if fragment is not None:
        return f'{fragment.get("page_index")}:{fragment.get("fragment_id")}'
    return owner.get('__pdf_craft_stream_node') or str(id(owner))


def _source_unit_owner(inline_segment) -> Element:
    """Return a PCEX TextFlowItem wrapper, or a safe independent fallback."""
    for element in inline_segment.head.parent_stack:
        if element.tag == "text":
            return element
    # Display formulas and other non-text XML content remain hard boundaries.
    return inline_segment.parent


_ANCHOR_TAG = "anchor"
_ANCHOR_KEY = "anchor_key"


class _NarrativeAnchorProjection:
    """Temporarily substitute embedded assets with opaque translation anchors.

    XMLTranslator's existing ID/template validation keeps these marker nodes
    structurally stable through its repair loop.  The source asset is restored
    only after that validated round trip, so no image/table field can leak into
    the NarrativeFlow prompt or be overwritten by its result.
    """

    def __init__(self, root: Element) -> None:
        self._root = root
        self._assets: list[tuple[str, Element, Element]] = []
        self._standalone_assets: list[Element] = []

    def replace_assets(self) -> None:
        for parent in self._root.iter():
            for child in list(parent):
                if (
                    parent.tag != "text"
                    or child.tag != "asset"
                    or child.get("ref") not in {"image", "table"}
                ):
                    continue
                key = str(len(self._assets))
                anchor = Element(_ANCHOR_TAG, {_ANCHOR_KEY: key})
                anchor.tail = child.tail
                index = list(parent).index(child)
                parent.remove(child)
                parent.insert(index, anchor)
                self._assets.append((key, clone_element(child), parent))

        # Standalone assets are not paragraph positions.  Keep their wrapper
        # in the flow but detach their textual fields from NarrativeFlow.
        # Unlike a nested asset, no <anchor> is fabricated for them.
        for wrapper in self._root.iter("standalone-asset"):
            asset = wrapper.find("asset")
            if asset is None or asset.get("ref") not in {"image", "table"}:
                continue
            self._standalone_assets.append(clone_element(asset))
            wrapper.remove(asset)
            wrapper.append(Element("asset", asset.attrib))

    def restore_assets(self, translated: Element) -> None:
        expected = [key for key, _, _ in self._assets]
        found: list[tuple[Element, Element, str]] = []
        for parent in translated.iter():
            for child in parent:
                if child.tag == _ANCHOR_TAG:
                    key = child.get(_ANCHOR_KEY)
                    if key is not None:
                        found.append((parent, child, key))

        found_keys = [key for _, _, key in found]
        if found_keys != expected:
            raise ValueError(
                "Narrative translation changed anchored-content structure; "
                "expected immutable anchor order "
                f"{expected}, got {found_keys}"
            )

        for (parent, anchor, _), (_, asset, _) in zip(found, self._assets, strict=True):
            index = list(parent).index(anchor)
            restored = clone_element(asset)
            restored.tail = anchor.tail
            parent.remove(anchor)
            parent.insert(index, restored)

        standalone_wrappers = list(translated.iter("standalone-asset"))
        if len(standalone_wrappers) != len(self._standalone_assets):
            raise ValueError("Narrative translation changed standalone asset structure")
        for wrapper, asset in zip(standalone_wrappers, self._standalone_assets, strict=True):
            placeholder = wrapper.find("asset")
            if placeholder is None:
                raise ValueError("Narrative translation removed standalone asset placeholder")
            index = list(wrapper).index(placeholder)
            wrapper.remove(placeholder)
            wrapper.insert(index, clone_element(asset))

    def immutable_elements_for_inline_segments(
        self, inline_segments: list[InlineSegment],
    ) -> list[ImmutableBlockElement]:
        """Expose only relevant self-closing anchors to XML fill validation.

        The XML stream mapper may split a chapter into token-sized groups.  An
        anchor is therefore included in every group touching its owning text
        flow.  This keeps it opaque while letting the standard repair loop
        reject a missing, renamed, duplicated, or reordered token.
        """
        # This is a source-program invariant, not feedback for the fill model.
        # A lost/duplicated source token must not silently disappear from the
        # contract merely because its owner has no text in this group.
        expected = [(key, parent) for key, _, parent in self._assets]
        found = [(child.get(_ANCHOR_KEY), parent) for parent in self._root.iter()
                 for child in parent if child.tag == _ANCHOR_TAG]
        if found != expected:
            raise SourceAnchorError('Narrative source anchor mapping is missing, duplicated or misplaced')
        result: list[ImmutableBlockElement] = []
        for key, anchor, parent in self._assets:
            assert anchor.tag == "asset"  # the stored clone is the source asset
            source_anchor = next(
                (
                    child for child in parent
                    if child.tag == _ANCHOR_TAG and child.get(_ANCHOR_KEY) == key
                ),
                None,
            )
            if source_anchor is None:
                raise SourceAnchorError(f'Narrative source anchor is missing: {key!r}')
            source_anchor_index = list(parent).index(source_anchor)
            segment_children: list[tuple[int, int]] = []
            for segment_index, segment in enumerate(inline_segments):
                direct_child = _direct_child_of(parent, segment.parent_stack)
                if direct_child is not None:
                    segment_children.append((segment_index, list(parent).index(direct_child)))
            if not segment_children:
                continue
            before = next(
                (segment_index for segment_index, child_index in segment_children
                 if child_index > source_anchor_index),
                # The owning flow can end before another flow/chapter in the
                # same request. Its trailing asset belongs immediately after
                # its last selected block, never at the end of the whole group.
                segment_children[-1][0] + 1,
            )
            result.append(ImmutableBlockElement(source_anchor, before))
        return result


def _group_anchors(segments, anchors_by_root) -> list[ImmutableBlockElement]:
    """Allocate request-local identities without mutating chapter asset keys.

    Both anchor_key and stream node numbers are chapter-local. Only these
    template copies cross that boundary; the original tokens still restore
    assets in their own chapter after the translated text is submitted.
    """
    roots = list(dict.fromkeys(id(segment.head.root) for segment in segments))
    result = []
    for owner, root in enumerate(roots):
        for item in anchors_by_root[root].immutable_elements_for_inline_segments(segments):
            anchor = clone_element(item.element)
            anchor.set(_ANCHOR_KEY, f'{owner}:{anchor.get(_ANCHOR_KEY)}')
            result.append(ImmutableBlockElement(anchor, item.before_block_index))
    return result


def _direct_child_of(parent: Element, stack: list[Element]) -> Element | None:
    """Find the immediate member of ``parent`` containing a text segment."""
    for index, element in enumerate(stack):
        if element is parent and index + 1 < len(stack):
            return stack[index + 1]
    return None


def _content_has_text(content) -> bool:
    """Return whether a structured text field has visible source content."""
    return any(
        (isinstance(part, str) and part.strip())
        or (isinstance(part, InlineExpression) and part.content.strip())
        for part in flatten(content)
    )


def _project_narrative(chapter, protocol):
    """The same prepared source projection for streaming execution and estimates."""
    element = encode(chapter)
    _prepare_chapter_references(element)
    anchors = _NarrativeAnchorProjection(element)
    anchors.replace_assets()
    protocol.prepare(element)
    return element, anchors
