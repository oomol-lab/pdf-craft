from doc_page_extractor import Rectangle
from shapely.geometry import Polygon

def rate(value1: float, value2: float) -> float:
  if value1 > value2:
    value1, value2 = value2, value1
  if value2 == 0.0:
    return 1.0
  else:
    return value1 / value2

def intersection_area_size(rect1: Rectangle, rect2: Rectangle) -> tuple[float, float]:
  poly1 = Polygon(rect1)
  poly2 = Polygon(rect2)
  intersection = poly1.intersection(poly2)

  if intersection.is_empty:
    return 0.0, 0.0

  if not isinstance(intersection, Polygon):
    return intersection.area, 0.0

  x1: float = float("inf")
  y1: float = float("inf")
  x2: float = float("-inf")
  y2: float = float("-inf")

  exterior = intersection.exterior
  if exterior is None:
    return 0.0, 0.0

  for x, y in exterior.coords:
    x1 = min(x1, x)
    y1 = min(y1, y)
    x2 = max(x2, x)
    y2 = max(y2, y)
  return x2 - x1, y2 - y1