from typing import Iterable
from dataclasses import dataclass

from ..pdf import PageLayout


_MIN_SIZE_RATE = 0.15

@dataclass
class _Projection:
    center: float
    size: float
    weight: float
    layout: PageLayout


def split_reading_serials(raw_layouts: list[PageLayout]) -> list[list[PageLayout]]:
    if not raw_layouts:
        return []

    projections: list[_Projection] = []

    for layout in raw_layouts:
        x1, y1, x2, y2 = layout.det
        projections.append(_Projection(
            center=(x1 + x2) / 2,
            size=x2 - x1,
            weight=float(y2 - y1),
            layout=layout,
        ))

    # 1. 预处理：避免毛刺
    avg_size = sum(p.size for p in projections) / len(projections)
    min_size_threshold = avg_size * _MIN_SIZE_RATE

    rectangles = []
    for p in projections:
        size = max(p.size, min_size_threshold)
        rectangles.append(_Rect(
            left=p.center - size / 2,
            right=p.center + size / 2,
            height=p.weight
        ))

    # 2. 使用 _histograms 标准化为直方图段
    histogram_segments = list(_histograms(rectangles))

    # 3. 找峰和谷（基于段的高度）
    valleys_idx = []
    peaks_idx = []

    for i in range(1, len(histogram_segments) - 1):
        curr_h = histogram_segments[i].height
        prev_h = histogram_segments[i - 1].height
        next_h = histogram_segments[i + 1].height

        if curr_h < prev_h and curr_h < next_h:
            valleys_idx.append(i)
        elif curr_h > prev_h and curr_h > next_h:
            peaks_idx.append(i)

    # 4. 过滤谷值：只保留两侧都有显著峰的谷
    if not valleys_idx or not peaks_idx:
        return [[p.layout for p in projections]]

    max_height = max(seg.height for seg in histogram_segments)
    peak_threshold = max_height * 0.2
    peaks_set = set(peaks_idx)

    filtered_valleys = []
    for valley_idx in valleys_idx:
        # 找左侧最近的峰
        left_peak_height = 0
        for i in range(valley_idx - 1, -1, -1):
            if i in peaks_set:
                left_peak_height = histogram_segments[i].height
                break

        # 找右侧最近的峰
        right_peak_height = 0
        for i in range(valley_idx + 1, len(histogram_segments)):
            if i in peaks_set:
                right_peak_height = histogram_segments[i].height
                break

        # 两侧都有显著的峰
        if left_peak_height > peak_threshold and right_peak_height > peak_threshold:
            filtered_valleys.append(valley_idx)

    if not filtered_valleys:
        return [[p.layout for p in projections]]

    # 5. 将谷的位置转换为 x 坐标边界（使用谷段的中心）
    import bisect

    boundaries = sorted([
        (histogram_segments[idx].left + histogram_segments[idx].right) / 2
        for idx in filtered_valleys
    ])

    # 6. 分组
    groups: list[list[PageLayout]] = [[] for _ in range(len(boundaries) + 1)]
    for p in projections:
        group_idx = bisect.bisect_right(boundaries, p.center)
        groups[group_idx].append(p.layout)

    return [g for g in groups if g]


@dataclass
class _Rect:
    left: float
    right: float
    height: float

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