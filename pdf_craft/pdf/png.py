"""Bound PNG generation using encoded bytes, rather than a compression estimate."""

from collections.abc import Callable
from io import BytesIO
from math import floor, sqrt
from pathlib import Path

from PIL.Image import Image

from ..metering import AbortedCheck, check_aborted


MAX_PNG_ATTEMPTS = 5
_TARGET_RATIO = 0.97


class PNGSizeError(ValueError):
    """A bounded generation exhausted its resolution or attempt budget."""


def validate_image_limit(limit: int | None) -> None:
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
        raise ValueError("max_page_image_file_size must be a positive integer or None")


def bounded_png(
    render: Callable[[int], Image],
    resolution: int,
    limit: int | None,
    path: Path | None = None,
    aborted: AbortedCheck = lambda: False,
) -> Image:
    """Return an owned image; close every rejected/failed candidate.

    Resolution is a DPI or a maximum pixel dimension. Always measure the
    requested resolution first. A successful on-disk PNG is the exact file
    measured here; callers must not re-encode it before submitting it.
    """
    validate_image_limit(limit)
    if resolution < 1:
        raise ValueError("PNG resolution must be positive")
    for attempt in range(MAX_PNG_ATTEMPTS):
        check_aborted(aborted)
        image = render(resolution)
        try:
            check_aborted(aborted)
            if path is not None:
                image.save(path, format="PNG")
                size = path.stat().st_size
            elif limit is not None:
                with BytesIO() as output:
                    image.save(output, format="PNG")
                    size = output.tell()
            else:
                return image
            check_aborted(aborted)
            if limit is None or size <= limit:
                return image
        except BaseException:
            image.close()
            raise
        image.close()
        if attempt + 1 == MAX_PNG_ATTEMPTS or resolution == 1:
            raise PNGSizeError(
                f"PNG exceeds max_page_image_file_size={limit}: {size} bytes "
                f"after {attempt + 1} generation(s) at resolution {resolution}"
            )
        assert limit is not None
        resolution = max(1, min(resolution - 1, floor(resolution * sqrt(_TARGET_RATIO * limit / size))))
    raise AssertionError("Unreachable PNG generation state")


def resized_png(
    image: Image,
    path: Path,
    limit: int | None,
    aborted: AbortedCheck,
    maximum: int | None = None,
) -> tuple[int, int]:
    """Encode request derivatives, always resizing from their original raster."""
    width, height = image.size
    original = max(width, height)

    def render(resolution: int) -> Image:
        if resolution == original:
            return image.copy()
        return image.resize((max(1, round(width * resolution / original)),
                             max(1, round(height * resolution / original))))

    result = bounded_png(render, min(original, maximum or original), limit, path, aborted)
    try:
        return result.size
    finally:
        result.close()
