"""Output-directory allocation for repository-local CLI runs."""

import re
from datetime import datetime
from pathlib import Path


DEFAULT_OUTPUT_ROOT = Path("pdf-craft-output")


def create_run_directory(root: Path, label: str, *, now: datetime | None = None) -> Path:
    """Create a date- and sequence-suffixed directory below *root*."""
    day = (now or datetime.now().astimezone()).strftime("%Y%m%d")
    root.mkdir(parents=True, exist_ok=True)
    sequence = _next_sequence(root, day)
    while True:
        path = root / f"{label}-{day}-{sequence:03d}"
        try:
            path.mkdir()
        except FileExistsError:
            sequence += 1
        else:
            return path


def _next_sequence(root: Path, day: str) -> int:
    pattern = re.compile(rf".*-{re.escape(day)}-(\d+)$")
    sequences = (
        int(match.group(1))
        for path in root.iterdir()
        if path.is_dir() and (match := pattern.fullmatch(path.name))
    )
    return max(sequences, default=0) + 1
