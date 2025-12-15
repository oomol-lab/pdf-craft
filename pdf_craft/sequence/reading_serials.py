from typing import Iterable, Generator
from dataclasses import dataclass
from enum import auto, Enum

from ..pdf import PageLayout


_MIN_SIZE_RATE = 0.15

@dataclass
class _Projection:
    center: float
    size: float
    weight: float
    layout: PageLayout


def split_reading_serials(raw_layouts: list[PageLayout]) -> Generator[list[PageLayout], None, None]:
    if not raw_layouts:
        return

    projections: list[_Projection] = list()
    for layout in raw_layouts:
        x1, y1, x2, y2 = layout.det
        projections.append(_Projection(
            center=(x1 + x2) / 2,
            size=x2 - x1,
            weight=float(y2 - y1),
            layout=layout,
        ))

    avg_size = sum(p.size for p in projections) / len(projections)
    min_size_threshold = avg_size * _MIN_SIZE_RATE

    rectangles: list[_Rect] = []
    for p in projections:
        size = max(p.size, min_size_threshold) # 避免毛刺
        rectangles.append(_Rect(
            left=p.center - size / 2,
            right=p.center + size / 2,
            height=p.weight
        ))

    for valley in _find_valleys(rectangles):
        next_group: list[_Projection] = []
        for projection in projections:
            if projection.center < valley:
                next_group.append(projection)
        for project in next_group:
            projections.remove(project)
        if next_group:
            yield [p.layout for p in next_group]

    if projections:
        yield [p.layout for p in projections]


@dataclass
class _Rect:
    left: float
    right: float
    height: float

def _find_valleys(rectangles: Iterable[_Rect]):
    window: list[tuple[float, float]] = []
    prev_class: _WindowClass = _WindowClass.OTHER
    flat_list: list[float] = []

    for rect in _histograms(rectangles):
        center = (rect.left + rect.right) / 2
        window.append((center, rect.height))
        if len(window) > 3:
            window.pop(0)
        if len(window) != 3:
            continue
        prev, curr, next = window # pylint: disable=unbalanced-tuple-unpacking
        clazz = _classify_window(prev, curr, next)
        if clazz == _WindowClass.TOUCHED_GROUND:
            flat_list = [curr[0]]

        elif clazz == _WindowClass.LEFT_GROUND:
            if prev_class in (_WindowClass.TOUCHED_GROUND, _WindowClass.FLAT_GROUND):
                flat_list.append(curr[0])
                yield sum(flat_list) / len(flat_list)
            elif flat_list:
                flat_list = []

        elif clazz == _WindowClass.FLAT_GROUND:
            if prev_class == _WindowClass.TOUCHED_GROUND:
                flat_list.append(curr[0])
            elif prev_class == _WindowClass.FLAT_GROUND and flat_list:
                flat_list.append(curr[0])
            elif flat_list:
                flat_list = []

        elif clazz == _WindowClass.AT_VALLEY:
            yield curr[0]
            if flat_list:
                flat_list = []
        elif flat_list:
            flat_list = []
        prev_class = clazz


def _histograms(raw_rectangles: Iterable[_Rect]):
    rectangles = list(raw_rectangles)
    rectangles.sort(key=lambda r: (r.left, r.right))
    forbidden: float = float("-inf")

    for i, rect in enumerate(rectangles):
        left = max(rect.left, forbidden)
        right = rect.right
        for j in range(i + 1, len(rectangles)):
            next_rect = rectangles[j]
            if next_rect.height > rect.height:
                right = min(right, next_rect.left)
        if left < right:
            yield _Rect(
                left=left,
                right=right,
                height=rect.height
            )
            forbidden = right

class _WindowClass(Enum):
    TOUCHED_GROUND = auto()
    LEFT_GROUND = auto()
    FLAT_GROUND = auto()
    AT_VALLEY = auto()
    OTHER = auto()

def _classify_window(
        prev: tuple[float, float],
        curr: tuple[float, float],
        next: tuple[float, float],
    ) -> _WindowClass:
    _, prev_h = prev
    _, curr_h = curr
    _, next_h = next
    if prev_h > curr_h and curr_h == next_h:
        return _WindowClass.TOUCHED_GROUND
    elif prev_h == curr_h and curr_h < next_h:
        return _WindowClass.LEFT_GROUND
    elif prev_h == curr_h and curr_h == next_h:
        return _WindowClass.FLAT_GROUND
    elif prev_h > curr_h < next_h:
        return _WindowClass.AT_VALLEY
    else:
        return _WindowClass.OTHER