from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any


@dataclass
class DocumentPackage:
    """Stable renderer input produced by an Extractor."""

    chapters_path: Path
    assets_path: Path
    toc_path: Path | None = None
    cover_path: Path | None = None
    metadata_path: Path | None = None

    @classmethod
    def from_path(cls, path: Path) -> "DocumentPackage":
        cover_path = path / "cover.png"
        return cls(
            chapters_path=path / "chapters",
            assets_path=path / "assets",
            toc_path=path / "toc.xml",
            cover_path=cover_path if cover_path.exists() else None,
            metadata_path=path / "document.json",
        )

    def validate(self, require_toc: bool = False) -> "DocumentPackage":
        if not self.chapters_path.is_dir():
            raise ValueError(f"missing chapters directory: {self.chapters_path}")
        if not self.assets_path.is_dir():
            raise ValueError(f"missing assets directory: {self.assets_path}")
        if require_toc and not self.has_toc():
            raise ValueError("document package is missing toc.xml")
        if self.metadata_path is not None and self.metadata_path.exists():
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            if data.get("schema") != 1:
                raise ValueError("unsupported document package schema")
            self._parse_page_pixel_sizes(data)
        return self

    def write_metadata(self, *, dpi: int | None = None,
                       page_pixel_sizes: dict[int, tuple[int, int]] | None = None) -> None:
        path = self.metadata_path or self.chapters_path.parent / "document.json"
        self.metadata_path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": 1, "bbox_coordinate_space": "ocr_pixels",
                   "page_index_base": 1, "dpi": dpi,
                   "page_pixel_sizes": {str(k): list(v) for k, v in (page_pixel_sizes or {}).items()}}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def page_pixel_sizes(self) -> dict[int, tuple[int, int]]:
        """Return OCR canvas sizes recorded by the Extractor, without OCR cache."""
        if self.metadata_path is None or not self.metadata_path.exists():
            return {}
        payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        return self._parse_page_pixel_sizes(payload)

    @staticmethod
    def _parse_page_pixel_sizes(payload: dict[str, Any]) -> dict[int, tuple[int, int]]:
        raw_sizes = payload.get("page_pixel_sizes", {})
        if not isinstance(raw_sizes, dict):
            raise ValueError("page_pixel_sizes must be a mapping")
        sizes: dict[int, tuple[int, int]] = {}
        for raw_index, raw_size in raw_sizes.items():
            try:
                page_index = int(raw_index)
            except (TypeError, ValueError) as error:
                raise ValueError("page_pixel_sizes keys must be page indexes") from error
            if page_index < 1:
                raise ValueError("page_pixel_sizes page indexes must be positive")
            if (
                not isinstance(raw_size, list | tuple)
                or len(raw_size) != 2
                or not all(
                    isinstance(value, int | float)
                    and not isinstance(value, bool)
                    and float(value) > 0
                    for value in raw_size
                )
            ):
                raise ValueError(
                    "page_pixel_sizes values must be two positive numeric dimensions"
                )
            sizes[page_index] = (int(raw_size[0]), int(raw_size[1]))
        return sizes

    def has_toc(self) -> bool:
        return self.toc_path is not None and self.toc_path.exists()

    def has_cover(self) -> bool:
        return self.cover_path is not None and self.cover_path.exists()
