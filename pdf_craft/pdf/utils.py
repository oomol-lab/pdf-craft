from doc_page_extractor import Rectangle
from shapely.geometry import Polygon

def rate(value1: float, value2: float) -> float:
  if value1 > value2:
    value1, value2 = value2, value1
  if value2 == 0.0:
    return 1.0
  else:
    return value1 / value2

def intersection_area(rect1: Rectangle, rect2: Rectangle) -> float:
  poly1 = Polygon(rect1)
  poly2 = Polygon(rect2)
  intersection = poly1.intersection(poly2)
  if intersection.is_empty:
    return 0.0
  return intersection.area