"""Bound PNG bytes with measured feedback and a final raw-size guarantee."""

from collections.abc import Callable
from io import BytesIO
from math import floor, isfinite, log
from pathlib import Path
import struct
import zlib

from PIL.Image import Image

from ..metering import AbortedCheck, check_aborted


MAX_PNG_ATTEMPTS = 5


class PNGSizeError(ValueError):
    """A bounded generation exhausted its resolution or attempt budget."""


def validate_image_limit(limit: int | None) -> None:
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
        raise ValueError("max_page_image_file_size must be a positive integer or None")


def _next_resolution(history: list[tuple[int, int]], limit: int) -> int:
    """Use the measured slope, with conservative early and aggressive late steps."""
    resolution, size = history[-1]
    exponent = 2.0
    if len(history) > 1:
        previous_resolution, previous_size = history[-2]
        if previous_resolution > resolution and previous_size > size:
            observed = log(previous_size / size) / log(previous_resolution / resolution)
            exponent = max(0.5, min(3.0, observed)) if isfinite(observed) else 1.0
        else:
            exponent = 1.0
    aggressive = len(history) >= 3
    target = 0.85 if aggressive else 0.97
    predicted = floor(resolution * (target * limit / size) ** (1 / exponent))
    candidate = (min(floor(resolution * 0.75), predicted) if aggressive
                 else max(floor(resolution * 0.8), predicted))
    return max(1, min(resolution - 1, candidate))


def _png_bound(size: tuple[int, int], pixel_bytes: int) -> int:
    """Upper bound for our final stored-DEFLATE, one-IDAT PNG.

    RGB/RGBA and 16-bit gray rows have one filter byte each. A zlib stream consists
    of a two-byte header, <=65535-byte stored blocks (five-byte headers), and
    an Adler32 trailer. PNG signature + IHDR + one IDAT + IEND cost 57 bytes.
    Only 16-bit grayscale transparency may add a bounded 14-byte tRNS chunk;
    other source metadata and palettes cannot add ancillary chunks.
    """
    width, height = size
    raw = height * (1 + width * pixel_bytes)
    return 57 + (14 if pixel_bytes == 2 else 0) + 6 + raw + 5 * ((raw + 65534) // 65535)


def _safe_size(size: tuple[int, int], pixel_bytes: int, limit: int) -> tuple[int, int]:
    if _png_bound((1, 1), pixel_bytes) > limit:
        raise PNGSizeError(f"PNG limit {limit} cannot hold even the final 1x1 {pixel_bytes}-byte/pixel image")
    width, height = size
    longest = max(size)

    def dimensions(edge):
        return max(1, width * edge // longest), max(1, height * edge // longest)

    lower, upper = 1, longest
    while lower < upper:
        middle = (lower + upper + 1) // 2
        if _png_bound(dimensions(middle), pixel_bytes) <= limit:
            lower = middle
        else:
            upper = middle - 1
    return dimensions(lower)


def _pixel_bytes(image: Image) -> int:
    if image.mode == 'I' or image.mode.startswith('I;16'):
        return 2  # Preserve grayscale precision rather than clipping to eight bits.
    return 4 if 'A' in image.getbands() or 'a' in image.getbands() or 'transparency' in image.info else 3


def _final_image(image: Image, limit: int) -> Image:
    """Keep pixels/alpha, discard transport-irrelevant metadata, fit the whole page."""
    pixel_bytes = _pixel_bytes(image)
    size = _safe_size(image.size, pixel_bytes, limit)
    scaled = image if image.size == size else image.resize(size)
    try:
        result = scaled.convert('I' if pixel_bytes == 2 else 'RGBA' if pixel_bytes == 4 else 'RGB')
    finally:
        if scaled is not image:
            scaled.close()
    try:
        if pixel_bytes == 2:
            encoded_gray = result.convert('I;16B')
            result.close()
            result = encoded_gray
        result.info.clear()
        if pixel_bytes == 2 and 'transparency' in image.info:
            result.info['transparency'] = image.info['transparency']
        return result
    except BaseException:
        result.close()
        raise


def _stored_png(image: Image, aborted: AbortedCheck) -> bytes:
    """One deterministic encoding; stored blocks make the size bound exact."""
    width, height = image.size
    pixel_bytes = _pixel_bytes(image)
    pixels = image.tobytes()
    stride = width * pixel_bytes
    raw = bytearray()
    for offset in range(0, len(pixels), stride):
        check_aborted(aborted)
        raw.append(0)  # PNG filter: None
        raw.extend(pixels[offset:offset + stride])
    stream = bytearray(b'\x78\x01')  # zlib: deflate, no preset dictionary
    for offset in range(0, len(raw), 65535):
        check_aborted(aborted)
        block = raw[offset:offset + 65535]
        final = offset + len(block) == len(raw)
        stream.extend(struct.pack('<BHH', int(final), len(block), 65535 - len(block)))
        stream.extend(block)
    stream.extend(struct.pack('>I', zlib.adler32(raw) & 0xffffffff))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)

    depth, color = (16, 0) if pixel_bytes == 2 else (8, 6 if pixel_bytes == 4 else 2)
    header = struct.pack('>IIBBBBB', width, height, depth, color, 0, 0, 0)
    transparency = (chunk(b'tRNS', struct.pack('>H', image.info['transparency']))
                    if pixel_bytes == 2 and 'transparency' in image.info else b'')
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + transparency
            + chunk(b'IDAT', stream) + chunk(b'IEND', b''))


def bounded_png(
    render: Callable[[int], Image],
    resolution: int,
    limit: int | None,
    path: Path | None = None,
    aborted: AbortedCheck = lambda: False,
) -> Image:
    """Measure at most five candidates; return the first compliant owned image.

    Resolution is DPI or maximum dimension. Normal images keep the requested
    quality. Attempts two/three use measured slopes with a 20% maximum quality
    reduction; four drops at least 25%. Five bounds dimensions before its only
    encoding, using a deterministic PNG whose worst-case bytes are known.
    The successful file is the exact measured file; never re-encode for upload.
    """
    validate_image_limit(limit)
    if resolution < 1:
        raise ValueError("PNG resolution must be positive")
    history: list[tuple[int, int]] = []
    last_size, last_pixel_bytes = (1, 1), 3
    for attempt in range(MAX_PNG_ATTEMPTS):
        check_aborted(aborted)
        final = limit is not None and (attempt == MAX_PNG_ATTEMPTS - 1 or attempt > 0 and resolution == 1)
        if final:
            assert limit is not None
            safe = _safe_size(last_size, last_pixel_bytes, limit)
            resolution = max(1, floor(history[-1][0] * min(safe[0] / last_size[0], safe[1] / last_size[1])))
        image = render(resolution)
        try:
            check_aborted(aborted)
            if final:
                assert limit is not None
                fitted = _final_image(image, limit)
                image.close()
                image = fitted
                encoded = _stored_png(image, aborted)
                if path is not None:
                    path.write_bytes(encoded)
                    size = path.stat().st_size
                else:
                    size = len(encoded)
            elif path is not None:
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
            last_size, last_pixel_bytes = image.size, _pixel_bytes(image)
        except BaseException:
            image.close()
            raise
        image.close()
        if final:
            raise PNGSizeError(f"Final PNG exceeds max_page_image_file_size={limit}: {size} bytes")
        assert limit is not None
        history.append((resolution, size))
        resolution = _next_resolution(history, limit)
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
