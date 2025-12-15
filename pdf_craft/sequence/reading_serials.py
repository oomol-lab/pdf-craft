from typing import TypeVar, Generic, Iterable, Generator
from dataclasses import dataclass
from enum import auto, Enum

from ..pdf import PageLayout


_MIN_SIZE_RATE = 0.15
_CV = 0.1
_T = TypeVar("_T")

@dataclass
class _Projection(Generic[_T]):
    center: float
    size: float
    weight: float
    payload: _T


def split_reading_serials(raw_layouts: list[PageLayout]) -> Generator[list[PageLayout], None, None]:
    """
    将 OCR 识别的文字块按列分组，用于多列布局的阅读顺序识别。

    问题背景：
    - 扫描文档可能包含多列布局（双栏、三栏等学术论文或书籍）
    - 图片、表格等元素可能导致局部文字块被挤压，形成临时的列布局
    - 需要识别这些列的边界，以便按正确的阅读顺序（从左到右，从上到下）处理文字

    算法思路：
    1. 将所有 layout 在 x 轴上投影（使用中心点和宽度）
    2. 构建加权直方图（高度作为权重，避免小字符干扰）
    3. 分析直方图的波峰和波谷：波峰对应列中心，波谷对应列间隙
    4. 在显著的波谷处切分，将文字块分配到对应的列组

    输入：原始的 layout 列表（从 OCR 获取，按页面位置无序）
    输出：按列分组的 layout 生成器，每组代表一列中的文字块
    """
    if not raw_layouts:
        return

    layout_pairs: list[tuple[int, int, PageLayout]] = [] # order, group_id, layout
    for group_id, group_layouts in enumerate(_group_projects(raw_projections=(
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

def _group_projects(raw_projections: Iterable[_Projection[_T]]) -> Generator[list[_T], None, None]:
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
            for sub_group in _split_projections_by_size_cv(next_group):
                yield [p.payload for p in sub_group]

    if projections:
        for sub_group in _split_projections_by_size_cv(projections):
            yield [p.payload for p in sub_group]


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


def _split_projections_by_size_cv(projections: list[_Projection[_T]], max_cv: float = _CV):
    """
    按 size 的变异系数（CV = std/mean）将投影分组。
    如果整组的 CV > max_cv，则通过寻找最大 size 差距来切分，递归处理每个子组。

    算法：
    1. 计算整组的 CV
    2. 如果 CV <= max_cv，直接 yield
    3. 如果 CV > max_cv：
       - 按 size 排序
       - 找到相邻元素间最大的差距
       - 在该处切分成两组
       - 递归处理每个子组
    """
    if len(projections) < 3:
        # 少于 3 个元素时，标准差意义不大，直接返回
        yield projections
        return

    # 提取每个投影的 size
    sizes = [p.size for p in projections]

    cv = _calculate_cv(sizes)

    if cv <= max_cv:
        # CV 满足要求，直接返回
        yield projections
    else:
        # CV 超标，需要切分
        # 按 size 排序，同时保持原始 projections 的对应关系
        sorted_items = sorted(zip(sizes, projections), key=lambda x: x[0])

        if len(sorted_items) < 2:
            yield projections
            return

        # 计算相邻元素的 size 差距
        gaps = []
        for i in range(len(sorted_items) - 1):
            gap = sorted_items[i + 1][0] - sorted_items[i][0]
            gaps.append((gap, i))

        if not gaps:
            yield projections
            return

        # 找到最大差距的位置
        _, split_index = max(gaps, key=lambda x: x[0])

        # 在最大差距处切分
        group1 = [proj for _, proj in sorted_items[:split_index + 1]]
        group2 = [proj for _, proj in sorted_items[split_index + 1:]]

        # 递归处理两个子组
        if group1:
            yield from _split_projections_by_size_cv(group1, max_cv)
        if group2:
            yield from _split_projections_by_size_cv(group2, max_cv)


def _calculate_cv(values: list[float]) -> float:
    """
    计算变异系数（Coefficient of Variation）：CV = std / mean
    """
    if not values or len(values) < 2:
        return 0.0

    mean = sum(values) / len(values)
    if mean == 0:
        return float('inf')

    variance = sum((x - mean) ** 2 for x in values) / len(values)
    std = variance ** 0.5

    return std / mean