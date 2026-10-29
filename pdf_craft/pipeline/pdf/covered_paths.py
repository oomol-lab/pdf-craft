"""Discard vector fills which an opaque erasure will completely cover.

Only simple filled paths are eligible. Strokes, clipping paths, images, text,
shadings and Form XObjects remain untouched. Bounds include Bezier control
points, so the entire curve (not merely its endpoints) must lie inside a mask.
"""
from math import isfinite

from .eraser import EraseRectangle


_PATH = {b'm', b'l', b'c', b'v', b'y', b'h', b're'}
_END = {b'S', b's', b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*', b'n'}


def _concatenate(current, incoming):
    a, b, c, d, e, f = current
    x, y, z, w, u, v = incoming
    return (x*a+y*c, x*b+y*d, z*a+w*c, z*b+w*d, u*a+v*c+e, u*b+v*d+f)


def _points(operator, operands, matrix):
    values = [float(value) for value in operands]
    if operator == b're':
        x, y, width, height = values
        values = [x, y, x+width, y, x+width, y+height, x, y+height]
    a, b, c, d, e, f = matrix
    return [(a*x+c*y+e, b*x+d*y+f) for x, y in zip(values[::2], values[1::2])]


def remove_covered_fills(page, erasures: tuple[EraseRectangle, ...], page_height: float) -> int:
    """Prune only provably invisible paths before adding the erasure overlay."""
    content = page.get_contents()
    if not erasures or content is None:
        return 0
    # Stay slightly inside the opaque mask, including its antialiased edge.
    margin = 1.0
    masks = [(r.rectangle.x+margin, page_height-r.rectangle.bottom+margin,
              r.rectangle.x+r.rectangle.width-margin, page_height-r.rectangle.top-margin)
             for r in erasures]
    matrix = (1., 0., 0., 1., 0., 0.)
    stack = []
    path_indexes = []
    points = []
    eligible = True
    removed = set()
    fills = 0
    operations = content.operations
    for index, (operands, operator) in enumerate(operations):
        if operator in _PATH:
            path_indexes.append(index)
            points.extend(_points(operator, operands, matrix))
            continue
        if operator in _END:
            if eligible and operator in {b'f', b'F', b'f*'} and points:
                xs, ys = zip(*points)
                bounds = min(xs), min(ys), max(xs), max(ys)
                if all(isfinite(value) for point in points for value in point) and any(
                    left <= bounds[0] and bottom <= bounds[1]
                    and bounds[2] <= right and bounds[3] <= top
                    for left, bottom, right, top in masks
                ):
                    removed.update(path_indexes)
                    removed.add(index)
                    fills += 1
            path_indexes = []
            points = []
            eligible = True
            continue
        if path_indexes:
            # Includes W/W*: clipping takes effect when the path ends and must
            # remain even if its geometry lies completely inside an erasure.
            eligible = False
        if operator == b'q':
            stack.append(matrix)
        elif operator == b'Q':
            if not stack:
                return 0  # Do not optimize malformed graphics state.
            matrix = stack.pop()
        elif operator == b'cm':
            matrix = _concatenate(matrix, [float(value) for value in operands])
    if stack:
        return 0
    if removed:
        content.operations = [entry for index, entry in enumerate(operations) if index not in removed]
        page.replace_contents(content)
    return fills
