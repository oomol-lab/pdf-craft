from typing import Protocol
from pdf_craft.sequence.chapter import Chapter

class ChapterTransformer(Protocol):
    """Format-neutral transformation contract used by document pipelines."""
    def transform(self, chapter: Chapter) -> Chapter: ...
