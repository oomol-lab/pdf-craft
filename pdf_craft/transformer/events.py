"""Events emitted by document translation workflows."""

from dataclasses import dataclass
from enum import Enum, auto


class TranslationEventKind(Enum):
    """Lifecycle events for a translation scope and its items."""

    START = auto()
    ITEM_START = auto()
    ITEM_COMPLETE = auto()
    PROGRESS = auto()
    COMPLETE = auto()


class TranslationItemKind(Enum):
    """Kinds of document items that may be translated."""

    TOC = auto()
    METADATA = auto()
    CHAPTER = auto()
    FURNITURE = auto()


@dataclass
class TranslationEvent:
    """A low-level translation fact, parallel to :class:`OCREvent`.

    Word counts refer to finalized source work, including fallback/skip,
    not translated output length.  Item identifiers are opaque to the event protocol.
    """

    kind: TranslationEventKind
    chapter_count: int | None = None
    has_toc: bool | None = None
    has_metadata: bool | None = None
    item_kind: TranslationItemKind | None = None
    item_id: str | int | None = None
    item_completed_words: int | None = None
    item_total_words: int | None = None
    completed_words: int | None = None
    total_words: int | None = None
