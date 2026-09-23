from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SmokeAsset:
    name: str
    format: str
    path: Path


def discover_assets(root: Path) -> list[SmokeAsset]:
    assets: list[SmokeAsset] = []
    for path in sorted(root.rglob("*")):
        if (
            path.is_dir()
            and (path / "page_pixel_sizes.json").is_file()
            and any(path.glob("page_*.xml"))
        ):
            assets.append(SmokeAsset(
                path.relative_to(root).as_posix(), "ocr-pages", path
            ))
            continue
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if path.suffix.lower() == ".pdf":
            assets.append(SmokeAsset(name, "pdf", path))
        elif path.suffix.lower() == ".epub":
            assets.append(SmokeAsset(name, "epub", path))
    return assets
