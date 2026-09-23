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
    """Translated text fields bound to one immutable anchored-content slot."""

    identity: tuple[str, int, int]
    title: Content
    content: Content
    caption: Content


class AnchoredContentTransformer(Protocol):
    """Translate extracted image/table text in small, contextual batches.

    Each non-``None`` result must carry the exact ``AnchoredContent.identity``
    of its source slot.  Callers validate every batch's slot set before
    applying any field, so a reordered or renamed transport result cannot
    write text into another asset. ``None`` preserves a singleton source asset
    and is otherwise treated as an invalid batch response.
    """

    def transform_assets(
        self,
        assets: Sequence[AnchoredContent],
    ) -> Sequence[AnchoredContentTranslation | None]: ...


class AsyncAnchoredContentTransformer(Protocol):
    """Native async extension contract for anchored image/table translation."""

    async def transform_assets(
        self,
        assets: Sequence[AnchoredContent],
    ) -> Sequence[AnchoredContentTranslation | None]: ...
