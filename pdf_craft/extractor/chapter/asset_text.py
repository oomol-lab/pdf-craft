"""Current asset text contract: image content is opaque, captions are prose."""
from dataclasses import replace

from .chapter import SourceAsset


def translatable_asset(asset: SourceAsset) -> SourceAsset:
    """Only image title/caption are translatable, regardless of field presence."""
    if asset.ref == 'image':
        return replace(asset, content=[])
    return asset
