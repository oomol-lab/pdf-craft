from __future__ import annotations

from typing import Iterable, Sequence, Callable
from doc_page_extractor import Point, Rectangle, Layout, OCRFragment
from .text_matcher import check_texts_matching_rate
from .utils import rate, intersection_area_size

class _Shape:
  def __init__(self, layout: Layout):
    self.layout: Layout = layout
    self.pre: list[Layout | None] = [None, None]
    self.nex: list[Layout | None] = [None, None]

  @property
  def distance2(self) -> float:
    x, y = self.layout.rect.lt
    return x*x + y*y

class Section:
  def __init__(
      self,
      page_index: int,
      layouts: Iterable[Layout],
    ) -> list[Layout]:
    self._page_index: int = page_index
    self._shapes: list[_Shape] = [_Shape(layout) for layout in layouts]

  @property
  def page_index(self) -> int:
    return self._page_index

  def framework(self) -> list[Layout]:
    pre_shapes = list(self._side_framework(lambda shape: shape.pre))
    nex_shapes = list(self._side_framework(lambda shape: shape.nex))
    framework_shapes: list[_Shape]

    if len(pre_shapes) < len(nex_shapes):
      framework_shapes = nex_shapes
    else:
      framework_shapes = pre_shapes

    return [shape.layout for shape in framework_shapes]

  def _side_framework(self, get_side: Callable[[_Shape], list[Layout | None]]):
    for shape in self._shapes:
      matched_shapes = get_side(shape)
      # The logic here should be understood as the processing of book page scans.
      # When the book is opened, the even-numbered pages will be on the left and the odd-numbered pages will be on the right.
      # Usually when editing and typesetting, even-numbered pages will be matched with one typesetting format,
      # and odd-numbered pages will be matched with another typesetting format, in order to achieve aesthetics.
      # Therefore, typesetting format (framework) matching usually takes into account the situation of matching every other page.
      if matched_shapes[0] is not None or matched_shapes[1] is not None:
        yield shape

  def link_next(self, next: Section, offset: int) -> None:
    assert offset in (1, 2), f"invalid offset {offset}"
    matched_shapes_matrix: list[list[_Shape]] = []
    for shape in self._shapes:
      matched_shapes_matrix.append([
        # pylint: disable=W0212
        next_shape for next_shape in next._shapes
        if self._is_shape_contents_matches(shape, next_shape)
      ])

    origin_shapes = self._find_origin_shapes(matched_shapes_matrix)
    if origin_shapes is not None:
      origins = (origin_shapes[0].layout.rect.lt, origin_shapes[1].layout.rect.lt)
      for shape1, shape2 in self._iter_matched_shapes(origins, matched_shapes_matrix):
        shape1.nex[offset - 1] = shape2
        shape2.pre[offset - 1] = shape1

  def _is_shape_contents_matches(self, shape1: _Shape, shape2: _Shape) -> bool:
    size_match_rate = 0.95
    layout1 = shape1.layout
    layout2 = shape2.layout
    size1 = layout1.rect.size
    size2 = layout2.rect.size
    if rate(size1[0], size2[0]) < size_match_rate or \
       rate(size1[1], size2[1]) < size_match_rate:
      return False

    matched_count: int = 0

    for fragment1 in layout1.fragments:
      for fragment2 in layout2.fragments:
        if self._is_fragments_matches(layout1, layout2, fragment1, fragment2):
          matched_count += 1
          break

    fragments_count = max(len(layout1.fragments), len(layout2.fragments))
    if fragments_count == 0:
      return True

    return self._check_group_matches(
       matched_count / fragments_count,
      fragments_count,
      (0.0, 0.45, 0.45, 0.6, 0.8, 0.95),
    )

  def _is_fragments_matches(self, layout1: Layout, layout2: Layout, fragment1: OCRFragment, fragment2: OCRFragment) -> bool:
    size_match_rate = 0.85
    rect1 = self._relative_rect(layout1.rect.lt, fragment1.rect)
    rect2 = self._relative_rect(layout2.rect.lt, fragment2.rect)
    if self._intersection_rate(rect1, rect2) < size_match_rate:
      return False

    text_rate, text_length = check_texts_matching_rate(fragment1.text, fragment2.text)
    return self._check_group_matches(
      text_rate,
      text_length,
      (0.0, 0.0, 0.5, 0.55, 0.6, 0.75, 0.8, 0.95),
    )

  def _find_origin_shapes(self, matched_shapes: list[list[_Shape]]):
    origin_shape1: _Shape | None = None
    origin_shape2: _Shape | None = None
    origin_matched_shape: list[_Shape] | None = None
    min_distance2: float = float("inf")

    for shape, matched_shapes in zip(self._shapes, matched_shapes):
      if len(matched_shapes) == 0:
        continue
      distance2 = shape.distance2
      if distance2 < min_distance2:
        origin_shape1 = shape
        origin_matched_shape = matched_shapes
        min_distance2 = distance2

    if origin_shape1 is None:
      return None

    assert origin_matched_shape is not None
    min_distance2 = float("inf")

    for shape in origin_matched_shape:
      distance2 = shape.distance2
      if distance2 < min_distance2:
        origin_shape2 = shape
        min_distance2 = distance2

    assert origin_shape2 is not None
    return origin_shape1, origin_shape2

  def _iter_matched_shapes(self, origins: tuple[Point, Point], matched_shapes_matrix: list[list[_Shape]]):
    for shape1, matched_shapes in zip(self._shapes, matched_shapes_matrix):
      if len(matched_shapes) == 0:
        continue

      rect1 = self._relative_rect(origins[0], shape1.layout.rect)
      max_area_rate: float = float("-inf")
      matched_shape2: _Shape | None = None

      for shape2 in matched_shapes:
        rect2 = self._relative_rect(origins[1], shape2.layout.rect)
        size_rate = self._intersection_rate(rect1, rect2)
        if size_rate > max_area_rate:
          max_area_rate = size_rate
          matched_shape2 = shape2

      if max_area_rate >= 0.85:
        assert matched_shape2 is not None
        yield shape1, matched_shape2

  def _check_group_matches(self, calculated_rate: float, group_count: int, rates_list: Sequence[float]) -> bool:
    if group_count < len(rates_list):
      return calculated_rate >= rates_list[group_count]
    else:
      return calculated_rate >= rates_list[-1]

  def _relative_rect(self, origin: Point, rect: Rectangle) -> Rectangle:
    return Rectangle(
      lt=(rect.lt[0] - origin[0], rect.lt[1] - origin[1]),
      rt=(rect.rt[0] - origin[0], rect.rt[1] - origin[1]),
      lb=(rect.lb[0] - origin[0], rect.lb[1] - origin[1]),
      rb=(rect.rb[0] - origin[0], rect.rb[1] - origin[1])
    )

  def _intersection_rate(self, rect1: Rectangle, rect2: Rectangle) -> float:
    width1, height1 = rect1.size
    width2, height2 = rect2.size
    width, height = intersection_area_size(rect1, rect2)
    width_rate = width / max(width1, width2)
    height_rate = height / max(height1, height2)
    return 0.5 * (width_rate + height_rate)
