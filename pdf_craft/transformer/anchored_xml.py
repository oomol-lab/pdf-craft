"""XMLTranslator adapter for the independent AnchoredContent stage."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from xml.etree.ElementTree import Element

from pdf_craft.extractor.chapter.chapter import Chapter, Reference, StandaloneAsset, decode, encode
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
        for index, (asset, item) in enumerate(zip(asset_elements, assets, strict=True)):
            asset.set("translation_slot", str(index))
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
        _remove_translation_context(translated)
        translated_assets = _decode_assets(translated)
        if len(translated_assets) != len(assets):
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
                _restore_source_references(asset.title, source_references),
                _restore_source_references(asset.content, source_references),
                _restore_source_references(asset.caption, source_references),
            )
            for asset in translated_assets
        )


def _batch_id(assets: Sequence[AnchoredContent]) -> str:
    first = assets[0]
    return f"anchored-{first.chapter_id}-{first.flow_index}-{first.child_index}"


def _remove_translation_context(root: Element) -> None:
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "translation-context":
                parent.remove(child)
    for asset in root.iter("asset"):
        asset.attrib.pop("translation_slot", None)


def _decode_assets(root: Element):
    chapter = decode(root)
    result = []
    for item in chapter.flow_items:
        if not isinstance(item, StandaloneAsset):
            return ()
        result.append(item.asset)
    return tuple(result)


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
