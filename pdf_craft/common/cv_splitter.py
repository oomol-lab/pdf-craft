import sys
from typing import Generic, TypeVar

P = TypeVar("P")


class _Group(Generic[P]):
    def __init__(self, items: list[tuple[float, P]]):
        self._items: list[tuple[float, P]] = items
        self._cached_cv: float | None = None
        self._cached_size: float | None = None

    @property
    def items(self) -> list[tuple[float, P]]:
        return self._items

    @property
    def cv(self) -> float:
        if self._cached_cv is None:
            self._cached_cv = self._calculate_cv(
                values=[size for size, _ in self._items],
            )
        return self._cached_cv

    @property
    def size(self) -> float:
        if self._cached_size is None:
            if self._items:
                self._cached_size = sum(size for size, _ in self._items) / len(
                    self._items
                )
            else:
                self._cached_size = 0.0
        return self._cached_size

    def _calculate_cv(self, values: list[float]) -> float:
        if not values or len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        if mean == 0:
            return float("inf")
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std = variance**0.5
        return std / mean


def split_by_cv(
    payload_items: list[tuple[float, P]],
    max_cv: float = 0.0,
    max_groups: int = sys.maxsize,
) -> list[list[P]]:
    """通过控制 CV （变异系数）将 payload 分组，返回分组后的 payload 列表"""

    if len(payload_items) <= 2:
        return [[payload for _, payload in payload_items]]

    groups: list[_Group[P]] = [_Group(items=payload_items)]

    while len(groups) < max_groups:
        max_cv_group_index = _find_max_cv_group_index(groups, max_cv)
        if max_cv_group_index == -1:
            break

        splitted = _split_group_by_max_gap(groups[max_cv_group_index].items)
        if splitted is None:
            break

        group1_items, group2_items = splitted
        groups[max_cv_group_index] = _Group(items=group1_items)
        groups.insert(max_cv_group_index + 1, _Group(items=group2_items))

    return [
        [payload for _, payload in group.items]
        for group in sorted(groups, key=lambda g: g.size)
    ]


def _find_max_cv_group_index(
    groups: list[_Group[P]],
    max_cv: float,
) -> int:
    max_cv_group_index = -1
    max_cv_value = max_cv

    for i, group in enumerate(groups):
        if len(group.items) <= 2:
            continue

        if group.cv > max_cv_value:
            max_cv_value = group.cv
            max_cv_group_index = i

    return max_cv_group_index


def _split_group_by_max_gap(
    group: list[tuple[float, P]],
) -> tuple[list[tuple[float, P]], list[tuple[float, P]]] | None:
    sorted_items = sorted(group, key=lambda x: x[0])
    gaps: list[tuple[float, int]] = []

    for i in range(len(sorted_items) - 1):
        gap = sorted_items[i + 1][0] - sorted_items[i][0]
        gaps.append((gap, i))

    if not gaps:
        return None

    _, split_index = max(gaps, key=lambda x: x[0])
    group1 = sorted_items[: split_index + 1]
    group2 = sorted_items[split_index + 1 :]

    return group1, group2
