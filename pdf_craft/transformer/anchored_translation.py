"""Workspace transformation for image/table text fields."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pdf_craft.common import read_xml, save_xml
from pdf_craft.extractor.chapter.chapter import (
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

from .anchored_content import (
    AnchoredContent,
    AnchoredContentTransformer,
    AnchoredContentTranslation,
)
from .translation_coverage import AnchoredContentCoverage, write_anchored_coverage


_BATCH_SIZE = 4
_CONTEXT_LIMIT = 480


@dataclass
class _AssetSlot:
    payload: AnchoredContent
    asset: SourceAsset


def translate_anchored_contents_in_workspace(
    chapters_path: Path,
    translation_path: Path,
    transformer: AnchoredContentTransformer,
) -> None:
    """Translate image/table fields without entering the NarrativeFlow stage."""
    coverage: list[AnchoredContentCoverage] = []
    for path in sorted(chapters_path.glob("chapter_*.xml")):
        chapter = decode(read_xml(path))
        slots = list(_asset_slots(chapter))
        if not slots:
            continue
        translated = _translate_slots(slots, transformer)
        for slot, target in zip(slots, translated, strict=True):
            state = "preserved"
            if target is not None:
                slot.asset.title = target.title
                slot.asset.content = target.content
                slot.asset.caption = target.caption
                state = "translated"
            coverage.append(AnchoredContentCoverage(*slot.payload.identity, state))
        save_xml(encode(chapter), path)

    if coverage:
        write_anchored_coverage(translation_path, coverage)


def _translate_slots(
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
        batch = [slot for _, slot in batch_with_indexes]
        translated = _transform_batch(tuple(slot.payload for slot in batch), transformer)
        for (index, _), value in zip(batch_with_indexes, translated, strict=True):
            results[index] = value
    return results


def _transform_batch(
    payloads: Sequence[AnchoredContent],
    transformer: AnchoredContentTransformer,
) -> Sequence[AnchoredContentTranslation | None]:
    try:
        translated = transformer.transform_assets(payloads)
        matched = _match_translations(payloads, translated)
        if matched is not None:
            return matched
    except Exception:  # Individual asset fallback is deliberately resilient.
        pass

    result: list[AnchoredContentTranslation | None] = []
    for payload in payloads:
        try:
            translated = transformer.transform_assets((payload,))
            matched = _match_translations((payload,), translated)
            result.append(matched[0] if matched is not None else None)
        except Exception:  # A failed asset remains source content.
            result.append(None)
    return result


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
    if any(item is None for item in translations):
        return None

    expected = [payload.identity for payload in payloads]
    if len(set(expected)) != len(expected):
        return None
    by_identity: dict[tuple[str, int, int], AnchoredContentTranslation] = {}
    for translation in translations:
        assert translation is not None
        if translation.identity in by_identity or translation.identity not in expected:
            return None
        by_identity[translation.identity] = translation
    if set(by_identity) != set(expected):
        return None
    return [by_identity[identity] for identity in expected]


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
