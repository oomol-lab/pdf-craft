"""Bounded observations of printed lines, independent of paragraph decisions."""

from collections.abc import Iterable
from statistics import median

from PIL.Image import Image

_MAX_CROP_PIXELS = 4_000_000
_DARK_PIXELS = [255 if value < 110 else 0 for value in range(256)]


def first_line_inset(
    image: Image,
    bbox: tuple[int, int, int, int],
    obstructions: Iterable[tuple[int, int, int, int]] = (),
) -> float | None:
    """Measure first-line left inset in units of median printed line height.

    Require a later full line to establish a local left edge. Large crops,
    narrow vertical strips, ambiguous ink and obstructed first lines remain
    unknown; missing evidence must not become a paragraph-boundary decision.
    """
    left, top, right, bottom = bbox
    width, height = right - left, bottom - top
    if (image.mode not in ("L", "RGB") or not 0 <= left < right <= image.width
            or not 0 <= top < bottom <= image.height
            or width < height or width * height > _MAX_CROP_PIXELS):
        return None
    with image.crop(bbox) as region, region.convert("L") as crop:
        with crop.point(_DARK_PIXELS, mode="1") as ink:
            _, rows = ink.getprojection()
            bands = _ink_bands(rows)
            if len(bands) < 2:
                return None
            glyph_height = median(end - start for start, end in bands)
            if bands[0][1] - bands[0][0] < 0.7 * glyph_height:
                return None
            lines = []
            for start, end in bands:
                with ink.crop((0, start, width, end)) as line:
                    bounds = line.getbbox()
                if bounds is not None:
                    lines.append((bounds[0], bounds[2] - bounds[0], start, end))
            full_lines = [line for line in lines[1:] if line[1] >= 0.6 * width]
            if len(full_lines) < 1:
                return None
            baseline = median(line[0] for line in full_lines)
            agreeing = sum(abs(line[0] - baseline) <= 0.3 * glyph_height for line in full_lines)
            if agreeing <= len(full_lines) // 2:
                return None
            first = lines[0]
            first_top, first_bottom = top + first[2], top + first[3]
            if any(y0 < first_bottom and y1 > first_top and x0 < right and x1 > left
                   for x0, y0, x1, y1 in obstructions):
                return None
            return round((first[0] - baseline) / glyph_height, 4)


def _ink_bands(rows: list[int]) -> list[tuple[int, int]]:
    bands = []
    start = None
    for index, active in enumerate([*rows, 0]):
        if active and start is None:
            start = index
        elif not active and start is not None:
            if index - start >= 3:
                bands.append((start, index))
            start = None
    return bands


def has_vertical_columns(image: Image, bbox: tuple[int, int, int, int]) -> bool:
    """Observe separated ink columns, leaving aligned grids ambiguous.

    Column gutters must exceed gaps in the vertical ink projection. A long
    occupied vertical band distinguishes this from dense horizontal text rows.
    This is positive evidence only, not a complete writing-direction detector.
    """
    left, top, right, bottom = bbox
    width, height = right - left, bottom - top
    if (image.mode not in ("L", "RGB") or not 0 <= left < right <= image.width
            or not 0 <= top < bottom <= image.height
            or height < 3 * width or width * height > _MAX_CROP_PIXELS):
        return False
    with image.crop(bbox) as region, region.convert("L") as crop:
        with crop.point(_DARK_PIXELS, mode="1") as ink:
            columns, rows = ink.getprojection()
    x_bands, y_bands = _ink_bands(columns), _ink_bands(rows)
    if not 2 <= len(x_bands) <= 8 or len(y_bands) < 2:
        return False
    column_width = median(end - start for start, end in x_bands)
    row_height = median(end - start for start, end in y_bands)
    column_gap = median(x_bands[i][0] - x_bands[i - 1][1]
                        for i in range(1, len(x_bands)))
    row_gap = median(y_bands[i][0] - y_bands[i - 1][1]
                     for i in range(1, len(y_bands)))
    return (column_gap >= max(2, 0.15 * column_width)
            and max(end - start for start, end in y_bands) >= 3 * column_width
            and column_gap / column_width >= 3 * row_gap / row_height)
