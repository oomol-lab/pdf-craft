from typing import TypeVar, Generic, Iterable, Generator
from dataclasses import dataclass
from enum import auto, Enum

from ..pdf import PageLayout


_MIN_SIZE_RATE = 0.15
_T = TypeVar("_T")

@dataclass
class _Projection(Generic[_T]):
    center: float
    size: float
    weight: float
    payload: _T


def split_reading_serials(raw_layouts: list[PageLayout]) -> Generator[list[PageLayout], None, None]:
    if not raw_layouts:
        return

    layout_pairs: list[tuple[int, int, PageLayout]] = [] # order, group_id, layout
    for group_id, group_layouts in enumerate(_group_projections(raw_projections=(
        _wrap_projection(order, layout)
        for order, layout in enumerate(raw_layouts)
    ))):
        for order, layout in group_layouts:
            layout_pairs.append((order, group_id, layout))

    last_group_id = -1
    layouts_buffer: list[PageLayout] = []
    for _, group_id, layout in sorted(layout_pairs, key=lambda p: p[0]):
        if group_id != last_group_id:
            last_group_id = group_id
            if layouts_buffer:
                yield layouts_buffer
                layouts_buffer = []
        layouts_buffer.append(layout)

    if layouts_buffer:
        yield layouts_buffer

def _wrap_projection(index: int, layout: PageLayout) -> _Projection[tuple[int, PageLayout]]:
    x1, y1, x2, y2 = layout.det
    return _Projection(
        center=(x1 + x2) / 2,
        size=x2 - x1,
        weight=float(y2 - y1),
        payload=(index, layout),
    )

def _group_projections(raw_projections: Iterable[_Projection[_T]]) -> Generator[list[_T], None, None]:
    projections = list(raw_projections)
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
            yield [p.payload for p in next_group]

    if projections:
        yield [p.payload for p in projections]


@dataclass
class _Rect:
    left: float
    right: float
    height: float

def _find_valleys(rectangles: Iterable[_Rect]):
    window: list[tuple[float, float]] = []
    prev_class: _WindowClass = _WindowClass.OTHER
    flat_buffer: list[float] = []

    for rect in _histograms(rectangles):
        center = (rect.left + rect.right) / 2
        window.append((center, rect.height))
        if len(window) > 3:
            window.pop(0)
        if len(window) != 3:
            continue
        prev, curr, next = window # pylint: disable=unbalanced-tuple-unpacking
        clazz = _classify_window(prev, curr, next)
        keep_buffer = False

        if clazz == _WindowClass.TOUCHED_GROUND:
            flat_buffer = [curr[0]]
            keep_buffer = True
        elif clazz == _WindowClass.LEFT_GROUND:
            if prev_class in (_WindowClass.TOUCHED_GROUND, _WindowClass.FLAT_GROUND):
                flat_buffer.append(curr[0])
                yield sum(flat_buffer) / len(flat_buffer)
        elif clazz == _WindowClass.FLAT_GROUND:
            if prev_class == _WindowClass.TOUCHED_GROUND or (
               prev_class == _WindowClass.FLAT_GROUND and flat_buffer
            ):
                flat_buffer.append(curr[0])
                keep_buffer = True
        elif clazz == _WindowClass.AT_VALLEY:
            yield curr[0]

        prev_class = clazz
        if not keep_buffer and flat_buffer:
            flat_buffer = []


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