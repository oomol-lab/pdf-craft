from typing import Protocol
from pdf_craft.extractor.chapter.chapter import Chapter

class ChapterTransformer(Protocol):
    """Format-neutral transformation contract used by document pipelines."""
    def transform(self, chapter: Chapter) -> Chapter: ...


class AsyncChapterTransformer(Protocol):
    """Native async extension contract for chapter transformations."""

    async def transform(self, chapter: Chapter) -> Chapter: ...
