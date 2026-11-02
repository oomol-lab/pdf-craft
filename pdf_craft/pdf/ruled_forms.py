"""Retain clearly ruled, fragmented OCR forms as artwork rather than prose."""
from dataclasses import replace

from PIL import Image

from ..common import AssetHub
from .types import PageLayout


def preserve_ruled_forms(image: Image.Image, body: list[PageLayout], notes: list[PageLayout],
                         assets: AssetHub) -> tuple[list[PageLayout], list[PageLayout]]:
    """Require a connected grid and multiple independent text fields.

    This does not reconstruct missing cell semantics. Already structured assets
    remain unchanged; a grid fragmented into prose is kept as a source image.
    """
    if sum(item.ref == 'text' for item in body) < 4:
        return body, notes
    regions = []
    for box in ruled_regions(image):
        box = _include_border_labels(box, (*body, *notes))
        members = [layout for layout in body if _inside(layout.det, box)]
        texts = [layout for layout in members if layout.ref == 'text']
        if len(texts) < 4 or not _parallel_fields(texts):
            continue
        # A crossing source cannot safely be split or partly hidden.
        if any(_intersects(layout.det, box) and not _inside(layout.det, box)
               for layout in (*body, *notes)):
            continue
        regions.append((box, members))
    for box, members in regions:
        first = min(body.index(item) for item in members)
        ids = {id(item) for item in members}
        body = [item for item in body if id(item) not in ids]
        body.insert(first, PageLayout('image', box, '', 0, assets.clip(image, box)))
        notes = [item for item in notes if not _inside(item.det, box)]
    return ([replace(item, order=index) for index, item in enumerate(body)],
            [replace(item, order=index) for index, item in enumerate(notes)])


def _include_border_labels(box, layouts):
    """Include short labels straddling a border; never absorb crossing prose."""
    left, top, right, bottom = box
    for item in layouts:
        a, b, c, d = item.det
        height = d - b
        if (_intersects(item.det, box) and not _inside(item.det, box)
                and height <= .15 * (box[3] - box[1]) and c - a <= .5 * (box[2] - box[0])
                and max(box[0] - a, box[1] - b, c - box[2], d - box[3]) <= height):
            left, top, right, bottom = min(left, a), min(top, b), max(right, c), max(bottom, d)
    return left, top, right, bottom


def _inside(a, b):
    return b[0] <= a[0] and b[1] <= a[1] and a[2] <= b[2] and a[3] <= b[3]


def _intersects(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _parallel_fields(layouts):
    for i, first in enumerate(layouts):
        a, b, c, d = first.det
        for second in layouts[i + 1:]:
            x, y, z, t = second.det
            if ((c <= x or z <= a)
                    and min(d, t) - max(b, y) >= .5 * min(d - b, t - y)):
                return True
    return False


def ruled_regions(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """Find connected horizontal/vertical rules on a bounded grayscale image."""
    size = image.size
    scale = min(1., 1600 / max(size))
    with image.resize((max(1, round(size[0] * scale)), max(1, round(size[1] * scale)))) as resized:
        with resized.convert('L') as small:
            width, height = small.size
            pixels = small.tobytes()
    horizontal = _runs(pixels, width, height, max(24, width // 30), vertical=False)
    vertical = _runs(pixels, width, height, max(24, height // 50), vertical=True)
    # No geometric conclusion from an exceptionally dense/dark raster. Bound
    # pairwise work as well as raster memory; ordinary grids have few rules.
    if len(horizontal) * len(vertical) > 1_000_000:
        return []
    lines = horizontal + vertical
    parents = list(range(len(lines)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for h, (x1, y, x2, _) in enumerate(horizontal):
        for v, (x, y1, _, y2) in enumerate(vertical, len(horizontal)):
            # A rounded frame's straight sides stop before its corners. The
            # tolerance is bounded in the analysis raster, independent of DPI.
            if x1 - 18 <= x <= x2 + 18 and y1 - 18 <= y <= y2 + 18:
                parents[root(v)] = root(h)
    groups: dict[int, list[int]] = {}
    for index in range(len(lines)):
        groups.setdefault(root(index), []).append(index)
    result = []
    for group in groups.values():
        hs = sorted({lines[i][1] for i in group if i < len(horizontal)})
        vs = sorted({lines[i][0] for i in group if i >= len(horizontal)})
        if _distinct(hs) < 3 or _distinct(vs) < 3:
            continue
        boxes = [lines[i] for i in group]
        result.append((max(0, int((min(b[0] for b in boxes) - 3) / scale)),
                       max(0, int((min(b[1] for b in boxes) - 3) / scale)),
                       min(size[0], int((max(b[2] for b in boxes) + 4) / scale)),
                       min(size[1], int((max(b[3] for b in boxes) + 4) / scale))))
    return result


def _distinct(values):
    count, previous = 0, -10
    for value in values:
        if value > previous + 3:
            count += 1
        previous = value
    return count


def _runs(pixels, width, height, minimum, *, vertical):
    result = []
    for outer in range(width if vertical else height):
        start, last, ink = None, -1, 0
        for inner in range(height if vertical else width):
            index = inner * width + outer if vertical else outer * width + inner
            if pixels[index] < 100:
                if start is None:
                    start = inner
                    ink = 0
                last = inner
                ink += 1
            elif start is not None and inner - last > 2:
                if last - start + 1 >= minimum and ink >= .9 * (last - start + 1):
                    result.append((outer, start, outer, last) if vertical else (start, outer, last, outer))
                start = None
        if start is not None and last - start + 1 >= minimum and ink >= .9 * (last - start + 1):
            result.append((outer, start, outer, last) if vertical else (start, outer, last, outer))
    return result
