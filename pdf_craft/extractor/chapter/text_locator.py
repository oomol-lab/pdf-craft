"""Source-local text matching with reversible whitespace comparison offsets."""
from bisect import bisect_left
from dataclasses import dataclass

from ...common.whitespace import is_whitespace


def _compact(text: str) -> str:
    return ''.join(char for char in text if not is_whitespace(char))


@dataclass
class _TextView:
    source: str
    positions: list[int]
    text: str

    @classmethod
    def of(cls, source: str) -> '_TextView':
        positions = [index for index, char in enumerate(source) if not is_whitespace(char)]
        return cls(source, positions, ''.join(source[index] for index in positions))

    def boundary(self, index: int) -> int:
        # Interior whitespace belongs to the left fragment. Source ends retain
        # their sentinel meaning, including any leading/trailing whitespace.
        if index == 0:
            return 0
        if index == len(self.positions):
            return len(self.source)
        return self.positions[index]

    def original_boundary(self, position: int) -> int:
        return self.boundary(bisect_left(self.positions, position))


def _matches(text: str, before: str, mark: str, after: str) -> list[tuple[int, int]]:
    needle = before + mark + after
    if not needle:
        return []
    result = []
    cursor = 0
    while (found := text.find(needle, cursor)) >= 0:
        start = found + len(before)
        result.append((start, start + len(mark)))
        cursor = found + 1
    return result


def locate_text(text: str, before: str, mark: str, after: str) -> list[tuple[int, int]]:
    """Find exact quotes first, then quotes differing only in shared whitespace.

    Returned spans always index the untouched source. Empty spans use a shared
    cut convention even on the exact path, so independently quoted adjacent
    ranges cannot lose or duplicate the space between them. Distinct source
    occurrences remain distinct; no fuzzy scoring or semantic decisions here.
    """
    left, middle, right = _compact(before), _compact(mark), _compact(after)
    if not left + middle + right:
        return []
    view = _TextView.of(text)
    exact = _matches(text, before, mark, after)
    if exact:
        return sorted(set((view.original_boundary(start), view.original_boundary(end))
                          if not mark else (start, end) for start, end in exact))
    matches = _matches(view.text, left, middle, right)
    if not middle:
        # A whitespace-only marker is not an invented visible reference.
        return sorted(set((view.boundary(start), view.boundary(start)) for start, _ in matches))
    return [(view.positions[start], view.positions[end - 1] + 1) for start, end in matches]
