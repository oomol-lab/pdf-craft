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
        if not path.is_file():
            continue
        if path.suffix.lower() == ".pdf":
            assets.append(SmokeAsset(str(path.relative_to(root)), "pdf", path))
        elif path.suffix.lower() == ".epub":
            assets.append(SmokeAsset(str(path.relative_to(root)), "epub", path))
    return assets
