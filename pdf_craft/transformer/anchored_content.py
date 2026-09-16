"""Contracts for translating image/table text outside NarrativeFlow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pdf_craft.extractor.chapter.chapter import Content, FlowAssetRef, SourceAsset


@dataclass(frozen=True)
class AnchoredContent:
    """One stable image/table text payload in a chapter flow.

    ``context`` is source NarrativeFlow supplied to this dedicated translation
    stage only.  It is not persisted as part of the asset and is never fed
    back into the Narrative translator.
    """

    chapter_id: str
    flow_index: int
    child_index: int
    asset: SourceAsset
    context: str

    def __post_init__(self) -> None:
        if self.asset.ref not in {"image", "table"}:
            raise ValueError("AnchoredContent requires an image or table asset")

    @property
    def identity(self) -> tuple[str, int, int]:
        return self.chapter_id, self.flow_index, self.child_index

    @property
    def ref(self) -> FlowAssetRef:
        return self.asset.ref


@dataclass(frozen=True)
class AnchoredContentTranslation:
    """Translated text fields for one asset, without mutable source geometry."""

    title: Content
    content: Content
    caption: Content


class AnchoredContentTransformer(Protocol):
    """Translate extracted image/table text in small, contextual batches.

    The returned sequence must match the supplied assets exactly.  ``None``
    preserves that individual source asset, which lets callers degrade safely
    when only part of a batch can be translated.
    """

    def transform_assets(
        self,
        assets: Sequence[AnchoredContent],
    ) -> Sequence[AnchoredContentTranslation | None]: ...
