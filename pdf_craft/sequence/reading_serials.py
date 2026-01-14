from dataclasses import dataclass
from enum import Enum, auto
from typing import Generator, Generic, Iterable, TypeVar

from ..common import avg, split_by_cv
from ..pdf import PageLayout

_T = TypeVar("_T")
_CV = 0.1  # Coefficient of Variation

_MIN_SIZE_RATE = 0.15


@dataclass
class _Projection(Generic[_T]):
    center: float
    size: float
    weight: float
    payload: _T


def split_reading_serials(
    raw_layouts: list[PageLayout],
) -> Generator[list[PageLayout], None, None]:
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
    5. 对每一列进一步切分，保证 CV （标准差 / 平均值）在合理范围，此步骤用于被图片挤压的文字块

    输入：原始的 layout 列表（从 OCR 获取，按页面位置无序）
    输出：按列分组的 layout 生成器，每组代表一列中的文字块
    """
    if not raw_layouts:
        return

    layout_pairs: list[tuple[int, int, PageLayout]] = []  # order, group_id, layout
    for group_id, group_layouts in enumerate(
        _group_projects(
            raw_projections=(
                _wrap_projection(order, layout)
                for order, layout in enumerate(raw_layouts)
            )
        )
    ):
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


def _wrap_projection(
    index: int, layout: PageLayout
) -> _Projection[tuple[int, PageLayout]]:
    x1, y1, x2, y2 = layout.det
    return _Projection(
        center=(x1 + x2) / 2,
        size=x2 - x1,
        weight=float(y2 - y1),
        payload=(index, layout),
    )


def _group_projects(
    raw_projections: Iterable[_Projection[_T]],
) -> Generator[list[_T], None, None]:
    projections = list(raw_projections)
    avg_size = avg(p.size for p in projections)
    min_size_threshold = avg_size * _MIN_SIZE_RATE

    rectangles: list[_Rect] = []
    for p in projections:
        size = max(p.size, min_size_threshold)  # 避免毛刺
        rectangles.append(
            _Rect(left=p.center - size / 2, right=p.center + size / 2, height=p.weight)
        )

    for valley in _find_valleys(rectangles):
        next_group: list[_Projection] = []
        for projection in projections:
            if projection.center < valley:
                next_group.append(projection)
        for project in next_group:
            projections.remove(project)
        if next_group:
            for sub_group in split_by_cv(
                payload_items=[(p.size, p) for p in next_group],
                max_cv=_CV,
            ):
                yield [p.payload for p in sub_group]

    if projections:
        for sub_group in split_by_cv(
            payload_items=[(p.size, p) for p in projections],
            max_cv=_CV,
        ):
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
        prev, curr, next = window  # pylint: disable=unbalanced-tuple-unpacking
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
            yield _Rect(left=left, right=right, height=rect.height)
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
