"""Visual source-text erasure for PDF replacement.

This deliberately remains a simple rectangular layer.  Image segmentation and
content-aware inpainting belong to a future eraser implementation, not the
paragraph text filler.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import ceil, floor
from typing import TypeAlias, cast

from PIL import Image

from .geometry import PageRectangle, pixel_rectangle_in_page_points
from .models import PDFReplacementRegion


RGBColor: TypeAlias = tuple[int, int, int]
_LUMINANCE_SCALE = 10_000
_LUMINANCE_WINDOW = 8 * _LUMINANCE_SCALE


@dataclass(frozen=True)
class EraseOptions:
    """Options for the intentionally simple, rectangular visual eraser.

    ``padding`` is measured in the OCR/page-image pixel coordinate system,
    before the rectangle is mapped to PDF points.
    """

    padding: int = 2

    def __post_init__(self) -> None:
        if self.padding < 0:
            raise ValueError("erase padding must be non-negative")


@dataclass(frozen=True)
class EraseRectangle:
    """One colored visual mask in PDF-point coordinates with a top-left origin."""

    page_index: int
    rectangle: PageRectangle
    color: RGBColor


class RectangularEraser:
    """Plan and paint background-colored rectangle masks independently of text.

    This is deliberately not a text-pixel detector. Each source bbox may cover
    multiple lines; the whole padded rectangle is covered with a robust local
    background RGB estimate from the unmodified source page image.
    """

    def __init__(self, options: EraseOptions | None = None) -> None:
        self.options = options or EraseOptions()

    def plan(
        self,
        regions: Iterable[PDFReplacementRegion],
        page_sizes: dict[int, tuple[float, float]],
        page_images: Mapping[int, Image.Image],
    ) -> tuple[EraseRectangle, ...]:
        """Create all masks from the same, unmodified source-page images."""
        rectangles: list[EraseRectangle] = []
        for region in regions:
            page_width, page_height = page_sizes[region.page_index]
            try:
                page_image = page_images[region.page_index]
            except KeyError as error:
                raise ValueError(
                    f"missing source page image for erasure on page {region.page_index}"
                ) from error
            bbox = self._expanded_bbox(region)
            rectangles.append(EraseRectangle(
                region.page_index,
                pixel_rectangle_in_page_points(
                    bbox, region.page_pixel_size, page_width, page_height,
                ),
                self.estimate_background_color(page_image, bbox, region.page_pixel_size),
            ))
        return tuple(rectangles)

    @staticmethod
    def draw(overlay, rectangles: Iterable[EraseRectangle], page_height: float) -> None:
        """Draw the masks on a ReportLab overlay (whose origin is bottom-left)."""
        for erase in rectangles:
            rectangle = erase.rectangle
            overlay.setFillColorRGB(*(component / 255 for component in erase.color))
            overlay.rect(
                rectangle.x,
                page_height - rectangle.bottom,
                rectangle.width,
                rectangle.height,
                stroke=0,
                fill=1,
            )

    def _expanded_bbox(self, region: PDFReplacementRegion) -> tuple[int, int, int, int]:
        """Expand one OCR bbox by the configured source-pixel padding."""
        left, top, right, bottom = region.bbox
        page_width, page_height = region.page_pixel_size
        padding = self.options.padding
        return (
            max(left - padding, 0),
            max(top - padding, 0),
            min(right + padding, page_width),
            min(bottom + padding, page_height),
        )

    @staticmethod
    def estimate_background_color(
        image: Image.Image,
        bbox: tuple[int, int, int, int],
        page_pixel_size: tuple[int, int],
    ) -> RGBColor:
        """Return a robust, hue-preserving RGB median for one source rectangle.

        The luminance-weighted median follows the rectangle's actual pixel
        frequencies, so a mostly beige page remains beige and a mostly black
        page remains black. A small luminance neighbourhood is then reduced by
        weighted component medians to avoid selecting one noisy JPEG pixel.
        """
        crop = _crop_in_page_coordinates(image, bbox, page_pixel_size)
        rgb_crop = crop.convert("RGB")
        pixels: list[RGBColor] = [
            cast(RGBColor, rgb_crop.getpixel((x, y)))
            for y in range(rgb_crop.height)
            for x in range(rgb_crop.width)
        ]
        if not pixels:
            raise ValueError("cannot estimate background color from an empty erasure rectangle")

        frequencies: Counter[RGBColor] = Counter(pixels)
        ordered = sorted(frequencies, key=lambda color: (_luminance(color), color))
        midpoint = (len(pixels) - 1) // 2
        cumulative = 0
        median_color = ordered[-1]
        for color in ordered:
            cumulative += frequencies[color]
            if cumulative > midpoint:
                median_color = color
                break

        median_luminance = _luminance(median_color)
        nearby = Counter({
            color: count
            for color, count in frequencies.items()
            if abs(_luminance(color) - median_luminance) <= _LUMINANCE_WINDOW
        })
        if not nearby:  # Defensive: the median color itself is always nearby.
            return median_color
        return (
            _weighted_component_median(nearby, 0),
            _weighted_component_median(nearby, 1),
            _weighted_component_median(nearby, 2),
        )


def _crop_in_page_coordinates(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    page_pixel_size: tuple[int, int],
) -> Image.Image:
    """Map OCR pixel coordinates onto a rendered page image and crop safely."""
    page_width, page_height = page_pixel_size
    left, top, right, bottom = bbox
    image_width, image_height = image.size
    crop_left = max(0, min(image_width, floor(left * image_width / page_width)))
    crop_top = max(0, min(image_height, floor(top * image_height / page_height)))
    crop_right = max(0, min(image_width, ceil(right * image_width / page_width)))
    crop_bottom = max(0, min(image_height, ceil(bottom * image_height / page_height)))
    if crop_right <= crop_left or crop_bottom <= crop_top:
        raise ValueError("erasure rectangle has no pixels after page-image mapping")
    return image.crop((crop_left, crop_top, crop_right, crop_bottom))


def _luminance(color: RGBColor) -> int:
    """Return deterministic Rec. 709 luminance in fixed-point units."""
    red, green, blue = color
    return 2126 * red + 7152 * green + 722 * blue


def _weighted_component_median(colors: Counter[RGBColor], component: int) -> int:
    """Compute one RGB channel's weighted median without selecting a noise pixel."""
    values: Counter[int] = Counter()
    for color, count in colors.items():
        values[color[component]] += count
    midpoint = (sum(values.values()) - 1) // 2
    cumulative = 0
    for value in sorted(values):
        cumulative += values[value]
        if cumulative > midpoint:
            return value
    raise RuntimeError("weighted component median has no values")
