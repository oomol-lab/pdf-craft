# pylint: disable=unused-argument
"""Workspace transformation for image/table text fields."""

from __future__ import annotations

from ..policies import RetryPolicy, ErrorPolicyInput
from .._policy import OMITTED, policy_operation
from ..usage import adopted_result

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pdf_craft.common import read_xml, save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter,
    DisplayFormula,
    FlowItem,
    InlineExpression,
    Reference,
    SourceAsset,
    StandaloneAsset,
    TextFlowItem,
    decode,
    encode,
)
from pdf_craft.extractor.chapter.text_projection import iter_continuous_content
from pdf_craft.markdown.paragraph import HTMLTag, flatten
from pdf_craft.runtime import IO_DOMAIN

from .anchored_content import (
    AnchoredContent,
    AnchoredContentTransformer,
    AnchoredContentTranslation,
)
from .translation_coverage import AnchoredContentCoverage, write_anchored_coverage
from .word_count import count_xml_words
from .progress import active_progress, asset_unit_context
from ..progress import UnitKind, UnitOutcome


_BATCH_SIZE = 4
_CONTEXT_LIMIT = 480


@dataclass
class _AssetSlot:
    payload: AnchoredContent
    asset: SourceAsset


async def translate_anchored_contents_in_workspace_async(
    chapters_path: Path,
    translation_path: Path,
    transformer: AnchoredContentTransformer,
) -> None:
    """Translate anchored payloads with native async LLM calls and pooled I/O."""
    paths = await IO_DOMAIN.run(
        lambda: sorted(chapters_path.glob("chapter_*.xml"))
    )
    from .._policy import Completion
    completion = Completion()
    from .._failure_sources import page_sources
    from ..document.package import _read_pages
    from ..document.access import page_geometries
    pages_path = chapters_path.parent / 'pages.xml'
    pages = {}
    if await IO_DOMAIN.run(pages_path.exists):
        dpi, sizes = await IO_DOMAIN.run(_read_pages, pages_path)
        pages = page_geometries(sizes, dpi)
    coverage: list[AnchoredContentCoverage] = []
    for path in paths:
        chapter = await IO_DOMAIN.run(lambda path=path: decode(read_xml(path)))
        slots = list(_asset_slots(chapter))
        if not slots:
            continue
        with page_sources(pages):
            translated = await _translate_slots_async(slots, transformer)
        for slot, target in zip(slots, translated, strict=True):
            state = "preserved"
            if target is not None:
                slot.asset.title = target.title
                slot.asset.content = target.content
                slot.asset.caption = target.caption
                state = "translated"
            if _asset_has_text(slot.asset):
                completion.succeeded += target is not None
                completion.degraded += target is None
            coverage.append(AnchoredContentCoverage(*slot.payload.identity, state))
        await IO_DOMAIN.run(save_xml, encode(chapter), path)
    completion.check('asset translation')
    if coverage:
        await IO_DOMAIN.run(write_anchored_coverage, translation_path, coverage)


async def _translate_slots_async(
    slots: Sequence[_AssetSlot],
    transformer: AnchoredContentTransformer,
) -> list[AnchoredContentTranslation | None]:
    results: list[AnchoredContentTranslation | None] = [None] * len(slots)
    translatable = [
        (index, slot) for index, slot in enumerate(slots)
        if _asset_has_text(slot.asset)
    ]
    for start in range(0, len(translatable), _BATCH_SIZE):
        batch_with_indexes = translatable[start : start + _BATCH_SIZE]
        payloads = tuple(slot.payload for _, slot in batch_with_indexes)
        translated = await _transform_batch_async(payloads, transformer)
        for (index, _), value in zip(batch_with_indexes, translated, strict=True):
            results[index] = value
    return results


@policy_operation
@adopted_result(lambda values: any(value is not None for value in values))
async def _transform_batch_async(
    payloads: Sequence[AnchoredContent],
    transformer: AnchoredContentTransformer, *,
    retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
) -> Sequence[AnchoredContentTranslation | None]:
    from .._policy import recover
    from ..policies import FailureCategory, FailureLocation
    targets = tuple(FailureLocation('regions', member_path=f'chapters/chapter_{item.chapter_id}.xml',
                    unit_id=str(item.identity), channel='asset', page_indexes=(item.asset.page_index,))
                    for item in payloads)
    progress = active_progress()
    units = {}
    if progress is not None and progress.started:
        for item in payloads:
            units[asset_unit_key(item)] = await progress.unit(UnitKind.ASSET, words=asset_word_count(item.asset))
    with asset_unit_context(units if units else None):
        try:
            translated = await _transform_assets_async(transformer, payloads)
            matched = _match_translations(payloads, translated)
            if matched is None:
                raise ValueError('asset translation does not preserve source identities')
        except Exception as error:
            await recover(error, FailureCategory.FILL, 'asset_structure', 'omit_translation', targets=targets)
            matched = [None for _ in payloads]
    # Reached only after acceptance or an explicitly allowed recovery. Raising
    # errors and cancellation leave the operation incomplete, as before.
    for item, value in zip(payloads, matched, strict=True):
        unit = units.get(asset_unit_key(item))
        if unit is not None:
            await unit.progress(1)
            await unit.finish(UnitOutcome.FALLBACK if value is None else UnitOutcome.PROCESSED)
    return matched


def asset_unit_key(item: AnchoredContent) -> str:
    return ':'.join(str(value) for value in item.identity)


def asset_word_count(asset: SourceAsset) -> int:
    """The same independent source field scope in pre-scan and execution."""
    return count_xml_words(encode(Chapter(None, -1, [StandaloneAsset(asset)])))


async def _transform_assets_async(
    transformer: AnchoredContentTransformer,
    assets: Sequence[AnchoredContent],
) -> Sequence[AnchoredContentTranslation | None]:
    return await transformer.transform_assets(assets)


def _match_translations(
    payloads: Sequence[AnchoredContent],
    translations: Sequence[AnchoredContentTranslation | None],
) -> list[AnchoredContentTranslation | None] | None:
    """Bind output by stable identity instead of transport order.

    ``None`` has no identity.  It is accepted only for a singleton request;
    a mixed multi-asset response falls back to isolated calls so no unknown
    position can accidentally preserve or overwrite another asset.
    """
    if len(translations) != len(payloads):
        return None
    if len(payloads) == 1 and translations[0] is None:
        return [None]
    if all(item is None for item in translations):
        return [None for _ in payloads]

    expected = [payload.identity for payload in payloads]
    if len(set(expected)) != len(expected):
        return None
    by_identity: dict[tuple[str, int, int], AnchoredContentTranslation] = {}
    for translation in translations:
        if translation is None:
            continue
        if translation.identity in by_identity or translation.identity not in expected:
            return None
        by_identity[translation.identity] = translation
    if len(by_identity) + sum(item is None for item in translations) != len(expected):
        return None
    return [by_identity.get(identity) for identity in expected]


def _asset_slots(chapter) -> Iterable[_AssetSlot]:
    chapter_id = str(chapter.id) if chapter.id is not None else "head"
    for flow_index, item in enumerate(chapter.flow_items):
        if isinstance(item, TextFlowItem):
            context = _text_flow_context(chapter.flow_items, flow_index, item)
            for child_index, child in enumerate(item.children):
                if isinstance(child, SourceAsset) and child.ref in {"image", "table"}:
                    yield _AssetSlot(
                        AnchoredContent(chapter_id, flow_index, child_index, child, context), child
                    )
        elif isinstance(item, StandaloneAsset) and item.asset.ref in {"image", "table"}:
            context = _standalone_context(chapter.flow_items, flow_index)
            yield _AssetSlot(
                AnchoredContent(chapter_id, flow_index, -1, item.asset, context), item.asset
            )
        elif isinstance(item, DisplayFormula):
            continue


def _text_flow_context(
    items: Sequence[FlowItem],
    flow_index: int,
    item: TextFlowItem,
) -> str:
    heading = next(
        (
            _flow_text(previous)
            for previous in reversed(items[:flow_index])
            if isinstance(previous, TextFlowItem) and previous.role == "heading"
        ),
        "",
    )
    return _clip("\n".join(part for part in (heading, _flow_text(item)) if part))


def _standalone_context(items: Sequence[FlowItem], flow_index: int) -> str:
    before = next(
        (_flow_text(item) for item in reversed(items[:flow_index]) if isinstance(item, TextFlowItem)),
        "",
    )
    after = next(
        (_flow_text(item) for item in items[flow_index + 1 :] if isinstance(item, TextFlowItem)),
        "",
    )
    return _clip("\n".join(part for part in (before, after) if part))


def _flow_text(item: TextFlowItem) -> str:
    return _content_text(iter_continuous_content(item)).strip()


def _content_text(content) -> str:
    values: list[str] = []
    for value in flatten(content):
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, InlineExpression):
            values.append(value.content)
        elif isinstance(value, Reference):
            values.append(str(value.mark))
        elif isinstance(value, HTMLTag):
            values.append(_content_text(value.children))
    return "".join(values)


def _asset_has_text(asset: SourceAsset) -> bool:
    return any(
        _content_text(content).strip()
        for content in (asset.title, asset.content, asset.caption)
    )


def _clip(value: str) -> str:
    return value[:_CONTEXT_LIMIT]
