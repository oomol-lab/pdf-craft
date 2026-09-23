"""Adapt XMLTranslator to the template/page-oriented furniture contract."""

from __future__ import annotations

from collections.abc import Sequence
import inspect
from typing import Protocol, cast
from xml.etree.ElementTree import Element, SubElement

from pdf_craft.runtime import TRANSLATION_DOMAIN
from .furniture import FurniturePosition, FurnitureSection
from .xml_translator.xml_translator import SubmitKind, TranslationTask


class XMLTaskTranslator(Protocol):
    """The XMLTranslator subset needed for furniture payloads."""

    def translate_element(
        self,
        task: TranslationTask[object],
        **kwargs,
    ) -> tuple[Element, object]: ...


class AsyncXMLTaskTranslator(Protocol):
    async def translate_element(
        self,
        task: TranslationTask[object],
        **kwargs,
    ) -> tuple[Element, object]: ...


class FurnitureXMLTransformer:
    """Send pattern and page furniture as separate XML translation payloads."""

    def __init__(
        self,
        translator: XMLTaskTranslator | AsyncXMLTaskTranslator,
        mode: SubmitKind = SubmitKind.REPLACE,
    ) -> None:
        self._translator = translator
        self._mode = mode

    def _transform_position_blocking(self, position: FurniturePosition) -> str | None:
        element = Element("furniture-position")
        element.text = position.content
        translated, _ = cast(XMLTaskTranslator, self._translator).translate_element(
            TranslationTask(
                element=element,
                action=self._mode,
                payload=position,
                item_id=f"pattern-{position.pattern_id}-position-{position.position_id}",
                character_count=len(position.content),
            )
        )
        return translated.text

    async def transform_position(
        self, position: FurniturePosition,
    ) -> str | None:
        if not inspect.iscoroutinefunction(self._translator.translate_element):
            return await TRANSLATION_DOMAIN.run(self._transform_position_blocking, position)
        element = Element("furniture-position")
        element.text = position.content
        translated, _ = await cast(
            AsyncXMLTaskTranslator, self._translator,
        ).translate_element(
            TranslationTask(
                element=element,
                action=self._mode,
                payload=position,
                item_id=f"pattern-{position.pattern_id}-position-{position.position_id}",
                character_count=len(position.content),
            )
        )
        return translated.text

    def _transform_sections_blocking(
        self,
        page_index: int,
        sections: Sequence[FurnitureSection],
    ) -> Sequence[str | None]:
        element = Element("furniture-page", {"index": str(page_index)})
        for index, section in enumerate(sections):
            child = SubElement(element, "section", {"id": str(index)})
            child.text = section.content
        translated, _ = cast(XMLTaskTranslator, self._translator).translate_element(
            TranslationTask(
                element=element,
                action=self._mode,
                payload=tuple(sections),
                item_id=f"furniture-page-{page_index}",
                character_count=sum(len(section.content) for section in sections),
            )
        )
        children = translated.findall("section")
        if len(children) != len(sections):
            return (None,) * len(sections)
        values: list[str | None] = []
        for index, child in enumerate(children):
            if child.get("id") != str(index):
                return (None,) * len(sections)
            values.append(child.text)
        return values

    async def transform_sections(
        self,
        page_index: int,
        sections: Sequence[FurnitureSection],
    ) -> Sequence[str | None]:
        if not inspect.iscoroutinefunction(self._translator.translate_element):
            return await TRANSLATION_DOMAIN.run(
                self._transform_sections_blocking, page_index, sections,
            )
        element = Element("furniture-page", {"index": str(page_index)})
        for index, section in enumerate(sections):
            child = SubElement(element, "section", {"id": str(index)})
            child.text = section.content
        translated, _ = await cast(
            AsyncXMLTaskTranslator, self._translator,
        ).translate_element(
            TranslationTask(
                element=element,
                action=self._mode,
                payload=tuple(sections),
                item_id=f"furniture-page-{page_index}",
                character_count=sum(len(section.content) for section in sections),
            )
        )
        children = translated.findall("section")
        if len(children) != len(sections):
            return (None,) * len(sections)
        values: list[str | None] = []
        for index, child in enumerate(children):
            if child.get("id") != str(index):
                return (None,) * len(sections)
            values.append(child.text)
        return values
