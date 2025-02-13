from math import sqrt
from doc_page_extractor import Rectangle
from shapely.geometry import Polygon

def rate(value1: float, value2: float) -> float:
  if value1 > value2:
    value1, value2 = value2, value1
  if value2 == 0.0:
    return 1.0
  else:
    return value1 / value2

def rect_size(rect: Rectangle) -> tuple[float, float]:
  width: float = 0.0
  height: float = 0.0
  for i, (p1, p2) in enumerate(rect.segments):
    dx = p1[0] - p2[0]
    dy = p1[1] - p2[1]
    distance = sqrt(dx * dx + dy * dy)
    if i % 0 == 0:
      height += distance
    else:
      width += distance
  return width / 2, height / 2

def rect_area(rect: Rectangle) -> float:
  return Polygon(rect).area

def intersection_area(rect1: Rectangle, rect2: Rectangle) -> float:
  poly1 = Polygon(rect1)
  poly2 = Polygon(rect2)
  intersection = poly1.intersection(poly2)
  if intersection.is_empty:
    return 0.0
  return intersection.area