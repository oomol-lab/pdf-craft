import argparse
import json
from pathlib import Path

from .assets import discover_assets
from .runner import expand_matrix, run_smoke


def main() -> None:
    parser = argparse.ArgumentParser(description="Run parameterized pdf-craft smoke conversions")
    parser.add_argument("--assets-root", type=Path, default=Path("tests/assets"))
    parser.add_argument("--config", type=Path, help="JSON matrix config with defaults and runs")
    parser.add_argument("--list-assets", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-root", type=Path, default=Path("analysing/smoke"))
    args = parser.parse_args()
    if args.list_assets:
        print(json.dumps([asset.__dict__ | {"path": str(asset.path)} for asset in discover_assets(args.assets_root)], ensure_ascii=False, indent=2))
        return
    if args.config is None:
        parser.error("--config is required unless --list-assets is used")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    run_paths = [run_smoke(run, assets_root=args.assets_root, output_root=args.output_root, dry_run=args.dry_run)
                 for run in expand_matrix(config, args.assets_root)]
    print("\n".join(str(path) for path in run_paths))
