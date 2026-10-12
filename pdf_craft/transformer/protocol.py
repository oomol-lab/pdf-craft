from typing import Protocol
from pdf_craft.extractor.chapter.chapter import Chapter

class _ChapterTransformer(Protocol):
    """Async format-neutral transformation contract used by document pipelines."""
    async def transform(self, chapter: Chapter) -> Chapter: ...
