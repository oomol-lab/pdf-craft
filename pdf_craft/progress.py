"""Public facts about extraction and translation work, without percentages."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto


class ProgressEventKind(Enum):
    """The same lifecycle for an operation and each of its units."""
    STARTED = auto()
    PROGRESS = auto()
    COMPLETED = auto()


class OperationKind(Enum):
    """Independent operation scopes; IDs are local to one scope."""
    EXTRACTION = auto()
    TRANSLATION = auto()


class UnitKind(Enum):
    """Stable logical work, independent of model request batching."""
    PAGE = auto()
    TOC = auto()
    METADATA = auto()
    CONTENT = auto()
    ASSET = auto()
    FURNITURE = auto()
    NAVIGATION = auto()


class UnitOutcome(Enum):
    """Final disposition; all outcomes count as completed work."""
    PROCESSED = auto()
    REUSED = auto()
    FALLBACK = auto()
    SKIPPED = auto()


@dataclass(frozen=True)
class ProgressEvent:
    """unit_id=None denotes the operation. Word values are cumulative."""
    kind: ProgressEventKind
    operation: OperationKind
    unit_id: int | None = None
    unit_kind: UnitKind | None = None
    unit_counts: Mapping[UnitKind, int] = field(default_factory=dict)
    max_stage: int | None = None
    stage: int | None = None
    total_words: int | None = None
    completed_words: int | None = None
    unit_completed_words: int | None = None
    outcome: UnitOutcome | None = None
    page_index: int | None = None
