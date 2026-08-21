from dataclasses import dataclass


@dataclass(frozen=True)
class SourceLocation:
    """Location of a document block in the rendered PDF page."""

    page_index: int
    bbox: tuple[int, int, int, int]
    order: int


def source_location(page_index: int, order: int, det: tuple[int, int, int, int]) -> SourceLocation:
    return SourceLocation(page_index=page_index, order=order, bbox=det)
