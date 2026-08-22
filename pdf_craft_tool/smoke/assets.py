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
        name = path.relative_to(root).as_posix()
        if path.suffix.lower() == ".pdf":
            assets.append(SmokeAsset(name, "pdf", path))
        elif path.suffix.lower() == ".epub":
            assets.append(SmokeAsset(name, "epub", path))
    return assets
