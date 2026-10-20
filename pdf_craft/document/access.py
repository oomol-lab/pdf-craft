"""Public source geometry and resource access, independent of XML storage."""
from dataclasses import dataclass
from pathlib import Path
import re

from ..runtime import IO_DOMAIN


@dataclass(frozen=True)
class PageGeometry:
    """One-based page geometry in the extraction's source coordinate space."""
    page_index: int
    width: int
    height: int
    render_dpi: int
    coordinate_space: str = "ocr_pixels"


def page_geometries(sizes, dpi):
    return {index: PageGeometry(index, width, height, dpi)
            for index, (width, height) in sizes.items()}


def asset_path(root: Path, asset_hash: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", asset_hash) is None:
        raise ValueError("asset_hash must be a lowercase SHA-256 hex digest")
    return root / f"{asset_hash}.png"


class ExtractionContext:
    """Caller-owned access to geometry and assets during PDF extraction.

    Pass to ExtractionOptions(context=...). Geometry snapshots remain usable
    afterwards. Asset reads are valid only while the extraction call is active;
    use craft.read_asset on the completed extraction for durable access.
    """
    def __init__(self):
        self._pages = {}
        self._assets: Path | None = None

    def read_pages(self) -> dict[int, PageGeometry]:
        """Return an independent snapshot of already known source pages."""
        return dict(self._pages)

    async def read_asset(self, asset_hash: str) -> bytes:
        """Copy an asset's bytes before the extraction workspace is released."""
        if self._assets is None:
            raise RuntimeError("asset access requires an active extraction")
        return await IO_DOMAIN.run(asset_path(self._assets, asset_hash).read_bytes)

    def _bind(self, assets):
        if self._assets is not None:
            raise RuntimeError("ExtractionContext is already in use")
        self._pages.clear()
        self._assets = assets

    def _update(self, sizes, dpi):
        self._pages.update(page_geometries(sizes, dpi))

    def _unbind(self):
        self._assets = None
