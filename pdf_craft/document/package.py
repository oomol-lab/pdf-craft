from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DocumentPackage:
    """Stable renderer input produced by an Extractor."""

    chapters_path: Path
    assets_path: Path
    toc_path: Path | None = None
    cover_path: Path | None = None

    @classmethod
    def from_path(cls, path: Path) -> "DocumentPackage":
        return cls(
            chapters_path=path / "chapters",
            assets_path=path / "assets",
            toc_path=path / "toc.xml",
            cover_path=path / "cover.png",
        )

    def has_toc(self) -> bool:
        return self.toc_path is not None and self.toc_path.exists()

    def has_cover(self) -> bool:
        return self.cover_path is not None and self.cover_path.exists()
