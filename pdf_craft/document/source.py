from dataclasses import dataclass


@dataclass(frozen=True)
class SourceLocation:
    """Location of one v3 source fragment in the rendered PDF page."""

    page_index: int
    bbox: tuple[int, int, int, int]
    source_order: int


def source_location(
    page_index: int,
    source_order: int,
    bbox: tuple[int, int, int, int],
) -> SourceLocation:
    """Create a v3 source location from a fragment's canonical fields."""
    return SourceLocation(page_index=page_index, source_order=source_order, bbox=bbox)
