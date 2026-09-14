"""Dedicated translation contracts for page furniture.

Furniture is not NarrativeFlow: a Position is a reusable page template
member, while a Section is a page-local fragment.  Keep those scopes explicit
instead of adapting furniture to the Chapter transformer protocol.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class FurniturePosition:
    """One unbound reusable pattern position to translate once."""

    pattern_id: int
    position_id: int
    kind: str
    content: str


@dataclass(frozen=True)
class FurnitureSection:
    """One unbound, page-local furniture fragment."""

    page_index: int
    det: Box
    content: str


class FurnitureTransformer(Protocol):
    """Translate furniture at its natural template and page scopes.

    ``None`` means the individual item was not translated and must be
    preserved.  ``transform_sections`` receives all ordinary fragments of one
    page, so an implementation can use page-local context without receiving
    NarrativeFlow text.
    """

    def transform_position(self, position: FurniturePosition) -> str | None: ...

    def transform_sections(
        self,
        page_index: int,
        sections: Sequence[FurnitureSection],
    ) -> Sequence[str | None]: ...
