# pylint: disable=unused-argument
"""Adapt XMLTranslator to the template/page-oriented furniture contract."""

from __future__ import annotations
from collections.abc import Mapping

from pdf_craft.policies import RetryPolicy, ErrorPolicyInput
from pdf_craft._policy import OMITTED, policy_operation

from collections.abc import Sequence
from typing import Protocol
from xml.etree.ElementTree import Element, SubElement

from .furniture import FurniturePosition, FurnitureSection
from .xml_translator.xml_translator import SubmitKind, TranslationTask
from .._failure_sources import furniture_section_locations, task_locations
from .._policy import recover
from ..policies import FailureCategory


class AsyncXMLTaskTranslator(Protocol):
    async def translate_element(
        self,
        task: TranslationTask,
        **kwargs,
    ) -> tuple[Element, object]: ...


class FurnitureXMLTransformer:
    """Send pattern and page furniture as separate XML translation payloads."""

    def __init__(
        self,
        translator: AsyncXMLTaskTranslator,
        mode: SubmitKind = SubmitKind.REPLACE,
    ) -> None:
        self._translator = translator
        self._mode = mode

    @policy_operation
    async def transform_position(
        self, position: FurniturePosition, *,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> str | None:
        element = Element("furniture-position")
        element.text = position.content
        task = TranslationTask(
                element=element,
                action=self._mode,
                payload=position,
                item_id=f"pattern-{position.pattern_id}-position-{position.position_id}",
                character_count=len(position.content),
            )
        translated, _ = await self._translator.translate_element(
            task
        )
        if task.outcomes and not all(task.outcomes.values()):
            return None
        if position.content.strip() and not (translated.text or "").strip():
            await recover(ValueError("translated furniture text is empty"),
                          FailureCategory.FILL, 'furniture', 'omit_translation', targets=task_locations(task))
            return None
        return translated.text

    @policy_operation
    async def transform_sections(
        self,
        page_index: int,
        sections: Sequence[FurnitureSection], *,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> Sequence[str | None]:
        element = Element("furniture-page", {"index": str(page_index)})
        for index, section in enumerate(sections):
            child = SubElement(element, "section", {"id": str(index)})
            child.text = section.content
        task = TranslationTask(
                element=element,
                action=self._mode,
                payload=tuple(sections),
                item_id=f"furniture-page-{page_index}",
                character_count=sum(len(section.content) for section in sections),
            )
        translated, _ = await self._translator.translate_element(
            task
        )
        if task.outcomes and not all(task.outcomes.values()):
            return (None,) * len(sections)
        children = translated.findall("section")
        if len(children) != len(sections) or any(
            child.get("id") != str(index) for index, child in enumerate(children)
        ):
            await recover(ValueError("translated furniture sections do not preserve source identities"),
                          FailureCategory.FILL, 'furniture_sections', 'omit_translation',
                          targets=task_locations(task))
            return (None,) * len(sections)
        values: list[str | None] = []
        for child, section in zip(children, sections, strict=True):
            if section.content.strip() and not (child.text or "").strip():
                await recover(ValueError("translated furniture section text is empty"),
                              FailureCategory.FILL, 'furniture_sections', 'omit_translation',
                              targets=furniture_section_locations((section,)))
                values.append(None)
            else:
                values.append(child.text)
        return values
