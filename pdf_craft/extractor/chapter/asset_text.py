"""Image-associated prose, distinct from text already painted in its pixels.

Explicit title/caption fields are authoritative. Older extractors stored the
image's associated description in content; use that field only for a content-
only image without either explicit title or caption. No pixel OCR is performed.
Tables and display formulas retain their separate content contracts.
"""
from dataclasses import replace

from .chapter import Content, SourceAsset


def uses_image_description(asset: SourceAsset) -> bool:
    """Whether this image uses the legacy, otherwise unrepresented description."""
    return asset.ref == 'image' and bool(asset.content) and not asset.caption and not asset.title


def image_caption(asset: SourceAsset) -> Content:
    """Return one visible caption, with the legacy content-field fallback."""
    return asset.content if uses_image_description(asset) else asset.caption


def translatable_asset(asset: SourceAsset) -> SourceAsset:
    """Do not translate redundant/embedded image content beside an explicit caption."""
    if asset.ref == 'image' and not uses_image_description(asset):
        return replace(asset, content=[])
    return asset


def normalize_image_description(asset: SourceAsset) -> SourceAsset:
    """New extraction writes associated description to the caption field.

    Keep legacy input untouched on read; its persisted translation identities
    and source structure must not be rewritten during rendering.
    """
    if uses_image_description(asset):
        return replace(asset, content=[], caption=asset.content)
    return asset
