"""Internal structural diagnostics shared by PCEX validation and recovery."""


class ChapterTocMismatch(ValueError):
    """Chapter identities differ from the TOC, without exposing book text."""

    def __init__(self, missing_ids: tuple[int, ...], unexpected_ids: tuple[int, ...],
                 page_indexes: tuple[int, ...]) -> None:
        self.missing_ids = missing_ids
        self.unexpected_ids = unexpected_ids
        self.page_indexes = page_indexes
        super().__init__(
            "chapter IDs do not match toc.xml item IDs: "
            f"missing={missing_ids}, unexpected={unexpected_ids}, pages={page_indexes}"
        )

    def __reduce__(self):
        return type(self), (self.missing_ids, self.unexpected_ids, self.page_indexes)


class FurnitureValidationError(ValueError):
    """Auxiliary furniture is invalid; this does not identify damaged OCR pages."""
