# pylint: disable=unused-argument
"""XMLTranslator adapter for the independent AnchoredContent stage."""

from __future__ import annotations

from copy import deepcopy

from pdf_craft.usage import UsageCollector, adopted_result
from collections.abc import Mapping

from pdf_craft.policies import RetryPolicy, ErrorPolicyInput
from pdf_craft._policy import OMITTED, policy_operation

from collections.abc import Sequence
from typing import Any, Protocol
from xml.etree.ElementTree import Element, tostring

from pdf_craft.extractor.chapter.chapter import Chapter, Reference, SourceAsset, StandaloneAsset, decode, encode
from pdf_craft.markdown.paragraph import HTMLTag, flatten
from pdf_craft.transformer.chapter_formula_interrupter import ChapterFormulaInterrupter
from .structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import SubmitKind, TranslationTask

from .progress import task_word_count
from .anchored_content import (
    AnchoredContent,
    AnchoredContentTranslation,
)


class AsyncXMLTaskTranslator(Protocol):
    async def translate_element(
        self,
        task: TranslationTask,
        **kwargs,
    ) -> tuple[Element, object]: ...


_MATH_UNIT = '__PDF_CRAFT_ASSET_MATH_UNIT'


class AnchoredContentXMLTransformer:
    """Translate image/table fields while supplying non-persisted local context."""

    def __init__(
        self,
        translator: AsyncXMLTaskTranslator,
        mode: SubmitKind = SubmitKind.REPLACE,
    ) -> None:
        self._translator = translator
        self._mode = mode

    @policy_operation
    @adopted_result(lambda values: any(value is not None for value in values))
    async def transform_assets(
        self,
        assets: Sequence[AnchoredContent], *,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> Sequence[AnchoredContentTranslation | None]:
        if not assets:
            return ()
        from ..extractor.chapter.asset_text import translatable_asset
        source = Chapter(None, -1, [StandaloneAsset(translatable_asset(item.asset)) for item in assets])
        element = encode(source)
        asset_elements = element.findall("flow/standalone-asset/asset")
        if len(asset_elements) != len(assets):
            return (None,) * len(assets)
        expected_slots = {_slot_key(item): item.identity for item in assets}
        if len(expected_slots) != len(assets):
            return (None,) * len(assets)
        for asset, item in zip(asset_elements, assets, strict=True):
            asset.set("translation_slot", _slot_key(item))
        # Only format-owned units can authorize a canonical mathematical
        # projection. The identity is restored from source attributes, never
        # accepted from an LLM response.
        for index, node in enumerate(element.iter()):
            if node.tag in {'td', 'th', 'title', 'content', 'caption'}:
                node.set(_MATH_UNIT, str(index))
        source_assets = {slot: deepcopy(asset) for slot, asset in
                         zip(expected_slots, asset_elements, strict=True)}

        contexts = {_slot_key(item): item.context for item in assets}
        protocol = StructuredTranslation(owner=_asset_unit_owner, formulas=ChapterFormulaInterrupter(),
                                         context=lambda segments: _asset_context(segments, contexts), record_units=True,
                                         identity=lambda owner: owner.get(_MATH_UNIT, str(id(owner))))
        task = TranslationTask(
                element=element,
                action=self._mode,
                payload=tuple(assets),
                item_id=_batch_id(assets),
                word_count=task_word_count(element),
            )
        translated, _ = await protocol.translate(self._translator, task)
        if task.outcomes and not all(task.outcomes.values()):
            return (None,) * len(assets)
        source_structures = {slot: _asset_structure(asset, protocol.projected_math_units, validated_units=protocol.validated_text_units)
                             for slot, asset in source_assets.items()}
        translated_assets = _decode_assets_by_slot(
            translated, expected_slots, source_structures, protocol.projected_math_units, protocol.validated_text_units,
        )
        if translated_assets is None:
            from .._policy import recover
            from ..policies import FailureCategory
            from .._failure_sources import task_locations
            error = ValueError('asset translation does not preserve its source structure')
            retain = getattr(self._translator, 'retain_failure', None)
            if retain is not None:
                await retain(error, stage='asset_structure', context={
                    'item_id': task.item_id,
                    'source_assets': {slot: tostring(asset, encoding='unicode')
                                      for slot, asset in source_assets.items()},
                    'translated_xml': tostring(translated, encoding='unicode'),
                    'projected_math_units': sorted(protocol.projected_math_units),
                    'validated_text_units': sorted(protocol.validated_text_units),
                })
            await recover(error,
                          FailureCategory.FILL, 'asset_structure', 'omit_translation',
                          targets=task_locations(task))
            return (None,) * len(assets)
        source_references = {
            reference.id: reference
            for item in assets
            for field in (item.asset.title, item.asset.content, item.asset.caption)
            for reference in flatten(field)
            if isinstance(reference, Reference)
        }
        return tuple(
            AnchoredContentTranslation(
                item.identity,
                _restore_source_references(
                    translated_assets[item.identity].title, source_references,
                ),
                _restore_source_references(
                    (translated_assets[item.identity].content
                     if item.asset.ref != "image"
                     else item.asset.content), source_references,
                ),
                _restore_source_references(
                    translated_assets[item.identity].caption, source_references,
                ),
            )
            for item in assets
        )


def _batch_id(assets: Sequence[AnchoredContent]) -> str:
    first = assets[0]
    return f"anchored-{first.chapter_id}-{first.flow_index}-{first.child_index}"


def _slot_key(item: AnchoredContent) -> str:
    chapter_id, flow_index, child_index = item.identity
    return f"{chapter_id}:{flow_index}:{child_index}"


def _decode_assets_by_slot(
    root: Element,
    expected_slots: dict[str, tuple[str, int, int]],
    source_structures: dict[str, tuple[Any, ...]],
    projected_units: set[str] | None = None,
    validated_units: set[str] | None = None,
) -> dict[tuple[str, int, int], SourceAsset] | None:
    wrappers = root.findall("flow/standalone-asset")
    if len(wrappers) != len(expected_slots):
        return None
    slot_keys: list[str] = []
    translated_elements: list[Element] = []
    for wrapper in wrappers:
        assets = wrapper.findall("asset")
        if len(assets) != 1:
            return None
        asset = assets[0]
        slot = asset.get("translation_slot")
        if slot is None or slot not in expected_slots or slot in slot_keys:
            return None
        slot_keys.append(slot)
        translated_elements.append(asset)
    if set(slot_keys) != set(expected_slots):
        return None

    if any(
        _asset_structure(asset, projected_units, validated_units=validated_units) != source_structures[slot]
        for asset, slot in zip(translated_elements, slot_keys, strict=True)
    ):
        return None
    for asset in translated_elements:
        asset.attrib.pop("translation_slot")
        for node in asset.iter():
            node.attrib.pop(_MATH_UNIT, None)
    chapter = decode(root)
    result: dict[tuple[str, int, int], SourceAsset] = {}
    for slot, item in zip(slot_keys, chapter.flow_items, strict=True):
        if not isinstance(item, StandaloneAsset):
            return None
        identity = expected_slots[slot]
        result[identity] = item.asset
    return result


def _asset_structure(element: Element, projected_units: set[str] | None = None,
                     canonical_math: bool = False, *, validated_units: set[str] | None = None,
                     canonical_text: bool = False) -> tuple[Any, ...]:
    """Preserve markup; only a validated math projection can change math shape."""
    canonical_math = canonical_math or (projected_units is not None and
                                       element.get(_MATH_UNIT) in projected_units)
    # Text occupancy is not markup. Canonical fill already checks the complete
    # immutable artist text for this owner, including legitimately empty units.
    # Unvalidated adapters and immutable typed formulas keep the strict check.
    canonical_text = canonical_text or (validated_units is not None and
                                       element.get(_MATH_UNIT) in validated_units)
    leading = True if canonical_text else bool(element.text and element.text.strip())
    children = []
    for child in element:
        if canonical_math and child.tag == 'inline_expr' and child.get('kind') != 'text':
            # Typed math occupies the same mixed-content slot as its canonical
            # text. Do not freeze its old count/value after validating the new
            # mathematical allocation; keep every non-math markup boundary.
            if children:
                structure, _ = children[-1]
                children[-1] = structure, True
            else:
                leading = True
        else:
            children.append((_asset_structure(child, projected_units, canonical_math,
                                               validated_units=validated_units, canonical_text=canonical_text),
                             True if canonical_text else bool(child.tail and child.tail.strip())))
    return (
        element.tag,
        tuple(sorted((key, value) for key, value in element.attrib.items()
                     if key not in {'translation_slot', _MATH_UNIT})),
        leading,
        element.text or '' if element.tag == 'inline_expr' and element.get('kind') != 'text' else None,
        tuple(children),
    )


def _restore_source_references(content, references: dict[tuple[int, int], Reference]):
    """Keep the source chapter's reference graph after the temporary decode."""
    restored = []
    for part in content:
        if isinstance(part, Reference):
            restored.append(references.get(part.id, part))
        elif isinstance(part, HTMLTag):
            restored.append(HTMLTag(
                definition=part.definition,
                attributes=part.attributes,
                children=_restore_source_references(part.children, references),
            ))
        else:
            restored.append(part)
    return restored


def _asset_unit_owner(segment):
    return next((node for node in reversed(segment.head.parent_stack)
                 if node.tag in {'td', 'th', 'title', 'content', 'caption'}), segment.parent)


def _asset_context(segments, contexts):
    return '\n\n'.join(dict.fromkeys(
        contexts.get(node.get('translation_slot'), '')
        for segment in segments for node in segment.head.parent_stack
        if node.tag == 'asset' and node.get('translation_slot') in contexts
    ))
