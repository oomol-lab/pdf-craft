"""XMLTranslator adapter for the independent AnchoredContent stage."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast
from xml.etree.ElementTree import Element

from pdf_craft.extractor.chapter.chapter import Chapter, Reference, SourceAsset, StandaloneAsset, decode, encode
from pdf_craft.markdown.paragraph import HTMLTag, flatten
from pdf_craft.transformer.chapter_formula_interrupter import ChapterFormulaInterrupter
from pdf_craft.transformer.xml_translator.xml_translator import SubmitKind, TranslationTask

from .anchored_content import (
    AnchoredContent,
    AnchoredContentTranslation,
)


class XMLTaskTranslator(Protocol):
    """The XMLTranslator subset used by the anchored-content adapter."""

    def translate_element(
        self,
        task: TranslationTask[object],
        **kwargs,
    ) -> tuple[Element, object]: ...


class AsyncXMLTaskTranslator(Protocol):
    async def translate_element_async(
        self,
        task: TranslationTask[object],
        **kwargs,
    ) -> tuple[Element, object]: ...


class AnchoredContentXMLTransformer:
    """Translate image/table fields while supplying non-persisted local context."""

    def __init__(
        self,
        translator: XMLTaskTranslator,
        mode: SubmitKind = SubmitKind.REPLACE,
    ) -> None:
        self._translator = translator
        self._mode = mode

    def transform_assets(
        self,
        assets: Sequence[AnchoredContent],
    ) -> Sequence[AnchoredContentTranslation | None]:
        if not assets:
            return ()

        source = Chapter(
            None,
            -1,
            [StandaloneAsset(item.asset) for item in assets],
        )
        element = encode(source)
        asset_elements = element.findall("flow/standalone-asset/asset")
        if len(asset_elements) != len(assets):
            return (None,) * len(assets)
        expected_slots = {_slot_key(item): item.identity for item in assets}
        if len(expected_slots) != len(assets):
            return (None,) * len(assets)
        for asset, item in zip(asset_elements, assets, strict=True):
            asset.set("translation_slot", _slot_key(item))
            if item.context:
                context = Element("translation-context", {"display": "inline"})
                context.text = item.context
                asset.insert(0, context)

        formula_interrupter = ChapterFormulaInterrupter()
        translated, _ = self._translator.translate_element(
            TranslationTask(
                element=element,
                action=self._mode,
                payload=tuple(assets),
                item_id=_batch_id(assets),
                character_count=sum(
                    len(item.context)
                    + sum(len(part) for field in (item.asset.title, item.asset.content, item.asset.caption)
                          for part in field if isinstance(part, str))
                    for item in assets
                ),
            ),
            interrupt_source_text_segments=formula_interrupter.interrupt_source_text_segments,
            interrupt_translated_text_segments=formula_interrupter.interrupt_translated_text_segments,
            interrupt_block_element=formula_interrupter.interrupt_block_element,
        )
        translated_assets = _decode_assets_by_slot(translated, expected_slots)
        if translated_assets is None:
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
                identity,
                _restore_source_references(asset.title, source_references),
                _restore_source_references(asset.content, source_references),
                _restore_source_references(asset.caption, source_references),
            )
            for identity, asset in ((item.identity, translated_assets[item.identity]) for item in assets)
        )

    async def transform_assets_async(
        self,
        assets: Sequence[AnchoredContent],
    ) -> Sequence[AnchoredContentTranslation | None]:
        if not assets:
            return ()
        source = Chapter(None, -1, [StandaloneAsset(item.asset) for item in assets])
        element = encode(source)
        asset_elements = element.findall("flow/standalone-asset/asset")
        if len(asset_elements) != len(assets):
            return (None,) * len(assets)
        expected_slots = {_slot_key(item): item.identity for item in assets}
        if len(expected_slots) != len(assets):
            return (None,) * len(assets)
        for asset, item in zip(asset_elements, assets, strict=True):
            asset.set("translation_slot", _slot_key(item))
            if item.context:
                context = Element("translation-context", {"display": "inline"})
                context.text = item.context
                asset.insert(0, context)

        formula_interrupter = ChapterFormulaInterrupter()
        translator = cast(AsyncXMLTaskTranslator, self._translator)
        translated, _ = await translator.translate_element_async(
            TranslationTask(
                element=element,
                action=self._mode,
                payload=tuple(assets),
                item_id=_batch_id(assets),
                character_count=sum(
                    len(item.context)
                    + sum(
                        len(part)
                        for field in (
                            item.asset.title, item.asset.content, item.asset.caption,
                        )
                        for part in field if isinstance(part, str)
                    )
                    for item in assets
                ),
            ),
            interrupt_source_text_segments=formula_interrupter.interrupt_source_text_segments,
            interrupt_translated_text_segments=formula_interrupter.interrupt_translated_text_segments,
            interrupt_block_element=formula_interrupter.interrupt_block_element,
        )
        translated_assets = _decode_assets_by_slot(translated, expected_slots)
        if translated_assets is None:
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
                    translated_assets[item.identity].content, source_references,
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


def _remove_translation_context(root: Element) -> None:
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "translation-context":
                parent.remove(child)


def _decode_assets_by_slot(
    root: Element,
    expected_slots: dict[str, tuple[str, int, int]],
) -> dict[tuple[str, int, int], SourceAsset] | None:
    wrappers = root.findall("flow/standalone-asset")
    if len(wrappers) != len(expected_slots):
        return None
    slot_keys: list[str] = []
    for wrapper in wrappers:
        assets = wrapper.findall("asset")
        if len(assets) != 1:
            return None
        slot = assets[0].get("translation_slot")
        if slot is None or slot not in expected_slots or slot in slot_keys:
            return None
        slot_keys.append(slot)
    if set(slot_keys) != set(expected_slots):
        return None

    _remove_translation_context(root)
    chapter = decode(root)
    result: dict[tuple[str, int, int], SourceAsset] = {}
    for slot, item in zip(slot_keys, chapter.flow_items, strict=True):
        if not isinstance(item, StandaloneAsset):
            return None
        identity = expected_slots[slot]
        result[identity] = item.asset
    return result


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
