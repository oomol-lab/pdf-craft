from typing import Protocol
from pdf_craft.extractor.chapter.chapter import Chapter

class ChapterTransformer(Protocol):
    """Async format-neutral transformation contract used by document pipelines."""
    async def transform(self, chapter: Chapter) -> Chapter: ...


class SyncChapterTransformer(Protocol):
    """Synchronous transformer accepted only by compatibility facades."""
    def transform(self, chapter: Chapter) -> Chapter: ...
